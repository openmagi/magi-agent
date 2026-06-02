from __future__ import annotations

import json
import math
import re
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from openmagi_core_agent.hooks.scope import HookScope
from openmagi_core_agent.runtime.control import ControlRequest
from openmagi_core_agent.tools.manifest import PermissionClass, RuntimeMode, ToolManifest
from openmagi_core_agent.tools.result import ToolStatus
from openmagi_core_agent.transport.tool_preview import sanitize_tool_preview

from .toolhost_contract import ToolHostContractAttachmentFlags


PathShellPolicyCategory = Literal[
    "workspace_escape_path",
    "sealed_file_read",
    "sealed_file_write",
    "protected_memory_path",
    "destructive_shell",
    "curl_pipe_exec",
    "unsafe_git",
    "safe_command_readonly",
    "write_command_requires_approval",
    "network_command_requires_approval",
    "command_timeout_budget",
]
PathShellPolicySubjectType = Literal["path", "command"]
PathShellPolicyDecision = Literal["allow", "deny", "approval_required"]

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
    "ghp_shellpolicysecret",
    "sk-shellpolicy-secret",
    "SUPABASE_SERVICE_ROLE_KEY",
    "private tool args",
    "pythonResponseAuthority",
)
_FORBIDDEN_RAW_KEY_TOKENS = frozenset(
    {
        "adk_runner_invoked",
        "adk_runner_attached",
        "canary_attached",
        "canary_traffic",
        "live_dispatch",
        "live_tool",
        "memory_provider",
        "python_response_authority",
        "route_attached",
        "shell_executed",
        "shell_or_code_executed",
        "tool_dispatched_live",
        "traffic_attached",
    }
)
_REQUIRED_CATEGORIES = set(PathShellPolicyCategory.__args__)  # type: ignore[attr-defined]


class PathShellBudgetMetadata(BaseModel):
    model_config = _MODEL_CONFIG

    timeout_ms: int = Field(alias="timeoutMs", gt=0)
    output_chars: int | None = Field(default=None, alias="outputChars", gt=0)
    transcript_chars: int | None = Field(default=None, alias="transcriptChars", gt=0)


class PathShellPolicyCase(BaseModel):
    model_config = _MODEL_CONFIG

    case_id: str = Field(alias="caseId")
    category: PathShellPolicyCategory
    subject_type: PathShellPolicySubjectType = Field(alias="subjectType")
    mode: RuntimeMode
    tool: ToolManifest
    requested_path_preview: str | None = Field(default=None, alias="requestedPathPreview")
    requested_command_preview: str | None = Field(default=None, alias="requestedCommandPreview")
    normalized_workspace_relative: str | None = Field(
        default=None,
        alias="normalizedWorkspaceRelative",
    )
    permission_class: PermissionClass = Field(alias="permissionClass")
    decision: PathShellPolicyDecision
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
    control_request: ControlRequest | None = Field(default=None, alias="controlRequest")
    result_status: ToolStatus = Field(alias="resultStatus")
    evidence_refs: tuple[str, ...] = Field(default=(), alias="evidenceRefs")
    audit_refs: tuple[str, ...] = Field(default=(), alias="auditRefs")
    scope: HookScope = Field(default_factory=HookScope)
    attachment_flags: ToolHostContractAttachmentFlags = Field(alias="attachmentFlags")

    @model_validator(mode="before")
    @classmethod
    def _validate_raw_case(cls, value: object) -> object:
        if isinstance(value, Mapping):
            _reject_unsafe_raw_value(value, allow_redacted_paths=True)
            control_request = value.get("controlRequest")
            if isinstance(control_request, Mapping):
                arguments = control_request.get("arguments")
                if isinstance(arguments, Mapping):
                    _reject_unsafe_raw_value(arguments, allow_redacted_paths=True)
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
            raise ValueError("blocking path/shell decisions cannot be fail-open")
        if self.fail_closed and self.fail_open:
            raise ValueError("path/shell decision cannot be both fail-open and fail-closed")
        _validate_subject_fields(self)
        _validate_decision(self)
        _validate_reason_metadata(self)
        return self

    @field_serializer("scope")
    def _serialize_scope(self, value: HookScope) -> dict[str, object]:
        return value.model_dump(by_alias=True, mode="json", warnings=False)


