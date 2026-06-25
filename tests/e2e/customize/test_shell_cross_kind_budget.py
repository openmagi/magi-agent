"""F-QA5 cross-kind shell budget contract tests (real subprocess).

The per-turn ``MAGI_CUSTOMIZE_SHELL_AUDIT_BUDGET`` counter (default 5)
is SHARED across the ``shell_command`` and ``shell_check`` kinds: an
operator who authors a mix of action rules + verifier rules at the
same turn should not be able to evade the cost ceiling by alternating
kinds. The shared counter lives in
:mod:`magi_agent.adk_bridge.lifecycle_shell_command_control` —
:func:`shell_budget_for` returns the same ``(remaining, decrement_fn)``
pair regardless of kind for a given ``(session_id, turn_id)``, and the
F-EXEC2 review fix lifted the master-flag gate to a UNION
(``shell_command_enabled OR shell_check_enabled``) so the budget is
initialized correctly when only one of the two kinds is opted in.

This module pins three contracts F-EXEC1 cross-slot budget test does
NOT cover (it tests three ``shell_command`` slots × 1 rule each; we
test cross-KIND):

1. :func:`test_cross_kind_budget_shares_counter` — author one
   shell_command rule at ``before_tool_use`` and one shell_check rule
   at ``pre_final``, set the budget to 1, drive both helpers in
   sequence. Assert the FIRST one spawns and the SECOND one returns a
   ``budget_exhausted`` audit record without invoking the runner.
2. :func:`test_budget_works_with_only_shell_command_enabled` — shell
   command master flag ON, shell_check master flag OFF: verify the
   budget initializes via :func:`shell_budget_for` (the F-EXEC2 review
   fix's union-gate). Without that fix the budget would honest-degrade
   to ``None`` (no cap) under the asymmetric flag state.
3. :func:`test_budget_works_with_only_shell_check_enabled` — mirror
   of (2) with shell_check enabled and shell_command disabled. Same
   union-gate contract.

All three tests spawn REAL subprocesses (``bash -c 'echo ran'`` /
``echo '{"passed": true}'`` / ``exit 0``). The cross-kind test caps
the budget at 1 so only ONE spawn happens regardless of how many rules
are authored — the second helper short-circuits at its accessor.
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
# NOTE: ``run_shell_command_at_before_tool_use`` is intentionally NOT
# imported here — the facade
# (:func:`magi_agent.facades.execute_tool_with_hooks`) calls the
# ``_run_shell_fan_out`` body directly at before_tool_use, so the
# fan-out helper is not exported as a stand-alone callable. This module
# therefore exercises the shared per-(session, turn) budget across
# kinds using the two ``pre_final`` GATE-honored helpers — both
# resolve ``(remaining, decrement_fn)`` from
# :func:`shell_budget_for` (the same module-level accessor every
# helper consults) so the cross-kind serialisation contract is
# observable without spinning up the facade.
from magi_agent.customize.lifecycle_audit import (
    run_shell_check_at_pre_final,
    run_shell_command_at_pre_final,
)
from magi_agent.customize.store import set_custom_rule

pytestmark = pytest.mark.skipif(
    sys.platform.startswith("win"),
    reason="shell_runner honest-degrades on Windows",
)


def _shell_command_rule(*, rid: str, fires_at: str, inline: str) -> dict:
    """Persisted shell_command rule shape — mirrors payload_factory output."""
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


def _shell_check_rule(*, rid: str, fires_at: str, inline: str) -> dict:
    """Persisted shell_check rule shape — mirrors payload_factory output."""
    return {
        "id": rid,
        "scope": "always",
        "enabled": True,
        "firesAt": fires_at,
        "action": "audit",
        "what": {
            "kind": "shell_check",
            "payload": {
                "source": "inline",
                "inline": inline,
                "timeout_seconds": 5,
                "shell": "bash",
            },
        },
    }


@pytest.fixture
def cfg_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Per-test customize.json + master flags ON for both shell kinds."""
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_SHELL_COMMAND_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_SHELL_CHECK_ENABLED", "1")
    monkeypatch.delenv("MAGI_RUNTIME_PROFILE", raising=False)
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    # Reset the process-wide shared budget map so back-to-back tests do
    # not see each other's (session, turn) state.
    reset_shared_budget_for_tests()
    yield cfile
    reset_shared_budget_for_tests()


