"""PR4 — Adversarial cross-review + best-of-N variant generation.

Track 17 PR4 wires EXISTING ``harness/inference_scaling`` (best-of-N) and
EXISTING ``harness/verifier_bus`` (claim filtering) into a real ``cross_review``
quality step, recording the outcome as evidence.

The three mandatory behaviours locked here:

1. **Best-of-N produces N variants + a selection.** When the eligibility
   metadata authorises ``maxVariants > 1`` (verifier-rankable + side-effect
   safe), the runtime generates exactly N variants and selects one per the
   verifier ranking — bounded by the executor concurrency cap (≤16).

2. **A claim unsupported by peers is genuinely FILTERED (not labelled).**
   Independent peer agents attest claims.  A claim that no peer corroborates is
   routed through the EXISTING ``verifier_bus`` ``source_claim_link`` stage,
   which returns a ``failed`` verdict, and the claim is REMOVED from the
   surviving claim set.  Cross-supported claims survive.

3. **Evidence records the review outcome.** The surviving-vs-filtered split and
   the per-claim rationale are captured as an evidence record (a public trace
   event dict suitable for ``event_projection``).

Invariants preserved: default-OFF (the runtime path only filters when the
executor env gate is on); concurrency bounds (best-of-N respects the semaphore
cap); context isolation (peers see sanitised claim refs, never raw transcripts).
"""
from __future__ import annotations

import asyncio
import os

import pytest

from magi_agent.harness.inference_scaling import BestOfNEligibilityMetadata


# ---------------------------------------------------------------------------
# Fixtures: peer attestations
# ---------------------------------------------------------------------------

def _supported_topology() -> tuple[dict[str, object], ...]:
    """Three peer agents.  Two claims are cross-supported; one is orphan.

    - ``claim:fact-a``  attested by peers 0 and 1 → cross-supported (survives)
    - ``claim:fact-b``  attested by peers 1 and 2 → cross-supported (survives)
    - ``claim:fact-orphan`` attested only by peer 2 → NO peer support (filtered)
    """
    return (
        {
            "agent_ref": "peer:agent-0",
            "claim_refs": ("claim:fact-a",),
        },
        {
            "agent_ref": "peer:agent-1",
            "claim_refs": ("claim:fact-a", "claim:fact-b"),
        },
        {
            "agent_ref": "peer:agent-2",
            "claim_refs": ("claim:fact-b", "claim:fact-orphan"),
        },
    )


# ---------------------------------------------------------------------------
# Test 1 — best-of-N produces N variants + a selection
# ---------------------------------------------------------------------------

def test_best_of_n_produces_n_variants_and_a_selection() -> None:
    from magi_agent.harness.cross_review import generate_best_of_n_variants

    eligibility = BestOfNEligibilityMetadata(
        verifierCanRankOutcomes=True,
        sideEffectsSafe=True,
        sideEffectClass="read_only",
        maxVariants=4,
    )
    assert eligibility.eligible is True

    outcome = asyncio.run(
        generate_best_of_n_variants(
            objective="synthesise the cited research answer",
            eligibility=eligibility,
            concurrency_cap=16,
        )
    )

    # Exactly N variants generated.
    assert len(outcome.variants) == 4
    # Exactly one is selected.
    assert outcome.selected_index is not None
    assert 0 <= outcome.selected_index < 4
    assert outcome.selected_variant is outcome.variants[outcome.selected_index]
    # The selection is verifier-ranked: the selected variant has the top score.
    assert outcome.selected_variant.score == max(v.score for v in outcome.variants)


def test_best_of_n_disabled_metadata_yields_single_variant() -> None:
    """When eligibility is not met (maxVariants=1), only one variant is made."""
    from magi_agent.harness.cross_review import generate_best_of_n_variants

    eligibility = BestOfNEligibilityMetadata(
        verifierCanRankOutcomes=False,
        sideEffectsSafe=True,
        sideEffectClass="none",
        maxVariants=1,
    )
    assert eligibility.eligible is False

    outcome = asyncio.run(
        generate_best_of_n_variants(
            objective="single shot",
            eligibility=eligibility,
            concurrency_cap=16,
        )
    )
    assert len(outcome.variants) == 1
    assert outcome.selected_index == 0


