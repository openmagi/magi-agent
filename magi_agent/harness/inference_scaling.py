from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Literal, Self

from pydantic import BaseModel, Field, field_serializer, field_validator, model_validator

from magi_agent.ops.authority import FalseOnlyAuthorityModel


AgentRole = Literal["general", "coding", "research"]
RunOn = Literal["main", "child"]
RiskLevel = Literal["low", "medium", "high"]
TaskKind = Literal[
    "simple_arithmetic",
    "source_sensitive_research",
    "coding_change",
    "ambiguous_architecture",
    "verifier_retry",
    "complex_synthesis",
    "general",
]
SideEffectClass = Literal["none", "read_only", "local_mutation", "external"]
EscalationKind = Literal["planner", "critic", "model"]

ATTACHMENT_FLAGS: tuple[str, ...] = (
    "trafficAttached",
    "executionAttached",
    "runnerAttached",
    "routeAttached",
    "modelRoutingAttached",
    "billingAttached",
    "authAttached",
    "apiProxyAttached",
    "canaryAttached",
)
_ATTACHMENT_FIELD_NAMES: tuple[str, ...] = (
    "traffic_attached",
    "execution_attached",
    "runner_attached",
    "route_attached",
    "model_routing_attached",
    "billing_attached",
    "auth_attached",
    "api_proxy_attached",
    "canary_attached",
)
_HIDDEN_REASONING_KEYS = frozenset(
    (
        "hiddenReasoning",
        "hidden_reasoning",
        "chainOfThought",
        "chain_of_thought",
        "reasoningTrace",
        "reasoning_trace",
        "privateReasoning",
        "private_reasoning",
    )
)
_MAX_PUBLIC_TEXT_CHARS = 200
_PUBLIC_REDACTION = "[REDACTED]"
_SECRET_KEY_NORMALIZED = frozenset(
    (
        "authorization",
        "proxyauthorization",
        "cookie",
        "token",
        "accesstoken",
        "refreshtoken",
        "sessiontoken",
        "authtoken",
        "apikey",
        "githuboauth",
        "privatekey",
        "servicerolekey",
        "secret",
        "password",
    )
)


class _StrictFrozenModel(FalseOnlyAuthorityModel):
    """Inference-scaling frozen base.

    Inherits force-false validator/serializer/construct from
    FalseOnlyAuthorityModel; preserves two per-class shims:

    * ``__getattr__`` — looks up fields by their camelCase alias
      (defense-in-depth on top of ``populate_by_name=True`` so any consumer
      fetching the alias-named attribute keeps working).

    * ``model_copy`` — reads field values directly via ``getattr`` (rather
      than ``self.model_dump``) so that a per-field serializer (e.g.
      :class:`TelemetryMetadata._serialize_metadata` which redacts the
      metadata) does NOT redact the raw payload on a ``model_copy``
      roundtrip. The kernel's default ``model_copy`` would route through
      ``model_dump(by_alias=True, mode="python")`` which fires every
      ``@field_serializer`` along the way; ``_canonical_model_data`` /
      ``_canonical_value`` recursion bypasses serializers entirely. The
      end result still routes through ``model_validate`` so the
      false-only invariant + nested revalidation chains run uniformly.
    """

    def __getattr__(self, item: str) -> Any:
        alias_to_name = {
            field.alias: name
            for name, field in self.__class__.model_fields.items()
            if field.alias is not None
        }
        if item in alias_to_name:
            return getattr(self, alias_to_name[item])
        return super().__getattr__(item)

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        _ = deep
        data = _canonical_model_data(self)
        if update:
            alias_by_input_key = {
                field_name: field.alias or field_name
                for field_name, field in self.__class__.model_fields.items()
            }
            alias_by_input_key.update(
                {
                    field.alias: field.alias
                    for field in self.__class__.model_fields.values()
                    if field.alias is not None
                }
            )
            data.update({alias_by_input_key.get(key, key): value for key, value in update.items()})
        return self.__class__.model_validate(data)


class FrozenMetadataDict(Mapping[str, Any]):
    def __init__(self, value: Mapping[str, Any]) -> None:
        self._value = dict(value)

    def __getitem__(self, key: str) -> Any:
        return self._value[key]

    def __iter__(self) -> Iterable[str]:
        return iter(self._value)

    def __len__(self) -> int:
        return len(self._value)

    def __repr__(self) -> str:
        return repr(self._value)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Mapping):
            return dict(self.items()) == dict(other.items())
        return False


