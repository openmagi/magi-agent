from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

from magi_agent.authoring.contracts import (
    AuthoringToolScope,
    AuthoringToolSession,
    RecipeBuilderSession,
    RecipePackDraft,
    RecipePackVersion,
)

RecipePackStorageStatus = Literal["draft", "staging_candidate", "disabled", "deleted"]
RecipePackVersionStorageStatus = Literal["candidate", "reviewed", "rejected", "archived"]
CompiledSnapshotStorageStatus = Literal["compiled", "staged", "disabled", "active"]
EvalResultStorageStatus = Literal["recorded", "blocked"]
ApprovalRefStatus = Literal["required", "approved_for_review", "rejected", "blocked"]

_DIGEST_PREFIX = "sha256:"
_MODEL_CONFIG = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")
_PRIVATE_REF_PREFIXES = ("/", "~", "file:", "s3:", "gs:")
_PRIVATE_URI_SCHEMES = ("postgres", "postgresql", "supabase", "vault", "gcs")
_URI_USERINFO_RE = re.compile(r"^[a-z][a-z0-9+.-]*://[^/?#\s]*:[^/?#\s]*@")
_SIGNED_QUERY_RE = re.compile(
    r"(?i)(?:[?&]|^)(?:x-amz-signature|x-amz-credential|x-goog-signature|"
    r"x-goog-credential|signature|sig|access_key|accesskey)="
)
_RAW_CREDENTIAL_FIELD_NAMES = {
    "apikey",
    "apitoken",
    "authtoken",
    "accesstoken",
    "refreshtoken",
    "bearertoken",
    "credential",
    "credentials",
    "rawcredential",
    "rawcredentials",
    "password",
    "privatekey",
    "secret",
    "secrettoken",
    "token",
}
_RAW_IO_FIELD_NAMES = {
    "rawprompt",
    "rawmodelprompt",
    "prompttojson",
    "rawoutput",
    "rawmodeloutput",
    "modelrawoutput",
}
_RAW_CODE_FIELD_NAMES = {
    "rawcode",
    "generatedcode",
    "executablecode",
}
_SECRET_TEXT_RE = re.compile(
    r"(?i)(bearer\s+[A-Za-z0-9._-]{8,}|sk-(?:live|test)-[A-Za-z0-9_-]{8,}|"
    r"(?:api[_-]?key|apiKey|token|secret|password|credential)\s*[:=?&]\s*[^\s,;]+)"
)
_RAW_MODEL_TEXT_RE = re.compile(
    r"(?i)\b(?:raw\s*model\s*output|raw\s*output|raw\s*prompt|"
    r"hidden\s+instructions?|hidden\s+transcript|chain\s+of\s+thought|"
    r"tool\s+result\s+payload)\b"
)


class RecipePackStorageError(ValueError):
    """Raised when a local authoring storage operation violates the boundary."""


class _StorageModel(BaseModel):
    model_config = _MODEL_CONFIG

    @classmethod
    def model_construct(cls, *args: object, **kwargs: object) -> Self:
        raise TypeError("model_construct is disabled for authoring storage contracts")

    def model_copy(
        self,
        *,
        update: Mapping[str, object] | None = None,
        deep: bool = False,
    ) -> Self:
        data = self.model_dump(by_alias=True)
        if update:
            for key, value in update.items():
                data[_copy_update_alias(type(self), key)] = value
        return type(self).model_validate(data)