def test_best_of_n_respects_concurrency_cap() -> None:
    """N is clamped to the executor concurrency cap (bounded variant explosion)."""
    from magi_agent.harness.cross_review import generate_best_of_n_variants

    eligibility = BestOfNEligibilityMetadata(
        verifierCanRankOutcomes=True,
        sideEffectsSafe=True,
        sideEffectClass="read_only",
        maxVariants=8,
    )
    outcome = asyncio.run(
        generate_best_of_n_variants(
            objective="bounded",
            eligibility=eligibility,
            concurrency_cap=3,
        )
    )
    # maxVariants=8 but cap=3 → only 3 variants generated.
    assert len(outcome.variants) == 3


# ---------------------------------------------------------------------------
# Test 2 — a claim unsupported by peers is FILTERED (not just labelled)
# ---------------------------------------------------------------------------

def test_unsupported_claim_is_filtered_via_verifier_bus() -> None:
    from magi_agent.harness.cross_review import run_cross_review

    review = run_cross_review(
        review_id="cross-review-1",
        peer_attestations=_supported_topology(),
        min_peer_support=2,
    )

    surviving = set(review.surviving_claim_refs)
    filtered = set(review.filtered_claim_refs)

    # Cross-supported claims survive.
    assert "claim:fact-a" in surviving
    assert "claim:fact-b" in surviving

    # The orphan claim is genuinely REMOVED from the surviving set.
    assert "claim:fact-orphan" not in surviving
    assert "claim:fact-orphan" in filtered

    # The surviving set is strictly smaller than the full claim universe —
    # proof the filter actually removed a claim rather than tagging it.
    all_claims = {"claim:fact-a", "claim:fact-b", "claim:fact-orphan"}
    assert surviving == all_claims - {"claim:fact-orphan"}
    assert surviving | filtered == all_claims
    assert surviving.isdisjoint(filtered)


def test_filtering_routes_through_source_claim_link_verifier() -> None:
    """Every claim must carry a real verifier verdict from the EXISTING bus.

    The orphan claim's verdict status must be 'failed' (the reason it was
    filtered); the supported claims' verdict status must be 'pass'.  Verdicts
    are produced via the verifier_bus ``source_claim_link`` verifier id.
    """
    from magi_agent.harness.cross_review import run_cross_review

    review = run_cross_review(
        review_id="cross-review-2",
        peer_attestations=_supported_topology(),
        min_peer_support=2,
    )

    verdicts = {v.claim_ref: v for v in review.claim_verdicts}
    assert verdicts["claim:fact-orphan"].verdict.status == "failed"
    assert verdicts["claim:fact-a"].verdict.status == "pass"
    assert verdicts["claim:fact-b"].verdict.status == "pass"

    # The verdicts are anchored to the real source_claim_link verifier.
    for v in review.claim_verdicts:
        assert v.verdict.verifier_id == "source-claim-link"


def test_all_claims_supported_none_filtered() -> None:
    """When every claim is cross-supported, the surviving set is unchanged."""
    from magi_agent.harness.cross_review import run_cross_review

    attestations = (
        {"agent_ref": "peer:a", "claim_refs": ("claim:x", "claim:y")},
        {"agent_ref": "peer:b", "claim_refs": ("claim:x", "claim:y")},
    )
    review = run_cross_review(
        review_id="cross-review-3",
        peer_attestations=attestations,
        min_peer_support=2,
    )
    assert set(review.surviving_claim_refs) == {"claim:x", "claim:y"}
    assert review.filtered_claim_refs == ()


# ---------------------------------------------------------------------------
# Test 3 — evidence records the review outcome
# ---------------------------------------------------------------------------

