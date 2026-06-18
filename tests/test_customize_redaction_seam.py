"""Bucket-A seam: a dedicated ``redaction`` Customize preset opt-ins the
hard-redaction satisfier.

``_hard_redaction_matched_requirement_labels`` (credential-clean scan of the
final answer + the no-production-attachment invariant) was gated only by
``MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED``. The new ``redaction`` preset
activates it for the runtime via ``preset_enabled`` — the same opt-in pattern as
the other satisfier seams. Byte-identical when both the env flag and the preset
are off.

Driven directly at the satisfier with a policy requiring the bare hard refs, so
the result is governed entirely by the redaction scan.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from magi_agent.cli.engine import MagiEngineDriver, RunnerPolicyAssembly
from magi_agent.customize.store import set_verification_override

_VALIDATORS = ("no_production_attachment", "public_redaction")
_EVIDENCE = ("redaction_audit",)

# A clean answer (path / email / bare word "token" must NOT count as a credential).
_CLEAN = "See /Users/kevin/notes.md and email bob@example.com about the token economy."
# A real-shaped credential assembled at runtime (no committed secret literal).
_LEAK = "Your key is sk-" + "proj-abcdef1234567890ABCDEFXYZ now."


def _driver() -> MagiEngineDriver:
    return MagiEngineDriver(
        runner=None,
        runner_policy_assembly=RunnerPolicyAssembly(
            modelProvider="local",
            modelLabel="local-dev",
            selectedPackIds=("user.quote",),
            evidenceRequirements=_EVIDENCE,
            requiredValidators=_VALIDATORS,
            missingEvidenceAction="audit",
        ),
        evidence_collector=lambda _turn: (),
    )


@pytest.fixture
def cfile(monkeypatch, tmp_path) -> Path:
    path = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(path))
    return path


def _enable(path: Path) -> None:
    set_verification_override("harness_presets", "redaction", True, path=path)


def _matched(final_text: str) -> list[str]:
    return _driver()._hard_redaction_matched_requirement_labels(final_text=final_text)


def test_inert_when_all_off(monkeypatch, cfile):
    # Byte-identical to main: env flag off + no customize ⇒ satisfier inert (the
    # bare hard refs stay missing ⇒ the gate keeps blocking, as today).
    monkeypatch.setenv("MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED", "0")
    monkeypatch.delenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", raising=False)
    assert _matched(_CLEAN) == []


def test_preset_toggle_emits_refs_on_clean_answer(monkeypatch, cfile):
    monkeypatch.setenv("MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED", "0")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    _enable(cfile)
    matched = _matched(_CLEAN)
    assert "no_production_attachment" in matched
    assert "public_redaction" in matched
    assert "redaction_audit" in matched


def test_preset_toggle_blocks_on_credential_leak(monkeypatch, cfile):
    monkeypatch.setenv("MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED", "0")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    _enable(cfile)
    matched = _matched(_LEAK)
    # A leaked credential withholds the redaction labels (gate blocks); the
    # unrelated no-production-attachment invariant is still satisfied.
    assert "public_redaction" not in matched
    assert "redaction_audit" not in matched
    assert "no_production_attachment" in matched


def test_not_activated_when_master_flag_off(monkeypatch, cfile):
    # (Master is profile-aware default-ON, so OFF is explicit "0".)
    monkeypatch.setenv("MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED", "0")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "0")
    _enable(cfile)
    assert _matched(_CLEAN) == []


def test_env_flag_still_activates_without_preset(monkeypatch, cfile):
    monkeypatch.setenv("MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED", "1")
    monkeypatch.delenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", raising=False)
    assert "public_redaction" in _matched(_CLEAN)
