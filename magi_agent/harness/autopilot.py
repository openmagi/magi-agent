from __future__ import annotations

from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


AUTOPILOT_FEATURE_KEY = "autopilot-fsm"
DEFAULT_AUTOPILOT_MAX_REVIEW_CYCLES = 3


class AutopilotPhase(StrEnum):
    INTERVIEW = "interview"
    PLAN = "plan"
    EXECUTE = "execute"
    REVIEW = "review"
    QA = "qa"
    COMPLETE = "complete"
    BLOCKED = "blocked"


class AutopilotGateVerdict(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"


TERMINAL_PHASES: tuple[AutopilotPhase, ...] = (
    AutopilotPhase.COMPLETE,
    AutopilotPhase.BLOCKED,
)


class AutopilotOptOutState(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

    opted_out: bool = Field(default=False, alias="optedOut")
    disabled_reason: str | None = Field(default=None, alias="disabledReason")


class AutopilotFsmPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

    feature_key: Literal["autopilot-fsm"] = Field(
        default=AUTOPILOT_FEATURE_KEY, alias="featureKey"
    )
    enabled: bool = False
    max_review_cycles: int = Field(
        default=DEFAULT_AUTOPILOT_MAX_REVIEW_CYCLES, alias="maxReviewCycles", ge=1
    )
    qa_skip_allowed_for_nonruntime: bool = Field(
        default=True, alias="qaSkipAllowedForNonruntime"
    )
    opt_out: AutopilotOptOutState = Field(
        default_factory=AutopilotOptOutState, alias="optOut"
    )
    traffic_attached: bool = Field(default=False, alias="trafficAttached")
    execution_attached: bool = Field(default=False, alias="executionAttached")

    @model_validator(mode="after")
    def _validate_traffic_free_and_opt_out(self) -> Self:
        if self.traffic_attached or self.execution_attached:
            raise ValueError("autopilot fsm scaffold must remain traffic-free")
        if self.opt_out.opted_out and self.enabled:
            raise ValueError("autopilot opt-out must disable the fsm")
        return self


class AutopilotPhaseTransition(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

    from_phase: AutopilotPhase = Field(alias="fromPhase")
    to_phase: AutopilotPhase = Field(alias="toPhase")
    gate: str
    verdict: AutopilotGateVerdict
    review_cycle: int = Field(default=0, alias="reviewCycle", ge=0)
    return_to_plan_reason: str | None = Field(default=None, alias="returnToPlanReason")
    terminal: bool = False
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")
    execution_attached: Literal[False] = Field(default=False, alias="executionAttached")

    @field_validator("gate")
    @classmethod
    def _reject_empty_gate(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("gate must be non-empty")
        return value

    @field_validator("return_to_plan_reason")
    @classmethod
    def _reject_empty_reason(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("returnToPlanReason must be non-empty when provided")
        return value

    @model_validator(mode="after")
    def _validate_transition_semantics(self) -> Self:
        returning_to_plan = self.to_phase == AutopilotPhase.PLAN and self.from_phase in (
            AutopilotPhase.REVIEW,
            AutopilotPhase.QA,
        )
        if returning_to_plan and not self.return_to_plan_reason:
            raise ValueError("return-to-plan transition requires returnToPlanReason")
        if self.terminal != (self.to_phase in TERMINAL_PHASES):
            raise ValueError("terminal flag must match terminal target phase")
        return self


def build_autopilot_policy(
    *,
    enabled: bool = False,
    max_review_cycles: int = DEFAULT_AUTOPILOT_MAX_REVIEW_CYCLES,
    qa_skip_allowed_for_nonruntime: bool = True,
    opt_out: AutopilotOptOutState | None = None,
) -> AutopilotFsmPolicy:
    resolved_opt_out = (
        AutopilotOptOutState.model_validate(opt_out.model_dump())
        if opt_out is not None
        else AutopilotOptOutState()
    )
    if resolved_opt_out.opted_out:
        enabled = False
    return AutopilotFsmPolicy(
        enabled=enabled,
        max_review_cycles=max_review_cycles,
        qa_skip_allowed_for_nonruntime=qa_skip_allowed_for_nonruntime,
        opt_out=resolved_opt_out,
        traffic_attached=False,
        execution_attached=False,
    )


# Gate names per active phase. NOTE for the PR5 live driver: `execution-evidence`
# (EXECUTE) has no autopilot-specific preset/validator ref on purpose — execute-phase
# advancement reuses the existing coding-verification/goal-progress presets, not a new
# verifier hook. The `max-review-cycle-bounded` recipe validator maps to the cycle bound
# enforced in evaluate_autopilot_transition below, not to a verifier gate.
_GATE_BY_PHASE: dict[AutopilotPhase, str] = {
    AutopilotPhase.INTERVIEW: "interview-ambiguity-cleared",
    AutopilotPhase.PLAN: "consensus-architect-then-critic",
    AutopilotPhase.EXECUTE: "execution-evidence",
    AutopilotPhase.REVIEW: "review-clean",
    AutopilotPhase.QA: "adversarial-qa",
}

_PASS_NEXT_PHASE: dict[AutopilotPhase, AutopilotPhase] = {
    AutopilotPhase.INTERVIEW: AutopilotPhase.PLAN,
    AutopilotPhase.PLAN: AutopilotPhase.EXECUTE,
    AutopilotPhase.EXECUTE: AutopilotPhase.REVIEW,
    AutopilotPhase.REVIEW: AutopilotPhase.QA,
    AutopilotPhase.QA: AutopilotPhase.COMPLETE,
}

_REVIEW_PHASES: tuple[AutopilotPhase, ...] = (AutopilotPhase.REVIEW, AutopilotPhase.QA)


def gate_for_phase(phase: AutopilotPhase) -> str:
    try:
        return _GATE_BY_PHASE[phase]
    except KeyError as exc:
        raise ValueError(f"phase {phase} has no gate") from exc


def _build_transition(
    *,
    from_phase: AutopilotPhase,
    to_phase: AutopilotPhase,
    gate: str,
    verdict: AutopilotGateVerdict,
    review_cycle: int,
    return_to_plan_reason: str | None = None,
) -> AutopilotPhaseTransition:
    return AutopilotPhaseTransition(
        from_phase=from_phase,
        to_phase=to_phase,
        gate=gate,
        verdict=verdict,
        review_cycle=review_cycle,
        return_to_plan_reason=return_to_plan_reason,
        terminal=to_phase in TERMINAL_PHASES,
    )


def evaluate_autopilot_transition(
    *,
    current: AutopilotPhase,
    verdict: AutopilotGateVerdict,
    review_cycle: int,
    policy: AutopilotFsmPolicy,
) -> AutopilotPhaseTransition:
    """Pure FSM transition. No I/O, no execution; returns the next transition snapshot."""
    if not policy.enabled:
        raise ValueError("autopilot policy is disabled")
    if current in TERMINAL_PHASES:
        raise ValueError("cannot transition from a terminal phase")
    gate = gate_for_phase(current)

    if verdict is AutopilotGateVerdict.SKIP:
        if current is not AutopilotPhase.QA or not policy.qa_skip_allowed_for_nonruntime:
            raise ValueError(
                "skip verdict only allowed for QA when qaSkipAllowedForNonruntime is true"
            )
        return _build_transition(
            from_phase=current, to_phase=AutopilotPhase.COMPLETE, gate=gate,
            verdict=verdict, review_cycle=review_cycle,
        )

    if verdict is AutopilotGateVerdict.PASS:
        return _build_transition(
            from_phase=current, to_phase=_PASS_NEXT_PHASE[current], gate=gate,
            verdict=verdict, review_cycle=review_cycle,
        )

    # FAIL
    if current in _REVIEW_PHASES:
        next_cycle = review_cycle + 1
        if next_cycle > policy.max_review_cycles:
            return _build_transition(
                from_phase=current, to_phase=AutopilotPhase.BLOCKED, gate=gate,
                verdict=verdict, review_cycle=review_cycle,
            )
        return _build_transition(
            from_phase=current, to_phase=AutopilotPhase.PLAN, gate=gate,
            verdict=verdict, review_cycle=next_cycle,
            return_to_plan_reason=f"{current.value} gate not clean",
        )
    # interview / plan / execute fail: stay in phase (retry/continue)
    return _build_transition(
        from_phase=current, to_phase=current, gate=gate,
        verdict=verdict, review_cycle=review_cycle,
    )


class AutopilotInterviewDepth(StrEnum):
    QUICK = "quick"
    STANDARD = "standard"
    DEEP = "deep"


AMBIGUITY_THRESHOLD_BY_PROFILE: dict[AutopilotInterviewDepth, float] = {
    AutopilotInterviewDepth.QUICK: 0.30,
    AutopilotInterviewDepth.STANDARD: 0.20,
    AutopilotInterviewDepth.DEEP: 0.15,
}

_DEFAULT_MAX_ROUNDS_BY_PROFILE: dict[AutopilotInterviewDepth, int] = {
    AutopilotInterviewDepth.QUICK: 5,
    AutopilotInterviewDepth.STANDARD: 12,
    AutopilotInterviewDepth.DEEP: 20,
}


class AutopilotAmbiguityScore(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

    depth: AutopilotInterviewDepth
    score: float = Field(ge=0.0, le=1.0)
    rounds: int = Field(ge=1)
    max_rounds: int | None = Field(default=None, alias="maxRounds", ge=1)

    @property
    def threshold(self) -> float:
        return AMBIGUITY_THRESHOLD_BY_PROFILE[self.depth]

    @property
    def resolved_max_rounds(self) -> int:
        if self.max_rounds is not None:
            return self.max_rounds
        return _DEFAULT_MAX_ROUNDS_BY_PROFILE[self.depth]

    # at_round_cap / resolved_max_rounds are read by the PR5 FSM driver;
    # interview_gate_verdict itself is purely score-vs-threshold and ignores them.
    @property
    def at_round_cap(self) -> bool:
        return self.rounds >= self.resolved_max_rounds


def interview_gate_verdict(score: AutopilotAmbiguityScore) -> AutopilotGateVerdict:
    """Pure: ambiguity at/below the depth threshold clears the interview gate."""
    if score.score <= score.threshold:
        return AutopilotGateVerdict.PASS
    return AutopilotGateVerdict.FAIL


__all__ = [
    "AMBIGUITY_THRESHOLD_BY_PROFILE",
    "AUTOPILOT_FEATURE_KEY",
    "DEFAULT_AUTOPILOT_MAX_REVIEW_CYCLES",
    "TERMINAL_PHASES",
    "AutopilotAmbiguityScore",
    "AutopilotFsmPolicy",
    "AutopilotGateVerdict",
    "AutopilotInterviewDepth",
    "AutopilotOptOutState",
    "AutopilotPhase",
    "AutopilotPhaseTransition",
    "build_autopilot_policy",
    "evaluate_autopilot_transition",
    "gate_for_phase",
    "interview_gate_verdict",
]