def test_evidence_records_review_outcome() -> None:
    from magi_agent.harness.cross_review import run_cross_review

    review = run_cross_review(
        review_id="cross-review-4",
        peer_attestations=_supported_topology(),
        min_peer_support=2,
    )

    event = review.evidence_event()
    assert isinstance(event, dict)
    assert event.get("type") == "runtime_trace"
    detail = str(event.get("detail", ""))
    # The evidence captures the surviving/filtered counts and rationale.
    assert "cross_review" in detail
    assert "surviving=2" in detail
    assert "filtered=1" in detail


def test_evidence_warns_when_claims_are_filtered() -> None:
    """A run that filters at least one claim records a 'warning' severity
    evidence event (a quality signal), while a clean run records 'info'."""
    from magi_agent.harness.cross_review import run_cross_review

    filtered_run = run_cross_review(
        review_id="cross-review-5",
        peer_attestations=_supported_topology(),
        min_peer_support=2,
    )
    assert filtered_run.evidence_event().get("severity") == "warning"

    clean_run = run_cross_review(
        review_id="cross-review-6",
        peer_attestations=(
            {"agent_ref": "peer:a", "claim_refs": ("claim:x",)},
            {"agent_ref": "peer:b", "claim_refs": ("claim:x",)},
        ),
        min_peer_support=2,
    )
    assert clean_run.evidence_event().get("severity") == "info"


# ---------------------------------------------------------------------------
# Invariants: context isolation — peers see only claim refs, never transcripts
# ---------------------------------------------------------------------------

def test_cross_review_rejects_raw_transcript_in_attestation() -> None:
    """Context isolation: attestations carry sanitised claim refs only.

    A claim ref that looks like a raw transcript / private path must be
    rejected, never silently accepted into the surviving set.
    """
    from magi_agent.harness.cross_review import run_cross_review

    with pytest.raises(ValueError):
        run_cross_review(
            review_id="cross-review-7",
            peer_attestations=(
                {
                    "agent_ref": "peer:a",
                    "claim_refs": ("/Users/kevin/secret/raw_transcript.txt",),
                },
                {"agent_ref": "peer:b", "claim_refs": ("claim:x",)},
            ),
            min_peer_support=2,
        )


# ---------------------------------------------------------------------------
# Invariant: bounded — min_peer_support must be a sane positive integer
# ---------------------------------------------------------------------------

def test_cross_review_rejects_invalid_min_peer_support() -> None:
    from magi_agent.harness.cross_review import run_cross_review

    with pytest.raises(ValueError):
        run_cross_review(
            review_id="cross-review-8",
            peer_attestations=_supported_topology(),
            min_peer_support=0,
        )


# ---------------------------------------------------------------------------
# Critic / escalation — metadata drives a recorded decision
# ---------------------------------------------------------------------------

def test_cross_review_filtered_claims_drive_critic_escalation() -> None:
    """A cross-review that FILTERS a claim records a critic escalation anchored
    to the verifier_bus — and the escalation admits the llm_critic verifier that
    is otherwise disabled.  This proves the escalation is a real metadata-driven
    decision, not a static field."""
    from magi_agent.harness.cross_review import run_cross_review
    from magi_agent.harness.verifier_bus import build_default_verifier_bus_metadata

    review = run_cross_review(
        review_id="cross-review-escalation",
        peer_attestations=_supported_topology(),
        min_peer_support=2,
    )

    # A claim was genuinely filtered → escalation is eligible.
    assert review.filtered_claim_refs == ("claim:fact-orphan",)
    assert review.escalation.eligible is True
    assert review.escalation.escalated is True
    assert review.escalation.reason == "synthesis_quality"

    # The escalated verifier ids are EXACTLY the verifiers the real verifier_bus
    # admits ONLY because the escalation reason is supplied (the llm_critic
    # stage that is otherwise disabled).  This anchors the decision to a genuine
    # verifier-bus state change.
    bus = build_default_verifier_bus_metadata()
    baseline = {
        v.verifier_id
        for v in bus.effective_verifiers(deterministic_prerequisites_satisfied=True)
    }
    escalated = bus.effective_verifiers(
        deterministic_prerequisites_satisfied=True,
        escalationReason="synthesis_quality",
    )
    expected_admitted = tuple(
        v.verifier_id for v in escalated if v.verifier_id not in baseline
    )
    assert expected_admitted  # the escalation actually admits something
    assert review.escalation.escalated_verifier_ids == expected_admitted
    # That admitted verifier is the semantic llm_critic stage.
    admitted = {v.verifier_id: v for v in escalated}
    for vid in review.escalation.escalated_verifier_ids:
        assert admitted[vid].stage == "llm_critic"


