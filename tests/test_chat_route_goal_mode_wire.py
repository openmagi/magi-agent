"""PR-B integration: ``_local_adk_chat_sse`` parses ``goalMode`` from the
chat-completions payload and publishes the resulting :class:`GoalLoopPolicy`
on the per-turn ContextVar that PR-C's clean-break judge call will read.

Mirrors the per-turn ``reasoningEffort`` wiring (PR2b/c) — same try/finally
shape, same ContextVar pattern — so the engine sees the policy at exactly
the same scope the live LiteLlm build does.

PR-B is intentionally NO-OP for the engine: this test asserts the ContextVar
is populated DURING ``build_headless_runtime`` (the call inside the try
block) and reset AFTER. PR-C will gate the actual judge call on it.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from magi_agent.cli.contracts import EngineResult, Terminal


_PROVIDER_KEYS = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "FIREWORKS_API_KEY",
    "MAGI_PROVIDER",
    "MAGI_MODEL",
    "MAGI_GOAL_LOOP_ENABLED",
    "MAGI_GOAL_LOOP_MAX_TURNS",
)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    for name in _PROVIDER_KEYS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "absent.toml"))
    monkeypatch.setenv("MAGI_AGENT_WORKSPACE", str(tmp_path))


class _FakeEngine:
    async def run_turn_stream(self, runtime, turn_input, *, cancel, gate):  # noqa: ANN001
        yield EngineResult(terminal=Terminal.completed, session_id="s", turn_id="t")


async def _drain(gen) -> str:  # noqa: ANN001
    return "".join([chunk async for chunk in gen])


def _runtime() -> SimpleNamespace:
    return SimpleNamespace(config=SimpleNamespace(model="anthropic/claude"))


@pytest.mark.asyncio
async def test_goal_mode_true_with_master_flag_publishes_policy_during_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_GOAL_LOOP_ENABLED", "1")

    import magi_agent.cli.wiring as wiring
    from magi_agent.transport import chat as chat_mod

    captured: dict[str, object] = {}

    def _fake_build(**kwargs: object) -> SimpleNamespace:
        # build_headless_runtime is invoked INSIDE the try block where the
        # ContextVar has just been set. The engine (PR-C) will read at the
        # same scope, so this is the right place to capture.
        from magi_agent.runtime.per_turn_goal_loop_context import (
            current_per_turn_goal_loop_policy,
        )

        captured["policy_during_build"] = current_per_turn_goal_loop_policy()
        captured["build_kwargs"] = kwargs
        return SimpleNamespace(engine=_FakeEngine(), gate=None)

    monkeypatch.setattr(wiring, "build_headless_runtime", _fake_build)
    monkeypatch.setattr(
        wiring, "local_runner_policy_routing_enabled_from_env", lambda: False
    )

    payload = {
        "sessionId": "s",
        "turnId": "t",
        "goalMode": True,
    }
    out = await _drain(
        chat_mod._local_adk_chat_sse(_runtime(), payload, "Multi-step Tesla report")
    )
    assert out.rstrip().endswith("data: [DONE]")

    policy = captured.get("policy_during_build")
    assert policy is not None, "ContextVar must be populated inside build_headless_runtime"
    assert getattr(policy, "enabled", False) is True
    assert getattr(policy, "objective", "") == "Multi-step Tesla report"

    # Reset must have happened in the finally — the post-turn read sees None.
    from magi_agent.runtime.per_turn_goal_loop_context import (
        current_per_turn_goal_loop_policy,
    )
    assert current_per_turn_goal_loop_policy() is None


@pytest.mark.asyncio
async def test_goal_mode_missing_keeps_context_var_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Default Phase-1 opt-in: a payload without goalMode must leave the
    # ContextVar at None (byte-identical to pre-PR-B chat-route behavior).
    monkeypatch.setenv("MAGI_GOAL_LOOP_ENABLED", "1")

    import magi_agent.cli.wiring as wiring
    from magi_agent.transport import chat as chat_mod

    captured: dict[str, object] = {}

    def _fake_build(**kwargs: object) -> SimpleNamespace:
        from magi_agent.runtime.per_turn_goal_loop_context import (
            current_per_turn_goal_loop_policy,
        )

        captured["policy_during_build"] = current_per_turn_goal_loop_policy()
        return SimpleNamespace(engine=_FakeEngine(), gate=None)

    monkeypatch.setattr(wiring, "build_headless_runtime", _fake_build)
    monkeypatch.setattr(
        wiring, "local_runner_policy_routing_enabled_from_env", lambda: False
    )

    out = await _drain(
        chat_mod._local_adk_chat_sse(
            _runtime(), {"sessionId": "s", "turnId": "t"}, "hello"
        )
    )
    assert out.rstrip().endswith("data: [DONE]")
    assert captured.get("policy_during_build") is None


@pytest.mark.asyncio
async def test_goal_mode_true_but_master_flag_off_does_not_publish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Master kill-switch must override the per-send toggle. The operator
    # controls rollout; an enthusiastic client cannot enable goal-loop on a
    # deployment that has not flipped MAGI_GOAL_LOOP_ENABLED.
    monkeypatch.setenv("MAGI_GOAL_LOOP_ENABLED", "0")

    import magi_agent.cli.wiring as wiring
    from magi_agent.transport import chat as chat_mod

    captured: dict[str, object] = {}

    def _fake_build(**kwargs: object) -> SimpleNamespace:
        from magi_agent.runtime.per_turn_goal_loop_context import (
            current_per_turn_goal_loop_policy,
        )

        captured["policy_during_build"] = current_per_turn_goal_loop_policy()
        return SimpleNamespace(engine=_FakeEngine(), gate=None)

    monkeypatch.setattr(wiring, "build_headless_runtime", _fake_build)
    monkeypatch.setattr(
        wiring, "local_runner_policy_routing_enabled_from_env", lambda: False
    )

    out = await _drain(
        chat_mod._local_adk_chat_sse(
            _runtime(),
            {"sessionId": "s", "turnId": "t", "goalMode": True},
            "Multi-step Tesla report",
        )
    )
    assert out.rstrip().endswith("data: [DONE]")
    assert captured.get("policy_during_build") is None
