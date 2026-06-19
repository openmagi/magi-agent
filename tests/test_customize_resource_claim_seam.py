"""H3-C-MERGE-2: resource/self-claim LLM gate + 2 opt_in seams.

``_resource_claim_llm_block`` blocks a final answer that asserts a specific
resource exists / was read / was checked while the turn produced NO source/read
evidence (``SourceInspection`` / ``WebSearch`` / ``KnowledgeSearch``). Merged
self-claim / resource-existence concern → ONE producer, two opt_in presets.
Gated by ``MAGI_VERIFY_RESOURCE_CLAIM`` OR either preset, AND a critic model
present (egress-gate cost gate).

Det pre-gate skips the model call when the turn has any source-read evidence.
Only zero-read turns reach the criterion judge, which is faked here. Fail-open.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import magi_agent.customize.criterion_engine as criterion_engine
from magi_agent.cli.engine import MagiEngineDriver, RunnerPolicyAssembly
from magi_agent.customize.store import set_verification_override


def _driver(*, with_model: bool = True, evidence: tuple = ()) -> MagiEngineDriver:
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
        evidence_collector=lambda _turn: evidence,
        criterion_model_factory=(lambda: object()) if with_model else None,
    )


def _block(driver: MagiEngineDriver, *, final_text: str = "utils.py defines foo().") -> str | None:
    return asyncio.run(
        driver._resource_claim_llm_block(turn_id="t", final_text=final_text)
    )


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
    monkeypatch.setenv("MAGI_VERIFY_RESOURCE_CLAIM", "0")
    monkeypatch.delenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", raising=False)
    monkeypatch.setattr(criterion_engine, "evaluate_criterion", _never_called())
    assert _block(_driver()) is None


def test_inert_when_no_model(monkeypatch, cfile):
    monkeypatch.setenv("MAGI_VERIFY_RESOURCE_CLAIM", "1")
    monkeypatch.setattr(criterion_engine, "evaluate_criterion", _never_called())
    assert _block(_driver(with_model=False)) is None


@pytest.mark.parametrize(
    "record_type", ("SourceInspection", "WebSearch", "KnowledgeSearch")
)
def test_source_read_turn_skips_judge(monkeypatch, cfile, record_type):
    # ANY source-evidence record ⇒ det pre-gate skips the model call.
    monkeypatch.setenv("MAGI_VERIFY_RESOURCE_CLAIM", "1")
    monkeypatch.setattr(criterion_engine, "evaluate_criterion", _never_called())
    assert _block(_driver(evidence=({"type": record_type},))) is None


def test_non_source_evidence_does_not_skip(monkeypatch, cfile):
    # A turn that ran tools but inspected no source still reaches the judge
    # (the model's role is to filter actual claim-shape).
    monkeypatch.setenv("MAGI_VERIFY_RESOURCE_CLAIM", "1")
    monkeypatch.setattr(
        criterion_engine, "evaluate_criterion", _fake_verdict(False, "unverified claim")
    )
    # GitDiff/TestRun are NOT source-read evidence.
    assert _block(_driver(evidence=({"type": "GitDiff"},))) == "unverified claim"


def test_zero_read_claim_blocks(monkeypatch, cfile):
    monkeypatch.setenv("MAGI_VERIFY_RESOURCE_CLAIM", "1")
    monkeypatch.setattr(
        criterion_engine, "evaluate_criterion", _fake_verdict(False, "unverified resource")
    )
    assert _block(_driver(evidence=())) == "unverified resource"


def test_zero_read_general_answer_passes(monkeypatch, cfile):
    # A general answer that does not assert reading anything is pass=true.
    monkeypatch.setenv("MAGI_VERIFY_RESOURCE_CLAIM", "1")
    monkeypatch.setattr(criterion_engine, "evaluate_criterion", _fake_verdict(True))
    assert _block(_driver(evidence=()), final_text="Here is some general advice.") is None


@pytest.mark.parametrize("preset", ("self-claim", "resource-existence"))
def test_either_preset_activates(monkeypatch, cfile, preset):
    monkeypatch.setenv("MAGI_VERIFY_RESOURCE_CLAIM", "0")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    set_verification_override("harness_presets", preset, True, path=cfile)
    monkeypatch.setattr(criterion_engine, "evaluate_criterion", _fake_verdict(False, "x"))
    assert _block(_driver(evidence=())) == "x"


def test_not_activated_when_master_flag_off(monkeypatch, cfile):
    monkeypatch.setenv("MAGI_VERIFY_RESOURCE_CLAIM", "0")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "0")
    set_verification_override("harness_presets", "self-claim", True, path=cfile)
    monkeypatch.setattr(criterion_engine, "evaluate_criterion", _never_called())
    assert _block(_driver(evidence=())) is None


def test_fail_open_on_judge_error(monkeypatch, cfile):
    monkeypatch.setenv("MAGI_VERIFY_RESOURCE_CLAIM", "1")

    async def _boom(*, criterion, draft_text, model_factory, invoke=None):
        raise RuntimeError("judge exploded")

    monkeypatch.setattr(criterion_engine, "evaluate_criterion", _boom)
    assert _block(_driver(evidence=())) is None
