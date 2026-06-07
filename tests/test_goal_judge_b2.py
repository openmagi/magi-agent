"""B2 — GoalJudge: goal-satisfaction judge tests.

TDD protocol: RED → GREEN → REFACTOR.

Tests cover:
- parse_verdict: SATISFIED token, NOT_SATISFIED token, JSON {"satisfied": true/false},
  case-insensitive tokens, unparseable → None
- fail_open_policy: None verdict → continue (fail-open), parsed verdict acts normally,
  consecutive parse-failure budget exhaustion → stop
- JudgeVerdict frozen model
- JudgeDecision shadow result: acted==False in shadow mode
- Evidence redaction: raw goal/transcript not stored; only hashes+lengths
- GoalJudge Protocol is @runtime_checkable
- Import boundary: no ADK / google.adk at top level
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from magi_agent.harness.goal_judge import (
    DEFAULT_JUDGE_PARSE_FAILURE_BUDGET,
    GoalJudge,
    JudgeDecision,
    JudgeVerdict,
    apply_judge_policy,
    build_judge_evidence,
    parse_verdict,
    run_judge,
)


# ---------------------------------------------------------------------------
# JudgeVerdict model
# ---------------------------------------------------------------------------


class TestJudgeVerdict:
    def test_satisfied_true(self) -> None:
        v = JudgeVerdict(satisfied=True, raw="SATISFIED")
        assert v.satisfied is True
        assert v.confidence is None
        assert v.raw == "SATISFIED"

    def test_satisfied_false(self) -> None:
        v = JudgeVerdict(satisfied=False, raw="NOT_SATISFIED")
        assert v.satisfied is False

    def test_confidence_stored(self) -> None:
        v = JudgeVerdict(satisfied=True, confidence=0.9, raw="SATISFIED confidence 0.9")
        assert v.confidence == 0.9

    def test_frozen(self) -> None:
        v = JudgeVerdict(satisfied=True, raw="SATISFIED")
        with pytest.raises((TypeError, ValidationError)):
            v.satisfied = False  # type: ignore[misc]

    def test_raw_required(self) -> None:
        with pytest.raises(ValidationError):
            JudgeVerdict(satisfied=True)  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# parse_verdict
# ---------------------------------------------------------------------------


class TestParseVerdict:
    def test_satisfied_token_uppercase(self) -> None:
        result = parse_verdict("The goal is complete. SATISFIED")
        assert result is not None
        assert result.satisfied is True

    def test_not_satisfied_token(self) -> None:
        result = parse_verdict("Still working. NOT_SATISFIED")
        assert result is not None
        assert result.satisfied is False

    def test_satisfied_token_case_insensitive(self) -> None:
        result = parse_verdict("satisfied")
        assert result is not None
        assert result.satisfied is True

    def test_not_satisfied_token_case_insensitive(self) -> None:
        result = parse_verdict("not_satisfied")
        assert result is not None
        assert result.satisfied is False

    def test_json_satisfied_true(self) -> None:
        result = parse_verdict('{"satisfied": true}')
        assert result is not None
        assert result.satisfied is True

    def test_json_satisfied_false(self) -> None:
        result = parse_verdict('{"satisfied": false}')
        assert result is not None
        assert result.satisfied is False

    def test_json_with_extra_fields(self) -> None:
        result = parse_verdict('{"satisfied": true, "reason": "done"}')
        assert result is not None
        assert result.satisfied is True

    def test_not_satisfied_takes_precedence_over_satisfied(self) -> None:
        # If "NOT_SATISFIED" appears, that wins over any bare "SATISFIED" token
        result = parse_verdict("NOT_SATISFIED but also mentioned SATISFIED")
        assert result is not None
        assert result.satisfied is False

    def test_unparseable_returns_none(self) -> None:
        result = parse_verdict("I'm still thinking about the goal.")
        assert result is None

    def test_empty_string_returns_none(self) -> None:
        result = parse_verdict("")
        assert result is None

    def test_raw_stored_on_result(self) -> None:
        raw = "SATISFIED"
        result = parse_verdict(raw)
        assert result is not None
        assert result.raw == raw

    def test_json_embedded_in_prose(self) -> None:
        raw = 'Based on review: {"satisfied": false}'
        result = parse_verdict(raw)
        assert result is not None
        assert result.satisfied is False


# ---------------------------------------------------------------------------
# apply_judge_policy (fail-open + parse-failure budget)
# ---------------------------------------------------------------------------


class TestApplyJudgePolicy:
    def test_satisfied_verdict_returns_stop(self) -> None:
        verdict = JudgeVerdict(satisfied=True, raw="SATISFIED")
        outcome = apply_judge_policy(verdict_or_none=verdict, consecutive_parse_failures=0)
        assert outcome["action"] == "stop"
        assert outcome["reason"] == "satisfied"

    def test_not_satisfied_verdict_returns_continue(self) -> None:
        verdict = JudgeVerdict(satisfied=False, raw="NOT_SATISFIED")
        outcome = apply_judge_policy(verdict_or_none=verdict, consecutive_parse_failures=0)
        assert outcome["action"] == "continue"
        assert outcome["reason"] == "not_satisfied"

    def test_none_verdict_fail_open_continue(self) -> None:
        outcome = apply_judge_policy(verdict_or_none=None, consecutive_parse_failures=0)
        assert outcome["action"] == "continue"
        assert outcome["reason"] == "parse_failure_fail_open"

    def test_budget_not_exhausted_continue(self) -> None:
        budget_minus_1 = DEFAULT_JUDGE_PARSE_FAILURE_BUDGET - 1
        outcome = apply_judge_policy(
            verdict_or_none=None, consecutive_parse_failures=budget_minus_1
        )
        assert outcome["action"] == "continue"

    def test_budget_exhausted_stop(self) -> None:
        outcome = apply_judge_policy(
            verdict_or_none=None,
            consecutive_parse_failures=DEFAULT_JUDGE_PARSE_FAILURE_BUDGET,
        )
        assert outcome["action"] == "stop"
        assert outcome["reason"] == "parse_failure_budget_exhausted"

    def test_budget_over_exhausted_stop(self) -> None:
        outcome = apply_judge_policy(
            verdict_or_none=None,
            consecutive_parse_failures=DEFAULT_JUDGE_PARSE_FAILURE_BUDGET + 5,
        )
        assert outcome["action"] == "stop"
        assert outcome["reason"] == "parse_failure_budget_exhausted"

    def test_outcome_is_dict_with_action_and_reason(self) -> None:
        outcome = apply_judge_policy(verdict_or_none=None, consecutive_parse_failures=0)
        assert "action" in outcome
        assert "reason" in outcome


# ---------------------------------------------------------------------------
# JudgeDecision (shadow result)
# ---------------------------------------------------------------------------


class TestJudgeDecision:
    def test_shadow_acted_false(self) -> None:
        verdict = JudgeVerdict(satisfied=True, raw="SATISFIED")
        decision = JudgeDecision(
            verdict=verdict,
            acted=False,
            failure_count=0,
            reason="shadow_mode",
        )
        assert decision.acted is False

    def test_live_acted_true(self) -> None:
        verdict = JudgeVerdict(satisfied=False, raw="NOT_SATISFIED")
        decision = JudgeDecision(
            verdict=verdict,
            acted=True,
            failure_count=0,
            reason="not_satisfied",
        )
        assert decision.acted is True

    def test_no_verdict_acted_false(self) -> None:
        decision = JudgeDecision(
            verdict=None,
            acted=False,
            failure_count=1,
            reason="parse_failure_fail_open",
        )
        assert decision.verdict is None
        assert decision.failure_count == 1

    def test_frozen(self) -> None:
        decision = JudgeDecision(
            verdict=None,
            acted=False,
            failure_count=0,
            reason="shadow_mode",
        )
        with pytest.raises((TypeError, ValidationError)):
            decision.acted = True  # type: ignore[misc]


# ---------------------------------------------------------------------------
# build_judge_evidence — redaction
# ---------------------------------------------------------------------------


class TestBuildJudgeEvidence:
    def test_evidence_does_not_contain_raw_goal(self) -> None:
        goal = "Write a comprehensive report on climate change policies"
        transcript = "User: have you finished? Agent: Almost done."
        verdict = JudgeVerdict(satisfied=False, raw="NOT_SATISFIED")
        evidence = build_judge_evidence(
            goal=goal,
            transcript_excerpt=transcript,
            verdict=verdict,
            failure_count=0,
        )
        # Raw strings must not appear in any field value
        fields_str = str(evidence.fields)
        assert goal not in fields_str
        assert transcript not in fields_str

    def test_evidence_stores_goal_hash(self) -> None:
        goal = "Summarize the quarterly earnings report"
        transcript = "Agent: I'll get started on that."
        verdict = JudgeVerdict(satisfied=True, raw="SATISFIED")
        evidence = build_judge_evidence(
            goal=goal,
            transcript_excerpt=transcript,
            verdict=verdict,
            failure_count=0,
        )
        expected_goal_hash = hashlib.sha256(goal.encode()).hexdigest()
        fields = dict(evidence.fields)
        assert fields.get("goalDigest") == f"sha256:{expected_goal_hash}"

    def test_evidence_stores_transcript_length_and_hash(self) -> None:
        goal = "Complete task X"
        transcript = "short transcript"
        verdict = JudgeVerdict(satisfied=False, raw="NOT_SATISFIED")
        evidence = build_judge_evidence(
            goal=goal,
            transcript_excerpt=transcript,
            verdict=verdict,
            failure_count=0,
        )
        fields = dict(evidence.fields)
        assert fields.get("transcriptLen") == len(transcript)
        expected_hash = hashlib.sha256(transcript.encode()).hexdigest()
        assert fields.get("transcriptDigest") == f"sha256:{expected_hash}"

    def test_evidence_stores_satisfied_and_failure_count(self) -> None:
        goal = "Do something"
        transcript = "done"
        verdict = JudgeVerdict(satisfied=True, raw="SATISFIED")
        evidence = build_judge_evidence(
            goal=goal,
            transcript_excerpt=transcript,
            verdict=verdict,
            failure_count=2,
        )
        fields = dict(evidence.fields)
        assert fields.get("satisfied") is True
        assert fields.get("failureCount") == 2

    def test_evidence_none_verdict(self) -> None:
        goal = "Do something"
        transcript = "hmm"
        evidence = build_judge_evidence(
            goal=goal,
            transcript_excerpt=transcript,
            verdict=None,
            failure_count=1,
        )
        fields = dict(evidence.fields)
        assert fields.get("satisfied") is None
        assert fields.get("failureCount") == 1

    def test_evidence_type_is_custom(self) -> None:
        goal = "goal"
        transcript = "transcript"
        verdict = JudgeVerdict(satisfied=False, raw="NOT_SATISFIED")
        evidence = build_judge_evidence(
            goal=goal,
            transcript_excerpt=transcript,
            verdict=verdict,
            failure_count=0,
        )
        assert evidence.type.startswith("custom:")


# ---------------------------------------------------------------------------
# GoalJudge Protocol (runtime_checkable)
# ---------------------------------------------------------------------------


class TestGoalJudgeProtocol:
    def test_protocol_is_runtime_checkable(self) -> None:
        # A fake implementing the method should pass isinstance check
        class FakeJudge:
            def judge(self, goal: str, transcript_excerpt: str) -> JudgeVerdict:
                return JudgeVerdict(satisfied=True, raw="SATISFIED")

        fake = FakeJudge()
        assert isinstance(fake, GoalJudge)

    def test_non_conforming_class_fails_isinstance(self) -> None:
        class NotAJudge:
            pass

        assert not isinstance(NotAJudge(), GoalJudge)


# ---------------------------------------------------------------------------
# Import boundary: no ADK at top level
# ---------------------------------------------------------------------------


class TestImportBoundary:
    def test_no_adk_top_level_import(self) -> None:
        code = (
            "import sys; "
            "import magi_agent.harness.goal_judge; "
            "mods = list(sys.modules.keys()); "
            "bad = [m for m in mods if 'google.adk' in m or 'adk_bridge' in m]; "
            "print(bad)"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "[]", (
            f"ADK leaked into top-level imports: {result.stdout.strip()}"
        )

    def test_no_adk_bridge_top_level_import(self) -> None:
        """goal_judge must not pull in adk_bridge or google.adk runner modules."""
        code = (
            "import importlib, sys; "
            "importlib.import_module('magi_agent.harness.goal_judge'); "
            "forbidden = ['magi_agent.adk_bridge.runner_adapter', "
            "             'magi_agent.adk_bridge.tool_adapter', "
            "             'magi_agent.transport.chat', "
            "             'magi_agent.tools.dispatcher']; "
            "bad = [m for m in sys.modules if any(m == f or m.startswith('google.adk') for f in forbidden)]; "
            "print(bad)"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "[]", (
            f"Forbidden modules leaked into top-level imports: {result.stdout.strip()}"
        )


# ---------------------------------------------------------------------------
# run_judge — budget boundary (Issue 1 TDD) and shadow/live gate (Issue 2)
# ---------------------------------------------------------------------------


class _AlwaysUnparseable:
    """Fake GoalJudge that always returns a raw string that parse_verdict cannot parse."""

    def judge(self, goal: str, transcript_excerpt: str) -> JudgeVerdict:
        return JudgeVerdict(satisfied=False, raw="<<no signal here>>")


class _AlwaysSatisfied:
    """Fake GoalJudge that always returns a clearly parseable SATISFIED verdict."""

    def judge(self, goal: str, transcript_excerpt: str) -> JudgeVerdict:
        return JudgeVerdict(satisfied=True, raw="SATISFIED")


class TestRunJudgeBudgetBoundary:
    """Issue 1 — STOP fires on exactly the Nth consecutive parse failure (not N+1).

    B3 threads the running consecutive_parse_failures count across calls.
    This class mirrors that caller pattern: each call passes the failure_count
    returned by the previous call so the accumulation is identical to production.
    """

    def test_stop_fires_on_nth_failure_not_n_plus_1(self) -> None:
        """With budget=3, the 3rd consecutive unparseable call must return action=stop."""
        judge = _AlwaysUnparseable()
        budget = DEFAULT_JUDGE_PARSE_FAILURE_BUDGET  # 3
        failure_count = 0

        for call_number in range(1, budget + 1):
            decision = run_judge(
                judge,
                goal="finish the task",
                transcript_excerpt="...",
                consecutive_parse_failures=failure_count,
                shadow=False,
            )
            failure_count = decision.failure_count
            if call_number < budget:
                # Calls 1 and 2: budget not yet exhausted — loop continues
                assert decision.reason == "parse_failure_fail_open", (
                    f"call {call_number}: expected fail_open, got {decision.reason}"
                )
            else:
                # Call 3 (= budget): must stop NOW, not on call 4
                assert decision.reason == "parse_failure_budget_exhausted", (
                    f"call {call_number}: expected budget_exhausted on exactly the "
                    f"{budget}th failure, got {decision.reason}"
                )

    def test_stop_does_not_fire_before_nth_failure(self) -> None:
        """With budget=3, calls 1 and 2 must continue (not stop early)."""
        judge = _AlwaysUnparseable()
        budget = DEFAULT_JUDGE_PARSE_FAILURE_BUDGET
        failure_count = 0
        for call_number in range(1, budget):  # calls 1 .. budget-1
            decision = run_judge(
                judge,
                goal="finish the task",
                transcript_excerpt="...",
                consecutive_parse_failures=failure_count,
                shadow=False,
            )
            failure_count = decision.failure_count
            assert decision.reason != "parse_failure_budget_exhausted", (
                f"call {call_number}: budget should NOT be exhausted yet "
                f"(fires on call {budget})"
            )

    def test_failure_count_increments_per_call(self) -> None:
        """Each unparseable call increments failure_count by exactly 1."""
        judge = _AlwaysUnparseable()
        failure_count = 0
        for expected_count in range(1, DEFAULT_JUDGE_PARSE_FAILURE_BUDGET + 1):
            decision = run_judge(
                judge,
                goal="task",
                transcript_excerpt="...",
                consecutive_parse_failures=failure_count,
                shadow=False,
            )
            failure_count = decision.failure_count
            assert failure_count == expected_count


class TestRunJudgeShadowGate:
    """Issue 2 — shadow=True sets acted=False; shadow=False (live) sets acted=True."""

    def test_shadow_true_acted_false(self) -> None:
        """Explicit shadow=True: verdict is observed but acted is False."""
        judge = _AlwaysSatisfied()
        decision = run_judge(
            judge,
            goal="finish the task",
            transcript_excerpt="Agent: Done.",
            consecutive_parse_failures=0,
            shadow=True,
        )
        assert decision.acted is False, (
            "shadow=True must set acted=False regardless of verdict"
        )
        # The verdict is still recorded (for audit)
        assert decision.verdict is not None
        assert decision.verdict.satisfied is True

    def test_shadow_false_acted_true(self) -> None:
        """Explicit shadow=False (live mode): acted is True."""
        judge = _AlwaysSatisfied()
        decision = run_judge(
            judge,
            goal="finish the task",
            transcript_excerpt="Agent: Done.",
            consecutive_parse_failures=0,
            shadow=False,
        )
        assert decision.acted is True, "shadow=False must set acted=True"
        assert decision.verdict is not None
        assert decision.verdict.satisfied is True

    def test_shadow_env_on_sets_acted_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """MAGI_GOAL_LOOP_JUDGE_SHADOW=1 (env default) → shadow mode → acted=False."""
        monkeypatch.setenv("MAGI_GOAL_LOOP_JUDGE_SHADOW", "1")
        judge = _AlwaysSatisfied()
        decision = run_judge(
            judge,
            goal="task",
            transcript_excerpt="done",
            consecutive_parse_failures=0,
            shadow=None,  # let env var decide
        )
        assert decision.acted is False

    def test_shadow_env_off_sets_acted_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """MAGI_GOAL_LOOP_JUDGE_SHADOW=0 → live mode → acted=True."""
        monkeypatch.setenv("MAGI_GOAL_LOOP_JUDGE_SHADOW", "0")
        judge = _AlwaysSatisfied()
        decision = run_judge(
            judge,
            goal="task",
            transcript_excerpt="done",
            consecutive_parse_failures=0,
            shadow=None,  # let env var decide
        )
        assert decision.acted is True
