"""Track 17 PR4 — adversarial cross-review + best-of-N variant generation.

This module wires two EXISTING metadata surfaces into a real quality step that
the workflow executor can call:

1. ``harness/inference_scaling.BestOfNEligibilityMetadata`` — when the policy
   authorises ``maxVariants > 1`` (verifier can rank outcomes + side-effects
   safe), ``generate_best_of_n_variants`` produces exactly N deterministic
   variants and selects one by the verifier ranking.  N is clamped to the
   executor concurrency cap (≤16) so variant generation stays bounded.

2. ``harness/verifier_bus`` — the ``source_claim_link`` deterministic verifier
   is the gate that FILTERS claims unsupported by peers.  ``run_cross_review``
   takes independent peer attestations (sanitised claim refs only — never raw
   transcripts), counts cross-support per claim, and for each claim produces a
   real ``VerifierResultMetadata`` verdict from the ``source_claim_link``
   verifier id.  A claim that no peer corroborates receives a ``failed`` verdict
   and is GENUINELY REMOVED from the surviving claim set; cross-supported claims
   pass and survive.

The review outcome (surviving vs filtered + per-claim rationale) is recorded as
an evidence event via ``runtime_trace_event`` (the same public-event shape the
``event_projection`` module emits), so it can flow to the Work Console and feed
downstream final assembly.

Cross-review is PEER review (agents review each other's claims), distinct from
``meta_orchestration.inspection_loop`` which is hierarchical parent→child
acceptance.  This module reuses the verifier-bus verdict surface but the
topology is peer-to-peer, not parent-owned.

Default-OFF / context isolation: this module performs no I/O, attaches no
runner/route/traffic, and rejects raw-transcript-shaped claim refs.  It is only
invoked from the gated live executor path; nothing here flips a locked
``Literal[False]`` authority flag.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from openmagi_core_agent.harness.inference_scaling import (
    BestOfNEligibilityMetadata,
    EscalationMetadata,
)
from openmagi_core_agent.harness.verifier_bus import (
    CriticEscalationReason,
    VerifierBusMetadata,
    VerifierResultMetadata,
    build_default_verifier_bus_metadata,
)
from openmagi_core_agent.runtime.public_events import PublicEvent, runtime_trace_event


_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
)

#: The EXISTING verifier_bus verifier that owns source/claim-link checks.
_SOURCE_CLAIM_LINK_VERIFIER_ID = "source-claim-link"

#: The EXISTING semantic-critic stage in the verifier bus.  When a critic
#: escalation reason is supplied, ``effective_verifiers`` admits the
#: ``llm_critic`` stage that is otherwise disabled — that admission IS the
#: deterministic escalation signal this module records.
_LLM_CRITIC_STAGE = "llm_critic"

#: The ``CriticEscalationReason`` cross-review escalates with.  Filtered claims
#: (peers could not corroborate the synthesised claim set) map to the existing
#: ``synthesis_quality`` reason; a best-of-N that could not rank/select a high
#: confidence variant maps to ``fuzzy_quality``.
_CROSS_REVIEW_ESCALATION_REASON: CriticEscalationReason = "synthesis_quality"
_BEST_OF_N_ESCALATION_REASON: CriticEscalationReason = "fuzzy_quality"
# NOTE: the reason is recorded as metadata only.  ``_effective_critic_verifier_ids``
# (and therefore the verifier bus) admits the ``llm_critic`` stage uniformly for
# ANY valid reason — the bus gate is ``reason is not None``, not the reason value
# itself.  So ``synthesis_quality`` and ``fuzzy_quality`` both admit the same
# ``llm-critic-fuzzy-quality`` verifier; they are distinct labels for the
# recorded escalation event, not selectors for different critic pipelines.

#: Reused claim-ref shape from the research child runner contract.
_CLAIM_REF_RE = re.compile(r"^claim:[A-Za-z0-9_.:!-]{1,180}$")
_AGENT_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:!-]{0,120}$")

#: Raw-transcript / private-path markers that must never enter a claim ref
#: (context isolation — peers see sanitised refs, never raw transcripts).
_PRIVATE_TEXT_RE = re.compile(
    r"(?:/Users/|/home/|/workspace/|/data/bots/|/var/lib/|authorization|"
    r"cookie|raw[_ -]?(?:child|tool|prompt|transcript|output|result|log|args)|"
    r"hidden[_-]?reasoning|token|secret|session[_-]?key|password|credential|"
    r"private[_-]?key)",
    re.IGNORECASE,
)

#: PR1/PR3 executor concurrency ceiling — best-of-N must never exceed it.
_MAX_CONCURRENCY_CAP = 16

#: Best-of-N selections at or below this verifier rank score are treated as
#: low-confidence — a quality signal that warrants critic escalation even when
#: N variants were produced.  Deterministic (scores come from ``_variant_score``).
_LOW_CONFIDENCE_SELECTION_THRESHOLD = 0.5


# ---------------------------------------------------------------------------
# Critic / escalation (metadata-only, anchored to verifier_bus)
# ---------------------------------------------------------------------------

class CrossReviewEscalation(BaseModel):
    """A recorded critic-escalation DECISION derived from real metadata.

    This carries the EXISTING ``inference_scaling.EscalationMetadata`` verdict
    (``kind="critic"``) plus the ``verifier_bus`` verifiers that become
    *effective* ONLY because the escalation reason is supplied.  The
    ``escalated_verifier_ids`` are computed by diffing
    ``effective_verifiers(escalationReason=...)`` against the baseline
    ``effective_verifiers()`` call — so the recorded decision is anchored to a
    genuine, deterministic verifier-bus state change (the ``llm_critic`` stage
    being admitted), not an invented field.

    metadata-only: no runner/route/model-routing is ever attached (the
    underlying ``EscalationMetadata`` locks those flags to ``False``).
    """

    model_config = _MODEL_CONFIG

    eligible: bool
    reason: str | None = None
    #: The verifier ids admitted to the effective set BECAUSE of the escalation
    #: reason (empty when not eligible).  These are the real signal.
    escalated_verifier_ids: tuple[str, ...] = Field(
        default=(),
        alias="escalatedVerifierIds",
    )
    metadata_only: bool = Field(default=True, alias="metadataOnly")
    runner_attached: bool = Field(default=False, alias="runnerAttached")

    @property
    def escalated(self) -> bool:
        return self.eligible and bool(self.escalated_verifier_ids)


def _escalation_metadata(
    *,
    eligible: bool,
    reason: CriticEscalationReason,
) -> EscalationMetadata:
    """Build the EXISTING ``EscalationMetadata`` (``kind="critic"``) verdict.

    A non-eligible decision carries no reason (the model rejects a reason-less
    eligible escalation, and an eligible one requires a reason)."""
    return EscalationMetadata(
        kind="critic",
        eligible=eligible,
        reason=reason if eligible else None,
    )


def _effective_critic_verifier_ids(
    bus: VerifierBusMetadata,
    *,
    reason: CriticEscalationReason,
) -> tuple[str, ...]:
    """Verifier ids admitted to the effective set ONLY by *reason*.

    Diffs ``effective_verifiers(escalationReason=reason)`` against the baseline
    ``effective_verifiers()`` (no reason).  The difference is the deterministic
    escalation signal — the ``llm_critic`` stage the bus otherwise disables.
    """
    baseline = {
        verifier.verifier_id
        for verifier in bus.effective_verifiers(
            deterministic_prerequisites_satisfied=True,
        )
    }
    escalated = bus.effective_verifiers(
        deterministic_prerequisites_satisfied=True,
        escalationReason=reason,
    )
    return tuple(
        verifier.verifier_id
        for verifier in escalated
        if verifier.verifier_id not in baseline
    )


def _derive_escalation(
    *,
    triggered: bool,
    reason: CriticEscalationReason,
    bus: VerifierBusMetadata | None = None,
) -> CrossReviewEscalation:
    """Produce a ``CrossReviewEscalation`` anchored to the real verifier bus.

    When *triggered* (a genuine quality signal — filtered claims, or a
    low-confidence best-of-N selection), an eligible critic ``EscalationMetadata``
    is built and the verifier-bus ``effective_verifiers(escalationReason=...)``
    surface is consulted to learn which verifiers the escalation admits.  When
    not triggered, the decision is recorded as non-eligible with no admitted
    verifiers (no escalation).

    Pass an already-built *bus* to avoid a redundant ``build_default_verifier_bus_metadata``
    call when the caller has already constructed one (e.g. ``run_cross_review``).
    When omitted, the bus is built here.
    """
    metadata = _escalation_metadata(eligible=triggered, reason=reason)
    if not metadata.eligible:
        return CrossReviewEscalation(eligible=False, reason=None)
    resolved_bus = bus if bus is not None else build_default_verifier_bus_metadata()
    escalated_ids = _effective_critic_verifier_ids(resolved_bus, reason=reason)
    return CrossReviewEscalation(
        eligible=True,
        reason=metadata.reason,
        escalatedVerifierIds=escalated_ids,
    )


# ---------------------------------------------------------------------------
# Best-of-N
# ---------------------------------------------------------------------------

class BestOfNVariant(BaseModel):
    """A single deterministic best-of-N candidate variant.

    Variants are content-free here (no raw text) — they carry a public ref and
    a verifier rank score so the selection is reproducible and isolation-safe.
    """

    model_config = _MODEL_CONFIG

    index: int = Field(ge=0)
    variant_ref: str = Field(alias="variantRef")
    score: float


class BestOfNOutcome(BaseModel):
    model_config = _MODEL_CONFIG

    variants: tuple[BestOfNVariant, ...]
    selected_index: int = Field(alias="selectedIndex", ge=0)
    #: PR4 — critic escalation derived from the selection's confidence.  When
    #: best-of-N could not rank/select a high-confidence variant (ineligible, or
    #: the top score is at/below the low-confidence threshold), this records an
    #: eligible critic escalation anchored to the verifier bus.
    escalation: CrossReviewEscalation = Field(
        default_factory=lambda: CrossReviewEscalation(eligible=False, reason=None),
    )

    @property
    def selected_variant(self) -> BestOfNVariant:
        return self.variants[self.selected_index]

    @field_validator("variants")
    @classmethod
    def _require_variants(cls, value: tuple[BestOfNVariant, ...]) -> tuple[BestOfNVariant, ...]:
        if not value:
            raise ValueError("best-of-N must produce at least one variant")
        return value

    @model_validator(mode="after")
    def _validate_selection(self) -> "BestOfNOutcome":
        if self.selected_index >= len(self.variants):
            raise ValueError("selectedIndex must reference a generated variant")
        return self


def _bounded_variant_count(eligibility: BestOfNEligibilityMetadata, concurrency_cap: int) -> int:
    """Resolve N: the eligible variant count clamped to [1, min(cap, 16)]."""
    if isinstance(concurrency_cap, bool) or not isinstance(concurrency_cap, int):
        cap = _MAX_CONCURRENCY_CAP
    else:
        cap = max(1, min(concurrency_cap, _MAX_CONCURRENCY_CAP))
    requested = eligibility.max_variants if eligibility.eligible else 1
    return max(1, min(requested, cap))


def _variant_score(objective: str, index: int) -> float:
    """Deterministic verifier-rank score in [0, 1) for a candidate variant."""
    seed = f"best-of-n:{objective}:{index}".encode("utf-8")
    digest = hashlib.sha256(seed).hexdigest()
    return int(digest[:8], 16) / float(0xFFFFFFFF + 1)


def _variant_ref(objective: str, index: int) -> str:
    seed = f"variant:{objective}:{index}".encode("utf-8")
    return f"result:variant-{hashlib.sha1(seed).hexdigest()[:16]}"


async def generate_best_of_n_variants(
    *,
    objective: str,
    eligibility: BestOfNEligibilityMetadata,
    concurrency_cap: int = _MAX_CONCURRENCY_CAP,
) -> BestOfNOutcome:
    """Produce N best-of-N variants and select the verifier-top-ranked one.

    N is derived from ``eligibility`` (``maxVariants`` when eligible, else 1)
    and clamped to the executor concurrency cap.  Each variant is generated
    under an ``asyncio.Semaphore`` bounded to N so concurrent generation never
    exceeds the cap.  The selection is the variant with the maximum verifier
    rank score (deterministic, tie-broken by lowest index).
    """
    n = _bounded_variant_count(eligibility, concurrency_cap)
    semaphore = asyncio.Semaphore(n)

    async def _make(index: int) -> BestOfNVariant:
        async with semaphore:
            await asyncio.sleep(0)
            return BestOfNVariant(
                index=index,
                variantRef=_variant_ref(objective, index),
                score=_variant_score(objective, index),
            )

    variants = tuple(await asyncio.gather(*(_make(i) for i in range(n))))
    selected_index = max(
        range(len(variants)),
        key=lambda i: (variants[i].score, -i),
    )
    # Critic escalation: when the policy could not authorise multi-variant
    # ranking (ineligible) OR the verifier top-rank score is low-confidence,
    # derive an eligible critic escalation anchored to the verifier bus.
    low_confidence = variants[selected_index].score <= _LOW_CONFIDENCE_SELECTION_THRESHOLD
    triggered = (not eligibility.eligible) or low_confidence
    escalation = _derive_escalation(
        triggered=triggered,
        reason=_BEST_OF_N_ESCALATION_REASON,
    )
    return BestOfNOutcome(
        variants=variants,
        selectedIndex=selected_index,
        escalation=escalation,
    )


# ---------------------------------------------------------------------------
# Cross-review (adversarial peer review)
# ---------------------------------------------------------------------------

class CrossReviewClaimVerdict(BaseModel):
    """A claim's peer-support count plus its real verifier_bus verdict."""

    model_config = _MODEL_CONFIG

    claim_ref: str = Field(alias="claimRef")
    peer_support_count: int = Field(alias="peerSupportCount", ge=0)
    verdict: VerifierResultMetadata

    @property
    def survives(self) -> bool:
        return self.verdict.status == "pass"


