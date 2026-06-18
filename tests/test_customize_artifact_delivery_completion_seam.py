"""Bucket-A seam: the artifact-delivery Customize toggle wires BOTH halves.

The opt-in ``artifact-delivery`` preset already activated the deliverable
*satisfier* (``_ga_deliverable_matched_requirement_labels``) via
``customize.runtime_gate.preset_enabled``, but the *completion check*
(``_ga_deliverable_missing_labels``, which ADDS the owed
``ga_deliverable:artifactRef`` reason when a turn promised an artifact but
emitted no receipt) honored only the ``MAGI_GA_DELIVERABLE_GATE_ENABLED`` env
flag. This suite proves the preset now drives the completion check too, while
staying byte-identical when both the env flag and the preset are off.

Driven directly at ``_ga_deliverable_missing_labels`` with a policy whose
evidence requirements include an artifact label, so the gate decision is
governed entirely by the deliverable completion check.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from magi_agent.cli.engine import MagiEngineDriver, RunnerPolicyAssembly
from magi_agent.customize.store import set_verification_override

_OWED = "ga_deliverable:artifactRef"


def _driver(*, requires_artifact: bool = True) -> MagiEngineDriver:
    return MagiEngineDriver(
        runner=None,
        runner_policy_assembly=RunnerPolicyAssembly(
            modelProvider="local",
            modelLabel="local-dev",
            selectedPackIds=("openmagi.artifact-delivery",),
            evidenceRequirements=("artifact_delivery_ref",) if requires_artifact else (),
            requiredValidators=(),
            missingEvidenceAction="block",
        ),
        evidence_collector=lambda _turn: (),
    )


# A receipt record that satisfies the artifact requirement (carries artifactRef).
_RECEIPT = {"type": "GaReceipt", "artifactRef": "sha256:abc123"}


@pytest.fixture
def cfile(monkeypatch, tmp_path) -> Path:
    path = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(path))
    return path


def _enable_preset(path: Path) -> None:
    set_verification_override("harness_presets", "artifact-delivery", True, path=path)


def test_inert_when_all_off(monkeypatch, cfile):
    # Byte-identical to main: env flag off + no customize ⇒ completion check inert
    # (no owed deliverable reason added) even though the turn promised an artifact
    # and emitted no receipt.
    monkeypatch.setenv("MAGI_GA_DELIVERABLE_GATE_ENABLED", "0")
    monkeypatch.delenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", raising=False)
    assert _driver()._ga_deliverable_missing_labels(evidence_records=()) == []


def test_activated_by_customize_toggle_blocks_when_no_receipt(monkeypatch, cfile):
    # env flag OFF, but the artifact-delivery preset is enabled (+ master flag):
    # the completion check now owes the artifact ref → block reason.
    monkeypatch.setenv("MAGI_GA_DELIVERABLE_GATE_ENABLED", "0")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    _enable_preset(cfile)
    assert _driver()._ga_deliverable_missing_labels(evidence_records=()) == [_OWED]


def test_activated_by_customize_toggle_passes_when_receipt_present(monkeypatch, cfile):
    monkeypatch.setenv("MAGI_GA_DELIVERABLE_GATE_ENABLED", "0")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    _enable_preset(cfile)
    assert _driver()._ga_deliverable_missing_labels(evidence_records=(_RECEIPT,)) == []


def test_not_activated_when_master_flag_off(monkeypatch, cfile):
    # Preset enabled in the file but the customize MASTER flag is off → inert.
    # (Master is profile-aware default-ON, so OFF is explicit "0".)
    monkeypatch.setenv("MAGI_GA_DELIVERABLE_GATE_ENABLED", "0")
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "0")
    _enable_preset(cfile)
    assert _driver()._ga_deliverable_missing_labels(evidence_records=()) == []


def test_env_flag_still_activates_without_preset(monkeypatch, cfile):
    # Regression: the existing env-flag path is unchanged (no preset needed).
    monkeypatch.setenv("MAGI_GA_DELIVERABLE_GATE_ENABLED", "1")
    monkeypatch.delenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", raising=False)
    assert _driver()._ga_deliverable_missing_labels(evidence_records=()) == [_OWED]


def test_inert_when_no_artifact_label_required(monkeypatch, cfile):
    # No artifact label in the policy ⇒ nothing owed even when fully enabled
    # (no fake block on a non-deliverable turn).
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    _enable_preset(cfile)
    assert (
        _driver(requires_artifact=False)._ga_deliverable_missing_labels(
            evidence_records=()
        )
        == []
    )