def test_clean_cross_review_records_no_escalation() -> None:
    """A cross-review with nothing filtered records a NON-eligible escalation —
    proving the escalation tracks real review quality, not a constant."""
    from magi_agent.harness.cross_review import run_cross_review

    review = run_cross_review(
        review_id="cross-review-clean",
        peer_attestations=(
            {"agent_ref": "peer:a", "claim_refs": ("claim:x",)},
            {"agent_ref": "peer:b", "claim_refs": ("claim:x",)},
        ),
        min_peer_support=2,
    )
    assert review.filtered_claim_refs == ()
    assert review.escalation.eligible is False
    assert review.escalation.escalated is False
    assert review.escalation.reason is None
    assert review.escalation.escalated_verifier_ids == ()


def test_cross_review_escalation_surfaced_in_evidence_detail() -> None:
    """The recorded escalation decision flows into the evidence detail string so
    downstream final assembly can consume it."""
    from magi_agent.harness.cross_review import run_cross_review

    filtered = run_cross_review(
        review_id="cross-review-evidence-escalation",
        peer_attestations=_supported_topology(),
        min_peer_support=2,
    )
    detail = str(filtered.evidence_event().get("detail", ""))
    assert "escalation=critic:synthesis_quality" in detail
    assert "escalatedVerifiers=llm-critic-fuzzy-quality" in detail

    clean = run_cross_review(
        review_id="cross-review-evidence-clean",
        peer_attestations=(
            {"agent_ref": "peer:a", "claim_refs": ("claim:x",)},
            {"agent_ref": "peer:b", "claim_refs": ("claim:x",)},
        ),
        min_peer_support=2,
    )
    assert "escalation=none" in str(clean.evidence_event().get("detail", ""))


def test_best_of_n_ineligible_drives_critic_escalation() -> None:
    """When best-of-N cannot rank multiple variants (ineligible), the outcome
    records an eligible critic escalation anchored to the verifier_bus."""
    from magi_agent.harness.cross_review import generate_best_of_n_variants

    eligibility = BestOfNEligibilityMetadata(
        verifierCanRankOutcomes=False,
        sideEffectsSafe=True,
        sideEffectClass="none",
        maxVariants=1,
    )
    assert eligibility.eligible is False

    outcome = asyncio.run(
        generate_best_of_n_variants(
            objective="single shot",
            eligibility=eligibility,
            concurrency_cap=16,
        )
    )
    assert outcome.escalation.escalated is True
    assert outcome.escalation.reason == "fuzzy_quality"
    assert outcome.escalation.escalated_verifier_ids == ("llm-critic-fuzzy-quality",)


def test_best_of_n_low_confidence_selection_drives_escalation() -> None:
    """An eligible best-of-N whose top-ranked variant is below the
    low-confidence threshold still escalates — the recorded decision tracks the
    real selection score, deterministically."""
    from magi_agent.harness.cross_review import (
        _LOW_CONFIDENCE_SELECTION_THRESHOLD,
        generate_best_of_n_variants,
    )

    eligibility = BestOfNEligibilityMetadata(
        verifierCanRankOutcomes=True,
        sideEffectsSafe=True,
        sideEffectClass="read_only",
        maxVariants=2,
    )
    # Objective 'h' deterministically yields a best-of-2 top score <= 0.5.
    outcome = asyncio.run(
        generate_best_of_n_variants(
            objective="h",
            eligibility=eligibility,
            concurrency_cap=16,
        )
    )
    assert outcome.selected_variant.score <= _LOW_CONFIDENCE_SELECTION_THRESHOLD
    assert outcome.escalation.escalated is True
    assert outcome.escalation.reason == "fuzzy_quality"