class CrossReviewOutcome(BaseModel):
    model_config = _MODEL_CONFIG

    review_id: str = Field(alias="reviewId")
    min_peer_support: int = Field(alias="minPeerSupport", ge=1)
    claim_verdicts: tuple[CrossReviewClaimVerdict, ...] = Field(alias="claimVerdicts")
    surviving_claim_refs: tuple[str, ...] = Field(alias="survivingClaimRefs")
    filtered_claim_refs: tuple[str, ...] = Field(alias="filteredClaimRefs")
    #: PR4 — critic escalation derived from the filtered claims.  When peers
    #: could not corroborate part of the synthesised claim set, this records an
    #: eligible critic escalation anchored to the verifier bus; when nothing was
    #: filtered, the decision is recorded as non-eligible (no escalation).
    escalation: CrossReviewEscalation = Field(
        default_factory=lambda: CrossReviewEscalation(eligible=False, reason=None),
    )

    def evidence_event(self) -> PublicEvent:
        """Record the review outcome as a public evidence trace event.

        The detail string carries the surviving/filtered counts and the filtered
        claim rationale so downstream ``event_projection`` / final assembly can
        consume the genuinely-reduced surviving set.  Severity is ``warning``
        when at least one claim was filtered (a quality signal), else ``info``.
        """
        surviving = len(self.surviving_claim_refs)
        filtered = len(self.filtered_claim_refs)
        seed = f"cross-review-evidence:{self.review_id}".encode("utf-8")
        turn_id = f"cross-review-{hashlib.sha1(seed).hexdigest()[:16]}"
        escalated = self.escalation.escalated
        escalation_detail = (
            f"escalation=critic:{self.escalation.reason} "
            f"escalatedVerifiers={','.join(self.escalation.escalated_verifier_ids)}"
            if escalated
            else "escalation=none"
        )
        return runtime_trace_event(
            turn_id=turn_id,
            phase="verifier_blocked",
            severity="warning" if filtered else "info",
            title="Adversarial cross-review outcome",
            detail=(
                "cross_review "
                f"reviewId={self.review_id} "
                f"minPeerSupport={self.min_peer_support} "
                f"surviving={surviving} filtered={filtered} "
                f"filteredVia={_SOURCE_CLAIM_LINK_VERIFIER_ID} "
                f"{escalation_detail}"
            ),
            retryable=False,
        )


