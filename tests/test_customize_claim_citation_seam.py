"""H3-C4: claim-citation (free-text claim-coverage) LLM gate + opt_in seam.

``_claim_citation_llm_block`` judges via the generic
``criterion_engine.evaluate_criterion`` whether the final answer makes specific
factual claims WITHOUT any source citation. Distinct from source-authority
(anti-fab/det over declared ``src_N`` refs); this is free-text coverage.

Det pre-gate: a final answer that contains any ``[src_N]`` citation marker (the
existing source-citation convention used by the research projection gate) skips
the model call. Only uncited answers reach the criterion judge.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import magi_agent.customize.criterion_engine as criterion_engine
from magi_agent.cli.engine import MagiEngineDriver, RunnerPolicyAssembly
from magi_agent.customize.store import set_verification_override


def _driver(*, with_model: bool = True) -> MagiEngineDriver:
    return MagiEngineDriver(
        runner=None,
        runner_policy_assembly=RunnerPolicyAssembly(
            modelProvider="local",
            modelLabel="local-dev",
            selectedPackIds=("user.quote",),
            evidenceRequirements=(),
            requiredValidators=(),
            missingEvidenceAction="block",
        ),
        evidence_collector=lambda _turn: (),
        criterion_model_factory=(lambda: object()) if with_model else None,
    )


def _block(driver: MagiEngineDriver, *, final_text: str) -> str | None:
    return asyncio.run(driver._claim_citation_llm_block(final_text=final_text))


def _fake_verdict(passed: bool, reason: str = "r"):
    async def _ev(*, criterion, draft_text, model_factory, invoke=None):
        return (passed, reason)

    return _ev


def _never_called():
    async def _ev(*, criterion, draft_text, model_factory, invoke=None):
        raise AssertionError("criterion judge must not be called on this path")

    return _ev


@pytest.fixture
def cfile(monkeypatch, tmp_path) -> Path:
    path = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(path))
    return path


def test_inert_when_flag_off(monkeypatch, cfile):
    monkeypatch.setenv("MAGI_VERIFY_CLAIM_CITATION", "0")
    monkeypatch.delenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", raising=False)
    monkeypatch.setattr(criterion_engine, "evaluate_criterion", _never_called())
    assert _block(_driver(), final_text="Revenue grew 30% in Q3.") is None


def test_inert_when_no_model(monkeypatch, cfile):
    monkeypatch.setenv("MAGI_VERIFY_CLAIM_CITATION", "1")
    monkeypatch.setattr(criterion_engine, "evaluate_criterion", _never_called())
    assert _block(_driver(with_model=False), final_text="Revenue grew 30%.") is None


def test_cited_answer_skips_judge(monkeypatch, cfile):
    # An answer with [src_N] markers ⇒ det pre-gate skips the model call.
    monkeypatch.setenv("MAGI_VERIFY_CLAIM_CITATION", "1")
    monkeypatch.setattr(criterion_engine, "evaluate_criterion", _never_called())
    assert _block(_driver(), final_text="Revenue grew 30% [src_1] last year.") is None


def test_uncited_claim_blocks(monkeypatch, cfile):
    monkeypatch.setenv("MAGI_VERIFY_CLAIM_CITATION", "1")
    monkeypatch.setattr(
        criterion_engine, "evaluate_criterion", _fake_verdict(False, "uncited claim")
    )
    assert _block(_driver(), final_text="Revenue grew 30% in Q3.") == "uncited claim"


def test_uncited_general_answer_passes(monkeypatch, cfile):
    # The judge passes a general/procedural answer that doesn't warrant a cite.
    monkeypatch.setenv("MAGI_VERIFY_CLAIM_CITATION", "1")
    monkeypatch.setattr(criterion_engine, "evaluate_criterion", _fake_verdict(True))
    assert _block(_driver(), final_text="Here is some general advice.") is None


def test_preset_toggle_activates(monkeypatch, cfile):
    monkeypatch.setenv("MAGI_VERIFY_CLAIM_CITATION", "0")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    set_verification_override("harness_presets", "claim-citation", True, path=cfile)
    monkeypatch.setattr(criterion_engine, "evaluate_criterion", _fake_verdict(False, "x"))
    assert _block(_driver(), final_text="Revenue was up 30%.") == "x"


def test_not_activated_when_master_flag_off(monkeypatch, cfile):
    monkeypatch.setenv("MAGI_VERIFY_CLAIM_CITATION", "0")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "0")
    set_verification_override("harness_presets", "claim-citation", True, path=cfile)
    monkeypatch.setattr(criterion_engine, "evaluate_criterion", _never_called())
    assert _block(_driver(), final_text="Revenue was up 30%.") is None


def test_fail_open_on_judge_error(monkeypatch, cfile):
    monkeypatch.setenv("MAGI_VERIFY_CLAIM_CITATION", "1")

    async def _boom(*, criterion, draft_text, model_factory, invoke=None):
        raise RuntimeError("judge exploded")

    monkeypatch.setattr(criterion_engine, "evaluate_criterion", _boom)
    assert _block(_driver(), final_text="Revenue was up 30%.") is None
