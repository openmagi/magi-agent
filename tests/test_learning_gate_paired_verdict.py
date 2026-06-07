"""Task 1 — paired statistical-significance foundation for the eval gate.

Tests the pure ``paired_verdict`` stats function plus the additive (dormant)
config/decision fields.  Nothing here exercises ``run_eval_gate``: Task 1 is the
dormant foundation only, so the gate's strict-band behavior is unchanged (that
is proven by ``test_learning_pr4_eval_gate.py`` continuing to pass unchanged).

``paired_verdict`` runs a paired one-sided significance check on the per-case
deltas ``d_i = after_i - before_i`` (each score in ``[0, 1]``, higher better):

- improved      : CI lower bound > 0 (after significantly beats before)
- regressed     : CI upper bound < 0 (after significantly worse)
- inconclusive  : CI straddles 0 (noise dominates)
- underpowered  : not enough paired samples to test (defer)
"""
from __future__ import annotations

import math

import pytest

from magi_agent.learning.eval_gate import (
    EvalGateConfig,
    EvalGateDecision,
    PairedVerdict,
    paired_verdict,
)


# ---------------------------------------------------------------------------
# paired_verdict — verdict classification
# ---------------------------------------------------------------------------


def test_improved_clear_separation() -> None:
    """All cases improve with low noise → CI lower bound > 0 → improved."""
    before = (0.40, 0.42, 0.41, 0.39, 0.40)
    after = (0.80, 0.82, 0.81, 0.79, 0.80)
    v = paired_verdict(before, after)
    assert v.verdict == "improved"
    assert v.n == 5
    assert v.delta > 0
    assert v.ci_low > 0
    assert v.se > 0


def test_regressed_clear_separation() -> None:
    """All cases degrade with low noise → CI upper bound < 0 → regressed."""
    before = (0.80, 0.82, 0.81, 0.79, 0.80)
    after = (0.40, 0.42, 0.41, 0.39, 0.40)
    v = paired_verdict(before, after)
    assert v.verdict == "regressed"
    assert v.delta < 0
    assert v.ci_high < 0


def test_inconclusive_straddles_zero() -> None:
    """Deltas of mixed sign with real spread → CI straddles 0 → inconclusive."""
    before = (0.50, 0.50, 0.50, 0.50, 0.50, 0.50)
    after = (0.90, 0.10, 0.85, 0.15, 0.80, 0.20)
    v = paired_verdict(before, after)
    assert v.verdict == "inconclusive"
    assert v.ci_low < 0 < v.ci_high


# ---------------------------------------------------------------------------
# paired_verdict — underpowered (defer)
# ---------------------------------------------------------------------------


def test_underpowered_below_min_n() -> None:
    """n < min_n → underpowered, se=0, ci collapses to delta."""
    before = (0.40, 0.50)
    after = (0.80, 0.90)
    v = paired_verdict(before, after, min_n=4)
    assert v.verdict == "underpowered"
    assert v.n == 2
    assert v.se == 0.0
    assert v.ci_low == v.delta == v.ci_high
    assert v.delta == pytest.approx(0.40)


def test_underpowered_single_sample() -> None:
    """n < 2 → underpowered even if min_n is lowered (stdev undefined)."""
    before = (0.40,)
    after = (0.90,)
    v = paired_verdict(before, after, min_n=1)
    assert v.verdict == "underpowered"
    assert v.n == 1
    assert v.se == 0.0
    assert v.ci_low == v.delta == v.ci_high


# ---------------------------------------------------------------------------
# paired_verdict — all-equal deltas (stdev == 0 → se == 0)
# ---------------------------------------------------------------------------


def test_all_equal_positive_deltas_improved() -> None:
    """Identical positive deltas: se=0, ci==delta>0 → improved."""
    before = (0.40, 0.40, 0.40, 0.40)
    after = (0.60, 0.60, 0.60, 0.60)
    v = paired_verdict(before, after)
    assert v.verdict == "improved"
    assert v.se == 0.0
    assert v.ci_low == v.ci_high == v.delta
    assert v.delta == pytest.approx(0.20)


def test_all_equal_negative_deltas_regressed() -> None:
    """Identical negative deltas: se=0, ci==delta<0 → regressed."""
    before = (0.60, 0.60, 0.60, 0.60)
    after = (0.40, 0.40, 0.40, 0.40)
    v = paired_verdict(before, after)
    assert v.verdict == "regressed"
    assert v.se == 0.0
    assert v.delta == pytest.approx(-0.20)


def test_all_equal_zero_deltas_inconclusive() -> None:
    """Identical zero deltas: se=0, ci==delta==0 → inconclusive."""
    before = (0.50, 0.50, 0.50, 0.50)
    after = (0.50, 0.50, 0.50, 0.50)
    v = paired_verdict(before, after)
    assert v.verdict == "inconclusive"
    assert v.se == 0.0
    assert v.delta == 0.0
    assert v.ci_low == v.ci_high == 0.0


# ---------------------------------------------------------------------------
# paired_verdict — guards & math details
# ---------------------------------------------------------------------------


