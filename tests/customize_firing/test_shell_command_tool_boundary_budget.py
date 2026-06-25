"""F-EXEC1 follow-up: tool-boundary shell_command helpers share the per-turn budget.

Before this change ``facades._maybe_apply_shell_command_before_tool`` and
``facades._maybe_apply_shell_command_after_tool`` invoked
``apply_shell_command_rule`` directly per-rule, bypassing
``shell_budget_for``. That left the tool-boundary firing path with NO
per-turn cap: an operator's "5 spawns / turn" cap was honored by the 9
turn / llm / compaction / pre-final slots but not by the two tool-boundary
slots, so a single noisy ``before_tool_use`` rule could spawn N processes
per tool call unbounded.

The fix mirrors the F-EXEC2 ``shell_check`` plumbing: a pair of
``lifecycle_audit.run_shell_command_at_{before,after}_tool_use`` fan-out
helpers that thread ``remaining_budget`` + ``decrement_fn`` through
``_run_shell_fan_out`` exactly like the 9 turn-boundary helpers. The
facades helpers then delegate, so every shell_command spawn (across all
11 slots) shares one ``shell_budget_for`` counter per ``(session,
turn)``.

This file pins two cross-slot regressions:

1. **The tool-boundary helpers exist and accept budget plumbing.** A
   non-importable helper or a signature drift would let the leak come
   back silently because nothing in production today imports these
   names.

2. **A 3-slot mix (turn-boundary + tool-boundary + tool-boundary) caps
   at the shared budget.** Two ``before_tool_use`` spawns plus one
   ``before_turn_start`` spawn with budget=2 must produce exactly 2
   ``executed`` audits and 1 ``budget_exhausted`` record at the third
   call.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from magi_agent.adk_bridge.lifecycle_shell_command_control import (
    reset_active_turn_identity,
    reset_shared_budget_for_tests,
    set_active_turn_identity,
    shell_budget_for,
)
from magi_agent.customize.lifecycle_audit import (
    run_shell_command_at_after_tool_use,
    run_shell_command_at_before_tool_use,
    run_shell_command_at_before_turn_start,
)
from magi_agent.customize.store import set_custom_rule

pytestmark = pytest.mark.skipif(
    sys.platform.startswith("win"),
    reason="shell_runner honest-degrades on Windows",
)


def _rule(*, rid: str, fires_at: str, action: str = "audit") -> dict:
    return {
        "id": rid,
        "scope": "always",
        "enabled": True,
        "firesAt": fires_at,
        "action": action,
        "what": {
            "kind": "shell_command",
            "payload": {
                "source": "inline",
                "inline": "echo ran",
                "timeout_seconds": 5,
                "shell": "bash",
            },
        },
    }


@pytest.fixture
def cfg_on(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("MAGI_CUSTOMIZE_SHELL_COMMAND_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    monkeypatch.delenv("MAGI_RUNTIME_PROFILE", raising=False)
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    reset_shared_budget_for_tests()
    return cfile


def test_run_shell_command_at_before_tool_use_returns_audits_and_verdict(
    cfg_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The helper exists, accepts budget plumbing, and returns ``(audits, verdict)``."""
    monkeypatch.setenv("MAGI_CUSTOMIZE_SHELL_AUDIT_BUDGET", "5")
    set_custom_rule(
        _rule(rid="cr_tb_bt", fires_at="before_tool_use"),
        path=cfg_on,
    )
    token = set_active_turn_identity("sess_tb_a", "turn_tb_a")
    try:
        remaining, decrement_fn = shell_budget_for()

        async def _drive() -> tuple[list[dict], str]:
            return await run_shell_command_at_before_tool_use(
                tool_name="bash",
                tool_args={"command": "ls"},
                remaining_budget=remaining,
                decrement_fn=decrement_fn,
            )

        audits, verdict = asyncio.run(_drive())
    finally:
        reset_active_turn_identity(token)

    assert verdict in {"proceed", "block"}
    assert audits, "expected at least one audit record"
    assert any(a.get("status") == "executed" for a in audits)


def test_run_shell_command_at_after_tool_use_is_audit_only(
    cfg_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``after_tool_use`` is audit-only — even a ``block`` rule must not flip the verdict."""
    monkeypatch.setenv("MAGI_CUSTOMIZE_SHELL_AUDIT_BUDGET", "5")
    set_custom_rule(
        _rule(rid="cr_tb_at", fires_at="after_tool_use", action="block"),
        path=cfg_on,
    )
    token = set_active_turn_identity("sess_tb_b", "turn_tb_b")
    try:
        remaining, decrement_fn = shell_budget_for()

        async def _drive() -> list[dict]:
            return await run_shell_command_at_after_tool_use(
                tool_name="bash",
                tool_output="hello",
                remaining_budget=remaining,
                decrement_fn=decrement_fn,
            )

        audits = asyncio.run(_drive())
    finally:
        reset_active_turn_identity(token)

    assert audits, "expected at least one audit record"
    assert any(a.get("status") == "executed" for a in audits)


def test_tool_boundary_slots_share_budget_with_turn_boundary_slots(
    cfg_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """budget=2 across 1 turn-boundary + 2 tool-boundary calls = 2 executed + 1 exhausted."""
    monkeypatch.setenv("MAGI_CUSTOMIZE_SHELL_AUDIT_BUDGET", "2")
    set_custom_rule(
        _rule(rid="cr_mix_t", fires_at="before_turn_start"),
        path=cfg_on,
    )
    set_custom_rule(
        _rule(rid="cr_mix_bt", fires_at="before_tool_use"),
        path=cfg_on,
    )
    set_custom_rule(
        _rule(rid="cr_mix_at", fires_at="after_tool_use"),
        path=cfg_on,
    )

    token = set_active_turn_identity("sess_mix", "turn_mix")
    try:
        async def _drive() -> tuple[int, int]:
            executed = 0
            exhausted = 0
            # Resolve budget per-call from the shared accessor exactly as
            # the facades / governed_turn callsites do — each successful
            # spawn decrements the shared counter for the next slot.
            for run_fn in (
                lambda: run_shell_command_at_before_turn_start(
                    prompt_text="hi",
                    remaining_budget=shell_budget_for()[0],
                    decrement_fn=shell_budget_for()[1],
                ),
                lambda: run_shell_command_at_before_tool_use(
                    tool_name="bash",
                    tool_args={"command": "ls"},
                    remaining_budget=shell_budget_for()[0],
                    decrement_fn=shell_budget_for()[1],
                ),
                lambda: run_shell_command_at_after_tool_use(
                    tool_name="bash",
                    tool_output="x",
                    remaining_budget=shell_budget_for()[0],
                    decrement_fn=shell_budget_for()[1],
                ),
            ):
                result = await run_fn()
                # before_tool_use returns (audits, verdict); the other two return audits.
                audits = result[0] if isinstance(result, tuple) else result
                for a in audits:
                    if a.get("status") == "executed":
                        executed += 1
                    elif a.get("status") == "budget_exhausted":
                        exhausted += 1
            return executed, exhausted

        executed_total, exhausted_total = asyncio.run(_drive())
    finally:
        reset_active_turn_identity(token)

    assert executed_total == 2, (
        f"expected exactly 2 spawns across the 3 slots when budget=2; got {executed_total}"
    )
    assert exhausted_total == 1, (
        f"expected one budget_exhausted record at the 3rd slot; got {exhausted_total}"
    )
