"""Learning KB — eval gate (PR4).

The eval gate is the first place the deterministic candidate pipeline (PR3)
meets the store.  Before a candidate is *activated*, the gate runs a regression
check (an A/B measurement over an injected, deterministic checkset) and gates
activation on the result.  It strictly enforces the existing policy invariants
(``policy.assert_activation_allowed``) on every activation path — there is no
direct ``status="active"`` write here.

Scope (YAGNI — this is the eval gate ONLY):
- No real LLM, no live eval execution against a running agent, no network.  The
  "score" comes from an injected ``CheckSet`` (``StaticCheckSet`` for tests /
  local-fake).  The real agent-driven eval is deferred to PR7.
- Injection into prompts / cron scheduling is PR5; the dashboard is PR6.

Decision logic (named constants below):
- Require at least ``MIN_EVAL_SAMPLE_SIZE`` samples in the checkset.
- Require the regression (``before_mean - after_mean``) to stay within
  ``MAX_REGRESSION_BAND`` (i.e. ``regression <= MAX_REGRESSION_BAND``).  A
  candidate that improves or holds steady passes; one that degrades beyond the
  band fails.

Branch → store mapping on a PASS:
- ``example`` → ``store.auto_activate(...)`` (policy ``eval-observation-required``
  is satisfied by the recorded eval ref; no human needed).
- ``rule``    → left ``proposed`` (policy ``no-direct-mutation`` requires a human
  ``approval_ref``; activation happens in the PR6 dashboard).  The eval ref is
  recorded so the item is ready for ``store.approve()``.
- ``eval``    → registered as a holdout case; eval cases are not "activated as
  behavior", so they stay ``proposed`` per store semantics.

On a FAIL (regression too large OR insufficient samples) the candidate is left
``proposed`` with the failing eval observation recorded, so a human can inspect
it.  Nothing is activated.

No ``Literal[False]`` authority flags are flipped.  Writing proposed candidates
to the *injected* local learning store is the intended local behavior; the
production / live authority flags stay frozen (real production write / live
mutation is PR7).
"""
from __future__ import annotations

import hashlib
import math
import os
import statistics
from collections.abc import Mapping
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from magi_agent.learning.candidates import LearningCandidate
from magi_agent.learning.models import LearningItem
from magi_agent.learning.policy import assert_activation_allowed
from magi_agent.learning.store import LearningStore


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Verifier id declared (metadata-only) in ``harness/verifier_bus.py`` and wired
#: as a ``verifier_gate`` on the ``memory-continuity`` preset.
LEARNING_EVAL_VERIFIER_ID: str = "learning-eval"

#: Minimum number of check cases required before an activation decision is
#: trusted.  Below this the gate fails closed (does not activate).
MIN_EVAL_SAMPLE_SIZE: int = 4

#: Maximum tolerated regression (before_mean - after_mean).  A non-negative
#: regression up to this value is acceptable; anything larger fails.  ``0.0``
#: means "no measurable degradation allowed"; raise to allow small noise.
# NOTE(PR7): real LLM scores carry float noise (~1e-9..1e-6); revisit this strict
# 0.0 band / introduce an epsilon when the live evaluator lands.
MAX_REGRESSION_BAND: float = 0.0


# ---------------------------------------------------------------------------
# Paired significance verdict (Task 1 — dormant foundation, not yet wired)
# ---------------------------------------------------------------------------

#: One of four classifications of a paired before/after measurement.  Maps to a
#: future gate decision: ``improved`` → promote, ``regressed`` → reject,
#: ``inconclusive`` → hold, ``underpowered`` → defer (need more samples).  Task 2
#: wires this into ``run_eval_gate``; Task 1 only provides the pure function.
Verdict = Literal["improved", "inconclusive", "regressed", "underpowered"]


class PairedVerdict(BaseModel):
    """Frozen result of a paired one-sided significance test on per-case deltas.

    ``delta`` is the mean of ``d_i = after_i - before_i`` (positive = the
    candidate improved).  ``ci_low``/``ci_high`` are the ``z``-scaled confidence
    bounds on ``delta``; ``verdict`` is derived from where that interval falls
    relative to 0.  When the sample is underpowered the interval collapses to a
    point at ``delta`` (``se=0``) and the verdict is ``"underpowered"``.
    """

    model_config = ConfigDict(
        frozen=True,
        populate_by_name=True,
        extra="forbid",
        validate_default=True,
    )

    verdict: Verdict
    delta: float
    se: float
    ci_low: float = Field(alias="ciLow")
    ci_high: float = Field(alias="ciHigh")
    n: int


