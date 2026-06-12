"""C2.1 — LoopContinuationPolicy seam on build_after_turn_goal_loop_hook.

The continue/stop decision becomes a swappable callable (policy); the
after-turn hook + fail-open plumbing + decision_sink delivery stay kernel.
Dual-load: ``policy=None`` keeps the legacy ``decide_loop_continuation``
behavior byte-identical (proven by the C2.0 golden oracle + the default test
below).
"""
from __future__ import annotations

from magi_agent.harness.goal_loop_control import (
    LoopControlInput,
    LoopControlResult,
    build_after_turn_goal_loop_hook,
)
from magi_agent.harness.goal_state import GoalState, InMemoryGoalStateStore
from magi_agent.hooks.context import HookContext


class _FakeInput:  # sentinel passed straight to the injected policy
    pass


class _NeverJudge:
    def judge(self, goal: str, transcript_excerpt: str):  # pragma: no cover
        raise AssertionError("judge must not run for a disabled loop")


class _Probe:
    def is_capped(self) -> bool:
        return False


def _stop_result() -> LoopControlResult:
    return LoopControlResult(
        decision="stop",
        reason="disabled",
        goalStateAfter=GoalState(goal="g", sessionId="s1"),
        consecutiveParseFailuresAfter=0,
    )


def _ctx() -> HookContext:
    return HookContext(botId="b1", sessionId="s1", turnId="t1")


def test_hook_routes_through_injected_loop_policy() -> None:
    calls: list[str] = []

    def custom_policy(loop_input):
        calls.append("custom")
        assert isinstance(loop_input, _FakeInput)
        return _stop_result()

    seen: list[LoopControlResult] = []
    _manifest, handler = build_after_turn_goal_loop_hook(
        # The policy is the consumer of the provider's value, so a sentinel
        # (not a real LoopControlInput) suffices for THIS seam test.
        input_provider=lambda ctx: _FakeInput(),
        decision_sink=seen.append,
        policy=custom_policy,
    )
    result = handler(_ctx())
    assert calls == ["custom"]
    assert seen and seen[0].reason == "disabled"
    assert result.action == "continue"


def test_policy_none_defaults_to_first_party_decide(monkeypatch) -> None:
    """Dual-load: no injected policy -> the legacy decide_loop_continuation
    path runs (enabled=False short-circuits to reason 'disabled')."""
    store = InMemoryGoalStateStore()
    store.set_goal("s1", "ship it", max_turns=3)
    loop_input = LoopControlInput(
        store=store,
        judge=_NeverJudge(),
        sessionId="s1",
        transcriptExcerpt="step",
        spendProbe=_Probe(),
        enabled=False,
    )
    seen: list[LoopControlResult] = []
    _manifest, handler = build_after_turn_goal_loop_hook(
        input_provider=lambda ctx: loop_input,
        decision_sink=seen.append,
    )
    result = handler(_ctx())
    assert seen and seen[0].decision == "stop"
    assert seen[0].reason == "disabled"
    assert result.reason == "goal_loop:stop:disabled"


def test_policy_error_is_fail_open() -> None:
    """A raising injected policy must not break the turn (kernel plumbing)."""

    def broken_policy(loop_input):
        raise RuntimeError("policy exploded")

    _manifest, handler = build_after_turn_goal_loop_hook(
        input_provider=lambda ctx: _FakeInput(),
        policy=broken_policy,
    )
    result = handler(_ctx())
    assert result.action == "continue"
    assert result.reason == "goal_loop_decision_error"