def test_length_mismatch_raises() -> None:
    """Mismatched before/after lengths → ValueError (incomparable samples)."""
    with pytest.raises(ValueError):
        paired_verdict((0.4, 0.5, 0.6), (0.8, 0.9))


def test_ci_bounds_match_formula() -> None:
    """ci_low/ci_high equal delta ± z*se with the supplied z."""
    before = (0.40, 0.42, 0.41, 0.39, 0.40)
    after = (0.80, 0.82, 0.81, 0.79, 0.80)
    z = 2.576
    v = paired_verdict(before, after, z=z)
    assert v.ci_low == pytest.approx(v.delta - z * v.se)
    assert v.ci_high == pytest.approx(v.delta + z * v.se)


def test_se_is_sample_stdev_over_sqrt_n() -> None:
    """se == statistics.stdev(d) / sqrt(n) for a known spread."""
    import statistics

    before = (0.00, 0.00, 0.00, 0.00)
    after = (0.10, 0.20, 0.30, 0.40)
    v = paired_verdict(before, after)
    deltas = [a - b for a, b in zip(after, before)]
    expected_se = statistics.stdev(deltas) / math.sqrt(len(deltas))
    assert v.se == pytest.approx(expected_se)


def test_paired_verdict_result_is_frozen() -> None:
    """PairedVerdict is immutable (frozen)."""
    v = paired_verdict((0.4, 0.4, 0.4, 0.4), (0.6, 0.6, 0.6, 0.6))
    assert isinstance(v, PairedVerdict)
    with pytest.raises(Exception):
        v.verdict = "regressed"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Additive config fields — defaults preserve today's behavior
# ---------------------------------------------------------------------------


def test_config_defaults_preserve_strict_band() -> None:
    cfg = EvalGateConfig()
    assert cfg.decision_rule == "strict_band"
    assert cfg.z == 1.96
    assert cfg.n_repeats == 1
    assert cfg.max_repeats == 1
    # existing defaults unchanged
    assert cfg.min_sample_size == 4
    assert cfg.max_regression_band == 0.0


def test_config_accepts_paired_significance_via_alias() -> None:
    cfg = EvalGateConfig(decisionRule="paired_significance", nRepeats=3, maxRepeats=5)
    assert cfg.decision_rule == "paired_significance"
    assert cfg.n_repeats == 3
    assert cfg.max_repeats == 5


def test_config_old_construction_still_works() -> None:
    """Constructing with only the pre-existing fields is unaffected."""
    cfg = EvalGateConfig(minSampleSize=8, maxRegressionBand=0.05)
    assert cfg.min_sample_size == 8
    assert cfg.max_regression_band == 0.05
    assert cfg.decision_rule == "strict_band"


# ---------------------------------------------------------------------------
# Additive decision fields — defaults so old construction sites are unaffected
# ---------------------------------------------------------------------------


def test_decision_defaults_for_new_fields() -> None:
    d = EvalGateDecision(itemId="x", kind="example", passed=True, activated=False)
    assert d.verdict == ""
    assert d.delta == 0.0
    assert d.se == 0.0
    assert d.ci_low == 0.0
    assert d.ci_high == 0.0
    assert d.repeats == 1


def test_decision_accepts_new_fields_via_alias() -> None:
    d = EvalGateDecision(
        itemId="x",
        kind="example",
        passed=True,
        activated=True,
        verdict="improved",
        delta=0.2,
        se=0.01,
        ciLow=0.18,
        ciHigh=0.22,
        repeats=3,
    )
    assert d.verdict == "improved"
    assert d.ci_low == 0.18
    assert d.ci_high == 0.22
    assert d.repeats == 3


def test_empty_inputs_are_underpowered() -> None:
    # equal (zero) lengths reach the n<min_n short-circuit; no divide-by-zero
    v = paired_verdict((), ())
    assert v.verdict == "underpowered"
    assert v.n == 0
    assert v.delta == 0.0


# ---------------------------------------------------------------------------
# paired_verdict — non-finite (NaN/inf) inputs defer as underpowered
# ---------------------------------------------------------------------------


def test_nan_in_before_defers_underpowered() -> None:
    """A NaN score in ``before`` defers as underpowered (not inconclusive)."""
    before = (0.40, math.nan, 0.41, 0.39)
    after = (0.80, 0.82, 0.81, 0.79)
    v = paired_verdict(before, after)
    assert v.verdict == "underpowered"
    assert v.se == 0.0
    assert v.ci_low == v.ci_high == 0.0


def test_inf_in_after_defers_underpowered() -> None:
    """An inf score in ``after`` defers as underpowered (not a passable result)."""
    before = (0.40, 0.42, 0.41, 0.39)
    after = (0.80, math.inf, 0.81, 0.79)
    v = paired_verdict(before, after)
    assert v.verdict == "underpowered"
    assert v.se == 0.0
    assert v.ci_low == v.ci_high == 0.0


def test_nan_scored_regression_does_not_classify_regressed() -> None:
    """A would-be regression with a NaN score defers rather than reporting a
    decisive verdict — the degenerate sample is not trusted."""
    before = (0.80, 0.82, 0.81, 0.79)
    after = (0.40, math.nan, 0.41, 0.39)
    v = paired_verdict(before, after)
    assert v.verdict == "underpowered"