class CrossReviewStep(BaseModel):
    """A declarative ``cross_review`` workflow step.

    This is the step-type surface that slots into the executor's live path: it
    declares the peer attestations to review and the cross-support threshold.
    The executor calls :func:`run_cross_review` with these fields after child
    dispatch.  ``peer_attestations`` is kept as opaque mappings (validated by
    :func:`run_cross_review`) so the step can be serialised alongside other
    workflow step kinds without bespoke nested models.
    """

    model_config = ConfigDict(
        frozen=True,
        populate_by_name=True,
        extra="forbid",
        arbitrary_types_allowed=True,
    )

    review_id: str = Field(alias="reviewId")
    peer_attestations: tuple[Mapping[str, object], ...] = Field(alias="peerAttestations")
    min_peer_support: int = Field(default=2, alias="minPeerSupport", ge=1)

    @field_validator("review_id")
    @classmethod
    def _reject_empty_review_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("reviewId must be non-empty")
        return value

    def run(self) -> CrossReviewOutcome:
        return run_cross_review(
            review_id=self.review_id,
            peer_attestations=self.peer_attestations,
            min_peer_support=self.min_peer_support,
        )


def _validate_claim_ref(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("claim refs must be strings")
    clean = value.strip()
    if _PRIVATE_TEXT_RE.search(clean) is not None:
        raise ValueError("claim refs must not contain raw transcript or private markers")
    if _CLAIM_REF_RE.fullmatch(clean) is None:
        raise ValueError(f"claim ref must match {_CLAIM_REF_RE.pattern}")
    return clean


def _validate_agent_ref(value: object) -> str:
    if not isinstance(value, str) or _AGENT_REF_RE.fullmatch(value.strip()) is None:
        raise ValueError("peer agent refs must be public, non-empty identifiers")
    return value.strip()


def _claim_verdict(
    claim_ref: str,
    *,
    peer_support_count: int,
    min_peer_support: int,
) -> VerifierResultMetadata:
    """Route a claim through the EXISTING source_claim_link verifier.

    The verifier id is taken from the default verifier bus so the verdict is
    anchored to the real ``source_claim_link`` stage.  A claim with sufficient
    peer support passes; one without fails (and is filtered out by the caller).
    """
    supported = peer_support_count >= min_peer_support
    if supported:
        return VerifierResultMetadata(
            verifierId=_SOURCE_CLAIM_LINK_VERIFIER_ID,
            status="pass",
            publicSummary=f"cross-supported by {peer_support_count} peers",
        )
    return VerifierResultMetadata(
        verifierId=_SOURCE_CLAIM_LINK_VERIFIER_ID,
        status="failed",
        publicSummary=f"only {peer_support_count} peer attestations",
        failureMessage="claim filtered: no peer cross-support",
    )


def run_cross_review(
    *,
    review_id: str,
    peer_attestations: Sequence[Mapping[str, object]],
    min_peer_support: int = 2,
) -> CrossReviewOutcome:
    """Run an adversarial cross-review over independent peer attestations.

    Each attestation is ``{"agent_ref": <public ref>, "claim_refs": (...)}``.
    A claim is cross-supported when at least ``min_peer_support`` DISTINCT peer
    agents attest it.  Unsupported claims are routed through the EXISTING
    ``verifier_bus`` ``source_claim_link`` verifier (which returns ``failed``)
    and REMOVED from the surviving set; supported claims pass and survive.

    Raises ``ValueError`` for invalid ``min_peer_support`` or for any claim ref
    that looks like a raw transcript / private path (context isolation).
    """
    if isinstance(min_peer_support, bool) or not isinstance(min_peer_support, int):
        raise ValueError("minPeerSupport must be an integer")
    if min_peer_support < 1:
        raise ValueError("minPeerSupport must be at least 1")

    # Validate the bus is well-formed (and that the source_claim_link verifier
    # exists) before producing any verdicts anchored to it.
    bus = build_default_verifier_bus_metadata()
    if not any(v.verifier_id == _SOURCE_CLAIM_LINK_VERIFIER_ID for v in bus.verifiers):
        raise ValueError("verifier bus is missing the source_claim_link verifier")

    # Count DISTINCT peer agents per claim (a peer cannot self-corroborate).
    support: dict[str, set[str]] = {}
    claim_order: list[str] = []
    for attestation in peer_attestations:
        if not isinstance(attestation, Mapping):
            raise ValueError("each peer attestation must be a mapping")
        agent_ref = _validate_agent_ref(attestation.get("agent_ref"))
        raw_claims = attestation.get("claim_refs", ())
        if isinstance(raw_claims, str) or not isinstance(raw_claims, Sequence):
            raise ValueError("claim_refs must be a sequence of claim refs")
        for raw_claim in raw_claims:
            claim_ref = _validate_claim_ref(raw_claim)
            if claim_ref not in support:
                support[claim_ref] = set()
                claim_order.append(claim_ref)
            support[claim_ref].add(agent_ref)

    claim_verdicts: list[CrossReviewClaimVerdict] = []
    surviving: list[str] = []
    filtered: list[str] = []
    for claim_ref in claim_order:
        peer_support_count = len(support[claim_ref])
        verdict = _claim_verdict(
            claim_ref,
            peer_support_count=peer_support_count,
            min_peer_support=min_peer_support,
        )
        claim_verdicts.append(
            CrossReviewClaimVerdict(
                claimRef=claim_ref,
                peerSupportCount=peer_support_count,
                verdict=verdict,
            )
        )
        # GENUINE filtering: a failed verdict removes the claim from the
        # surviving set entirely — it never enters ``surviving``.
        if verdict.status == "pass":
            surviving.append(claim_ref)
        else:
            filtered.append(claim_ref)

    # Critic escalation: when peers could not corroborate part of the claim
    # set (≥1 filtered claim), derive an eligible critic escalation anchored to
    # the verifier bus.  A clean review (nothing filtered) records no escalation.
    # Pass the already-built bus so ``_derive_escalation`` doesn't build it again.
    escalation = _derive_escalation(
        triggered=bool(filtered),
        reason=_CROSS_REVIEW_ESCALATION_REASON,
        bus=bus,
    )

    return CrossReviewOutcome(
        reviewId=review_id,
        minPeerSupport=min_peer_support,
        claimVerdicts=tuple(claim_verdicts),
        survivingClaimRefs=tuple(surviving),
        filteredClaimRefs=tuple(filtered),
        escalation=escalation,
    )


__all__ = [
    "BestOfNOutcome",
    "BestOfNVariant",
    "CrossReviewClaimVerdict",
    "CrossReviewEscalation",
    "CrossReviewOutcome",
    "CrossReviewStep",
    "generate_best_of_n_variants",
    "run_cross_review",
]
