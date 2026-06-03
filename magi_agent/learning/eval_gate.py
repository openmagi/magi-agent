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
from typing import Protocol, runtime_checkable

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

    min_sample_size: int = Field(default=MIN_EVAL_SAMPLE_SIZE, alias="minSampleSize")
    max_regression_band: float = Field(
        default=MAX_REGRESSION_BAND, alias="maxRegressionBand"
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


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _mean(scores: tuple[float, ...]) -> float:
    return sum(scores) / len(scores) if scores else 0.0


def _candidate_item_id(candidate: LearningCandidate) -> str:
    """Derive a stable, collision-free store id for *candidate*.

    The id is a content hash of the (opaque) source signal ref, so re-running
    reflection over the same trace produces the same id (idempotent propose)
    while two genuinely-different refs — even ones differing only by a trailing
    ``:v<n>`` — get distinct ids.  A hex digest can never end in the store's
    reserved ``:v<digits>`` version suffix, so there is no fragile strip logic
    and no collision with ``store.edit()``'s version chain.
    """
    digest = hashlib.sha1(candidate.source_signal_ref.encode()).hexdigest()[:16]
    return f"learning:{candidate.kind}:{digest}"


def _to_item(candidate: LearningCandidate, *, tenant_id: str = "local") -> LearningItem:
    """Promote a ``LearningCandidate`` to a proposed ``LearningItem``.

    The proposed item is stamped with *tenant_id* so the whole eval-gate flow
    (propose → get → record_eval_observation → auto_activate) stays inside one
    tenant.  Defaults to ``"local"`` so the OSS single-tenant path is unchanged.
    """
    return LearningItem(
        id=_candidate_item_id(candidate),
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
        item = store.propose(proposed_item)

        before, after = checkset.run(candidate)
        # Guard: a mismatched evaluator that returns different-length before/after
        # tuples would make mean(before) - mean(after) compare different sample
        # populations.  Surface the broken evaluator early rather than silently
        # averaging over a min-length slice.
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

        eval_ref = store.record_eval_observation(
            item_id=item.id,
            before={"mean": _mean(before), "n": len(before)},
            after={"mean": _mean(after), "n": len(after)},
            sample_n=sample_n,
            passed=passed,
            tenant_id=tenant_id,
        )

        activated = False
        if passed and item.kind == "example":
            # policy:eval-observation-required satisfied by eval_ref; no human
            # needed for examples.  assert_activation_allowed runs inside the
            # store's auto_activate (defense in depth) — call it here too so the
            # gate is unambiguously the policy-enforcement point.
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
    "StaticCheckSet",
    "run_eval_gate",
]
