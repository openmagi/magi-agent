from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Mapping
from math import isfinite
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

from magi_agent.transport.tool_preview import sanitize_tool_preview


FailureClass = Literal[
    "tool_input_validation",
    "permission_denial",
    "transient_network",
    "context_overflow",
    "empty_or_truncated_model_response",
    "unknown_tool_call",
    "loop_detector_warning",
    "test_or_command_failure",
    "verifier_block",
    "delivery_failure",
]
RetryStrategy = Literal[
    "same_tool",
    "different_tool",
    "cheaper_model",
    "stronger_model",
    "user_clarification",
]
RetryDecision = Literal["allow_retry", "block_retry", "report_failure"]
ReportKind = Literal["partial_failure_report"]
RunOn = Literal["main", "child"]
AgentRole = Literal["general", "coding", "research"]

STABLE_FAILURE_CLASS_CATALOG: tuple[FailureClass, ...] = (
    "tool_input_validation",
    "permission_denial",
    "transient_network",
    "context_overflow",
    "empty_or_truncated_model_response",
    "unknown_tool_call",
    "loop_detector_warning",
    "test_or_command_failure",
    "verifier_block",
    "delivery_failure",
)
_HIDDEN_REASONING_KEYS = frozenset(
    {
        "chain_of_thought",
        "chainOfThought",
        "hidden_reasoning",
        "hiddenReasoning",
        "internal_reasoning",
        "internalReasoning",
        "raw_hidden_thoughts",
        "rawHiddenThoughts",
        "reasoning_trace",
        "reasoningTrace",
    }
)
_SECRET_KEY_PARTS = (
    "api_key",
    "apikey",
    "secret",
    "token",
    "password",
    "passphrase",
    "private_key",
    "client_secret",
)
_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    arbitrary_types_allowed=True,
)


class _SelfDebugModel(BaseModel):
    model_config = _MODEL_CONFIG

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        data = self.model_dump(by_alias=False, mode="python", warnings=False)
        if update:
            alias_to_name = {
                field.alias: name
                for name, field in self.__class__.model_fields.items()
                if field.alias is not None
            }
            data.update({alias_to_name.get(key, key): value for key, value in update.items()})
        return self.__class__.model_validate(data)


class _FrozenMetadata(Mapping[str, object]):
    def __init__(self, value: Mapping[str, object]) -> None:
        self._value = {key: _freeze_json_like(nested, key=key) for key, nested in value.items()}

    def __getitem__(self, key: str) -> object:
        return self._value[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._value)

    def __len__(self) -> int:
        return len(self._value)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Mapping):
            return _thaw_json_like(self) == _thaw_json_like(other)
        return False

    def __repr__(self) -> str:
        return repr(_thaw_json_like(self))


def _freeze_json_like(value: object, *, key: str | None = None) -> object:
    if key is not None and not isinstance(key, str):
        raise ValueError("metadata keys must be strings")
    if key in _HIDDEN_REASONING_KEYS:
        raise ValueError("observable metadata must not include hidden reasoning fields")
    if isinstance(value, _FrozenMetadata):
        return value
    if isinstance(value, Mapping):
        for nested_key in value:
            if not isinstance(nested_key, str):
                raise ValueError("metadata keys must be strings")
            if nested_key in _HIDDEN_REASONING_KEYS:
                raise ValueError("observable metadata must not include hidden reasoning fields")
        return _FrozenMetadata(value)
    if isinstance(value, list):
        return tuple(_freeze_json_like(item) for item in value)
    if isinstance(value, tuple | set | frozenset | bytes | bytearray):
        raise ValueError("metadata must contain only JSON-like values")
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError("metadata must contain only finite JSON-like float values")
        return value
    if isinstance(value, str | bool) or value is None:
        return _public_json_value(value, key=key)
    if isinstance(value, int):
        return value
    raise ValueError("metadata must contain only JSON-like values")


def _thaw_json_like(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw_json_like(nested) for key, nested in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json_like(item) for item in value]
    if isinstance(value, list):
        return [_thaw_json_like(item) for item in value]
    return value


def _public_json_value(value: object, *, key: str | None = None) -> object:
    if isinstance(value, str):
        if key is not None and _looks_secret_key(key):
            return "[redacted]"
        return sanitize_tool_preview(value)
    return value