class RecipePackDraftRecord(_StorageModel):
    draft_id: str = Field(alias="draftId")
    bot_id: str = Field(alias="botId")
    owner_id: str = Field(alias="ownerId")
    authoring_session_id: str = Field(alias="authoringSessionId")
    storage_status: RecipePackStorageStatus = Field(alias="storageStatus")
    draft: RecipePackDraft
    draft_digest: str = Field(alias="draftDigest")
    content_digest: str = Field(alias="contentDigest")
    revision: int = Field(ge=1)
    activation_enabled: StrictBool = Field(default=False, alias="activationEnabled")
    activation_eligibility: StrictBool = Field(default=False, alias="activationEligibility")
    connector_credentials_exposed: StrictBool = Field(
        default=False, alias="connectorCredentialsExposed"
    )
    disabled: StrictBool = False
    deleted: StrictBool = False
    tombstone_reason_digest: str | None = Field(default=None, alias="tombstoneReasonDigest")

    @field_validator("draft_id", "bot_id", "owner_id", "authoring_session_id")
    @classmethod
    def _reject_empty(cls, value: str) -> str:
        return _require_non_empty(value, "draft record fields")

    @field_validator("draft_digest", "content_digest", "tombstone_reason_digest")
    @classmethod
    def _validate_optional_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_digest(value, "draft record digest")

    @model_validator(mode="after")
    def _require_consistency(self) -> RecipePackDraftRecord:
        _reject_true(self.activation_enabled, "activationEnabled")
        _reject_true(self.activation_eligibility, "activationEligibility")
        _reject_true(self.connector_credentials_exposed, "connectorCredentialsExposed")
        if self.draft_id != self.draft.draft_id:
            raise ValueError("draftId must match draft.draftId")
        if self.bot_id != self.draft.bot_id:
            raise ValueError("botId must match draft.botId")
        if self.owner_id != self.draft.owner_id:
            raise ValueError("ownerId must match draft.ownerId")
        if self.authoring_session_id != self.draft.authoring_session_id:
            raise ValueError("authoringSessionId must match draft.authoringSessionId")
        if self.content_digest != digest_storage_content(self.draft):
            raise ValueError("contentDigest must match draft payload")
        if self.draft_digest != self.content_digest:
            raise ValueError("draftDigest must match contentDigest")
        if self.storage_status == "disabled" and not self.disabled:
            raise ValueError("disabled tombstone must set disabled=true")
        if self.storage_status == "deleted" and not self.deleted:
            raise ValueError("deleted tombstone must set deleted=true")
        if self.storage_status in {"disabled", "deleted"} and self.tombstone_reason_digest is None:
            raise ValueError("tombstoneReasonDigest is required for disabled/deleted drafts")
        if self.storage_status not in {"disabled", "deleted"}:
            _reject_true(self.disabled, "disabled")
            _reject_true(self.deleted, "deleted")
        return self


