"""H3-C2: pre-refusal LLM gate + opt_in seam.

``_pre_refusal_llm_block`` judges via the generic
``criterion_engine.evaluate_criterion`` whether the final answer prematurely
refuses a doable task. Gated by ``MAGI_VERIFY_PRE_REFUSAL`` OR the ``pre-refusal``
preset, AND a critic model present (egress-gate cost gate). Fail-open.

The model is faked and ``evaluate_criterion`` is monkeypatched (no real model
call) — these exercise gating + verdict→block mapping deterministically.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import magi_agent.customize.criterion_engine as criterion_engine
from magi_agent.cli.engine import MagiEngineDriver, RunnerPolicyAssembly
from magi_agent.customize.store import set_verification_override

_PROMPT = "Rename the variable foo to bar in utils.py."


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


def _block(driver: MagiEngineDriver, *, final_text: str = "I can't do that.") -> str | None:
    return asyncio.run(
        driver._pre_refusal_llm_block(prompt=_PROMPT, final_text=final_text)
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
    set_verification_override("harness_presets", "pre-refusal", True, path=path)


def test_inert_when_flag_off(monkeypatch, cfile):
    monkeypatch.setenv("MAGI_VERIFY_PRE_REFUSAL", "0")
    monkeypatch.delenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", raising=False)
    monkeypatch.setattr(criterion_engine, "evaluate_criterion", _fake_verdict(False))
    assert _block(_driver()) is None


def test_inert_when_no_model(monkeypatch, cfile):
    monkeypatch.setenv("MAGI_VERIFY_PRE_REFUSAL", "1")
    monkeypatch.setattr(criterion_engine, "evaluate_criterion", _fake_verdict(False))
    assert _block(_driver(with_model=False)) is None


def test_flag_on_blocks_premature_refusal(monkeypatch, cfile):
    monkeypatch.setenv("MAGI_VERIFY_PRE_REFUSAL", "1")
    monkeypatch.setattr(
        criterion_engine, "evaluate_criterion", _fake_verdict(False, "premature refusal")
    )
    assert _block(_driver()) == "premature refusal"


def test_flag_on_passes_justified_refusal(monkeypatch, cfile):
    monkeypatch.setenv("MAGI_VERIFY_PRE_REFUSAL", "1")
    monkeypatch.setattr(criterion_engine, "evaluate_criterion", _fake_verdict(True))
    assert _block(_driver(), final_text="I cannot: utils.py does not exist.") is None


def test_preset_toggle_activates(monkeypatch, cfile):
    monkeypatch.setenv("MAGI_VERIFY_PRE_REFUSAL", "0")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    _enable_preset(cfile)
    monkeypatch.setattr(criterion_engine, "evaluate_criterion", _fake_verdict(False, "x"))
    assert _block(_driver()) == "x"


def test_not_activated_when_master_flag_off(monkeypatch, cfile):
    monkeypatch.setenv("MAGI_VERIFY_PRE_REFUSAL", "0")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "0")
    _enable_preset(cfile)
    monkeypatch.setattr(criterion_engine, "evaluate_criterion", _fake_verdict(False))
    assert _block(_driver()) is None


def test_criterion_embeds_the_task(monkeypatch, cfile):
    monkeypatch.setenv("MAGI_VERIFY_PRE_REFUSAL", "1")
    captured: dict[str, str] = {}

    async def _capture(*, criterion, draft_text, model_factory, invoke=None):
        captured["criterion"] = criterion
        return (True, "ok")

    monkeypatch.setattr(criterion_engine, "evaluate_criterion", _capture)
    _block(_driver())
    assert _PROMPT in captured["criterion"]


def test_fail_open_on_judge_error(monkeypatch, cfile):
    monkeypatch.setenv("MAGI_VERIFY_PRE_REFUSAL", "1")

    async def _boom(*, criterion, draft_text, model_factory, invoke=None):
        raise RuntimeError("judge exploded")

    monkeypatch.setattr(criterion_engine, "evaluate_criterion", _boom)
    assert _block(_driver()) is None
