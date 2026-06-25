"""F-EXEC2 firing tests: ``shell_check`` condition kind end-to-end.

Drives :mod:`magi_agent.customize.lifecycle_audit` fan-out helpers
end-to-end through a tmp ``customize.json`` + the triple-gated flag
combination (``MAGI_CUSTOMIZE_SHELL_CHECK_ENABLED`` strict-truthy +
``MAGI_CUSTOMIZE_VERIFICATION_ENABLED`` +
``MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED``).

Uses real ``echo`` / ``exit`` commands so the assertions reflect actual
subprocess invocations (not mocks). Also exercises the cross-kind budget
share with shell_command (a turn that spawns 3 shell_command + 3
shell_check rules must hit the 5-spawn ceiling at the 6th invocation
regardless of kind).
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
    run_shell_check_at_before_tool_use,
    run_shell_check_at_pre_final,
    run_shell_command_at_pre_final,
)
from magi_agent.customize.store import set_custom_rule


pytestmark = pytest.mark.skipif(
    sys.platform.startswith("win"),
    reason="shell_runner honest-degrades on Windows",
)


def _check_rule(
    *,
    rid: str,
    fires_at: str,
    action: str,
    inline: str,
    timeout_seconds: int = 5,
) -> dict:
    return {
        "id": rid,
        "scope": "always",
        "enabled": True,
        "firesAt": fires_at,
        "action": action,
        "what": {
            "kind": "shell_check",
            "payload": {
                "source": "inline",
                "inline": inline,
                "timeout_seconds": timeout_seconds,
                "shell": "bash",
            },
        },
    }


def _command_rule(
    *,
    rid: str,
    fires_at: str,
    action: str,
    inline: str,
    timeout_seconds: int = 5,
) -> dict:
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
                "inline": inline,
                "timeout_seconds": timeout_seconds,
                "shell": "bash",
            },
        },
    }


@pytest.fixture
def cfg_on(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("MAGI_CUSTOMIZE_SHELL_CHECK_ENABLED", "1")
    # F-EXEC1 stays OFF unless a test opts in — keeps the per-test budget
    # accounting unambiguous (the shared budget only initialises when a
    # F-EXEC1 or F-EXEC2 master flag is ON).
    monkeypatch.setenv("MAGI_CUSTOMIZE_SHELL_COMMAND_ENABLED", "0")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    monkeypatch.delenv("MAGI_RUNTIME_PROFILE", raising=False)
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    reset_shared_budget_for_tests()
    return cfile


# ---------------------------------------------------------------------------
# Exit-code fallback semantics (no JSON on stdout)
# ---------------------------------------------------------------------------


def test_pre_final_exit_zero_passes(cfg_on: Path) -> None:
    """exit 0 with no stdout JSON ⇒ passed=True, verdict=proceed."""
    set_custom_rule(
        _check_rule(
            rid="cr_fexec2_exit0",
            fires_at="pre_final",
            action="block",
            inline="exit 0",
        ),
        path=cfg_on,
    )
    audits, verdict = asyncio.run(run_shell_check_at_pre_final(draft_text="ok"))
    assert verdict == "proceed"
    assert audits and audits[0]["status"] == "evaluated"
    assert audits[0]["passed"] is True
    assert audits[0]["exit_code"] == 0
    assert audits[0]["reason"].startswith("shell_check_exit_")


def test_pre_final_exit_nonzero_blocks_when_action_block(cfg_on: Path) -> None:
    """exit 1 + action=block ⇒ passed=False, verdict=block."""
    set_custom_rule(
        _check_rule(
            rid="cr_fexec2_exit1_block",
            fires_at="pre_final",
            action="block",
            inline="exit 1",
        ),
        path=cfg_on,
    )
    audits, verdict = asyncio.run(run_shell_check_at_pre_final(draft_text="ok"))
    assert verdict == "block"
    assert audits and audits[0]["status"] == "evaluated"
    assert audits[0]["passed"] is False
    assert audits[0]["exit_code"] == 1


def test_pre_final_exit_nonzero_audit_does_not_block(cfg_on: Path) -> None:
    """exit 1 + action=audit ⇒ passed=False but verdict=proceed (audit-only)."""
    set_custom_rule(
        _check_rule(
            rid="cr_fexec2_exit1_audit",
            fires_at="pre_final",
            action="audit",
            inline="exit 1",
        ),
        path=cfg_on,
    )
    audits, verdict = asyncio.run(run_shell_check_at_pre_final(draft_text="ok"))
    assert verdict == "proceed"
    assert audits and audits[0]["passed"] is False
    assert audits[0]["status"] == "evaluated"


# ---------------------------------------------------------------------------
# JSON stdout takes precedence over exit code
# ---------------------------------------------------------------------------


def test_stdout_json_passed_false_overrides_exit_zero(cfg_on: Path) -> None:
    """Script exits 0 but emits {passed:false} ⇒ verdict=block on action=block."""
    set_custom_rule(
        _check_rule(
            rid="cr_fexec2_json_false",
            fires_at="pre_final",
            action="block",
            # Exits 0 but the JSON says failed — the verifier honors JSON.
            inline="echo '{\"passed\": false, \"reason\": \"missing X\"}'",
        ),
        path=cfg_on,
    )
    audits, verdict = asyncio.run(run_shell_check_at_pre_final(draft_text="ok"))
    assert verdict == "block"
    assert audits and audits[0]["passed"] is False
    assert audits[0]["reason"] == "missing X"
    assert audits[0]["exit_code"] == 0


def test_stdout_json_passed_true_overrides_exit_nonzero(cfg_on: Path) -> None:
    """Script exits 1 but emits {passed:true} ⇒ verdict=proceed.

    Honest contract: the verifier's JSON output is the canonical verdict;
    the exit code is a fallback. If a script chooses to surface a
    structured pass while still exiting non-zero (e.g. for downstream
    log filtering), the runtime honors the structured verdict.
    """
    set_custom_rule(
        _check_rule(
            rid="cr_fexec2_json_true",
            fires_at="pre_final",
            action="block",
            inline="echo '{\"passed\": true}'; exit 1",
        ),
        path=cfg_on,
    )
    audits, verdict = asyncio.run(run_shell_check_at_pre_final(draft_text="ok"))
    assert verdict == "proceed"
    assert audits and audits[0]["passed"] is True
    assert audits[0]["exit_code"] == 1


def test_stdout_json_last_line_salvage(cfg_on: Path) -> None:
    """Diagnostic output before a single-line JSON verdict is tolerated.

    Most operator scripts emit some diagnostic prose before the final
    structured verdict. The parser salvages the LAST non-blank line as
    JSON when the full body isn't parseable.
    """
    set_custom_rule(
        _check_rule(
            rid="cr_fexec2_salvage",
            fires_at="pre_final",
            action="block",
            inline=(
                "echo 'checking thing X';"
                " echo 'thing X is fine';"
                " echo '{\"passed\": false, \"reason\": \"actually nope\"}'"
            ),
        ),
        path=cfg_on,
    )
    audits, verdict = asyncio.run(run_shell_check_at_pre_final(draft_text="ok"))
    assert verdict == "block"
    assert audits and audits[0]["passed"] is False
    assert audits[0]["reason"] == "actually nope"


# ---------------------------------------------------------------------------
# before_tool_use slot
# ---------------------------------------------------------------------------


def test_before_tool_use_block_on_failed_verdict(cfg_on: Path) -> None:
    """before_tool_use + action=block + failed verdict ⇒ verdict=block."""
    set_custom_rule(
        _check_rule(
            rid="cr_fexec2_before_block",
            fires_at="before_tool_use",
            action="block",
            inline="exit 3",
        ),
        path=cfg_on,
    )
    audits, verdict = asyncio.run(
        run_shell_check_at_before_tool_use(
            tool_name="shell_exec",
            tool_args={"command": "rm -rf /"},
        )
    )
    assert verdict == "block"
    assert audits and audits[0]["exit_code"] == 3
    assert audits[0]["passed"] is False


def test_before_tool_use_passed_proceeds(cfg_on: Path) -> None:
    """before_tool_use + JSON passed=true ⇒ proceed."""
    set_custom_rule(
        _check_rule(
            rid="cr_fexec2_before_pass",
            fires_at="before_tool_use",
            action="block",
            inline="echo '{\"passed\": true}'",
        ),
        path=cfg_on,
    )
    audits, verdict = asyncio.run(
        run_shell_check_at_before_tool_use(
            tool_name="shell_exec",
            tool_args={"command": "ls"},
        )
    )
    assert verdict == "proceed"
    assert audits and audits[0]["passed"] is True


# ---------------------------------------------------------------------------
# OFF-path silence — master flag OFF ⇒ no spawn, no fan-out
# ---------------------------------------------------------------------------


def test_off_path_byte_identical(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Master flag OFF ⇒ no audits, no subprocess spawn."""
    monkeypatch.setenv("MAGI_CUSTOMIZE_SHELL_CHECK_ENABLED", "0")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    reset_shared_budget_for_tests()
    set_custom_rule(
        _check_rule(
            rid="cr_fexec2_off",
            fires_at="pre_final",
            action="block",
            inline="exit 1",
        ),
        path=cfile,
    )
    audits, verdict = asyncio.run(run_shell_check_at_pre_final(draft_text="ok"))
    assert audits == []
    assert verdict == "proceed"


