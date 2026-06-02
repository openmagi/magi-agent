from __future__ import annotations

import json
import math
import re
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from openmagi_core_agent.runtime.control import ControlRequest
from openmagi_core_agent.tools.manifest import RuntimeMode, ToolManifest
from openmagi_core_agent.tools.result import ToolResult
from openmagi_core_agent.transport.tool_preview import sanitize_tool_preview


ToolHostContractOutcome = Literal[
    "allowed",
    "denied",
    "approval_required",
    "missing_handler",
    "timeout",
    "handler_error",
    "redaction_failure",
    "disabled",
    "protected_replacement_attempt",
]
ToolHostPolicyAction = Literal["allow", "deny", "ask", "block", "error"]

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
        "tool_dispatched_live",
        "traffic_attached",
    }
)


class ToolHostContractAttachmentFlags(BaseModel):
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


class ProtectedReplacementAttempt(BaseModel):
    model_config = _MODEL_CONFIG

    attempted_kind: str = Field(alias="attemptedKind")
    attempted_source_kind: str = Field(alias="attemptedSourceKind")
    attempted_permission: str = Field(alias="attemptedPermission")
    attempted_dangerous: bool = Field(alias="attemptedDangerous")
    attempted_mutates_workspace: bool = Field(alias="attemptedMutatesWorkspace")
    downgrade_reasons: tuple[str, ...] = Field(alias="downgradeReasons")
    preserved_handler: bool = Field(alias="preservedHandler")


class ToolHostContractCase(BaseModel):
    model_config = _MODEL_CONFIG

    case_id: str = Field(alias="caseId")
    outcome: ToolHostContractOutcome
    mode: RuntimeMode
    tool: ToolManifest
    enabled: bool
    handler_available: bool = Field(alias="handlerAvailable")
    protected: bool
    policy_action: ToolHostPolicyAction = Field(alias="policyAction")
    blocking: bool
    fail_open: bool = Field(alias="failOpen")
    fail_closed: bool = Field(alias="failClosed")
    timeout_budget_ms: int = Field(alias="timeoutBudgetMs")
    result: ToolResult
    control_request: ControlRequest | None = Field(default=None, alias="controlRequest")
    recorded_output_preview: str = Field(default="", alias="recordedOutputPreview")
    evidence_refs: tuple[str, ...] = Field(default=(), alias="evidenceRefs")
    audit_refs: tuple[str, ...] = Field(default=(), alias="auditRefs")
    replacement_attempt: ProtectedReplacementAttempt | None = Field(
        default=None,
        alias="replacementAttempt",
    )
    attachment_flags: ToolHostContractAttachmentFlags = Field(alias="attachmentFlags")

    @model_validator(mode="before")
    @classmethod
    def _validate_raw_case(cls, value: object) -> object:
        if isinstance(value, Mapping):
            _validate_json_like(value)
            control_request = value.get("controlRequest")
            if isinstance(control_request, Mapping):
                arguments = control_request.get("arguments")
                if isinstance(arguments, Mapping):
                    _reject_unsafe_control_arguments(arguments)
        return value

    @model_validator(mode="after")
    def _validate_contract_case(self) -> Self:
        if self.timeout_budget_ms != self.tool.timeout_ms:
            raise ValueError("timeoutBudgetMs must match tool timeoutMs")
        if self.blocking and self.fail_open:
            raise ValueError("blocking ToolHost outcomes cannot be fail-open")
        if self.fail_closed and self.fail_open:
            raise ValueError("ToolHost outcome cannot be both fail-open and fail-closed")
        _validate_case_outcome(self)
        return self


class ToolHostContractFixture(BaseModel):
    model_config = _MODEL_CONFIG

    schema_version: Literal["toolhostContractFixture.v1"] = Field(alias="schemaVersion")
    fixture_id: str = Field(alias="fixtureId")
    source_runtime: Literal["typescript-core-agent"] = Field(alias="sourceRuntime")
    recording_mode: Literal["local_diagnostic_fixture"] = Field(alias="recordingMode")
    redaction_status: Literal["verified"] = Field(alias="redactionStatus")
    local_diagnostic: Literal[True] = Field(alias="localDiagnostic")
    attachment_flags: ToolHostContractAttachmentFlags = Field(alias="attachmentFlags")
    cases: tuple[ToolHostContractCase, ...]

    @model_validator(mode="before")
    @classmethod
    def _validate_raw_fixture(cls, value: object) -> object:
        if isinstance(value, Mapping):
            _validate_json_like(value)
        return value

    @model_validator(mode="after")
    def _validate_contract_matrix(self) -> Self:
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("ToolHost contract caseIds must be unique")
        outcomes = {case.outcome for case in self.cases}
        required_outcomes = set(ToolHostContractOutcome.__args__)  # type: ignore[attr-defined]
        if not required_outcomes.issubset(outcomes):
            raise ValueError("ToolHost contract fixture is missing required outcomes")
        return self