class PathShellPolicyContractFixture(BaseModel):
    model_config = _MODEL_CONFIG

    schema_version: Literal["pathShellPolicyFixture.v1"] = Field(alias="schemaVersion")
    fixture_id: str = Field(alias="fixtureId")
    source_runtime: Literal["typescript-core-agent"] = Field(alias="sourceRuntime")
    recording_mode: Literal["local_diagnostic_fixture"] = Field(alias="recordingMode")
    redaction_status: Literal["verified"] = Field(alias="redactionStatus")
    local_diagnostic: Literal[True] = Field(alias="localDiagnostic")
    attachment_flags: ToolHostContractAttachmentFlags = Field(alias="attachmentFlags")
    cases: tuple[PathShellPolicyCase, ...]

    @model_validator(mode="before")
    @classmethod
    def _validate_raw_fixture(cls, value: object) -> object:
        if isinstance(value, Mapping):
            _reject_unsafe_raw_value(value, allow_redacted_paths=True)
        return value

    @model_validator(mode="after")
    def _validate_fixture(self) -> Self:
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("path/shell policy caseIds must be unique")
        categories = {case.category for case in self.cases}
        if not _REQUIRED_CATEGORIES.issubset(categories):
            raise ValueError("path/shell policy fixture is missing required categories")
        return self


class PathShellPolicyProjection(BaseModel):
    model_config = _MODEL_CONFIG

    fixture_id: str = Field(alias="fixtureId")
    local_diagnostic: Literal[True] = Field(default=True, alias="localDiagnostic")
    attachment_flags: ToolHostContractAttachmentFlags = Field(alias="attachmentFlags")
    no_live_execution: Literal[True] = Field(alias="noLiveExecution")
    case_order: tuple[str, ...] = Field(alias="caseOrder")
    by_decision: dict[str, int] = Field(alias="byDecision")
    by_category: dict[str, int] = Field(alias="byCategory")
    public_previews: dict[str, str] = Field(alias="publicPreviews")
    control_requests: dict[str, dict[str, object]] = Field(alias="controlRequests")
    case_snapshots: dict[str, dict[str, object]] = Field(alias="caseSnapshots")


def load_path_shell_policy_contract_fixture(
    path: str | Path,
    *,
    fixture_root: str | Path | None = None,
) -> PathShellPolicyContractFixture:
    resolved_path = _resolve_fixture_path(path, fixture_root=fixture_root)
    with resolved_path.open("r", encoding="utf-8") as fixture_file:
        payload: object = json.load(fixture_file)
    return PathShellPolicyContractFixture.model_validate(payload)


def project_path_shell_policy_contract_fixture(
    fixture: PathShellPolicyContractFixture | Mapping[str, Any],
) -> PathShellPolicyProjection:
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
    return PathShellPolicyProjection(
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
    fixture: PathShellPolicyContractFixture | Mapping[str, Any],
) -> PathShellPolicyContractFixture:
    if isinstance(fixture, PathShellPolicyContractFixture):
        return PathShellPolicyContractFixture.model_validate(
            fixture.model_dump(by_alias=True, mode="json", warnings=False)
        )
    return PathShellPolicyContractFixture.model_validate(fixture)


