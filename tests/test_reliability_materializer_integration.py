from __future__ import annotations

import pytest

from magi_agent.recipes.compiler import (
    AgentRecipeCompiler,
    PackRegistry,
    ProfileResolutionRequest,
    RecipeSnapshot,
    build_recipe_snapshot_id,
)
from magi_agent.recipes.materializer import RecipeMaterializer


def test_materializer_adds_reliability_policy_for_research_on_cheap_model() -> None:
    snapshot = AgentRecipeCompiler(PackRegistry.with_first_party_packs()).compile(
        ProfileResolutionRequest(taskProfile={"taskType": "research"})
    )

    plan = RecipeMaterializer.with_reliability_defaults().materialize(
        snapshot,
        modelProvider="google",
        modelLabel="gemini-3.5-flash",
    )

    assert plan.reliability.model_tier == "cheap"
    assert "source_ledger" in plan.reliability.required_evidence
    assert "openmagi.context-safety" in plan.selected_pack_ids
    assert "openmagi.evidence" in plan.selected_pack_ids
    assert plan.context_budget.strategy == "refs_only_with_chunk_summaries"
    assert plan.final_gate_policy.missing_evidence_action == "insufficient_evidence"
    assert plan.phase_routing.max_sota_escalations == 1
    assert plan.phase_routing.budget_ledger.sota_escalation_count <= 1


def test_materializer_without_reliability_pack_uses_neutral_fallback_not_research() -> None:
    snapshot = RecipeSnapshot(
        snapshotId=build_recipe_snapshot_id(()),
        resolvedProfile={"taskType": "general"},
        selectedPackIds=(),
    )

    plan = RecipeMaterializer.with_reliability_defaults().materialize(
        snapshot,
        modelProvider="google",
        modelLabel="gemini-3.5-flash",
    )

    assert plan.reliability.recipe_id == "openmagi.context-safety"
    assert "citation_support" not in plan.reliability.required_evidence
    assert "source_ledger" not in plan.reliability.required_evidence
    assert "openmagi.research" not in plan.phase_routing.reason_codes


def test_materializer_adds_coding_small_patch_and_review_policy_for_cheap_model() -> None:
    snapshot = AgentRecipeCompiler(PackRegistry.with_first_party_packs()).compile(
        ProfileResolutionRequest(taskProfile={"taskType": "coding"})
    )

    plan = RecipeMaterializer.with_reliability_defaults().materialize(
        snapshot,
        modelProvider="google",
        modelLabel="gemini-3.5-flash",
    )

    assert plan.reliability.max_patch_files == 1
    assert "fresh_review" in plan.reliability.required_checkpoints
    assert "code_search" in plan.phase_routing.phase_routes
    assert "patch_generation" in plan.phase_routing.phase_routes


def test_materializer_represents_spreadsheet_browser_memory_methodology_and_multi_recipe_strictness() -> None:
    snapshot = AgentRecipeCompiler(PackRegistry.with_first_party_packs()).compile(
        ProfileResolutionRequest(
            taskProfile={
                "taskTypes": ["spreadsheet-automation", "browser-automation"],
                "taskIntents": ["agent-methodology", "memory-provider-eval", "artifact-delivery"],
            }
        )
    )

    plan = RecipeMaterializer.with_reliability_defaults().materialize(
        snapshot,
        modelProvider="google",
        modelLabel="gemini-3.5-flash",
        memoryMode="incognito",
    )

    assert plan.reliability.autonomy_level == "low"
    assert plan.context_budget.ref_groups["memory"] == []
    assert "calculation_evidence" in plan.reliability.required_evidence
    assert "external_action_approval" in plan.reliability.required_checkpoints
    assert "memory_mode_check" in plan.reliability.required_checkpoints
    assert "review_gate" in plan.reliability.required_checkpoints
    assert "delivery_ack_metadata" in plan.reliability.required_checkpoints
    assert plan.attachment_flags == {
        "providerCalled": False,
        "routeAttached": False,
        "adkRunnerInvoked": False,
        "productionWriteAllowed": False,
        "userVisibleOutputAllowed": False,
    }


def test_materializer_rejects_unknown_models_instead_of_relaxing_to_standard_policy() -> None:
    snapshot = AgentRecipeCompiler(PackRegistry.with_first_party_packs()).compile(
        ProfileResolutionRequest(taskProfile={"taskType": "research"})
    )

    with pytest.raises(ValueError, match="unknown model"):
        RecipeMaterializer.with_reliability_defaults().materialize(
            snapshot,
            modelProvider="example",
            modelLabel="unknown-model",
        )


def test_materializer_preserves_research_escalation_when_dependency_has_no_escalation() -> None:
    snapshot = AgentRecipeCompiler(PackRegistry.with_first_party_packs()).compile(
        ProfileResolutionRequest(taskProfile={"taskType": "research"})
    )

    plan = RecipeMaterializer.with_reliability_defaults().materialize(
        snapshot,
        modelProvider="google",
        modelLabel="gemini-3.5-flash",
        budgetUsd=0.25,
    )

    assert "openmagi.web-acquisition" in plan.selected_pack_ids
    assert plan.phase_routing.max_sota_escalations == 1
    assert plan.phase_routing.budget_ledger.sota_escalation_count == 1