class ToolHostContractProjection(BaseModel):
    model_config = _MODEL_CONFIG

    fixture_id: str = Field(alias="fixtureId")
    local_diagnostic: Literal[True] = Field(default=True, alias="localDiagnostic")
    attachment_flags: ToolHostContractAttachmentFlags = Field(alias="attachmentFlags")
    no_live_execution: Literal[True] = Field(alias="noLiveExecution")
    case_order: tuple[str, ...] = Field(alias="caseOrder")
    by_outcome: dict[str, int] = Field(alias="byOutcome")
    public_previews: dict[str, str] = Field(alias="publicPreviews")
    control_requests: dict[str, dict[str, object]] = Field(alias="controlRequests")
    case_snapshots: dict[str, dict[str, object]] = Field(alias="caseSnapshots")


def load_toolhost_contract_fixture(
    path: str | Path,
    *,
    fixture_root: str | Path | None = None,
) -> ToolHostContractFixture:
    resolved_path = _resolve_fixture_path(path, fixture_root=fixture_root)
    with resolved_path.open("r", encoding="utf-8") as fixture_file:
        payload: object = json.load(fixture_file)
    return ToolHostContractFixture.model_validate(payload)


def project_toolhost_contract_fixture(
    fixture: ToolHostContractFixture | Mapping[str, Any],
) -> ToolHostContractProjection:
    safe_fixture = _validated_fixture_snapshot(fixture)
    public_previews: dict[str, str] = {}
    control_requests: dict[str, dict[str, object]] = {}
    case_snapshots: dict[str, dict[str, object]] = {}
    for case in safe_fixture.cases:
        preview = _sanitize_public_preview(case.recorded_output_preview)
        public_previews[case.case_id] = preview
        if case.control_request is not None:
            control_requests[case.case_id] = _public_control_request(case.control_request)
        snapshot = _case_snapshot(case, preview=preview)
        _reject_unsafe_public_snapshot(snapshot)
        case_snapshots[case.case_id] = snapshot
    return ToolHostContractProjection(
        fixtureId=safe_fixture.fixture_id,
        attachmentFlags=safe_fixture.attachment_flags,
        noLiveExecution=True,
        caseOrder=tuple(case.case_id for case in safe_fixture.cases),
        byOutcome=dict(Counter(case.outcome for case in safe_fixture.cases)),
        publicPreviews=public_previews,
        controlRequests=control_requests,
        caseSnapshots=case_snapshots,
    )


def _validated_fixture_snapshot(
    fixture: ToolHostContractFixture | Mapping[str, Any],
) -> ToolHostContractFixture:
    if isinstance(fixture, ToolHostContractFixture):
        return ToolHostContractFixture.model_validate(
            fixture.model_dump(by_alias=True, mode="json", warnings=False)
        )
    return ToolHostContractFixture.model_validate(fixture)


def _case_snapshot(case: ToolHostContractCase, *, preview: str) -> dict[str, object]:
    snapshot: dict[str, object] = {
        "caseId": case.case_id,
        "outcome": case.outcome,
        "mode": case.mode,
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
        "enabled": case.enabled,
        "handlerAvailable": case.handler_available,
        "protected": case.protected,
        "policyAction": case.policy_action,
        "blocking": case.blocking,
        "failOpen": case.fail_open,
        "failClosed": case.fail_closed,
        "timeoutBudgetMs": case.timeout_budget_ms,
        "result": {
            "status": case.result.status,
            "errorCode": case.result.error_code,
            "retryable": case.result.retryable,
        },
        "outputPreview": preview,
        "evidenceRefs": case.evidence_refs,
        "auditRefs": case.audit_refs,
    }
    if case.control_request is not None:
        snapshot["controlRequest"] = _public_control_request(case.control_request)
    if case.replacement_attempt is not None:
        snapshot["replacementAttempt"] = {
            "downgradeReasons": case.replacement_attempt.downgrade_reasons,
            "preservedHandler": case.replacement_attempt.preserved_handler,
        }
    return snapshot


def _public_control_request(request: ControlRequest) -> dict[str, object]:
    return {
        "requestId": request.request_id,
        "turnId": request.turn_id,
        "toolName": request.tool_name,
        "reason": request.reason,
    }


