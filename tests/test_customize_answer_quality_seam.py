"""H3-C1: answer-quality LLM gate + opt_in seam.

``_answer_quality_llm_block`` judges whether the final answer genuinely addresses
the user task via the generic ``criterion_engine.evaluate_criterion`` judge.
Gated by ``MAGI_VERIFY_ANSWER_QUALITY`` OR the ``answer-quality`` preset, AND a
critic model must be available (``criterion_model_factory`` — the egress-gate
cost gate). Fail-open: no model / off / error ⇒ None (byte-identical).

The model is faked and ``evaluate_criterion`` is monkeypatched, so no real model
call happens — these exercise the gating + verdict→block mapping deterministically.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import magi_agent.customize.criterion_engine as criterion_engine
from magi_agent.cli.engine import MagiEngineDriver, RunnerPolicyAssembly
from magi_agent.customize.store import set_verification_override

_PROMPT = "Summarize the Q3 revenue report."


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


def _block(driver: MagiEngineDriver, *, final_text: str = "An answer.") -> str | None:
    return asyncio.run(
        driver._answer_quality_llm_block(prompt=_PROMPT, final_text=final_text)
    )


def _fake_verdict(passed: bool, reason: str = "r"):
    async def _ev(*, criterion, draft_text, model_factory, invoke=None):
        return (passed, reason)

    return _ev


@pytest.fixture
def cfile(monkeypatch, tmp_path) -> Path:
    path = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(path))
    return path


def _enable_preset(path: Path) -> None:
    set_verification_override("harness_presets", "answer-quality", True, path=path)


def test_inert_when_flag_off(monkeypatch, cfile):
    monkeypatch.setenv("MAGI_VERIFY_ANSWER_QUALITY", "0")
    monkeypatch.delenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", raising=False)
    monkeypatch.setattr(criterion_engine, "evaluate_criterion", _fake_verdict(False))
    assert _block(_driver()) is None


def test_inert_when_no_model(monkeypatch, cfile):
    # Flag on but no critic model (egress gate off) ⇒ fail-open, no judge call.
    monkeypatch.setenv("MAGI_VERIFY_ANSWER_QUALITY", "1")
    monkeypatch.setattr(criterion_engine, "evaluate_criterion", _fake_verdict(False))
    assert _block(_driver(with_model=False)) is None


def test_flag_on_blocks_on_fail_verdict(monkeypatch, cfile):
    monkeypatch.setenv("MAGI_VERIFY_ANSWER_QUALITY", "1")
    monkeypatch.setattr(
        criterion_engine, "evaluate_criterion", _fake_verdict(False, "empty answer")
    )
    assert _block(_driver()) == "empty answer"


def test_flag_on_passes_on_pass_verdict(monkeypatch, cfile):
    monkeypatch.setenv("MAGI_VERIFY_ANSWER_QUALITY", "1")
    monkeypatch.setattr(criterion_engine, "evaluate_criterion", _fake_verdict(True))
    assert _block(_driver()) is None


def test_preset_toggle_activates(monkeypatch, cfile):
    monkeypatch.setenv("MAGI_VERIFY_ANSWER_QUALITY", "0")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    _enable_preset(cfile)
    monkeypatch.setattr(criterion_engine, "evaluate_criterion", _fake_verdict(False, "x"))
    assert _block(_driver()) == "x"


def test_not_activated_when_master_flag_off(monkeypatch, cfile):
    monkeypatch.setenv("MAGI_VERIFY_ANSWER_QUALITY", "0")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "0")
    _enable_preset(cfile)
    monkeypatch.setattr(criterion_engine, "evaluate_criterion", _fake_verdict(False))
    assert _block(_driver()) is None


def test_criterion_embeds_the_task(monkeypatch, cfile):
    # The user task must reach the judge via the criterion slot.
    monkeypatch.setenv("MAGI_VERIFY_ANSWER_QUALITY", "1")
    captured: dict[str, str] = {}

    async def _capture(*, criterion, draft_text, model_factory, invoke=None):
        captured["criterion"] = criterion
        captured["draft"] = draft_text
        return (True, "ok")

    monkeypatch.setattr(criterion_engine, "evaluate_criterion", _capture)
    _block(_driver(), final_text="The revenue was up.")
    assert _PROMPT in captured["criterion"]
    assert captured["draft"] == "The revenue was up."


def test_fail_open_on_judge_error(monkeypatch, cfile):
    monkeypatch.setenv("MAGI_VERIFY_ANSWER_QUALITY", "1")

    async def _boom(*, criterion, draft_text, model_factory, invoke=None):
        raise RuntimeError("judge exploded")

    monkeypatch.setattr(criterion_engine, "evaluate_criterion", _boom)
    assert _block(_driver()) is None
