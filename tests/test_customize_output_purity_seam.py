"""H3-C3: output-purity LLM gate + opt_in seam.

``_output_purity_llm_block`` blocks a final answer that leaks internal data —
raw tool-result envelopes, reasoning traces, or canonical private payload keys
in JSON shape. Det pre-gate (a JSON-keyed private-key pattern, NOT bare prose
mentions) skips the model call on clean answers; suspicious answers reach the
criterion judge to distinguish a legitimate JSON answer from a raw envelope
leak.

Gated by ``MAGI_VERIFY_OUTPUT_PURITY`` OR the ``output-purity`` preset, AND a
critic model present (egress-gate cost gate). Fail-open.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import magi_agent.customize.criterion_engine as criterion_engine
from magi_agent.cli.engine import MagiEngineDriver, RunnerPolicyAssembly
from magi_agent.customize.store import set_verification_override

# A suspicious draft: contains a canonical private key in JSON shape, so the
# det pre-gate flags it and the criterion judge is consulted.
_SUSPICIOUS = (
    'Here is the tool output: {"hidden_reasoning": "step 1: think about X"}.'
)
# A clean draft mentioning a private key word in PURE PROSE — must NOT match the
# det pre-gate (the regex requires the quoted-JSON-key shape).
_CLEAN_PROSE = (
    "When you study LLM agents, you'll encounter chain_of_thought prompting "
    "and discussions of hidden_reasoning patterns."
)
# A legitimate JSON answer with a documentation-style use of the keys.
_LEGIT_JSON = (
    'Here is an example schema you asked about: {"hidden_reasoning": "<string>"}.'
)


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
    return asyncio.run(driver._output_purity_llm_block(final_text=final_text))


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
    monkeypatch.setenv("MAGI_VERIFY_OUTPUT_PURITY", "0")
    monkeypatch.delenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", raising=False)
    monkeypatch.setattr(criterion_engine, "evaluate_criterion", _never_called())
    assert _block(_driver(), final_text=_SUSPICIOUS) is None


def test_inert_when_no_model(monkeypatch, cfile):
    monkeypatch.setenv("MAGI_VERIFY_OUTPUT_PURITY", "1")
    monkeypatch.setattr(criterion_engine, "evaluate_criterion", _never_called())
    assert _block(_driver(with_model=False), final_text=_SUSPICIOUS) is None


def test_clean_prose_skips_judge(monkeypatch, cfile):
    # Bare prose mentions of private key words must NOT trigger the model call.
    monkeypatch.setenv("MAGI_VERIFY_OUTPUT_PURITY", "1")
    monkeypatch.setattr(criterion_engine, "evaluate_criterion", _never_called())
    assert _block(_driver(), final_text=_CLEAN_PROSE) is None


def test_no_private_keys_skips_judge(monkeypatch, cfile):
    # A generic answer with no private keys at all ⇒ pre-gate skips the judge.
    monkeypatch.setenv("MAGI_VERIFY_OUTPUT_PURITY", "1")
    monkeypatch.setattr(criterion_engine, "evaluate_criterion", _never_called())
    assert _block(_driver(), final_text="Here is a plain-text answer.") is None


def test_suspicious_envelope_reaches_judge_and_blocks(monkeypatch, cfile):
    monkeypatch.setenv("MAGI_VERIFY_OUTPUT_PURITY", "1")
    monkeypatch.setattr(
        criterion_engine, "evaluate_criterion", _fake_verdict(False, "internal leak")
    )
    assert _block(_driver(), final_text=_SUSPICIOUS) == "internal leak"


def test_legitimate_json_answer_passes(monkeypatch, cfile):
    # The criterion engine passes a documentation-style JSON answer.
    monkeypatch.setenv("MAGI_VERIFY_OUTPUT_PURITY", "1")
    monkeypatch.setattr(criterion_engine, "evaluate_criterion", _fake_verdict(True))
    assert _block(_driver(), final_text=_LEGIT_JSON) is None


def test_preset_toggle_activates(monkeypatch, cfile):
    monkeypatch.setenv("MAGI_VERIFY_OUTPUT_PURITY", "0")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    set_verification_override("harness_presets", "output-purity", True, path=cfile)
    monkeypatch.setattr(criterion_engine, "evaluate_criterion", _fake_verdict(False, "x"))
    assert _block(_driver(), final_text=_SUSPICIOUS) == "x"


def test_not_activated_when_master_flag_off(monkeypatch, cfile):
    monkeypatch.setenv("MAGI_VERIFY_OUTPUT_PURITY", "0")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "0")
    set_verification_override("harness_presets", "output-purity", True, path=cfile)
    monkeypatch.setattr(criterion_engine, "evaluate_criterion", _never_called())
    assert _block(_driver(), final_text=_SUSPICIOUS) is None


def test_fail_open_on_judge_error(monkeypatch, cfile):
    monkeypatch.setenv("MAGI_VERIFY_OUTPUT_PURITY", "1")

    async def _boom(*, criterion, draft_text, model_factory, invoke=None):
        raise RuntimeError("judge exploded")

    monkeypatch.setattr(criterion_engine, "evaluate_criterion", _boom)
    assert _block(_driver(), final_text=_SUSPICIOUS) is None


@pytest.mark.parametrize(
    "key",
    (
        "hidden_reasoning",
        "chain_of_thought",
        "private_reasoning",
        "reasoning_trace",
        "private_tool_preview",
        "private_tool_input",
        "private_tool_output",
        "raw_tool_preview",
        "raw_connector_credentials",
        "child_private_records",
        "private_preview",
    ),
)
def test_all_canonical_keys_in_json_shape_trigger_judge(monkeypatch, cfile, key):
    # Every key from gate3b._PRIVATE_KEYS in JSON shape must reach the judge.
    monkeypatch.setenv("MAGI_VERIFY_OUTPUT_PURITY", "1")
    monkeypatch.setattr(
        criterion_engine, "evaluate_criterion", _fake_verdict(False, "leak")
    )
    assert _block(_driver(), final_text=f'{{"{key}": "x"}}') == "leak"