# ---------------------------------------------------------------------------
# Cross-kind budget share with shell_command
# ---------------------------------------------------------------------------


def test_budget_works_with_shell_check_only_master_flag_on(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Regression for BLOCKER 2 — F-EXEC1 OFF + F-EXEC2 ON must yield a real
    budget pair.

    Prior behaviour: ``shell_budget_for`` short-circuited to
    ``(None, _no_op_decrement)`` whenever ``shell_command_enabled`` was
    False, even when ``shell_check_enabled`` was True. Operators authoring
    only ``shell_check`` rules would see ``remaining_budget=None`` and the
    fan-out helper would skip every cap branch ⇒ unbounded spawning.

    This test asserts the union-gate fix:

    (a) ``shell_budget_for()`` returns a real ``(remaining, decrement_fn)``
        pair when ONLY ``MAGI_CUSTOMIZE_SHELL_CHECK_ENABLED`` is ON.
    (b) Two back-to-back shell_check fan-outs under budget=1 produce
        exactly one ``executed`` audit and one ``budget_exhausted`` audit
        — the second fan-out must short-circuit at the cap.
    """
    monkeypatch.setenv("MAGI_CUSTOMIZE_SHELL_CHECK_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_SHELL_COMMAND_ENABLED", "0")  # OFF
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_SHELL_AUDIT_BUDGET", "1")
    monkeypatch.delenv("MAGI_RUNTIME_PROFILE", raising=False)
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    reset_shared_budget_for_tests()

    set_custom_rule(
        _check_rule(
            rid="cr_shell_check_only_first",
            fires_at="pre_final",
            action="audit",
            inline="echo '{\"passed\": true}'",
        ),
        path=cfile,
    )

    token = set_active_turn_identity("sess_check_only", "turn_check_only")
    try:
        # (a) shell_budget_for must return a real (remaining, decrement_fn)
        # pair — NOT the (None, no_op) OFF sentinel. Before the fix this
        # assertion would fail because shell_command_enabled was False.
        remaining_a, decrement_a = shell_budget_for()
        assert remaining_a == 1, (
            "shell_budget_for returned None when only shell_check is ON — "
            "the union gate is broken; the fan-out helper would skip the "
            "cap branch."
        )

        # (b) First spawn consumes the single budget unit.
        first_audits, _ = asyncio.run(
            run_shell_check_at_pre_final(
                draft_text="x",
                remaining_budget=remaining_a,
                decrement_fn=decrement_a,
            )
        )
        assert first_audits and first_audits[0]["status"] == "evaluated"

        # (b) Second back-to-back spawn at the same (session, turn) MUST see
        # remaining=0 and short-circuit to a budget_exhausted record.
        remaining_b, decrement_b = shell_budget_for()
        assert remaining_b == 0
        second_audits, _ = asyncio.run(
            run_shell_check_at_pre_final(
                draft_text="x",
                remaining_budget=remaining_b,
                decrement_fn=decrement_b,
            )
        )
        assert second_audits, (
            "expected a budget_exhausted record on the second spawn; got []"
        )
        assert second_audits[0]["status"] == "budget_exhausted"
    finally:
        reset_active_turn_identity(token)


def test_budget_shared_with_shell_command_in_same_turn(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A turn that fires shell_command first and shell_check second shares
    the same MAGI_CUSTOMIZE_SHELL_AUDIT_BUDGET counter.

    Wires both master flags ON, sets the per-turn budget to 1, drives a
    pre_final shell_command spawn (1 budget unit consumed), then drives a
    pre_final shell_check spawn — the shell_check call must see budget
    exhausted and short-circuit to a single ``budget_exhausted`` audit
    record without spawning.
    """
    monkeypatch.setenv("MAGI_CUSTOMIZE_SHELL_COMMAND_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_SHELL_CHECK_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_SHELL_AUDIT_BUDGET", "1")
    monkeypatch.delenv("MAGI_RUNTIME_PROFILE", raising=False)
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    reset_shared_budget_for_tests()

    # Author one shell_command rule + one shell_check rule, both at
    # pre_final with audit (we don't care about verdict — we care about
    # the budget interaction).
    set_custom_rule(
        _command_rule(
            rid="cr_shell_command_first",
            fires_at="pre_final",
            action="audit",
            inline="echo cmd_ran",
        ),
        path=cfile,
    )
    set_custom_rule(
        _check_rule(
            rid="cr_shell_check_second",
            fires_at="pre_final",
            action="audit",
            inline="echo '{\"passed\": true}'",
        ),
        path=cfile,
    )

    token = set_active_turn_identity("sess_share", "turn_share")
    try:
        # First spawn: shell_command. Burns the single budget unit.
        remaining_a, decrement_a = shell_budget_for()
        assert remaining_a == 1
        cmd_audits, _ = asyncio.run(
            run_shell_command_at_pre_final(
                draft_text="x",
                remaining_budget=remaining_a,
                decrement_fn=decrement_a,
            )
        )
        assert cmd_audits and cmd_audits[0]["status"] == "executed"

        # Second spawn attempt: shell_check at the SAME (session, turn).
        # The shared map now has remaining=0 → check fan-out short-circuits
        # to a single budget_exhausted record without invoking the runner.
        remaining_b, decrement_b = shell_budget_for()
        assert remaining_b == 0
        check_audits, check_verdict = asyncio.run(
            run_shell_check_at_pre_final(
                draft_text="x",
                remaining_budget=remaining_b,
                decrement_fn=decrement_b,
            )
        )
        assert check_audits and check_audits[0]["status"] == "budget_exhausted"
        assert check_audits[0]["passed"] is True
        assert check_verdict == "proceed"
    finally:
        reset_active_turn_identity(token)