def _looks_secret_key(key: str) -> bool:
    normalized = key.replace("-", "_").lower()
    return any(part in normalized for part in _SECRET_KEY_PARTS)


def _freeze_mapping(value: Mapping[str, object] | None) -> _FrozenMetadata:
    if value is None:
        return _FrozenMetadata({})
    return _freeze_json_like(value)  # type: ignore[return-value]


def _reject_empty(value: str, field_name: str) -> str:
    if not value.strip():
        raise ValueError(f"{field_name} must be non-empty")
    return value


def _default_strategy(failure_class: FailureClass) -> RetryStrategy:
    if failure_class == "transient_network":
        return "same_tool"
    if failure_class in {"context_overflow", "empty_or_truncated_model_response"}:
        return "cheaper_model"
    if failure_class in {"permission_denial", "verifier_block"}:
        return "user_clarification"
    return "different_tool"


class SelfDebugPolicyDefaults(_SelfDebugModel):
    default_enabled: Literal[False] = Field(default=False, alias="defaultEnabled")
    traffic_attached: Literal[False] = Field(default=False, alias="trafficAttached")
    execution_attached: Literal[False] = Field(default=False, alias="executionAttached")
    runner_attached: Literal[False] = Field(default=False, alias="runnerAttached")
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")
    retry_attached: Literal[False] = Field(default=False, alias="retryAttached")
    blocking_attached: Literal[False] = Field(default=False, alias="blockingAttached")
    canary_attached: Literal[False] = Field(default=False, alias="canaryAttached")


class ChangedConditionMetadata(_SelfDebugModel):
    changed_input: bool = Field(default=False, alias="changedInput")
    changed_tool: bool = Field(default=False, alias="changedTool")
    changed_plan: bool = Field(default=False, alias="changedPlan")
    changed_evidence_target: bool = Field(default=False, alias="changedEvidenceTarget")
    changed_permission_state: bool = Field(default=False, alias="changedPermissionState")
    changed_model_policy: bool = Field(default=False, alias="changedModelPolicy")

    @property
    def changed_dimensions(self) -> tuple[str, ...]:
        dimensions: list[str] = []
        if self.changed_input:
            dimensions.append("input")
        if self.changed_tool:
            dimensions.append("tool")
        if self.changed_plan:
            dimensions.append("plan")
        if self.changed_evidence_target:
            dimensions.append("evidence_target")
        if self.changed_permission_state:
            dimensions.append("permission_state")
        if self.changed_model_policy:
            dimensions.append("model_policy")
        return tuple(dimensions)

    @property
    def has_changed_condition(self) -> bool:
        return bool(self.changed_dimensions)


class FailureSafetyMetadata(_SelfDebugModel):
    hard_safety: bool = Field(default=False, alias="hardSafety")
    approval_required: bool = Field(default=False, alias="approvalRequired")
    optional: bool = True
    fail_open: bool = Field(default=False, alias="failOpen")
    opt_out_allowed: bool = Field(default=True, alias="optOutAllowed")

    @model_validator(mode="after")
    def _validate_fail_closed_non_optional(self) -> Self:
        if not self.optional and (self.fail_open or self.opt_out_allowed):
            raise ValueError("non-optional safety metadata must fail closed and cannot opt out")
        if self.hard_safety and (self.fail_open or self.opt_out_allowed):
            raise ValueError("hard-safety metadata must fail closed and cannot opt out")
        if self.hard_safety and not self.approval_required:
            raise ValueError("hard-safety metadata requires approval-required posture")
        return self


class ActionFingerprintInput(_SelfDebugModel):
    action_type: Literal["tool_call", "model_response", "verifier", "delivery"] = Field(
        alias="actionType"
    )
    tool_name: str | None = Field(default=None, alias="toolName")
    observable_input: _FrozenMetadata = Field(
        default_factory=lambda: _FrozenMetadata({}),
        alias="observableInput",
    )
    evidence_target: str | None = Field(default=None, alias="evidenceTarget")
    permission_state: str | None = Field(default=None, alias="permissionState")
    model_policy: str | None = Field(default=None, alias="modelPolicy")

    @field_validator("tool_name", "evidence_target", "permission_state", "model_policy")
    @classmethod
    def _reject_empty_optional(cls, value: str | None) -> str | None:
        if value is not None:
            return _reject_empty(value, "observable action metadata")
        return value

    @field_validator("observable_input", mode="before")
    @classmethod
    def _validate_observable_input(cls, value: object) -> _FrozenMetadata:
        if value is None:
            return _FrozenMetadata({})
        if not isinstance(value, Mapping):
            raise ValueError("observableInput must be a JSON-like object")
        return _freeze_mapping(value)

    @field_serializer("observable_input")
    def _serialize_observable_input(self, value: _FrozenMetadata) -> dict[str, object]:
        return _thaw_json_like(value)  # type: ignore[return-value]

    def public_fingerprint_material(self) -> str:
        return json.dumps(
            {
                "actionType": self.action_type,
                "toolName": self.tool_name,
                "observableInput": _thaw_json_like(self.observable_input),
                "evidenceTarget": self.evidence_target,
                "permissionState": self.permission_state,
                "modelPolicy": self.model_policy,
            },
            sort_keys=True,
            separators=(",", ":"),
        )


