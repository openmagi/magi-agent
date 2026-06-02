from __future__ import annotations

from collections.abc import Mapping
import hashlib
import re
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from magi_agent.tools.read_ledger import (
    ReadLedger,
    WorkspaceMutationReadCheck,
    WorkspaceMutationReadDecision,
    digest_ref,
    is_unsafe_workspace_path,
    safe_workspace_relative_path,
    workspace_content_digest,
    workspace_path_ref,
)


CodingMutationToolName = Literal["FileEdit", "FileWrite", "PatchApply"]
CodingMutationStatus = Literal[
    "disabled",
    "blocked",
    "approval_required",
    "applied_local_fake",
]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)
_SHA256_REF_RE = re.compile(r"^sha256:[a-f0-9]{64}$")


class CodingMutationConfig(BaseModel):
    model_config = _MODEL_CONFIG

    enabled: bool = False
    local_fake_apply_enabled: bool = Field(default=False, alias="localFakeApplyEnabled")
    production_workspace_mutation_enabled: Literal[False] = Field(
        default=False,
        alias="productionWorkspaceMutationEnabled",
    )


class CodingMutationAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    recipe_enabled: bool = Field(default=False, alias="recipeEnabled")
    local_fake_apply_enabled: bool = Field(default=False, alias="localFakeApplyEnabled")
    filesystem_write_attempted: Literal[False] = Field(
        default=False,
        alias="filesystemWriteAttempted",
    )
    production_workspace_mutation_enabled: Literal[False] = Field(
        default=False,
        alias="productionWorkspaceMutationEnabled",
    )
    live_tool_attached: Literal[False] = Field(default=False, alias="liveToolAttached")
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")
    user_visible_output_allowed: Literal[False] = Field(
        default=False,
        alias="userVisibleOutputAllowed",
    )

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set
        values.update(_false_authority_overrides())
        return cls.model_validate(values)

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        data = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            data.update(dict(update))
        data.update(_false_authority_overrides())
        return type(self).model_validate(data)

    @field_serializer(
        "filesystem_write_attempted",
        "production_workspace_mutation_enabled",
        "live_tool_attached",
        "route_attached",
        "user_visible_output_allowed",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class CodingMutationRequest(BaseModel):
    model_config = _MODEL_CONFIG

    tool_name: CodingMutationToolName = Field(alias="toolName")
    session_id: str = Field(alias="sessionId")
    workspace_ref: str = Field(alias="workspaceRef")
    path: str
    current_digest: str | None = Field(default=None, alias="currentDigest")
    current_text: str | None = Field(default=None, repr=False, alias="currentText")
    old_string: str | None = Field(default=None, repr=False, alias="oldString")
    new_string: str | None = Field(default=None, repr=False, alias="newString")
    patch: str | None = Field(default=None, repr=False)
    mutation_kind: Literal["edit", "create", "replace", "patch"] = Field(
        default="edit",
        alias="mutationKind",
    )
    replace_all: bool = Field(default=False, alias="replaceAll")
    explicit_approval: bool = Field(default=False, alias="explicitApproval")

    @field_validator("session_id", "workspace_ref")
    @classmethod
    def _non_empty_public_ref(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("coding mutation refs must be non-empty")
        if any(marker in value.lower() for marker in ("/users/", "/workspace/", "token", "secret")):
            raise ValueError("coding mutation refs must not contain private data")
        return value.strip()[:180]

    @field_validator("path")
    @classmethod
    def _safe_path(cls, value: str) -> str:
        return safe_workspace_relative_path(value)

    @field_validator("current_digest")
    @classmethod
    def _valid_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _SHA256_REF_RE.fullmatch(value):
            raise ValueError("currentDigest must be sha256:<64 hex chars>")
        return value


class CodingMutationMaterialization(BaseModel):
    model_config = _MODEL_CONFIG

    recipe_id: str = Field(default="openmagi.dev-coding.mutation", alias="recipeId")
    tool_names: tuple[CodingMutationToolName, ...] = Field(
        default=("FileWrite", "FileEdit", "PatchApply"),
        alias="toolNames",
    )
    ledger_required: bool = Field(default=True, alias="ledgerRequired")
    policy_refs: tuple[str, ...] = Field(
        default=(
            "coding-policy:read-before-edit",
            "coding-policy:exact-old-string",
            "coding-policy:approval-required",
        ),
        alias="policyRefs",
    )
    attachment_flags: Mapping[str, Literal[False]] = Field(alias="attachmentFlags")

    def public_projection(self) -> dict[str, object]:
        return {
            "recipeId": self.recipe_id,
            "toolNames": list(self.tool_names),
            "ledgerRequired": self.ledger_required,
            "policyRefs": list(self.policy_refs),
            "attachmentFlags": _false_authority_overrides(),
        }


class CodingMutationDecision(BaseModel):
    model_config = _MODEL_CONFIG

    status: CodingMutationStatus
    tool_name: CodingMutationToolName = Field(alias="toolName")
    reason_codes: tuple[str, ...] = Field(alias="reasonCodes")
    path_ref: str = Field(alias="pathRef")
    receipt_ref: str = Field(alias="receiptRef")
    read_ledger: Mapping[str, object] | None = Field(default=None, alias="readLedger")
    old_digest_ref: str | None = Field(default=None, alias="oldDigestRef")
    new_digest_ref: str | None = Field(default=None, alias="newDigestRef")
    diff_summary: Mapping[str, object] = Field(default_factory=dict, alias="diffSummary")
    authority_flags: CodingMutationAuthorityFlags = Field(alias="authorityFlags")

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set
        flags = values.get("authorityFlags")
        values["authorityFlags"] = _coerce_authority_flags(flags)
        return cls.model_validate(values)

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        data = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            data.update(dict(update))
        data["authorityFlags"] = _coerce_authority_flags(data.get("authorityFlags"))
        return type(self).model_validate(data)

    def public_projection(self) -> dict[str, object]:
        projection: dict[str, object] = {
            "status": self.status,
            "toolName": self.tool_name,
            "reasonCodes": list(self.reason_codes),
            "pathRef": self.path_ref,
            "receiptRef": self.receipt_ref,
            "diffSummary": _safe_diff_summary(self.diff_summary),
            "authorityFlags": {
                "recipeEnabled": bool(self.authority_flags.recipe_enabled),
                "localFakeApplyEnabled": bool(
                    self.authority_flags.local_fake_apply_enabled,
                ),
                "filesystemWriteAttempted": False,
                "productionWorkspaceMutationEnabled": False,
                "liveToolAttached": False,
                "routeAttached": False,
                "userVisibleOutputAllowed": False,
            },
        }
        if self.read_ledger is not None:
            projection["readLedger"] = _safe_read_ledger_projection(self.read_ledger)
        if self.old_digest_ref is not None:
            projection["oldDigestRef"] = self.old_digest_ref
        if self.new_digest_ref is not None:
            projection["newDigestRef"] = self.new_digest_ref
        return projection


class CodingMutationRecipe:
    """Coding-owned mutation semantics. It produces receipts and never writes files."""

    def __init__(
        self,
        config: CodingMutationConfig | None = None,
        *,
        read_ledger: ReadLedger | None = None,
    ) -> None:
        self.config = config or CodingMutationConfig()
        self.read_ledger = read_ledger

    def evaluate(self, request: CodingMutationRequest) -> CodingMutationDecision:
        flags = CodingMutationAuthorityFlags(
            recipeEnabled=self.config.enabled,
            localFakeApplyEnabled=self.config.local_fake_apply_enabled,
            **_false_authority_overrides(),
        )
        path_ref = workspace_path_ref(request.workspace_ref, request.path)
        if not self.config.enabled:
            return _decision(
                request,
                "disabled",
                ("coding_mutation_recipe_disabled",),
                path_ref,
                flags,
            )
        if not _mutation_kind_matches_tool(request):
            return _decision(
                request,
                "blocked",
                ("mutation_kind_tool_mismatch",),
                path_ref,
                flags,
            )
        if request.tool_name == "PatchApply":
            return self._evaluate_patch_apply(request, path_ref, flags)
        if request.tool_name == "FileWrite":
            return self._evaluate_file_write(request, path_ref, flags)
        if request.tool_name != "FileEdit":
            return _decision(
                request,
                "blocked",
                ("coding_mutation_tool_not_supported",),
                path_ref,
                flags,
            )
        if is_unsafe_workspace_path(request.path):
            return _decision(
                request,
                "blocked",
                ("unsafe_or_sealed_path_blocked",),
                path_ref,
                flags,
            )
        if self.read_ledger is None:
            return _decision(request, "blocked", ("read_ledger_required",), path_ref, flags)
        read_decision = self.read_ledger.require_fresh_full_read(
            WorkspaceMutationReadCheck(
                sessionId=request.session_id,
                workspaceRef=request.workspace_ref,
                path=request.path,
                currentDigest=request.current_digest,
                mutationKind="edit",
            ),
        )
        if read_decision.status != "ok":
            return _decision(
                request,
                "blocked",
                read_decision.reason_codes,
                path_ref,
                flags,
                read_ledger=read_decision,
            )
        if request.current_text is None:
            return _decision(
                request,
                "blocked",
                ("current_text_required",),
                path_ref,
                flags,
                read_ledger=read_decision,
            )
        if request.old_string is None or request.new_string is None:
            return _decision(
                request,
                "blocked",
                ("old_and_new_string_required",),
                path_ref,
                flags,
                read_ledger=read_decision,
            )
        if request.old_string == "":
            return _decision(
                request,
                "blocked",
                ("old_string_required",),
                path_ref,
                flags,
                read_ledger=read_decision,
            )
        if workspace_content_digest(request.current_text) != request.current_digest:
            return _decision(
                request,
                "blocked",
                ("current_text_digest_mismatch",),
                path_ref,
                flags,
                read_ledger=read_decision,
            )
        if request.old_string == request.new_string:
            return _decision(
                request,
                "blocked",
                ("no_op_edit",),
                path_ref,
                flags,
                read_ledger=read_decision,
            )

        occurrences = request.current_text.count(request.old_string)
        if occurrences == 0:
            return _decision(
                request,
                "blocked",
                ("no_match",),
                path_ref,
                flags,
                read_ledger=read_decision,
            )
        if occurrences > 1 and not request.replace_all:
            return _decision(
                request,
                "blocked",
                ("multiple_matches",),
                path_ref,
                flags,
                read_ledger=read_decision,
            )

        resulting_text = request.current_text.replace(
            request.old_string,
            request.new_string,
            -1 if request.replace_all else 1,
        )
        old_digest = workspace_content_digest(request.current_text)
        new_digest = workspace_content_digest(resulting_text)
        status: CodingMutationStatus = (
            "applied_local_fake"
            if self.config.local_fake_apply_enabled and request.explicit_approval
            else "approval_required"
        )
        reason_codes = (
            ("local_fake_mutation_receipt_only",)
            if status == "applied_local_fake"
            else ("coding_mutation_requires_explicit_approval",)
        )
        return _decision(
            request,
            status,
            reason_codes,
            path_ref,
            flags,
            read_ledger=read_decision,
            old_digest=old_digest,
            new_digest=new_digest,
            replacements=occurrences if request.replace_all else 1,
        )

    def _evaluate_patch_apply(
        self,
        request: CodingMutationRequest,
        path_ref: str,
        flags: CodingMutationAuthorityFlags,
    ) -> CodingMutationDecision:
        if is_unsafe_workspace_path(request.path):
            return _decision(
                request,
                "blocked",
                ("unsafe_or_sealed_path_blocked",),
                path_ref,
                flags,
            )
        if self.read_ledger is None:
            return _decision(request, "blocked", ("read_ledger_required",), path_ref, flags)
        read_decision = self.read_ledger.require_fresh_full_read(
            WorkspaceMutationReadCheck(
                sessionId=request.session_id,
                workspaceRef=request.workspace_ref,
                path=request.path,
                currentDigest=request.current_digest,
                mutationKind="patch",
            ),
        )
        if read_decision.status != "ok":
            return _decision(
                request,
                "blocked",
                read_decision.reason_codes,
                path_ref,
                flags,
                read_ledger=read_decision,
            )
        if request.patch is None:
            return _decision(
                request,
                "blocked",
                ("patch_content_required",),
                path_ref,
                flags,
                read_ledger=read_decision,
            )
        status: CodingMutationStatus = (
            "applied_local_fake"
            if self.config.local_fake_apply_enabled and request.explicit_approval
            else "approval_required"
        )
        reason_codes = (
            ("local_fake_mutation_receipt_only",)
            if status == "applied_local_fake"
            else ("coding_mutation_requires_explicit_approval",)
        )
        return _decision(
            request,
            status,
            reason_codes,
            path_ref,
            flags,
            read_ledger=read_decision,
        )

    def _evaluate_file_write(
        self,
        request: CodingMutationRequest,
        path_ref: str,
        flags: CodingMutationAuthorityFlags,
    ) -> CodingMutationDecision:
        if is_unsafe_workspace_path(request.path):
            return _decision(
                request,
                "blocked",
                ("unsafe_or_sealed_path_blocked",),
                path_ref,
                flags,
            )
        if request.new_string is None:
            return _decision(request, "blocked", ("new_content_required",), path_ref, flags)

        read_decision: WorkspaceMutationReadDecision | None = None
        if request.mutation_kind != "create":
            if self.read_ledger is None:
                return _decision(request, "blocked", ("read_ledger_required",), path_ref, flags)
            read_decision = self.read_ledger.require_fresh_full_read(
                WorkspaceMutationReadCheck(
                    sessionId=request.session_id,
                    workspaceRef=request.workspace_ref,
                    path=request.path,
                    currentDigest=request.current_digest,
                    mutationKind="replace",
                ),
            )
            if read_decision.status != "ok":
                return _decision(
                    request,
                    "blocked",
                    read_decision.reason_codes,
                    path_ref,
                    flags,
                    read_ledger=read_decision,
                )

        new_digest = workspace_content_digest(request.new_string)
        status: CodingMutationStatus = (
            "applied_local_fake"
            if self.config.local_fake_apply_enabled and request.explicit_approval
            else "approval_required"
        )
        reason_codes = (
            ("local_fake_mutation_receipt_only",)
            if status == "applied_local_fake"
            else ("coding_mutation_requires_explicit_approval",)
        )
        return _decision(
            request,
            status,
            reason_codes,
            path_ref,
            flags,
            read_ledger=read_decision,
            new_digest=new_digest,
            created_files=1 if request.mutation_kind == "create" else 0,
            replacements=0 if request.mutation_kind == "create" else 1,
        )


def _decision(
    request: CodingMutationRequest,
    status: CodingMutationStatus,
    reason_codes: tuple[str, ...],
    path_ref: str,
    flags: CodingMutationAuthorityFlags,
    *,
    read_ledger: WorkspaceMutationReadDecision | None = None,
    old_digest: str | None = None,
    new_digest: str | None = None,
    replacements: int = 0,
    created_files: int = 0,
) -> CodingMutationDecision:
    old_digest_ref = digest_ref(old_digest) if old_digest is not None else None
    new_digest_ref = digest_ref(new_digest) if new_digest is not None else None
    return CodingMutationDecision(
        status=status,
        toolName=request.tool_name,
        reasonCodes=reason_codes,
        pathRef=path_ref,
        receiptRef=_receipt_ref(request, status, reason_codes),
        readLedger=(
            read_ledger.public_projection()
            if type(read_ledger) is WorkspaceMutationReadDecision
            else None
        ),
        oldDigestRef=old_digest_ref,
        newDigestRef=new_digest_ref,
        diffSummary=(
            _diff_summary(
                created_files=created_files,
                replacements=replacements,
                old_digest_ref=old_digest_ref,
                new_digest_ref=new_digest_ref,
            )
            if replacements or created_files
            else {}
        ),
        authorityFlags=flags,
    )


def _diff_summary(
    *,
    created_files: int,
    replacements: int,
    old_digest_ref: str | None,
    new_digest_ref: str | None,
) -> dict[str, object]:
    summary: dict[str, object] = {
        "changedFiles": 1,
        "replacements": replacements,
    }
    if created_files:
        summary["createdFiles"] = created_files
    if old_digest_ref is not None:
        summary["oldDigestRef"] = old_digest_ref
    if new_digest_ref is not None:
        summary["newDigestRef"] = new_digest_ref
    return summary


def _receipt_ref(
    request: CodingMutationRequest,
    status: CodingMutationStatus,
    reason_codes: tuple[str, ...],
) -> str:
    seed = "|".join(
        (
            request.session_id,
            request.workspace_ref,
            request.path,
            request.tool_name,
            request.mutation_kind,
            status,
            ",".join(reason_codes),
            request.current_digest or "missing-digest",
            str(request.replace_all),
            _payload_digest(request.current_text),
            _payload_digest(request.old_string),
            _payload_digest(request.new_string),
            _payload_digest(request.patch),
        )
    )
    return "coding-mutation-receipt:" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]


def _payload_digest(value: str | None) -> str:
    if value is None:
        return "none"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _mutation_kind_matches_tool(request: CodingMutationRequest) -> bool:
    if request.tool_name == "FileEdit":
        return request.mutation_kind == "edit"
    if request.tool_name == "FileWrite":
        return request.mutation_kind in {"create", "replace"}
    return request.mutation_kind == "patch"


def _false_authority_overrides() -> dict[str, bool]:
    return {
        "filesystemWriteAttempted": False,
        "productionWorkspaceMutationEnabled": False,
        "liveToolAttached": False,
        "routeAttached": False,
        "userVisibleOutputAllowed": False,
    }


def _coerce_authority_flags(value: object) -> CodingMutationAuthorityFlags:
    if isinstance(value, CodingMutationAuthorityFlags):
        return value.model_copy(update=_false_authority_overrides())
    if isinstance(value, Mapping):
        data = dict(value)
        data.update(_false_authority_overrides())
        return CodingMutationAuthorityFlags.model_validate(data)
    return CodingMutationAuthorityFlags()


def _safe_diff_summary(summary: Mapping[str, object]) -> dict[str, object]:
    safe: dict[str, object] = {}
    for key in ("changedFiles", "createdFiles", "replacements", "oldDigestRef", "newDigestRef"):
        value = summary.get(key)
        if isinstance(value, bool | int | float) or value is None:
            safe[key] = value
        elif isinstance(value, str) and _is_public_ref(value):
            safe[key] = value
    return safe


def _safe_read_ledger_projection(projection: Mapping[str, object]) -> dict[str, object]:
    safe: dict[str, object] = {}
    for key in ("status", "reasonCodes", "pathRef", "entryRef", "digestRef", "authorityFlags"):
        value = projection.get(key)
        if key == "reasonCodes" and isinstance(value, list | tuple):
            safe[key] = [str(item)[:80] for item in value]
        elif key == "authorityFlags" and isinstance(value, Mapping):
            safe[key] = {
                "readLedgerEnabled": bool(value.get("readLedgerEnabled")),
                "localInMemoryOnly": bool(value.get("localInMemoryOnly")),
                "productionWritesEnabled": False,
                "workspaceMutationAuthority": False,
            }
        elif isinstance(value, str) and _is_public_ref(value):
            safe[key] = value
    return safe


def _is_public_ref(value: str) -> bool:
    lowered = value.lower()
    return (
        len(value) <= 180
        and not any(marker in lowered for marker in ("secret", "token", "/users/", "/workspace/"))
    )


def materialize_coding_mutation_recipe() -> CodingMutationMaterialization:
    return CodingMutationMaterialization(attachmentFlags=_false_authority_overrides())


__all__ = [
    "CodingMutationAuthorityFlags",
    "CodingMutationConfig",
    "CodingMutationDecision",
    "CodingMutationMaterialization",
    "CodingMutationRecipe",
    "CodingMutationRequest",
    "CodingMutationStatus",
    "CodingMutationToolName",
    "materialize_coding_mutation_recipe",
]