def _validate_case_outcome(case: ToolHostContractCase) -> None:
    if case.outcome == "allowed":
        if case.policy_action != "allow" or case.result.status != "ok":
            raise ValueError("allowed ToolHost case must allow and return ok")
        if case.tool.permission not in {"read", "meta"} or case.tool.dangerous:
            raise ValueError("allowed ToolHost case must be readonly/meta and not dangerous")
        if case.tool.mutates_workspace:
            raise ValueError("allowed ToolHost case must not mutate workspace")
    elif case.outcome == "denied":
        if case.policy_action != "deny" or case.result.status != "blocked":
            raise ValueError("denied ToolHost case must deny and block")
        if not case.blocking or not case.fail_closed:
            raise ValueError("denied ToolHost case must block fail-closed")
    elif case.outcome == "approval_required":
        if (
            case.policy_action != "ask"
            or case.result.status != "needs_approval"
            or case.control_request is None
        ):
            raise ValueError("approval-required ToolHost case must ask with ControlRequest")
    elif case.outcome == "missing_handler":
        if case.handler_available or case.result.error_code != "tool_handler_missing":
            raise ValueError("missing-handler ToolHost case must declare missing handler")
    elif case.outcome == "timeout":
        if case.result.error_code != "tool_timeout":
            raise ValueError("timeout ToolHost case must use tool_timeout")
    elif case.outcome == "handler_error":
        if case.result.error_code != "handler_error":
            raise ValueError("handler-error ToolHost case must use handler_error")
    elif case.outcome == "redaction_failure":
        if case.result.error_code != "public_redaction_failed":
            raise ValueError("redaction-failure ToolHost case must use public_redaction_failed")
    elif case.outcome == "disabled":
        if case.enabled or case.result.status != "blocked":
            raise ValueError("disabled ToolHost case must be disabled and blocked")
    elif case.outcome == "protected_replacement_attempt":
        if case.replacement_attempt is None or not case.protected:
            raise ValueError("protected replacement case must include protected metadata")


def _sanitize_public_preview(value: str) -> str:
    return _PRODUCTION_PATH_RE.sub("[redacted-path]", sanitize_tool_preview(value))


def _reject_unsafe_control_arguments(value: Mapping[str, object]) -> None:
    _reject_unsafe_raw_value(value, allow_redacted_paths=False)


def _reject_unsafe_public_snapshot(value: Mapping[str, object]) -> None:
    rendered = json.dumps(value, sort_keys=True)
    if _FORBIDDEN_PATH_RE.search(rendered):
        raise ValueError("ToolHost contract public snapshot contains production paths")
    unsafe_tokens = (
        "Bearer unsafe",
        "ghp_contractsecret",
        "sk-handler-error-secret",
        "private tool args",
        "raw_secret",
        "pythonResponseAuthority",
    )
    if any(token in rendered for token in unsafe_tokens):
        raise ValueError("ToolHost contract public snapshot contains unsafe data")


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
        raise ValueError("ToolHost contract fixture path must stay under fixture_root")
    return resolved_candidate


def _reject_unsafe_path_text(path_text: str) -> None:
    if _FORBIDDEN_PATH_RE.search(path_text):
        raise ValueError("ToolHost contract fixtures must be local and non-production")


def _reject_unsafe_raw_value(
    value: object,
    *,
    allow_redacted_paths: bool,
    _path: tuple[str, ...] = (),
) -> None:
    _validate_json_like(value)
    if isinstance(value, str):
        if not allow_redacted_paths and _FORBIDDEN_PATH_RE.search(value):
            raise ValueError("ToolHost contract fixture contains unsafe path")
        return
    if isinstance(value, Mapping):
        for key, nested_value in value.items():
            normalized = _normalize_key(key)
            if nested_value is True and normalized in _FORBIDDEN_RAW_KEY_TOKENS:
                raise ValueError("ToolHost contract fixture cannot claim live behavior")
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
        raise ValueError("ToolHost contract fixture values must be JSON-compatible")
    if isinstance(value, list | tuple):
        for item in value:
            _validate_json_like(item)
        return
    if isinstance(value, Mapping):
        for key, nested_value in value.items():
            if not isinstance(key, str):
                raise ValueError("ToolHost contract mappings must use string keys")
            _validate_json_like(nested_value)
        return
    raise ValueError("ToolHost contract fixture values must be JSON-compatible")


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
    "ProtectedReplacementAttempt",
    "ToolHostContractAttachmentFlags",
    "ToolHostContractCase",
    "ToolHostContractFixture",
    "ToolHostContractProjection",
    "load_toolhost_contract_fixture",
    "project_toolhost_contract_fixture",
]
