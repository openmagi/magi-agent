from __future__ import annotations

import pytest
from pydantic import ValidationError

from magi_agent.harness.autopilot import (
    AMBIGUITY_THRESHOLD_BY_PROFILE,
    AUTOPILOT_FEATURE_KEY,
    DEFAULT_AUTOPILOT_MAX_REVIEW_CYCLES,
    TERMINAL_PHASES,
    AutopilotAmbiguityScore,
    AutopilotFsmPolicy,
    AutopilotGateVerdict,
    AutopilotInterviewDepth,
    AutopilotOptOutState,
    AutopilotPhase,
    AutopilotPhaseTransition,
    build_autopilot_policy,
    evaluate_autopilot_transition,
    gate_for_phase,
    interview_gate_verdict,
)


def test_phase_and_verdict_enum_values() -> None:
    assert [p.value for p in AutopilotPhase] == [
        "interview", "plan", "execute", "review", "qa", "complete", "blocked",
    ]
    assert [v.value for v in AutopilotGateVerdict] == ["pass", "fail", "skip"]
    assert TERMINAL_PHASES == (AutopilotPhase.COMPLETE, AutopilotPhase.BLOCKED)
    assert AUTOPILOT_FEATURE_KEY == "autopilot-fsm"
    assert DEFAULT_AUTOPILOT_MAX_REVIEW_CYCLES == 3


def test_default_policy_is_disabled_and_traffic_free() -> None:
    dumped = build_autopilot_policy().model_dump(by_alias=True)
    assert dumped["featureKey"] == "autopilot-fsm"
    assert dumped["enabled"] is False
    assert dumped["maxReviewCycles"] == 3
    assert dumped["qaSkipAllowedForNonruntime"] is True
    assert dumped["trafficAttached"] is False
    assert dumped["executionAttached"] is False
    assert dumped["optOut"]["optedOut"] is False


def test_disabled_policy_cannot_evaluate_transition() -> None:
    with pytest.raises(ValueError, match="disabled"):
        evaluate_autopilot_transition(
            current=AutopilotPhase.INTERVIEW,
            verdict=AutopilotGateVerdict.PASS,
            review_cycle=0,
            policy=AutopilotFsmPolicy(),
        )


def test_policy_rejects_attachment_flags() -> None:
    with pytest.raises(ValidationError, match="traffic-free"):
        AutopilotFsmPolicy(enabled=True, trafficAttached=True)


def test_opt_out_must_disable_fsm() -> None:
    with pytest.raises(ValidationError, match="opt-out"):
        AutopilotFsmPolicy(enabled=True, optOut=AutopilotOptOutState(optedOut=True))


def test_build_policy_opt_out_forces_disabled() -> None:
    policy = build_autopilot_policy(
        enabled=True, opt_out=AutopilotOptOutState(optedOut=True)
    )
    assert policy.enabled is False


def test_max_review_cycles_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        AutopilotFsmPolicy(maxReviewCycles=0)


def _transition(**kw: object) -> AutopilotPhaseTransition:
    base: dict[str, object] = {
        "fromPhase": AutopilotPhase.EXECUTE,
        "toPhase": AutopilotPhase.REVIEW,
        "gate": "execution-evidence",
        "verdict": AutopilotGateVerdict.PASS,
    }
    base.update(kw)
    return AutopilotPhaseTransition(**base)


def test_transition_dumps_aliases_and_is_route_free() -> None:
    dumped = _transition().model_dump(by_alias=True)
    assert dumped["fromPhase"] == "execute"
    assert dumped["toPhase"] == "review"
    assert dumped["routeAttached"] is False
    assert dumped["executionAttached"] is False
    assert dumped["terminal"] is False


def test_transition_rejects_empty_gate() -> None:
    with pytest.raises(ValidationError, match="gate"):
        _transition(gate="  ")


def test_return_to_plan_requires_reason() -> None:
    with pytest.raises(ValidationError, match="returnToPlanReason"):
        _transition(
            fromPhase=AutopilotPhase.REVIEW,
            toPhase=AutopilotPhase.PLAN,
            gate="review-clean",
            verdict=AutopilotGateVerdict.FAIL,
        )


def test_terminal_flag_must_match_target_phase() -> None:
    with pytest.raises(ValidationError, match="terminal"):
        _transition(toPhase=AutopilotPhase.COMPLETE, terminal=False)


