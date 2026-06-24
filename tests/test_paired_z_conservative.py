"""H-29 step 1 — ``paired_verdict``'s default multiplier is statistically
conservative at small n.

The dormant paired-significance gate used to default to ``z=1.96`` (the
asymptotic-normal two-sided 95% multiplier). That multiplier UNDER-COVERS
at small n: at ``n=4`` (df=3) a proper two-sided 95% Student-t critical
is ``~3.18``, so the old default yielded a CI that was too narrow and
called regressions inconclusive (anti-conservative). REVIEW-A
``review/memory-learning.md`` M2 flagged this as the one unambiguous
correctness fix to land regardless of any other H-29 decision.

This module locks the corrected behaviour:

1. ``t_critical_two_sided_95`` returns ``>= 3.18`` for ``df=3`` (n=4).
2. ``t_critical_two_sided_95`` converges to ``1.96`` for ``df > 30``.
3. ``paired_verdict`` with the default ``z=None`` uses the t-critical
   for the actual sample, so it can be MORE conservative than the
   legacy normal multiplier at small n.
4. Explicit ``z=...`` still overrides, so legacy callers and the live
   ``run_eval_gate(config.z=1.96)`` path are unchanged.
"""

from __future__ import annotations

from magi_agent.learning.eval_gate import (
    paired_verdict,
    t_critical_two_sided_95,
)


# ---------------------------------------------------------------------------
# t_critical_two_sided_95 — table contract
# ---------------------------------------------------------------------------


def test_t_critical_at_df_3_is_at_least_t_value_not_z() -> None:
    """The H-29 plan's explicit RED case: ``n=4`` (df=3) must yield a
    multiplier ``>= 3.18`` — not the old anti-conservative ``1.96``."""

    value = t_critical_two_sided_95(df=3)
    assert value >= 3.18, value
    assert value > 1.96, value


def test_t_critical_at_df_4_is_conservative_for_n_5() -> None:
    """df=4 (n=5) two-sided 95% t-critical ≈ 2.776 — still well above
    the asymptotic-normal 1.96."""

    value = t_critical_two_sided_95(df=4)
    assert value >= 2.7
    assert value > 1.96


def test_t_critical_at_df_30_still_above_z() -> None:
    """df=30 is the boundary; the table holds a t-value (~2.04)
    strictly above the normal asymptote."""

    value = t_critical_two_sided_95(df=30)
    assert value > 1.96
    assert value < 2.1  # but already close to the normal limit


def test_t_critical_at_large_df_falls_back_to_z_1_96() -> None:
    """For df > 30 the t→z limit is close enough that the textbook
    ``z=1.96`` is the documented fallback."""

    assert t_critical_two_sided_95(df=31) == 1.96
    assert t_critical_two_sided_95(df=100) == 1.96
    assert t_critical_two_sided_95(df=10_000) == 1.96


def test_t_critical_at_df_below_1_uses_most_conservative_value() -> None:
    """df < 1 is degenerate (n <= 1); the lookup defers to df=1 so the
    multiplier is the table's most conservative value (~12.7)."""

    value = t_critical_two_sided_95(df=0)
    assert value >= 12.0


# ---------------------------------------------------------------------------
# paired_verdict — auto-selects the t-critical at small n.
# ---------------------------------------------------------------------------


def test_paired_verdict_default_z_is_conservative_at_small_n() -> None:
    """A sample whose mean improvement is just barely past the
    z=1.96-CI boundary at n=4 should be ``inconclusive`` under the new
    t-critical default (because the t-CI is wider) — NOT ``improved``
    as the old default would have called it."""

    # Hand-picked sample with mean delta = 0.10 and stdev(d) chosen so
    # ci_low under z=1.96 is just > 0 (improved) but ci_low under
    # t≈3.18 is < 0 (inconclusive).
    #
    # deltas = [0.06, 0.08, 0.12, 0.14]; mean = 0.10, stdev = ~0.0365,
    # se = stdev/sqrt(4) = ~0.01826.
    # Under z=1.96: ci_low = 0.10 - 1.96*0.01826 ≈ 0.0642 > 0 → improved.
    # Under t=3.18: ci_low = 0.10 - 3.18*0.01826 ≈ 0.0419 > 0 → improved.
    #
    # We need a sharper case. Use deltas with larger spread.
    #
    # deltas = [0.0, 0.05, 0.15, 0.20]; mean = 0.10, stdev ≈ 0.0913,
    # se ≈ 0.0456.
    # Under z=1.96: ci_low ≈ 0.10 - 1.96*0.0456 ≈ 0.0106 > 0 → improved.
    # Under t=3.18: ci_low ≈ 0.10 - 3.18*0.0456 ≈ -0.0451 < 0 → inconclusive.
    before = (0.0, 0.0, 0.0, 0.0)
    after = (0.0, 0.05, 0.15, 0.20)
    legacy = paired_verdict(before, after, z=1.96)
    new_default = paired_verdict(before, after)
    assert legacy.verdict == "improved"
    assert new_default.verdict == "inconclusive"
    # CIs are the same delta but the new default's bounds are wider.
    assert new_default.ci_low < legacy.ci_low
    assert new_default.ci_high > legacy.ci_high


def test_paired_verdict_explicit_z_still_overrides() -> None:
    """Callers that pass an explicit ``z`` keep the legacy behaviour
    (so the live ``run_eval_gate(config.z=1.96)`` path is byte-identical
    under H-29 step 1)."""

    before = (0.0, 0.0, 0.0, 0.0)
    after = (0.0, 0.05, 0.15, 0.20)
    pinned = paired_verdict(before, after, z=1.96)
    # Recompute manually: mean=0.10, stdev≈0.0913, se≈0.0456;
    # ci_low ≈ 0.0106 (strictly positive → improved).
    assert pinned.verdict == "improved"
    assert pinned.ci_low > 0


def test_paired_verdict_at_large_n_default_matches_legacy() -> None:
    """At n > 31 the t-critical converges to z=1.96, so the default
    multiplier == the explicit legacy multiplier on the same sample."""

    before = tuple(0.0 for _ in range(40))
    after = tuple(0.05 + 0.01 * (i % 5) for i in range(40))
    default = paired_verdict(before, after)
    pinned = paired_verdict(before, after, z=1.96)
    assert default.verdict == pinned.verdict
    assert default.ci_low == pinned.ci_low
    assert default.ci_high == pinned.ci_high
