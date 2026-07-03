from __future__ import annotations

import asyncio

import pytest

from magi_agent.cli.engine import MagiEngineDriver
from magi_agent.customize.store import set_custom_rule


def _llm_rule(action: str = "block", rid: str = "cr_llm"):
    return {
        "id": rid,
        "scope": "research",
        "enabled": True,
        "what": {"kind": "llm_criterion", "payload": {"criterion": "all claims cited"}},
        "firesAt": "pre_final",
        "action": action,
    }


@pytest.fixture
def cfg(monkeypatch, tmp_path):
    monkeypatch.setenv("MAGI_EGRESS_GATE_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    return cfile


def _run(driver):
    return asyncio.run(
        driver._maybe_llm_criterion_block(
            final_text="The market grew 40%.", turn_id="t1"
        )
    )


def test_inert_when_flags_off(monkeypatch, tmp_path):
    monkeypatch.delenv("MAGI_EGRESS_GATE_ENABLED", raising=False)
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    set_custom_rule(_llm_rule(), path=cfile)
    driver = MagiEngineDriver(criterion_model_factory=lambda: object())
    assert _run(driver) is None


def test_fail_open_when_no_model_factory(cfg):
    set_custom_rule(_llm_rule(), path=cfg)
    driver = MagiEngineDriver(criterion_model_factory=None)
    assert _run(driver) is None  # no model → inert (never block)


def test_blocks_when_rule_fails(cfg, monkeypatch):
    set_custom_rule(_llm_rule(action="block"), path=cfg)

    async def fake_eval(*, criterion, draft_text, model_factory, evidence_context=None):
        return (False, "uncited claim")

    monkeypatch.setattr("magi_agent.customize.criterion_engine.evaluate_criterion", fake_eval)
    driver = MagiEngineDriver(criterion_model_factory=lambda: object())
    assert _run(driver) == "uncited claim"


def test_passes_when_rule_passes(cfg, monkeypatch):
    set_custom_rule(_llm_rule(action="block"), path=cfg)

    async def fake_eval(*, criterion, draft_text, model_factory, evidence_context=None):
        return (True, "ok")

    monkeypatch.setattr("magi_agent.customize.criterion_engine.evaluate_criterion", fake_eval)
    driver = MagiEngineDriver(criterion_model_factory=lambda: object())
    assert _run(driver) is None


def test_non_block_action_not_enforced(cfg, monkeypatch):
    set_custom_rule(_llm_rule(action="audit"), path=cfg)

    async def fake_eval(*, criterion, draft_text, model_factory, evidence_context=None):
        return (False, "would fail but audit-only")

    monkeypatch.setattr("magi_agent.customize.criterion_engine.evaluate_criterion", fake_eval)
    driver = MagiEngineDriver(criterion_model_factory=lambda: object())
    assert _run(driver) is None  # audit action does not block at pre-final in P3


# --- evidence-grounded pre_final wiring ---


def _evidence_rule(refs):
    rule = _llm_rule(action="block")
    rule["what"]["payload"]["evidenceRefs"] = refs
    return rule


def test_evidence_grounded_projects_declared_evidence(cfg, monkeypatch):
    monkeypatch.setenv("MAGI_EVIDENCE_GROUNDED_CRITIC_ENABLED", "1")
    set_custom_rule(_evidence_rule(["TestRun"]), path=cfg)

    captured: dict = {}

    async def fake_eval(*, criterion, draft_text, model_factory, evidence_context=None):
        captured["ctx"] = evidence_context
        return (True, "ok")

    monkeypatch.setattr("magi_agent.customize.criterion_engine.evaluate_criterion", fake_eval)
    driver = MagiEngineDriver(
        criterion_model_factory=lambda: object(),
        evidence_collector=lambda _tid: [{"type": "TestRun", "fields": {"exit_code": 0}}],
    )
    assert _run(driver) is None
    ctx = captured["ctx"]
    assert ctx is not None
    assert [r.type for r in ctx.records] == ["TestRun"]


def test_no_evidence_refs_keeps_context_none(cfg, monkeypatch):
    # Flag on, but the rule declares no evidenceRefs → evidence-blind (None).
    monkeypatch.setenv("MAGI_EVIDENCE_GROUNDED_CRITIC_ENABLED", "1")
    set_custom_rule(_llm_rule(action="block"), path=cfg)

    captured: dict = {}

    async def fake_eval(*, criterion, draft_text, model_factory, evidence_context=None):
        captured["ctx"] = evidence_context
        return (True, "ok")

    monkeypatch.setattr("magi_agent.customize.criterion_engine.evaluate_criterion", fake_eval)
    driver = MagiEngineDriver(
        criterion_model_factory=lambda: object(),
        evidence_collector=lambda _tid: [{"type": "TestRun", "fields": {}}],
    )
    assert _run(driver) is None
    assert captured["ctx"] is None


def test_evidence_grounded_off_keeps_context_none(cfg, monkeypatch):
    # evidenceRefs declared but the flag is off → evidence-blind (None).
    monkeypatch.setenv("MAGI_EVIDENCE_GROUNDED_CRITIC_ENABLED", "0")
    set_custom_rule(_evidence_rule(["TestRun"]), path=cfg)

    captured: dict = {}

    async def fake_eval(*, criterion, draft_text, model_factory, evidence_context=None):
        captured["ctx"] = evidence_context
        return (True, "ok")

    monkeypatch.setattr("magi_agent.customize.criterion_engine.evaluate_criterion", fake_eval)
    driver = MagiEngineDriver(
        criterion_model_factory=lambda: object(),
        evidence_collector=lambda _tid: [{"type": "TestRun", "fields": {}}],
    )
    assert _run(driver) is None
    assert captured["ctx"] is None
