from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, Self, TypeVar, get_args, get_origin

from pydantic import Field, field_validator, model_validator

from magi_agent.evidence.ledger import _redact_public_summary_text
from magi_agent.evidence.types import EvidenceMetadataModel, validate_evidence_type_name


VerifierStage = Literal[
    "schema_structured_output",
    "tool_evidence_contract",
    "file_artifact_delivery",
    "source_claim_link",
    "task_plan_completion",
    "security_policy",
    "llm_critic",
]
VerifierPhase = Literal["deterministic", "semantic_critic"]
VerifierAction = Literal["audit", "retry", "terminal", "block_final_answer", "approval_required"]
VerifierStatus = Literal["pass", "failed", "missing", "approval_required", "audit"]
ControlRequestKindMetadata = Literal["tool_permission", "plan_approval", "user_question"]
ControlRequestSourceMetadata = Literal["turn", "mcp", "child-agent", "plan", "system"]
CriticEscalationReason = Literal[
    "fuzzy_quality",
    "missing_reasoning",
    "ambiguity",
    "synthesis_quality",
]

_PROTECTED_HARD_SAFETY_VERIFIER_ID = "security-policy-hard-safety"
_PROTECTED_HARD_SAFETY_DEFAULT_PRIORITY = 60
_CRITIC_ESCALATION_REASONS: frozenset[str] = frozenset(
    (
        "fuzzy_quality",
        "missing_reasoning",
        "ambiguity",
        "synthesis_quality",
    )
)
_STAGE_ORDER: tuple[VerifierStage, ...] = (
    "schema_structured_output",
    "tool_evidence_contract",
    "file_artifact_delivery",
    "source_claim_link",
    "task_plan_completion",
    "security_policy",
    "llm_critic",
)
_STAGE_RANK = {stage: index + 1 for index, stage in enumerate(_STAGE_ORDER)}
_MAX_PUBLIC_TEXT_CHARS = 200


class VerifierBusModel(EvidenceMetadataModel):
    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        data = _canonicalize_false_only_fields(cls, values)
        return cls.model_validate(data)

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


_VerifierBusModelT = TypeVar("_VerifierBusModelT", bound=VerifierBusModel)


def _is_false_only_field(annotation: object) -> bool:
    return get_origin(annotation) is Literal and get_args(annotation) == (False,)


def _canonicalize_false_only_fields(
    model_type: type[VerifierBusModel],
    values: Mapping[str, Any],
) -> dict[str, Any]:
    data = dict(values)
    for name, field in model_type.model_fields.items():
        if not _is_false_only_field(field.annotation):
            continue
        if name in data:
            data[name] = False
        if field.alias is not None and field.alias in data:
            data[field.alias] = False
    return data


def _revalidate_nested_model(
    value: _VerifierBusModelT,
    model_type: type[_VerifierBusModelT],
) -> _VerifierBusModelT:
    if isinstance(value, model_type):
        return model_type.model_validate(
            value.model_dump(by_alias=False, mode="python", warnings=False)
        )
    return value


def _validate_unique_non_empty_strings(value: tuple[str, ...], field_name: str) -> tuple[str, ...]:
    if any(not item.strip() for item in value):
        raise ValueError(f"{field_name} must not contain empty strings")
    if len(set(value)) != len(value):
        raise ValueError(f"{field_name} must not contain duplicates")
    return value


def _sanitize_public_text(value: str | None) -> str | None:
    if value is None:
        return None
    sanitized = _redact_public_summary_text(value)
    if len(sanitized) > _MAX_PUBLIC_TEXT_CHARS:
        return f"{sanitized[: _MAX_PUBLIC_TEXT_CHARS - 3]}..."
    return sanitized


def _set_default_alias_aware(
    data: dict[str, object],
    field_name: str,
    alias: str,
    value: object,
) -> None:
    if field_name in data or alias not in data:
        data.setdefault(field_name, value)
        return
    data.setdefault(alias, value)


def _resolve_escalation_reason(
    *,
    escalationReason: str | None,
    escalation_reason: str | None,
) -> str | None:
    supplied = tuple(
        reason
        for reason in (escalationReason, escalation_reason)
        if reason is not None
    )
    invalid = tuple(reason for reason in supplied if reason not in _CRITIC_ESCALATION_REASONS)
    if invalid:
        raise ValueError("escalation reason is not supported")
    if len(set(supplied)) > 1:
        raise ValueError("escalation reason aliases must match")
    return supplied[0] if supplied else None


