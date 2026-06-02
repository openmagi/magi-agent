from __future__ import annotations

import json
import math
import re
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator
from pydantic import model_validator

from openmagi_core_agent.hooks.scope import HookScope
from openmagi_core_agent.runtime.control import ControlRequest
from openmagi_core_agent.tools.manifest import PermissionClass, RuntimeMode, ToolManifest
from openmagi_core_agent.tools.result import ToolStatus
from openmagi_core_agent.transport.tool_preview import sanitize_tool_preview

from .path_shell_policy_contract import PathShellBudgetMetadata


PatchFilePolicyCategory = Literal[
    "file_read_allowed",
    "file_read_workspace_escape",
    "file_read_protected_path",
    "file_write_requires_approval",
    "file_write_sealed_denied",
    "file_edit_stale_version",
    "file_edit_dry_run_preflight",
    "file_edit_workspace_escape",
    "patch_apply_dry_run_preflight",
    "patch_apply_sealed_denied",
    "patch_apply_path_traversal",
    "patch_apply_requires_approval",
]
PatchFilePolicyDecision = Literal[
    "allow",
    "deny",
    "approval_required",
    "dry_run_only",
    "preflight_failed",
]
PatchFilePathClassification = Literal[
    "workspace_safe",
    "outside_workspace",
    "sealed_file",
    "protected_memory",
    "absolute_production_path",
]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_PRODUCTION_PATH_RE = re.compile(
    r"(?:/data/bots|/workspace|/var/lib/kubelet)(?:/[^\s\"',}]+)*",
    re.IGNORECASE,
)
_FORBIDDEN_PATH_RE = re.compile(
    r"(?:^|[\\/])(?:data[\\/]bots|workspace|var[\\/]lib[\\/]kubelet)(?:[\\/]|$)|"
    r"pvc|supabase://|s3://|gs://|postgres(?:ql)?://|telegram|canary",
    re.IGNORECASE,
)
_FORBIDDEN_PUBLIC_TOKENS = (
    "Bearer unsafe",
    "ghp_patchsecret",
    "sk-patch-secret",
    "SUPABASE_SERVICE_ROLE_KEY",
    "private tool args",
    "pythonResponseAuthority",
)
_FORBIDDEN_RAW_KEY_TOKENS = frozenset(
    {
        "adk_runner_invoked",
        "adk_runner_attached",
        "canary_attached",
        "canary_traffic_attached",
        "evidence_block_enabled",
        "file_mutated",
        "live_dispatch",
        "live_tool",
        "live_tool_dispatched",
        "memory_provider",
        "memory_provider_called",
        "patch_applied",
        "production_authority",
        "production_storage_written",
        "python_response_authority",
        "route_attached",
        "route_or_api_attached",
        "shell_executed",
        "shell_or_code_executed",
        "tool_dispatched_live",
        "traffic_attached",
        "workspace_written",
    }
)
_REQUIRED_CATEGORIES = set(PatchFilePolicyCategory.__args__)  # type: ignore[attr-defined]
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")