def test_best_of_n_high_confidence_selection_records_no_escalation() -> None:
    """An eligible best-of-N with a high-confidence top-ranked variant records
    NO escalation — proving the signal is driven by the real selection, not a
    constant."""
    from magi_agent.harness.cross_review import (
        _LOW_CONFIDENCE_SELECTION_THRESHOLD,
        generate_best_of_n_variants,
    )

    eligibility = BestOfNEligibilityMetadata(
        verifierCanRankOutcomes=True,
        sideEffectsSafe=True,
        sideEffectClass="read_only",
        maxVariants=4,
    )
    outcome = asyncio.run(
        generate_best_of_n_variants(
            objective="synthesise the cited research answer",
            eligibility=eligibility,
            concurrency_cap=16,
        )
    )
    assert outcome.selected_variant.score > _LOW_CONFIDENCE_SELECTION_THRESHOLD
    assert outcome.escalation.escalated is False
    assert outcome.escalation.reason is None


# ---------------------------------------------------------------------------
# Executor wiring — the cross_review step slots into the live executor path
# ---------------------------------------------------------------------------

from magi_agent.workflows.compiler import (  # noqa: E402
    CompiledWorkflowContract,
    compile_governed_workflow,
    WorkflowCompileInput,
)
from magi_agent.workflows.registry import WorkflowRegistryEntry  # noqa: E402

_DIGEST = "sha256:" + "a" * 64

_FAKE_EVIDENCE_REF = "evidence:abcdef1234567890"
_FAKE_CHILD_EXEC_ID = "child:1234567890abcdef"


class _TrackingFakeRunner:
    openmagi_local_fake_provider = True

    def __init__(self) -> None:
        self.calls = 0

    async def run_child(self, request: object) -> dict[str, object]:
        self.calls += 1
        return {
            "childExecutionId": _FAKE_CHILD_EXEC_ID,
            "status": "completed",
            "summary": "fake completed",
            "evidenceRefs": (_FAKE_EVIDENCE_REF,),
            "artifactRefs": (),
            "auditEventRefs": (),
        }


def _valid_contract(n_recipes: int = 2) -> CompiledWorkflowContract:
    n_recipes = max(1, min(n_recipes, 8))
    entries = tuple(_registry_entry(version=f"1.0.{i}") for i in range(n_recipes))
    recipe_ids = tuple(f"openmagi.research.cited.v1.0.{i}" for i in range(n_recipes))
    config = WorkflowCompileInput(
        workflowId="openmagi.research.cited",
        version="1.0.0",
        selectedRecipes=recipe_ids,
        registeredWorkflows=entries,
        toolAllowlist=("SourceLedgerRead", "SearchFiles"),
        toolDenylist=(),
        evidenceRequirements=("SourceInspection",),
        validatorRefs=("deterministic-verifier",),
        projectionPolicy="structured_claims_only",
        repairPolicy="retry-once",
        approvalPolicy="auto",
        contextProjectionPolicy="explicit",
        budgets={"maxIterations": 10, "wallClockTimeoutMs": 60_000},
        hardInvariants={
            "rawDraftStreamingForbidden": True,
            "toolhostOnlyExecution": True,
            "validatorBeforeProjection": True,
        },
        effectivePolicySnapshotDigest=_DIGEST,
        availableTools=("SourceLedgerRead", "SearchFiles"),
        availableValidators=("deterministic-verifier",),
        availableRenderers=("structured_claims_only",),
        evidenceProducers=("SourceInspection",),
        routePrecedence=(),
        noMatchTerminalState="block",
    )
    return compile_governed_workflow(config)


def _registry_entry(
    workflow_id: str = "openmagi.research.cited",
    version: str = "1.0.0",
) -> WorkflowRegistryEntry:
    return WorkflowRegistryEntry(
        workflowId=workflow_id,
        version=version,
        ownerRef="team-digest:research",
        status="active",
        sourceDigest=_DIGEST,
        promotionHistory=("draft:2026-05-01", "staging:2026-05-02", "active:2026-05-03"),
        compatibleRuntimeContractVersion="programmable-determinism.v1",
    )