class FrozenMetadataList(Sequence[Any]):
    def __init__(self, value: Sequence[Any]) -> None:
        self._value = tuple(value)

    def __getitem__(self, index: int | slice) -> Any:
        return self._value[index]

    def __len__(self) -> int:
        return len(self._value)

    def __repr__(self) -> str:
        return repr(list(self._value))

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Sequence) and not isinstance(other, str | bytes | bytearray):
            return list(self._value) == list(other)
        return False

    def append(self, _: Any) -> None:
        raise TypeError("metadata lists are immutable")


class InferenceScalingScope(_StrictFrozenModel):
    run_on: RunOn = Field(alias="runOn")
    agent_role: AgentRole = Field(alias="agentRole")
    spawn_depth: int = Field(alias="spawnDepth")

    @model_validator(mode="after")
    def _validate_scope(self) -> Self:
        if self.spawn_depth < 0:
            raise ValueError("spawnDepth must be non-negative")
        if self.run_on == "main" and self.spawn_depth != 0:
            raise ValueError("main runs must use spawnDepth=0")
        if self.run_on == "child" and self.spawn_depth <= 0:
            raise ValueError("child runs must use spawnDepth greater than 0")
        return self


class ComputeBudgetMetadata(_StrictFrozenModel):
    risk_level: RiskLevel = Field(alias="riskLevel")
    budget_tier: Literal["minimal", "standard", "expanded"] = Field(alias="budgetTier")
    max_planning_rounds: int = Field(alias="maxPlanningRounds")
    max_critic_rounds: int = Field(alias="maxCriticRounds")
    max_model_escalations: int = Field(alias="maxModelEscalations")
    default_enabled: Literal[False] = Field(default=False, alias="defaultEnabled")
    metadata_only: Literal[True] = Field(default=True, alias="metadataOnly")


