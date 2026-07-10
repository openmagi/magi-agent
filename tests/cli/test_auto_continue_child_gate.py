"""Regression guard for the #1329 child auto-continue leak.

#1329 (feat(engine): ledger-first auto-continue) keyed ``auto_continue_enabled``
ONLY on ``is_goal_loop_enabled()`` inside ``build_headless_runtime``. Because a
SpawnAgent child builds its engine through the SAME wiring
(``child_runner_live`` -> ``build_headless_runtime``), the child engine also got
auto-continue authority. The child would answer its bounded subtask (e.g. "2"),
then the auto-continue loop fired a goal-completion self-check, the model replied
"Yes." / "The goal has been fully met.", and the child collector took that LAST
block as the child summary. The parent then received "Yes." instead of "2".

A SpawnAgent child is a bounded, single-objective delegated execution the PARENT
orchestrates. It must NOT auto-continue / self-check-goal. Auto-continue belongs
to the top-level (parent) turn only.

Fix: ``build_headless_runtime`` grows an explicit ``auto_continue_allowed``
parameter (default ``True`` = parent behaviour unchanged). ``child_runner_live``
sets it ``False``; the ``governed_turn._build_runtime`` fallback gates it on
``ctx.depth == 0`` so any depth>0 turn is also off. Auto-continue is then
``is_goal_loop_enabled() AND auto_continue_allowed``.
"""
from __future__ import annotations

from typing import Any

import pytest

from magi_agent.cli.wiring import build_headless_runtime


def test_parent_default_keeps_auto_continue_on_when_flag_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Top-level (parent) turn: flag ON + default param -> auto-continue ON.

    Kevin policy: parent turns keep ledger-first auto-continue default-ON. This
    is the unchanged-behaviour guard.
    """
    monkeypatch.setenv("MAGI_GOAL_LOOP_ENABLED", "1")
    rt = build_headless_runtime(
        cwd="/tmp",
        session_id="sid-parent",
        runner=object(),
    )
    assert rt.engine._auto_continue_enabled is True


def test_child_gate_forces_auto_continue_off_even_when_flag_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Child turn: flag ON but ``auto_continue_allowed=False`` -> OFF.

    The whole point of the fix: the env flag alone must NOT be able to re-enable
    auto-continue for a child build.
    """
    monkeypatch.setenv("MAGI_GOAL_LOOP_ENABLED", "1")
    rt = build_headless_runtime(
        cwd="/tmp",
        session_id="sid-child",
        runner=object(),
        auto_continue_allowed=False,
    )
    assert rt.engine._auto_continue_enabled is False


def test_child_gate_stays_off_when_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag OFF + child gate -> OFF (byte-identical historic behaviour)."""
    monkeypatch.setenv("MAGI_GOAL_LOOP_ENABLED", "0")
    rt = build_headless_runtime(
        cwd="/tmp",
        session_id="sid-child-off",
        runner=object(),
        auto_continue_allowed=False,
    )
    assert rt.engine._auto_continue_enabled is False


@pytest.mark.asyncio
async def test_governed_child_build_passes_auto_continue_allowed_false(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """``child_runner_live`` governed path MUST call build_headless_runtime with
    ``auto_continue_allowed=False`` regardless of MAGI_GOAL_LOOP_ENABLED.

    This is the seam the #1329 regression corrupted: the child engine inherited
    the parent's auto-continue authority through shared wiring.
    """
    import magi_agent.runtime.child_runner_live as crl
    from magi_agent.runtime.child_runner_boundary import ChildTaskRequest

    for name in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "absent.toml"))
    # Force the governed branch + assert it does NOT re-enable auto-continue.
    monkeypatch.setenv("MAGI_SUBAGENT_GOVERNED_TURN_ENABLED", "1")
    monkeypatch.setenv("MAGI_GOAL_LOOP_ENABLED", "1")

    captured: dict[str, Any] = {}

    def _fake_build_headless_runtime(**kwargs: Any) -> object:
        captured.update(kwargs)
        return object()

    async def _fake_collector(_stream: object, **_kw: object) -> tuple[str, tuple[str, ...], str, str | None]:
        return "2", ("evidence:calc",), "completed", None

    monkeypatch.setattr(
        "magi_agent.cli.wiring.build_headless_runtime",
        _fake_build_headless_runtime,
    )
    monkeypatch.setattr(
        "magi_agent.runtime.governed_turn.run_governed_turn",
        lambda *_a, **_kw: object(),
    )
    monkeypatch.setattr(
        "magi_agent.runtime.child_governed_collector.collect_governed_child_turn",
        _fake_collector,
    )

    from magi_agent.cli.providers import ProviderConfig

    child = crl.RealLocalChildRunner(
        provider_config=ProviderConfig(
            provider="anthropic", model="claude-sonnet-4-6", api_key="sk-test"
        ),
        runner=object(),
    )
    result = await child.run_child(
        ChildTaskRequest(
            parentExecutionId="parent-exec",
            turnId="turn-1",
            taskId="task-1",
            objective="Compute 1+1 and reply with the result.",
            role="general",
            delivery="return",
        )
    )
    assert result["status"] == "completed", dict(result)
    # The child summary must be the delegated answer, never a goal-completion
    # self-check echo ("Yes." / "goal met").
    assert "2" in str(result.get("summary", "")), dict(result)
    assert captured, "build_headless_runtime was never called on the governed path"
    assert captured.get("auto_continue_allowed") is False, (
        "governed child build must force auto_continue_allowed=False so the env "
        f"flag cannot re-enable child auto-continue. Got: {captured!r}"
    )


def test_build_runtime_fallback_gates_on_depth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``governed_turn._build_runtime`` (runtime=None fallback) gates auto
    continue on ``ctx.depth``: depth>0 (child) -> allowed False; depth==0
    (top-level serve turn) -> allowed True.
    """
    import magi_agent.runtime.governed_turn as gt
    from magi_agent.runtime.turn_context import TurnContext

    captured: dict[str, Any] = {}

    def _fake_build_headless_runtime(**kwargs: Any) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(
        "magi_agent.cli.wiring.build_headless_runtime",
        _fake_build_headless_runtime,
    )

    child_ctx = TurnContext(
        prompt="p", session_id="s", turn_id="t", depth=1
    )
    gt._build_runtime(child_ctx)
    assert captured.get("auto_continue_allowed") is False, captured

    captured.clear()
    top_ctx = TurnContext(
        prompt="p", session_id="s", turn_id="t", depth=0
    )
    gt._build_runtime(top_ctx)
    assert captured.get("auto_continue_allowed") is True, captured