def _eval(current: AutopilotPhase, verdict: AutopilotGateVerdict, cycle: int = 0) -> AutopilotPhaseTransition:
    return evaluate_autopilot_transition(
        current=current,
        verdict=verdict,
        review_cycle=cycle,
        policy=build_autopilot_policy(enabled=True),
    )


def test_gate_for_phase_maps_each_active_phase() -> None:
    assert gate_for_phase(AutopilotPhase.INTERVIEW) == "interview-ambiguity-cleared"
    assert gate_for_phase(AutopilotPhase.PLAN) == "consensus-architect-then-critic"
    assert gate_for_phase(AutopilotPhase.EXECUTE) == "execution-evidence"
    assert gate_for_phase(AutopilotPhase.REVIEW) == "review-clean"
    assert gate_for_phase(AutopilotPhase.QA) == "adversarial-qa"
    with pytest.raises(ValueError):
        gate_for_phase(AutopilotPhase.COMPLETE)
    with pytest.raises(ValueError):
        gate_for_phase(AutopilotPhase.BLOCKED)


def test_happy_path_advances_each_phase() -> None:
    assert _eval(AutopilotPhase.INTERVIEW, AutopilotGateVerdict.PASS).to_phase == AutopilotPhase.PLAN
    assert _eval(AutopilotPhase.PLAN, AutopilotGateVerdict.PASS).to_phase == AutopilotPhase.EXECUTE
    assert _eval(AutopilotPhase.EXECUTE, AutopilotGateVerdict.PASS).to_phase == AutopilotPhase.REVIEW
    assert _eval(AutopilotPhase.REVIEW, AutopilotGateVerdict.PASS).to_phase == AutopilotPhase.QA
    qa_pass = _eval(AutopilotPhase.QA, AutopilotGateVerdict.PASS)
    assert qa_pass.to_phase == AutopilotPhase.COMPLETE
    assert qa_pass.terminal is True


def test_qa_skip_completes_only_when_allowed() -> None:
    skip = _eval(AutopilotPhase.QA, AutopilotGateVerdict.SKIP)
    assert skip.to_phase == AutopilotPhase.COMPLETE and skip.terminal is True
    with pytest.raises(ValueError, match="skip"):
        _eval(AutopilotPhase.REVIEW, AutopilotGateVerdict.SKIP)
    policy = build_autopilot_policy(enabled=True, qa_skip_allowed_for_nonruntime=False)
    with pytest.raises(ValueError, match="skip"):
        evaluate_autopilot_transition(
            current=AutopilotPhase.QA, verdict=AutopilotGateVerdict.SKIP,
            review_cycle=0, policy=policy,
        )


def test_review_fail_returns_to_plan_and_increments_cycle() -> None:
    t = _eval(AutopilotPhase.REVIEW, AutopilotGateVerdict.FAIL, cycle=0)
    assert t.to_phase == AutopilotPhase.PLAN
    assert t.review_cycle == 1
    assert t.return_to_plan_reason


def test_review_fail_past_max_cycles_blocks() -> None:
    t = _eval(AutopilotPhase.REVIEW, AutopilotGateVerdict.FAIL, cycle=3)
    assert t.to_phase == AutopilotPhase.BLOCKED
    assert t.terminal is True


def test_review_fail_at_cycle_below_max_still_returns_to_plan() -> None:
    # cycle=2 with default max=3: next_cycle=3 is NOT > 3, so still bounces to plan
    t = _eval(AutopilotPhase.REVIEW, AutopilotGateVerdict.FAIL, cycle=2)
    assert t.to_phase == AutopilotPhase.PLAN
    assert t.review_cycle == 3


def test_early_phase_fail_stays_in_phase() -> None:
    for phase in (AutopilotPhase.INTERVIEW, AutopilotPhase.PLAN, AutopilotPhase.EXECUTE):
        t = _eval(phase, AutopilotGateVerdict.FAIL)
        assert t.to_phase == phase


def test_cannot_transition_from_terminal_phase() -> None:
    with pytest.raises(ValueError, match="terminal"):
        _eval(AutopilotPhase.COMPLETE, AutopilotGateVerdict.PASS)