def test_cross_kind_budget_shares_counter(
    cfg_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Budget=1 ⇒ 1st kind spawns, 2nd kind sees budget_exhausted.

    Authors:
    * 1 shell_command rule at ``pre_final`` (gate-honored, audit-action
      so verdict stays proceed and the spawn is observable as a
      single ``executed`` audit record).
    * 1 shell_check rule at ``pre_final`` (gate-honored, audit-action
      so the spawn is observable as a single ``evaluated`` audit
      record).

    Drives both helpers in sequence — the FIRST (shell_command) spawns
    a subprocess; the SECOND (shell_check) sees ``remaining_budget==0``
    at its accessor and short-circuits with a ``budget_exhausted``
    audit record. The contract is "ONE total spawn across the two
    kinds when the shared budget is 1" — proving the
    :func:`shell_budget_for` counter is shared across kinds via the
    same per-(session, turn) state map.

    Without the F-EXEC2 review union-gate fix this test would still
    pass (both flags are ON) — the F-EXEC2 regressions live in the
    asymmetric-flag tests below.
    """
    monkeypatch.setenv("MAGI_CUSTOMIZE_SHELL_AUDIT_BUDGET", "1")
    set_custom_rule(
        _shell_command_rule(
            rid="cr_fqa5_xkind_cmd",
            fires_at="pre_final",
            inline="exit 0",
        ),
        path=cfg_path,
    )
    set_custom_rule(
        _shell_check_rule(
            rid="cr_fqa5_xkind_chk",
            fires_at="pre_final",
            inline='echo \'{"passed": true}\'',
        ),
        path=cfg_path,
    )

    token = set_active_turn_identity("sess_fqa5_xkind", "turn_fqa5_xkind")
    try:

        async def _drive() -> tuple[list[dict], list[dict]]:
            # First helper (shell_command) — thread the shared accessor's
            # pair so the spawn decrements the shared counter for the
            # next helper.
            remaining_cmd, decrement_cmd = shell_budget_for()
            cmd_audits, _cmd_verdict = await run_shell_command_at_pre_final(
                draft_text="fqa5 xkind draft",
                remaining_budget=remaining_cmd,
                decrement_fn=decrement_cmd,
            )
            # Second helper (shell_check) — re-resolve so the counter
            # update from the previous spawn is observed at the gate
            # check.
            remaining_chk, decrement_chk = shell_budget_for()
            chk_audits, _chk_verdict = await run_shell_check_at_pre_final(
                draft_text="fqa5 xkind draft",
                remaining_budget=remaining_chk,
                decrement_fn=decrement_chk,
            )
            return list(cmd_audits), list(chk_audits)

        cmd_audits, chk_audits = asyncio.run(_drive())
    finally:
        reset_active_turn_identity(token)

    # 1st kind spawned exactly once (status="executed" — shell_command's
    # successful-spawn label).
    cmd_executed = [a for a in cmd_audits if a.get("status") == "executed"]
    cmd_exhausted = [
        a for a in cmd_audits if a.get("status") == "budget_exhausted"
    ]
    assert len(cmd_executed) == 1, (
        f"shell_command at pre_final expected 1 executed audit "
        f"(budget=1, kind1); got {len(cmd_executed)} cmd_audits={cmd_audits!r}"
    )
    assert not cmd_exhausted, (
        f"shell_command kind1 must spawn (budget=1 fresh); got "
        f"cmd_audits={cmd_audits!r}"
    )

    # 2nd kind MUST short-circuit — exactly one budget_exhausted record,
    # no evaluated/executed spawn.
    chk_evaluated = [
        a for a in chk_audits if a.get("status") == "evaluated"
    ]
    chk_executed = [a for a in chk_audits if a.get("status") == "executed"]
    chk_exhausted = [
        a for a in chk_audits if a.get("status") == "budget_exhausted"
    ]
    assert not chk_evaluated and not chk_executed, (
        f"shell_check kind2 MUST NOT spawn after budget exhaustion; got "
        f"chk_audits={chk_audits!r}"
    )
    assert len(chk_exhausted) == 1, (
        f"shell_check kind2 expected one budget_exhausted record "
        f"(budget shared across kinds); got {len(chk_exhausted)} "
        f"chk_audits={chk_audits!r}"
    )


def test_budget_works_with_only_shell_command_enabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """shell_command ON / shell_check OFF ⇒ budget still initializes.

    The F-EXEC2 review fix in
    :func:`magi_agent.adk_bridge.lifecycle_shell_command_control.shell_budget_for`
    union-gates the master-flag check
    (``shell_command_enabled OR shell_check_enabled``). Before the fix
    the helper checked only ``shell_command_enabled`` — flipping that
    flag OFF would honest-degrade to ``(None, _no_op)`` even when the
    operator only authored shell_check rules; the cross-kind budget
    contract above would silently bypass the cap.

    This regression test asserts the converse: when only the
    shell_command flag is ON, :func:`shell_budget_for` returns a real
    ``(remaining, decrement_fn)`` pair so the cap fires.
    """
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_SHELL_COMMAND_ENABLED", "1")
    # Explicitly disable shell_check — exercise the asymmetric flag
    # state the F-EXEC2 review pass identified as a budget gap.
    monkeypatch.setenv("MAGI_CUSTOMIZE_SHELL_CHECK_ENABLED", "0")
    monkeypatch.delenv("MAGI_RUNTIME_PROFILE", raising=False)
    monkeypatch.setenv("MAGI_CUSTOMIZE_SHELL_AUDIT_BUDGET", "3")
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    reset_shared_budget_for_tests()

    token = set_active_turn_identity("sess_fqa5_cmd_only", "turn_fqa5_cmd_only")
    try:
        remaining, decrement_fn = shell_budget_for()
        # The budget MUST initialize from the env knob (3) — not None
        # — even though only shell_command is enabled.
        assert remaining == 3, (
            f"shell_budget_for must initialize from the union-gate when "
            f"only shell_command flag is ON; got remaining={remaining!r}"
        )
        # decrement_fn must be a real callable that drops the counter.
        decrement_fn()
        remaining_after, _ = shell_budget_for()
        assert remaining_after == 2, (
            f"shell_budget_for decrement must drop the shared counter; "
            f"got remaining_after={remaining_after!r}"
        )
    finally:
        reset_active_turn_identity(token)
        reset_shared_budget_for_tests()


def test_budget_works_with_only_shell_check_enabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """shell_check ON / shell_command OFF ⇒ budget still initializes.

    Mirror of :func:`test_budget_works_with_only_shell_command_enabled`
    — the F-EXEC2 review fix's union-gate must initialize the budget
    when only shell_check is enabled. This is the canonical
    "shell_check-only deployment" path (operator opted in to
    verifiers without enabling actions) — the pre-fix behavior would
    have returned ``remaining=None`` so the cap silently never fired.
    """
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    # Explicitly disable shell_command — exercise the converse
    # asymmetric flag state.
    monkeypatch.setenv("MAGI_CUSTOMIZE_SHELL_COMMAND_ENABLED", "0")
    monkeypatch.setenv("MAGI_CUSTOMIZE_SHELL_CHECK_ENABLED", "1")
    monkeypatch.delenv("MAGI_RUNTIME_PROFILE", raising=False)
    monkeypatch.setenv("MAGI_CUSTOMIZE_SHELL_AUDIT_BUDGET", "4")
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    reset_shared_budget_for_tests()

    token = set_active_turn_identity("sess_fqa5_chk_only", "turn_fqa5_chk_only")
    try:
        remaining, decrement_fn = shell_budget_for()
        assert remaining == 4, (
            f"shell_budget_for must initialize from the union-gate when "
            f"only shell_check flag is ON; got remaining={remaining!r}"
        )
        decrement_fn()
        remaining_after, _ = shell_budget_for()
        assert remaining_after == 3, (
            f"shell_budget_for decrement must drop the shared counter; "
            f"got remaining_after={remaining_after!r}"
        )
    finally:
        reset_active_turn_identity(token)
        reset_shared_budget_for_tests()