class RetryFailureMetadata(_SelfDebugModel):
    failure_class: FailureClass = Field(alias="failureClass")
    error_message: str = Field(alias="errorMessage")
    public_error_preview: str | None = Field(default=None, alias="publicErrorPreview")
    structured_fields: _FrozenMetadata = Field(
        default_factory=lambda: _FrozenMetadata({}),
        alias="structuredFields",
    )

    @field_validator("error_message")
    @classmethod
    def _reject_empty_error_message(cls, value: str) -> str:
        return sanitize_tool_preview(_reject_empty(value, "errorMessage"))

    @field_validator("structured_fields", mode="before")
    @classmethod
    def _validate_structured_fields(cls, value: object) -> _FrozenMetadata:
        if value is None:
            return _FrozenMetadata({})
        if not isinstance(value, Mapping):
            raise ValueError("structuredFields must be a JSON-like object")
        return _freeze_mapping(value)

    @field_serializer("structured_fields")
    def _serialize_structured_fields(self, value: _FrozenMetadata) -> dict[str, object]:
        return _thaw_json_like(value)  # type: ignore[return-value]

    @model_validator(mode="after")
    def _fill_public_preview(self) -> Self:
        preview = sanitize_tool_preview(self.error_message)
        if self.public_error_preview is None:
            object.__setattr__(self, "public_error_preview", preview)
        else:
            object.__setattr__(
                self,
                "public_error_preview",
                sanitize_tool_preview(self.public_error_preview),
            )
        return self


class RetryStateMetadata(_SelfDebugModel):
    session_id: str = Field(alias="sessionId")
    turn_id: str = Field(alias="turnId")
    run_on: RunOn = Field(alias="runOn")
    agent_role: AgentRole = Field(alias="agentRole")
    spawn_depth: int = Field(alias="spawnDepth")
    failure: RetryFailureMetadata
    failed_action_fingerprint: str = Field(alias="failedActionFingerprint")
    evidence_refs: tuple[str, ...] = Field(default=(), alias="evidenceRefs")
    required_next_action: str = Field(alias="requiredNextAction")
    attempts_remaining: int = Field(alias="attemptsRemaining")
    strategy: RetryStrategy
    changed_condition: ChangedConditionMetadata = Field(
        default_factory=ChangedConditionMetadata,
        alias="changedCondition",
    )
    safety: FailureSafetyMetadata = Field(default_factory=FailureSafetyMetadata)
    metadata: _FrozenMetadata = Field(default_factory=lambda: _FrozenMetadata({}))
    traffic_attached: Literal[False] = Field(default=False, alias="trafficAttached")
    execution_attached: Literal[False] = Field(default=False, alias="executionAttached")
    runner_attached: Literal[False] = Field(default=False, alias="runnerAttached")
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")
    retry_attached: Literal[False] = Field(default=False, alias="retryAttached")
    blocking_attached: Literal[False] = Field(default=False, alias="blockingAttached")
    canary_attached: Literal[False] = Field(default=False, alias="canaryAttached")

    @field_validator("session_id", "turn_id", "failed_action_fingerprint", "required_next_action")
    @classmethod
    def _reject_empty_required_text(cls, value: str) -> str:
        return _reject_empty(value, "retry state field")

    @field_validator("evidence_refs")
    @classmethod
    def _reject_empty_evidence_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() for item in value):
            raise ValueError("evidenceRefs entries must be non-empty")
        return value

    @field_validator("metadata", mode="before")
    @classmethod
    def _validate_metadata(cls, value: object) -> _FrozenMetadata:
        if value is None:
            return _FrozenMetadata({})
        if not isinstance(value, Mapping):
            raise ValueError("metadata must be a JSON-like object")
        return _freeze_mapping(value)

    @field_serializer("metadata")
    def _serialize_metadata(self, value: _FrozenMetadata) -> dict[str, object]:
        return _thaw_json_like(value)  # type: ignore[return-value]

    @model_validator(mode="after")
    def _validate_state(self) -> Self:
        if self.spawn_depth < 0:
            raise ValueError("spawnDepth must be non-negative")
        if self.run_on == "main" and self.spawn_depth != 0:
            raise ValueError("main retry metadata must use spawnDepth=0")
        if self.run_on == "child" and self.spawn_depth <= 0:
            raise ValueError("child retry metadata must use spawnDepth greater than 0")
        if self.attempts_remaining < 0:
            raise ValueError("attemptsRemaining must be non-negative")
        if self.failure.failure_class in {"permission_denial", "verifier_block"}:
            if (
                not self.safety.hard_safety
                or not self.safety.approval_required
                or self.safety.optional
                or self.safety.fail_open
                or self.safety.opt_out_allowed
            ):
                raise ValueError(
                    "permission/verifier safety metadata must be hard-safety "
                    "approval-required non-optional fail-closed and non-opt-out"
                )
        return self