def test_executor_cross_review_step_filters_and_emits_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When a cross_review step is wired into the live executor, an unsupported
    claim is filtered from the result and an evidence event is emitted."""
    monkeypatch.setenv("MAGI_WORKFLOW_EXECUTOR_ENABLED", "1")

    from magi_agent.harness.cross_review import CrossReviewStep
    from magi_agent.harness.workflow_executor import (
        WorkflowExecutorConfig,
        execute_workflow,
    )

    contract = _valid_contract(n_recipes=2)
    config = WorkflowExecutorConfig(enabled=True, local_fake_child_runner_enabled=True)
    step = CrossReviewStep(
        review_id="exec-cross-review",
        peer_attestations=_supported_topology(),
        min_peer_support=2,
    )
    events: list[dict[str, object]] = []

    result = asyncio.run(
        execute_workflow(
            contract,
            config=config,
            child_runner=_TrackingFakeRunner(),
            event_sink=events.append,
            cross_review_step=step,
        )
    )

    assert result.status in {"accepted", "partial"}
    # The filtered orphan claim is surfaced on the result, genuinely removed
    # from the surviving claim refs.
    assert "claim:fact-orphan" in result.cross_review_filtered_claim_refs
    assert "claim:fact-orphan" not in result.cross_review_surviving_claim_refs
    assert set(result.cross_review_surviving_claim_refs) == {"claim:fact-a", "claim:fact-b"}
    # An evidence event was emitted for the cross-review outcome.
    cross_events = [e for e in events if "cross_review" in str(e.get("detail", ""))]
    assert len(cross_events) == 1
    assert cross_events[0].get("severity") == "warning"
    # The critic escalation derived from the filtered claim is surfaced on the
    # executor result and recorded in the evidence detail.
    assert result.cross_review_escalation_eligible is True
    assert result.cross_review_escalation_reason == "synthesis_quality"
    assert result.cross_review_escalated_verifier_ids == ("llm-critic-fuzzy-quality",)
    assert "escalation=critic:synthesis_quality" in str(cross_events[0].get("detail", ""))


def test_executor_without_cross_review_step_is_byte_identical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When no cross_review step is supplied, the executor result carries empty
    cross-review fields — byte-identical to PR3 behaviour."""
    monkeypatch.setenv("MAGI_WORKFLOW_EXECUTOR_ENABLED", "1")

    from magi_agent.harness.workflow_executor import (
        WorkflowExecutorConfig,
        execute_workflow,
    )

    contract = _valid_contract(n_recipes=2)
    config = WorkflowExecutorConfig(enabled=True, local_fake_child_runner_enabled=True)

    result = asyncio.run(
        execute_workflow(contract, config=config, child_runner=_TrackingFakeRunner())
    )
    assert result.cross_review_filtered_claim_refs == ()
    assert result.cross_review_surviving_claim_refs == ()
    assert result.cross_review_escalation_eligible is False
    assert result.cross_review_escalation_reason is None
    assert result.cross_review_escalated_verifier_ids == ()


def test_executor_cross_review_skipped_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default-OFF: with the executor env gate off, the cross_review step never
    runs even when supplied — no filtering, no evidence event."""
    monkeypatch.delenv("MAGI_WORKFLOW_EXECUTOR_ENABLED", raising=False)

    from magi_agent.harness.cross_review import CrossReviewStep
    from magi_agent.harness.workflow_executor import (
        WorkflowExecutorConfig,
        execute_workflow,
    )

    contract = _valid_contract(n_recipes=2)
    config = WorkflowExecutorConfig(enabled=True, local_fake_child_runner_enabled=True)
    step = CrossReviewStep(
        review_id="exec-cross-review-off",
        peer_attestations=_supported_topology(),
        min_peer_support=2,
    )
    events: list[dict[str, object]] = []

    result = asyncio.run(
        execute_workflow(
            contract,
            config=config,
            child_runner=_TrackingFakeRunner(),
            event_sink=events.append,
            cross_review_step=step,
        )
    )
    assert result.status == "disabled"
    assert result.cross_review_filtered_claim_refs == ()
    assert events == []
