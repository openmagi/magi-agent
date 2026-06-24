"""F6 — tool_perm path / pathAllowlist rules fire on path-bearing tool calls.

Proves the customize ``tool_perm`` rule machinery (``matched_decision``)
returns a deterministic ``(decision, rule_id)`` when a tool call's file/path
argument is at or under the deny prefix, and ``None`` for a non-matching
path.

This is the F6 backend acceptance: the Author wizard now exposes ``path``
and ``path_allowlist`` condition kinds for ``before_tool_use`` + target=any.
Those kinds compile to ``match.path`` / ``match.pathAllowlist`` in the
custom-rule payload, which the runtime tool-permission gate then consults
via ``matched_decision``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from magi_agent.customize.store import set_custom_rule
from magi_agent.customize.tool_perm import matched_decision


_RULE_ID = "cr_f6_deny_path_etc"


def _deny_path_etc_rule() -> dict:
    return {
        "id": _RULE_ID,
        "scope": "coding",
        "enabled": True,
        "what": {
            "kind": "tool_perm",
            "payload": {
                "match": {"path": "/etc"},
                "decision": "deny",
            },
        },
        "firesAt": "before_tool_use",
        "action": "block",
    }


@pytest.fixture
def cfg(monkeypatch, tmp_path) -> Path:
    """Tmp customize.json + flags ON. Persists the F6 path-deny rule."""
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    set_custom_rule(_deny_path_etc_rule(), path=cfile)
    return cfile


def test_file_write_to_etc_path_is_denied(cfg: Path) -> None:
    """FileWrite under the deny prefix → ``("deny", rule_id)``.

    The runtime gate consults ``matched_decision`` before dispatching a
    path-bearing tool — a non-None return blocks the call without ever
    invoking FileWrite.
    """
    decision = matched_decision(
        tool_name="FileWrite",
        arguments={"path": "/etc/hosts"},
        current_scope="coding",
    )
    assert decision == ("deny", _RULE_ID)


def test_file_write_outside_deny_prefix_is_not_matched(cfg: Path) -> None:
    """Path argument outside the deny prefix → ``None``.

    Negative: ``/tmp/x`` is not under ``/etc``; the rule must not fire so
    the call is dispatched normally.
    """
    decision = matched_decision(
        tool_name="FileWrite",
        arguments={"path": "/tmp/x"},
        current_scope="coding",
    )
    assert decision is None