class RetryDecisionMetadata(_SelfDebugModel):
    retry_state: RetryStateMetadata = Field(alias="retryState")
    decision: RetryDecision
    reason: str
    changed_condition: ChangedConditionMetadata = Field(
        default_factory=ChangedConditionMetadata,
        alias="changedCondition",
    )
    report_kind: ReportKind | None = Field(default=None, alias="reportKind")
    public_report_preview: str | None = Field(default=None, alias="publicReportPreview")
    traffic_attached: Literal[False] = Field(default=False, alias="trafficAttached")
    execution_attached: Literal[False] = Field(default=False, alias="executionAttached")
    runner_attached: Literal[False] = Field(default=False, alias="runnerAttached")
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")
    retry_attached: Literal[False] = Field(default=False, alias="retryAttached")
    blocking_attached: Literal[False] = Field(default=False, alias="blockingAttached")
    canary_attached: Literal[False] = Field(default=False, alias="canaryAttached")

    @field_validator("reason")
    @classmethod
    def _reject_empty_reason(cls, value: str) -> str:
        return sanitize_tool_preview(_reject_empty(value, "reason"))

    @model_validator(mode="after")
    def _fill_public_report_preview(self) -> Self:
        preview = sanitize_tool_preview(self.public_report_preview or self.reason)
        object.__setattr__(self, "public_report_preview", preview)
        if self.decision == "report_failure" and self.report_kind is None:
            object.__setattr__(self, "report_kind", "partial_failure_report")
        if self.decision != "report_failure" and self.report_kind is not None:
            raise ValueError("reportKind is only valid for report_failure decisions")
        return self


def stable_failure_class_catalog() -> tuple[FailureClass, ...]:
    return STABLE_FAILURE_CLASS_CATALOG


def self_debug_policy_defaults() -> SelfDebugPolicyDefaults:
    return SelfDebugPolicyDefaults()


