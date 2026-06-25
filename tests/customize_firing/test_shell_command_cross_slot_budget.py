"""F-EXEC1 cross-slot budget integration test.

Drives the SHARED per-(session, turn) shell-command budget across multiple
lifecycle slots and asserts that the Nth + 1 spawn — across DIFFERENT slots,
not just within one — short-circuits at the next slot's accessor.

Why this test exists
--------------------
A reviewer flagged that an earlier draft kept a per-CALL counter inside
``_run_shell_fan_out`` only, meaning the budget reset to ``DEFAULT`` on each
new slot's invocation. With 5 spawn cap and 3 slots each authoring one rule,
the operator's intended "5 spawns per turn" would actually permit 15
spawns. This test simulates 3 turn boundaries (before_turn_start,
on_user_prompt_submit, after_turn_end) each authoring one ``echo``-shaped
rule with the budget set to 2 and asserts only 2 subprocess spawns occur in
total across the three slots — the 3rd slot must receive a
``budget_exhausted`` audit record without invoking the runner.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from magi_agent.adk_bridge.lifecycle_shell_command_control import (
    DEFAULT_SHELL_COMMAND_BUDGET,
    reset_active_turn_identity,
    reset_shared_budget_for_tests,
    set_active_turn_identity,
    shell_budget_for,
)
from magi_agent.customize.lifecycle_audit import (
    run_shell_command_at_after_turn_end,
    run_shell_command_at_before_turn_start,
    run_shell_command_at_on_user_prompt_submit,
)
from magi_agent.customize.store import set_custom_rule

pytestmark = pytest.mark.skipif(
    sys.platform.startswith("win"),
    reason="shell_runner honest-degrades on Windows",
)


def _rule(*, rid: str, fires_at: str, inline: str = "echo ran") -> dict:
    return {
        "id": rid,
        "scope": "always",
        "enabled": True,
        "firesAt": fires_at,
        "action": "audit",
        "what": {
            "kind": "shell_command",
            "payload": {
                "source": "inline",
                "inline": inline,
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
    # Reset the process-wide shared budget map so back-to-back test runs do
    # not see each other's (session, turn) state.
    reset_shared_budget_for_tests()
    return cfile


def _author_three_slots(cfg_path: Path) -> None:
    set_custom_rule(
        _rule(rid="cr_cross_slot_a", fires_at="before_turn_start"),
        path=cfg_path,
    )
    set_custom_rule(
        _rule(rid="cr_cross_slot_b", fires_at="on_user_prompt_submit"),
        path=cfg_path,
    )
    set_custom_rule(
        _rule(rid="cr_cross_slot_c", fires_at="after_turn_end"),
        path=cfg_path,
    )


def test_shell_budget_for_returns_none_without_identity(cfg_on: Path) -> None:
    """No active identity ⇒ ``remaining=None`` ⇒ no cap (byte-identical)."""
    remaining, decrement = shell_budget_for()
    assert remaining is None
    # No-op decrement must be callable without raising.
    decrement()


def test_shell_budget_for_returns_default_with_identity(
    cfg_on: Path,
) -> None:
    """Active identity ⇒ ``remaining == DEFAULT_SHELL_COMMAND_BUDGET``."""
    token = set_active_turn_identity("sess_a", "turn_a")
    try:
        remaining, _ = shell_budget_for()
        assert remaining == DEFAULT_SHELL_COMMAND_BUDGET
    finally:
        reset_active_turn_identity(token)


def test_combined_budget_across_slots_caps_at_default(
    cfg_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """3 slots × 1 rule each, budget=2 ⇒ only 2 spawns total, 3rd budget_exhausted."""
    # Cap the per-turn budget tight so the test can observe the cross-slot
    # short-circuit without spawning many subprocesses.
    monkeypatch.setenv("MAGI_CUSTOMIZE_SHELL_AUDIT_BUDGET", "2")
    _author_three_slots(cfg_on)

    token = set_active_turn_identity("sess_cross", "turn_cross")
    executed_total = 0
    budget_exhausted_total = 0
    try:
        async def _drive() -> tuple[int, int]:
            executed = 0
            exhausted = 0
            # Each fan-out resolves remaining + decrement_fn from the SHARED
            # budget so a successful spawn in slot N decrements the counter
            # for slot N + 1. The 3rd slot must see remaining==0 and emit a
            # single budget_exhausted record without invoking the runner.
            for run_fn in (
                lambda: run_shell_command_at_before_turn_start(
                    prompt_text="hi",
                    remaining_budget=shell_budget_for()[0],
                    decrement_fn=shell_budget_for()[1],
                ),
                lambda: run_shell_command_at_on_user_prompt_submit(
                    prompt_text="hi",
                    remaining_budget=shell_budget_for()[0],
                    decrement_fn=shell_budget_for()[1],
                ),
                lambda: run_shell_command_at_after_turn_end(
                    final_text="bye",
                    remaining_budget=shell_budget_for()[0],
                    decrement_fn=shell_budget_for()[1],
                ),
            ):
                audits = await run_fn()
                for a in audits:
                    if a.get("status") == "executed":
                        executed += 1
                    elif a.get("status") == "budget_exhausted":
                        exhausted += 1
            return executed, exhausted

        executed_total, budget_exhausted_total = asyncio.run(_drive())
    finally:
        reset_active_turn_identity(token)

    # 2 successful spawns total across the 3 slots; 3rd slot short-circuits.
    assert executed_total == 2, (
        f"expected exactly 2 spawns across 3 slots when budget=2; got {executed_total}"
    )
    assert budget_exhausted_total == 1, (
        f"expected one budget_exhausted record at the 3rd slot; got {budget_exhausted_total}"
    )


def test_decrement_is_observable_via_subsequent_shell_budget_for(
    cfg_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After one successful spawn the SAME (session, turn) sees remaining-1."""
    monkeypatch.setenv("MAGI_CUSTOMIZE_SHELL_AUDIT_BUDGET", "4")
    set_custom_rule(
        _rule(rid="cr_decr_obs", fires_at="before_turn_start"),
        path=cfg_on,
    )

    token = set_active_turn_identity("sess_decr", "turn_decr")
    try:
        remaining_pre, _ = shell_budget_for()
        assert remaining_pre == 4

        async def _drive() -> None:
            remaining, decrement_fn = shell_budget_for()
            await run_shell_command_at_before_turn_start(
                prompt_text="x",
                remaining_budget=remaining,
                decrement_fn=decrement_fn,
            )

        asyncio.run(_drive())

        # Re-resolve: the shared counter must have decremented by exactly 1.
        remaining_post, _ = shell_budget_for()
        assert remaining_post == 3
    finally:
        reset_active_turn_identity(token)


