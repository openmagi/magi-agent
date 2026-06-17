"""Phase 2: customize verification opt-out → RunnerPolicyAssembly wiring.

The recipe-driven gate is default-ON (full profile) and the default task profile
selects every pack, so ``verifier:dev-coding:test-evidence`` is required by
default. The Customize tab's job is opt-OUT: explicitly disabling
coding-verification removes that ref. These exercise the real
``_build_default_runner_policy_assembly`` merge point (assembly only builds when
MAGI_EVIDENCE_COMPLETION_GATE_ENABLED is on).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from magi_agent.cli.real_runner import _build_default_runner_policy_assembly
from magi_agent.customize.store import set_custom_rule, set_verification_override

_CODING_REF = "verifier:dev-coding:test-evidence"


def _det_rule(ref: str, rid: str = "cr_test"):
    return {
        "id": rid,
        "scope": "coding",
        "enabled": True,
        "what": {"kind": "deterministic_ref", "payload": {"ref": ref}},
        "firesAt": "pre_final",
        "action": "block",
    }


def _build():
    return _build_default_runner_policy_assembly(
        model_provider="local",
        model_label="local-dev",
        live_policy_callback_attached=False,
    )


def _set(cfile: Path, preset_id: str, enabled: bool) -> None:
    set_verification_override("harness_presets", preset_id, enabled, path=cfile)


@pytest.fixture
def gate_on(monkeypatch, tmp_path):
    monkeypatch.setenv("MAGI_EVIDENCE_COMPLETION_GATE_ENABLED", "1")
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    return cfile


def test_coding_verification_required_by_default(gate_on, monkeypatch):
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    assembly = _build()
    assert assembly is not None
    assert _CODING_REF in assembly.required_validators


def test_opt_out_removes_ref_when_flag_on(gate_on, monkeypatch):
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    _set(gate_on, "coding-verification", False)  # explicit opt-out
    assembly = _build()
    assert assembly is not None
    assert _CODING_REF not in assembly.required_validators


def test_opt_out_has_no_effect_when_flag_off(gate_on, monkeypatch):
    """Regression: with the customize flag OFF, an opt-out in customize.json must
    NOT change the assembled policy (byte-identical to baseline)."""
    monkeypatch.delenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", raising=False)
    _set(gate_on, "coding-verification", False)
    assembly = _build()
    assert assembly is not None
    assert _CODING_REF in assembly.required_validators


def test_unrelated_opt_out_does_not_remove_coding_ref(gate_on, monkeypatch):
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    _set(gate_on, "answer-quality", False)  # not a Phase-2 seam
    assembly = _build()
    assert assembly is not None
    assert _CODING_REF in assembly.required_validators


# --- Custom deterministic_ref rule compilation (P1) ---
# Tested at the _apply_customize_verification seam directly: the 3 producer-backed
# menu refs are ALL already in the default assembly, so a controlled input list
# is the only way to observe the add discriminatingly.
from magi_agent.cli.real_runner import _apply_customize_verification  # noqa: E402


def test_custom_det_rule_adds_ref_when_both_flags_on(gate_on, monkeypatch):
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    set_custom_rule(_det_rule("evidence:git-diff"), path=gate_on)
    out = _apply_customize_verification(["seed:ref"])
    assert "evidence:git-diff" in out


def test_custom_det_rule_inert_when_custom_flag_off(gate_on, monkeypatch):
    # master ON but custom-rules flag OFF → rule persists but does NOT compile.
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.delenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", raising=False)
    set_custom_rule(_det_rule("evidence:git-diff"), path=gate_on)
    out = _apply_customize_verification(["seed:ref"])
    assert "evidence:git-diff" not in out


def test_custom_det_rule_inert_when_master_flag_off(gate_on, monkeypatch):
    monkeypatch.delenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", raising=False)
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    set_custom_rule(_det_rule("evidence:git-diff"), path=gate_on)
    assert _apply_customize_verification(["seed:ref"]) == ["seed:ref"]


def test_disabled_custom_det_rule_not_added(gate_on, monkeypatch):
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    rule = _det_rule("evidence:git-diff")
    rule["enabled"] = False
    set_custom_rule(rule, path=gate_on)
    out = _apply_customize_verification(["seed:ref"])
    assert "evidence:git-diff" not in out
