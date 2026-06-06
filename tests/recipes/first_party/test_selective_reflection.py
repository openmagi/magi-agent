"""Tests for the selective (complexity-gated) reflection capability.

Coverage:
  PR 1 — ComplexitySignal band classification (8 tests: counts 0,1,2,3,5,6,10,11)
  PR 1 — Gate uses general complexity signal, NOT a GAIA level field
  PR 2 — ReflectionPolicy.decide() (6 gate-decision tests + 1 immutability test)
  PR 3 — run_reflection_step() with FakeModelCaller (5 tests)
  PR 4 — build_reflection_hook_contribution() (4 tests)
  PR 4 — compose_hook_contributions ordering test
  PR 5 — GAIA harness integration with reflection_enabled=True
"""

from __future__ import annotations

import asyncio
import dataclasses
from typing import AsyncGenerator

import pytest
from google.adk.models import BaseLlm, LlmResponse
from google.genai import types

from magi_agent.benchmarks.gaia.dataset import GaiaQuestion
from magi_agent.benchmarks.gaia.harness import run_gaia_question
from magi_agent.recipes.first_party.selective_reflection.complexity_signal import (
    ComplexitySignal,
)
from magi_agent.recipes.first_party.selective_reflection.reflection_hook import (
    REFLECTION_HOOK_ID,
    build_reflection_hook_contribution,
)
from magi_agent.recipes.first_party.selective_reflection.reflection_policy import (
    ReflectionPolicy,
)
from magi_agent.recipes.first_party.selective_reflection.reflection_step import (
    parse_critique_response,
    run_reflection_step,
)
from magi_agent.recipes.hook_composition import HookContribution, compose_hook_contributions


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_policy(enabled: bool = True, **kwargs: object) -> ReflectionPolicy:
    return ReflectionPolicy(enabled=enabled, **kwargs)  # type: ignore[arg-type]


async def _fake_caller_pass(prompt: str) -> str:
    return "VERDICT: PASS\nThe answer is correct."


async def _fake_caller_issues(prompt: str) -> str:
    return (
        "VERDICT: ISSUES_FOUND\n"
        "ISSUES: The claim that X happened in 1999 is not supported by the source. "
        "The source states 2001."
    )


async def _fake_caller_no_verdict(prompt: str) -> str:
    return "I think the answer looks fine, no clear errors found."


# ---------------------------------------------------------------------------
# PR 1: ComplexitySignal band classification
# ---------------------------------------------------------------------------

class TestComplexitySignalBandClassification:
    """Verify band classification at key boundary values."""

    def test_tool_call_0_is_low(self) -> None:
        sig = ComplexitySignal.from_runtime(tool_call_count=0)
        assert sig.band == "low"
        assert sig.estimated_step_count == 0

    def test_tool_call_1_is_low(self) -> None:
        sig = ComplexitySignal.from_runtime(tool_call_count=1)
        assert sig.band == "low"

    def test_tool_call_2_is_low(self) -> None:
        # 2 == _LOW_MAX default → still "low"
        sig = ComplexitySignal.from_runtime(tool_call_count=2)
        assert sig.band == "low"

    def test_tool_call_3_is_medium(self) -> None:
        # 3 > _LOW_MAX=2, <= _MEDIUM_MAX=5
        sig = ComplexitySignal.from_runtime(tool_call_count=3)
        assert sig.band == "medium"

    def test_tool_call_5_is_medium(self) -> None:
        sig = ComplexitySignal.from_runtime(tool_call_count=5)
        assert sig.band == "medium"

    def test_tool_call_6_is_high(self) -> None:
        # 6 > _MEDIUM_MAX=5, <= _HIGH_MAX=10
        sig = ComplexitySignal.from_runtime(tool_call_count=6)
        assert sig.band == "high"

    def test_tool_call_10_is_high(self) -> None:
        sig = ComplexitySignal.from_runtime(tool_call_count=10)
        assert sig.band == "high"

    def test_tool_call_11_is_very_high(self) -> None:
        # 11 > _HIGH_MAX=10
        sig = ComplexitySignal.from_runtime(tool_call_count=11)
        assert sig.band == "very_high"

    def test_sub_goal_overrides_when_higher(self) -> None:
        # tool_call_count=1 (low) but sub_goal_count=11 (very_high)
        sig = ComplexitySignal.from_runtime(tool_call_count=1, sub_goal_count=11)
        assert sig.estimated_step_count == 11
        assert sig.band == "very_high"

    def test_estimated_step_count_is_max(self) -> None:
        sig = ComplexitySignal.from_runtime(tool_call_count=4, sub_goal_count=7)
        assert sig.estimated_step_count == 7

    def test_signal_is_frozen(self) -> None:
        sig = ComplexitySignal.from_runtime(tool_call_count=3)
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            sig.band = "low"  # type: ignore[misc]


