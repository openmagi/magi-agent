"""H2-C9: response-language policy gate + opt_in seam.

``_response_language_block_labels`` wires the previously-dormant
``discipline_boundary.response_language`` check to the live pre-final gate. When
active (``MAGI_VERIFY_RESPONSE_LANGUAGE`` OR the ``response-language`` Customize
preset) AND a policy is configured (``MAGI_RESPONSE_LANGUAGE``, e.g. ``"ko"``), a
final answer that violates the policy is blocked. No policy ⇒ never blocks (no
fake toggle). Byte-identical when off.

Driven directly at the method so the result is governed by gate + policy +
boundary verdict.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from magi_agent.cli.engine import MagiEngineDriver, RunnerPolicyAssembly
from magi_agent.customize.store import set_verification_override

_BLOCK = "response_language:policy_violation"
_KOREAN = "이것은 한국어 답변입니다."
_ENGLISH = "This is an English answer."


def _driver() -> MagiEngineDriver:
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
    )


@pytest.fixture
def cfile(monkeypatch, tmp_path) -> Path:
    path = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(path))
    return path


def _enable_preset(path: Path) -> None:
    set_verification_override("harness_presets", "response-language", True, path=path)


def _labels(final_text: str) -> list[str]:
    return _driver()._response_language_block_labels(final_text=final_text)


def test_inert_when_all_off(monkeypatch, cfile):
    # Byte-identical: gate off + no customize ⇒ no block even with a policy set.
    monkeypatch.setenv("MAGI_VERIFY_RESPONSE_LANGUAGE", "0")
    monkeypatch.setenv("MAGI_RESPONSE_LANGUAGE", "ko")
    monkeypatch.delenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", raising=False)
    assert _labels(_ENGLISH) == []


def test_no_policy_never_blocks(monkeypatch, cfile):
    # Gate on but NO policy configured ⇒ no block (no fake toggle).
    monkeypatch.setenv("MAGI_VERIFY_RESPONSE_LANGUAGE", "1")
    monkeypatch.delenv("MAGI_RESPONSE_LANGUAGE", raising=False)
    assert _labels(_ENGLISH) == []


def test_env_flag_blocks_policy_violation(monkeypatch, cfile):
    monkeypatch.setenv("MAGI_VERIFY_RESPONSE_LANGUAGE", "1")
    monkeypatch.setenv("MAGI_RESPONSE_LANGUAGE", "ko")
    assert _labels(_ENGLISH) == [_BLOCK]


def test_env_flag_passes_compliant_answer(monkeypatch, cfile):
    monkeypatch.setenv("MAGI_VERIFY_RESPONSE_LANGUAGE", "1")
    monkeypatch.setenv("MAGI_RESPONSE_LANGUAGE", "ko")
    assert _labels(_KOREAN) == []


def test_preset_toggle_activates(monkeypatch, cfile):
    monkeypatch.setenv("MAGI_VERIFY_RESPONSE_LANGUAGE", "0")
    monkeypatch.setenv("MAGI_RESPONSE_LANGUAGE", "ko")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    _enable_preset(cfile)
    assert _labels(_ENGLISH) == [_BLOCK]


def test_not_activated_when_master_flag_off(monkeypatch, cfile):
    monkeypatch.setenv("MAGI_VERIFY_RESPONSE_LANGUAGE", "0")
    monkeypatch.setenv("MAGI_RESPONSE_LANGUAGE", "ko")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "0")
    _enable_preset(cfile)
    assert _labels(_ENGLISH) == []
