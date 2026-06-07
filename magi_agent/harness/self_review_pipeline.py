"""C2 — LearningPipelineSink: routes self-review candidates through the learning eval-gate.

This module provides the real ``CandidateSink`` implementation for C1's
``run_self_review_hook``.  It maps a ``ReviewCandidate`` (from the self-review
fork) into a ``LearningCandidate`` and routes it through the existing learning
eval-gate pipeline defined in ``learning/eval_gate.py``.

Activation routing (per ``learning/policy.py``):
  - On eval-gate PASS:
      * ``example``-class candidate → ``store.auto_activate`` (policy
        ``eval-observation-required`` satisfied by the eval ref; no human needed).
      * ``rule``-class candidate → stays ``proposed`` (requires a human
        ``approval_ref`` per ``policy:no-direct-mutation``).
  - On eval-gate FAIL (insufficient samples OR regression too large):
      * Candidate stays ``proposed``; eval observation is recorded for human
        inspection.
  - ``eval``-class candidate stays ``proposed`` (registered as holdout; not
    activated as behavior).

Env gates
---------
MAGI_SELF_REVIEW_PIPELINE_ENABLED   default OFF.  When off the sink records the
                                     routing decision (gate_off=True) but does NOT
                                     run the eval-gate or write anything.
MAGI_SELF_REVIEW_SHADOW             (shared with C1) default ON.  When on, even if
                                     the pipeline is enabled the sink skips the
                                     eval-gate and store writes (shadow-first rollout
                                     — C1 candidates are observed but not acted upon).

Eval-gate honesty
-----------------
The real ``run_eval_gate`` from ``learning/eval_gate.py`` is called with the real
``EvalGateConfig`` defaults (``MIN_EVAL_SAMPLE_SIZE=4``,
``MAX_REGRESSION_BAND=0.0``).  These thresholds are NOT weakened here.

Evidence + redaction
--------------------
An ``EvidenceRecord`` is emitted for each routing decision.  Fields contain only
digests, kind strings, verdict booleans, and length counts — NO raw proposal text
or session content.

Authority flags
---------------
All ``Literal[False]`` authority flags remain unset.  This module NEVER calls the
eval-gate or writes to the store except through the existing
``run_eval_gate``/``store.auto_activate`` pipeline.  ``rule`` items are NEVER
auto-activated regardless of eval outcome.

Forbidden top-level imports: magi_agent.adk_bridge, google.adk, urllib, socket,
subprocess — none appear in this module's top-level import graph.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from magi_agent.evidence.types import EvidenceRecord, EvidenceSource
from magi_agent.harness.self_review import CandidateSink, ReviewCandidate
from magi_agent.learning.candidates import LearningCandidate
from magi_agent.learning.eval_gate import (
    CheckSet,
    EvalGateConfig,
    EvalGateDecision,
    StaticCheckSet,
    run_eval_gate,
)
from magi_agent.learning.models import LearningKind, LearningScope, Provenance
from magi_agent.learning.store import LearningStore, SqliteLearningStore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Env gates
# ---------------------------------------------------------------------------

_ENV_PIPELINE_ENABLED = "MAGI_SELF_REVIEW_PIPELINE_ENABLED"
_ENV_SHADOW = "MAGI_SELF_REVIEW_SHADOW"

_TRUE_STRINGS = frozenset({"1", "true", "yes", "on"})


def _env_flag(name: str, *, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in _TRUE_STRINGS


def _pipeline_enabled() -> bool:
    return _env_flag(_ENV_PIPELINE_ENABLED, default=False)


def _shadow_mode() -> bool:
    # Shadow-first: default ON unless explicitly disabled
    raw = os.environ.get(_ENV_SHADOW)
    if raw is None:
        return True
    return raw.strip().lower() not in {"0", "false", "no", "off"}


# ---------------------------------------------------------------------------
# PipelineSinkConfig
# ---------------------------------------------------------------------------


class PipelineSinkConfig(BaseModel):
    """Frozen config controlling pipeline sink behavior."""

    model_config = ConfigDict(
        frozen=True,
        populate_by_name=True,
        extra="forbid",
        validate_default=True,
    )

    pipeline_enabled: bool = False
    shadow: bool = True

    @classmethod
    def from_env(cls) -> PipelineSinkConfig:
        return cls(
            pipeline_enabled=_pipeline_enabled(),
            shadow=_shadow_mode(),
        )


# ---------------------------------------------------------------------------
# RoutingDecision — result returned from LearningPipelineSink.receive()
# ---------------------------------------------------------------------------


class RoutingDecision(BaseModel):
    """Frozen result from a single candidate routing attempt."""

    model_config = ConfigDict(
        frozen=True,
        populate_by_name=True,
        extra="forbid",
        validate_default=True,
    )

    #: The learning store item id (None when gate is off or shadow mode).
    item_id: str | None = Field(default=None, alias="itemId")
    #: Whether the pipeline gate was off (MAGI_SELF_REVIEW_PIPELINE_ENABLED=0).
    gate_off: bool = Field(default=False, alias="gateOff")
    #: Whether shadow mode suppressed eval/store writes.
    shadow: bool = False
    #: Whether this candidate was skipped (idempotency guard: already exists).
    skipped: bool = False
    #: Whether the candidate was activated (status → active).
    activated: bool = False
    #: Resulting status in the store ("proposed"/"active" or None when gate-off).
    resulting_status: str | None = Field(default=None, alias="resultingStatus")
    #: The eval gate decision (None when gate-off, shadow, or exception).
    eval_verdict: EvalGateDecision | None = Field(default=None, alias="evalVerdict")
    #: Evidence record for audit (always present).
    evidence: EvidenceRecord


# ---------------------------------------------------------------------------
# Candidate kind mapping
# ---------------------------------------------------------------------------

# ReviewCandidate.kind ("memory" | "skill") maps to LearningKind:
#   "memory" → "example"  (a recalled fact = a learned example behavior)
#   "skill"  → "example"  (an observed skill pattern = a learned example behavior)
# Neither is directly emitted as a "rule" by C1 — rule promotion is C3's concern
# (cross-session aggregation via labeler.aggregate_candidates).
_REVIEW_KIND_TO_LEARNING_KIND: dict[str, LearningKind] = {
    "memory": "example",
    "skill": "example",
}


def _map_candidate(rc: ReviewCandidate) -> LearningCandidate:
    """Map a ``ReviewCandidate`` to a ``LearningCandidate``.

    Field mapping:
    - ``rc.kind`` ("memory"/"skill") → ``lc.kind`` ("example")
    - ``rc.proposal`` → ``lc.content`` {"situation": proposal, "behavior": proposal}
    - ``rc.proposal`` → ``lc.rationale``
    - ``rc.provenance_digest`` → included in ``lc.source_signal_ref``
    - ``rc.session_id`` → ``lc.provenance.session_ids``
    - ``rc.confidence`` is noted in ``source_signal_ref`` for traceability
    - ``lc.scope.task_kind`` = "self-review" (well-known tag for retrieval routing)
    - ``lc.provenance.derived_by`` = "reflection"
    """
    kind = _REVIEW_KIND_TO_LEARNING_KIND.get(rc.kind, "example")
    proposal = rc.proposal
    # Use current UTC timestamp for provenance.created_at
    created_at = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    return LearningCandidate(
        kind=kind,
        scope=LearningScope(
            taskKind="self-review",
            tags=(rc.kind,),
        ),
        content={
            "situation": proposal,
            "behavior": proposal,
        },
        rationale=proposal,
        provenance=Provenance(
            sessionIds=(rc.session_id,),
            derivedBy="reflection",
            createdAt=created_at,
        ),
        # Source signal ref: includes the provenance digest for traceability;
        # digest is a SHA-256 hex — safe to record.
        sourceSignalRef=f"self-review:{rc.kind}:{rc.provenance_digest[:16]}@{rc.session_id}",
    )


# ---------------------------------------------------------------------------
# Evidence builder
# ---------------------------------------------------------------------------


def _build_routing_evidence(
    *,
    candidate: ReviewCandidate,
    gate_off: bool,
    shadow: bool,
    skipped: bool,
    activated: bool,
    resulting_status: str | None,
    eval_passed: bool | None,
    item_id: str | None,
    elapsed_ms: float,
    status: Literal["ok", "failed"],
    now: datetime,
) -> EvidenceRecord:
    """Build a redacted EvidenceRecord for a routing decision.

    All fields contain digests, kind strings, booleans, and lengths.
    NO raw proposal text or session content is included.
    """
    fields: dict[str, Any] = {
        # Candidate kind ("memory" / "skill") — not raw text
        "candidateKind": candidate.kind,
        # Provenance digest (first 16 chars) — safe digest, not raw content
        "provenanceDigest": candidate.provenance_digest[:16],
        # Confidence score — numeric, not raw text
        "confidence": round(candidate.confidence, 4),
        # Pipeline routing decision fields
        "gateOff": gate_off,
        "shadow": shadow,
        "skipped": skipped,
        "activated": activated,
        "resultingStatus": resulting_status,
        "evalPassed": eval_passed,
        # Item id (opaque hash) — safe to record
        "itemId": item_id,
        "elapsedMs": round(elapsed_ms, 2),
        # Proposal length (not content) for size audit
        "proposalLen": len(candidate.proposal),
    }

    return EvidenceRecord(
        type="custom:SelfReviewPipelineRouting",
        status=status,
        observedAt=int(now.astimezone(UTC).timestamp() * 1000),
        source=EvidenceSource(kind="execution_contract"),
        fields=fields,
    )


# ---------------------------------------------------------------------------
# LearningPipelineSink
# ---------------------------------------------------------------------------


class LearningPipelineSink:
    """Real ``CandidateSink`` that routes ``ReviewCandidate`` objects through
    the learning eval-gate and applies the activation policy.

    Implements ``CandidateSink`` (synchronous ``receive`` method).

    Parameters
    ----------
    store:
        An injectable ``LearningStore`` (``SqliteLearningStore`` in production,
        in-memory SQLite for tests).
    checkset:
        Injectable ``CheckSet`` for eval-gate scoring.  Defaults to a
        ``StaticCheckSet`` that immediately fails (no samples), which ensures
        nothing auto-activates unless the caller explicitly injects a passing
        checkset.  Production will inject the real agent-driven evaluator (PR7).
    config:
        ``PipelineSinkConfig`` controlling gate/shadow behavior.  Defaults to
        ``PipelineSinkConfig.from_env()`` (OFF by default).
    eval_gate_config:
        ``EvalGateConfig`` thresholds.  Defaults to ``EvalGateConfig()`` which
        uses the module-level ``MIN_EVAL_SAMPLE_SIZE`` and ``MAX_REGRESSION_BAND``
        constants — NOT weakened here.
    tenant_id:
        Learning store tenant id.  Defaults to ``"local"`` (single-tenant OSS path).
    """

    def __init__(
        self,
        store: LearningStore,
        *,
        checkset: CheckSet | None = None,
        config: PipelineSinkConfig | None = None,
        eval_gate_config: EvalGateConfig | None = None,
        tenant_id: str = "local",
    ) -> None:
        self._store = store
        # Default checkset: produces 0 samples → always fails eval-gate
        # (nothing auto-activates without an explicit injected checkset).
        self._checkset: CheckSet = (
            checkset
            if checkset is not None
            else StaticCheckSet(before=(), after=())
        )
        self._config = config if config is not None else PipelineSinkConfig.from_env()
        self._eval_gate_config = (
            eval_gate_config if eval_gate_config is not None else EvalGateConfig()
        )
        self._tenant_id = tenant_id

    # -- CandidateSink protocol -------------------------------------------

    def receive(self, candidate: ReviewCandidate) -> RoutingDecision:
        """Route a ``ReviewCandidate`` through the learning eval-gate.

        Always synchronous (no await) so it can be called from within the
        fork's asyncio task without additional dispatch complexity.

        Returns a ``RoutingDecision`` for observability.  Never raises.
        """
        start = time.monotonic()
        now = datetime.now(tz=UTC)

        # Gate-off fast path: record decision, skip everything.
        if not self._config.pipeline_enabled:
            elapsed = (time.monotonic() - start) * 1000
            evidence = _build_routing_evidence(
                candidate=candidate,
                gate_off=True,
                shadow=False,
                skipped=False,
                activated=False,
                resulting_status=None,
                eval_passed=None,
                item_id=None,
                elapsed_ms=elapsed,
                status="ok",
                now=now,
            )
            return RoutingDecision(
                gateOff=True,
                evidence=evidence,
            )

        # Shadow mode fast path: skip eval-gate and store writes.
        if self._config.shadow:
            elapsed = (time.monotonic() - start) * 1000
            evidence = _build_routing_evidence(
                candidate=candidate,
                gate_off=False,
                shadow=True,
                skipped=False,
                activated=False,
                resulting_status=None,
                eval_passed=None,
                item_id=None,
                elapsed_ms=elapsed,
                status="ok",
                now=now,
            )
            return RoutingDecision(
                shadow=True,
                evidence=evidence,
            )

        # Live path: map → eval-gate → activation policy.
        try:
            return self._route_live(candidate, start=start, now=now)
        except Exception:
            logger.exception(
                "self_review_pipeline: routing failed for candidate "
                "(kind=%s provenance=%.16s) — fail-open",
                candidate.kind,
                candidate.provenance_digest,
            )
            elapsed = (time.monotonic() - start) * 1000
            evidence = _build_routing_evidence(
                candidate=candidate,
                gate_off=False,
                shadow=False,
                skipped=False,
                activated=False,
                resulting_status=None,
                eval_passed=None,
                item_id=None,
                elapsed_ms=elapsed,
                status="failed",
                now=now,
            )
            return RoutingDecision(
                activated=False,
                evidence=evidence,
            )

    def _map(self, rc: ReviewCandidate) -> LearningCandidate:
        """Override point for tests that need to force a different LearningKind."""
        return _map_candidate(rc)

    def _route_live(
        self,
        candidate: ReviewCandidate,
        *,
        start: float,
        now: datetime,
    ) -> RoutingDecision:
        """Execute the full eval-gate pipeline for a live candidate.

        Steps:
        1. Map ReviewCandidate → LearningCandidate.
        2. Run ``run_eval_gate`` (propose + checkset + record_eval_observation
           + policy-gated activation for example-class items).
        3. Build RoutingDecision from the EvalGateDecision.

        All activations flow through ``run_eval_gate`` which calls
        ``store.auto_activate`` which calls ``policy.assert_activation_allowed``.
        Rule items are NEVER activated here — the gate enforces
        ``policy:no-direct-mutation`` by leaving them ``proposed``.
        """
        lc = self._map(candidate)

        decisions = run_eval_gate(
            (lc,),
            store=self._store,
            checkset=self._checkset,
            config=self._eval_gate_config,
            tenant_id=self._tenant_id,
            auto_activate_examples=True,
        )

        decision = decisions[0]
        item_id: str = decision.item_id
        activated: bool = decision.activated
        skipped: bool = decision.skipped

        # Determine resulting status
        if skipped:
            resulting_status: str | None = None
            # Look up the actual status from the store for the reporting
            existing = self._store.get(item_id, tenant_id=self._tenant_id)
            if existing is not None:
                resulting_status = existing.status
        elif activated:
            resulting_status = "active"
        else:
            resulting_status = "proposed"

        elapsed = (time.monotonic() - start) * 1000
        evidence = _build_routing_evidence(
            candidate=candidate,
            gate_off=False,
            shadow=False,
            skipped=skipped,
            activated=activated,
            resulting_status=resulting_status,
            eval_passed=decision.passed if not skipped else None,
            item_id=item_id,
            elapsed_ms=elapsed,
            status="ok",
            now=now,
        )

        return RoutingDecision(
            itemId=item_id,
            skipped=skipped,
            activated=activated,
            resultingStatus=resulting_status,
            evalVerdict=decision,
            evidence=evidence,
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "LearningPipelineSink",
    "PipelineSinkConfig",
    "RoutingDecision",
    "_map_candidate",
]