#: H-29 step 1 (NEW) — two-sided 95% Student-t critical values keyed by
#: degrees of freedom (df = n - 1). At df=3 (n=4) the t-critical is ~3.18,
#: well above the asymptotic normal ``z=1.96``; the old fixed-``z`` CI
#: under-covered at small n (anti-conservative). For df > 30 the t→z
#: limit is close enough that 1.96 is acceptable. Caller code may still
#: pass an explicit ``z`` to override, but the default now auto-selects
#: the conservative per-n multiplier.
_T_CRITICAL_TWO_SIDED_95: dict[int, float] = {
    1: 12.706,
    2: 4.303,
    3: 3.182,
    4: 2.776,
    5: 2.571,
    6: 2.447,
    7: 2.365,
    8: 2.306,
    9: 2.262,
    10: 2.228,
    11: 2.201,
    12: 2.179,
    13: 2.160,
    14: 2.145,
    15: 2.131,
    16: 2.120,
    17: 2.110,
    18: 2.101,
    19: 2.093,
    20: 2.086,
    21: 2.080,
    22: 2.074,
    23: 2.069,
    24: 2.064,
    25: 2.060,
    26: 2.056,
    27: 2.052,
    28: 2.048,
    29: 2.045,
    30: 2.042,
}
#: Large-sample asymptote: at df > 30 the t-critical is within ~3% of the
#: standard normal multiplier, so we fall back to the textbook ``z=1.96``.
_LARGE_N_Z_TWO_SIDED_95: float = 1.96


def t_critical_two_sided_95(df: int) -> float:
    """H-29 — two-sided 95% Student-t critical value for ``df`` degrees of
    freedom. ``df < 1`` defers to ``df=1`` (the most conservative tabulated
    value, ~12.7), ``df > 30`` returns the large-sample normal multiplier
    ``z=1.96``. Pure, hermetic, no scipy dependency."""

    if df < 1:
        df = 1
    if df > 30:
        return _LARGE_N_Z_TWO_SIDED_95
    return _T_CRITICAL_TWO_SIDED_95[df]


def paired_verdict(
    before: tuple[float, ...],
    after: tuple[float, ...],
    *,
    z: float | None = None,
    min_n: int = MIN_EVAL_SAMPLE_SIZE,
) -> PairedVerdict:
    """Paired one-sided significance verdict on per-case deltas.

    Computes ``d_i = after_i - before_i`` (each score in ``[0, 1]``, higher
    better) and tests whether the mean delta is significantly non-zero using a
    ``z``-scaled confidence interval on ``mean(d)``.

    - ``len(before) != len(after)`` → ``ValueError`` (incomparable samples;
      mirrors ``run_eval_gate``'s length-mismatch guard).
    - Any non-finite score (NaN/inf) in ``before``/``after`` →
      ``"underpowered"`` (safe defer) with ``se=0`` and the CI collapsed to a
      point at ``0`` — a degenerate sample cannot support any decision, so the
      gate must NOT promote nor silently treat it as a passable result.
    - ``n < min_n`` OR ``n < 2`` → ``"underpowered"`` with ``se=0`` and the CI
      collapsed to a point at ``delta`` (can't test → defer later).
    - Otherwise ``se = stdev(d) / sqrt(n)`` (sample stdev),
      ``ci = delta ± z*se``; ``"improved"`` if ``ci_low > 0``, ``"regressed"``
      if ``ci_high < 0``, else ``"inconclusive"``.  When every delta is equal
      (``stdev=0`` → ``se=0``) the interval is a point at ``delta``, so a
      non-zero ``delta`` classifies as improved/regressed and exactly ``0`` as
      inconclusive — this falls out of the same comparisons.

    This function is pure (no I/O, no store, no policy) and is NOT yet wired
    into ``run_eval_gate`` — Task 1 is the dormant foundation only.

    SMALL-n CORRECTNESS (H-29 step 1): when ``z`` is ``None`` (default) the
    multiplier is auto-selected as the two-sided 95% Student-t critical value
    for ``df = n - 1`` via :func:`t_critical_two_sided_95`. At ``n=4`` (df=3)
    that yields ``z≈3.18``, well above the asymptotic normal ``z=1.96`` — the
    old fixed default under-covered at small n (anti-conservative). For
    ``n > 31`` (df > 30) the t-multiplier converges to the textbook
    ``z=1.96``. Callers that need to pin the multiplier (e.g. a soak test
    that wants exactly the legacy ``1.96`` for byte-identical legacy output)
    can still pass ``z=1.96`` explicitly.

    NOTE(PR7): scores are assumed in ``[0, 1]``.  Non-finite (NaN/inf) inputs
    now DEFER as ``"underpowered"`` (see above) rather than silently classifying
    as ``"inconclusive"``; the live agent-driven evaluator should still validate
    the score domain before calling this.
    """
    if len(before) != len(after):
        raise ValueError(
            "paired_verdict got mismatched before/after lengths "
            f"({len(before)} != {len(after)}); the evaluator is producing "
            "incomparable samples."
        )

    # Finiteness guard: a NaN/inf score makes every CI comparison False, which
    # would silently classify as ``"inconclusive"`` — and a NaN-scored genuine
    # regression would escape the rollback signal.  Defer such degenerate
    # samples as ``"underpowered"`` (safe defer) instead.
    if not all(math.isfinite(x) for x in (*before, *after)):
        return PairedVerdict(
            verdict="underpowered",
            delta=0.0,
            se=0.0,
            ciLow=0.0,
            ciHigh=0.0,
            n=len(before),
        )

    n = len(before)
    deltas = [a - b for b, a in zip(before, after)]
    delta = sum(deltas) / n if n else 0.0

    if n < min_n or n < 2:
        return PairedVerdict(
            verdict="underpowered",
            delta=delta,
            se=0.0,
            ciLow=delta,
            ciHigh=delta,
            n=n,
        )

    se = statistics.stdev(deltas) / math.sqrt(n)
    # H-29 step 1: when caller did not pin an explicit ``z``, auto-select the
    # two-sided 95% t-critical value for ``df = n - 1`` so the CI is
    # statistically valid at small n (the old fixed ``z=1.96`` under-covered).
    if z is None:
        z = t_critical_two_sided_95(df=n - 1)
    ci_low = delta - z * se
    ci_high = delta + z * se

    if ci_low > 0:
        verdict: Verdict = "improved"
    elif ci_high < 0:
        verdict = "regressed"
    else:
        verdict = "inconclusive"

    return PairedVerdict(
        verdict=verdict,
        delta=delta,
        se=se,
        ciLow=ci_low,
        ciHigh=ci_high,
        n=n,
    )


