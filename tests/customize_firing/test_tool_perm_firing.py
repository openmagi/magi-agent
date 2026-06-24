"""F1 — tool_perm rule fires before runtime executes the tool call.

Proves the customize ``tool_perm`` rule machinery (``matched_decision``)
returns a deterministic ``(decision, rule_id)`` for a matching tool call,
``None`` for a non-matching tool name, and ``None`` when the rule's scope
does not cover the current turn.

The matcher is the seam runtime code consults *before_tool_use* — a return
of ``("deny", rule_id)`` lets the runtime block the call without ever
dispatching the tool, so this test is a sufficient proxy for the firing
behavior end-to-end without standing up the full agent loop.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from magi_agent.customize.store import set_custom_rule
from magi_agent.customize.tool_perm import matched_decision


_RULE_ID = "cr_f1_shell_exec_deny"


def _shell_exec_coding_deny_rule() -> dict:
    return {
        "id": _RULE_ID,
        "scope": "coding",
        "enabled": True,
        "what": {
            "kind": "tool_perm",
            "payload": {
                "match": {"tool": "shell_exec"},
                "decision": "deny",
            },
        },
        "firesAt": "before_tool_use",
        "action": "block",
    }


@pytest.fixture
def cfg(monkeypatch, tmp_path) -> Path:
    """Tmp customize.json + flags ON. Persists the F1 deny rule."""
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    set_custom_rule(_shell_exec_coding_deny_rule(), path=cfile)
    return cfile


def test_shell_exec_in_coding_scope_is_denied(cfg: Path) -> None:
    """Matching tool name + matching scope → ``("deny", rule_id)``.

    This is the firing case the runtime gate consults before dispatching
    ``shell_exec`` — a non-None return blocks the call.
    """
    decision = matched_decision(
        tool_name="shell_exec",
        arguments={},
        current_scope="coding",
    )
    assert decision == ("deny", _RULE_ID)


def test_other_tool_in_coding_scope_is_not_matched(cfg: Path) -> None:
    """Tool name does not match the rule → ``None``.

    Negative: ``read_file`` is unrelated; the rule must not fire.
    """
    decision = matched_decision(
        tool_name="read_file",
        arguments={},
        current_scope="coding",
    )
    assert decision is None


def test_shell_exec_outside_scope_is_not_matched(cfg: Path) -> None:
    """Rule's ``scope=coding`` must not fire on a ``research`` turn → ``None``.

    Note: ``tool_perm.matched_decision`` is scope-blind when ``current_scope``
    is ``None``; the production caller in ``magi_agent/tools/permission.py:143``
    invokes without threading scope, so a coding-scoped rule WOULD fire on a
    research turn through that callsite today. This test exercises the
    scope-aware path that callers can opt into.
    """
    decision = matched_decision(
        tool_name="shell_exec",
        arguments={},
        current_scope="research",
    )
    assert decision is None


def test_inert_when_flags_off(monkeypatch, tmp_path) -> None:
    """Master verification flags OFF → matched_decision returns None even with
    a persisted matching rule. Locks the default-OFF byte-identical invariant
    so a future regression cannot make the gate flag-blind.
    """
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "0")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "0")
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    set_custom_rule(_shell_exec_coding_deny_rule(), path=cfile)

    decision = matched_decision(
        tool_name="shell_exec",
        arguments={},
        current_scope="coding",
    )
    assert decision is None