class RecipePackVersionRecord(_StorageModel):
    bot_id: str = Field(alias="botId")
    owner_id: str = Field(alias="ownerId")
    authoring_session_id: str = Field(alias="authoringSessionId")
    version: RecipePackVersion
    version_digest: str = Field(alias="versionDigest")
    revision: int = Field(ge=1)
    storage_status: RecipePackVersionStorageStatus = Field(alias="storageStatus")
    activation_enabled: StrictBool = Field(default=False, alias="activationEnabled")

    @field_validator("bot_id", "owner_id", "authoring_session_id")
    @classmethod
    def _reject_empty(cls, value: str) -> str:
        return _require_non_empty(value, "version record fields")

    @field_validator("version_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return _require_digest(value, "versionDigest")

    @model_validator(mode="after")
    def _require_default_off(self) -> RecipePackVersionRecord:
        _reject_true(self.activation_enabled, "activationEnabled")
        if self.storage_status != self.version.status:
            raise ValueError("storageStatus must match version status")
        if self.version_digest != digest_storage_content(self.version):
            raise ValueError("versionDigest must match version payload")
        return self


class CompiledSnapshotRef(_StorageModel):
    ref_id: str = Field(alias="refId")
    pack_id: str = Field(alias="packId")
    version: str
    source_draft_id: str = Field(alias="sourceDraftId")
    authoring_session_id: str | None = Field(default=None, alias="authoringSessionId")
    compiled_snapshot_digest: str = Field(alias="compiledSnapshotDigest")
    snapshot_kind: Literal["compiled_snapshot"] = Field(
        default="compiled_snapshot", alias="snapshotKind"
    )
    status: CompiledSnapshotStorageStatus
    activation_enabled: StrictBool = Field(default=False, alias="activationEnabled")
    activation_eligibility: StrictBool = Field(default=False, alias="activationEligibility")

    @model_validator(mode="before")
    @classmethod
    def _reject_active_status(cls, data: object) -> object:
        if isinstance(data, Mapping) and data.get("status") == "active":
            raise ValueError("status cannot be active for storage snapshot refs")
        return data

    @field_validator("ref_id", "pack_id", "version", "source_draft_id", "authoring_session_id")
    @classmethod
    def _reject_empty(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_non_empty(value, "compiled snapshot ref fields")

    @field_validator("compiled_snapshot_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return _require_digest(value, "compiledSnapshotDigest")

    @model_validator(mode="after")
    def _require_default_off(self) -> CompiledSnapshotRef:
        _reject_true(self.activation_enabled, "activationEnabled")
        _reject_true(self.activation_eligibility, "activationEligibility")
        return self


class EvalResultRef(_StorageModel):
    eval_result_id: str = Field(alias="evalResultId")
    draft_id: str = Field(alias="draftId")
    authoring_session_id: str | None = Field(default=None, alias="authoringSessionId")
    result_digest: str = Field(alias="resultDigest")
    artifact_ref: str = Field(alias="artifactRef")
    status: EvalResultStorageStatus = "recorded"
    local_only: StrictBool = Field(default=True, alias="localOnly")
    non_production: StrictBool = Field(default=True, alias="nonProduction")

    @field_validator("eval_result_id", "draft_id", "authoring_session_id", "artifact_ref")
    @classmethod
    def _reject_empty(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_non_empty(value, "eval result ref fields")

    @field_validator("result_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return _require_digest(value, "resultDigest")

    @model_validator(mode="after")
    def _require_local_only(self) -> EvalResultRef:
        _reject_false(self.local_only, "localOnly")
        _reject_false(self.non_production, "nonProduction")
        return self


class RecipePackApprovalRef(_StorageModel):
    approval_ref_id: str = Field(alias="approvalRefId")
    draft_id: str = Field(alias="draftId")
    authoring_session_id: str | None = Field(default=None, alias="authoringSessionId")
    authority_ref: str = Field(alias="authorityRef")
    approval_digest: str = Field(alias="approvalDigest")
    status: ApprovalRefStatus
    activation_enabled: StrictBool = Field(default=False, alias="activationEnabled")

    @field_validator("approval_ref_id", "draft_id", "authoring_session_id", "authority_ref")
    @classmethod
    def _reject_empty(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_non_empty(value, "approval ref fields")

    @field_validator("approval_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return _require_digest(value, "approvalDigest")

    @model_validator(mode="after")
    def _require_default_off(self) -> RecipePackApprovalRef:
        _reject_true(self.activation_enabled, "activationEnabled")
        return self


class GeneratedPluginProposalArtifactRef(_StorageModel):
    proposal_id: str = Field(alias="proposalId")
    draft_id: str = Field(alias="draftId")
    authoring_session_id: str | None = Field(default=None, alias="authoringSessionId")
    artifact_ref: str = Field(alias="artifactRef")
    artifact_digest: str = Field(alias="artifactDigest")
    executable: StrictBool = False
    activation_enabled: StrictBool = Field(default=False, alias="activationEnabled")
    runtime_entrypoint: None = Field(default=None, alias="runtimeEntrypoint")

    @field_validator("proposal_id", "draft_id", "authoring_session_id", "artifact_ref")
    @classmethod
    def _reject_empty(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_non_empty(value, "generated plugin proposal artifact fields")

    @field_validator("artifact_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return _require_digest(value, "artifactDigest")

    @model_validator(mode="after")
    def _require_non_executable(self) -> GeneratedPluginProposalArtifactRef:
        _reject_true(self.executable, "executable")
        _reject_true(self.activation_enabled, "activationEnabled")
        if self.runtime_entrypoint is not None:
            raise ValueError("runtimeEntrypoint must be absent for proposal artifact refs")
        return self


class PromotionHistoryEntry(_StorageModel):
    entry_id: str = Field(alias="entryId")
    bot_id: str = Field(alias="botId")
    owner_id: str = Field(alias="ownerId")
    authoring_session_id: str = Field(alias="authoringSessionId")
    draft_id: str = Field(alias="draftId")
    from_status: RecipePackStorageStatus = Field(alias="fromStatus")
    to_status: RecipePackStorageStatus = Field(alias="toStatus")
    reason_digest: str = Field(alias="reasonDigest")
    revision: int = Field(ge=1)
    activation_enabled: StrictBool = Field(default=False, alias="activationEnabled")

    @field_validator("entry_id", "bot_id", "owner_id", "authoring_session_id", "draft_id")
    @classmethod
    def _reject_empty(cls, value: str) -> str:
        return _require_non_empty(value, "promotion history fields")

    @field_validator("reason_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return _require_digest(value, "reasonDigest")

    @model_validator(mode="after")
    def _require_allowed_transition(self) -> PromotionHistoryEntry:
        if (self.from_status, self.to_status) != ("draft", "staging_candidate"):
            raise ValueError("only draft -> staging_candidate promotion is supported")
        _reject_true(self.activation_enabled, "activationEnabled")
        return self


class LocalRecipePackStorage:
    """Deterministic process-local fake adapter for Recipe Builder Mode storage."""

    def __init__(self) -> None:
        self._revision = 0
        self._drafts: dict[tuple[str, str, str, str], RecipePackDraftRecord] = {}
        self._versions: dict[tuple[str, str, str, str, str], RecipePackVersionRecord] = {}
        self._snapshots: dict[tuple[str, str, str, str], CompiledSnapshotRef] = {}
        self._eval_results: dict[tuple[str, str, str, str], EvalResultRef] = {}
        self._approval_refs: dict[tuple[str, str, str, str], RecipePackApprovalRef] = {}
        self._proposal_artifacts: dict[
            tuple[str, str, str, str],
            GeneratedPluginProposalArtifactRef,
        ] = {}
        self._promotion_history: list[PromotionHistoryEntry] = []

    def save_draft(
        self,
        scope: AuthoringToolSession | Mapping[str, object],
        draft: RecipePackDraft | Mapping[str, object],
    ) -> RecipePackDraftRecord:
        coerced_scope = _coerce_scope(scope)
        coerced_draft = _coerce_draft(draft)
        _require_scope_matches_draft(coerced_scope, coerced_draft)
        if coerced_draft.status != "draft":
            raise RecipePackStorageError("draft save only accepts draft status")
        existing = self._drafts.get(_draft_key(coerced_scope, coerced_draft.draft_id))
        if existing is not None and existing.storage_status != "draft":
            raise RecipePackStorageError(
                "cannot overwrite a draft after explicit storage transition"
            )
        digest = digest_storage_content(coerced_draft)
        record = RecipePackDraftRecord(
            draftId=coerced_draft.draft_id,
            botId=coerced_draft.bot_id,
            ownerId=coerced_draft.owner_id,
            authoringSessionId=coerced_draft.authoring_session_id,
            storageStatus="draft",
            draft=coerced_draft,
            draftDigest=digest,
            contentDigest=digest,
            revision=self._next_revision(),
        )
        _reject_unsafe_storage_payload(record)
        self._drafts[_draft_key(coerced_scope, coerced_draft.draft_id)] = record
        return record

    def read_draft(
        self,
        scope: AuthoringToolSession | Mapping[str, object],
        draft_id: str,
        *,
        include_disabled: bool = False,
        include_deleted: bool = False,
    ) -> RecipePackDraftRecord | None:
        record = self._drafts.get(_draft_key(_coerce_scope(scope), draft_id))
        if record is None:
            return None
        if record.disabled and not include_disabled:
            return None
        if record.deleted and not include_deleted:
            return None
        return record

    def list_drafts(
        self,
        scope: AuthoringToolSession | Mapping[str, object],
        *,
        include_disabled: bool = False,
        include_deleted: bool = False,
    ) -> tuple[RecipePackDraftRecord, ...]:
        bot_id, owner_id, session_id = _scope_ids(_coerce_scope(scope))
        records = [
            record
            for (
                stored_owner_id,
                stored_bot_id,
                stored_session_id,
                _,
            ), record in self._drafts.items()
            if stored_owner_id == owner_id
            and stored_bot_id == bot_id
            and stored_session_id == session_id
        ]
        return tuple(
            record
            for record in records
            if (include_disabled or not record.disabled)
            and (include_deleted or not record.deleted)
        )

    def promote_draft_to_staging_candidate(
        self,
        scope: AuthoringToolSession | Mapping[str, object],
        draft_id: str,
        *,
        reason: str,
    ) -> RecipePackDraftRecord:
        coerced_scope = _coerce_scope(scope)
        key = _draft_key(coerced_scope, draft_id)
        record = self._drafts.get(key)
        if record is None:
            raise RecipePackStorageError("draft not found in current bot storage")
        if record.storage_status != "draft":
            raise RecipePackStorageError("only draft -> staging_candidate promotion is allowed")
        _reject_unsafe_storage_payload({"reason": reason})
        promoted = record.model_copy(
            update={
                "storageStatus": "staging_candidate",
                "revision": self._next_revision(),
            }
        )
        history = PromotionHistoryEntry(
            entryId=f"promotion:{draft_id}:{promoted.revision}",
            botId=record.bot_id,
            ownerId=record.owner_id,
            authoringSessionId=record.authoring_session_id,
            draftId=draft_id,
            fromStatus="draft",
            toStatus="staging_candidate",
            reasonDigest=digest_storage_content({"reason": reason}),
            revision=promoted.revision,
        )
        _reject_unsafe_storage_payload(promoted)
        _reject_unsafe_storage_payload(history)
        self._drafts[key] = promoted
        self._promotion_history.append(history)
        return promoted

    def disable_draft(
        self,
        scope: AuthoringToolSession | Mapping[str, object],
        draft_id: str,
        *,
        reason: str,
    ) -> RecipePackDraftRecord:
        return self._tombstone_draft(
            scope,
            draft_id,
            status="disabled",
            reason=reason,
        )

    def delete_draft(
        self,
        scope: AuthoringToolSession | Mapping[str, object],
        draft_id: str,
        *,
        reason: str,
    ) -> RecipePackDraftRecord:
        return self._tombstone_draft(
            scope,
            draft_id,
            status="deleted",
            reason=reason,
        )

    def list_promotion_history(
        self,
        scope: AuthoringToolSession | Mapping[str, object],
        draft_id: str | None = None,
    ) -> tuple[PromotionHistoryEntry, ...]:
        bot_id, owner_id, session_id = _scope_ids(_coerce_scope(scope))
        return tuple(
            entry
            for entry in self._promotion_history
            if entry.bot_id == bot_id
            and entry.owner_id == owner_id
            and entry.authoring_session_id == session_id
            and (draft_id is None or entry.draft_id == draft_id)
        )

    def save_version(
        self,
        scope: AuthoringToolSession | Mapping[str, object],
        version: RecipePackVersion | Mapping[str, object],
    ) -> RecipePackVersionRecord:
        coerced_scope = _coerce_scope(scope)
        coerced_version = (
            version
            if isinstance(version, RecipePackVersion)
            else RecipePackVersion.model_validate(version)
        )
        source = self._drafts.get(_draft_key(coerced_scope, coerced_version.source_draft_id))
        if source is None:
            raise RecipePackStorageError("source draft not found in current bot storage")
        if source.storage_status != "staging_candidate":
            raise RecipePackStorageError("source draft must be staging_candidate")
        if coerced_version.source_digest != source.draft_digest:
            raise RecipePackStorageError("version sourceDigest must match stored draft digest")
        bot_id, owner_id, session_id = _scope_ids(coerced_scope)
        record = RecipePackVersionRecord(
            botId=bot_id,
            ownerId=owner_id,
            authoringSessionId=session_id,
            version=coerced_version,
            versionDigest=digest_storage_content(coerced_version),
            revision=self._next_revision(),
            storageStatus=coerced_version.status,
        )
        _reject_unsafe_storage_payload(record)
        self._versions[
            (owner_id, bot_id, session_id, coerced_version.pack_id, coerced_version.version)
        ] = record
        return record

    def list_versions(
        self,
        scope: AuthoringToolSession | Mapping[str, object],
    ) -> tuple[RecipePackVersionRecord, ...]:
        bot_id, owner_id, session_id = _scope_ids(_coerce_scope(scope))
        return tuple(
            record
            for (
                stored_owner_id,
                stored_bot_id,
                stored_session_id,
                _,
                _,
            ), record in self._versions.items()
            if stored_owner_id == owner_id
            and stored_bot_id == bot_id
            and stored_session_id == session_id
        )

    def read_version(
        self,
        scope: AuthoringToolSession | Mapping[str, object],
        pack_id: str,
        version: str,
    ) -> RecipePackVersionRecord | None:
        bot_id, owner_id, session_id = _scope_ids(_coerce_scope(scope))
        return self._versions.get((owner_id, bot_id, session_id, pack_id, version))

    def save_compiled_snapshot_ref(
        self,
        scope: AuthoringToolSession | Mapping[str, object],
        snapshot_ref: CompiledSnapshotRef | Mapping[str, object],
    ) -> CompiledSnapshotRef:
        coerced_scope = _coerce_scope(scope)
        ref = (
            snapshot_ref
            if isinstance(snapshot_ref, CompiledSnapshotRef)
            else CompiledSnapshotRef.model_validate(snapshot_ref)
        )
        bot_id, owner_id, session_id = _scope_ids(coerced_scope)
        version_record = self._versions.get(
            (owner_id, bot_id, session_id, ref.pack_id, ref.version)
        )
        if version_record is None:
            raise RecipePackStorageError("version not found in current bot storage")
        source_record = self._drafts.get(_draft_key(coerced_scope, ref.source_draft_id))
        if source_record is None:
            raise RecipePackStorageError("source draft not found in current bot storage")
        if version_record.version.source_draft_id != ref.source_draft_id:
            raise RecipePackStorageError(
                "compiled snapshot sourceDraftId must match version sourceDraftId"
            )
        if version_record.version.source_digest != source_record.draft_digest:
            raise RecipePackStorageError(
                "compiled snapshot source provenance must match staged draft digest"
            )
        ref = ref.model_copy(update={"authoringSessionId": session_id})
        _reject_unsafe_storage_payload(ref)
        self._snapshots[(owner_id, bot_id, session_id, ref.ref_id)] = ref
        return ref

    def list_compiled_snapshot_refs(
        self,
        scope: AuthoringToolSession | Mapping[str, object],
    ) -> tuple[CompiledSnapshotRef, ...]:
        bot_id, owner_id, session_id = _scope_ids(_coerce_scope(scope))
        return tuple(
            ref
            for (
                stored_owner_id,
                stored_bot_id,
                stored_session_id,
                _,
            ), ref in self._snapshots.items()
            if stored_owner_id == owner_id
            and stored_bot_id == bot_id
            and stored_session_id == session_id
        )

    def read_compiled_snapshot_ref(
        self,
        scope: AuthoringToolSession | Mapping[str, object],
        ref_id: str,
    ) -> CompiledSnapshotRef | None:
        bot_id, owner_id, session_id = _scope_ids(_coerce_scope(scope))
        return self._snapshots.get((owner_id, bot_id, session_id, ref_id))

    def save_eval_result_ref(
        self,
        scope: AuthoringToolSession | Mapping[str, object],
        eval_ref: EvalResultRef | Mapping[str, object],
    ) -> EvalResultRef:
        coerced_scope = _coerce_scope(scope)
        ref = (
            eval_ref
            if isinstance(eval_ref, EvalResultRef)
            else EvalResultRef.model_validate(eval_ref)
        )
        _reject_unsafe_storage_payload(ref)
        _require_draft_exists(coerced_scope, ref.draft_id, self._drafts)
        bot_id, owner_id, session_id = _scope_ids(coerced_scope)
        ref = ref.model_copy(update={"authoringSessionId": session_id})
        _reject_unsafe_storage_payload(ref)
        self._eval_results[(owner_id, bot_id, session_id, ref.eval_result_id)] = ref
        return ref

    def list_eval_result_refs(
        self,
        scope: AuthoringToolSession | Mapping[str, object],
        draft_id: str | None = None,
    ) -> tuple[EvalResultRef, ...]:
        bot_id, owner_id, session_id = _scope_ids(_coerce_scope(scope))
        return tuple(
            ref
            for (
                stored_owner_id,
                stored_bot_id,
                stored_session_id,
                _,
            ), ref in self._eval_results.items()
            if stored_owner_id == owner_id
            and stored_bot_id == bot_id
            and stored_session_id == session_id
            and (draft_id is None or ref.draft_id == draft_id)
        )

    def read_eval_result_ref(
        self,
        scope: AuthoringToolSession | Mapping[str, object],
        eval_result_id: str,
    ) -> EvalResultRef | None:
        bot_id, owner_id, session_id = _scope_ids(_coerce_scope(scope))
        return self._eval_results.get((owner_id, bot_id, session_id, eval_result_id))

    def save_approval_ref(
        self,
        scope: AuthoringToolSession | Mapping[str, object],
        approval_ref: RecipePackApprovalRef | Mapping[str, object],
    ) -> RecipePackApprovalRef:
        coerced_scope = _coerce_scope(scope)
        ref = (
            approval_ref
            if isinstance(approval_ref, RecipePackApprovalRef)
            else RecipePackApprovalRef.model_validate(approval_ref)
        )
        _reject_unsafe_storage_payload(ref)
        _require_draft_exists(coerced_scope, ref.draft_id, self._drafts)
        bot_id, owner_id, session_id = _scope_ids(coerced_scope)
        ref = ref.model_copy(update={"authoringSessionId": session_id})
        _reject_unsafe_storage_payload(ref)
        self._approval_refs[(owner_id, bot_id, session_id, ref.approval_ref_id)] = ref
        return ref

    def list_approval_refs(
        self,
        scope: AuthoringToolSession | Mapping[str, object],
        draft_id: str | None = None,
    ) -> tuple[RecipePackApprovalRef, ...]:
        bot_id, owner_id, session_id = _scope_ids(_coerce_scope(scope))
        return tuple(
            ref
            for (
                stored_owner_id,
                stored_bot_id,
                stored_session_id,
                _,
            ), ref in self._approval_refs.items()
            if stored_owner_id == owner_id
            and stored_bot_id == bot_id
            and stored_session_id == session_id
            and (draft_id is None or ref.draft_id == draft_id)
        )

    def read_approval_ref(
        self,
        scope: AuthoringToolSession | Mapping[str, object],
        approval_ref_id: str,
    ) -> RecipePackApprovalRef | None:
        bot_id, owner_id, session_id = _scope_ids(_coerce_scope(scope))
        return self._approval_refs.get((owner_id, bot_id, session_id, approval_ref_id))

    def save_generated_plugin_proposal_artifact_ref(
        self,
        scope: AuthoringToolSession | Mapping[str, object],
        proposal_ref: GeneratedPluginProposalArtifactRef | Mapping[str, object],
    ) -> GeneratedPluginProposalArtifactRef:
        coerced_scope = _coerce_scope(scope)
        ref = (
            proposal_ref
            if isinstance(proposal_ref, GeneratedPluginProposalArtifactRef)
            else GeneratedPluginProposalArtifactRef.model_validate(proposal_ref)
        )
        _reject_unsafe_storage_payload(ref)
        draft_record = _require_draft_exists(coerced_scope, ref.draft_id, self._drafts)
        proposal_ids = {
            proposal.proposal_id for proposal in draft_record.draft.generated_plugin_proposals
        }
        if ref.proposal_id not in proposal_ids:
            raise RecipePackStorageError("proposalId must reference a draft proposal")
        bot_id, owner_id, session_id = _scope_ids(coerced_scope)
        ref = ref.model_copy(update={"authoringSessionId": session_id})
        _reject_unsafe_storage_payload(ref)
        self._proposal_artifacts[(owner_id, bot_id, session_id, ref.proposal_id)] = ref
        return ref

    def list_generated_plugin_proposal_artifact_refs(
        self,
        scope: AuthoringToolSession | Mapping[str, object],
        draft_id: str | None = None,
    ) -> tuple[GeneratedPluginProposalArtifactRef, ...]:
        bot_id, owner_id, session_id = _scope_ids(_coerce_scope(scope))
        return tuple(
            ref
            for (
                stored_owner_id,
                stored_bot_id,
                stored_session_id,
                _,
            ), ref in self._proposal_artifacts.items()
            if stored_owner_id == owner_id
            and stored_bot_id == bot_id
            and stored_session_id == session_id
            and (draft_id is None or ref.draft_id == draft_id)
        )

    def read_generated_plugin_proposal_artifact_ref(
        self,
        scope: AuthoringToolSession | Mapping[str, object],
        proposal_id: str,
    ) -> GeneratedPluginProposalArtifactRef | None:
        bot_id, owner_id, session_id = _scope_ids(_coerce_scope(scope))
        return self._proposal_artifacts.get((owner_id, bot_id, session_id, proposal_id))

    def _tombstone_draft(
        self,
        scope: AuthoringToolSession | Mapping[str, object],
        draft_id: str,
        *,
        status: Literal["disabled", "deleted"],
        reason: str,
    ) -> RecipePackDraftRecord:
        coerced_scope = _coerce_scope(scope)
        key = _draft_key(coerced_scope, draft_id)
        record = self._drafts.get(key)
        if record is None:
            raise RecipePackStorageError("draft not found in current bot storage")
        _reject_unsafe_storage_payload({"reason": reason})
        tombstoned = record.model_copy(
            update={
                "storageStatus": status,
                "revision": self._next_revision(),
                "disabled": status == "disabled",
                "deleted": status == "deleted",
                "tombstoneReasonDigest": digest_storage_content({"reason": reason}),
            }
        )
        _reject_unsafe_storage_payload(tombstoned)
        self._drafts[key] = tombstoned
        return tombstoned

    def _next_revision(self) -> int:
        self._revision += 1
        return self._revision


def digest_storage_content(value: object) -> str:
    return _digest_json(_to_jsonable(value))


def _digest_json(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return f"{_DIGEST_PREFIX}{hashlib.sha256(encoded.encode('utf-8')).hexdigest()}"


def _to_jsonable(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(by_alias=True)
    if isinstance(value, Mapping):
        return {str(key): _to_jsonable(nested) for key, nested in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_to_jsonable(nested) for nested in value]
    return value


def _coerce_scope(scope: AuthoringToolSession | Mapping[str, object]) -> AuthoringToolSession:
    if isinstance(scope, RecipeBuilderSession | AuthoringToolScope):
        return scope
    if not isinstance(scope, Mapping):
        raise RecipePackStorageError("scope must be a RecipeBuilderSession or AuthoringToolScope")
    try:
        return RecipeBuilderSession.model_validate(scope)
    except ValueError:
        return AuthoringToolScope.model_validate(scope)


def _coerce_draft(draft: RecipePackDraft | Mapping[str, object]) -> RecipePackDraft:
    if isinstance(draft, RecipePackDraft):
        return draft
    return RecipePackDraft.model_validate(draft)


def _scope_ids(scope: AuthoringToolSession) -> tuple[str, str, str]:
    return (scope.bot_id, scope.owner_id, scope.session_id)


def _draft_key(
    scope: AuthoringToolSession,
    draft_id: str,
) -> tuple[str, str, str, str]:
    bot_id, owner_id, session_id = _scope_ids(scope)
    return (owner_id, bot_id, session_id, draft_id)


def _require_scope_matches_draft(
    scope: AuthoringToolSession,
    draft: RecipePackDraft,
) -> None:
    bot_id, owner_id, session_id = _scope_ids(scope)
    if draft.bot_id != bot_id:
        raise RecipePackStorageError("scope botId must match draft botId")
    if draft.owner_id != owner_id:
        raise RecipePackStorageError("scope ownerId must match draft ownerId")
    if draft.authoring_session_id != session_id:
        raise RecipePackStorageError("scope sessionId must match draft authoringSessionId")


def _require_draft_exists(
    scope: AuthoringToolSession,
    draft_id: str,
    drafts: Mapping[tuple[str, str, str, str], RecipePackDraftRecord],
) -> RecipePackDraftRecord:
    record = drafts.get(_draft_key(scope, draft_id))
    if record is None:
        raise RecipePackStorageError("draft not found in current bot storage")
    return record


def _reject_unsafe_storage_payload(value: object) -> None:
    try:
        _scan_storage_payload(_to_jsonable(value), path="")
    except RecipePackStorageError:
        raise
    except ValueError as exc:
        raise RecipePackStorageError(str(exc)) from exc


def _scan_storage_payload(value: object, *, path: str) -> None:
    if isinstance(value, Mapping):
        for raw_key, nested in value.items():
            key = str(raw_key)
            normalized = _normalize_field_name(key)
            if normalized in _RAW_CREDENTIAL_FIELD_NAMES:
                raise RecipePackStorageError(f"raw secrets are not stored at {path or key}")
            if normalized in _RAW_IO_FIELD_NAMES:
                raise RecipePackStorageError(f"raw model data is not stored at {path or key}")
            if normalized in _RAW_CODE_FIELD_NAMES:
                raise RecipePackStorageError(
                    f"raw generated code is not stored at {path or key}"
                )
            next_path = f"{path}.{key}" if path else key
            _scan_storage_payload(nested, path=next_path)
        return
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        for index, nested in enumerate(value):
            _scan_storage_payload(nested, path=f"{path}.{index}" if path else str(index))
        return
    if isinstance(value, str):
        _reject_unsafe_string(value, path)


def _reject_unsafe_string(value: str, path: str) -> None:
    lowered = value.lower()
    if lowered.startswith("../") or lowered.startswith("./") or "/../" in lowered:
        raise RecipePackStorageError(f"private local paths are not stored at {path}")
    if _is_private_scheme(lowered):
        raise RecipePackStorageError(f"private local paths are not stored at {path}")
    if _URI_USERINFO_RE.search(value):
        raise RecipePackStorageError(f"raw secrets are not stored at {path}")
    if _SIGNED_QUERY_RE.search(value):
        raise RecipePackStorageError(f"raw secrets are not stored at {path}")
    if (
        lowered.startswith(_PRIVATE_REF_PREFIXES)
        or (_is_ref_like_path(path) and ("/" in value or "\\" in value))
        or (len(value) > 2 and value[1] == ":" and value[2] in {"/", "\\"})
    ):
        raise RecipePackStorageError(f"private local paths are not stored at {path}")
    if _SECRET_TEXT_RE.search(value):
        raise RecipePackStorageError(f"raw secrets are not stored at {path}")
    if _RAW_MODEL_TEXT_RE.search(value):
        raise RecipePackStorageError(f"raw model data is not stored at {path}")


def _normalize_field_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _is_ref_like_path(path: str) -> bool:
    normalized = _normalize_field_name(path)
    return (
        "ref" in normalized
        or normalized.endswith("id")
        or "uri" in normalized
        or "url" in normalized
    )


def _is_private_scheme(lowered: str) -> bool:
    match = re.match(r"^([a-z][a-z0-9+.-]*):", lowered)
    return bool(match and match.group(1) in _PRIVATE_URI_SCHEMES)


def _copy_update_alias(model_type: type[BaseModel], key: str) -> str:
    field = model_type.model_fields.get(key)
    if field is not None and field.alias is not None:
        return field.alias
    return key


def _reject_true(value: bool, alias: str) -> None:
    if value:
        raise ValueError(f"{alias} cannot be true in authoring storage contracts")


def _reject_false(value: bool, alias: str) -> None:
    if not value:
        raise ValueError(f"{alias} cannot be false in authoring storage contracts")


def _require_non_empty(value: str, field_label: str) -> str:
    if not value.strip():
        raise ValueError(f"{field_label} must be non-empty")
    return value


def _require_digest(value: str, field_name: str) -> str:
    suffix = value.removeprefix(_DIGEST_PREFIX)
    if not value.startswith(_DIGEST_PREFIX) or len(suffix) != 64 or any(
        char not in "0123456789abcdef" for char in suffix
    ):
        raise ValueError(f"{field_name} must be a sha256 digest")
    return value


__all__ = [
    "ApprovalRefStatus",
    "CompiledSnapshotRef",
    "CompiledSnapshotStorageStatus",
    "EvalResultRef",
    "EvalResultStorageStatus",
    "GeneratedPluginProposalArtifactRef",
    "LocalRecipePackStorage",
    "PromotionHistoryEntry",
    "RecipePackApprovalRef",
    "RecipePackDraftRecord",
    "RecipePackStorageError",
    "RecipePackStorageStatus",
    "RecipePackVersionRecord",
    "RecipePackVersionStorageStatus",
    "digest_storage_content",
]