class TestComplexitySignalNoGaiaField:
    """Critical: the gate predicate uses the general complexity signal, NOT a
    GAIA level field.  ComplexitySignal has no ``level`` attribute and must
    not accept one."""

    def test_complexity_signal_has_no_level_attribute(self) -> None:
        sig = ComplexitySignal.from_runtime(tool_call_count=5)
        assert not hasattr(sig, "level"), (
            "ComplexitySignal must not have a 'level' attribute — "
            "that would hardcode a GAIA benchmark concept into a general capability"
        )

    def test_band_derived_from_tool_count_not_level(self) -> None:
        # Construct with tool_call_count that maps to "medium" and verify
        # the band comes from the count, not any external label.
        sig = ComplexitySignal.from_runtime(tool_call_count=4)
        # 4 > LOW_MAX=2 and <= MEDIUM_MAX=5 → medium
        assert sig.band == "medium"
        # No GAIA-specific fields
        signal_fields = {f.name for f in dataclasses.fields(sig)}
        assert "level" not in signal_fields
        assert "gaia_level" not in signal_fields

    def test_same_count_produces_same_band_regardless_of_context(self) -> None:
        # The gate decision is purely a function of the count, not any task context.
        sig_a = ComplexitySignal.from_runtime(tool_call_count=6)  # "high"
        sig_b = ComplexitySignal.from_runtime(tool_call_count=6)  # same
        assert sig_a.band == sig_b.band == "high"


# ---------------------------------------------------------------------------
# PR 2: ReflectionPolicy.decide()
# ---------------------------------------------------------------------------

class TestReflectionPolicyDecide:
    def test_disabled_policy_always_skips_low(self) -> None:
        policy = _make_policy(enabled=False)
        assert policy.decide("low") == "skip"

    def test_disabled_policy_always_skips_medium(self) -> None:
        policy = _make_policy(enabled=False)
        assert policy.decide("medium") == "skip"

    def test_disabled_policy_always_skips_very_high(self) -> None:
        policy = _make_policy(enabled=False)
        assert policy.decide("very_high") == "skip"

    def test_enabled_low_returns_reflect(self) -> None:
        policy = _make_policy(enabled=True)
        assert policy.decide("low") == "reflect"

    def test_enabled_medium_returns_reflect(self) -> None:
        policy = _make_policy(enabled=True)
        assert policy.decide("medium") == "reflect"

    def test_enabled_high_returns_bounded_reflect(self) -> None:
        policy = _make_policy(enabled=True)
        assert policy.decide("high") == "bounded_reflect"

    def test_enabled_very_high_returns_skip(self) -> None:
        policy = _make_policy(enabled=True)
        assert policy.decide("very_high") == "skip"

    def test_policy_is_frozen(self) -> None:
        policy = _make_policy(enabled=True)
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            policy.enabled = False  # type: ignore[misc]

    def test_effective_max_depth_reflect(self) -> None:
        policy = ReflectionPolicy(enabled=True, max_depth=2)
        assert policy.effective_max_depth("reflect") == 2

    def test_effective_max_depth_bounded_reflect(self) -> None:
        policy = ReflectionPolicy(enabled=True, max_depth=2, high_complexity_max_depth=1)
        assert policy.effective_max_depth("bounded_reflect") == 1

    def test_effective_max_depth_skip(self) -> None:
        policy = _make_policy(enabled=True)
        assert policy.effective_max_depth("skip") == 0


# ---------------------------------------------------------------------------
# PR 3: run_reflection_step() with FakeModelCaller
# ---------------------------------------------------------------------------

