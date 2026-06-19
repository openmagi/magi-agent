from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal, Self, TypeVar, get_args, get_origin

from pydantic import Field, field_validator, model_validator

from magi_agent.evidence.ledger import _redact_public_summary_text
from magi_agent.evidence.types import (
    EvidenceFieldMatcher,
    EvidenceMetadataModel,
    validate_evidence_type_name,
)


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
_PUBLIC_REF_PREFIXES = ("evidence:", "verifier:", "receipt:sha256:", "sha256:")


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
            # Task C — OPTIONAL BLOCKING document-authoring coverage gate.
            # Default-OFF (``defaultEnabled=False``/``disabled=True``); activated
            # only when the ``document-authoring-coverage`` preset is enabled via
            # ``MAGI_DOCUMENT_AUTHORING_COVERAGE``. Consumes ``DocumentCoverage``
            # evidence (Task B) and, when active, blocks the final answer/commit on
            # a failed-coverage record. The block keys off the record's
            # ``fields["status"]`` (NOT the top-level EvidenceRecord.status, which
            # follows the tool status of ``"ok"`` through the production
            # ``evidence_from_tool_result`` path) via an ``EvidenceFieldMatcher``
            # equals "pass" — see ``execute_pre_final_verifier_bus``. fail_open so a
            # missing/erroring boundary never wedges a turn; absence of any
            # DocumentCoverage record ⇒ pass (non-document turns never block).
            VerifierMetadata(
                verifierId="document-authoring-coverage",
                stage="file_artifact_delivery",
                phase="deterministic",
                priority=31,
                description="Document-authoring source-content coverage gate (default-off).",
                inputDeclarations=(
                    VerifierInputDeclaration(
                        evidenceTypes=("DocumentCoverage",),
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
            # Track 19 PR3 — General-Automation deliverable-completion check.
            # Deterministic, default-OFF; the live consumer lives in
            # ``harness/general_automation/task_completion`` and routes a missing
            # deliverable to a turn-loop *repair* (not terminal). This metadata
            # entry stays declaration-only (``actions=("audit","retry")``); the
            # "repair" routing is a turn-loop concept, not a metadata action.
            VerifierMetadata(
                verifierId="ga-task-completion",
                stage="task_plan_completion",
                phase="deterministic",
                priority=51,
                description="General-Automation deliverable-completion metadata (default-off).",
                inputDeclarations=(
                    VerifierInputDeclaration(
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
            # Placed in task_plan_completion for catalog compatibility only; this
            # is a learning-layer regression check with no task/plan semantics.
            # Move to a dedicated memory/learning stage if one is added.
            VerifierMetadata(
                verifierId="learning-eval",
                stage="task_plan_completion",
                phase="deterministic",
                priority=55,
                description="Learning-layer eval-gate regression metadata (default-off).",
                inputDeclarations=(
                    VerifierInputDeclaration(
                        transcriptRefs=("transcript:learning-reflection",),
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


VERIFIER_ENTRY_POINT_GROUP = "magi.verifiers"
MAGI_KERNEL_VERIFIER_ENTRY_POINTS_ENABLED_ENV = "MAGI_KERNEL_VERIFIER_ENTRY_POINTS_ENABLED"


def with_additional_verifiers(
    base: VerifierBusMetadata,
    externals: Sequence[VerifierMetadata],
) -> VerifierBusMetadata:
    """Return ``base`` extended with ``externals`` — **tighten-only**.

    External verifiers may only ADD checks. This helper drops any external
    manifest that would remove, replace, or weaken an existing verifier:

    * V1 — the protected hard-safety id (``security-policy-hard-safety``) can
      never be overwritten;
    * V4 — any external reusing an EXISTING verifier id is dropped, never
      replacing it (replacing would weaken / alter a first-party gate);
    * V5 — any external whose priority invades the hard-safety band
      (``priority <= 60``) is dropped;
    * any external asserting hard-safety / security-critical authority is dropped
      (those gates are first-party-only).

    Duplicate external ids keep only the first. The result is rebuilt through the
    :class:`VerifierBusMetadata` constructor so ``_validate_bus`` re-runs — the
    protected hard-safety verifier and stage order are re-asserted, so a merge
    can never produce a downgraded or malformed bus.

    With ``externals`` empty (the default-OFF path) the returned bus is
    byte-identical to ``base``.
    """

    existing_ids = {verifier.verifier_id for verifier in base.verifiers}
    accepted: list[VerifierMetadata] = []
    seen_external: set[str] = set()
    for verifier in externals:
        verifier_id = verifier.verifier_id
        if verifier_id in existing_ids or verifier_id in seen_external:
            continue
        if verifier.hard_safety or verifier.security_critical:
            continue
        if verifier.priority <= _PROTECTED_HARD_SAFETY_DEFAULT_PRIORITY:
            continue
        seen_external.add(verifier_id)
        accepted.append(verifier)

    if not accepted:
        return base
    return VerifierBusMetadata(
        stages=base.stages,
        verifiers=tuple(base.verifiers) + tuple(accepted),
    )


def _coerce_verifier_payload(value: Any) -> dict | None:
    """Coerce an ``entry_points`` payload to a verifier-manifest dict, or ``None``.

    Mirrors :func:`magi_agent.recipes.kernel_recipe_packs._coerce_entry_point_payload`:
    only inert DATA shapes (dict / Pydantic-style ``model_dump``) are accepted;
    callable / code-carrying payloads are dropped so a published plugin cannot
    smuggle a tool/control invocation through the verifier surface.
    """

    if value is None or callable(value):
        return None
    if isinstance(value, dict):
        return value
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            dumped = model_dump(by_alias=True)
        except TypeError:
            dumped = model_dump()
        return dumped if isinstance(dumped, dict) else None
    return None


def _discover_entry_point_verifiers(*, group: str) -> list[VerifierMetadata]:
    """Discover external verifier manifests via Python ``entry_points``.

    Self-host opt-in: ``EntryPoint.load()`` imports the publisher's module (the
    standard distribution-tool trust model — like pytest plugins). Hosted floor
    must keep the gate OFF. Each per-entry failure (load error, validation error,
    callable payload) is dropped; the whole boundary is fail-closed so the rest
    of the discovery never halts.
    """

    import logging
    from importlib import metadata as importlib_metadata

    log = logging.getLogger(__name__)
    discovered: list[VerifierMetadata] = []
    try:
        entry_points = tuple(importlib_metadata.entry_points(group=group))
    except Exception:  # noqa: BLE001 - importlib_metadata absent/broken → empty
        return discovered
    for ep in entry_points:
        loader = getattr(ep, "load", None)
        if not callable(loader):
            continue
        try:
            value = loader()
        except Exception:  # noqa: BLE001 - a broken publisher never poisons others
            log.warning("verifier entry_point %r failed to load", getattr(ep, "name", "?"))
            continue
        # Allow a publisher to ship a SEQUENCE of manifests in one entry point.
        items = value if isinstance(value, (list, tuple)) else (value,)
        for item in items:
            payload = _coerce_verifier_payload(item)
            if payload is None:
                log.warning(
                    "verifier entry_point %r skipped (non-data payload)",
                    getattr(ep, "name", "?"),
                )
                continue
            try:
                verifier = VerifierMetadata.model_validate(payload)
            except Exception:  # noqa: BLE001 - a malformed manifest drops, never raises
                log.warning(
                    "verifier entry_point %r dropped (invalid manifest)",
                    getattr(ep, "name", "?"),
                )
                continue
            discovered.append(verifier)
    return discovered


def build_runtime_verifier_bus_metadata(
    env: Mapping[str, str] | None = None,
) -> VerifierBusMetadata:
    """Return the default verifier bus tightened with discovered externals.

    Starts from :func:`build_default_verifier_bus_metadata` and merges any
    external verifier manifests discovered via Python ``entry_points`` group
    ``magi.verifiers`` through the tighten-only :func:`with_additional_verifiers`
    helper.

    With ``MAGI_KERNEL_VERIFIER_ENTRY_POINTS_ENABLED`` OFF (default), no discovery
    is attempted so the bus is byte-identical to
    :func:`build_default_verifier_bus_metadata`. Discovery is fail-closed: a bad
    publisher never raises and the result always at least carries the full
    default set.
    """

    from magi_agent.config.flags import flag_bool  # noqa: PLC0415

    base = build_default_verifier_bus_metadata()
    if not flag_bool(MAGI_KERNEL_VERIFIER_ENTRY_POINTS_ENABLED_ENV, env=env):
        return base
    try:
        externals = _discover_entry_point_verifiers(group=VERIFIER_ENTRY_POINT_GROUP)
    except Exception:  # noqa: BLE001 - fail-closed-to-first-party
        return base
    return with_additional_verifiers(base, externals)


def execute_pre_final_verifier_bus(
    *,
    required_evidence: Sequence[str],
    required_validators: Sequence[str],
    observed_public_refs: Sequence[str],
    evidence_records: Sequence[object],
    document_coverage_gate_enabled: bool = False,
    shacl_gate_enabled: bool = False,
    dashboard_gate_enabled: bool = False,
) -> dict[str, object]:
    """Run the deterministic pre-final evidence verifier over public refs.

    This is the live execution half of the pre-final verifier bus for the local
    runner. It performs a pure read over already-public refs and already-collected
    evidence records; it does not call models, external tools, storage, child
    agents, or production services.

    ``document_coverage_gate_enabled`` (Task C, default OFF) activates the
    ``document-authoring-coverage`` gate. When OFF this function is behavior-identical
    to before: ``DocumentCoverage`` evidence (Task B) stays audit-only and never
    affects the decision. When ON, a ``DocumentCoverage`` record whose
    ``fields["status"] != "pass"`` (failed coverage) flips the decision to
    ``"block"`` with a ``document-authoring-coverage`` result. The match keys off
    the record's ``fields["status"]`` — NOT the top-level ``EvidenceRecord.status``
    (which follows the tool status of ``"ok"`` through the production
    ``evidence_from_tool_result`` path) — via an ``EvidenceFieldMatcher``. Absence
    of any ``DocumentCoverage`` record ⇒ pass, so a non-document turn is never
    blocked by this gate.

    ``shacl_gate_enabled`` (Task 1.3 / PR1, default OFF) activates the SHACL
    consume-side gate.  When OFF this function is behavior-identical to before:
    ``custom:ShaclConstraintCheck`` evidence stays audit-only and never affects
    the decision.  When ON, any ``custom:ShaclConstraintCheck`` record whose
    top-level ``EvidenceRecord.status == "failed"`` (a constraint violation) flips
    the decision to ``"block"`` with a ``shacl-constraint-verifier`` result.
    Unlike the document-coverage gate, the match keys off the **top-level**
    ``EvidenceRecord.status`` because the SHACL producer (PR1 Task 1.2) sets
    ``status="failed"`` directly on the record for a violation — NOT via
    ``fields["status"]``.  Records with ``status="unknown"`` (fail-safe: shape
    parse/pyshacl exception) and ``status="ok"`` are never blocked.  Absence of
    any ``custom:ShaclConstraintCheck`` record ⇒ no effect, so a non-SHACL turn
    is never blocked by this gate.
    """

    matched_refs = set(_valid_public_refs(observed_public_refs))
    for record in evidence_records:
        _collect_public_refs(record, matched_refs)

    missing_evidence = [
        ref for ref in _valid_public_refs(required_evidence) if ref not in matched_refs
    ]
    missing_validators = [
        ref for ref in _valid_public_refs(required_validators) if ref not in matched_refs
    ]

    failed_coverage = (
        _failed_document_coverage_records(evidence_records)
        if document_coverage_gate_enabled
        else ()
    )

    failed_shacl = (
        _failed_shacl_records(evidence_records)
        if shacl_gate_enabled
        else ()
    )

    failed_dashboard = (
        _failed_dashboard_records(evidence_records)
        if dashboard_gate_enabled
        else ()
    )

    decision = (
        "block"
        if (
            missing_evidence
            or missing_validators
            or failed_coverage
            or failed_shacl
            or failed_dashboard
        )
        else "pass"
    )

    results: list[dict[str, object]] = []
    if missing_evidence:
        results.append(
            _live_verifier_result(
                verifier_id="tool-evidence-contract",
                status="missing",
                public_summary="missing required deterministic evidence",
                retry_message="collect required evidence before final answer",
            )
        )
    if missing_validators:
        results.append(
            _live_verifier_result(
                verifier_id="dev-coding-verification-audit",
                status="missing",
                public_summary="missing required validator evidence",
                retry_message="run required validation before final answer",
            )
        )
    if failed_coverage:
        results.append(
            _live_verifier_result(
                verifier_id="document-authoring-coverage",
                status="failed",
                public_summary="document coverage failed",
                retry_message="regenerate the document to cover the missing source content",
            )
        )
    if failed_shacl:
        # Surface the rule IDs from the failed records so operators know which
        # constraint(s) fired.  Each ShaclConstraintCheck record carries
        # fields["ruleId"] set by the producer (Task 1.2).
        rule_ids = ", ".join(
            str(getattr(r, "fields", {}).get("ruleId") or "unknown")
            if not isinstance(r, Mapping)
            else str((r.get("fields") or {}).get("ruleId") or "unknown")
            for r in failed_shacl
        )
        results.append(
            _live_verifier_result(
                verifier_id="shacl-constraint-verifier",
                status="failed",
                public_summary=f"SHACL constraint violation: {rule_ids}",
                retry_message=(
                    f"address the SHACL constraint failure(s) before final answer: {rule_ids}"
                ),
            )
        )
    if failed_dashboard:
        # Surface the rule IDs from the failed records so operators know which
        # dashboard check(s) fired.  Each DashboardCheck record carries
        # fields["ruleId"] set by the producer (DashboardProducerControl).
        dashboard_rule_ids = ", ".join(
            _dashboard_rule_id(r) for r in failed_dashboard
        )
        results.append(
            _live_verifier_result(
                verifier_id="dashboard-custom-check",
                status="failed",
                public_summary=f"dashboard custom check violation: {dashboard_rule_ids}",
                retry_message=(
                    "address the dashboard custom check failure(s) before final "
                    f"answer: {dashboard_rule_ids}"
                ),
            )
        )
    if not results:
        results.append(
            _live_verifier_result(
                verifier_id="pre-final-evidence-gate",
                status="pass",
                public_summary="pre-final evidence gate passed",
            )
        )

    return {
        "metadataOnly": False,
        "decision": decision,
        "results": results,
        "trafficAttached": False,
        "executionAttached": True,
        "evidenceRecordCount": len(evidence_records),
        "matchedRefs": sorted(matched_refs),
        "missingEvidence": missing_evidence,
        "missingValidators": missing_validators,
        "failedDocumentCoverage": len(failed_coverage),
        "failedShaclConstraints": len(failed_shacl),
        "failedDashboardChecks": len(failed_dashboard),
    }


def _live_verifier_result(
    *,
    verifier_id: str,
    status: VerifierStatus,
    public_summary: str | None = None,
    retry_message: str | None = None,
) -> dict[str, object]:
    payload = VerifierResultMetadata(
        verifierId=verifier_id,
        status=status,
        publicSummary=public_summary,
        retryMessage=retry_message,
    ).model_dump(by_alias=True, mode="json", warnings=False)
    # VerifierResultMetadata is intentionally metadata-only; this live local
    # runner payload records execution at the envelope/result projection layer
    # without mutating that protected metadata model.
    payload["metadataOnly"] = False
    payload["executionAttached"] = True
    payload["trafficAttached"] = False
    return payload


_DOCUMENT_COVERAGE_EVIDENCE_TYPE = "DocumentCoverage"
# A coverage record passes only when its coverage verdict field equals "pass".
# This is built once and reused; the matcher is the canonical field-matching
# primitive so the gate honors the Task B fields["status"] contract rather than
# the top-level EvidenceRecord.status (which follows the "ok" tool status).
_DOCUMENT_COVERAGE_PASS_MATCHER = EvidenceFieldMatcher(equals="pass")


def _failed_document_coverage_records(
    evidence_records: Sequence[object],
) -> tuple[object, ...]:
    """Return DocumentCoverage records whose ``fields["status"]`` is not "pass".

    Deterministic and never raises: a record that is not a DocumentCoverage
    record (or that lacks a readable ``fields["status"]``) is ignored, so a
    non-document turn yields an empty tuple and never blocks. Field-level
    matching is performed with the canonical :class:`EvidenceFieldMatcher`
    (``equals="pass"``) over the record's ``fields`` mapping.
    """
    failed: list[object] = []
    for record in evidence_records:
        evidence_type, fields = _document_coverage_view(record)
        if evidence_type != _DOCUMENT_COVERAGE_EVIDENCE_TYPE:
            continue
        # Absent status key ⇒ pass (fail_open contract: only a present
        # fields["status"] != "pass" blocks).
        if "status" not in fields:
            continue
        # fields["status"] != "pass" (failed coverage). _document_field_fails_matcher
        # returns True when the field does not satisfy the matcher.
        if _document_field_fails_matcher(fields, "status", _DOCUMENT_COVERAGE_PASS_MATCHER):
            failed.append(record)
    return tuple(failed)


_SHACL_EVIDENCE_TYPE = "custom:ShaclConstraintCheck"


def _failed_shacl_records(
    evidence_records: Sequence[object],
) -> tuple[object, ...]:
    """Return ShaclConstraintCheck records whose top-level ``status`` is "failed".

    NOTE: Unlike ``_failed_document_coverage_records``, which keys off
    ``fields["status"]`` (because DocumentCoverage arrives via the
    ``evidence_from_tool_result`` path where top-level status follows the tool
    status), SHACL records key off the **top-level** ``EvidenceRecord.status``.
    The SHACL producer (Task 1.2) sets ``status="failed"`` directly on the record
    for a constraint violation.  ``status="unknown"`` (fail-safe: parse/pyshacl
    exception) and ``status="ok"`` (conforming) are never counted as failures —
    this preserves the global fail-safe contract: a broken shape can never cause
    a spurious block.

    Deterministic and never raises: a malformed/non-SHACL record is silently
    skipped so a non-SHACL turn yields an empty tuple and is never blocked.
    """
    failed: list[object] = []
    for record in evidence_records:
        evidence_type, _ = _document_coverage_view(record)
        if evidence_type != _SHACL_EVIDENCE_TYPE:
            continue
        # Read top-level status from the record.  Prefer attribute access (works
        # for EvidenceRecord Pydantic models); fall back to mapping key.
        if isinstance(record, Mapping):
            top_status = record.get("status")
        else:
            top_status = getattr(record, "status", None)
            if top_status is None:
                # Try model_dump path as a last resort (handles aliased fields).
                model_dump = getattr(record, "model_dump", None)
                if callable(model_dump):
                    try:
                        dumped = model_dump(by_alias=True, mode="python", warnings=False)
                        if isinstance(dumped, Mapping):
                            top_status = dumped.get("status")
                    except Exception:
                        pass
        # Only "failed" blocks; "unknown" (fail-safe) and "ok" are never blocking.
        if top_status == "failed":
            failed.append(record)
    return tuple(failed)


_DASHBOARD_EVIDENCE_TYPE = "custom:DashboardCheck"


def _dashboard_rule_id(record: object) -> str:
    """Return a DashboardCheck record's ``fields["ruleId"]`` (``"unknown"`` if absent).

    Mirrors the ``fields`` access style of ``_failed_dashboard_records``: handles
    both Mapping records (camel/snake corpora) and ``EvidenceRecord``-like objects
    exposing a ``fields`` attribute, and never raises.
    """
    if isinstance(record, Mapping):
        return str((record.get("fields") or {}).get("ruleId") or "unknown")
    return str(getattr(record, "fields", {}).get("ruleId") or "unknown")


def _failed_dashboard_records(
    evidence_records: Sequence[object],
) -> tuple[object, ...]:
    """Return DashboardCheck records whose top-level ``status`` is "failed".

    Mirrors ``_failed_shacl_records`` exactly: the DashboardProducerControl sets
    ``status="failed"`` directly on the record for a matched ``block`` check, so
    the gate keys off the **top-level** ``EvidenceRecord.status``. ``status="ok"``
    (a matched ``audit`` check) and ``status="unknown"`` are never counted as
    failures — this preserves the global fail-safe contract.

    Deterministic and never raises: a malformed/non-dashboard record is silently
    skipped so a non-dashboard turn yields an empty tuple and is never blocked.
    """
    failed: list[object] = []
    for record in evidence_records:
        evidence_type, _ = _document_coverage_view(record)
        if evidence_type != _DASHBOARD_EVIDENCE_TYPE:
            continue
        if isinstance(record, Mapping):
            top_status = record.get("status")
        else:
            top_status = getattr(record, "status", None)
            if top_status is None:
                model_dump = getattr(record, "model_dump", None)
                if callable(model_dump):
                    try:
                        dumped = model_dump(by_alias=True, mode="python", warnings=False)
                        if isinstance(dumped, Mapping):
                            top_status = dumped.get("status")
                    except Exception:
                        pass
        if top_status == "failed":
            failed.append(record)
    return tuple(failed)


def _document_coverage_view(record: object) -> tuple[object, Mapping[str, object]]:
    """Normalize an evidence record to ``(type, fields)`` without raising.

    Accepts ``EvidenceRecord``-like objects (with ``type``/``fields`` attrs), any
    model exposing ``model_dump``, or plain mappings (camel or snake keys).
    Returns ``(None, {})`` for anything that is not a readable evidence record.

    Total/never-raising: any exception from ``model_dump`` (including
    ``ValueError``, ``AttributeError``, ``RuntimeError``, etc.) causes
    ``dumped`` to be set to ``None``, which then falls through to the
    attribute-read path so the gate is never wedged by a malformed record.
    When model_dump raises, we do NOT fall through to attribute reads (which
    could accidentally surface class-level attributes as coverage data) —
    instead we treat it as no-readable-record and return ``(None, {})``.
    """
    data: Mapping[str, object] | None = None
    if isinstance(record, Mapping):
        data = record
    else:
        model_dump = getattr(record, "model_dump", None)
        if callable(model_dump):
            dumped: object = None
            try:
                dumped = model_dump(by_alias=True, mode="python", warnings=False)
            except Exception:
                # Any exception (TypeError, ValueError, AttributeError,
                # RuntimeError, …) from model_dump is caught here.
                # We try the no-kwargs form as a last resort, but if that
                # also fails (or yields a non-Mapping), we return no-record
                # rather than falling through to attribute reads — that path
                # could pick up class-level attributes that were never meant
                # as serialized field data.
                try:
                    dumped = model_dump()
                except Exception:
                    return None, {}
            if isinstance(dumped, Mapping):
                data = dumped
            elif dumped is None:
                # model_dump() returned None (or both forms failed gracefully)
                return None, {}
        if data is None:
            evidence_type = getattr(record, "type", None)
            raw_fields = getattr(record, "fields", None)
            fields = raw_fields if isinstance(raw_fields, Mapping) else {}
            return evidence_type, fields

    evidence_type = data.get("type")
    raw_fields = data.get("fields")
    fields = raw_fields if isinstance(raw_fields, Mapping) else {}
    return evidence_type, fields


def _document_field_fails_matcher(
    fields: Mapping[str, object],
    field_name: str,
    matcher: EvidenceFieldMatcher,
) -> bool:
    """Return True iff ``fields[field_name]`` does NOT satisfy ``matcher``.

    Named to make the "fails" sense explicit at call sites: a True return means
    the field did not pass the matcher (i.e. the record should be counted as
    failed coverage). Reuses the canonical evidence-contract field matcher so
    the gate's field-level semantics are identical to ``EvidenceContractEngine``.
    Imported lazily to keep this module ADK/runtime/route-import-free at module
    load.
    """
    # Intentional cross-module reuse of the canonical field matcher from
    # evidence/contracts.py — keeps field-matching semantics in one place.
    # If _match_field is ever renamed, grep for this import site.
    from magi_agent.evidence.contracts import _match_field

    return _match_field(fields, field_name, matcher) is not None


def _valid_public_refs(values: Sequence[str]) -> tuple[str, ...]:
    refs: list[str] = []
    for value in values:
        if isinstance(value, str) and value.startswith(_PUBLIC_REF_PREFIXES):
            refs.append(value)
    return tuple(dict.fromkeys(refs))


def _collect_public_refs(value: object, refs: set[str], depth: int = 0) -> None:
    if depth > 8:
        return
    if isinstance(value, str):
        if value.startswith(_PUBLIC_REF_PREFIXES):
            refs.add(value)
        return
    if isinstance(value, Mapping):
        for nested in value.values():
            _collect_public_refs(nested, refs, depth + 1)
        return
    if isinstance(value, list | tuple | set | frozenset):
        for nested in value:
            _collect_public_refs(nested, refs, depth + 1)
        return

    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            dumped = model_dump(by_alias=True, mode="python", warnings=False)
        except Exception:
            try:
                dumped = model_dump()
            except Exception:
                return
        _collect_public_refs(dumped, refs, depth + 1)
        return

    for attr in ("evidence_ref", "evidenceRef", "payload", "metadata"):
        if hasattr(value, attr):
            _collect_public_refs(getattr(value, attr), refs, depth + 1)


__all__ = [
    "ApprovalRequestMetadata",
    "FailureRoutingMetadata",
    "VerifierBusMetadata",
    "VerifierInputDeclaration",
    "VerifierMetadata",
    "VerifierResultMetadata",
    "VerifierStageMetadata",
    "build_default_verifier_bus_metadata",
    "execute_pre_final_verifier_bus",
]