def test_materializer_does_not_activate_pack_intents_for_blocked_composition_policy() -> None:
    snapshot = AgentRecipeCompiler(PackRegistry.with_first_party_packs()).compile(
        ProfileResolutionRequest(
            userProfile={"providers": {"web": "provider-a"}},
            workspacePolicy={"providerMap": {"web": "provider-b"}},
            taskProfile={"taskType": "research"},
        )
    )

    assert snapshot.composition_policy_metadata.blocked is True
    assert "openmagi.web-acquisition" in snapshot.selected_pack_ids
    assert snapshot.composition_policy_metadata.conflict_refs == ("provider.web",)

    plan = RecipeMaterializer.with_reliability_defaults().materialize(
        snapshot,
        modelProvider="google",
        modelLabel="gemini-3.5-flash",
    )

    assert plan.selected_pack_ids == ()
    assert plan.recipe_snapshot_id == build_recipe_snapshot_id(())
    assert plan.provider_intents == ()
    assert plan.tool_intents == ()
    assert plan.channel_intents == ()
    assert plan.artifact_intents == ()
    assert plan.scheduler_intents == ()
    assert "source_acquisition" not in plan.phase_routing.phase_routes
    assert "source_extraction" not in plan.phase_routing.phase_routes
    assert "source_ledger" not in plan.evidence_requirements
    assert "requirement:opened-or-observed-source-proof" not in plan.evidence_requirements
    assert "runtime_evidence_record" in plan.reliability.required_evidence
    assert "no_raw_evidence_payload" in plan.reliability.required_validators
    assert "runtime_evidence_record" in plan.final_gate_policy.required_evidence
    assert "no_raw_evidence_payload" in plan.final_gate_policy.required_validators
    assert plan.approval_gates == ("approval:composition-policy:requires-clarification",)


def test_materializer_ignores_stale_pack_refs_on_blocked_composition_policy() -> None:
    snapshot = AgentRecipeCompiler(PackRegistry.with_first_party_packs()).compile(
        ProfileResolutionRequest(
            userProfile={"providers": {"web": "provider-a"}},
            workspacePolicy={"providerMap": {"web": "provider-b"}},
            taskProfile={"taskType": "research"},
        )
    )
    stale_snapshot = snapshot.model_copy(
        update={
            "evidenceRefs": ("evidence:web-acquisition:source-ledger-input",),
            "validatorRefs": ("verifier:web-acquisition:provider-boundary",),
            "approvalGateRefs": (
                "approval:composition-policy:requires-clarification",
                "approval:web-acquisition:provider-opt-in",
            ),
        }
    )

    plan = RecipeMaterializer.with_reliability_defaults().materialize(
        stale_snapshot,
        modelProvider="google",
        modelLabel="gemini-3.5-flash",
    )

    assert plan.context_budget.ref_groups["evidence"] == []
    assert plan.context_budget.ref_groups["source"] == []
    assert "evidence:web-acquisition:source-ledger-input" not in plan.evidence_requirements
    assert (
        "verifier:web-acquisition:provider-boundary"
        not in plan.final_gate_policy.required_validators
    )
    assert plan.approval_gates == ("approval:composition-policy:requires-clarification",)
    assert "approval:web-acquisition:provider-opt-in" not in plan.approval_gates


def test_materializer_derives_blocked_approval_gates_from_current_blocker_state() -> None:
    snapshot = AgentRecipeCompiler(PackRegistry.with_first_party_packs()).compile(
        ProfileResolutionRequest(
            userProfile={"providers": {"web": "provider-a"}},
            workspacePolicy={"providerMap": {"web": "provider-b"}},
            taskProfile={"taskType": "research"},
        )
    )
    stale_snapshot = snapshot.model_copy(
        update={
            "approvalGateRefs": (
                "approval:recipe-selection:blocked",
                "approval:composition-policy:requires-clarification",
                "approval:web-acquisition:provider-opt-in",
            ),
        }
    )

    plan = RecipeMaterializer.with_reliability_defaults().materialize(
        stale_snapshot,
        modelProvider="google",
        modelLabel="gemini-3.5-flash",
    )

    assert plan.approval_gates == ("approval:composition-policy:requires-clarification",)


def test_materializer_does_not_reactivate_admission_blocked_stale_applied_refs() -> None:
    snapshot = AgentRecipeCompiler(PackRegistry.with_first_party_packs()).compile(
        ProfileResolutionRequest(taskProfile={"taskType": "research"})
    )
    stale_snapshot = snapshot.model_copy(
        update={
            "recipeSelection": snapshot.recipe_selection.model_copy(
                update={"admissionBlocked": True}
            )
        }
    )

    assert stale_snapshot.recipe_selection.admission_blocked is True
    assert stale_snapshot.recipe_selection.applied_recipe_refs
    assert "openmagi.web-acquisition" in stale_snapshot.selected_pack_ids

    plan = RecipeMaterializer.with_reliability_defaults().materialize(
        stale_snapshot,
        modelProvider="google",
        modelLabel="gemini-3.5-flash",
    )

    assert plan.selected_pack_ids == ()
    assert plan.recipe_snapshot_id == build_recipe_snapshot_id(())
    assert plan.provider_intents == ()
    assert plan.tool_intents == ()
    assert "source_acquisition" not in plan.phase_routing.phase_routes
    assert "source_extraction" not in plan.phase_routing.phase_routes
    assert "source_ledger" not in plan.evidence_requirements
    assert plan.approval_gates == ("approval:recipe-selection:blocked",)


def test_materializer_reports_budget_denial_in_phase_routing_metadata() -> None:
    snapshot = AgentRecipeCompiler(PackRegistry.with_first_party_packs()).compile(
        ProfileResolutionRequest(taskProfile={"taskType": "research"})
    )

    plan = RecipeMaterializer.with_reliability_defaults().materialize(
        snapshot,
        modelProvider="google",
        modelLabel="gemini-3.5-flash",
        budgetUsd=0.001,
    )

    assert plan.phase_routing.route_denied is True
    assert plan.phase_routing.denial_reason == "budget_too_low"
    assert plan.budget_ledger.total_reserved_cost_usd <= 0.001