def _case_snapshot(case: PathShellPolicyCase, *, preview: str) -> dict[str, object]:
    snapshot: dict[str, object] = {
        "caseId": case.case_id,
        "category": case.category,
        "subjectType": case.subject_type,
        "mode": case.mode,
        "decision": case.decision,
        "permissionClass": case.permission_class,
        "normalizedWorkspaceRelative": case.normalized_workspace_relative,
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


def _public_preview(case: PathShellPolicyCase) -> str:
    raw_preview = case.requested_path_preview or case.requested_command_preview or ""
    redacted = _PRODUCTION_PATH_RE.sub("[redacted-path]", sanitize_tool_preview(raw_preview))
    return redacted


def _validate_subject_fields(case: PathShellPolicyCase) -> None:
    if case.subject_type == "path":
        if not case.requested_path_preview:
            raise ValueError("path policy case requires requestedPathPreview")
        if case.requested_command_preview is not None:
            raise ValueError("path policy case cannot include requestedCommandPreview")
        if case.normalized_workspace_relative is None:
            raise ValueError("path policy case requires normalizedWorkspaceRelative")
    else:
        if not case.requested_command_preview:
            raise ValueError("command policy case requires requestedCommandPreview")
        if case.requested_path_preview is not None:
            raise ValueError("command policy case cannot include requestedPathPreview")


def _validate_decision(case: PathShellPolicyCase) -> None:
    if case.decision == "allow":
        if case.result_status != "ok" or case.control_request is not None:
            raise ValueError("allowed path/shell case must be ok without ControlRequest")
        if case.blocking or case.fail_closed:
            raise ValueError("allowed path/shell case must be non-blocking fail-open metadata")
    elif case.decision == "deny":
        if case.result_status != "blocked" or case.control_request is not None:
            raise ValueError("denied path/shell case must block without ControlRequest")
        if not case.blocking or not case.fail_closed or case.fail_open:
            raise ValueError("denied path/shell case must block fail-closed")
    else:
        if case.result_status != "needs_approval" or case.control_request is None:
            raise ValueError("approval-required path/shell case must include ControlRequest")
        if not case.blocking or not case.fail_closed or case.fail_open:
            raise ValueError("approval-required path/shell case must block fail-closed")


def _validate_reason_metadata(case: PathShellPolicyCase) -> None:
    expected_reason = _expected_reason_for_category(case.category)
    if expected_reason not in case.reason_codes:
        raise ValueError("path/shell case reasonCodes must include category reason")
    if case.sealed_path and case.category not in {"sealed_file_read", "sealed_file_write"}:
        raise ValueError("sealedPath requires sealed file category")
    if case.protected_path and case.category != "protected_memory_path":
        raise ValueError("protectedPath requires protected_memory_path category")
    if case.security_critical and not case.hard_safety:
        raise ValueError("securityCritical path/shell policy requires hardSafety")
    if case.category in {
        "workspace_escape_path",
        "sealed_file_write",
        "protected_memory_path",
        "destructive_shell",
        "curl_pipe_exec",
        "unsafe_git",
    }:
        if case.decision != "deny":
            raise ValueError("hard-safety blocked category must deny")
        if not case.hard_safety or not case.security_critical:
            raise ValueError("blocked hard-safety category must be security-critical")


def _expected_reason_for_category(category: PathShellPolicyCategory) -> str:
    return {
        "workspace_escape_path": "path_escapes_workspace",
        "sealed_file_read": "sealed_file_read_observed",
        "sealed_file_write": "sealed_file_write_blocked",
        "protected_memory_path": "protected_memory_path",
        "destructive_shell": "destructive_shell",
        "curl_pipe_exec": "curl_pipe_exec",
        "unsafe_git": "unsafe_git",
        "safe_command_readonly": "safe_command_readonly",
        "write_command_requires_approval": "write_command_requires_approval",
        "network_command_requires_approval": "network_command_requires_approval",
        "command_timeout_budget": "command_timeout_budget",
    }[category]


def _reject_unsafe_public_snapshot(value: Mapping[str, object]) -> None:
    rendered = json.dumps(value, sort_keys=True)
    if _FORBIDDEN_PATH_RE.search(rendered):
        raise ValueError("path/shell public snapshot contains production paths")
    if any(token in rendered for token in _FORBIDDEN_PUBLIC_TOKENS):
        raise ValueError("path/shell public snapshot contains unsafe data")


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
        raise ValueError("path/shell policy fixture path must stay under fixture_root")
    return resolved_candidate


def _reject_unsafe_path_text(path_text: str) -> None:
    if _FORBIDDEN_PATH_RE.search(path_text):
        raise ValueError("path/shell policy fixtures must be local and non-production")


def _reject_unsafe_raw_value(
    value: object,
    *,
    allow_redacted_paths: bool,
    _path: tuple[str, ...] = (),
) -> None:
    _validate_json_like(value)
    if isinstance(value, str):
        if not allow_redacted_paths and _FORBIDDEN_PATH_RE.search(value):
            raise ValueError("path/shell policy fixture contains unsafe path")
        if _FORBIDDEN_PATH_RE.search(value):
            raise ValueError("path/shell policy fixture contains unsafe path")
        if any(token in value for token in _FORBIDDEN_PUBLIC_TOKENS):
            raise ValueError("path/shell policy fixture contains unsafe data")
        return
    if isinstance(value, Mapping):
        for key, nested_value in value.items():
            normalized = _normalize_key(key)
            if nested_value is True and normalized in _FORBIDDEN_RAW_KEY_TOKENS:
                raise ValueError("path/shell policy fixture cannot claim live behavior")
            next_path = (*_path, normalized)
            _reject_unsafe_raw_value(
                nested_value,
                allow_redacted_paths=allow_redacted_paths,
                _path=next_path,
            )
        return
    if isinstance(value, list | tuple):
        for item in value:
            _reject_unsafe_raw_value(
                item,
                allow_redacted_paths=allow_redacted_paths,
                _path=_path,
            )


def _validate_json_like(value: object) -> None:
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, int) and not isinstance(value, bool):
        return
    if isinstance(value, float):
        if math.isfinite(value):
            return
        raise ValueError("path/shell policy fixture values must be JSON-compatible")
    if isinstance(value, list | tuple):
        for item in value:
            _validate_json_like(item)
        return
    if isinstance(value, Mapping):
        for key, nested_value in value.items():
            if not isinstance(key, str):
                raise ValueError("path/shell policy mappings must use string keys")
            _validate_json_like(nested_value)
        return
    raise ValueError("path/shell policy fixture values must be JSON-compatible")


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
    "PathShellBudgetMetadata",
    "PathShellPolicyCase",
    "PathShellPolicyContractFixture",
    "PathShellPolicyProjection",
    "load_path_shell_policy_contract_fixture",
    "project_path_shell_policy_contract_fixture",
]