def test_distinct_turn_ids_have_independent_budgets(
    cfg_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(session, turn) keying ⇒ separate turns get separate counters."""
    monkeypatch.setenv("MAGI_CUSTOMIZE_SHELL_AUDIT_BUDGET", "2")
    set_custom_rule(
        _rule(rid="cr_iso", fires_at="before_turn_start"),
        path=cfg_on,
    )

    async def _drive(session_id: str, turn_id: str) -> int:
        token = set_active_turn_identity(session_id, turn_id)
        try:
            remaining, decrement_fn = shell_budget_for()
            audits = await run_shell_command_at_before_turn_start(
                prompt_text="x",
                remaining_budget=remaining,
                decrement_fn=decrement_fn,
            )
            return sum(1 for a in audits if a.get("status") == "executed")
        finally:
            reset_active_turn_identity(token)

    executed_t1 = asyncio.run(_drive("sess_iso", "turn_1"))
    executed_t2 = asyncio.run(_drive("sess_iso", "turn_2"))
    assert executed_t1 == 1
    assert executed_t2 == 1

    # Both turns should still have 1 remaining (started at 2, spent 1 each).
    token1 = set_active_turn_identity("sess_iso", "turn_1")
    try:
        remaining_1, _ = shell_budget_for()
    finally:
        reset_active_turn_identity(token1)
    token2 = set_active_turn_identity("sess_iso", "turn_2")
    try:
        remaining_2, _ = shell_budget_for()
    finally:
        reset_active_turn_identity(token2)
    assert remaining_1 == 1
    assert remaining_2 == 1


def test_shell_budget_for_off_flag_returns_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Master flag OFF ⇒ ``remaining=None`` even with identity set."""
    monkeypatch.setenv("MAGI_CUSTOMIZE_SHELL_COMMAND_ENABLED", "0")
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    reset_shared_budget_for_tests()
    token = set_active_turn_identity("sess_off", "turn_off")
    try:
        remaining, decrement = shell_budget_for()
        assert remaining is None
        # No-op decrement remains callable without raising.
        decrement()
    finally:
        reset_active_turn_identity(token)
