from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Literal, Self
from urllib.parse import unquote

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    field_validator,
    model_validator,
)


RecipeRestoreValidationStatus = Literal["valid", "blocked"]
RecipeRestoreValidationMode = Literal["dry_run"]

_DIGEST_PREFIX = "sha256:"
_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    hide_input_in_errors=True,
    revalidate_instances="always",
)
_PRIVATE_URI_SCHEMES = {
    "file",
    "gcs",
    "gs",
    "postgres",
    "postgresql",
    "s3",
    "supabase",
    "vault",
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
    "content",
    "contents",
    "filecontent",
    "filecontents",
    "rawcode",
    "generatedcode",
    "executablecode",
    "source",
    "sourcecode",
    "generatedsource",
}
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
_RUNTIME_ENTRYPOINT_FIELD_NAMES = {
    "runtimeentrypoint",
    "entrypoint",
    "toolhostpath",
    "runtimepluginloader",
}
_ACTIVATION_FIELD_NAMES = {
    "activation",
    "activationenabled",
    "activationeligibility",
    "activationeligible",
    "activationplan",
    "activationready",
    "activated",
    "allowautoactivation",
    "autoactivation",
    "runtimeactivationeligible",
}
_MEMORY_AUTHORITY_FIELD_NAMES = {
    "allowmemorywrite",
    "memorywrite",
    "memorywriteallowed",
    "memorywritesenabled",
}
_WORKSPACE_AUTHORITY_FIELD_NAMES = {
    "allowworkspacemutation",
    "workspacemutation",
    "workspacemutationallowed",
    "workspacemutationenabled",
}
_EXTERNAL_DELIVERY_AUTHORITY_FIELD_NAMES = {
    "allowexternaldelivery",
    "externaldelivery",
    "externaldeliveryallowed",
    "externaldeliveryrestored",
}
_SCHEDULE_AUTHORITY_FIELD_NAMES = {
    "allowschedulemutation",
    "schedulemutation",
    "schedulemutationallowed",
    "cronmutation",
    "schedulesrestored",
    "cronrestored",
    "webhooksrestored",
}
_LIVE_CONNECTOR_CREDENTIAL_FIELD_NAMES = {
    "allowliveconnectors",
    "liveconnectorcredentials",
    "connectorcredentialreadsallowed",
    "connectorcredentialsexposed",
    "connectorcredentials",
    "connectorcredentialsrestored",
}
_SEPARATE_AGENT_IDENTITY_FIELD_NAMES = {
    "agentid",
    "builderagentid",
    "builderagentidentity",
    "builderagentref",
}
_WRITE_APPLY_FIELD_NAMES = {
    "apply",
    "applyrestore",
    "promote",
    "promotetolive",
    "activate",
    "activatelive",
    "live",
    "livemode",
    "restorewritesenabled",
    "writerestore",
}
_NON_RESTORABLE_APPROVAL_FIELD_NAMES = {
    "foreignapprovalrefs",
    "externalapprovalauthoritystate",
    "approvalauthoritystate",
}
_NON_RESTORABLE_LIVE_STATE_FIELD_NAMES = {
    "livesessioncursors",
    "sessioncursors",
}
_NON_RESTORABLE_SANDBOX_FIELD_NAMES = {
    "inflightsandboxprocesses",
    "sandboxprocesses",
}
_NON_RESTORABLE_OBJECT_STORE_FIELD_NAMES = {
    "missingobjectstoreblobs",
    "objectstoreblobsrestored",
}
_NON_RESTORABLE_BILLING_FIELD_NAMES = {
    "hostedbillingquotastate",
    "billingstate",
    "quotastate",
}
_URI_USERINFO_RE = re.compile(r"^[a-z][a-z0-9+.-]*://[^/?#\s]*:[^/?#\s]*@")
_URI_SCHEME_RE = re.compile(r"^([a-z][a-z0-9+.-]*):", re.IGNORECASE)
_SIGNED_QUERY_RE = re.compile(
    r"(?i)(?:[?&]|^)(?:x-amz-signature|x-amz-credential|x-goog-signature|"
    r"x-goog-credential|signature|sig|access_key|accesskey)="
)
_SECRET_TEXT_RE = re.compile(
    r"(?i)(bearer\s+[A-Za-z0-9._-]{8,}|sk-(?:live|test)-[A-Za-z0-9_-]{8,}|"
    r"(?:api[_-]?key|apikey|private[_-]?key|secret[_-]?key|token|secret|password|"
    r"credential)\s*[:=?&]\s*[^\s,;]+)"
)
_RAW_MODEL_TEXT_RE = re.compile(
    r"(?i)\b(?:"
    r"raw[\s_-]*model[\s_-]*output|raw[\s_-]*output|raw[\s_-]*prompt|"
    r"hidden[\s_-]*instructions?|hidden[\s_-]*transcript|"
    r"chain[\s_-]*of[\s_-]*thought|tool[\s_-]*result[\s_-]*payload"
    r")\b"
)
_PRIVATE_PATH_TEXT_RE = re.compile(
    r"(?i)(?:"
    r"(?:/Users|/home|/root|/workspace|/app|/private|/var|/tmp|/etc|/opt|/srv|/mnt|~)"
    r"/[^\s,;)]+|"
    r"(?:\.\.?/)[^\s,;)]+|"
    r"(?:infra|apps|src|scripts|supabase|memory|outputs|tests|magi_agent|"
    r"\.claude|\.worktrees)/[^\s,;)]+|"
    r"[A-Za-z]:[\\/][^\s,;)]+"
    r")"
)
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_STATUS_TOKEN_RE = re.compile(
    r"(?i)(?:^|[^a-z0-9])"
    r"(?:active|activate|activated|activation|enabled|enable|live|promote|promoted|"
    r"promotion|runtime)"
    r"(?:$|[^a-z0-9])"
)
_SOURCE_CODE_TEXT_RE = re.compile(
    r"(?ms)(?:```|^\s*#!|\bdef\s+\w+\s*\(|\bclass\s+\w+\s*[:(]|"
    r"\bfunction\s+\w*\s*\(|=>\s*\{|^\s*(?:import|from)\s+[\w.]+|"
    r"\b(?:console\.log|print)\s*\()"
)
_SEPARATE_BUILDER_AGENT_TEXT_RE = re.compile(r"(?i)\bbuilder[\s_.:-]*agent\b")
_AFFIRMATIVE_AUTHORITY_TEXT_RE = re.compile(
    r"(?is)\b(?:grant|grants|allow|allows|enable|enables|may|can|will|request|requests|"
    r"requested|write|mutate|create|deliver|access|use|read|restore|restores|restored|"
    r"resume|resumes|resumed)\b(?!\s+no\b)"
    r".{0,80}\b"
    r"(?:live[\s_-]+connector[\s_-]+credentials?|connector[\s_-]+credentials?|"
    r"foreign[\s_-]+approval[\s_-]+refs?|external[\s_-]+approval[\s_-]+authority|"
    r"memory[\s_-]+write|workspace[\s_-]+mutation|external[\s_-]+delivery|"
    r"schedule[\s_-]+mutation|schedules?|cron|webhooks?|live[\s_-]+session[\s_-]+cursors?|"
    r"in[\s_-]*flight[\s_-]+sandbox[\s_-]+processes?|sandbox[\s_-]+processes?|"
    r"object[\s_-]+store[\s_-]+blobs?|hosted[\s_-]+billing|billing|quota|"
    r"production\s+workspace)\b"
)
_AFFIRMATIVE_AUTHORITY_ACTION_RE = re.compile(
    r"(?is)\b(?:"
    r"(?:mutate|mutates|mutating)[\s_-]+(?:the[\s_-]+)?workspace|"
    r"(?:write|writes|writing)[\s_-]+(?:to[\s_-]+)?(?:the[\s_-]+)?memory|"
    r"(?:create|creates|creating)[\s_-]+(?:scheduled[\s_-]+jobs?|cron[\s_-]+jobs?|schedules?)|"
    r"(?:deliver|delivers|delivering)\b.{0,40}\bexternally|"
    r"externally\b.{0,40}\b(?:deliver|delivers|delivering)|"
    r"(?:access|accesses|use|uses|using|read|reads|reading)[\s_-]+"
    r"(?:live[\s_-]+)?connector[\s_-]+credentials?|"
    r"(?:restore|restores|restored)[\s_-]+(?:foreign[\s_-]+approval[\s_-]+refs?|"
    r"external[\s_-]+approval[\s_-]+authority|schedules?|cron[\s_-]+jobs?|webhooks?|"
    r"hosted[\s_-]+billing|quota)|"
    r"(?:resume|resumes|resumed)[\s_-]+(?:live[\s_-]+session[\s_-]+cursors?|"
    r"in[\s_-]*flight[\s_-]+sandbox[\s_-]+processes?)|"
    r"(?:live[\s_-]+session[\s_-]+cursors?|in[\s_-]*flight[\s_-]+sandbox[\s_-]+processes?|"
    r"hosted[\s_-]+billing|billing|quota)\b.{0,60}\b(?:restore|restores|restored|"
    r"resume|resumes|resumed)|"
    r"object[\s_-]+store[\s_-]+blobs?\b.{0,60}\b(?:restore|restores|restored)"
    r")\b"
)


