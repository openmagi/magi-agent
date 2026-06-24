"""PR-F-UX1 firing test: on_user_prompt_submit audit-only lifecycle gate.

Drives :func:`magi_agent.customize.lifecycle_audit.run_user_prompt_submit_audit`
end-to-end through a tmp ``customize.json`` + the triple-gated flag combination
(``MAGI_CUSTOMIZE_LIFECYCLE_EXPANSION_ENABLED`` strict-truthy +
``MAGI_CUSTOMIZE_VERIFICATION_ENABLED`` + ``MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED``
profile-aware default-ON). Proves four things together:

1. A persisted ``llm_criterion`` rule with ``firesAt == "on_user_prompt_submit"``
   and ``action == "audit"`` is loaded and selected for evaluation.
2. The criterion judge is invoked exactly once and with the user-authored
   criterion text against the prompt text supplied to the fan-out.
3. A failing verdict surfaces as an audit record (``passed=False``) — NEVER
   as a block: the contract for this Tier 2 slot is audit-only.
4. With the master flag OFF the rule is silently inert (judge never called).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from magi_agent.customize.lifecycle_audit import run_user_prompt_submit_audit
from magi_agent.customize.store import set_custom_rule

_RULE_ID = "cr_fux1_user_prompt_no_secrets"
_CRITERION_TEXT = "the prompt does not contain raw credentials"
_PROMPT_TEXT = "Please fetch https://example.com with AKIA1234567890ABCDEF."


def _rule() -> dict:
    return {
        "id": _RULE_ID,
        "scope": "always",
        "enabled": True,
        "what": {
            "kind": "llm_criterion",
            "payload": {"criterion": _CRITERION_TEXT},
        },
        "firesAt": "on_user_prompt_submit",
        "action": "audit",
    }


@pytest.fixture
def cfg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Triple-gated flags ON + tmp customize.json with the audit rule."""
    monkeypatch.setenv("MAGI_CUSTOMIZE_LIFECYCLE_EXPANSION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    set_custom_rule(_rule(), path=cfile)
    return cfile


def test_user_prompt_submit_audit_fires_and_records_fail_verdict(
    cfg: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict] = []

    async def fake_eval(*, criterion, draft_text, model_factory, invoke=None):
        calls.append(
            {"criterion": criterion, "draft_text": draft_text}
        )
        return (False, "raw secret detected in prompt")

    monkeypatch.setattr(
        "magi_agent.customize.criterion_engine.evaluate_criterion", fake_eval
    )

    audits = asyncio.run(
        run_user_prompt_submit_audit(
            prompt_text=_PROMPT_TEXT,
            model_factory=lambda: object(),
        )
    )

    # 1. Exactly one rule audited.
    assert len(audits) == 1
    audit = audits[0]
    # 2. Rule id is round-tripped.
    assert audit["rule_id"] == _RULE_ID
    # 3. Fail verdict is recorded — audit-only contract: NO block surface.
    assert audit["passed"] is False
    assert audit["reason"] == "raw secret detected in prompt"
    assert audit["status"] == "evaluated"
    # 4. Judge invoked exactly once with the rule's criterion + prompt text.
    assert len(calls) == 1
    assert calls[0]["criterion"] == _CRITERION_TEXT
    assert calls[0]["draft_text"] == _PROMPT_TEXT


def test_user_prompt_submit_audit_records_pass_verdict(
    cfg: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_eval(*, criterion, draft_text, model_factory, invoke=None):
        return (True, "ok")

    monkeypatch.setattr(
        "magi_agent.customize.criterion_engine.evaluate_criterion", fake_eval
    )

    audits = asyncio.run(
        run_user_prompt_submit_audit(
            prompt_text="hello",
            model_factory=lambda: object(),
        )
    )

    assert len(audits) == 1
    assert audits[0]["passed"] is True
    assert audits[0]["status"] == "evaluated"


def test_user_prompt_submit_inert_when_master_flag_off(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Master flag OFF ⇒ judge MUST NOT be invoked even with a persisted rule."""
    monkeypatch.setenv("MAGI_CUSTOMIZE_LIFECYCLE_EXPANSION_ENABLED", "0")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    set_custom_rule(_rule(), path=cfile)

    async def fail_eval(*, criterion, draft_text, model_factory, invoke=None):
        raise AssertionError("judge must not be invoked when master flag is OFF")

    monkeypatch.setattr(
        "magi_agent.customize.criterion_engine.evaluate_criterion", fail_eval
    )

    audits = asyncio.run(
        run_user_prompt_submit_audit(
            prompt_text=_PROMPT_TEXT,
            model_factory=lambda: object(),
        )
    )
    assert audits == []


def test_user_prompt_submit_inert_when_no_rules_authored(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No rules ⇒ fan-out returns empty list without invoking the judge."""
    monkeypatch.setenv("MAGI_CUSTOMIZE_LIFECYCLE_EXPANSION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    # no set_custom_rule call → empty customize

    async def fail_eval(*, criterion, draft_text, model_factory, invoke=None):
        raise AssertionError("judge must not be invoked when no rules are authored")

    monkeypatch.setattr(
        "magi_agent.customize.criterion_engine.evaluate_criterion", fail_eval
    )

    audits = asyncio.run(
        run_user_prompt_submit_audit(
            prompt_text=_PROMPT_TEXT,
            model_factory=lambda: object(),
        )
    )
    assert audits == []


def test_user_prompt_submit_fail_open_when_critic_missing(
    cfg: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No critic model ⇒ audit short-circuits passed=True (fail-open contract)."""

    async def fail_eval(*, criterion, draft_text, model_factory, invoke=None):
        raise AssertionError("judge must not be invoked when model_factory is None")

    monkeypatch.setattr(
        "magi_agent.customize.criterion_engine.evaluate_criterion", fail_eval
    )

    audits = asyncio.run(
        run_user_prompt_submit_audit(
            prompt_text=_PROMPT_TEXT,
            model_factory=None,
        )
    )

    assert len(audits) == 1
    audit = audits[0]
    assert audit["rule_id"] == _RULE_ID
    assert audit["passed"] is True  # fail-open
    assert audit["status"] == "skipped"