class DeterministicGateMetadata(_StrictFrozenModel):
    required: bool
    required_evidence_types: tuple[str, ...] = Field(default=(), alias="requiredEvidenceTypes")
    available_evidence_types: tuple[str, ...] = Field(default=(), alias="availableEvidenceTypes")
    missing_evidence_types: tuple[str, ...] = Field(default=(), alias="missingEvidenceTypes")
    tool_before_escalation: Literal[True] = Field(default=True, alias="toolBeforeEscalation")
    escalation_blocked_until_evidence: bool = Field(alias="escalationBlockedUntilEvidence")
    metadata_only: Literal[True] = Field(default=True, alias="metadataOnly")

    @field_validator("required_evidence_types", "available_evidence_types", "missing_evidence_types")
    @classmethod
    def _reject_duplicate_evidence(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _validate_unique_non_empty_strings(value, "evidence types")


class EscalationMetadata(_StrictFrozenModel):
    kind: EscalationKind
    eligible: bool = False
    reason: str | None = None
    metadata_only: Literal[True] = Field(default=True, alias="metadataOnly")
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")
    model_routing_attached: Literal[False] = Field(default=False, alias="modelRoutingAttached")

    @model_validator(mode="after")
    def _validate_reason(self) -> Self:
        if self.eligible and (self.reason is None or not self.reason.strip()):
            raise ValueError("eligible escalation metadata requires a reason")
        return self


class ReasoningBudgetGateMetadata(_StrictFrozenModel):
    allow_larger_reasoning_budget: bool = Field(alias="allowLargerReasoningBudget")
    reason: str
    blocked_by_deterministic_evidence_gap: bool = Field(
        default=False,
        alias="blockedByDeterministicEvidenceGap",
    )
    default_enabled: Literal[False] = Field(default=False, alias="defaultEnabled")
    metadata_only: Literal[True] = Field(default=True, alias="metadataOnly")

    @field_validator("reason")
    @classmethod
    def _reject_empty_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("reason must be non-empty")
        return value


class BestOfNEligibilityMetadata(_StrictFrozenModel):
    verifier_can_rank_outcomes: bool = Field(alias="verifierCanRankOutcomes")
    side_effects_safe: bool = Field(alias="sideEffectsSafe")
    side_effect_class: SideEffectClass = Field(default="none", alias="sideEffectClass")
    max_variants: int = Field(default=1, alias="maxVariants")
    eligible: bool = False
    default_enabled: Literal[False] = Field(default=False, alias="defaultEnabled")
    metadata_only: Literal[True] = Field(default=True, alias="metadataOnly")

    @model_validator(mode="after")
    def _validate_eligibility(self) -> Self:
        if self.max_variants < 1:
            raise ValueError("maxVariants must be at least 1")
        requested = self.max_variants > 1
        if requested and not self.verifier_can_rank_outcomes:
            raise ValueError("best-of-N requires reliable verifier ranking")
        if requested and (not self.side_effects_safe or self.side_effect_class not in {"none", "read_only"}):
            raise ValueError("best-of-N requires safe or no side effects")
        object.__setattr__(self, "eligible", requested)
        return self


class VerifierConfidenceMetadata(_StrictFrozenModel):
    score: float
    low_confidence_threshold: float = Field(default=0.5, alias="lowConfidenceThreshold")
    critic_requested: bool = Field(default=False, alias="criticRequested")
    metadata_only: Literal[True] = Field(default=True, alias="metadataOnly")

    @model_validator(mode="after")
    def _validate_score(self) -> Self:
        if not 0 <= self.score <= 1:
            raise ValueError("verifier confidence score must be between 0 and 1")
        if not 0 <= self.low_confidence_threshold <= 1:
            raise ValueError("lowConfidenceThreshold must be between 0 and 1")
        return self


class VerifierRetryMetadata(_StrictFrozenModel):
    repeated_verifier_failure: bool = Field(default=False, alias="repeatedVerifierFailure")
    changed_action_or_evidence_target: bool = Field(
        default=False,
        alias="changedActionOrEvidenceTarget",
    )
    changed_action_or_evidence_target_required: bool = Field(
        default=False,
        alias="changedActionOrEvidenceTargetRequired",
    )
    blind_resampling_allowed: bool = Field(default=False, alias="blindResamplingAllowed")
    metadata_only: Literal[True] = Field(default=True, alias="metadataOnly")

    @model_validator(mode="after")
    def _validate_retry(self) -> Self:
        if self.repeated_verifier_failure and not self.changed_action_or_evidence_target:
            object.__setattr__(self, "changed_action_or_evidence_target_required", True)
            object.__setattr__(self, "blind_resampling_allowed", False)
        if self.blind_resampling_allowed:
            raise ValueError("repeated verifier failures cannot allow blind resampling")
        return self


class TelemetryMetadata(_StrictFrozenModel):
    session_id: str = Field(alias="sessionId")
    turn_id: str = Field(alias="turnId")
    scope: InferenceScalingScope
    public_summary: str = Field(alias="publicSummary")
    metadata: Mapping[str, Any] = Field(default_factory=dict)
    reasoning_exposure: Literal[False] = Field(default=False, alias="reasoningExposure")
    telemetry_attached: Literal[False] = Field(default=False, alias="telemetryAttached")
    traffic_attached: Literal[False] = Field(default=False, alias="trafficAttached")
    execution_attached: Literal[False] = Field(default=False, alias="executionAttached")
    runner_attached: Literal[False] = Field(default=False, alias="runnerAttached")
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")
    model_routing_attached: Literal[False] = Field(default=False, alias="modelRoutingAttached")
    billing_attached: Literal[False] = Field(default=False, alias="billingAttached")
    auth_attached: Literal[False] = Field(default=False, alias="authAttached")
    api_proxy_attached: Literal[False] = Field(default=False, alias="apiProxyAttached")
    canary_attached: Literal[False] = Field(default=False, alias="canaryAttached")

    @field_validator("session_id", "turn_id")
    @classmethod
    def _reject_empty_ids(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("telemetry identifiers must be non-empty")
        return value

    @field_validator("public_summary")
    @classmethod
    def _redact_public_summary(cls, value: str) -> str:
        return _sanitize_public_text(value)

    @field_validator("metadata")
    @classmethod
    def _validate_metadata(cls, value: Any) -> Mapping[str, Any]:
        return _freeze_metadata(value, path="metadata")

    @field_serializer("metadata")
    def _serialize_metadata(self, value: Mapping[str, Any]) -> dict[str, Any]:
        redacted = _redact_json_like(value)
        if not isinstance(redacted, dict):
            raise TypeError("metadata serializer expected an object")
        return redacted

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        data = {
            field.alias or field_name: _thaw_json_like(getattr(self, field_name))
            for field_name, field in self.__class__.model_fields.items()
        }
        if update:
            alias_by_input_key = {
                field_name: field.alias or field_name
                for field_name, field in self.__class__.model_fields.items()
            }
            alias_by_input_key.update(
                {
                    field.alias: field.alias
                    for field in self.__class__.model_fields.values()
                    if field.alias is not None
                }
            )
            data.update({alias_by_input_key.get(key, key): value for key, value in update.items()})
        return self.__class__.model_validate(data)

    @model_validator(mode="after")
    def _validate_detached(self) -> Self:
        _reject_any_attachment(self)
        return self


class ScalingPolicyInput(_StrictFrozenModel):
    task_kind: TaskKind = Field(default="general", alias="taskKind")
    risk_level: RiskLevel = Field(default="low", alias="riskLevel")
    scope: InferenceScalingScope
    verifier_confidence: float = Field(default=1.0, alias="verifierConfidence")
    available_evidence_types: tuple[str, ...] = Field(default=(), alias="availableEvidenceTypes")
    opt_out_non_hard_scaling: bool = Field(default=False, alias="optOutNonHardScaling")
    repeated_verifier_failure: bool = Field(default=False, alias="repeatedVerifierFailure")
    changed_action_or_evidence_target: bool = Field(
        default=False,
        alias="changedActionOrEvidenceTarget",
    )
    verifier_can_rank_outcomes: bool = Field(default=False, alias="verifierCanRankOutcomes")
    side_effects_safe: bool = Field(default=True, alias="sideEffectsSafe")
    side_effect_class: SideEffectClass = Field(default="none", alias="sideEffectClass")
    max_variants: int = Field(default=1, alias="maxVariants")

    @field_validator("available_evidence_types")
    @classmethod
    def _reject_duplicate_available_evidence(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _validate_unique_non_empty_strings(value, "availableEvidenceTypes")

    @field_validator("verifier_confidence")
    @classmethod
    def _validate_verifier_confidence(cls, value: float) -> float:
        if not 0 <= value <= 1:
            raise ValueError("verifierConfidence must be between 0 and 1")
        return value


class ScalingPolicyDecision(_StrictFrozenModel):
    scope: InferenceScalingScope
    compute_budget: ComputeBudgetMetadata = Field(alias="computeBudget")
    deterministic_gate: DeterministicGateMetadata = Field(alias="deterministicGate")
    planner_escalation: EscalationMetadata = Field(alias="plannerEscalation")
    critic_escalation: EscalationMetadata = Field(alias="criticEscalation")
    model_escalation: EscalationMetadata = Field(alias="modelEscalation")
    reasoning_budget_gate: ReasoningBudgetGateMetadata = Field(alias="reasoningBudgetGate")
    best_of_n: BestOfNEligibilityMetadata = Field(alias="bestOfN")
    verifier_confidence: VerifierConfidenceMetadata = Field(alias="verifierConfidence")
    verifier_retry: VerifierRetryMetadata = Field(alias="verifierRetry")
    budget_telemetry: TelemetryMetadata = Field(alias="budgetTelemetry")
    non_hard_scaling_opted_out: bool = Field(default=False, alias="nonHardScalingOptedOut")
    hard_evidence_requirements_bypassable: Literal[False] = Field(
        default=False,
        alias="hardEvidenceRequirementsBypassable",
    )
    traffic_attached: Literal[False] = Field(default=False, alias="trafficAttached")
    execution_attached: Literal[False] = Field(default=False, alias="executionAttached")
    runner_attached: Literal[False] = Field(default=False, alias="runnerAttached")
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")
    model_routing_attached: Literal[False] = Field(default=False, alias="modelRoutingAttached")
    billing_attached: Literal[False] = Field(default=False, alias="billingAttached")
    auth_attached: Literal[False] = Field(default=False, alias="authAttached")
    api_proxy_attached: Literal[False] = Field(default=False, alias="apiProxyAttached")
    canary_attached: Literal[False] = Field(default=False, alias="canaryAttached")

    @field_validator("budget_telemetry")
    @classmethod
    def _revalidate_telemetry(cls, value: TelemetryMetadata) -> TelemetryMetadata:
        return TelemetryMetadata.model_validate(_canonical_model_data(value))

    @model_validator(mode="after")
    def _validate_detached(self) -> Self:
        _reject_any_attachment(self)
        return self


def build_scaling_policy_decision(policy_input: ScalingPolicyInput) -> ScalingPolicyDecision:
    data = ScalingPolicyInput.model_validate(
        policy_input.model_dump(by_alias=True, mode="python", warnings=False)
    )
    required_evidence = _required_evidence_for_task(data.task_kind)
    missing_evidence = tuple(item for item in required_evidence if item not in data.available_evidence_types)
    deterministic_required = bool(required_evidence)
    blocked_by_evidence = deterministic_required and bool(missing_evidence)
    confidence = VerifierConfidenceMetadata(
        score=data.verifier_confidence,
        criticRequested=data.verifier_confidence < 0.5 and not blocked_by_evidence,
    )
    opt_out = data.opt_out_non_hard_scaling

    planner_eligible = data.task_kind == "ambiguous_architecture" and not opt_out
    critic_eligible = _critic_eligible(data, blocked_by_evidence=blocked_by_evidence)
    model_eligible = _model_eligible(data, blocked_by_evidence=blocked_by_evidence)
    reasoning_allowed = _reasoning_budget_allowed(data, blocked_by_evidence=blocked_by_evidence)

    return ScalingPolicyDecision(
        scope=data.scope,
        computeBudget=_compute_budget(data.risk_level),
        deterministicGate=DeterministicGateMetadata(
            required=deterministic_required,
            requiredEvidenceTypes=required_evidence,
            availableEvidenceTypes=data.available_evidence_types,
            missingEvidenceTypes=missing_evidence,
            escalationBlockedUntilEvidence=blocked_by_evidence,
        ),
        plannerEscalation=EscalationMetadata(
            kind="planner",
            eligible=planner_eligible,
            reason="ambiguity" if planner_eligible else None,
        ),
        criticEscalation=EscalationMetadata(
            kind="critic",
            eligible=critic_eligible,
            reason=_critic_reason(data) if critic_eligible else None,
        ),
        modelEscalation=EscalationMetadata(
            kind="model",
            eligible=model_eligible,
            reason="complex_synthesis" if model_eligible else None,
        ),
        reasoningBudgetGate=ReasoningBudgetGateMetadata(
            allowLargerReasoningBudget=reasoning_allowed,
            reason=_reasoning_budget_reason(data, blocked_by_evidence=blocked_by_evidence),
            blockedByDeterministicEvidenceGap=blocked_by_evidence,
        ),
        bestOfN=BestOfNEligibilityMetadata(
            verifierCanRankOutcomes=data.verifier_can_rank_outcomes,
            sideEffectsSafe=data.side_effects_safe,
            sideEffectClass=data.side_effect_class,
            maxVariants=_allowed_best_of_n_variants(data),
        ),
        verifierConfidence=confidence,
        verifierRetry=VerifierRetryMetadata(
            repeatedVerifierFailure=data.repeated_verifier_failure,
            changedActionOrEvidenceTarget=data.changed_action_or_evidence_target,
        ),
        budgetTelemetry=TelemetryMetadata(
            sessionId="metadata-only",
            turnId="metadata-only",
            scope=data.scope,
            publicSummary="inference scaling policy metadata only; no model routing or execution attached",
            metadata={
                "taskKind": data.task_kind,
                "riskLevel": data.risk_level,
                "missingEvidenceTypes": list(missing_evidence),
                "nonHardScalingOptedOut": opt_out,
            },
        ),
        nonHardScalingOptedOut=opt_out,
        hardEvidenceRequirementsBypassable=False,
    )


def _compute_budget(risk_level: RiskLevel) -> ComputeBudgetMetadata:
    if risk_level == "high":
        return ComputeBudgetMetadata(
            riskLevel=risk_level,
            budgetTier="expanded",
            maxPlanningRounds=3,
            maxCriticRounds=1,
            maxModelEscalations=1,
        )
    if risk_level == "medium":
        return ComputeBudgetMetadata(
            riskLevel=risk_level,
            budgetTier="standard",
            maxPlanningRounds=2,
            maxCriticRounds=1,
            maxModelEscalations=1,
        )
    return ComputeBudgetMetadata(
        riskLevel=risk_level,
        budgetTier="minimal",
        maxPlanningRounds=1,
        maxCriticRounds=0,
        maxModelEscalations=0,
    )


def _required_evidence_for_task(task_kind: TaskKind) -> tuple[str, ...]:
    if task_kind == "simple_arithmetic":
        return ("Calculation",)
    if task_kind == "source_sensitive_research":
        return ("WebSearch", "SourceInspection", "ClaimLink")
    if task_kind == "coding_change":
        return ("FileInspection", "GitDiff", "Diagnostics", "TestRun", "DiffReview")
    return ()


def _critic_eligible(data: ScalingPolicyInput, *, blocked_by_evidence: bool) -> bool:
    if data.opt_out_non_hard_scaling or blocked_by_evidence:
        return False
    if data.repeated_verifier_failure and not data.changed_action_or_evidence_target:
        return False
    return data.task_kind == "ambiguous_architecture" or data.verifier_confidence < 0.5


def _model_eligible(data: ScalingPolicyInput, *, blocked_by_evidence: bool) -> bool:
    if data.opt_out_non_hard_scaling or blocked_by_evidence:
        return False
    if data.repeated_verifier_failure and not data.changed_action_or_evidence_target:
        return False
    return data.task_kind == "complex_synthesis" and data.verifier_confidence < 0.5


def _reasoning_budget_allowed(data: ScalingPolicyInput, *, blocked_by_evidence: bool) -> bool:
    if data.opt_out_non_hard_scaling or blocked_by_evidence:
        return False
    return data.task_kind in {"complex_synthesis", "ambiguous_architecture"} and data.risk_level in {
        "medium",
        "high",
    }


def _allowed_best_of_n_variants(data: ScalingPolicyInput) -> int:
    if data.opt_out_non_hard_scaling:
        return 1
    if data.repeated_verifier_failure and not data.changed_action_or_evidence_target:
        return 1
    return data.max_variants


def _critic_reason(data: ScalingPolicyInput) -> str:
    if data.task_kind == "ambiguous_architecture":
        return "ambiguity"
    if data.verifier_confidence < 0.5:
        return "low_verifier_confidence"
    return "synthesis_quality"


def _reasoning_budget_reason(data: ScalingPolicyInput, *, blocked_by_evidence: bool) -> str:
    if blocked_by_evidence:
        return "deterministic evidence is required before larger reasoning budgets"
    if data.opt_out_non_hard_scaling:
        return "non-hard scaling opted out"
    if data.task_kind in {"simple_arithmetic", "source_sensitive_research", "coding_change"}:
        return "deterministic evidence is preferred over larger reasoning"
    return "complex synthesis metadata may use a larger reasoning budget later"


def _sanitize_public_text(value: str) -> str:
    sanitized = _redact_public_summary_text(value)
    if len(sanitized) > _MAX_PUBLIC_TEXT_CHARS:
        return f"{sanitized[: _MAX_PUBLIC_TEXT_CHARS - 3]}..."
    return sanitized


def _redact_public_summary_text(value: str) -> str:
    redacted = re.sub(
        r"(?i)\bbearer\s+[a-z0-9._~+/=-]+",
        f"Bearer {_PUBLIC_REDACTION}",
        value,
    )
    redacted = re.sub(
        r"(?i)\bbasic\s+[a-z0-9+/=_:-]+",
        f"Basic {_PUBLIC_REDACTION}",
        redacted,
    )
    redacted = re.sub(
        r"(?i)\b((?:proxy[_-]?)?authorization|github[_-]?oauth|api[_-]?key|"
        r"(?:access|auth|refresh|session)?[_-]?token|service[_-]?role[_-]?key|private[_-]?key|"
        r"cookie|secret|password)\s*([:=])\s*([\"']?)[^\s,\"']+([\"']?)",
        lambda match: (
            f"{match.group(1)}{match.group(2)}{match.group(3)}{_PUBLIC_REDACTION}{match.group(4)}"
        ),
        redacted,
    )
    redacted = re.sub(
        r"(?i)([\"']?)(\b(?:accessToken|authToken|refreshToken|sessionToken|apiKey|githubOauth|token)\b)"
        r"([\"']?)\s*([:=])\s*([\"']?)[^\s,\"']+([\"']?)",
        lambda match: (
            f"{match.group(1)}{match.group(2)}{match.group(3)}"
            f"{match.group(4)}{match.group(5)}{_PUBLIC_REDACTION}{match.group(6)}"
        ),
        redacted,
    )
    redacted = re.sub(
        r"\b(?:sk|pk|rk|ghp|github_pat)[_-][A-Za-z0-9_=-]{12,}\b",
        _PUBLIC_REDACTION,
        redacted,
    )
    return redacted


def _redact_json_like(value: Any, *, key: str | None = None) -> Any:
    if key is not None and _is_secret_like_key(key):
        return _PUBLIC_REDACTION
    if isinstance(value, Mapping):
        return {item_key: _redact_json_like(item, key=item_key) for item_key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_redact_json_like(item) for item in value]
    if isinstance(value, str):
        return _sanitize_public_text(value)
    return value


def _is_secret_like_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", key.lower())
    if normalized in _SECRET_KEY_NORMALIZED:
        return True
    return normalized.endswith("token") or normalized.endswith("apikey") or normalized.endswith("secret")


def _freeze_metadata(value: Any, *, path: str) -> Mapping[str, Any]:
    frozen = _freeze_json_like(value, path=path)
    if not isinstance(frozen, Mapping):
        raise ValueError(f"{path} must be a JSON-like object")
    return frozen


def _freeze_json_like(value: Any, *, path: str) -> Any:
    if isinstance(value, FrozenMetadataDict | FrozenMetadataList):
        return value
    if _is_hidden_reasoning_key(path):
        raise ValueError("hidden reasoning fields are not allowed in public/session telemetry")
    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} numbers must be finite")
        return value
    if isinstance(value, list):
        return FrozenMetadataList(
            [_freeze_json_like(item, path=f"{path}[{index}]") for index, item in enumerate(value)]
        )
    if isinstance(value, dict):
        frozen: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path} keys must be strings")
            if key in _HIDDEN_REASONING_KEYS:
                raise ValueError("hidden reasoning fields are not allowed in public/session telemetry")
            frozen[key] = _freeze_json_like(item, path=f"{path}.{key}")
        return FrozenMetadataDict(frozen)
    raise ValueError(f"{path} must contain only JSON-like metadata")


def _thaw_json_like(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json_like(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_thaw_json_like(item) for item in value]
    return value


def _canonical_model_data(model: BaseModel) -> dict[str, Any]:
    return {
        field.alias or field_name: _canonical_value(getattr(model, field_name))
        for field_name, field in model.__class__.model_fields.items()
    }


def _canonical_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _canonical_model_data(value)
    if isinstance(value, Mapping):
        return {key: _canonical_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_canonical_value(item) for item in value]
    return value


def _is_hidden_reasoning_key(path: str) -> bool:
    return path.rsplit(".", 1)[-1] in _HIDDEN_REASONING_KEYS


def _reject_any_attachment(model: BaseModel) -> None:
    for field_name in _ATTACHMENT_FIELD_NAMES:
        if bool(getattr(model, field_name, False)):
            raise ValueError("inference scaling scaffold attachment flags must remain false")


def _validate_unique_non_empty_strings(value: tuple[str, ...], field_name: str) -> tuple[str, ...]:
    if any(not item.strip() for item in value):
        raise ValueError(f"{field_name} must contain non-empty strings")
    if len(set(value)) != len(value):
        raise ValueError(f"{field_name} must not contain duplicates")
    return value


__all__ = [
    "ATTACHMENT_FLAGS",
    "AgentRole",
    "BestOfNEligibilityMetadata",
    "ComputeBudgetMetadata",
    "DeterministicGateMetadata",
    "EscalationMetadata",
    "InferenceScalingScope",
    "ReasoningBudgetGateMetadata",
    "RunOn",
    "ScalingPolicyDecision",
    "ScalingPolicyInput",
    "TelemetryMetadata",
    "VerifierConfidenceMetadata",
    "VerifierRetryMetadata",
    "build_scaling_policy_decision",
]