# ---------------------------------------------------------------------------
# CheckSet protocol + deterministic static implementation
# ---------------------------------------------------------------------------


@runtime_checkable
class CheckSet(Protocol):
    """A deterministic set of eval check cases.

    ``run`` returns paired before/after per-case scores for a candidate.  Each
    score is in ``[0, 1]`` where higher is better.  The REAL, agent-driven
    evaluator (running the candidate against held-out cases) is deferred to PR7;
    it will implement THIS protocol and be injected in place of
    ``StaticCheckSet`` — no other gate code changes.
    """

    def run(
        self, candidate: LearningCandidate
    ) -> tuple[tuple[float, ...], tuple[float, ...]]:
        """Return ``(before_scores, after_scores)`` for *candidate*."""
        ...  # pragma: no cover


class StaticCheckSet(BaseModel):
    """Injected-fixture checkset — fixed before/after scores (NO LLM, NO net).

    Returns the same ``before``/``after`` score tuples for every candidate.
    Used for local-fake / test runs; PR7 replaces it with a real
    agent-driven evaluator implementing ``CheckSet``.
    """

    model_config = ConfigDict(
        frozen=True,
        populate_by_name=True,
        extra="forbid",
        validate_default=True,
    )

    before: tuple[float, ...]
    after: tuple[float, ...]

    def run(
        self, candidate: LearningCandidate
    ) -> tuple[tuple[float, ...], tuple[float, ...]]:
        return self.before, self.after