class VerifierStageMetadata(VerifierBusModel):
    stage: VerifierStage
    order: int
    phase: VerifierPhase
    description: str
    deterministic_prerequisite: bool = Field(default=True, alias="deterministicPrerequisite")

    @field_validator("description")
    @classmethod
    def _reject_empty_description(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("description must be non-empty")
        return value

    @model_validator(mode="after")
    def _validate_stage_order_and_phase(self) -> Self:
        if self.order != _STAGE_RANK[self.stage]:
            raise ValueError("stage order must match deterministic-to-semantic verifier order")
        expected_phase = "semantic_critic" if self.stage == "llm_critic" else "deterministic"
        if self.phase != expected_phase:
            raise ValueError("stage phase must match verifier bus stage semantics")
        if self.stage == "llm_critic" and self.deterministic_prerequisite:
            raise ValueError("llm critic is not a deterministic prerequisite stage")
        if self.stage != "llm_critic" and not self.deterministic_prerequisite:
            raise ValueError("deterministic verifier stages must be prerequisites")
        return self


class VerifierInputDeclaration(VerifierBusModel):
    evidence_types: tuple[str, ...] = Field(default=(), alias="evidenceTypes")
    ledger_refs: tuple[str, ...] = Field(default=(), alias="ledgerRefs")
    artifact_refs: tuple[str, ...] = Field(default=(), alias="artifactRefs")
    session_refs: tuple[str, ...] = Field(default=(), alias="sessionRefs")
    transcript_refs: tuple[str, ...] = Field(default=(), alias="transcriptRefs")
    control_refs: tuple[str, ...] = Field(default=(), alias="controlRefs")
    metadata_only: Literal[True] = Field(default=True, alias="metadataOnly")

    @field_validator("evidence_types")
    @classmethod
    def _validate_evidence_types(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(validate_evidence_type_name(item) for item in value)

    @field_validator("ledger_refs", "artifact_refs", "session_refs", "transcript_refs", "control_refs")
    @classmethod
    def _validate_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _validate_unique_non_empty_strings(value, "refs")


class ApprovalRequestMetadata(VerifierBusModel):
    kind: ControlRequestKindMetadata
    source: ControlRequestSourceMetadata
    reason: str
    public_preview: str | None = Field(default=None, alias="publicPreview")
    control_request_attached: Literal[False] = Field(default=False, alias="controlRequestAttached")
    metadata_only: Literal[True] = Field(default=True, alias="metadataOnly")

    @field_validator("reason")
    @classmethod
    def _reject_empty_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("reason must be non-empty")
        return value

    @field_validator("public_preview")
    @classmethod
    def _sanitize_public_preview(cls, value: str | None) -> str | None:
        return _sanitize_public_text(value)


class FailureRoutingMetadata(VerifierBusModel):
    actions: tuple[VerifierAction, ...] = ("audit",)
    retryable: bool = False
    terminal: bool = False
    block_final_answer: bool = Field(default=False, alias="blockFinalAnswer")
    approval_required: bool = Field(default=False, alias="approvalRequired")
    fail_open: bool = Field(default=False, alias="failOpen")
    fail_closed: bool = Field(default=True, alias="failClosed")
    approval_request: ApprovalRequestMetadata | None = Field(default=None, alias="approvalRequest")
    metadata_only: Literal[True] = Field(default=True, alias="metadataOnly")

    @model_validator(mode="before")
    @classmethod
    def _infer_fail_mode(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        has_fail_open = "failOpen" in data or "fail_open" in data
        has_fail_closed = "failClosed" in data or "fail_closed" in data
        if has_fail_open and not has_fail_closed:
            value_to_invert = bool(data.get("failOpen", data.get("fail_open")))
            if "fail_open" in data:
                data["fail_closed"] = not value_to_invert
            else:
                data["failClosed"] = not value_to_invert
        if has_fail_closed and not has_fail_open:
            value_to_invert = bool(data.get("failClosed", data.get("fail_closed")))
            if "fail_closed" in data:
                data["fail_open"] = not value_to_invert
            else:
                data["failOpen"] = not value_to_invert
        return data

    @field_validator("actions")
    @classmethod
    def _reject_duplicate_actions(cls, value: tuple[VerifierAction, ...]) -> tuple[VerifierAction, ...]:
        if len(set(value)) != len(value):
            raise ValueError("actions must not contain duplicates")
        return value

    @field_validator("approval_request")
    @classmethod
    def _revalidate_approval_request(
        cls,
        value: ApprovalRequestMetadata | None,
    ) -> ApprovalRequestMetadata | None:
        if value is None:
            return None
        return _revalidate_nested_model(value, ApprovalRequestMetadata)

    @model_validator(mode="after")
    def _validate_actions(self) -> Self:
        action_set = set(self.actions)
        if self.retryable != ("retry" in action_set):
            raise ValueError("retryable must match retry action metadata")
        if self.terminal != ("terminal" in action_set):
            raise ValueError("terminal must match terminal action metadata")
        if self.block_final_answer != ("block_final_answer" in action_set):
            raise ValueError("blockFinalAnswer must match block_final_answer action metadata")
        if self.approval_request is not None and "approval_required" not in action_set:
            raise ValueError("approvalRequest requires approval_required action")
        if self.approval_required != ("approval_required" in action_set):
            raise ValueError("approvalRequired must match approval_required action metadata")
        if self.fail_open == self.fail_closed:
            raise ValueError("exactly one of failOpen or failClosed must be true")
        return self


class VerifierMetadata(VerifierBusModel):
    verifier_id: str = Field(alias="verifierId")
    stage: VerifierStage
    phase: VerifierPhase
    priority: int
    description: str = ""
    input_declarations: tuple[VerifierInputDeclaration, ...] = Field(
        default=(),
        alias="inputDeclarations",
    )
    failure_routing: FailureRoutingMetadata = Field(
        default_factory=FailureRoutingMetadata,
        alias="failureRouting",
    )
    hard_safety: bool = Field(default=False, alias="hardSafety")
    security_critical: bool = Field(default=False, alias="securityCritical")
    blocking: bool = False
    fail_open: bool = Field(default=False, alias="failOpen")
    fail_closed: bool = Field(default=True, alias="failClosed")
    opt_out: bool = Field(default=True, alias="optOut")
    default_enabled: bool = Field(default=False, alias="defaultEnabled")
    disabled: bool = True
    metadata_only: Literal[True] = Field(default=True, alias="metadataOnly")
    extraction_attached: Literal[False] = Field(default=False, alias="extractionAttached")
    traffic_attached: Literal[False] = Field(default=False, alias="trafficAttached")
    execution_attached: Literal[False] = Field(default=False, alias="executionAttached")
    runner_attached: Literal[False] = Field(default=False, alias="runnerAttached")
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")
    canary_attached: Literal[False] = Field(default=False, alias="canaryAttached")

    @model_validator(mode="before")
    @classmethod
    def _apply_hard_safety_defaults(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        hard_safety = data.get("hardSafety", data.get("hard_safety", False))
        security_critical = data.get("securityCritical", data.get("security_critical", False))
        if hard_safety is True or security_critical is True:
            _set_default_alias_aware(data, "hard_safety", "hardSafety", True)
            _set_default_alias_aware(data, "security_critical", "securityCritical", True)
            data.setdefault("blocking", True)
            _set_default_alias_aware(data, "fail_open", "failOpen", False)
            _set_default_alias_aware(data, "fail_closed", "failClosed", True)
            _set_default_alias_aware(data, "opt_out", "optOut", False)
            _set_default_alias_aware(data, "default_enabled", "defaultEnabled", True)
            data.setdefault("disabled", False)
        else:
            has_fail_open = "failOpen" in data or "fail_open" in data
            has_fail_closed = "failClosed" in data or "fail_closed" in data
            if has_fail_open and not has_fail_closed:
                value_to_invert = bool(data.get("failOpen", data.get("fail_open")))
                if "fail_open" in data:
                    data["fail_closed"] = not value_to_invert
                else:
                    data["failClosed"] = not value_to_invert
            if has_fail_closed and not has_fail_open:
                value_to_invert = bool(data.get("failClosed", data.get("fail_closed")))
                if "fail_closed" in data:
                    data["fail_open"] = not value_to_invert
                else:
                    data["failOpen"] = not value_to_invert
        return data

    @field_validator("verifier_id")
    @classmethod
    def _reject_empty_verifier_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("verifierId must be non-empty")
        return value

    @field_validator("description")
    @classmethod
    def _sanitize_description(cls, value: str) -> str:
        if not value.strip() and value != "":
            raise ValueError("description must be non-empty when provided")
        return value

    @field_validator("input_declarations")
    @classmethod
    def _revalidate_inputs(
        cls,
        value: tuple[VerifierInputDeclaration, ...],
    ) -> tuple[VerifierInputDeclaration, ...]:
        return tuple(_revalidate_nested_model(item, VerifierInputDeclaration) for item in value)

    @field_validator("failure_routing")
    @classmethod
    def _revalidate_failure_routing(
        cls,
        value: FailureRoutingMetadata,
    ) -> FailureRoutingMetadata:
        return _revalidate_nested_model(value, FailureRoutingMetadata)

    @model_validator(mode="after")
    def _validate_verifier(self) -> Self:
        expected_phase = "semantic_critic" if self.stage == "llm_critic" else "deterministic"
        if self.phase != expected_phase:
            raise ValueError("verifier phase must match its stage")
        if self.priority < 1:
            raise ValueError("priority must be positive")
        if (
            not self.hard_safety
            and "fail_open" not in self.model_fields_set
            and "fail_closed" not in self.model_fields_set
        ):
            object.__setattr__(self, "fail_open", self.failure_routing.fail_open)
            object.__setattr__(self, "fail_closed", self.failure_routing.fail_closed)
        if self.fail_open == self.fail_closed:
            raise ValueError("exactly one of failOpen or failClosed must be true")
        if self.hard_safety or self.security_critical:
            if not self.hard_safety or not self.security_critical:
                raise ValueError("hard-safety verifiers must also be security-critical")
            if not self.blocking:
                raise ValueError("hard-safety verifiers must be blocking")
            if self.fail_open or not self.fail_closed:
                raise ValueError("hard-safety verifiers must fail closed")
            if self.opt_out:
                raise ValueError("hard-safety verifiers cannot be opt-out")
            if self.disabled:
                raise ValueError("hard-safety verifiers cannot be disabled")
            if not self.default_enabled:
                raise ValueError("hard-safety verifiers must be default enabled")
        return self


class VerifierResultMetadata(VerifierBusModel):
    verifier_id: str = Field(alias="verifierId")
    status: VerifierStatus
    public_summary: str | None = Field(default=None, alias="publicSummary")
    retry_message: str | None = Field(default=None, alias="retryMessage")
    failure_message: str | None = Field(default=None, alias="failureMessage")
    approval_request: ApprovalRequestMetadata | None = Field(default=None, alias="approvalRequest")
    metadata_only: Literal[True] = Field(default=True, alias="metadataOnly")
    traffic_attached: Literal[False] = Field(default=False, alias="trafficAttached")
    execution_attached: Literal[False] = Field(default=False, alias="executionAttached")

    @field_validator("verifier_id")
    @classmethod
    def _reject_empty_verifier_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("verifierId must be non-empty")
        return value

    @field_validator("public_summary", "retry_message", "failure_message")
    @classmethod
    def _sanitize_public_messages(cls, value: str | None) -> str | None:
        return _sanitize_public_text(value)

    @field_validator("approval_request")
    @classmethod
    def _revalidate_approval_request(
        cls,
        value: ApprovalRequestMetadata | None,
    ) -> ApprovalRequestMetadata | None:
        if value is None:
            return None
        return _revalidate_nested_model(value, ApprovalRequestMetadata)


class VerifierBusMetadata(VerifierBusModel):
    stages: tuple[VerifierStageMetadata, ...]
    verifiers: tuple[VerifierMetadata, ...]
    metadata_only: Literal[True] = Field(default=True, alias="metadataOnly")
    traffic_attached: Literal[False] = Field(default=False, alias="trafficAttached")
    execution_attached: Literal[False] = Field(default=False, alias="executionAttached")
    runner_attached: Literal[False] = Field(default=False, alias="runnerAttached")
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")
    canary_attached: Literal[False] = Field(default=False, alias="canaryAttached")

    @field_validator("stages")
    @classmethod
    def _revalidate_stages(
        cls,
        value: tuple[VerifierStageMetadata, ...],
    ) -> tuple[VerifierStageMetadata, ...]:
        return tuple(_revalidate_nested_model(item, VerifierStageMetadata) for item in value)

    @field_validator("verifiers")
    @classmethod
    def _revalidate_verifiers(
        cls,
        value: tuple[VerifierMetadata, ...],
    ) -> tuple[VerifierMetadata, ...]:
        return tuple(_revalidate_nested_model(item, VerifierMetadata) for item in value)

    @model_validator(mode="after")
    def _validate_bus(self) -> Self:
        if tuple(stage.stage for stage in self.stages) != _STAGE_ORDER:
            raise ValueError("verifier bus stages must use deterministic-to-semantic order")
        if len({verifier.verifier_id for verifier in self.verifiers}) != len(self.verifiers):
            raise ValueError("verifierIds must be unique")
        valid_stages = {stage.stage for stage in self.stages}
        if any(verifier.stage not in valid_stages for verifier in self.verifiers):
            raise ValueError("verifiers must reference declared stages")
        by_id = {verifier.verifier_id: verifier for verifier in self.verifiers}
        hard_safety = by_id.get(_PROTECTED_HARD_SAFETY_VERIFIER_ID)
        if hard_safety is None:
            raise ValueError("protected hard-safety verifier must remain present")
        if (
            hard_safety.stage != "security_policy"
            or hard_safety.phase != "deterministic"
            or hard_safety.priority != _PROTECTED_HARD_SAFETY_DEFAULT_PRIORITY
            or not hard_safety.hard_safety
            or not hard_safety.security_critical
            or not hard_safety.blocking
            or hard_safety.fail_open
            or not hard_safety.fail_closed
            or hard_safety.opt_out
            or not hard_safety.default_enabled
            or hard_safety.disabled
        ):
            raise ValueError("protected hard-safety verifier cannot be downgraded")
        routing = hard_safety.failure_routing
        if (
            "terminal" not in routing.actions
            or "block_final_answer" not in routing.actions
            or not routing.terminal
            or not routing.block_final_answer
            or routing.fail_open
            or not routing.fail_closed
        ):
            raise ValueError("protected hard-safety verifier must block terminal answers fail-closed")
        return self

    def effective_verifiers(
        self,
        *,
        deterministic_prerequisites_satisfied: bool,
        escalationReason: CriticEscalationReason | None = None,
        escalation_reason: CriticEscalationReason | None = None,
    ) -> tuple[VerifierMetadata, ...]:
        reason = _resolve_escalation_reason(
            escalationReason=escalationReason,
            escalation_reason=escalation_reason,
        )
        effective: list[VerifierMetadata] = []
        for verifier in self.verifiers:
            if verifier.stage == "llm_critic":
                if reason is None and (
                    not deterministic_prerequisites_satisfied or verifier.disabled
                ):
                    continue
            elif verifier.disabled:
                continue
            effective.append(verifier)
        return tuple(
            sorted(
                effective,
                key=lambda verifier: (_STAGE_RANK[verifier.stage], verifier.priority, verifier.verifier_id),
            )
        )


def build_default_verifier_bus_metadata() -> VerifierBusMetadata:
    return VerifierBusMetadata(
        stages=(
            VerifierStageMetadata(
                stage="schema_structured_output",
                order=1,
                phase="deterministic",
                description="schema and structured-output checks",
            ),
            VerifierStageMetadata(
                stage="tool_evidence_contract",
                order=2,
                phase="deterministic",
                description="tool and evidence contract checks",
            ),
            VerifierStageMetadata(
                stage="file_artifact_delivery",
                order=3,
                phase="deterministic",
                description="file, artifact, and delivery checks",
            ),
            VerifierStageMetadata(
                stage="source_claim_link",
                order=4,
                phase="deterministic",
                description="source and claim-link checks",
            ),
            VerifierStageMetadata(
                stage="task_plan_completion",
                order=5,
                phase="deterministic",
                description="task and plan completion checks",
            ),
            VerifierStageMetadata(
                stage="security_policy",
                order=6,
                phase="deterministic",
                description="security and policy checks",
            ),
            VerifierStageMetadata(
                stage="llm_critic",
                order=7,
                phase="semantic_critic",
                description="fuzzy quality, ambiguity, reasoning, and synthesis checks",
                deterministicPrerequisite=False,
            ),
        ),
        verifiers=(
            VerifierMetadata(
                verifierId="schema-structured-output",
                stage="schema_structured_output",
                phase="deterministic",
                priority=10,
                description="Validate structured output metadata shape.",
                inputDeclarations=(
                    VerifierInputDeclaration(evidenceTypes=("DeterministicEvidenceVerifier",)),
                ),
                failureRouting=FailureRoutingMetadata(actions=("audit",), failOpen=True),
                defaultEnabled=False,
                disabled=True,
            ),
            VerifierMetadata(
                verifierId="tool-evidence-contract",
                stage="tool_evidence_contract",
                phase="deterministic",
                priority=20,
                description="Validate required tool evidence contract metadata.",
                inputDeclarations=(
                    VerifierInputDeclaration(
                        evidenceTypes=("DeterministicEvidenceVerifier", "TestRun"),
                        ledgerRefs=("ledger:tool-evidence-contract",),
                    ),
                ),
                failureRouting=FailureRoutingMetadata(
                    actions=("audit", "retry"),
                    retryable=True,
                    failOpen=True,
                ),
                defaultEnabled=False,
                disabled=True,
            ),
            VerifierMetadata(
                verifierId="dev-coding-verification-audit",
                stage="tool_evidence_contract",
                phase="deterministic",
                priority=21,
                description="Audit-only coding verification evidence metadata.",
                inputDeclarations=(
                    VerifierInputDeclaration(
                        evidenceTypes=(
                            "GitDiff",
                            "TestRun",
                            "CodeDiagnostics",
                            "CommitCheckpoint",
                            "DeterministicEvidenceVerifier",
                        ),
                        ledgerRefs=("ledger:dev-coding-verification-audit",),
                    ),
                ),
                failureRouting=FailureRoutingMetadata(actions=("audit",), failOpen=True),
                defaultEnabled=False,
                disabled=True,
            ),
            VerifierMetadata(
                verifierId="artifact-delivery",
                stage="file_artifact_delivery",
                phase="deterministic",
                priority=30,
                description="Validate file, artifact, and delivery evidence metadata.",
                inputDeclarations=(
                    VerifierInputDeclaration(
                        evidenceTypes=("FileDeliver", "ArtifactVerify", "TelegramDeliveryAck"),
                        artifactRefs=("artifact:declared-output",),
                    ),
                ),
                failureRouting=FailureRoutingMetadata(
                    actions=("audit", "retry"),
                    retryable=True,
                    failOpen=True,
                ),
                defaultEnabled=False,
                disabled=True,
            ),
            VerifierMetadata(
                verifierId="source-claim-link",
                stage="source_claim_link",
                phase="deterministic",
                priority=40,
                description="Validate source and claim-link evidence metadata.",
                inputDeclarations=(
                    VerifierInputDeclaration(
                        evidenceTypes=("WebSearch", "KnowledgeSearch", "SourceInspection"),
                    ),
                ),
                failureRouting=FailureRoutingMetadata(actions=("audit",), failOpen=True),
                defaultEnabled=False,
                disabled=True,
            ),
            VerifierMetadata(
                verifierId="task-plan-completion",
                stage="task_plan_completion",
                phase="deterministic",
                priority=50,
                description="Validate task and plan completion evidence metadata.",
                inputDeclarations=(
                    VerifierInputDeclaration(
                        evidenceTypes=("PlanVerifier", "GitDiff", "TestRun", "CommitCheckpoint"),
                        transcriptRefs=("transcript:plan-progress",),
                    ),
                ),
                failureRouting=FailureRoutingMetadata(actions=("audit",), failOpen=True),
                defaultEnabled=False,
                disabled=True,
            ),
            VerifierMetadata(
                verifierId="security-policy-hard-safety",
                stage="security_policy",
                phase="deterministic",
                priority=60,
                description="Fail-closed security and policy metadata gate.",
                inputDeclarations=(
                    VerifierInputDeclaration(
                        evidenceTypes=("DeterministicEvidenceVerifier",),
                        controlRefs=("control:policy-state",),
                    ),
                ),
                failureRouting=FailureRoutingMetadata(
                    actions=("audit", "terminal", "block_final_answer"),
                    terminal=True,
                    blockFinalAnswer=True,
                    failClosed=True,
                ),
                hardSafety=True,
                securityCritical=True,
            ),
            VerifierMetadata(
                verifierId="llm-critic-fuzzy-quality",
                stage="llm_critic",
                phase="semantic_critic",
                priority=70,
                description="Escalation-only semantic critic metadata.",
                inputDeclarations=(
                    VerifierInputDeclaration(
                        transcriptRefs=("transcript:final-candidate",),
                        sessionRefs=("session:active",),
                    ),
                ),
                failureRouting=FailureRoutingMetadata(actions=("audit",), failOpen=True),
                defaultEnabled=False,
                disabled=True,
            ),
        ),
    )


__all__ = [
    "ApprovalRequestMetadata",
    "FailureRoutingMetadata",
    "VerifierBusMetadata",
    "VerifierInputDeclaration",
    "VerifierMetadata",
    "VerifierResultMetadata",
    "VerifierStageMetadata",
    "build_default_verifier_bus_metadata",
]
