"""Phase 3: a Customize opt-in toggle activates the existing engine satisfier.

The 3 opt-in presets (fact-grounding / source-authority / artifact-delivery) are
UI for env-flag-gated engine satisfiers. With the env flag OFF, enabling the
preset in customize.json (+ master flag) must make the satisfier run exactly as
the env flag would; with both off it stays inert (byte-identical to main).

fact-grounding is the representative end-to-end case (cleanest producer). The
other two share the identical one-line gate change + the shared
``customize.runtime_gate.preset_enabled`` helper (unit-tested separately).
"""
from __future__ import annotations

from magi_agent.cli.engine import MagiEngineDriver, RunnerPolicyAssembly
from magi_agent.customize.store import set_verification_override


def _driver() -> MagiEngineDriver:
    return MagiEngineDriver(
        runner=None,
        runner_policy_assembly=RunnerPolicyAssembly(
            modelProvider="local",
            modelLabel="local-dev",
            selectedPackIds=("user.quote",),  # non-dev-coding → gate applies
            evidenceRequirements=(),
            requiredValidators=("fact_grounding",),
            missingEvidenceAction="audit",
        ),
        evidence_collector=lambda _turn: (),
    )


# A semantic-only answer with no specific value to ground → producer "grounded"
# (the G4 boundary), so a running satisfier returns the label.
_GROUNDED_TEXT = "The system works well overall."


def test_fact_grounding_inert_when_all_off(monkeypatch, tmp_path):
    monkeypatch.delenv("MAGI_FACT_GROUNDING_VERIFICATION_ENABLED", raising=False)
    monkeypatch.delenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", raising=False)
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(tmp_path / "customize.json"))
    out = _driver()._fact_grounding_matched_requirement_labels(
        final_text=_GROUNDED_TEXT, evidence_records=()
    )
    assert out == []  # satisfier inert → byte-identical to main


def test_fact_grounding_activated_by_customize_toggle(monkeypatch, tmp_path):
    # env flag OFF, but customize enables the preset (+ master flag on)
    monkeypatch.delenv("MAGI_FACT_GROUNDING_VERIFICATION_ENABLED", raising=False)
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    set_verification_override("harness_presets", "fact-grounding", True, path=cfile)
    out = _driver()._fact_grounding_matched_requirement_labels(
        final_text=_GROUNDED_TEXT, evidence_records=()
    )
    assert out == ["fact_grounding"]  # satisfier ran → grounded → requirement met


def test_fact_grounding_not_activated_when_master_flag_off(monkeypatch, tmp_path):
    # preset enabled in file but MASTER flag off → still inert
    monkeypatch.delenv("MAGI_FACT_GROUNDING_VERIFICATION_ENABLED", raising=False)
    monkeypatch.delenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", raising=False)
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    set_verification_override("harness_presets", "fact-grounding", True, path=cfile)
    out = _driver()._fact_grounding_matched_requirement_labels(
        final_text=_GROUNDED_TEXT, evidence_records=()
    )
    assert out == []