def test_full_loop_with_one_review_failure_reaches_complete() -> None:
    policy = build_autopilot_policy(enabled=True)
    phase = AutopilotPhase.INTERVIEW
    cycle = 0
    verdicts = iter([
        ("interview", AutopilotGateVerdict.PASS),
        ("plan", AutopilotGateVerdict.PASS),
        ("execute", AutopilotGateVerdict.PASS),
        ("review", AutopilotGateVerdict.FAIL),   # bounce back to plan
        ("plan", AutopilotGateVerdict.PASS),
        ("execute", AutopilotGateVerdict.PASS),
        ("review", AutopilotGateVerdict.PASS),
        ("qa", AutopilotGateVerdict.PASS),
    ])
    visited: list[str] = []
    for _label, verdict in verdicts:
        t = evaluate_autopilot_transition(
            current=phase, verdict=verdict, review_cycle=cycle, policy=policy
        )
        visited.append(t.to_phase.value)
        phase, cycle = t.to_phase, t.review_cycle
    assert phase == AutopilotPhase.COMPLETE
    assert cycle == 1  # one review failure consumed one cycle
    assert visited[-1] == "complete"


def test_depth_thresholds() -> None:
    assert AMBIGUITY_THRESHOLD_BY_PROFILE[AutopilotInterviewDepth.QUICK] == 0.30
    assert AMBIGUITY_THRESHOLD_BY_PROFILE[AutopilotInterviewDepth.STANDARD] == 0.20
    assert AMBIGUITY_THRESHOLD_BY_PROFILE[AutopilotInterviewDepth.DEEP] == 0.15


def test_ambiguity_score_bounds() -> None:
    with pytest.raises(ValidationError):
        AutopilotAmbiguityScore(depth=AutopilotInterviewDepth.STANDARD, score=1.5, rounds=1)
    with pytest.raises(ValidationError):
        AutopilotAmbiguityScore(depth=AutopilotInterviewDepth.STANDARD, score=0.2, rounds=0)
    with pytest.raises(ValidationError):
        AutopilotAmbiguityScore(depth=AutopilotInterviewDepth.STANDARD, score=-0.01, rounds=1)
    with pytest.raises(ValidationError):
        AutopilotAmbiguityScore(depth=AutopilotInterviewDepth.STANDARD, score=0.2, rounds=1, max_rounds=0)


def test_interview_gate_passes_when_score_at_or_below_threshold() -> None:
    cleared = AutopilotAmbiguityScore(
        depth=AutopilotInterviewDepth.STANDARD, score=0.20, rounds=3
    )
    assert interview_gate_verdict(cleared) is AutopilotGateVerdict.PASS


def test_interview_gate_fails_when_score_above_threshold() -> None:
    unresolved = AutopilotAmbiguityScore(
        depth=AutopilotInterviewDepth.STANDARD, score=0.25, rounds=3
    )
    assert interview_gate_verdict(unresolved) is AutopilotGateVerdict.FAIL


def test_interview_gate_fails_at_max_rounds_even_if_unresolved() -> None:
    # hitting the round cap does not force PASS; the FSM treats FAIL-at-cap as
    # "carry forward unresolved", handled by the caller, not by faking a clear.
    capped = AutopilotAmbiguityScore(
        depth=AutopilotInterviewDepth.QUICK, score=0.9, rounds=5, max_rounds=5
    )
    assert capped.at_round_cap is True
    assert interview_gate_verdict(capped) is AutopilotGateVerdict.FAIL


def test_resolved_max_rounds_falls_back_to_profile_default() -> None:
    score = AutopilotAmbiguityScore(
        depth=AutopilotInterviewDepth.STANDARD, score=0.1, rounds=12
    )
    assert score.max_rounds is None
    assert score.resolved_max_rounds == 12  # STANDARD default
    assert score.at_round_cap is True


def test_interview_fail_keeps_fsm_in_interview() -> None:
    unresolved = AutopilotAmbiguityScore(
        depth=AutopilotInterviewDepth.STANDARD, score=0.25, rounds=3
    )
    verdict = interview_gate_verdict(unresolved)
    t = evaluate_autopilot_transition(
        current=AutopilotPhase.INTERVIEW,
        verdict=verdict,
        review_cycle=0,
        policy=build_autopilot_policy(enabled=True),
    )
    assert verdict is AutopilotGateVerdict.FAIL
    assert t.to_phase == AutopilotPhase.INTERVIEW
