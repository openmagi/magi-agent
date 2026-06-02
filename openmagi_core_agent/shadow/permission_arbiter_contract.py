from __future__ import annotations

import json
import math
import re
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator


PermissionArbiterMode = Literal["plan", "auto", "default", "bypass", "workspace_bypass"]
PermissionArbiterSource = Literal["builtin", "mcp", "shell", "workspace", "child_agent"]
PermissionArbiterDecision = Literal["allow", "ask", "deny"]
PermissionArbiterPermissionClass = Literal["read", "write", "execute", "delegate"]
PermissionArbiterSecurityPrecheck = Literal["passed", "failed", "not_applicable"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_FORBIDDEN_PATH_RE = re.compile(
    r"(?:^|[\\/])(?:data[\\/]bots|workspace|var[\\/]lib[\\/]kubelet)(?:[\\/]|$)|"
    r"pvc|supabase://|s3://|gs://|postgres(?:ql)?://|telegram|canary",
    re.IGNORECASE,
)
_FORBIDDEN_PUBLIC_TOKENS = (
    "Bearer unsafe",
    "ghp_permissionsecret",
    "sk-permission-secret",
    "SUPABASE_SERVICE_ROLE_KEY",
    "private tool args",
    "rm -rf /",
    "pythonResponseAuthority",
)
_FORBIDDEN_RAW_KEY_TOKENS = frozenset(
    {
        "adk_runner_invoked",
        "adk_runner_attached",
        "canary_attached",
        "canary_traffic",
        "evidence_block_enabled",
        "live_dispatch",
        "live_tool",
        "live_tool_dispatched",
        "memory_provider",
        "memory_provider_called",
        "python_response_authority",
        "route_attached",
        "route_or_api_attached",
        "shell_executed",
        "shell_or_code_executed",
        "tool_dispatched_live",
        "traffic_attached",
    }
)


class PermissionArbiterAttachmentFlags(BaseModel):
    model_config = _MODEL_CONFIG

    adk_runner_invoked: Literal[False] = Field(default=False, alias="adkRunnerInvoked")
    live_tool_dispatched: Literal[False] = Field(default=False, alias="liveToolDispatched")
    shell_or_code_executed: Literal[False] = Field(
        default=False,
        alias="shellOrCodeExecuted",
    )
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
    route_or_api_attached: Literal[False] = Field(default=False, alias="routeOrApiAttached")
    telegram_attached: Literal[False] = Field(default=False, alias="telegramAttached")
    canary_traffic_attached: Literal[False] = Field(
        default=False,
        alias="canaryTrafficAttached",
    )
    evidence_block_enabled: Literal[False] = Field(default=False, alias="evidenceBlockEnabled")

    @field_serializer(
        "adk_runner_invoked",
        "live_tool_dispatched",
        "shell_or_code_executed",
        "memory_provider_called",
        "agent_memory_imported",
        "hipocampus_qmd_live_called",
        "production_storage_written",
        "route_or_api_attached",
        "telegram_attached",
        "canary_traffic_attached",
        "evidence_block_enabled",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class PermissionArbiterApprovalMetadata(BaseModel):
    model_config = _MODEL_CONFIG

    request_id: str = Field(alias="requestId")
    turn_id: str = Field(alias="turnId")
    tool_name: str = Field(alias="toolName")
    reason: str

    @model_validator(mode="before")
    @classmethod
    def _validate_raw_metadata(cls, value: object) -> object:
        _reject_unsafe_raw_value(value)
        return value


class PermissionArbiterStatusMetadata(BaseModel):
    model_config = _MODEL_CONFIG

    status: Literal["blocked"]
    error_code: Literal["bypass_denied_hard_safety"] = Field(alias="errorCode")
    observable: Literal[True]
    metadata_only: Literal[True] = Field(alias="metadataOnly")


class PermissionArbiterCase(BaseModel):
    model_config = _MODEL_CONFIG

    case_id: str = Field(alias="caseId")
    mode: PermissionArbiterMode
    source: PermissionArbiterSource
    tool_name: str = Field(alias="toolName")
    permission_class: PermissionArbiterPermissionClass = Field(alias="permissionClass")
    decision: PermissionArbiterDecision
    reason_codes: tuple[str, ...] = Field(alias="reasonCodes")
    public_preview: str = Field(alias="publicPreview")
    dry_run: bool = Field(default=False, alias="dryRun")
    bypass_requested: bool = Field(default=False, alias="bypassRequested")
    security_precheck: PermissionArbiterSecurityPrecheck = Field(alias="securityPrecheck")
    path_policy_recorded: bool = Field(default=False, alias="pathPolicyRecorded")
    mutates_workspace: bool = Field(alias="mutatesWorkspace")
    dangerous: bool
    approval_metadata: PermissionArbiterApprovalMetadata | None = Field(
        default=None,
        alias="approvalMetadata",
    )
    status_metadata: PermissionArbiterStatusMetadata | None = Field(
        default=None,
        alias="statusMetadata",
    )
    control_request: Literal[None] = Field(default=None, alias="controlRequest")
    attachment_flags: PermissionArbiterAttachmentFlags = Field(alias="attachmentFlags")

    @model_validator(mode="before")
    @classmethod
    def _validate_raw_case(cls, value: object) -> object:
        _reject_unsafe_raw_value(value)
        return value

    @model_validator(mode="after")
    def _validate_case(self) -> Self:
        if self.security_precheck == "failed" and self.decision == "allow":
            raise ValueError("failed security precheck cannot allow")

        if self.decision == "ask":
            if self.approval_metadata is None:
                raise ValueError("ask permission-arbiter case requires approvalMetadata")
            if self.status_metadata is not None:
                raise ValueError("ask permission-arbiter case cannot include statusMetadata")
        elif self.approval_metadata is not None:
            raise ValueError("non-ask permission-arbiter case cannot include approvalMetadata")

        if self.decision == "deny":
            if self.bypass_requested and self.security_precheck == "failed":
                if self.status_metadata is None:
                    raise ValueError("bypass hard-safety denial requires statusMetadata")
            elif self.status_metadata is not None:
                raise ValueError("statusMetadata is only for bypass denial metadata")
        elif self.status_metadata is not None:
            raise ValueError("allowed or ask cases cannot include statusMetadata")

        if self.mode == "plan" and self.mutates_workspace and not self.dry_run:
            if self.decision not in {"ask", "deny"}:
                raise ValueError("plan mutating apply cases must ask or deny")
        if self.mode == "bypass" and self.bypass_requested is not True:
            raise ValueError("bypass mode requires bypassRequested")
        if self.mode == "workspace_bypass" and self.bypass_requested is not True:
            raise ValueError("workspace_bypass mode requires bypassRequested")
        _reject_unsafe_public_text(self.public_preview)
        return self


class PermissionArbiterContractFixture(BaseModel):
    model_config = _MODEL_CONFIG

    schema_version: Literal["permissionArbiterFixture.v1"] = Field(alias="schemaVersion")
    fixture_id: str = Field(alias="fixtureId")
    source_runtime: Literal["typescript-core-agent"] = Field(alias="sourceRuntime")
    recording_mode: Literal["local_diagnostic_fixture"] = Field(alias="recordingMode")
    redaction_status: Literal["verified"] = Field(alias="redactionStatus")
    local_diagnostic: Literal[True] = Field(alias="localDiagnostic")
    attachment_flags: PermissionArbiterAttachmentFlags = Field(alias="attachmentFlags")
    cases: tuple[PermissionArbiterCase, ...]

    @model_validator(mode="before")
    @classmethod
    def _validate_raw_fixture(cls, value: object) -> object:
        _reject_unsafe_raw_value(value)
        return value

    @model_validator(mode="after")
    def _validate_fixture(self) -> Self:
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("permission-arbiter caseIds must be unique")
        if len(self.cases) != 13:
            raise ValueError("permission-arbiter fixture must contain representative matrix")
        return self


class PermissionArbiterProjection(BaseModel):
    model_config = _MODEL_CONFIG

    fixture_id: str = Field(alias="fixtureId")
    local_diagnostic: Literal[True] = Field(default=True, alias="localDiagnostic")
    attachment_flags: PermissionArbiterAttachmentFlags = Field(alias="attachmentFlags")
    no_live_execution: Literal[True] = Field(alias="noLiveExecution")
    case_order: tuple[str, ...] = Field(alias="caseOrder")
    by_decision: dict[str, int] = Field(alias="byDecision")
    public_previews: dict[str, str] = Field(alias="publicPreviews")
    approval_metadata: dict[str, dict[str, object]] = Field(alias="approvalMetadata")
    bypass_status_metadata: dict[str, dict[str, object]] = Field(
        alias="bypassStatusMetadata",
    )
    control_requests: dict[str, dict[str, object]] = Field(alias="controlRequests")
    case_snapshots: dict[str, dict[str, object]] = Field(alias="caseSnapshots")


def load_permission_arbiter_contract_fixture(
    path: str | Path,
    *,
    fixture_root: str | Path | None = None,
) -> PermissionArbiterContractFixture:
    resolved_path = _resolve_fixture_path(path, fixture_root=fixture_root)
    with resolved_path.open("r", encoding="utf-8") as fixture_file:
        payload: object = json.load(fixture_file)
    return PermissionArbiterContractFixture.model_validate(payload)


def project_permission_arbiter_contract_fixture(
    fixture: PermissionArbiterContractFixture | Mapping[str, Any],
) -> PermissionArbiterProjection:
    safe_fixture = _validated_fixture_snapshot(fixture)
    public_previews: dict[str, str] = {}
    approval_metadata: dict[str, dict[str, object]] = {}
    bypass_status_metadata: dict[str, dict[str, object]] = {}
    case_snapshots: dict[str, dict[str, object]] = {}
    for case in safe_fixture.cases:
        public_previews[case.case_id] = case.public_preview
        if case.approval_metadata is not None:
            approval_metadata[case.case_id] = case.approval_metadata.model_dump(
                by_alias=True,
                mode="json",
            )
        if case.status_metadata is not None:
            bypass_status_metadata[case.case_id] = case.status_metadata.model_dump(
                by_alias=True,
                mode="json",
            )
        snapshot = _case_snapshot(case)
        _reject_unsafe_public_snapshot(snapshot)
        case_snapshots[case.case_id] = snapshot
    return PermissionArbiterProjection(
        fixtureId=safe_fixture.fixture_id,
        attachmentFlags=safe_fixture.attachment_flags,
        noLiveExecution=True,
        caseOrder=tuple(case.case_id for case in safe_fixture.cases),
        byDecision=dict(Counter(case.decision for case in safe_fixture.cases)),
        publicPreviews=public_previews,
        approvalMetadata=approval_metadata,
        bypassStatusMetadata=bypass_status_metadata,
        controlRequests={},
        caseSnapshots=case_snapshots,
    )


def _validated_fixture_snapshot(
    fixture: PermissionArbiterContractFixture | Mapping[str, Any],
) -> PermissionArbiterContractFixture:
    if isinstance(fixture, PermissionArbiterContractFixture):
        return PermissionArbiterContractFixture.model_validate(
            fixture.model_dump(by_alias=True, mode="json", warnings=False)
        )
    return PermissionArbiterContractFixture.model_validate(fixture)


def _case_snapshot(case: PermissionArbiterCase) -> dict[str, object]:
    snapshot: dict[str, object] = {
        "caseId": case.case_id,
        "mode": case.mode,
        "source": case.source,
        "toolName": case.tool_name,
        "permissionClass": case.permission_class,
        "decision": case.decision,
        "reasonCodes": case.reason_codes,
        "publicPreview": case.public_preview,
        "dryRun": case.dry_run,
        "bypassRequested": case.bypass_requested,
        "securityPrecheck": case.security_precheck,
        "pathPolicyRecorded": case.path_policy_recorded,
        "mutatesWorkspace": case.mutates_workspace,
        "dangerous": case.dangerous,
    }
    if case.approval_metadata is not None:
        snapshot["approvalMetadata"] = case.approval_metadata.model_dump(
            by_alias=True,
            mode="json",
        )
    if case.status_metadata is not None:
        snapshot["statusMetadata"] = case.status_metadata.model_dump(
            by_alias=True,
            mode="json",
        )
    return snapshot


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
        raise ValueError("permission-arbiter fixture path must stay under fixture_root")
    return resolved_candidate


def _reject_unsafe_path_text(path_text: str) -> None:
    if _FORBIDDEN_PATH_RE.search(path_text):
        raise ValueError("permission-arbiter fixtures must be local and non-production")


def _reject_unsafe_public_text(value: str) -> None:
    if _FORBIDDEN_PATH_RE.search(value):
        raise ValueError("permission-arbiter public projection contains unsafe path")
    if any(token in value for token in _FORBIDDEN_PUBLIC_TOKENS):
        raise ValueError("permission-arbiter public projection contains unsafe data")


def _reject_unsafe_public_snapshot(value: Mapping[str, object]) -> None:
    rendered = json.dumps(value, sort_keys=True)
    _reject_unsafe_public_text(rendered)


def _reject_unsafe_raw_value(value: object) -> None:
    _validate_json_like(value)
    if isinstance(value, str):
        if any(token in value for token in _FORBIDDEN_PUBLIC_TOKENS):
            raise ValueError("permission-arbiter fixture contains unsafe data")
        return
    if isinstance(value, Mapping):
        for key, nested_value in value.items():
            normalized = _normalize_key(key)
            if nested_value is True and normalized in _FORBIDDEN_RAW_KEY_TOKENS:
                raise ValueError("permission-arbiter fixture cannot claim live behavior")
            _reject_unsafe_raw_value(nested_value)
        return
    if isinstance(value, list | tuple):
        for item in value:
            _reject_unsafe_raw_value(item)


def _validate_json_like(value: object) -> None:
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, int) and not isinstance(value, bool):
        return
    if isinstance(value, float):
        if math.isfinite(value):
            return
        raise ValueError("permission-arbiter fixture values must be JSON-compatible")
    if isinstance(value, list | tuple):
        for item in value:
            _validate_json_like(item)
        return
    if isinstance(value, Mapping):
        for key, nested_value in value.items():
            if not isinstance(key, str):
                raise ValueError("permission-arbiter mappings must use string keys")
            _validate_json_like(nested_value)
        return
    raise ValueError("permission-arbiter fixture values must be JSON-compatible")


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
    "PermissionArbiterApprovalMetadata",
    "PermissionArbiterAttachmentFlags",
    "PermissionArbiterCase",
    "PermissionArbiterContractFixture",
    "PermissionArbiterProjection",
    "PermissionArbiterStatusMetadata",
    "load_permission_arbiter_contract_fixture",
    "project_permission_arbiter_contract_fixture",
]
