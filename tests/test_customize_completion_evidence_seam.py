"""H3-C-MERGE-1: completion/promise-without-action LLM gate + opt_in seams.

``_completion_evidence_llm_block`` blocks a final answer that claims completion
or promises future delivery while the turn produced NO action evidence. Merged
completion-evidence / goal-progress / deferral-blocker concern → ONE producer,
three opt_in presets. Gated by ``MAGI_VERIFY_COMPLETION_EVIDENCE`` OR any of those
presets, AND a critic model present (egress-gate cost gate).

Det pre-gate: a turn with ANY collected evidence skips the model call (an acting
turn can't false-block). Only zero-evidence turns reach the criterion judge,
which is faked here. Fail-open.
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


def _block(driver: MagiEngineDriver, *, final_text: str = "Done — task complete.") -> str | None:
    return asyncio.run(
        driver._completion_evidence_llm_block(turn_id="t", final_text=final_text)
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
    monkeypatch.setenv("MAGI_VERIFY_COMPLETION_EVIDENCE", "0")
    monkeypatch.delenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", raising=False)
    monkeypatch.setattr(criterion_engine, "evaluate_criterion", _never_called())
    assert _block(_driver()) is None


def test_inert_when_no_model(monkeypatch, cfile):
    monkeypatch.setenv("MAGI_VERIFY_COMPLETION_EVIDENCE", "1")
    monkeypatch.setattr(criterion_engine, "evaluate_criterion", _never_called())
    assert _block(_driver(with_model=False)) is None


def test_acting_turn_skips_judge(monkeypatch, cfile):
    # Any collected evidence ⇒ det pre-gate skips the model call (no false block).
    monkeypatch.setenv("MAGI_VERIFY_COMPLETION_EVIDENCE", "1")
    monkeypatch.setattr(criterion_engine, "evaluate_criterion", _never_called())
    assert _block(_driver(evidence=({"type": "GitDiff"},))) is None


def test_zero_evidence_completion_claim_blocks(monkeypatch, cfile):
    monkeypatch.setenv("MAGI_VERIFY_COMPLETION_EVIDENCE", "1")
    monkeypatch.setattr(
        criterion_engine, "evaluate_criterion", _fake_verdict(False, "unsupported claim")
    )
    assert _block(_driver(evidence=())) == "unsupported claim"


def test_zero_evidence_honest_failure_passes(monkeypatch, cfile):
    monkeypatch.setenv("MAGI_VERIFY_COMPLETION_EVIDENCE", "1")
    monkeypatch.setattr(criterion_engine, "evaluate_criterion", _fake_verdict(True))
    assert _block(_driver(evidence=()), final_text="I could not complete the task.") is None


@pytest.mark.parametrize("preset", ("completion-evidence", "goal-progress", "deferral-blocker"))
def test_any_of_three_presets_activates(monkeypatch, cfile, preset):
    monkeypatch.setenv("MAGI_VERIFY_COMPLETION_EVIDENCE", "0")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    set_verification_override("harness_presets", preset, True, path=cfile)
    monkeypatch.setattr(criterion_engine, "evaluate_criterion", _fake_verdict(False, "x"))
    assert _block(_driver(evidence=())) == "x"


def test_not_activated_when_master_flag_off(monkeypatch, cfile):
    monkeypatch.setenv("MAGI_VERIFY_COMPLETION_EVIDENCE", "0")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "0")
    set_verification_override("harness_presets", "completion-evidence", True, path=cfile)
    monkeypatch.setattr(criterion_engine, "evaluate_criterion", _never_called())
    assert _block(_driver(evidence=())) is None


def test_fail_open_on_judge_error(monkeypatch, cfile):
    monkeypatch.setenv("MAGI_VERIFY_COMPLETION_EVIDENCE", "1")

    async def _boom(*, criterion, draft_text, model_factory, invoke=None):
        raise RuntimeError("judge exploded")

    monkeypatch.setattr(criterion_engine, "evaluate_criterion", _boom)
    assert _block(_driver(evidence=())) is None