class TestRunReflectionStep:
    def test_pass_verdict_produces_no_injection(self) -> None:
        policy = _make_policy(enabled=True)
        result = asyncio.run(
            run_reflection_step(
                draft_answer="Paris",
                tool_call_summary="Called SearchWeb once",
                policy=policy,
                band="low",
                depth=1,
                model_caller=_fake_caller_pass,
            )
        )
        assert result.verdict == "pass"
        assert result.hidden_user_message == ""
        assert result.issues_summary == ""

    def test_issues_verdict_produces_non_empty_hidden_message(self) -> None:
        policy = _make_policy(enabled=True)
        result = asyncio.run(
            run_reflection_step(
                draft_answer="The event happened in 1999",
                tool_call_summary="Called SearchWeb once, result said 2001",
                policy=policy,
                band="low",
                depth=1,
                model_caller=_fake_caller_issues,
            )
        )
        assert result.verdict == "issues_found"
        assert result.issues_summary != ""
        assert result.hidden_user_message != ""
        assert "corrected" in result.hidden_user_message.lower()

    def test_max_depth_enforcement_returns_pass(self) -> None:
        policy = ReflectionPolicy(enabled=True, max_depth=1)
        # depth=2 > max_depth=1 → should skip the LLM call and return pass
        result = asyncio.run(
            run_reflection_step(
                draft_answer="Some draft",
                tool_call_summary="Called tool once",
                policy=policy,
                band="low",
                depth=2,  # beyond max_depth=1
                model_caller=_fake_caller_issues,  # would return issues if called
            )
        )
        assert result.verdict == "pass"
        assert result.hidden_user_message == ""

    def test_no_verdict_in_response_treated_as_pass(self) -> None:
        policy = _make_policy(enabled=True)
        result = asyncio.run(
            run_reflection_step(
                draft_answer="Some draft",
                tool_call_summary="Called tool once",
                policy=policy,
                band="low",
                depth=1,
                model_caller=_fake_caller_no_verdict,
            )
        )
        assert result.verdict == "pass"
        assert result.hidden_user_message == ""

    def test_skip_decision_returns_pass_without_model_call(self) -> None:
        # very_high band → policy.decide() = "skip" → no LLM call
        policy = _make_policy(enabled=True)
        called: list[str] = []

        async def spy_caller(prompt: str) -> str:
            called.append(prompt)
            return "VERDICT: ISSUES_FOUND\nISSUES: fake issue"

        result = asyncio.run(
            run_reflection_step(
                draft_answer="Some draft",
                tool_call_summary="Many tools",
                policy=policy,
                band="very_high",
                depth=1,
                model_caller=spy_caller,
            )
        )
        assert result.verdict == "pass"
        assert called == [], "model_caller must not be invoked when decision is 'skip'"

    def test_disabled_policy_returns_pass_without_model_call(self) -> None:
        policy = _make_policy(enabled=False)
        called: list[str] = []

        async def spy_caller(prompt: str) -> str:
            called.append(prompt)
            return "VERDICT: ISSUES_FOUND\nISSUES: fake issue"

        result = asyncio.run(
            run_reflection_step(
                draft_answer="Some draft",
                tool_call_summary="Called tool once",
                policy=policy,
                band="low",
                depth=1,
                model_caller=spy_caller,
            )
        )
        assert result.verdict == "pass"
        assert called == [], "model_caller must not be invoked when policy is disabled"


class TestParseCritiqueResponse:
    def test_parse_pass_verdict(self) -> None:
        verdict, issues = parse_critique_response("VERDICT: PASS\nAll good.")
        assert verdict == "pass"
        assert issues == ""

    def test_parse_issues_found_verdict(self) -> None:
        raw = "VERDICT: ISSUES_FOUND\nISSUES: The date is wrong."
        verdict, issues = parse_critique_response(raw)
        assert verdict == "issues_found"
        assert "date" in issues.lower()

    def test_parse_missing_verdict_defaults_to_pass(self) -> None:
        verdict, issues = parse_critique_response("The answer looks fine.")
        assert verdict == "pass"
        assert issues == ""

    def test_parse_issues_found_with_empty_body_defaults_to_pass(self) -> None:
        # ISSUES_FOUND with no actual issues text → conservative fallback to pass
        verdict, issues = parse_critique_response("VERDICT: ISSUES_FOUND\n")
        assert verdict == "pass"
        assert issues == ""


# ---------------------------------------------------------------------------
# PR 4: build_reflection_hook_contribution()
# ---------------------------------------------------------------------------

class TestBuildReflectionHookContribution:
    def test_disabled_policy_returns_none(self) -> None:
        policy = ReflectionPolicy(enabled=False)
        result = build_reflection_hook_contribution(policy=policy)
        assert result is None

    def test_enabled_policy_returns_hook_contribution(self) -> None:
        policy = ReflectionPolicy(enabled=True)
        result = build_reflection_hook_contribution(policy=policy)
        assert isinstance(result, HookContribution)

    def test_hook_has_correct_stage_beforecommit(self) -> None:
        policy = ReflectionPolicy(enabled=True)
        hook = build_reflection_hook_contribution(policy=policy)
        assert hook is not None
        assert hook.stage == "beforeCommit"

    def test_hook_has_priority_25(self) -> None:
        policy = ReflectionPolicy(enabled=True)
        hook = build_reflection_hook_contribution(policy=policy)
        assert hook is not None
        assert hook.priority == 25

    def test_hook_is_fail_open_non_blocking(self) -> None:
        policy = ReflectionPolicy(enabled=True)
        hook = build_reflection_hook_contribution(policy=policy)
        assert hook is not None
        assert hook.blocking is False
        assert hook.failure_mode == "fail_open"

    def test_hook_has_correct_id(self) -> None:
        policy = ReflectionPolicy(enabled=True)
        hook = build_reflection_hook_contribution(policy=policy)
        assert hook is not None
        assert hook.hook_id == REFLECTION_HOOK_ID


