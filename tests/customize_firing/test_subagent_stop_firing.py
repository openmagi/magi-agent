"""PR-F-UX1 firing test: on_subagent_stop audit-only lifecycle gate.

Drives :func:`magi_agent.customize.lifecycle_audit.run_subagent_stop_audit`
end-to-end through a tmp ``customize.json`` + triple-gated flag combination.
Mirrors the user_prompt_submit firing test (audit-only contract is the same)
but exercises the post-turn slot — the child's emitted final text — to lock
the second Tier 2 wire site.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from magi_agent.customize.lifecycle_audit import run_subagent_stop_audit
from magi_agent.customize.store import set_custom_rule

_RULE_ID = "cr_fux1_subagent_stop_no_leaks"
_CRITERION_TEXT = "the child output does not leak internal raw tool envelopes"
_FINAL_TEXT = "Here is a summary of what the child did during its turn."


def _rule() -> dict:
    return {
        "id": _RULE_ID,
        "scope": "always",
        "enabled": True,
        "what": {
            "kind": "llm_criterion",
            "payload": {"criterion": _CRITERION_TEXT},
        },
        "firesAt": "on_subagent_stop",
        "action": "audit",
    }


@pytest.fixture
def cfg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("MAGI_CUSTOMIZE_LIFECYCLE_EXPANSION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    set_custom_rule(_rule(), path=cfile)
    return cfile


def test_subagent_stop_audit_fires_and_records_pass_verdict(
    cfg: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict] = []

    async def fake_eval(*, criterion, draft_text, model_factory, invoke=None, evidence_context=None):
        calls.append({"criterion": criterion, "draft_text": draft_text})
        return (True, "looks clean")

    monkeypatch.setattr(
        "magi_agent.customize.criterion_engine.evaluate_criterion", fake_eval
    )

    audits = asyncio.run(
        run_subagent_stop_audit(
            final_text=_FINAL_TEXT,
            model_factory=lambda: object(),
        )
    )

    assert len(audits) == 1
    audit = audits[0]
    assert audit["rule_id"] == _RULE_ID
    assert audit["passed"] is True
    assert audit["status"] == "evaluated"
    assert len(calls) == 1
    assert calls[0]["criterion"] == _CRITERION_TEXT
    assert calls[0]["draft_text"] == _FINAL_TEXT


def test_subagent_stop_audit_records_fail_verdict_never_blocks(
    cfg: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Critic returning pass=false ⇒ audit dict carries fail but NO block surface.

    Locks the Tier 2 audit-only contract: even when the verdict is a fail
    the fan-out must NOT raise / return a block dict, only an audit record.
    """

    async def fake_eval(*, criterion, draft_text, model_factory, invoke=None, evidence_context=None):
        return (False, "leaked tool envelope detected")

    monkeypatch.setattr(
        "magi_agent.customize.criterion_engine.evaluate_criterion", fake_eval
    )

    audits = asyncio.run(
        run_subagent_stop_audit(
            final_text=_FINAL_TEXT,
            model_factory=lambda: object(),
        )
    )

    assert len(audits) == 1
    audit = audits[0]
    assert audit["passed"] is False
    assert audit["reason"] == "leaked tool envelope detected"
    # The audit record dict is a plain mapping — never a block envelope. The
    # Tier 2 contract is explicit: no key in the audit dict implies "block";
    # the surrounding runtime keeps emitting byte-identically.
    assert "blocked_by" not in audit
    assert "response_type" not in audit


def test_subagent_stop_inert_when_master_flag_off(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE_LIFECYCLE_EXPANSION_ENABLED", "0")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    set_custom_rule(_rule(), path=cfile)

    async def fail_eval(*, criterion, draft_text, model_factory, invoke=None, evidence_context=None):
        raise AssertionError("judge must not be invoked when master flag is OFF")

    monkeypatch.setattr(
        "magi_agent.customize.criterion_engine.evaluate_criterion", fail_eval
    )

    audits = asyncio.run(
        run_subagent_stop_audit(
            final_text=_FINAL_TEXT,
            model_factory=lambda: object(),
        )
    )
    assert audits == []
