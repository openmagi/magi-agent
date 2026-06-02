from __future__ import annotations

from magi_agent.recipes.compiler import (
    AgentRecipeCompiler,
    CompositionPolicyMetadata,
    ExplicitRecipeRef,
    ExplicitRecipeSelectionRequest,
    MissionLifecycleMetadata,
    PackRegistry,
    ProfileResolutionRequest,
    ProfileResolver,
    RecipeAttachmentFlags,
    RecipePackManifest,
    RecipeSelectionMetadata,
    RecipeSnapshot,
    ResolvedRecipeProfile,
    build_recipe_pack_digest,
)
from magi_agent.recipes.selector_validation import (
    RecipeSelectorFixture,
    RecipeSelectorVerdict,
    SelectorVerdictStatus,
    evaluate_recipe_selector_fixture,
)

_CODING_MUTATION_EXPORTS = {
    "CodingMutationConfig",
    "CodingMutationDecision",
    "CodingMutationMaterialization",
    "CodingMutationRecipe",
    "CodingMutationRequest",
    "materialize_coding_mutation_recipe",
}
_CODING_EVIDENCE_GATE_EXPORTS = {
    "CodingEvidenceGate",
    "CodingEvidenceGateAuthorityFlags",
    "CodingEvidenceGateConfig",
    "CodingEvidenceGateDecision",
    "CodingEvidenceGateHarnessBinding",
    "CodingEvidenceGateMaterialization",
    "CodingEvidenceGateRequest",
}
_CODING_SUBAGENT_EXPORTS = {
    "CodingSubagentConfig",
    "CodingSubagentFinding",
    "CodingSubagentModeRequest",
    "CodingSubagentRecipe",
    "CodingSubagentResult",
    "CodingSubagentToolScope",
}
_RESEARCH_CHILD_RUNNER_EXPORTS = {
    "ResearchChildRole",
    "ResearchChildRunnerAuthorityFlags",
    "ResearchChildRunnerConfig",
    "ResearchChildRunnerRecipe",
    "ResearchChildRunnerResult",
    "ResearchChildRunnerStatus",
    "ResearchChildSynthesisInput",
    "ResearchChildTaskSpec",
    "ResearchChildToolScope",
    "ResearchParentSynthesisInput",
    "ResearchSynthesisRequest",
}

__all__ = (
    "AgentRecipeCompiler",
    "CodingEvidenceGate",
    "CodingEvidenceGateAuthorityFlags",
    "CodingEvidenceGateConfig",
    "CodingEvidenceGateDecision",
    "CodingEvidenceGateHarnessBinding",
    "CodingEvidenceGateMaterialization",
    "CodingEvidenceGateRequest",
    "CodingMutationConfig",
    "CodingMutationDecision",
    "CodingMutationMaterialization",
    "CodingMutationRecipe",
    "CodingMutationRequest",
    "CodingSubagentConfig",
    "CodingSubagentFinding",
    "CodingSubagentModeRequest",
    "CodingSubagentRecipe",
    "CodingSubagentResult",
    "CodingSubagentToolScope",
    "CompositionPolicyMetadata",
    "ExplicitRecipeRef",
    "ExplicitRecipeSelectionRequest",
    "MissionLifecycleMetadata",
    "PackRegistry",
    "ProfileResolutionRequest",
    "ProfileResolver",
    "RecipeAttachmentFlags",
    "RecipePackManifest",
    "RecipeSelectionMetadata",
    "RecipeSelectorFixture",
    "RecipeSelectorVerdict",
    "RecipeSnapshot",
    "ResearchChildRole",
    "ResearchChildRunnerAuthorityFlags",
    "ResearchChildRunnerConfig",
    "ResearchChildRunnerRecipe",
    "ResearchChildRunnerResult",
    "ResearchChildRunnerStatus",
    "ResearchChildSynthesisInput",
    "ResearchChildTaskSpec",
    "ResearchChildToolScope",
    "ResearchParentSynthesisInput",
    "ResearchSynthesisRequest",
    "ResolvedRecipeProfile",
    "SelectorVerdictStatus",
    "build_recipe_pack_digest",
    "evaluate_recipe_selector_fixture",
    "materialize_coding_mutation_recipe",
)


def __getattr__(name: str) -> object:
    if name in _CODING_EVIDENCE_GATE_EXPORTS:
        from magi_agent.recipes import coding_evidence_gate

        return getattr(coding_evidence_gate, name)
    if name in _CODING_MUTATION_EXPORTS:
        from magi_agent.recipes import coding_mutation

        return getattr(coding_mutation, name)
    if name in _CODING_SUBAGENT_EXPORTS:
        from magi_agent.recipes import coding_subagents

        return getattr(coding_subagents, name)
    if name in _RESEARCH_CHILD_RUNNER_EXPORTS:
        from magi_agent.recipes import research_child_runner

        return getattr(research_child_runner, name)
    raise AttributeError(name)