def _average_runs(
    runs: list[tuple[tuple[float, ...], tuple[float, ...]]],
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """Element-wise per-case mean of accumulated ``(before, after)`` inner runs.

    Shared by ``RepeatedCheckSet.run`` and ``run_eval_gate``'s adaptive
    escalation loop (DRY) so both compute the averaged samples identically.

    Every run must return equal-length ``before``/``after`` tuples, and all runs
    must agree on that length (mirrors ``run_eval_gate``'s length-mismatch
    guard) — otherwise the averaged samples would be incomparable, so this
    raises ``ValueError`` instead of silently averaging over a min-length slice.
    Averaging over ``k`` identical deterministic runs equals the single result,
    so a single accumulated run is exactly the inner's single output.
    """
    if not runs:
        raise ValueError("_average_runs requires at least one run.")
    case_n: int | None = None
    for before, after in runs:
        if len(before) != len(after):
            raise ValueError(
                "RepeatedCheckSet inner returned mismatched before/after "
                f"lengths ({len(before)} != {len(after)}); the evaluator "
                "is producing incomparable samples."
            )
        if case_n is None:
            case_n = len(before)
        elif len(before) != case_n:
            raise ValueError(
                "RepeatedCheckSet inner returned inconsistent case counts "
                f"across repeats ({len(before)} != {case_n}); cannot "
                "average incomparable samples."
            )

    k = len(runs)
    before_runs = [before for before, _ in runs]
    after_runs = [after for _, after in runs]
    avg_before = tuple(sum(col) / k for col in zip(*before_runs))
    avg_after = tuple(sum(col) / k for col in zip(*after_runs))
    return avg_before, avg_after


class RepeatedCheckSet:
    """CheckSet wrapper that runs an inner ``CheckSet`` ``repeats`` times and
    averages the per-case scores element-wise (Task 4).

    A real agent-driven evaluator is NONDETERMINISTIC, so a single eval is
    noisy.  Averaging ``repeats`` runs shrinks the per-case variance → shrinks
    the paired delta's standard error → tightens the verdict CI, which can
    resolve an ``inconclusive`` single-shot result into ``improved``/
    ``regressed`` WITHOUT changing the case identity (same length/cases as the
    inner).  For a deterministic inner (e.g. ``StaticCheckSet``) the average
    equals the single result, so a default of ``repeats=1`` is exactly the
    inner's single run.

    Every inner run must return equal-length ``before``/``after`` tuples, and
    all runs must agree on that length (mirrors ``run_eval_gate``'s
    length-mismatch guard) — otherwise the averaged samples would be
    incomparable, so this raises ``ValueError`` instead of silently averaging
    over a min-length slice.
    """

    def __init__(self, *, inner: CheckSet, repeats: int) -> None:
        if repeats < 1:
            raise ValueError(
                f"RepeatedCheckSet requires repeats >= 1 (got {repeats})."
            )
        self.inner = inner
        self.repeats = repeats

    def run(
        self, candidate: LearningCandidate
    ) -> tuple[tuple[float, ...], tuple[float, ...]]:
        runs = [self.inner.run(candidate) for _ in range(self.repeats)]
        return _average_runs(runs)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class EvalGateConfig(BaseModel):
    """Tunable decision thresholds for the eval gate.

    Defaults mirror the module-level named constants so callers can run the gate
    with no config.  Promotion to live / production eval is out of scope (PR7).
    """

    model_config = ConfigDict(
        frozen=True,
        populate_by_name=True,
        extra="forbid",
        validate_default=True,
    )

    #: Minimum paired sample count before a verdict is trusted.  CAVEAT: the
    #: paired-significance CI is a normal-approximation (``z``) interval valid
    #: only asymptotically; at the default ``4`` (df=3) it UNDER-COVERS (a true
    #: 95% t-interval multiplier is ~3.18 vs ``z=1.96``).  Raise this (or ``z``)
    #: for a genuine 95% no-regression posture at small n.  A proper t-interval
    #: is a follow-up.
    min_sample_size: int = Field(default=MIN_EVAL_SAMPLE_SIZE, alias="minSampleSize")
    max_regression_band: float = Field(
        default=MAX_REGRESSION_BAND, alias="maxRegressionBand"
    )

    #: Which decision path the gate uses.  ``"strict_band"`` (default) preserves
    #: today's exact behavior (mean-regression vs ``max_regression_band``).
    #: ``"paired_significance"`` selects the paired-CI path — DORMANT in Task 1;
    #: it is not consulted by ``run_eval_gate`` until Task 2 wires it in.
    decision_rule: Literal["strict_band", "paired_significance"] = Field(
        default="strict_band", alias="decisionRule"
    )
    #: z multiplier for the paired-significance confidence interval (Task 2).
    #: This is a NORMAL-APPROXIMATION multiplier, valid asymptotically.  At small
    #: ``min_sample_size`` it under-covers — e.g. at n≈4 a true 95% t-interval
    #: needs ~3.18, so the default ``1.96`` gives a CI that is too NARROW
    #: (anti-conservative).  Raise ``z`` (``>=3`` for n≈4) or ``min_sample_size``
    #: for a real 95% no-regression posture.  A proper t-interval is a follow-up.
    z: float = 1.96
    #: Number of paired eval repeats per candidate (Task 4).  Default ``1``
    #: reproduces the single-shot strict-band measurement.  Escalation (when a
    #: verdict is ``inconclusive``) accumulates ADDITIONAL inner runs from
    #: ``n_repeats`` up to ``max_repeats``, so ``n_repeats >= max_repeats``
    #: simply disables escalation (fixed repeats, exactly ``n_repeats`` runs).
    n_repeats: int = Field(default=1, alias="nRepeats")
    #: Upper bound on adaptive repeats when escalating an inconclusive result
    #: (Task 4).  Default ``1`` disables escalation.  Escalation runs from
    #: ``n_repeats`` up to ``max_repeats``; when ``n_repeats >= max_repeats``
    #: there is no headroom, so the gate performs exactly ``n_repeats`` inner
    #: runs and never escalates (a valid "fixed repeats" config, not an error).
    max_repeats: int = Field(default=1, alias="maxRepeats")


# ---------------------------------------------------------------------------
# Env-driven config factory (Task 5 — operator rule selection, NO default flip)
# ---------------------------------------------------------------------------

#: Env var → ``decision_rule``.  Unset or any value other than the two accepted
#: rules falls back to ``"strict_band"`` (the safe default — never raises).
GATE_RULE_ENV_VAR = "MAGI_LEARNING_GATE_RULE"
#: Env var → ``z`` (paired-CI multiplier).  Invalid/unset → field default 1.96.
GATE_Z_ENV_VAR = "MAGI_LEARNING_GATE_Z"
#: Env var → ``n_repeats``.  Invalid/unset → field default 1.
GATE_N_REPEATS_ENV_VAR = "MAGI_LEARNING_GATE_N_REPEATS"
#: Env var → ``max_repeats``.  Invalid/unset → field default 1.
GATE_MAX_REPEATS_ENV_VAR = "MAGI_LEARNING_GATE_MAX_REPEATS"

#: The decision rules an operator may select via env.  Anything else (typo,
#: empty string, legacy value) safely degrades to ``"strict_band"``.
_VALID_DECISION_RULES = frozenset({"strict_band", "paired_significance"})


def _parse_float(env: Mapping[str, str], key: str, default: float) -> float:
    """Parse ``env[key]`` as a float, defaulting on absence or malformed input.

    Defensive by design: a bad operator env value must NEVER crash the gate, so
    a missing key or an unparseable string both fall back to *default*.
    """
    raw = env.get(key)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _parse_int(env: Mapping[str, str], key: str, default: int) -> int:
    """Parse ``env[key]`` as an int, defaulting on absence or malformed input.

    Like :func:`_parse_float`, this never raises — a malformed value (including
    a float-looking string such as ``"1.5"``) falls back to *default* so a bad
    env can't crash the gate.
    """
    raw = env.get(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def eval_gate_config_from_env(
    env: Mapping[str, str] = os.environ,
) -> EvalGateConfig:
    """Build an :class:`EvalGateConfig` from operator env (Task 5).

    This lets a fleet operator SELECT the eval-gate decision rule and its
    operating point WITHOUT flipping any default.  With NO env vars set this
    returns a ``strict_band`` config byte-identical to ``EvalGateConfig()`` — the
    gate's behavior stays exactly as today.  The gate is a NO-REGRESSION safety
    mechanism whose default is, and stays, strict; paired significance is an
    opt-in for nondeterministic evaluators, not a relaxation of that posture.

    Env vars (all optional; parsed defensively — a malformed value falls back to
    the field default and never raises):

    - ``MAGI_LEARNING_GATE_RULE`` → ``decision_rule``.  Accepts exactly
      ``"strict_band"`` or ``"paired_significance"``.  Unset or ANY invalid value
      (typo, empty string, legacy token) → ``"strict_band"`` (safe default).
    - ``MAGI_LEARNING_GATE_Z`` → ``z`` (float; unset/malformed → 1.96).
    - ``MAGI_LEARNING_GATE_N_REPEATS`` → ``n_repeats`` (int; unset/malformed → 1).
    - ``MAGI_LEARNING_GATE_MAX_REPEATS`` → ``max_repeats`` (int; unset/malformed
      → 1).

    Operating-point guidance:

    - Enable ``paired_significance`` once the evaluator is NONDETERMINISTIC (real
      agent-driven scores carry run-to-run noise).  The strict-band rule treats
      any mean drop as a regression, so noise alone can block a genuinely-neutral
      candidate; the paired rule abstains (``inconclusive``) instead of failing
      on noise, and only promotes when the per-case improvement is significant.
    - Raising ``z`` (e.g. 1.96 → 2.5) WIDENS the inconclusive band → fewer
      promotions clear the bar → MORE conservative promotion.  Lowering it is
      more permissive; keep it ``>= 1.96`` for a real no-regression posture.
    - ``n_repeats`` / ``max_repeats`` shrink evaluator noise: averaging repeated
      runs tightens each candidate's delta CI, which can resolve an
      ``inconclusive`` single-shot result into a decisive verdict WITHOUT
      changing the cases.  ``n_repeats`` is the baseline repeat count;
      ``max_repeats`` is the adaptive ceiling the gate may escalate to on an
      inconclusive verdict.  ``n_repeats >= max_repeats`` disables escalation
      (fixed repeats).  Both default to ``1`` (single-shot — exactly today).

    An explicitly-constructed ``EvalGateConfig`` passed by a caller always wins;
    the live executor only falls back to this factory when no config is supplied
    (see ``harness/learning_executor.run_reflection``).  Direct/test callers of
    ``run_eval_gate`` that pass ``config=None`` still get the ``EvalGateConfig()``
    strict default — this factory does not change that path.
    """
    raw_rule = env.get(GATE_RULE_ENV_VAR)
    # Pre-validate against the known rules so an unknown/typo env value degrades
    # to the safe default instead of raising in EvalGateConfig's Literal check.
    # Pydantic narrows the plain str to the Literal field at construction.
    rule = raw_rule if raw_rule in _VALID_DECISION_RULES else "strict_band"
    # ``z`` guard: a non-positive z would invert/collapse the CI (``delta ± z*se``
    # with z<=0 flips the bounds, breaking the improved/regressed comparisons).
    # Treat a parsed ``z <= 0`` as invalid and fall back to the safe default.
    z = _parse_float(env, GATE_Z_ENV_VAR, 1.96)
    if z <= 0:
        z = 1.96
    return EvalGateConfig(
        decision_rule=rule,
        z=z,
        n_repeats=_parse_int(env, GATE_N_REPEATS_ENV_VAR, 1),
        max_repeats=_parse_int(env, GATE_MAX_REPEATS_ENV_VAR, 1),
    )


# ---------------------------------------------------------------------------
# Decision result
# ---------------------------------------------------------------------------


class EvalGateDecision(BaseModel):
    """Outcome of running one candidate through the eval gate."""

    model_config = ConfigDict(
        frozen=True,
        populate_by_name=True,
        extra="forbid",
        validate_default=True,
    )

    item_id: str = Field(alias="itemId")
    kind: str
    #: Whether the regression check passed (enough samples AND within band).
    passed: bool
    #: Whether the item was activated (status -> "active").  Only ``example``
    #: candidates that pass are activated; rules/evals never auto-activate.
    activated: bool
    #: The eval observation ref recorded for this candidate.  Present on every
    #: evaluated path; empty string when the candidate was *skipped* (it already
    #: exists in a non-proposed status, so no new observation is recorded).
    eval_observation_ref: str = Field(default="", alias="evalObservationRef")
    sample_n: int = Field(default=0, alias="sampleN")
    regression: float = 0.0
    #: Whether the candidate was skipped without evaluation because it already
    #: exists in the store as ``active`` / ``archived`` (re-running reflection
    #: over overlapping sessions).  Skipped candidates are never re-proposed or
    #: re-activated.
    skipped: bool = False
    #: Human-readable explanation when ``skipped`` is True (else empty).
    reason: str = ""
    #: Paired-significance verdict (Task 2).  Empty string on the strict-band
    #: path, which is the only path Task 1 exercises.
    verdict: str = ""
    #: Mean per-case delta (after - before) from the paired path (Task 2).
    delta: float = 0.0
    #: Standard error of the mean delta from the paired path (Task 2).
    se: float = 0.0
    #: Lower confidence bound on the mean delta (Task 2).
    ci_low: float = Field(default=0.0, alias="ciLow")
    #: Upper confidence bound on the mean delta (Task 2).
    ci_high: float = Field(default=0.0, alias="ciHigh")
    #: Number of paired eval repeats performed (Task 4).  ``1`` for the
    #: single-shot strict-band path.
    repeats: int = 1


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _mean(scores: tuple[float, ...]) -> float:
    return sum(scores) / len(scores) if scores else 0.0


def _summary_with_std(scores: tuple[float, ...]) -> dict[str, object]:
    """``{"mean", "n"}`` plus ``"std"`` when computable (>=2 samples).

    Used only on the paired-significance path so the persisted observation
    carries dispersion alongside the mean.  ``statistics.stdev`` needs at least
    two data points, so the ``"std"`` key is omitted for ``n<2`` rather than
    stored as ``0``/``None`` (an absent key reads as "not computable").
    """
    summary: dict[str, object] = {"mean": _mean(scores), "n": len(scores)}
    if len(scores) >= 2:
        summary["std"] = statistics.stdev(scores)
    return summary


def _candidate_item_id(candidate: LearningCandidate, *, tenant_id: str = "local") -> str:
    """Derive a stable, collision-free store id for *candidate*.

    The id is a content hash of ``tenant_id`` + the (opaque) source signal ref,
    so re-running reflection over the same trace produces the same id
    (idempotent propose) while two genuinely-different refs — even ones
    differing only by a trailing ``:v<n>`` — get distinct ids.  Mixing the
    tenant id into the digest makes the id tenant-unique: two different tenants
    proposing content with the same ``source_signal_ref`` derive DISTINCT ids,
    so cross-tenant clobber is impossible even before the store's tenant-scoped
    guards run (defense in depth).  A hex digest can never end in the store's
    reserved ``:v<digits>`` version suffix, so there is no fragile strip logic
    and no collision with ``store.edit()``'s version chain.
    """
    digest = hashlib.sha1(
        f"{tenant_id}\x00{candidate.source_signal_ref}".encode()
    ).hexdigest()[:16]
    return f"learning:{candidate.kind}:{digest}"


def _to_item(candidate: LearningCandidate, *, tenant_id: str = "local") -> LearningItem:
    """Promote a ``LearningCandidate`` to a proposed ``LearningItem``.

    The proposed item is stamped with *tenant_id* so the whole eval-gate flow
    (propose → get → record_eval_observation → auto_activate) stays inside one
    tenant.  Defaults to ``"local"`` so the OSS single-tenant path is unchanged.
    """
    return LearningItem(
        id=_candidate_item_id(candidate, tenant_id=tenant_id),
        tenantId=tenant_id,
        kind=candidate.kind,
        status="proposed",
        scope=candidate.scope,
        content=dict(candidate.content),
        rationale=candidate.rationale,
        provenance=candidate.provenance,
    )


# ---------------------------------------------------------------------------
# Eval gate entry point
# ---------------------------------------------------------------------------


def run_eval_gate(
    candidates: tuple[LearningCandidate, ...],
    *,
    store: LearningStore,
    checkset: CheckSet,
    config: EvalGateConfig | None = None,
    tenant_id: str = "local",
    auto_activate_examples: bool = True,
) -> tuple[EvalGateDecision, ...]:
    """Run each candidate through propose → A/B eval → policy-gated activation.

    For every candidate:
      1. ``store.propose(...)`` (store forces ``status="proposed"``).
      2. ``checkset.run(...)`` → before/after scores → regression measurement.
      3. ``store.record_eval_observation(...)`` → ``eval_observation_ref`` (always
         recorded, pass or fail).
      4. Decision: pass requires ``sample_n >= min_sample_size`` AND
         ``regression <= max_regression_band``.
      5. On pass, ``example`` candidates are activated via
         ``store.auto_activate(...)`` (policy-gated).  ``rule``/``eval``
         candidates are left proposed.  On fail, nothing is activated.

    All activations go through ``policy.assert_activation_allowed`` (enforced by
    the store's ``auto_activate``).  No direct ``status="active"`` writes.

    Args:
        auto_activate_examples: When ``True`` (default — preserves all existing
            PR4 behaviour) a passing ``example`` is auto-activated.  When
            ``False`` a passing ``example`` is NOT auto-activated; it stays
            ``proposed`` and awaits human approval (the bootstrap / default-ON
            reflect tier passes ``False`` so nothing activates without review).
            The eval observation is recorded either way, so a later human
            approval or real-eval pass has the measurement data.
    """
    if config is None:
        config = EvalGateConfig()

    decisions: list[EvalGateDecision] = []
    for candidate in candidates:
        proposed_item = _to_item(candidate, tenant_id=tenant_id)

        # Idempotency guard: store.propose() raises if the item already exists
        # in a non-``proposed`` status.  On a re-run over overlapping sessions
        # an already-active/archived candidate must be SKIPPED gracefully —
        # never re-proposed, never re-activated, and never aborting the batch.
        # Tenant-scoped read so the guard only sees this tenant's items.
        existing = store.get(proposed_item.id, tenant_id=tenant_id)
        if existing is not None and existing.status != "proposed":
            decisions.append(
                EvalGateDecision(
                    itemId=proposed_item.id,
                    kind=proposed_item.kind,
                    passed=False,
                    activated=False,
                    skipped=True,
                    reason=(
                        f"item already exists with status={existing.status!r}; "
                        "skipped (not re-proposed / not re-activated)"
                    ),
                )
            )
            continue

        # Re-proposing a still-``proposed`` item is idempotent (ON CONFLICT
        # upsert in the store), so this is safe.
        #
        # TOCTOU guard: the ``store.get`` skip-check above and this ``propose``
        # are not atomic.  If a concurrent activation flips the item to active in
        # that window, ``propose`` raises ``ValueError`` (re-proposing a
        # non-proposed item is forbidden).  Treat that exactly like the
        # already-active skip case — mark the candidate skipped and continue so
        # the rest of the batch is never aborted by a single racing item.
        try:
            item = store.propose(proposed_item)
        except ValueError:
            decisions.append(
                EvalGateDecision(
                    itemId=proposed_item.id,
                    kind=proposed_item.kind,
                    passed=False,
                    activated=False,
                    skipped=True,
                    reason=(
                        "item flipped to a non-proposed status concurrently "
                        "between the skip-check and propose; skipped (not "
                        "re-proposed / not re-activated)"
                    ),
                )
            )
            continue

        # Decision: ``strict_band`` (default) reproduces today's mean-regression
        # vs band test BYTE-IDENTICALLY; ``paired_significance`` uses the paired
        # one-sided verdict (harm-gated, abstaining).  ``pv`` is None on the
        # strict-band path so the new EvalGateDecision fields stay at defaults.
        pv: PairedVerdict | None = None
        # Final repeat count actually performed (Task 4).  Recorded on the
        # decision + observation stats; ``1`` on the single-shot strict-band path.
        repeats = 1
        if config.decision_rule == "paired_significance":
            # Adaptive repeated evaluation (Task 4).  Start by accumulating
            # ``n_repeats`` inner runs and, while the verdict is ``inconclusive``
            # and there is headroom below ``max_repeats``, run the inner ONE more
            # time and re-average over ALL accumulated runs to shrink the
            # per-case noise → shrink the delta SE → tighten the CI, which can
            # resolve ``inconclusive`` into ``improved``/``regressed``.
            # Accumulation is INCREMENTAL: each escalation step is exactly one
            # ADDITIONAL inner call (NOT a fresh re-run from scratch), so total
            # inner calls == final ``repeats`` (LINEAR, not quadratic).
            # ``underpowered`` is NOT escalated (more repeats add SAMPLES per
            # case, not CASES, so it cannot cross the ``min_n`` floor);
            # ``improved``/``regressed`` are already decisive.  The
            # ``len(runs) < max_repeats`` bound guarantees termination.  With
            # ``n_repeats=max_repeats=1`` this is exactly one inner run (no loop)
            # — identical to the pre-Task-4 single-shot path.
            #
            # Robustness: clamp the seed count and escalation cap to >= 1.  An
            # explicitly-constructed ``EvalGateConfig(n_repeats=0)`` (or negative)
            # would otherwise produce an EMPTY ``runs`` list → ``_average_runs([])``
            # raises.  A non-positive repeat count is meaningless, so run exactly
            # one repeat rather than crashing the gate.
            repeats0 = max(1, config.n_repeats)
            max_repeats = max(1, config.max_repeats)
            runs: list[tuple[tuple[float, ...], tuple[float, ...]]] = [
                checkset.run(candidate) for _ in range(repeats0)
            ]
            before, after = _average_runs(runs)
            # Length guard (mirrors the strict-band path): a mismatched evaluator
            # that returns different-length before/after tuples would compare
            # different sample populations.  ``_average_runs`` already guards each
            # inner run, but keep the item-scoped message for the averaged result.
            if len(before) != len(after):
                raise ValueError(
                    "eval checkset returned mismatched before/after lengths "
                    f"({len(before)} != {len(after)}) for item {item.id!r}; "
                    "the evaluator is producing incomparable samples."
                )
            pv = paired_verdict(
                before, after, z=config.z, min_n=config.min_sample_size
            )
            # NOTE(optional-stopping): this loop re-decides after each ADDED
            # repeat and stops on the first decisive verdict.  That is sequential
            # testing — it inflates Type-I (false-positive) error under a
            # NONDETERMINISTIC evaluator.  With the current deterministic averaged
            # checkset the averaged samples are identical across repeats, so there
            # is no sequential-testing risk.  Once the live (PR7) nondeterministic
            # evaluator lands, escalation must use a FIXED repeat budget or an
            # alpha-spending schedule to avoid these sequential false positives.
            while pv.verdict == "inconclusive" and len(runs) < max_repeats:
                runs.append(checkset.run(candidate))
                before, after = _average_runs(runs)
                pv = paired_verdict(
                    before, after, z=config.z, min_n=config.min_sample_size
                )
            # Final repeat count == number of accumulated inner runs == total
            # inner calls (linear).
            repeats = len(runs)
            sample_n = pv.n
            # Back-compat sign: regression = mean(before) - mean(after) = -delta.
            regression = -pv.delta
            # Back-compat boolean: only an ``improved`` verdict passes.
            # ``inconclusive``/``underpowered`` defer (leave proposed); a
            # ``regressed`` verdict is itself the downstream rollback signal —
            # the gate records it but does NOT roll back here.
            passed = pv.verdict == "improved"
        else:
            before, after = checkset.run(candidate)
            # Guard: a mismatched evaluator that returns different-length
            # before/after tuples would make mean(before) - mean(after) compare
            # different sample populations.  Surface the broken evaluator early
            # rather than silently averaging over a min-length slice.
            if len(before) != len(after):
                raise ValueError(
                    "eval checkset returned mismatched before/after lengths "
                    f"({len(before)} != {len(after)}) for item {item.id!r}; "
                    "the evaluator is producing incomparable samples."
                )
            sample_n = len(before)
            regression = _mean(before) - _mean(after)
            passed = (
                sample_n >= config.min_sample_size
                and regression <= config.max_regression_band
            )

        # Persist the observation.  The strict_band path stays BYTE-IDENTICAL to
        # today (no std, stats=None).  The paired_significance path enriches the
        # record so a reviewer can see whether a promotion was significant or
        # noise: before/after gain a "std" (when >=2 samples) and the
        # significance stats (delta/se/ci/verdict/z/repeats) are persisted.
        if pv is not None:
            before_obs = _summary_with_std(before)
            after_obs = _summary_with_std(after)
            stats = {
                "delta": pv.delta,
                "se": pv.se,
                "ci_low": pv.ci_low,
                "ci_high": pv.ci_high,
                "verdict": pv.verdict,
                "z": config.z,
                # The decision's FINAL repeat count (Task 4): how many inner
                # runs were averaged before the verdict was decided.
                "repeats": repeats,
            }
        else:
            before_obs = {"mean": _mean(before), "n": len(before)}
            after_obs = {"mean": _mean(after), "n": len(after)}
            stats = None

        eval_ref = store.record_eval_observation(
            item_id=item.id,
            before=before_obs,
            after=after_obs,
            sample_n=sample_n,
            passed=passed,
            tenant_id=tenant_id,
            stats=stats,
        )

        activated = False
        if passed and item.kind == "example" and auto_activate_examples:
            # policy:eval-observation-required satisfied by eval_ref; no human
            # needed for examples.  assert_activation_allowed runs inside the
            # store's auto_activate (defense in depth) — call it here too so the
            # gate is unambiguously the policy-enforcement point.
            #
            # GOVERNANCE: when ``auto_activate_examples`` is False (bootstrap /
            # default-ON reflect tier) this branch is skipped so EVERY kind stays
            # ``proposed`` and awaits human approval.  The eval observation above
            # is still recorded, so the later human-approval path has the data.
            assert_activation_allowed(item, eval_observation_ref=eval_ref)
            store.auto_activate(
                item.id, eval_observation_ref=eval_ref, tenant_id=tenant_id
            )
            activated = True
        # rule  → leave proposed (no-direct-mutation: human approval in PR6).
        # eval  → register as holdout (proposed); not activated as behavior.
        # fail  → leave proposed with failing observation recorded.

        decisions.append(
            EvalGateDecision(
                itemId=item.id,
                kind=item.kind,
                passed=passed,
                activated=activated,
                evalObservationRef=eval_ref,
                sampleN=sample_n,
                regression=regression,
                # Paired-significance fields — populated only on that path; the
                # strict-band path leaves them at their empty/zero defaults.
                verdict=pv.verdict if pv is not None else "",
                delta=pv.delta if pv is not None else 0.0,
                se=pv.se if pv is not None else 0.0,
                ciLow=pv.ci_low if pv is not None else 0.0,
                ciHigh=pv.ci_high if pv is not None else 0.0,
                # Final adaptive repeat count (Task 4); ``1`` on strict_band.
                repeats=repeats,
            )
        )

    return tuple(decisions)


__all__ = [
    "LEARNING_EVAL_VERIFIER_ID",
    "MAX_REGRESSION_BAND",
    "MIN_EVAL_SAMPLE_SIZE",
    "CheckSet",
    "EvalGateConfig",
    "EvalGateDecision",
    "PairedVerdict",
    "RepeatedCheckSet",
    "StaticCheckSet",
    "Verdict",
    "eval_gate_config_from_env",
    "paired_verdict",
    "run_eval_gate",
]
