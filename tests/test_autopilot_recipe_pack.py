from __future__ import annotations

from magi_agent.recipes.compiler import (
    AgentRecipeCompiler,
    PackRegistry,
    ProfileResolutionRequest,
    ProfileResolver,
)


def test_autopilot_pack_registered_and_default_off() -> None:
    registry = PackRegistry.with_first_party_packs()
    pack = registry.get("openmagi.autopilot")
    assert pack.default_enabled is False
    assert pack.opt_out_allowed is True
    assert pack.customizable is True
    assert pack.hard_safety is False
    assert pack.depends_on_pack_ids == (
        "openmagi.agent-methodology",
        "openmagi.dev-coding",
    )
    assert pack.task_profile_selectors == (
        "autopilot",
        "autonomous",
        "full-auto",
        "build-me",
    )
    assert {
        "validator:autopilot:interview-ambiguity-cleared",
        "validator:autopilot:consensus-architect-then-critic",
        "validator:autopilot:review-clean",
        "validator:autopilot:qa-passed-or-skipped",
        "validator:autopilot:max-review-cycle-bounded",
    }.issubset(set(pack.validator_refs))
    assert {
        "checkpoint:autopilot:interview",
        "checkpoint:autopilot:consensus-plan",
        "checkpoint:autopilot:execute",
        "checkpoint:autopilot:review",
        "checkpoint:autopilot:qa",
        "checkpoint:autopilot:return-to-plan",
    }.issubset(set(pack.checkpoint_refs))


def test_autopilot_not_selected_by_default() -> None:
    registry = PackRegistry.with_first_party_packs()
    resolved = ProfileResolver(registry).resolve(ProfileResolutionRequest())
    assert "openmagi.autopilot" not in resolved.selected_pack_ids


def test_autopilot_selected_by_task_type_and_pulls_dependencies() -> None:
    registry = PackRegistry.with_first_party_packs()
    resolved = ProfileResolver(registry).resolve(
        ProfileResolutionRequest(taskProfile={"taskType": "autopilot"})
    )
    assert "openmagi.autopilot" in resolved.selected_pack_ids
    assert "openmagi.agent-methodology" in resolved.selected_pack_ids
    assert "openmagi.dev-coding" in resolved.selected_pack_ids


def test_autopilot_opt_out_respected() -> None:
    registry = PackRegistry.with_first_party_packs()
    resolved = ProfileResolver(registry).resolve(
        ProfileResolutionRequest(
            taskProfile={"taskType": "autopilot"},
            recipePackConfig={"packs": {"disable": ["openmagi.autopilot"]}},
        )
    )
    assert "openmagi.autopilot" not in resolved.selected_pack_ids


def test_autopilot_snapshot_aggregates_validator_refs() -> None:
    registry = PackRegistry.with_first_party_packs()
    snapshot = AgentRecipeCompiler(registry).compile(
        ProfileResolutionRequest(taskProfile={"taskType": "autopilot"})
    )
    assert "validator:autopilot:review-clean" in snapshot.validator_refs
    assert "checkpoint:autopilot:return-to-plan" in snapshot.checkpoint_refs
    assert "openmagi.agent-methodology" in snapshot.selected_pack_ids
    assert "openmagi.dev-coding" in snapshot.selected_pack_ids