class _BackupRestoreModel(BaseModel):
    model_config = _MODEL_CONFIG

    @classmethod
    def model_construct(cls, *args: object, **kwargs: object) -> Self:
        raise TypeError("model_construct is disabled for recipe backup/restore contracts")

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

    @model_validator(mode="before")
    @classmethod
    def _reject_unsafe_input_fields(cls, data: object) -> object:
        _reject_unsafe_input(data)
        return data


class RecipeBackupScope(_BackupRestoreModel):
    owner_id: str = Field(alias="ownerId")
    bot_id: str = Field(alias="botId")
    session_id: str = Field(alias="sessionId")

    @field_validator("owner_id", "bot_id", "session_id")
    @classmethod
    def _validate_ref(cls, value: str) -> str:
        return _require_safe_ref(value, "scope")


class RecipeBackupArtifactRef(_BackupRestoreModel):
    artifact_type: Literal[
        "sqlite_backup",
        "export_bundle",
        "artifact_index",
        "ledger_manifest",
    ] = Field(alias="artifactType")
    path: str
    digest: str
    byte_size: StrictInt = Field(ge=0, alias="byteSize")
    media_type: str = Field(alias="mediaType")
    redaction_status: Literal["public_safe", "redacted"] = Field(alias="redactionStatus")
    summary: str

    @field_validator("artifact_type")
    @classmethod
    def _validate_artifact_type(cls, value: str) -> str:
        return _require_safe_ref(value, "artifactType")

    @field_validator("path")
    @classmethod
    def _validate_path(cls, value: str) -> str:
        return _require_safe_artifact_path(value)

    @field_validator("digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return _require_digest(value, "digest")

    @field_validator("media_type")
    @classmethod
    def _validate_media_type(cls, value: str) -> str:
        if not value.strip() or "/" not in value or any(char.isspace() for char in value):
            raise ValueError("mediaType must be a concrete media type")
        _reject_unsafe_string(value, "mediaType")
        return value

    @field_validator("summary")
    @classmethod
    def _validate_summary(cls, value: str) -> str:
        return _require_public_summary(value, "summary")


class RecipeBackupLedgerRef(_BackupRestoreModel):
    ledger_ref: str = Field(alias="ledgerRef")
    ledger_head_digest: str = Field(alias="ledgerHeadDigest")
    summary: str

    @field_validator("ledger_ref")
    @classmethod
    def _validate_ledger_ref(cls, value: str) -> str:
        return _require_safe_ref(value, "ledgerRef")

    @field_validator("ledger_head_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return _require_digest(value, "ledgerHeadDigest")

    @field_validator("summary")
    @classmethod
    def _validate_summary(cls, value: str) -> str:
        return _require_public_summary(value, "summary")


class _RecipeBackupCheckMetadataRef(_BackupRestoreModel):
    ref: str
    digest: str
    summary: str

    @field_validator("ref")
    @classmethod
    def _validate_ref(cls, value: str) -> str:
        return _require_safe_ref(value, "ref")

    @field_validator("digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return _require_digest(value, "digest")

    @field_validator("summary")
    @classmethod
    def _validate_summary(cls, value: str) -> str:
        return _require_public_summary(value, "summary")


class RecipeBackupManifest(_BackupRestoreModel):
    schema_version: Literal["recipe_backup_manifest.v1"] = Field(alias="schemaVersion")
    backup_id: str = Field(alias="backupId")
    scope: RecipeBackupScope
    durable_store_ref: str = Field(alias="durableStoreRef")
    artifact_index_digest: str = Field(alias="artifactIndexDigest")
    ledger: RecipeBackupLedgerRef
    export_package_refs: tuple[str, ...] = Field(
        min_length=1, alias="exportPackageRefs"
    )
    export_package_digests: tuple[str, ...] = Field(
        min_length=1, alias="exportPackageDigests"
    )
    backup_artifacts: tuple[RecipeBackupArtifactRef, ...] = Field(
        min_length=1, alias="backupArtifacts"
    )
    created_by_ref: str = Field(alias="createdByRef")
    backup_mode: Literal["metadata_refs_only"] = Field(
        default="metadata_refs_only", alias="backupMode"
    )
    integrity_checks: tuple[str, ...] = Field(min_length=1, alias="integrityChecks")
    check_metadata: tuple[_RecipeBackupCheckMetadataRef, ...] = Field(
        min_length=1, alias="checkMetadata"
    )

    @field_validator("backup_id", "durable_store_ref", "created_by_ref")
    @classmethod
    def _validate_refs(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_safe_ref(value, "backupId/durableStoreRef/createdByRef")

    @field_validator("artifact_index_digest")
    @classmethod
    def _validate_artifact_index_digest(cls, value: str) -> str:
        return _require_digest(value, "artifactIndexDigest")

    @field_validator("export_package_refs", "integrity_checks")
    @classmethod
    def _validate_string_refs(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            _require_safe_ref(value, "exportPackageRefs/integrityChecks")
        return values

    @field_validator("export_package_digests")
    @classmethod
    def _validate_export_package_digests(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            _require_digest(value, "exportPackageDigests")
        return values

    @model_validator(mode="after")
    def _require_export_package_digest_alignment(self) -> RecipeBackupManifest:
        if len(self.export_package_refs) != len(self.export_package_digests):
            raise ValueError("exportPackageRefs and exportPackageDigests must align")
        return self


class RecipeRestoreValidationBlocker(_BackupRestoreModel):
    code: str
    message: str
    ref: str | None = None

    @field_validator("code")
    @classmethod
    def _validate_code(cls, value: str) -> str:
        return _require_safe_ref(value, "code")

    @field_validator("message")
    @classmethod
    def _validate_message(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message must be non-empty")
        _reject_unsafe_string(value, "message")
        return value

    @field_validator("ref")
    @classmethod
    def _validate_ref(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_safe_ref(value, "ref")


class RecipeRestoreValidationRequest(_BackupRestoreModel):
    target_scope: RecipeBackupScope = Field(alias="targetScope")
    backup_manifest: RecipeBackupManifest = Field(alias="backupManifest")
    backup_digest: str = Field(alias="backupDigest")
    validation_mode: RecipeRestoreValidationMode = Field(
        default="dry_run", alias="validationMode"
    )
    restore_writes_enabled: StrictBool = Field(
        default=False, alias="restoreWritesEnabled"
    )
    activation_enabled: StrictBool = Field(default=False, alias="activationEnabled")
    runtime_activation_eligible: StrictBool = Field(
        default=False, alias="runtimeActivationEligible"
    )
    connector_credentials_restored: StrictBool = Field(
        default=False, alias="connectorCredentialsRestored"
    )
    schedules_restored: StrictBool = Field(default=False, alias="schedulesRestored")
    memory_writes_enabled: StrictBool = Field(default=False, alias="memoryWritesEnabled")
    workspace_mutation_enabled: StrictBool = Field(
        default=False, alias="workspaceMutationEnabled"
    )
    live_mode: StrictBool = Field(default=False, alias="liveMode")

    @field_validator("backup_digest")
    @classmethod
    def _validate_backup_digest(cls, value: str) -> str:
        return _require_digest(value, "backupDigest")

    @model_validator(mode="after")
    def _require_dry_run_only(self) -> RecipeRestoreValidationRequest:
        if self.validation_mode != "dry_run":
            raise ValueError("validationMode must be dry_run")
        _reject_true(self.restore_writes_enabled, "restoreWritesEnabled")
        _reject_true(self.activation_enabled, "activationEnabled")
        _reject_true(self.runtime_activation_eligible, "runtimeActivationEligible")
        _reject_true(
            self.connector_credentials_restored, "connectorCredentialsRestored"
        )
        _reject_true(self.schedules_restored, "schedulesRestored")
        _reject_true(self.memory_writes_enabled, "memoryWritesEnabled")
        _reject_true(self.workspace_mutation_enabled, "workspaceMutationEnabled")
        _reject_true(self.live_mode, "liveMode")
        return self


class RecipeRestoreValidationResult(_BackupRestoreModel):
    status: RecipeRestoreValidationStatus
    blockers: tuple[RecipeRestoreValidationBlocker, ...] = ()
    backup_digest: str = Field(alias="backupDigest")
    accepted_artifact_refs: tuple[str, ...] = Field(
        default=(), alias="acceptedArtifactRefs"
    )
    target_scope: RecipeBackupScope = Field(alias="targetScope")
    restore_writes_enabled: StrictBool = Field(
        default=False, alias="restoreWritesEnabled"
    )
    activation_enabled: StrictBool = Field(default=False, alias="activationEnabled")
    runtime_activation_eligible: StrictBool = Field(
        default=False, alias="runtimeActivationEligible"
    )
    connector_credentials_restored: StrictBool = Field(
        default=False, alias="connectorCredentialsRestored"
    )
    schedules_restored: StrictBool = Field(default=False, alias="schedulesRestored")
    memory_writes_enabled: StrictBool = Field(default=False, alias="memoryWritesEnabled")
    workspace_mutation_enabled: StrictBool = Field(
        default=False, alias="workspaceMutationEnabled"
    )

    @field_validator("backup_digest")
    @classmethod
    def _validate_backup_digest(cls, value: str) -> str:
        return _require_digest(value, "backupDigest")

    @field_validator("accepted_artifact_refs")
    @classmethod
    def _validate_accepted_artifact_refs(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            _require_safe_artifact_path(value)
        return values

    @model_validator(mode="after")
    def _require_consistency(self) -> RecipeRestoreValidationResult:
        _reject_true(self.restore_writes_enabled, "restoreWritesEnabled")
        _reject_true(self.activation_enabled, "activationEnabled")
        _reject_true(self.runtime_activation_eligible, "runtimeActivationEligible")
        _reject_true(
            self.connector_credentials_restored, "connectorCredentialsRestored"
        )
        _reject_true(self.schedules_restored, "schedulesRestored")
        _reject_true(self.memory_writes_enabled, "memoryWritesEnabled")
        _reject_true(self.workspace_mutation_enabled, "workspaceMutationEnabled")
        if self.status == "valid" and self.blockers:
            raise ValueError("valid restore validation cannot include blockers")
        if self.status == "blocked" and not self.blockers:
            raise ValueError("blocked restore validation requires blockers")
        if self.status == "blocked" and self.accepted_artifact_refs:
            raise ValueError("blocked restore validation cannot accept artifact refs")
        return self


def digest_recipe_backup_manifest(
    manifest: RecipeBackupManifest | Mapping[str, object],
) -> str:
    coerced = RecipeBackupManifest.model_validate(_to_validation_payload(manifest))
    return _digest_json(_to_jsonable(coerced))


def validate_recipe_restore_request(
    request: RecipeRestoreValidationRequest | Mapping[str, object],
) -> RecipeRestoreValidationResult:
    coerced = RecipeRestoreValidationRequest.model_validate(_to_validation_payload(request))
    expected_digest = digest_recipe_backup_manifest(coerced.backup_manifest)
    if coerced.backup_digest != expected_digest:
        return _blocked_result(
            coerced,
            code="backup_digest_mismatch",
            message="backupDigest must match the recipe backup manifest",
        )
    if coerced.target_scope != coerced.backup_manifest.scope:
        return _blocked_result(
            coerced,
            code="backup_scope_mismatch",
            message="targetScope must match the recipe backup manifest scope",
            ref=coerced.backup_manifest.backup_id,
        )
    return RecipeRestoreValidationResult(
        status="valid",
        blockers=(),
        backupDigest=coerced.backup_digest,
        acceptedArtifactRefs=tuple(
            artifact.path for artifact in coerced.backup_manifest.backup_artifacts
        ),
        targetScope=coerced.target_scope,
        restoreWritesEnabled=False,
        activationEnabled=False,
        runtimeActivationEligible=False,
        connectorCredentialsRestored=False,
        schedulesRestored=False,
        memoryWritesEnabled=False,
        workspaceMutationEnabled=False,
    )


def _blocked_result(
    request: RecipeRestoreValidationRequest,
    *,
    code: str,
    message: str,
    ref: str | None = None,
) -> RecipeRestoreValidationResult:
    return RecipeRestoreValidationResult(
        status="blocked",
        blockers=(RecipeRestoreValidationBlocker(code=code, message=message, ref=ref),),
        backupDigest=request.backup_digest,
        acceptedArtifactRefs=(),
        targetScope=request.target_scope,
        restoreWritesEnabled=False,
        activationEnabled=False,
        runtimeActivationEligible=False,
        connectorCredentialsRestored=False,
        schedulesRestored=False,
        memoryWritesEnabled=False,
        workspaceMutationEnabled=False,
    )


def _reject_unsafe_input(value: object) -> None:
    if isinstance(value, Mapping):
        for raw_key, nested in value.items():
            key = str(raw_key)
            normalized = _normalize_field_name(key)
            if normalized in _LIVE_CONNECTOR_CREDENTIAL_FIELD_NAMES and nested is not False:
                raise ValueError("live connector credentials are not accepted")
            if normalized in _RAW_CREDENTIAL_FIELD_NAMES:
                raise ValueError("raw credential fields are not accepted")
            if normalized in _RAW_IO_FIELD_NAMES:
                raise ValueError("raw prompt/output fields are not accepted")
            if normalized in _RAW_CODE_FIELD_NAMES:
                raise ValueError("raw generated code fields are not accepted")
            if normalized in _RUNTIME_ENTRYPOINT_FIELD_NAMES:
                raise ValueError("runtimeEntrypoint is not accepted in backup manifests")
            if normalized in _SEPARATE_AGENT_IDENTITY_FIELD_NAMES:
                raise ValueError("separate Builder Agent identity is not accepted")
            if normalized in _NON_RESTORABLE_APPROVAL_FIELD_NAMES:
                raise ValueError("approval authority state is not restorable")
            if normalized in _NON_RESTORABLE_LIVE_STATE_FIELD_NAMES:
                raise ValueError("live session cursors are not restorable")
            if normalized in _NON_RESTORABLE_SANDBOX_FIELD_NAMES:
                raise ValueError("in-flight sandbox processes are not restorable")
            if normalized in _NON_RESTORABLE_OBJECT_STORE_FIELD_NAMES:
                raise ValueError("object-store blobs missing from backup are not restorable")
            if normalized in _NON_RESTORABLE_BILLING_FIELD_NAMES:
                raise ValueError("hosted billing and quota state is not restorable")
            if normalized in _ACTIVATION_FIELD_NAMES and nested is not False:
                raise ValueError("activation flags are not accepted")
            if normalized in _MEMORY_AUTHORITY_FIELD_NAMES and nested is not False:
                raise ValueError("memory write authority is not accepted")
            if normalized in _WORKSPACE_AUTHORITY_FIELD_NAMES and nested is not False:
                raise ValueError("workspace mutation authority is not accepted")
            if normalized in _EXTERNAL_DELIVERY_AUTHORITY_FIELD_NAMES and nested is not False:
                raise ValueError("external delivery authority is not accepted")
            if normalized in _SCHEDULE_AUTHORITY_FIELD_NAMES and nested is not False:
                raise ValueError("schedule mutation authority is not accepted")
            if normalized in _WRITE_APPLY_FIELD_NAMES and nested is True:
                raise ValueError("restore validation is dry-run only; live mode is not accepted")
            _reject_unsafe_input(nested)
        return
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        for nested in value:
            _reject_unsafe_input(nested)


def _require_safe_artifact_path(value: str) -> str:
    for candidate in _decoded_candidates(value):
        if not candidate.strip():
            raise ValueError("path must be non-empty")
        if "\\" in candidate:
            raise ValueError("path must not contain backslashes")
        if _URI_SCHEME_RE.match(candidate):
            raise ValueError("path must be a relative artifact path")
        if candidate.startswith(("/", "~")):
            raise ValueError("path must be a public relative artifact path")
        if _WINDOWS_DRIVE_RE.match(candidate):
            raise ValueError("path must not be a Windows drive path")
        parts = candidate.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise ValueError("path must not contain traversal or symlink escape segments")
        _reject_unsafe_string(candidate, "path")
    return value


def _require_safe_ref(value: str, field_name: str) -> str:
    for candidate in _decoded_candidates(value):
        if not candidate.strip():
            raise ValueError(f"{field_name} must be non-empty")
        if "\\" in candidate:
            raise ValueError(f"{field_name} must not contain private path separators")
        if _URI_SCHEME_RE.match(candidate):
            raise ValueError(f"{field_name} must not contain URI schemes")
        if (
            candidate.startswith(("/", "~", "../", "./"))
            or "/../" in candidate
            or candidate.endswith("/..")
            or "/./" in candidate
            or candidate.endswith("/.")
        ):
            raise ValueError(f"{field_name} must not contain private paths")
        if _WINDOWS_DRIVE_RE.match(candidate):
            raise ValueError(f"{field_name} must not be a Windows drive path")
        _reject_status_tokens(candidate, field_name)
        _reject_unsafe_string(candidate, field_name)
    return value


def _require_public_summary(value: str, field_name: str) -> str:
    if not value.strip():
        raise ValueError(f"{field_name} must be non-empty")
    _reject_unsafe_string(value, field_name)
    return value


def _reject_unsafe_string(value: str, field_name: str) -> None:
    for candidate in _decoded_candidates(value):
        lowered = candidate.lower()
        if _URI_USERINFO_RE.search(candidate):
            raise ValueError(f"raw secrets are not accepted in {field_name}")
        if _SIGNED_QUERY_RE.search(candidate):
            raise ValueError(f"raw secrets are not accepted in {field_name}")
        if _SECRET_TEXT_RE.search(candidate):
            raise ValueError(f"raw secrets are not accepted in {field_name}")
        if _RAW_MODEL_TEXT_RE.search(candidate):
            raise ValueError(f"raw model data is not accepted in {field_name}")
        if _SOURCE_CODE_TEXT_RE.search(candidate):
            raise ValueError(f"raw source code is not accepted in {field_name}")
        if _SEPARATE_BUILDER_AGENT_TEXT_RE.search(candidate):
            raise ValueError(f"separate Builder Agent identity is not accepted in {field_name}")
        _reject_authority_text(candidate, field_name)
        if _is_private_scheme(lowered):
            raise ValueError(f"private URI schemes are not accepted in {field_name}")
        if _PRIVATE_PATH_TEXT_RE.search(candidate):
            raise ValueError(f"private paths are not accepted in {field_name}")


def _is_private_scheme(lowered: str) -> bool:
    match = _URI_SCHEME_RE.match(lowered)
    return bool(match and match.group(1) in _PRIVATE_URI_SCHEMES)


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


def _to_validation_payload(value: object) -> object:
    if isinstance(value, BaseModel):
        payload: dict[str, object] = {}
        model_fields = type(value).model_fields
        for name, field in model_fields.items():
            if not hasattr(value, name):
                continue
            alias = field.alias or name
            payload[alias] = _to_validation_payload(getattr(value, name))
        for raw_key, nested in value.__dict__.items():
            if raw_key in model_fields:
                continue
            payload[str(raw_key)] = _to_validation_payload(nested)
        extra = getattr(value, "__pydantic_extra__", None)
        if isinstance(extra, Mapping):
            for raw_key, nested in extra.items():
                payload[str(raw_key)] = _to_validation_payload(nested)
        return payload
    if isinstance(value, Mapping):
        return {str(key): _to_validation_payload(nested) for key, nested in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_to_validation_payload(nested) for nested in value]
    return value


def _normalize_field_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _decoded_candidates(value: str) -> tuple[str, ...]:
    if len(value) > 2048:
        raise ValueError("encoded values are too large to validate")
    candidates = [value]
    decoded = value
    for _ in range(20):
        next_decoded = unquote(decoded)
        if next_decoded == decoded:
            break
        decoded = next_decoded
        candidates.append(decoded)
    else:
        raise ValueError("percent-encoded values must decode to a stable value")
    return tuple(dict.fromkeys(candidates))


def _reject_status_tokens(value: str, field_name: str) -> None:
    if _STATUS_TOKEN_RE.search(value):
        raise ValueError(f"activation/runtime status tokens are not accepted in {field_name}")


def _reject_authority_text(value: str, field_name: str) -> None:
    if _AFFIRMATIVE_AUTHORITY_TEXT_RE.search(value) or _AFFIRMATIVE_AUTHORITY_ACTION_RE.search(
        value
    ):
        raise ValueError(f"authoring authority is not accepted in {field_name}")


def _copy_update_alias(model_type: type[BaseModel], key: str) -> str:
    field = model_type.model_fields.get(key)
    if field is not None and field.alias is not None:
        return field.alias
    return key


def _reject_true(value: bool, alias: str) -> None:
    if value:
        raise ValueError(f"{alias} cannot be true in recipe backup/restore contracts")


def _require_digest(value: str, field_name: str) -> str:
    suffix = value.removeprefix(_DIGEST_PREFIX)
    if not value.startswith(_DIGEST_PREFIX) or len(suffix) != 64 or any(
        char not in "0123456789abcdef" for char in suffix
    ):
        raise ValueError(f"{field_name} must be a sha256 digest")
    return value


__all__ = [
    "RecipeBackupArtifactRef",
    "RecipeBackupLedgerRef",
    "RecipeBackupManifest",
    "RecipeBackupScope",
    "RecipeRestoreValidationBlocker",
    "RecipeRestoreValidationRequest",
    "RecipeRestoreValidationResult",
    "digest_recipe_backup_manifest",
    "validate_recipe_restore_request",
]
