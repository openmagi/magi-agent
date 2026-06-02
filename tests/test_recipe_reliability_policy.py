from __future__ import annotations

from openmagi_core_agent.recipes.reliability_policy import (
    RecipeReliabilityPolicyRegistry,
)


def test_research_cheap_model_requires_source_ledger_fact_grounding_and_abstention() -> None:
    policy = RecipeReliabilityPolicyRegistry.with_defaults().for_recipe(
        "openmagi.research",
        modelTier="cheap",
    )

    assert policy.context_strategy == "chunk_refs_only"
    assert "source_ledger" in policy.required_evidence
    assert "fact_grounding" in policy.required_validators
    assert policy.final_answer_without_evidence == "insufficient_evidence"
    assert policy.autonomy_level == "low"


def test_dev_coding_cheap_model_requires_small_patches_and_review_checkpoint() -> None:
    policy = RecipeReliabilityPolicyRegistry.with_defaults().for_recipe(
        "openmagi.dev-coding",
        modelTier="cheap",
    )

    assert policy.max_patch_files == 1
    assert "test_or_not_run_reason" in policy.required_evidence
    assert "fresh_review" in policy.required_checkpoints


def test_first_party_recipe_policies_cover_required_domains() -> None:
    registry = RecipeReliabilityPolicyRegistry.with_defaults()

    expected_pack_ids = {
        "openmagi.web-acquisition",
        "openmagi.office-automation",
        "openmagi.spreadsheet-automation",
        "openmagi.browser-automation",
        "openmagi.agent-methodology",
        "openmagi.memory-agentmemory",
        "openmagi.missions",
        "openmagi.document-review",
        "openmagi.lightweight-scripting",
        "openmagi.artifact-delivery",
    }

    assert expected_pack_ids.issubset(set(registry.recipe_ids()))


def test_cheap_model_policies_are_more_restrictive_than_standard() -> None:
    registry = RecipeReliabilityPolicyRegistry.with_defaults()

    cheap = registry.for_recipe("openmagi.office-automation", modelTier="cheap")
    standard = registry.for_recipe("openmagi.office-automation", modelTier="standard")

    assert cheap.max_context_refs <= standard.max_context_refs
    assert cheap.max_raw_input_bytes <= standard.max_raw_input_bytes
    assert cheap.autonomy_level == "low"
    assert standard.autonomy_level in {"low", "medium"}
    assert set(standard.required_evidence).issuperset({"redaction_audit"})


def test_standard_policy_never_drops_hard_safety_requirements() -> None:
    registry = RecipeReliabilityPolicyRegistry.with_defaults()

    for recipe_id in registry.recipe_ids():
        policy = registry.for_recipe(recipe_id, modelTier="standard")
        assert "redaction_audit" in policy.required_evidence
        assert "public_redaction" in policy.required_validators
        assert "no_production_attachment" in policy.required_validators


def test_sota_is_escalation_not_implicit_default_executor() -> None:
    policy = RecipeReliabilityPolicyRegistry.with_defaults().for_recipe(
        "openmagi.research",
        modelTier="sota",
    )

    assert policy.minimum_model_tier == "standard"
    assert policy.preferred_model_tier == "sota"
    assert policy.sota_escalation_allowed is True
    assert "final_verification" in policy.sota_escalation_reasons
    assert policy.max_sota_escalations <= 1


def test_policy_serializes_deterministic_sorted_tuples() -> None:
    policy = RecipeReliabilityPolicyRegistry.with_defaults().for_recipe(
        "openmagi.browser-automation",
        modelTier="cheap",
    )
    dumped = policy.model_dump(by_alias=True)

    assert dumped["recipeId"] == "openmagi.browser-automation"
    assert list(dumped["requiredEvidence"]) == sorted(dumped["requiredEvidence"])
    assert list(dumped["requiredValidators"]) == sorted(dumped["requiredValidators"])
    assert list(dumped["requiredCheckpoints"]) == sorted(dumped["requiredCheckpoints"])