class PatchFilePolicyAttachmentFlags(BaseModel):
    model_config = _MODEL_CONFIG

    adk_runner_invoked: Literal[False] = Field(default=False, alias="adkRunnerInvoked")
    live_tool_dispatched: Literal[False] = Field(default=False, alias="liveToolDispatched")
    shell_or_code_executed: Literal[False] = Field(
        default=False,
        alias="shellOrCodeExecuted",
    )
    file_mutated: Literal[False] = Field(default=False, alias="fileMutated")
    patch_applied: Literal[False] = Field(default=False, alias="patchApplied")
    workspace_written: Literal[False] = Field(default=False, alias="workspaceWritten")
    memory_provider_called: Literal[False] = Field(
        default=False,
        alias="memoryProviderCalled",
    )
    agent_memory_imported: Literal[False] = Field(default=False, alias="agentMemoryImported")
    hipocampus_qmd_live_called: Literal[False] = Field(
        default=False,
        alias="hipocampusQmdLiveCalled",
    )
    production_storage_written: Literal[False] = Field(
        default=False,
        alias="productionStorageWritten",
    )
    production_authority: Literal[False] = Field(default=False, alias="productionAuthority")
    route_or_api_attached: Literal[False] = Field(default=False, alias="routeOrApiAttached")
    telegram_attached: Literal[False] = Field(default=False, alias="telegramAttached")
    canary_traffic_attached: Literal[False] = Field(
        default=False,
        alias="canaryTrafficAttached",
    )
    evidence_block_enabled: Literal[False] = Field(default=False, alias="evidenceBlockEnabled")

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        return cls(**{name: False for name in cls.model_fields})

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        data = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            data.update(dict(update))
        return type(self).model_validate(data)

    @field_serializer(
        "adk_runner_invoked",
        "live_tool_dispatched",
        "shell_or_code_executed",
        "file_mutated",
        "patch_applied",
        "workspace_written",
        "memory_provider_called",
        "agent_memory_imported",
        "hipocampus_qmd_live_called",
        "production_storage_written",
        "production_authority",
        "route_or_api_attached",
        "telegram_attached",
        "canary_traffic_attached",
        "evidence_block_enabled",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class PatchFilePreflightMetadata(BaseModel):
    model_config = _MODEL_CONFIG

    dry_run: bool = Field(alias="dryRun")
    preflight_passed: bool = Field(alias="preflightPassed")
    changed_files: tuple[str, ...] = Field(default=(), alias="changedFiles")
    created_files: tuple[str, ...] = Field(default=(), alias="createdFiles")
    deleted_files: tuple[str, ...] = Field(default=(), alias="deletedFiles")
    hunks: int = Field(default=0, ge=0)
    expected_sha256: str | None = Field(default=None, alias="expectedSha256")
    current_sha256: str | None = Field(default=None, alias="currentSha256")
    version_mismatch: bool = Field(default=False, alias="versionMismatch")
    error_code: str | None = Field(default=None, alias="errorCode")

    @field_validator("expected_sha256", "current_sha256")
    @classmethod
    def _validate_sha256(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("SHA-256 metadata must be lowercase 64-character hex")
        return value

    @model_validator(mode="after")
    def _validate_preflight(self) -> Self:
        for rel_path in (*self.changed_files, *self.created_files, *self.deleted_files):
            _reject_unsafe_relative_path(rel_path)
        if self.preflight_passed and self.error_code is not None:
            raise ValueError("successful preflight cannot include errorCode")
        if not self.preflight_passed and self.error_code is None:
            raise ValueError("failed preflight requires errorCode")
        if self.version_mismatch and not (self.expected_sha256 and self.current_sha256):
            raise ValueError("versionMismatch requires expected and current SHA-256 metadata")
        return self


class PatchFilePolicyCase(BaseModel):
    model_config = _MODEL_CONFIG

    case_id: str = Field(alias="caseId")
    category: PatchFilePolicyCategory
    mode: RuntimeMode
    tool: ToolManifest
    requested_path_preview: str = Field(alias="requestedPathPreview")
    normalized_workspace_relative: str = Field(alias="normalizedWorkspaceRelative")
    path_classification: PatchFilePathClassification = Field(alias="pathClassification")
    permission_class: PermissionClass = Field(alias="permissionClass")
    decision: PatchFilePolicyDecision
    hard_safety: bool = Field(alias="hardSafety")
    security_critical: bool = Field(alias="securityCritical")
    blocking: bool
    fail_open: bool = Field(alias="failOpen")
    fail_closed: bool = Field(alias="failClosed")
    mutates_workspace: bool = Field(alias="mutatesWorkspace")
    dangerous: bool
    is_concurrency_safe: bool = Field(alias="isConcurrencySafe")
    sealed_path: bool = Field(default=False, alias="sealedPath")
    protected_path: bool = Field(default=False, alias="protectedPath")
    reason_codes: tuple[str, ...] = Field(alias="reasonCodes")
    timeout_budget_ms: int = Field(alias="timeoutBudgetMs", gt=0)
    budget_metadata: PathShellBudgetMetadata = Field(alias="budgetMetadata")
    preflight: PatchFilePreflightMetadata | None = None
    control_request: ControlRequest | None = Field(default=None, alias="controlRequest")
    result_status: ToolStatus = Field(alias="resultStatus")
    evidence_refs: tuple[str, ...] = Field(default=(), alias="evidenceRefs")
    audit_refs: tuple[str, ...] = Field(default=(), alias="auditRefs")
    scope: HookScope = Field(default_factory=HookScope)
    attachment_flags: PatchFilePolicyAttachmentFlags = Field(alias="attachmentFlags")

    @model_validator(mode="before")
    @classmethod
    def _validate_raw_case(cls, value: object) -> object:
        if isinstance(value, Mapping):
            _reject_unsafe_raw_value(value)
        return value

    @model_validator(mode="after")
    def _validate_case(self) -> Self:
        if self.timeout_budget_ms != self.tool.timeout_ms:
            raise ValueError("timeoutBudgetMs must match tool timeoutMs")
        if self.budget_metadata.timeout_ms != self.timeout_budget_ms:
            raise ValueError("budgetMetadata.timeoutMs must match timeoutBudgetMs")
        if self.mutates_workspace != self.tool.mutates_workspace:
            raise ValueError("mutatesWorkspace must match tool manifest")
        if self.dangerous != self.tool.dangerous:
            raise ValueError("dangerous must match tool manifest")
        if self.is_concurrency_safe != self.tool.is_concurrency_safe:
            raise ValueError("isConcurrencySafe must match tool manifest")
        if self.blocking and self.fail_open:
            raise ValueError("blocking patch/file decisions cannot be fail-open")
        if self.fail_closed and self.fail_open:
            raise ValueError("patch/file decision cannot be both fail-open and fail-closed")
        _validate_decision(self)
        _validate_reason_metadata(self)
        return self

    @field_serializer("scope")
    def _serialize_scope(self, value: HookScope) -> dict[str, object]:
        return value.model_dump(by_alias=True, mode="json", warnings=False)


class PatchFilePolicyContractFixture(BaseModel):
    model_config = _MODEL_CONFIG

    schema_version: Literal["patchFilePolicyFixture.v1"] = Field(alias="schemaVersion")
    fixture_id: str = Field(alias="fixtureId")
    source_runtime: Literal["typescript-core-agent"] = Field(alias="sourceRuntime")
    recording_mode: Literal["local_diagnostic_fixture"] = Field(alias="recordingMode")
    redaction_status: Literal["verified"] = Field(alias="redactionStatus")
    local_diagnostic: Literal[True] = Field(alias="localDiagnostic")
    attachment_flags: PatchFilePolicyAttachmentFlags = Field(alias="attachmentFlags")
    cases: tuple[PatchFilePolicyCase, ...]

    @model_validator(mode="before")
    @classmethod
    def _validate_raw_fixture(cls, value: object) -> object:
        if isinstance(value, Mapping):
            _reject_unsafe_raw_value(value)
        return value

    @model_validator(mode="after")
    def _validate_fixture(self) -> Self:
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("patch/file policy caseIds must be unique")
        categories = {case.category for case in self.cases}
        if not _REQUIRED_CATEGORIES.issubset(categories):
            raise ValueError("patch/file policy fixture is missing required categories")
        return self


class PatchFilePolicyProjection(BaseModel):
    model_config = _MODEL_CONFIG

    fixture_id: str = Field(alias="fixtureId")
    local_diagnostic: Literal[True] = Field(default=True, alias="localDiagnostic")
    attachment_flags: PatchFilePolicyAttachmentFlags = Field(alias="attachmentFlags")
    no_live_execution: Literal[True] = Field(alias="noLiveExecution")
    case_order: tuple[str, ...] = Field(alias="caseOrder")
    by_decision: dict[str, int] = Field(alias="byDecision")
    by_category: dict[str, int] = Field(alias="byCategory")
    public_previews: dict[str, str] = Field(alias="publicPreviews")
    control_requests: dict[str, dict[str, object]] = Field(alias="controlRequests")
    case_snapshots: dict[str, dict[str, object]] = Field(alias="caseSnapshots")


def load_patch_file_policy_contract_fixture(
    path: str | Path,
    *,
    fixture_root: str | Path | None = None,
) -> PatchFilePolicyContractFixture:
    resolved_path = _resolve_fixture_path(path, fixture_root=fixture_root)
    with resolved_path.open("r", encoding="utf-8") as fixture_file:
        payload: object = json.load(fixture_file)
    return PatchFilePolicyContractFixture.model_validate(payload)


def project_patch_file_policy_contract_fixture(
    fixture: PatchFilePolicyContractFixture | Mapping[str, Any],
) -> PatchFilePolicyProjection:
    safe_fixture = _validated_fixture_snapshot(fixture)
    public_previews: dict[str, str] = {}
    control_requests: dict[str, dict[str, object]] = {}
    case_snapshots: dict[str, dict[str, object]] = {}
    for case in safe_fixture.cases:
        preview = _public_preview(case)
        public_previews[case.case_id] = preview
        if case.control_request is not None:
            control_requests[case.case_id] = _public_control_request(case.control_request)
        snapshot = _case_snapshot(case, preview=preview)
        _reject_unsafe_public_snapshot(snapshot)
        case_snapshots[case.case_id] = snapshot
    return PatchFilePolicyProjection(
        fixtureId=safe_fixture.fixture_id,
        attachmentFlags=safe_fixture.attachment_flags,
        noLiveExecution=True,
        caseOrder=tuple(case.case_id for case in safe_fixture.cases),
        byDecision=dict(Counter(case.decision for case in safe_fixture.cases)),
        byCategory=dict(Counter(case.category for case in safe_fixture.cases)),
        publicPreviews=public_previews,
        controlRequests=control_requests,
        caseSnapshots=case_snapshots,
    )


def _validated_fixture_snapshot(
    fixture: PatchFilePolicyContractFixture | Mapping[str, Any],
) -> PatchFilePolicyContractFixture:
    if isinstance(fixture, PatchFilePolicyContractFixture):
        return PatchFilePolicyContractFixture.model_validate(
            fixture.model_dump(by_alias=True, mode="json", warnings=False)
        )
    return PatchFilePolicyContractFixture.model_validate(fixture)


def _case_snapshot(case: PatchFilePolicyCase, *, preview: str) -> dict[str, object]:
    snapshot: dict[str, object] = {
        "caseId": case.case_id,
        "category": case.category,
        "mode": case.mode,
        "decision": case.decision,
        "permissionClass": case.permission_class,
        "normalizedWorkspaceRelative": case.normalized_workspace_relative,
        "pathClassification": case.path_classification,
        "tool": {
            "name": case.tool.name,
            "kind": case.tool.kind,
            "sourceKind": case.tool.source.kind,
            "permissionClass": case.tool.permission,
            "sideEffectClass": case.tool.side_effect_class,
            "dangerous": case.tool.dangerous,
            "mutatesWorkspace": case.tool.mutates_workspace,
            "isConcurrencySafe": case.tool.is_concurrency_safe,
            "parallelSafety": case.tool.parallel_safety,
            "timeoutMs": case.tool.timeout_ms,
        },
        "hardSafety": case.hard_safety,
        "securityCritical": case.security_critical,
        "blocking": case.blocking,
        "failOpen": case.fail_open,
        "failClosed": case.fail_closed,
        "mutatesWorkspace": case.mutates_workspace,
        "dangerous": case.dangerous,
        "isConcurrencySafe": case.is_concurrency_safe,
        "sealedPath": case.sealed_path,
        "protectedPath": case.protected_path,
        "reasonCodes": case.reason_codes,
        "timeoutBudgetMs": case.timeout_budget_ms,
        "budgetMetadata": case.budget_metadata.model_dump(by_alias=True, mode="json"),
        "outputPreview": preview,
        "resultStatus": case.result_status,
        "evidenceRefs": case.evidence_refs,
        "auditRefs": case.audit_refs,
        "scope": case.scope.model_dump(by_alias=True, mode="json", warnings=False),
    }
    if case.preflight is not None:
        snapshot["preflight"] = case.preflight.model_dump(by_alias=True, mode="json")
    if case.control_request is not None:
        snapshot["controlRequest"] = _public_control_request(case.control_request)
    return snapshot


def _public_control_request(request: ControlRequest) -> dict[str, object]:
    return {
        "requestId": request.request_id,
        "turnId": request.turn_id,
        "toolName": request.tool_name,
        "reason": request.reason,
    }


def _public_preview(case: PatchFilePolicyCase) -> str:
    redacted = _PRODUCTION_PATH_RE.sub(
        "[redacted-path]",
        sanitize_tool_preview(case.requested_path_preview),
    )
    return redacted


def _validate_decision(case: PatchFilePolicyCase) -> None:
    if case.decision == "allow":
        if case.result_status != "ok" or case.control_request is not None:
            raise ValueError("allowed patch/file case must be ok without ControlRequest")
        if case.blocking or case.fail_closed:
            raise ValueError("allowed patch/file case must be non-blocking fail-open metadata")
    elif case.decision == "deny":
        if case.result_status != "blocked" or case.control_request is not None:
            raise ValueError("denied patch/file case must block without ControlRequest")
        if not case.blocking or not case.fail_closed or case.fail_open:
            raise ValueError("denied patch/file case must block fail-closed")
    elif case.decision == "approval_required":
        if case.result_status != "needs_approval" or case.control_request is None:
            raise ValueError("approval-required patch/file case must include ControlRequest")
        if not case.blocking or not case.fail_closed or case.fail_open:
            raise ValueError("approval-required patch/file case must block fail-closed")
    elif case.decision == "dry_run_only":
        if case.result_status != "ok" or case.preflight is None:
            raise ValueError("dry-run patch/file case must include successful preflight")
        if not case.preflight.dry_run or not case.preflight.preflight_passed:
            raise ValueError("dry-run patch/file case must remain dry-run only")
        if case.control_request is not None or case.blocking or case.fail_closed:
            raise ValueError("dry-run patch/file case cannot request approval or block")
    elif case.decision == "preflight_failed":
        if case.result_status != "error" or case.preflight is None:
            raise ValueError("preflight-failed patch/file case must include failed preflight")
        if case.preflight.preflight_passed:
            raise ValueError("preflight-failed patch/file case cannot pass preflight")
        if case.control_request is not None:
            raise ValueError("preflight-failed patch/file case cannot request approval")
        if not case.blocking or not case.fail_closed or case.fail_open:
            raise ValueError("preflight-failed patch/file case must block fail-closed")


def _validate_reason_metadata(case: PatchFilePolicyCase) -> None:
    expected_reason = _expected_reason_for_category(case.category)
    if expected_reason not in case.reason_codes:
        raise ValueError("patch/file case reasonCodes must include category reason")
    if case.sealed_path and case.path_classification != "sealed_file":
        raise ValueError("sealedPath requires sealed_file pathClassification")
    if case.protected_path and case.path_classification != "protected_memory":
        raise ValueError("protectedPath requires protected_memory pathClassification")
    if case.security_critical and not case.hard_safety:
        raise ValueError("securityCritical patch/file policy requires hardSafety")
    if case.category in {
        "file_read_workspace_escape",
        "file_read_protected_path",
        "file_write_sealed_denied",
        "file_edit_workspace_escape",
        "patch_apply_sealed_denied",
    }:
        if case.decision != "deny":
            raise ValueError("blocked hard-safety patch/file category must deny")
        if not case.hard_safety or not case.security_critical:
            raise ValueError("blocked hard-safety patch/file category must be critical")
    if case.category in {"file_edit_stale_version", "patch_apply_path_traversal"}:
        if case.decision != "preflight_failed":
            raise ValueError("preflight-failure patch/file category must fail preflight")
        if not case.hard_safety or not case.security_critical:
            raise ValueError("preflight-failure patch/file category must be critical")


def _expected_reason_for_category(category: PatchFilePolicyCategory) -> str:
    return {
        "file_read_allowed": "workspace_safe",
        "file_read_workspace_escape": "path_escapes_workspace",
        "file_read_protected_path": "protected_memory_path",
        "file_write_requires_approval": "workspace_mutation_requires_approval",
        "file_write_sealed_denied": "sealed_file_write_blocked",
        "file_edit_stale_version": "stale_file_version_mismatch",
        "file_edit_dry_run_preflight": "file_edit_preflight_ok",
        "file_edit_workspace_escape": "path_escapes_workspace",
        "patch_apply_dry_run_preflight": "patch_dry_run_preflight_ok",
        "patch_apply_sealed_denied": "sealed_file_write_blocked",
        "patch_apply_path_traversal": "patch_path_traversal",
        "patch_apply_requires_approval": "patch_workspace_mutation_requires_approval",
    }[category]


def _reject_unsafe_public_snapshot(value: Mapping[str, object]) -> None:
    rendered = json.dumps(value, sort_keys=True)
    if _FORBIDDEN_PATH_RE.search(rendered):
        raise ValueError("patch/file public snapshot contains production paths")
    if any(token in rendered for token in _FORBIDDEN_PUBLIC_TOKENS):
        raise ValueError("patch/file public snapshot contains unsafe data")


def _resolve_fixture_path(path: str | Path, *, fixture_root: str | Path | None) -> Path:
    _reject_unsafe_path_text(str(path))
    candidate = Path(path)
    if fixture_root is None:
        resolved = candidate.resolve(strict=True)
        _reject_unsafe_path_text(str(resolved))
        return resolved
    _reject_unsafe_path_text(str(fixture_root))
    resolved_root = Path(fixture_root).resolve(strict=True)
    _reject_unsafe_path_text(str(resolved_root))
    if not candidate.is_absolute():
        candidate = resolved_root / candidate
    resolved_candidate = candidate.resolve(strict=True)
    _reject_unsafe_path_text(str(resolved_candidate))
    if not resolved_candidate.is_relative_to(resolved_root):
        raise ValueError("patch/file policy fixture path must stay under fixture_root")
    return resolved_candidate


def _reject_unsafe_path_text(path_text: str) -> None:
    if _FORBIDDEN_PATH_RE.search(path_text):
        raise ValueError("patch/file policy fixtures must be local and non-production")


def _reject_unsafe_relative_path(path_text: str) -> None:
    if not path_text or path_text.startswith("/") or ".." in Path(path_text).parts:
        raise ValueError("patch/file changed paths must be workspace-relative")
    _reject_unsafe_path_text(path_text)


def _reject_unsafe_raw_value(
    value: object,
    *,
    _path: tuple[str, ...] = (),
) -> None:
    _validate_json_like(value)
    if isinstance(value, str):
        if _FORBIDDEN_PATH_RE.search(value):
            raise ValueError("patch/file policy fixture contains unsafe path")
        if any(token in value for token in _FORBIDDEN_PUBLIC_TOKENS):
            raise ValueError("patch/file policy fixture contains unsafe data")
        return
    if isinstance(value, Mapping):
        for key, nested_value in value.items():
            normalized = _normalize_key(key)
            if nested_value is True and normalized in _FORBIDDEN_RAW_KEY_TOKENS:
                raise ValueError("patch/file policy fixture cannot claim live behavior")
            next_path = (*_path, normalized)
            _reject_unsafe_raw_value(nested_value, _path=next_path)
        return
    if isinstance(value, list | tuple):
        for item in value:
            _reject_unsafe_raw_value(item, _path=_path)


def _validate_json_like(value: object) -> None:
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, int) and not isinstance(value, bool):
        return
    if isinstance(value, float):
        if math.isfinite(value):
            return
        raise ValueError("patch/file policy fixture values must be JSON-compatible")
    if isinstance(value, list | tuple):
        for item in value:
            _validate_json_like(item)
        return
    if isinstance(value, Mapping):
        for key, nested_value in value.items():
            if not isinstance(key, str):
                raise ValueError("patch/file policy mappings must use string keys")
            _validate_json_like(nested_value)
        return
    raise ValueError("patch/file policy fixture values must be JSON-compatible")


def _normalize_key(value: object) -> str:
    if not isinstance(value, str):
        return ""
    chars: list[str] = []
    previous_was_separator = False
    for char in value:
        if char.isalnum():
            chars.append(char.lower())
            previous_was_separator = False
        elif not previous_was_separator:
            chars.append("_")
            previous_was_separator = True
    return "".join(chars).strip("_")


__all__ = [
    "PatchFilePolicyAttachmentFlags",
    "PatchFilePolicyCase",
    "PatchFilePolicyContractFixture",
    "PatchFilePolicyProjection",
    "PatchFilePreflightMetadata",
    "load_patch_file_policy_contract_fixture",
    "project_patch_file_policy_contract_fixture",
]
