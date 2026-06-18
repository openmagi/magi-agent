"""Bucket-A seam: a dedicated ``evidence-pack`` Customize preset opt-ins the
openmagi.evidence satisfier.

``_evidence_pack_matched_requirement_labels`` (the runtime issued >=1 evidence
record this turn + the audit-mode invariant) was gated only by
``MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED``. The new ``evidence-pack`` preset
activates it for the runtime via ``preset_enabled`` — the same opt-in pattern as
the other satisfier seams. Byte-identical when both the env flag and the preset
are off.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from magi_agent.cli.engine import MagiEngineDriver, RunnerPolicyAssembly
from magi_agent.customize.store import set_verification_override

_RUNTIME_REF = "runtime_evidence_record"
_NO_BLOCK_MODE = "validator:evidence:no-block-mode"


def _driver() -> MagiEngineDriver:
    return MagiEngineDriver(
        runner=None,
        runner_policy_assembly=RunnerPolicyAssembly(
            modelProvider="local",
            modelLabel="local-dev",
            selectedPackIds=("user.quote",),
            evidenceRequirements=(_RUNTIME_REF,),
            requiredValidators=(_NO_BLOCK_MODE,),
            missingEvidenceAction="audit",
        ),
        evidence_collector=lambda _turn: (),
    )


_RECORD = {"type": "ToolResult", "status": "ok"}


@pytest.fixture
def cfile(monkeypatch, tmp_path) -> Path:
    path = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(path))
    return path


def _enable(path: Path) -> None:
    set_verification_override("harness_presets", "evidence-pack", True, path=path)


def _matched(records: tuple[object, ...]) -> list[str]:
    return _driver()._evidence_pack_matched_requirement_labels(records)


def test_inert_when_all_off(monkeypatch, cfile):
    monkeypatch.setenv("MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED", "0")
    monkeypatch.delenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", raising=False)
    assert _matched((_RECORD,)) == []


def test_preset_toggle_emits_refs_with_evidence(monkeypatch, cfile):
    monkeypatch.setenv("MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED", "0")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    _enable(cfile)
    matched = _matched((_RECORD,))
    assert _RUNTIME_REF in matched
    assert _NO_BLOCK_MODE in matched


def test_preset_toggle_withholds_runtime_ref_with_no_evidence(monkeypatch, cfile):
    # No evidence record collected ⇒ runtime_evidence_record stays missing (gate
    # blocks on it); the structural audit-mode invariant is still satisfied.
    monkeypatch.setenv("MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED", "0")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    _enable(cfile)
    matched = _matched(())
    assert _RUNTIME_REF not in matched
    assert _NO_BLOCK_MODE in matched


def test_not_activated_when_master_flag_off(monkeypatch, cfile):
    monkeypatch.setenv("MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED", "0")
    # The customize master flag is profile-aware default-ON (#664), so OFF must be
    # set explicitly with "0" to exercise the master-gated-off path.
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "0")
    _enable(cfile)
    assert _matched((_RECORD,)) == []


def test_env_flag_still_activates_without_preset(monkeypatch, cfile):
    monkeypatch.setenv("MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED", "1")
    monkeypatch.delenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", raising=False)
    assert _RUNTIME_REF in _matched((_RECORD,))