def _make_proof_verifier_hook() -> HookContribution:
    """Build a fake proof-verifier hook at priority 30 (before-commit stage)."""
    payload: dict[str, object] = {
        "recipeRef": "magi.research.proof-verifier",
        "hookId": "magi.proof-verifier",
        "stage": "beforeCommit",
        "priority": 30,
        "scope": ("all",),
        "idempotencyKey": None,
        "blocking": False,
        "failureMode": "fail_open",
        "sideEffectful": False,
        "securityCritical": False,
        "privateConfig": {},
    }
    payload["contributionDigest"] = HookContribution.compute_contribution_digest(payload)
    return HookContribution._from_registry_contribution(payload)


class TestHookCompositionOrdering:
    def test_reflection_hook_appears_before_proof_verifier(self) -> None:
        policy = ReflectionPolicy(enabled=True)
        reflection_hook = build_reflection_hook_contribution(policy=policy)
        proof_verifier_hook = _make_proof_verifier_hook()
        assert reflection_hook is not None

        contract = compose_hook_contributions([reflection_hook, proof_verifier_hook])

        assert contract.blocked is False
        assert len(contract.hooks) == 2

        hook_ids = [hook.hook_id for hook in contract.hooks]
        assert hook_ids.index(REFLECTION_HOOK_ID) < hook_ids.index("magi.proof-verifier"), (
            "reflection hook (priority=25) must appear before proof verifier (priority=30)"
        )


# ---------------------------------------------------------------------------
# PR 5: GAIA harness integration
# ---------------------------------------------------------------------------

class _ReflectionAwareLlm(BaseLlm):
    """Scripted LLM that simulates: first call returns a draft, critique finds
    issues, second call returns a corrected answer.

    Call sequence:
      1. Main agent turn → draft answer "The event happened in 1999"
      2. Reflection critique call → ISSUES_FOUND (date is wrong)
      3. (In real flow) second agent turn → corrected answer; we skip that here
         and just verify the harness does not crash when reflection_enabled=True.
    """

    def __init__(self, model: str = "fake") -> None:
        super().__init__(model=model)
        self._call_count = 0

    async def generate_content_async(
        self, llm_request: object, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        self._call_count += 1
        # Always return a final answer so the harness can extract it.
        yield LlmResponse(
            content=types.Content(
                role="model",
                parts=[types.Part(text="Some reasoning.\nFINAL ANSWER: Paris")],
            )
        )


def test_gaia_harness_runs_with_reflection_enabled(tmp_path) -> None:
    """Integration smoke test: harness does not raise when reflection_enabled=True.

    The reflection step is wired in but the LLM model is fake, so no real
    provider traffic occurs.  We verify:
    1. The harness returns a non-empty answer.
    2. No exception is raised.
    """
    q = GaiaQuestion(
        task_id="reflect-smoke",
        question="What is the capital of France?",
        level=1,
        final_answer="Paris",
    )
    answer = run_gaia_question(
        q,
        workspace_root=str(tmp_path),
        model_factory=lambda cfg: _ReflectionAwareLlm(model="fake"),
        reflection_enabled=True,
    )
    assert answer == "Paris"


def test_gaia_harness_reflection_disabled_by_default(tmp_path) -> None:
    """Default harness call (no reflection_enabled) works unchanged."""
    q = GaiaQuestion(
        task_id="no-reflect-default",
        question="What is the capital of France?",
        level=1,
        final_answer="Paris",
    )

    class _SimpleLlm(BaseLlm):
        async def generate_content_async(
            self, llm_request: object, stream: bool = False
        ) -> AsyncGenerator[LlmResponse, None]:
            yield LlmResponse(
                content=types.Content(
                    role="model",
                    parts=[types.Part(text="FINAL ANSWER: Paris")],
                )
            )

    answer = run_gaia_question(
        q,
        workspace_root=str(tmp_path),
        model_factory=lambda cfg: _SimpleLlm(model="fake"),
    )
    assert answer == "Paris"