def action_fingerprint(action: ActionFingerprintInput) -> str:
    validated = ActionFingerprintInput.model_validate(
        action.model_dump(by_alias=True, mode="python", warnings=False)
    )
    digest = hashlib.sha256(validated.public_fingerprint_material().encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def build_retry_state(
    *,
    sessionId: str,
    turnId: str,
    runOn: RunOn,
    agentRole: AgentRole,
    spawnDepth: int,
    failure: RetryFailureMetadata,
    failedAction: ActionFingerprintInput,
    attemptsRemaining: int,
    evidenceRefs: tuple[str, ...] = (),
    requiredNextAction: str | None = None,
    strategy: RetryStrategy | None = None,
    changedCondition: ChangedConditionMetadata | None = None,
    safety: FailureSafetyMetadata | None = None,
    metadata: Mapping[str, object] | None = None,
) -> RetryStateMetadata:
    resolved_strategy = strategy or _default_strategy(failure.failure_class)
    return RetryStateMetadata(
        sessionId=sessionId,
        turnId=turnId,
        runOn=runOn,
        agentRole=agentRole,
        spawnDepth=spawnDepth,
        failure=failure,
        failedActionFingerprint=action_fingerprint(failedAction),
        evidenceRefs=evidenceRefs,
        requiredNextAction=requiredNextAction
        or _required_next_action(failure.failure_class, resolved_strategy),
        attemptsRemaining=attemptsRemaining,
        strategy=resolved_strategy,
        changedCondition=changedCondition or ChangedConditionMetadata(),
        safety=safety or _safety_for_failure(failure.failure_class),
        metadata=metadata or {},
        trafficAttached=False,
        executionAttached=False,
        runnerAttached=False,
        routeAttached=False,
        retryAttached=False,
        blockingAttached=False,
        canaryAttached=False,
    )


def decide_retry_metadata(
    retry_state: RetryStateMetadata,
    *,
    next_action: ActionFingerprintInput,
) -> RetryDecisionMetadata:
    state = RetryStateMetadata.model_validate(
        retry_state.model_dump(by_alias=True, mode="python", warnings=False)
    )
    next_fingerprint = action_fingerprint(next_action)

    if state.attempts_remaining <= 0:
        return RetryDecisionMetadata(
            retryState=state,
            decision="report_failure",
            reason=(
                "attempts exhausted; emit partial/failure retry report metadata "
                "without attaching a live retry loop"
            ),
            changedCondition=state.changed_condition,
            reportKind="partial_failure_report",
        )

    changed_condition = _infer_changed_condition(state, next_action)
    if (
        state.failure.failure_class == "transient_network"
        and state.strategy == "same_tool"
        and next_fingerprint == state.failed_action_fingerprint
    ):
        return RetryDecisionMetadata(
            retryState=state,
            decision="allow_retry",
            reason=(
                "transient network failure permits bounded same-tool retry metadata "
                "while attempts remain"
            ),
            changedCondition=changed_condition,
        )
    if (
        next_fingerprint == state.failed_action_fingerprint
        and not changed_condition.has_changed_condition
    ):
        return RetryDecisionMetadata(
            retryState=state,
            decision="block_retry",
            reason=(
                "repeated identical failed action has no changed condition; "
                "retry metadata is blocked"
            ),
            changedCondition=changed_condition,
        )

    return RetryDecisionMetadata(
        retryState=state,
        decision="allow_retry",
        reason="retry metadata allowed because at least one retry condition changed",
        changedCondition=changed_condition,
    )


def _infer_changed_condition(
    state: RetryStateMetadata,
    next_action: ActionFingerprintInput,
) -> ChangedConditionMetadata:
    declared = state.changed_condition
    if declared.has_changed_condition:
        return declared
    # Without the original action metadata, the scaffold can only prove a changed
    # condition when the public fingerprint differs.
    if action_fingerprint(next_action) != state.failed_action_fingerprint:
        return ChangedConditionMetadata(changedInput=True)
    return ChangedConditionMetadata()


def _required_next_action(
    failure_class: FailureClass,
    strategy: RetryStrategy,
) -> str:
    if failure_class == "transient_network" and strategy == "same_tool":
        return "retry same tool with bounded attempts after transient network evidence"
    if failure_class in {"permission_denial", "verifier_block"}:
        return "obtain approval or satisfy verifier before retrying"
    return "retry only after changing input, tool, plan, evidence target, permission, or model policy"


def _safety_for_failure(failure_class: FailureClass) -> FailureSafetyMetadata:
    if failure_class in {"permission_denial", "verifier_block"}:
        return FailureSafetyMetadata(
            hardSafety=True,
            approvalRequired=True,
            optional=False,
            failOpen=False,
            optOutAllowed=False,
        )
    return FailureSafetyMetadata()


__all__ = [
    "ActionFingerprintInput",
    "AgentRole",
    "ChangedConditionMetadata",
    "FailureClass",
    "FailureSafetyMetadata",
    "ReportKind",
    "RetryDecision",
    "RetryDecisionMetadata",
    "RetryFailureMetadata",
    "RetryStateMetadata",
    "RetryStrategy",
    "RunOn",
    "SelfDebugPolicyDefaults",
    "STABLE_FAILURE_CLASS_CATALOG",
    "action_fingerprint",
    "build_retry_state",
    "decide_retry_metadata",
    "self_debug_policy_defaults",
    "stable_failure_class_catalog",
]
