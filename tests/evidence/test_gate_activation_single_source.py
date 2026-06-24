"""F-11 — single activation predicate for two-flag gate configs.

``evidence/final_output_gate.FinalOutputGateConfig`` and
``recipes/coding_evidence_gate.CodingEvidenceGateConfig`` each define
``enabled`` + ``local_evaluation_enabled`` as twin activation flags.
Pre-F-11 each gate inlined the same short-circuit at its evaluate
entry; F-11 collapses both call sites to
``evidence.gate_activation.gate_is_live(config)``.

This module locks the consolidation:

1. ``gate_is_live`` returns the correct value for all 4 combinations
   of the two booleans.
2. ``gate_is_live`` fail-closes on incomplete duck-typed configs
   (missing attrs read as ``False``).
3. A meta-test forbids any other module under ``magi_agent/`` from
   inlining the legacy ``not config.enabled or not
   config.local_evaluation_enabled`` pattern (verifying both gates
   route through the helper).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from magi_agent.evidence.gate_activation import gate_is_live


# ---------------------------------------------------------------------------
# Helper truth table
# ---------------------------------------------------------------------------


@dataclass
class _FakeConfig:
    enabled: bool = False
    local_evaluation_enabled: bool = False


@pytest.mark.parametrize(
    "enabled,local_eval,expected",
    [
        (False, False, False),
        (False, True, False),
        (True, False, False),
        (True, True, True),
    ],
)
def test_gate_is_live_truth_table(
    enabled: bool, local_eval: bool, expected: bool
) -> None:
    config = _FakeConfig(enabled=enabled, local_evaluation_enabled=local_eval)
    assert gate_is_live(config) is expected


# ---------------------------------------------------------------------------
# Fail-closed on incomplete ducks
# ---------------------------------------------------------------------------


def test_gate_is_live_missing_enabled_attr_returns_false() -> None:
    class _Partial:
        local_evaluation_enabled = True

    assert gate_is_live(_Partial()) is False


def test_gate_is_live_missing_local_attr_returns_false() -> None:
    class _Partial:
        enabled = True

    assert gate_is_live(_Partial()) is False


def test_gate_is_live_empty_object_returns_false() -> None:
    assert gate_is_live(object()) is False


def test_gate_is_live_none_returns_false() -> None:
    assert gate_is_live(None) is False


# ---------------------------------------------------------------------------
# Real-config integration
# ---------------------------------------------------------------------------


def test_final_output_gate_config_matches_gate_is_live() -> None:
    from magi_agent.evidence.final_output_gate import FinalOutputGateConfig

    off = FinalOutputGateConfig()
    only_master = FinalOutputGateConfig(enabled=True)
    only_local = FinalOutputGateConfig(localEvaluationEnabled=True)
    on = FinalOutputGateConfig(enabled=True, localEvaluationEnabled=True)
    assert gate_is_live(off) is False
    assert gate_is_live(only_master) is False
    assert gate_is_live(only_local) is False
    assert gate_is_live(on) is True


def test_coding_evidence_gate_config_matches_gate_is_live() -> None:
    from magi_agent.recipes.coding_evidence_gate import CodingEvidenceGateConfig

    off = CodingEvidenceGateConfig()
    on = CodingEvidenceGateConfig(enabled=True, localEvaluationEnabled=True)
    assert gate_is_live(off) is False
    assert gate_is_live(on) is True


# ---------------------------------------------------------------------------
# Meta-test: forbid the legacy inlined pattern outside the helper file.
# ---------------------------------------------------------------------------


def test_no_inlined_enabled_local_eval_pattern_outside_helper() -> None:
    package_root = Path(__file__).resolve().parents[1] / "magi_agent"
    if not package_root.exists():
        package_root = Path(__file__).resolve().parents[2] / "magi_agent"
    assert package_root.exists()

    # The pre-F-11 sites had the literal short-circuit
    # ``not <prefix>.enabled or not <prefix>.local_evaluation_enabled``;
    # forbid the bare two-attr conjunction anywhere outside the helper
    # itself.
    canonical = {"gate_activation.py"}
    forbidden = "config.enabled or not self.config.local_evaluation_enabled"
    offenders: list[str] = []
    for path in package_root.rglob("*.py"):
        if path.name in canonical:
            continue
        if "tests" in path.relative_to(package_root).parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if forbidden in text:
            offenders.append(str(path.relative_to(package_root)))
    assert offenders == [], (
        "Found the inlined legacy gate-activation short-circuit. "
        "Route through ``evidence/gate_activation.gate_is_live`` "
        f"instead. Offenders: {offenders}"
    )
