# tests/test_autopilot_presets.py
from __future__ import annotations

from magi_agent.harness.presets import (
    PresetCategory,
    builtin_preset_by_key,
    builtin_preset_keys,
)

AUTOPILOT_PRESET_KEYS = {
    "autopilot-phase-router",
    "autopilot-interview-gate",
    "autopilot-consensus-gate",
    "autopilot-review-gate",
    "autopilot-qa-gate",
}


def test_all_autopilot_presets_present() -> None:
    assert AUTOPILOT_PRESET_KEYS <= set(builtin_preset_keys())


def test_autopilot_presets_are_default_off_and_env_gated() -> None:
    for key in AUTOPILOT_PRESET_KEYS:
        preset = builtin_preset_by_key(key)
        assert preset.category is PresetCategory.TASK
        assert preset.default_on is False
        assert preset.opt_out is True
        assert preset.hard_safety is False
        assert "MAGI_AUTOPILOT" in preset.env_gates


def test_autopilot_gate_presets_reference_fsm_gates() -> None:
    assert "interview-ambiguity-cleared" in builtin_preset_by_key("autopilot-interview-gate").verifier_gates
    assert "consensus-architect-then-critic" in builtin_preset_by_key("autopilot-consensus-gate").verifier_gates
    assert "review-clean" in builtin_preset_by_key("autopilot-review-gate").verifier_gates
    assert "coding-child-review" in builtin_preset_by_key("autopilot-review-gate").verifier_gates
    assert "adversarial-qa" in builtin_preset_by_key("autopilot-qa-gate").verifier_gates


def test_autopilot_preset_blocking_and_scope_attributes() -> None:
    router = builtin_preset_by_key("autopilot-phase-router")
    assert router.blocking is True
    assert router.fail_open is True
    assert router.scope_hints == ("autopilot",)

    interview = builtin_preset_by_key("autopilot-interview-gate")
    assert interview.blocking is True
    assert interview.fail_open is True

    for key in ("autopilot-consensus-gate", "autopilot-review-gate", "autopilot-qa-gate"):
        preset = builtin_preset_by_key(key)
        assert preset.blocking is None
        assert preset.fail_open is None


def test_verifier_backed_fsm_gates_each_have_a_preset() -> None:
    # The verifier-bus-backed FSM gates (from autopilot._GATE_BY_PHASE) must each be
    # referenced by some autopilot preset. `execution-evidence` (EXECUTE phase) is
    # intentionally excluded: it reuses coding-verification/goal-progress, not a new gate.
    verifier_backed_gates = {
        "interview-ambiguity-cleared",
        "consensus-architect-then-critic",
        "review-clean",
        "adversarial-qa",
    }
    referenced: set[str] = set()
    for key in AUTOPILOT_PRESET_KEYS:
        referenced.update(builtin_preset_by_key(key).verifier_gates)
    assert verifier_backed_gates.issubset(referenced)
