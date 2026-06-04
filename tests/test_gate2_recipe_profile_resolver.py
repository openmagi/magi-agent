from __future__ import annotations

import json
import subprocess
import sys

import pytest
from pydantic import ValidationError

from magi_agent.recipes.compiler import (
    AgentRecipeCompiler,
    MissionLifecycleMetadata,
    PackRegistry,
    ProfileResolutionRequest,
    ProfileResolver,
    RecipeAttachmentFlags,
    RecipePackManifest,
    RecipeSnapshot,
    build_recipe_pack_digest,
    build_recipe_snapshot_id,
    ResolvedRecipeProfile,
)


def _fixture_value(*parts: str) -> str:
    return "".join(parts)


def test_profile_resolver_merges_layers_deterministically_and_selects_task_packs() -> None:
    registry = PackRegistry.with_first_party_packs()
    resolver = ProfileResolver(registry)

    request = ProfileResolutionRequest(
        userProfile={
            "language": "en",
            "tone": "direct",
            "packs": ["openmagi.office-automation"],
            "customization": {"citationStyle": "inline", "approval": "ask"},
        },
        workspacePolicy={
            "tone": "company",
            "allowedConnectors": ["web.search", "file.read"],
            "customization": {"approval": "required", "maxToolCalls": 10},
        },
        taskProfile={
            "taskType": "research",
            "packs": ["openmagi.dev-coding"],
            "customization": {"maxToolCalls": 4},
        },
        recipePackConfig={
            "packs": {"enable": ["openmagi.missions"], "disable": ["openmagi.office-automation"]},
            "customization": {"freshnessDays": 30},
        },
        runtimeContext={
            "channel": "fixture",
            "currentDate": "2026-05-16",
            "customization": {"riskLevel": "low"},
        },
    )

    resolved = resolver.resolve(request)

    assert resolved.resolved_profile == {
        "language": "en",
        "tone": "company",
        "allowedConnectors": ("web.search", "file.read"),
        "taskType": "research",
        "channel": "fixture",
        "currentDate": "2026-05-16",
        "customization": {
            "citationStyle": "inline",
            "approval": "required",
            "maxToolCalls": 4,
            "freshnessDays": 30,
            "riskLevel": "low",
        },
    }
    assert resolved.selected_pack_ids == (
        "openmagi.context-safety",
        "openmagi.evidence",
        "openmagi.web-acquisition",
        "openmagi.research",
        "openmagi.dev-coding",
        "openmagi.missions",
    )
    assert "openmagi.office-automation" in resolved.opted_out_pack_ids


def test_web_acquisition_recipe_pack_is_default_off_foundation_metadata_only() -> None:
    registry = PackRegistry.with_first_party_packs()
    pack = registry.get("openmagi.web-acquisition")

    assert pack.display_name == "Web Acquisition"
    assert pack.default_enabled is False
    assert pack.hard_safety is False
    assert pack.opt_out_allowed is True
    assert pack.customizable is True
    assert pack.depends_on_pack_ids == ()
    assert {
        "web",
        "web-acquisition",
        "web-qa",
        "office",
        "browser",
        "document-review",
        "legal",
        "accounting",
        "domain-workflow",
    }.issubset(set(pack.task_profile_selectors))
    assert pack.tool_refs == ()
    assert pack.callback_refs == ()
    assert pack.live_tool_refs == ()
    assert pack.live_callback_refs == ()
    assert pack.runner_route_refs == ()
    assert set(pack.attachment_flags.model_dump(by_alias=True).values()) == {False}
    assert any(
        "FunctionTool through ToolHost" in ref for ref in pack.openmagi_boundary_ownership
    )
    assert any(
        "individual long crawl/render/export jobs" in ref
        for ref in pack.openmagi_boundary_ownership
    )
    assert "core" not in " ".join(pack.openmagi_boundary_ownership).lower()


def test_agent_methodology_recipe_pack_is_default_off_selected_metadata_only() -> None:
    registry = PackRegistry.with_first_party_packs()
    pack = registry.get("openmagi.agent-methodology")

    assert pack.display_name == "Agent Methodology"
    assert pack.default_enabled is False
    assert pack.hard_safety is False
    assert pack.opt_out_allowed is True
    assert pack.customizable is True
    assert pack.task_profile_selectors == (
        "methodology",
        "agent-methodology",
        "planning",
        "onboarding",
        "implementation-planning",
    )
    assert {
        "instruction:agent-methodology:using-superpowers",
        "instruction:agent-methodology:brainstorming-design-refinement",
        "instruction:agent-methodology:writing-plans",
        "instruction:agent-methodology:executing-plans",
        "instruction:agent-methodology:finishing-development-branch",
    }.issubset(set(pack.instruction_refs))
    assert {
        "callback:agent-methodology:plan-mode-auto-trigger",
        "callback:agent-methodology:onboarding-needed-check",
    }.issubset(set(pack.callback_refs))
    assert {
        "checkpoint:agent-methodology:plan-mode-auto-trigger",
        "checkpoint:agent-methodology:onboarding",
        "checkpoint:agent-methodology:subagent-parent-context-isolation",
        "checkpoint:agent-methodology:git-worktree-isolation",
    }.issubset(set(pack.checkpoint_refs))
    assert {
        "validator:agent-methodology:tdd-red-green-refactor",
        "validator:agent-methodology:systematic-debugging",
        "validator:agent-methodology:verification-before-completion",
        "validator:agent-methodology:requesting-code-review",
        "validator:agent-methodology:receiving-code-review",
        "validator:agent-methodology:child-envelope-sanitized-upward-only",
    }.issubset(set(pack.validator_refs))
    assert {
        "evidence:agent-methodology:test-run",
        "evidence:agent-methodology:git-diff",
        "evidence:agent-methodology:review-record",
        "evidence:agent-methodology:sanitized-child-envelope",
    }.issubset(set(pack.evidence_refs))
    assert "tool:SpawnAgent" not in pack.live_tool_refs
    assert pack.live_tool_refs == ()
    assert pack.live_callback_refs == ()
    assert pack.runner_route_refs == ()
    assert set(pack.attachment_flags.model_dump(by_alias=True).values()) == {False}
    assert any("ADK callbacks/plugins/evals/session primitives" in ref for ref in pack.openmagi_boundary_ownership)
    assert all("RunnerAdapter" not in ref for ref in pack.openmagi_boundary_ownership)
    assert all("custom orchestration" not in ref.lower() for ref in pack.openmagi_boundary_ownership)

    snapshot = AgentRecipeCompiler(registry).compile(
        ProfileResolutionRequest(taskProfile={"taskType": "methodology"})
    )

    assert snapshot.selected_pack_ids == (
        "openmagi.context-safety",
        "openmagi.evidence",
        "openmagi.agent-methodology",
    )
    assert "callback:agent-methodology:plan-mode-auto-trigger" in snapshot.callback_refs
    assert "checkpoint:agent-methodology:subagent-parent-context-isolation" in snapshot.checkpoint_refs
    assert "validator:agent-methodology:child-envelope-sanitized-upward-only" in snapshot.validator_refs
    assert set(snapshot.attachment_flags.model_dump(by_alias=True).values()) == {False}


def test_superpowers_compat_pack_is_explicit_import_metadata_only() -> None:
    registry = PackRegistry.with_first_party_packs()
    pack = registry.get("openmagi.superpowers-compat")

    assert pack.default_enabled is False
    assert pack.hard_safety is False
    assert pack.opt_out_allowed is True
    assert pack.customizable is True
    assert pack.depends_on_pack_ids == ("openmagi.agent-methodology",)
    assert "instruction:superpowers-compat:skill-import-index" in pack.instruction_refs
    assert "callback:superpowers-compat:slash-command-import-metadata" in pack.callback_refs
    assert "checkpoint:superpowers-compat:no-live-slash-runtime" in pack.checkpoint_refs
    assert "validator:superpowers-compat:prompt-only-import-boundary" in pack.validator_refs
    assert "audit:superpowers-compat:source-skill-index" in pack.audit_refs
    assert pack.live_tool_refs == ()
    assert pack.live_callback_refs == ()
    assert pack.runner_route_refs == ()
    assert set(pack.attachment_flags.model_dump(by_alias=True).values()) == {False}

    snapshot = AgentRecipeCompiler(registry).compile(
        ProfileResolutionRequest(
            recipePackConfig={"packs": {"enable": ["openmagi.superpowers-compat"]}}
        )
    )

    assert snapshot.selected_pack_ids == (
        "openmagi.context-safety",
        "openmagi.evidence",
        "openmagi.agent-methodology",
        "openmagi.superpowers-compat",
    )
    assert "checkpoint:superpowers-compat:no-live-slash-runtime" in snapshot.checkpoint_refs
    assert set(snapshot.attachment_flags.model_dump(by_alias=True).values()) == {False}


def test_research_recipe_depends_on_web_acquisition_before_fact_grounding_metadata() -> None:
    registry = PackRegistry.with_first_party_packs()
    research = registry.get("openmagi.research")

    assert research.depends_on_pack_ids == ("openmagi.web-acquisition",)
    assert "tool:web.search" not in research.tool_refs
    assert "tool:web.fetch" not in research.tool_refs
    assert "validator:research:citation-support" in research.validator_refs
    assert "validator:research:fact-grounding" in research.validator_refs
    assert "evidence:inspected-source" in research.evidence_refs

    snapshot = AgentRecipeCompiler(registry).compile(
        ProfileResolutionRequest(taskProfile={"taskType": "research"})
    )

    assert snapshot.selected_pack_ids == (
        "openmagi.context-safety",
        "openmagi.evidence",
        "openmagi.web-acquisition",
        "openmagi.research",
    )
    assert snapshot.evidence_refs.index(
        "evidence:web-acquisition:source-ledger-input"
    ) < snapshot.evidence_refs.index("evidence:inspected-source")
    assert "audit:web-acquisition:source-ledger-inputs" in snapshot.audit_refs
    assert "validator:research:citation-support" in snapshot.validator_refs
    assert "validator:research:fact-grounding" in snapshot.validator_refs
    assert set(snapshot.attachment_flags.model_dump(by_alias=True).values()) == {False}


def test_research_recipe_is_not_selected_when_required_web_acquisition_is_disabled() -> None:
    snapshot = AgentRecipeCompiler(PackRegistry.with_first_party_packs()).compile(
        ProfileResolutionRequest(
            taskProfile={"taskType": "research"},
            recipePackConfig={"packs": {"disable": ["openmagi.web-acquisition"]}},
        )
    )

    assert snapshot.selected_pack_ids == (
        "openmagi.context-safety",
        "openmagi.evidence",
    )
    assert snapshot.opted_out_pack_ids == ("openmagi.web-acquisition",)
    assert "openmagi.research" not in snapshot.selected_pack_ids
    assert "validator:research:citation-support" not in snapshot.validator_refs
    assert "validator:research:fact-grounding" not in snapshot.validator_refs
    assert "validator:research:evidence-checks" not in snapshot.validator_refs


def test_pack_registry_catalog_is_metadata_only_and_has_expected_first_party_packs() -> None:
    registry = PackRegistry.with_first_party_packs()

    assert registry.pack_ids == (
        "openmagi.context-safety",
        "openmagi.evidence",
        "openmagi.agent-methodology",
        "openmagi.superpowers-compat",
        "openmagi.web-acquisition",
        "openmagi.research",
        "openmagi.research-scout",
        "openmagi.dev-coding",
        "openmagi.missions",
        "openmagi.scheduled-work",
        "openmagi.memory-agentmemory",
        "openmagi.channel-delivery",
        "openmagi.office-automation",
        "openmagi.artifact-delivery",
        "openmagi.spreadsheet-automation",
        "openmagi.browser-automation",
        "openmagi.document-review",
        "openmagi.lightweight-scripting",
        "openmagi.learning-usage",
    )

    for pack_id in registry.pack_ids:
        pack = registry.get(pack_id)
        assert isinstance(pack, RecipePackManifest)
        assert set(pack.attachment_flags.model_dump(by_alias=True).values()) == {False}
        assert pack.live_tool_refs == ()
        assert pack.live_callback_refs == ()
        assert pack.runner_route_refs == ()


def test_office_subpacks_are_represented_as_configurable_metadata_only_manifests() -> None:
    registry = PackRegistry.with_first_party_packs()

    for pack_id in (
        "openmagi.spreadsheet-automation",
        "openmagi.browser-automation",
        "openmagi.document-review",
        "openmagi.lightweight-scripting",
    ):
        pack = registry.get(pack_id)
        assert pack.hard_safety is False
        assert pack.default_enabled is False
        assert pack.opt_out_allowed is True
        assert pack.customizable is True
        assert set(pack.attachment_flags.model_dump(by_alias=True).values()) == {False}
        assert pack.live_tool_refs == ()
        assert pack.live_callback_refs == ()
        assert pack.runner_route_refs == ()


def test_agentmemory_recipe_pack_is_metadata_only_and_explicitly_selected() -> None:
    registry = PackRegistry.with_first_party_packs()
    pack = registry.get("openmagi.memory-agentmemory")

    assert pack.default_enabled is False
    assert pack.hard_safety is False
    assert pack.opt_out_allowed is True
    assert pack.customizable is True
    assert "tool:AgentMemorySearch" in pack.tool_refs
    assert "tool:AgentMemoryRemember" in pack.tool_refs
    assert "callback:agentmemory.recall" in pack.callback_refs
    assert "callback:agentmemory.observe" in pack.callback_refs
    assert pack.validator_refs == ("verifier:agentmemory-provider-boundary",)
    assert pack.instruction_refs == ()
    assert pack.approval_gate_refs == ()
    assert pack.evidence_refs == ()
    assert pack.audit_refs == ()
    assert set(pack.attachment_flags.model_dump(by_alias=True).values()) == {False}
    assert pack.live_tool_refs == ()
    assert pack.live_callback_refs == ()
    assert pack.runner_route_refs == ()

    snapshot = AgentRecipeCompiler(registry).compile(
        ProfileResolutionRequest(
            taskProfile={"taskType": "memory-provider-eval"},
            recipePackConfig={"packs": {"enable": ["openmagi.memory-agentmemory"]}},
            runtimeContext={"channel": "fixture"},
        )
    )

    assert "openmagi.memory-agentmemory" in snapshot.selected_pack_ids
    assert "tool:AgentMemorySearch" in snapshot.tool_refs
    assert "callback:agentmemory.recall" in snapshot.callback_refs
    assert "verifier:agentmemory-provider-boundary" in snapshot.validator_refs
    assert set(snapshot.attachment_flags.model_dump(by_alias=True).values()) == {False}


def test_agent_recipe_compiler_emits_immutable_snapshot_with_aggregated_refs() -> None:
    compiler = AgentRecipeCompiler(PackRegistry.with_first_party_packs())
    snapshot = compiler.compile(
        ProfileResolutionRequest(
            task_profile={"taskType": "coding", "packs": ["openmagi.office-automation"]},
            recipe_pack_config={"packs": {"disable": ["openmagi.office-automation"]}},
            runtime_context={"channel": "fixture"},
        )
    )

    assert snapshot.snapshot_id.startswith("recipe-snapshot:")
    assert snapshot.selected_pack_ids == (
        "openmagi.context-safety",
        "openmagi.evidence",
        "openmagi.dev-coding",
    )
    assert "instruction:context-safety:system" in snapshot.instruction_refs
    assert "validator:dev-coding:tdd-verification" in snapshot.validator_refs
    assert "evidence:git-diff" in snapshot.evidence_refs
    assert "audit:recipe-profile-resolution" in snapshot.audit_refs
    assert "ADK Agent owns execution shape" in snapshot.adk_primitive_ownership
    assert "OpenMagi ProfileResolver owns deterministic metadata merge" in snapshot.openmagi_boundary_ownership
    assert set(snapshot.attachment_flags.model_dump(by_alias=True).values()) == {False}

    with pytest.raises(ValidationError):
        snapshot.model_copy(update={"trafficAttached": True})
    with pytest.raises(ValidationError):
        snapshot.model_copy(update={"selectedPackIds": ("openmagi.research",)})
    with pytest.raises(ValidationError):
        snapshot.instruction_refs += ("instruction:forged",)


def test_multi_recipe_task_intents_compose_research_coding_and_artifact_delivery_controls() -> None:
    compiler = AgentRecipeCompiler(PackRegistry.with_first_party_packs())

    snapshot = compiler.compile(
        ProfileResolutionRequest(
            taskProfile={
                "taskType": "research",
                "taskTypes": ["coding"],
                "taskIntents": ["office-automation", "artifact-delivery"],
            },
            runtimeContext={"channel": "fixture"},
        )
    )

    assert snapshot.selected_pack_ids == (
        "openmagi.context-safety",
        "openmagi.evidence",
        "openmagi.web-acquisition",
        "openmagi.research",
        "openmagi.dev-coding",
        "openmagi.office-automation",
        "openmagi.artifact-delivery",
    )
    assert {
        "instruction:research:source-policy",
        "instruction:dev-coding:tdd",
        "instruction:office-automation:preview-then-approve",
        "instruction:artifact-delivery:sanitized-delivery-preview",
    }.issubset(snapshot.instruction_refs)
    assert {
        "tool:file.read",
        "tool:test.run",
        "tool:artifact.prepare-delivery",
        "tool:file.delivery-plan",
    }.issubset(snapshot.tool_refs)
    assert {
        "callback:research:source-capture",
        "callback:dev-coding:diff-capture",
        "callback:office-automation:preview-capture",
        "callback:artifact-delivery:delivery-manifest-capture",
    }.issubset(snapshot.callback_refs)
    assert {
        "validator:context-safety:public-redaction",
        "validator:research:fact-grounding",
        "validator:dev-coding:tdd-verification",
        "validator:office-automation:preview-before-write",
        "validator:artifact-delivery:no-raw-path-leakage",
    }.issubset(snapshot.validator_refs)
    assert {
        "approval:research:external-source-use",
        "approval:dev-coding:workspace-mutation",
        "approval:office-automation:write-or-send",
        "approval:artifact-delivery:channel-send",
    }.issubset(snapshot.approval_gate_refs)
    assert {
        "checkpoint:artifact-delivery:sanitized-artifact-ref",
        "checkpoint:artifact-delivery:delivery-ack-metadata",
    }.issubset(snapshot.checkpoint_refs)
    assert {
        "evidence:web-acquisition:source-ledger-input",
        "evidence:inspected-source",
        "evidence:git-diff",
        "evidence:test-run",
        "evidence:office-preview",
        "evidence:artifact-delivery-ref",
    }.issubset(snapshot.evidence_refs)
    assert {
        "audit:research-source-ledger",
        "audit:dev-coding-verification",
        "audit:office-automation-action-plan",
        "audit:artifact-delivery-manifest",
    }.issubset(snapshot.audit_refs)
    assert snapshot.composition_policy_metadata.validator_merge == "all_of"
    assert snapshot.composition_policy_metadata.approval_gate_merge == "union"
    assert snapshot.composition_policy_metadata.evidence_merge == "union"
    assert snapshot.composition_policy_metadata.audit_merge == "union"
    assert set(snapshot.attachment_flags.model_dump(by_alias=True).values()) == {False}
    assert "liveToolRefs" not in snapshot.model_dump(by_alias=True)


def test_legacy_task_type_precedence_does_not_broaden_from_lower_layers() -> None:
    snapshot = AgentRecipeCompiler(PackRegistry.with_first_party_packs()).compile(
        ProfileResolutionRequest(
            userProfile={"taskType": "office"},
            workspacePolicy={"taskTypes": ["research"]},
            taskProfile={"taskType": "coding"},
        )
    )

    assert snapshot.selected_pack_ids == (
        "openmagi.context-safety",
        "openmagi.evidence",
        "openmagi.dev-coding",
    )
    assert "tool:test.run" in snapshot.tool_refs
    assert "tool:spreadsheet.read" not in snapshot.tool_refs
    assert "tool:browser.inspect" not in snapshot.tool_refs
    assert "openmagi.office-automation" not in snapshot.selected_pack_ids
    assert "openmagi.web-acquisition" not in snapshot.selected_pack_ids
    assert "openmagi.research" not in snapshot.selected_pack_ids


def test_multi_recipe_plural_intents_respect_disabled_dependencies() -> None:
    snapshot = AgentRecipeCompiler(PackRegistry.with_first_party_packs()).compile(
        ProfileResolutionRequest(
            taskProfile={
                "taskTypes": ["research"],
                "taskIntents": ["artifact-delivery"],
            },
            recipePackConfig={
                "packs": {
                    "disable": [
                        "openmagi.web-acquisition",
                        "openmagi.office-automation",
                    ]
                }
            },
        )
    )

    assert snapshot.selected_pack_ids == (
        "openmagi.context-safety",
        "openmagi.evidence",
    )
    assert "openmagi.research" not in snapshot.selected_pack_ids
    assert "openmagi.artifact-delivery" not in snapshot.selected_pack_ids
    assert snapshot.opted_out_pack_ids == (
        "openmagi.web-acquisition",
        "openmagi.office-automation",
    )
    assert "validator:research:fact-grounding" not in snapshot.validator_refs
    assert "approval:artifact-delivery:channel-send" not in snapshot.approval_gate_refs


def test_restrictive_composition_policy_blocks_silent_last_write_wins_conflicts() -> None:
    snapshot = AgentRecipeCompiler(PackRegistry.with_first_party_packs()).compile(
        ProfileResolutionRequest(
            userProfile={
                "memoryMode": "normal",
                "sideEffectPosture": "allow",
                "budgetCap": 40,
                "budget": {"max_tool_calls": 6},
                "providers": {
                    "web": "search-a",
                    "/Users/kevin/private/raw.txt": "private-provider-a",
                    ".env": "env-provider-a",
                    _fixture_value("openai", "_api", "_key"): _fixture_value(
                        "sec",
                        "ret-provider-a",
                    ),
                    _fixture_value("sec", "rets.local"): _fixture_value(
                        "local-sec",
                        "ret-provider-a",
                    ),
                },
            },
            workspacePolicy={
                "memoryMode": "read_only",
                "sideEffectPosture": "approval_required",
                "budgetCap": 20,
                "customization": {"maxToolCalls": 8, "budget_cap": 3},
                "providerMap": {
                    "web": "search-b",
                    "/Users/kevin/private/raw.txt": "private-provider-b",
                    ".env": "env-provider-b",
                    _fixture_value("openai", "_api", "_key"): _fixture_value(
                        "sec",
                        "ret-provider-b",
                    ),
                    _fixture_value("sec", "rets.local"): _fixture_value(
                        "local-sec",
                        "ret-provider-b",
                    ),
                },
                "providers": {"browser": "browser-a"},
            },
            taskProfile={
                "taskTypes": ["research", "coding"],
                "memoryMode": "incognito",
                "providers": {"web": "search-b"},
                "customization": {"maxToolCalls": 4},
            },
            runtimeContext={
                "sideEffectPosture": "deny",
                "budgetCap": 10,
                "toolProviders": {"file.write": "workspace-a", "browser.open": "browser-a"},
            },
        )
    )

    policy = snapshot.composition_policy_metadata

    assert policy.validator_merge == "all_of"
    assert policy.approval_gate_merge == "union"
    assert policy.evidence_merge == "union"
    assert policy.audit_merge == "union"
    assert policy.budget_cap == 3
    assert policy.memory_mode == "incognito"
    assert policy.side_effect_posture == "deny"
    assert policy.provider_tool_conflict_policy == "blocked_or_requires_clarification"
    assert policy.blocked is True
    assert policy.requires_clarification is True
    assert "provider.web" in policy.conflict_refs
    assert any(ref.startswith("provider.ref_") for ref in policy.conflict_refs)
    serialized_conflicts = " ".join(policy.conflict_refs)
    assert "/Users/kevin/private/raw.txt" not in serialized_conflicts
    assert ".env" not in serialized_conflicts
    assert "openai_api_key" not in serialized_conflicts
    assert "secrets.local" not in serialized_conflicts
    assert "private-provider" not in serialized_conflicts
    assert "secret-provider" not in serialized_conflicts
    assert "composition-policy:provider-tool-conflict" in snapshot.validator_set_metadata
    assert snapshot.tool_refs == ()
    assert snapshot.callback_refs == ()
    assert "approval:composition-policy:requires-clarification" in snapshot.approval_gate_refs
    assert (
        "checkpoint:composition-policy:provider-tool-conflict-blocked"
        in snapshot.checkpoint_refs
    )
    assert "audit:composition-policy-provider-tool-conflict" in snapshot.audit_refs


def test_provider_tool_conflict_aliases_are_canonicalized_before_blocking() -> None:
    snapshot = AgentRecipeCompiler(PackRegistry.with_first_party_packs()).compile(
        ProfileResolutionRequest(
            userProfile={
                "providers": {"web": "search-a"},
                "toolProviders": {"file.write": "workspace-a"},
            },
            workspacePolicy={
                "provider_map": {"web": "search-b"},
                "tool_providers": {"file.write": "workspace-b"},
            },
        )
    )

    policy = snapshot.composition_policy_metadata

    assert policy.blocked is True
    assert policy.requires_clarification is True
    assert "provider.web" in policy.conflict_refs
    assert "tool_provider.file.write" in policy.conflict_refs


def test_recipe_regression_web_acquisition_with_citation_enforcement() -> None:
    snapshot = AgentRecipeCompiler(PackRegistry.with_first_party_packs()).compile(
        ProfileResolutionRequest(taskProfile={"taskTypes": ["web-acquisition", "research"]})
    )

    assert snapshot.selected_pack_ids == (
        "openmagi.context-safety",
        "openmagi.evidence",
        "openmagi.web-acquisition",
        "openmagi.research",
    )
    assert snapshot.evidence_refs.index(
        "evidence:web-acquisition:source-ledger-input"
    ) < snapshot.evidence_refs.index("evidence:inspected-source")
    assert {
        "verifier:web-acquisition:provider-boundary",
        "validator:research:citation-support",
        "validator:research:fact-grounding",
    }.issubset(snapshot.validator_refs)
    assert "approval:web-acquisition:provider-opt-in" in snapshot.approval_gate_refs
    assert "approval:research:external-source-use" in snapshot.approval_gate_refs
    assert set(snapshot.attachment_flags.model_dump(by_alias=True).values()) == {False}


def test_recipe_regression_coding_with_workspace_policy_controls() -> None:
    snapshot = AgentRecipeCompiler(PackRegistry.with_first_party_packs()).compile(
        ProfileResolutionRequest(
            taskProfile={"taskTypes": ["coding"]},
            workspacePolicy={
                "sideEffectPosture": "approval_required",
                "toolProviders": {"file.write": "workspace-a"},
            },
        )
    )

    assert snapshot.selected_pack_ids == (
        "openmagi.context-safety",
        "openmagi.evidence",
        "openmagi.dev-coding",
    )
    assert "tool:file.read" in snapshot.tool_refs
    assert "tool:test.run" in snapshot.tool_refs
    assert "approval:dev-coding:workspace-mutation" in snapshot.approval_gate_refs
    assert "validator:dev-coding:tdd-verification" in snapshot.validator_refs
    assert snapshot.composition_policy_metadata.side_effect_posture == "approval_required"
    assert snapshot.composition_policy_metadata.blocked is False
    assert set(snapshot.attachment_flags.model_dump(by_alias=True).values()) == {False}


def test_recipe_regression_methodology_superpowers_and_implementation_workflow() -> None:
    snapshot = AgentRecipeCompiler(PackRegistry.with_first_party_packs()).compile(
        ProfileResolutionRequest(
            taskProfile={
                "taskTypes": ["methodology", "coding"],
                "taskIntents": ["implementation-planning"],
            },
            recipePackConfig={"packs": {"enable": ["openmagi.superpowers-compat"]}},
        )
    )

    assert snapshot.selected_pack_ids == (
        "openmagi.context-safety",
        "openmagi.evidence",
        "openmagi.agent-methodology",
        "openmagi.dev-coding",
        "openmagi.superpowers-compat",
    )
    assert "instruction:agent-methodology:executing-plans" in snapshot.instruction_refs
    assert "instruction:agent-methodology:tdd-red-green-refactor" in snapshot.instruction_refs
    assert "callback:superpowers-compat:slash-command-import-metadata" in snapshot.callback_refs
    assert "checkpoint:agent-methodology:subagent-parent-context-isolation" in snapshot.checkpoint_refs
    assert "validator:agent-methodology:child-envelope-sanitized-upward-only" in snapshot.validator_refs
    assert "approval:agent-methodology:plan-execution" in snapshot.approval_gate_refs
    assert "approval:dev-coding:workspace-mutation" in snapshot.approval_gate_refs
    assert set(snapshot.attachment_flags.model_dump(by_alias=True).values()) == {False}


def test_provider_tool_conflicts_are_deterministic_across_reordered_layers() -> None:
    compiler = AgentRecipeCompiler(PackRegistry.with_first_party_packs())
    first = compiler.compile(
        ProfileResolutionRequest(
            userProfile={"providers": {"web": "a", "browser": "one"}},
            workspacePolicy={"providerMap": {"browser": "two", "web": "b"}},
            taskProfile={"toolProviders": {"file.write": "workspace-a"}},
            runtimeContext={"tool_providers": {"file.write": "workspace-b"}},
        )
    )
    second = compiler.compile(
        ProfileResolutionRequest(
            userProfile={"providers": {"browser": "one", "web": "a"}},
            workspacePolicy={"providerMap": {"web": "b", "browser": "two"}},
            taskProfile={"toolProviders": {"file.write": "workspace-a"}},
            runtimeContext={"tool_providers": {"file.write": "workspace-b"}},
        )
    )

    assert first.composition_policy_metadata.conflict_refs == second.composition_policy_metadata.conflict_refs
    assert first.composition_policy_metadata.conflict_refs == (
        "provider.browser",
        "provider.web",
        "tool_provider.file.write",
    )
    assert first.composition_policy_metadata.blocked is True
    assert second.composition_policy_metadata.requires_clarification is True
    assert first.tool_refs == second.tool_refs == ()
    assert first.callback_refs == second.callback_refs == ()


def test_resolved_profile_mappings_are_deeply_immutable_but_serialize_as_dicts() -> None:
    resolver = ProfileResolver(PackRegistry.with_first_party_packs())

    resolved = resolver.resolve(
        ProfileResolutionRequest(
            userProfile={
                "customization": {
                    "approval": "ask",
                    "nested": {"risk": "low"},
                },
            },
            runtimeContext={"channel": "fixture"},
        )
    )

    with pytest.raises(TypeError):
        resolved.resolved_profile["channel"] = "live"
    with pytest.raises(TypeError):
        resolved.resolved_profile["customization"]["approval"] = "silent"  # type: ignore[index]
    with pytest.raises(TypeError):
        resolved.resolved_profile["customization"]["nested"]["risk"] = "high"  # type: ignore[index]

    assert resolved.model_dump(by_alias=True)["resolvedProfile"] == {
        "channel": "fixture",
        "customization": {
            "approval": "ask",
            "nested": {"risk": "low"},
        },
    }


def test_resolved_profile_unordered_sets_are_canonicalized_to_sorted_tuples() -> None:
    resolver = ProfileResolver(PackRegistry.with_first_party_packs())

    resolved = resolver.resolve(
        ProfileResolutionRequest(
            userProfile={
                "allowedConnectors": {"web.search", "file.read"},
                "customization": {"labels": {"beta", "alpha"}},
            },
        )
    )

    assert resolved.resolved_profile["allowedConnectors"] == (
        "file.read",
        "web.search",
    )
    assert resolved.resolved_profile["customization"]["labels"] == ("alpha", "beta")  # type: ignore[index]
    assert resolved.model_dump(by_alias=True)["resolvedProfile"] == {
        "allowedConnectors": ("file.read", "web.search"),
        "customization": {"labels": ("alpha", "beta")},
    }


def test_snapshot_resolved_profile_mappings_are_deeply_immutable_but_serialize_as_dicts() -> None:
    compiler = AgentRecipeCompiler(PackRegistry.with_first_party_packs())

    snapshot = compiler.compile(
        ProfileResolutionRequest(
            taskProfile={
                "taskType": "coding",
                "customization": {"approval": "required"},
            },
            runtimeContext={"customization": {"risk": {"level": "low"}}},
        )
    )

    with pytest.raises(TypeError):
        snapshot.resolved_profile["taskType"] = "research"
    with pytest.raises(TypeError):
        snapshot.resolved_profile["customization"]["approval"] = "silent"  # type: ignore[index]
    with pytest.raises(TypeError):
        snapshot.resolved_profile["customization"]["risk"]["level"] = "high"  # type: ignore[index]

    dumped = snapshot.model_dump(by_alias=True)["resolvedProfile"]
    assert dumped["customization"] == {
        "approval": "required",
        "risk": {"level": "low"},
    }


def test_pack_selection_from_unordered_sets_is_sorted_and_deterministic() -> None:
    compiler = AgentRecipeCompiler(PackRegistry.with_first_party_packs())

    snapshot = compiler.compile(
        ProfileResolutionRequest(
            recipePackConfig={
                "packs": {
                    "enable": {"openmagi.missions", "openmagi.research"},
                    "disable": {"openmagi.research"},
                }
            },
        )
    )

    assert snapshot.selected_pack_ids == (
        "openmagi.context-safety",
        "openmagi.evidence",
        "openmagi.missions",
    )
    assert snapshot.opted_out_pack_ids == ("openmagi.research",)


def test_hard_safety_is_non_opt_out_but_first_party_workflow_packs_are_opt_out() -> None:
    compiler = AgentRecipeCompiler(PackRegistry.with_first_party_packs())

    snapshot = compiler.compile(
        ProfileResolutionRequest(
            taskProfile={"taskType": "research"},
            recipePackConfig={
                "packs": {
                    "disable": [
                        "openmagi.context-safety",
                        "openmagi.evidence",
                        "openmagi.research",
                    ]
                }
            },
        )
    )

    assert snapshot.selected_pack_ids == (
        "openmagi.context-safety",
        "openmagi.evidence",
    )
    assert snapshot.non_opt_out_pack_ids == (
        "openmagi.context-safety",
        "openmagi.evidence",
    )
    assert snapshot.opted_out_pack_ids == ("openmagi.research",)
    assert "validator:context-safety:public-redaction" in snapshot.validator_refs
    assert "validator:research:citation-support" not in snapshot.validator_refs


def test_hard_safety_pack_manifests_are_non_opt_out_and_non_customizable() -> None:
    with pytest.raises(ValidationError):
        RecipePackManifest(
            packId="openmagi.invalid-hard-safety",
            displayName="Invalid Hard Safety",
            description="Hard safety cannot be user-customizable.",
            hardSafety=True,
            optOutAllowed=False,
            customizable=True,
        )

    workflow_pack = RecipePackManifest(
        packId="openmagi.workflow",
        displayName="Workflow",
        description="First-party workflow packs remain configurable.",
        taskProfileSelectors=("workflow",),
    )

    assert workflow_pack.hard_safety is False
    assert workflow_pack.opt_out_allowed is True
    assert workflow_pack.customizable is True


def test_non_hard_recipe_pack_manifests_remain_opt_out_and_customizable() -> None:
    with pytest.raises(ValidationError):
        RecipePackManifest(
            packId="openmagi.invalid-workflow-opt-out",
            displayName="Invalid Workflow Opt Out",
            description="Non-hard workflow packs remain user-configurable.",
            optOutAllowed=False,
        )

    with pytest.raises(ValidationError):
        RecipePackManifest(
            packId="openmagi.invalid-workflow-customization",
            displayName="Invalid Workflow Customization",
            description="Non-hard workflow packs remain customizable.",
            customizable=False,
        )


@pytest.mark.parametrize(
    ("task_type", "expected_pack_id"),
    (
        ("research", "openmagi.research"),
        ("coding", "openmagi.dev-coding"),
        ("mission", "openmagi.missions"),
        ("office", "openmagi.office-automation"),
        ("spreadsheet", "openmagi.spreadsheet-automation"),
        ("browser", "openmagi.browser-automation"),
        ("document-review", "openmagi.document-review"),
        ("lightweight-scripting", "openmagi.lightweight-scripting"),
    ),
)
def test_recipe_packs_are_selected_by_profile_without_runner_adapter_branches(
    task_type: str,
    expected_pack_id: str,
) -> None:
    compiler = AgentRecipeCompiler(PackRegistry.with_first_party_packs())

    snapshot = compiler.compile(ProfileResolutionRequest(taskProfile={"taskType": task_type}))

    assert expected_pack_id in snapshot.selected_pack_ids
    assert all("RunnerAdapter" not in ref for ref in snapshot.openmagi_boundary_ownership)
    assert "ADK Runner owns invocation" in snapshot.adk_primitive_ownership


def test_mission_lifecycle_metadata_is_not_a_long_running_function_tool() -> None:
    missions = PackRegistry.with_first_party_packs().get("openmagi.missions")

    assert missions.mission_lifecycle is not None
    assert missions.mission_lifecycle.enabled is False
    assert missions.mission_lifecycle.lifecycle_refs == (
        "mission-objective",
        "progress-checkpoint",
        "cancel-retry-resume-control",
        "completion-criteria",
    )
    assert missions.mission_lifecycle.mission_uses_long_running_function_tool is False
    assert "LongRunningFunctionTool" not in missions.tool_refs

    with pytest.raises(ValidationError):
        missions.mission_lifecycle.model_copy(
            update={"missionUsesLongRunningFunctionTool": True}
        )


@pytest.mark.parametrize(
    "flag_name",
    (
        "trafficAttached",
        "executionAttached",
        "routeAttached",
        "runnerAttached",
        "liveToolsAttached",
        "liveCallbacksAttached",
        "canaryAttached",
        "productionAttached",
        "blockModeEnabledForLiveTraffic",
    ),
)
def test_attachment_flags_cannot_be_enabled_by_constructor_or_model_copy(flag_name: str) -> None:
    with pytest.raises(ValidationError):
        RecipeAttachmentFlags(**{flag_name: True})

    flags = RecipeAttachmentFlags()
    with pytest.raises(ValidationError):
        flags.model_copy(update={flag_name: True})


def test_attachment_flags_model_construct_cannot_serialize_forbidden_true_state() -> None:
    flags = RecipeAttachmentFlags.model_construct(traffic_attached=True)

    assert flags.traffic_attached is False
    assert set(flags.model_dump(by_alias=True).values()) == {False}


def test_raw_set_attachment_flags_cannot_serialize_forbidden_true_state() -> None:
    flags = RecipeAttachmentFlags()

    object.__setattr__(flags, "traffic_attached", True)

    assert flags.model_dump(by_alias=True)["trafficAttached"] is False
    assert set(flags.model_dump(by_alias=True).values()) == {False}


def test_constructed_attachment_flags_are_canonicalized_inside_pack_and_snapshot() -> None:
    constructed_flags = RecipeAttachmentFlags.model_construct(traffic_attached=True)
    pack = RecipePackManifest(
        packId="openmagi.constructed-flags",
        displayName="Constructed Flags",
        description="Constructed flags must not leak attachment state.",
        attachmentFlags=constructed_flags,
    )
    snapshot = RecipeSnapshot(
        snapshotId="recipe-snapshot:e3b0c44298fc1c14",
        resolvedProfile={},
        selectedPackIds=(),
        attachmentFlags=constructed_flags,
    )

    assert set(pack.attachment_flags.model_dump(by_alias=True).values()) == {False}
    assert set(snapshot.attachment_flags.model_dump(by_alias=True).values()) == {False}


def test_raw_set_pack_manifest_live_refs_and_attachment_flags_do_not_serialize() -> None:
    forged_flags = RecipeAttachmentFlags()
    object.__setattr__(forged_flags, "traffic_attached", True)
    pack = RecipePackManifest(
        packId="openmagi.raw-set-pack",
        displayName="Raw Set Pack",
        description="Raw attribute replacement must not leak live state.",
    )

    object.__setattr__(pack, "live_tool_refs", ("live:tool",))
    object.__setattr__(pack, "live_callback_refs", ("live:callback",))
    object.__setattr__(pack, "runner_route_refs", ("route:/live",))
    object.__setattr__(pack, "attachment_flags", forged_flags)

    dumped = pack.model_dump(by_alias=True)
    assert dumped["liveToolRefs"] == ()
    assert dumped["liveCallbackRefs"] == ()
    assert dumped["runnerRouteRefs"] == ()
    assert dumped["attachmentFlags"] == {
        "trafficAttached": False,
        "executionAttached": False,
        "routeAttached": False,
        "runnerAttached": False,
        "liveToolsAttached": False,
        "liveCallbacksAttached": False,
        "canaryAttached": False,
        "productionAttached": False,
        "blockModeEnabledForLiveTraffic": False,
    }


def test_raw_set_snapshot_attachment_flags_do_not_serialize() -> None:
    forged_flags = RecipeAttachmentFlags()
    object.__setattr__(forged_flags, "traffic_attached", True)
    snapshot = RecipeSnapshot(
        snapshotId="recipe-snapshot:e3b0c44298fc1c14",
        resolvedProfile={},
        selectedPackIds=(),
    )

    object.__setattr__(snapshot, "attachment_flags", forged_flags)

    dumped = snapshot.model_dump(by_alias=True)
    assert dumped["attachmentFlags"]["trafficAttached"] is False
    assert set(dumped["attachmentFlags"].values()) == {False}


def test_pack_and_snapshot_model_construct_revalidate_attachment_flags() -> None:
    pack = RecipePackManifest.model_construct(
        pack_id="openmagi.constructed-pack-flags",
        display_name="Constructed Pack Flags",
        description="Constructed pack manifests still validate attachment flags.",
        attachment_flags={"trafficAttached": True},
    )
    snapshot = RecipeSnapshot.model_construct(
        snapshot_id="recipe-snapshot:e3b0c44298fc1c14",
        resolved_profile={},
        selected_pack_ids=(),
        attachment_flags={"trafficAttached": True},
    )

    assert set(pack.attachment_flags.model_dump(by_alias=True).values()) == {False}
    assert set(snapshot.attachment_flags.model_dump(by_alias=True).values()) == {False}


def test_mission_lifecycle_model_construct_cannot_serialize_long_running_tool_state() -> None:
    lifecycle = MissionLifecycleMetadata.model_construct(
        mission_uses_long_running_function_tool=True
    )

    assert lifecycle.mission_uses_long_running_function_tool is False
    assert lifecycle.model_dump(by_alias=True)["missionUsesLongRunningFunctionTool"] is False


def test_constructed_mission_lifecycle_is_canonicalized_inside_pack_and_snapshot() -> None:
    constructed_lifecycle = MissionLifecycleMetadata.model_construct(
        enabled=True,
        mission_uses_long_running_function_tool=True,
    )
    pack = RecipePackManifest(
        packId="openmagi.constructed-mission",
        displayName="Constructed Mission",
        description="Constructed mission lifecycle must remain metadata-only.",
        missionLifecycle=constructed_lifecycle,
    )
    snapshot = RecipeSnapshot(
        snapshotId="recipe-snapshot:e3b0c44298fc1c14",
        resolvedProfile={},
        selectedPackIds=(),
        missionLifecycle=constructed_lifecycle,
    )

    assert pack.mission_lifecycle is not None
    assert pack.mission_lifecycle.mission_uses_long_running_function_tool is False
    assert snapshot.mission_lifecycle is not None
    assert snapshot.mission_lifecycle.mission_uses_long_running_function_tool is False


def test_pack_and_snapshot_model_construct_revalidate_mission_lifecycle() -> None:
    pack = RecipePackManifest.model_construct(
        pack_id="openmagi.constructed-pack-mission",
        display_name="Constructed Pack Mission",
        description="Constructed pack manifests still validate mission lifecycle.",
        mission_lifecycle={"missionUsesLongRunningFunctionTool": True},
    )
    snapshot = RecipeSnapshot.model_construct(
        snapshot_id="recipe-snapshot:e3b0c44298fc1c14",
        resolved_profile={},
        selected_pack_ids=(),
        mission_lifecycle={"missionUsesLongRunningFunctionTool": True},
    )

    assert pack.mission_lifecycle is not None
    assert pack.mission_lifecycle.mission_uses_long_running_function_tool is False
    assert snapshot.mission_lifecycle is not None
    assert snapshot.mission_lifecycle.mission_uses_long_running_function_tool is False


def test_importing_recipe_compiler_stays_runtime_route_and_adk_runner_free() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

module = importlib.import_module("magi_agent.recipes.compiler")
assert hasattr(module, "AgentRecipeCompiler")

forbidden_prefixes = (
    "google.adk",
    "magi_agent.adk_bridge.runner_adapter",
    "magi_agent.adk_bridge.local_runner",
    "magi_agent.adk_bridge.tool_adapter",
    "magi_agent.runtime",
    "magi_agent.routing",
    "magi_agent.transport",
    "magi_agent.channels",
    "magi_agent.workspace",
)
loaded = [
    module_name
    for module_name in sys.modules
    if any(
        module_name == prefix or module_name.startswith(f"{prefix}.")
        for prefix in forbidden_prefixes
    )
]
if loaded:
    raise AssertionError(f"recipe compiler import loaded forbidden modules: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_multi_recipe_composition_import_boundary_stays_metadata_only() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import sys

from magi_agent.recipes.compiler import (
    AgentRecipeCompiler,
    PackRegistry,
    ProfileResolutionRequest,
)

snapshot = AgentRecipeCompiler(PackRegistry.with_first_party_packs()).compile(
    ProfileResolutionRequest(
        taskProfile={
            "taskTypes": ["research", "coding"],
            "taskIntents": ["office-automation", "artifact-delivery"],
        }
    )
)
assert "openmagi.artifact-delivery" in snapshot.selected_pack_ids

forbidden_prefixes = (
    "google.adk",
    "magi_agent.adk_bridge",
    "magi_agent.channels",
    "magi_agent.tools.dispatcher",
    "magi_agent.memory",
    "magi_agent.runtime",
    "magi_agent.routing",
    "magi_agent.transport",
    "magi_agent.workspace",
)
loaded = [
    module_name
    for module_name in sys.modules
    if any(
        module_name == prefix or module_name.startswith(f"{prefix}.")
        for prefix in forbidden_prefixes
    )
]
if loaded:
    raise AssertionError(f"recipe composition loaded forbidden live modules: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_explicit_recipe_selection_includes_required_recipe_when_auto_is_default() -> None:
    registry = PackRegistry.with_first_party_packs()
    digest = build_recipe_pack_digest(registry.get("openmagi.research"))

    snapshot = AgentRecipeCompiler(registry).compile(
        ProfileResolutionRequest(
            runtimeContext={
                "explicitRecipeSelection": {
                    "mode": "this_turn",
                    "requiredRecipeRefs": [
                        {
                            "recipeId": "openmagi.research",
                            "version": "1",
                            "digest": digest,
                        }
                    ],
                    "allowAdditionalAutoRecipes": True,
                }
            }
        )
    )

    assert snapshot.selected_pack_ids == (
        "openmagi.context-safety",
        "openmagi.evidence",
        "openmagi.web-acquisition",
        "openmagi.research",
    )
    assert snapshot.recipe_selection.selection_source == "explicit"
    assert tuple(ref.recipe_id for ref in snapshot.recipe_selection.requested_recipe_refs) == (
        "openmagi.research",
    )
    assert "openmagi.research" in tuple(
        ref.recipe_id for ref in snapshot.recipe_selection.applied_recipe_refs
    )
    assert snapshot.recipe_selection.omitted_recipe_refs == ()
    assert snapshot.recipe_selection.admission_blocked is False
    assert snapshot.recipe_selection.policy_snapshot_digest.startswith("sha256:")


def test_explicit_governed_recipe_cannot_fall_back_to_default_when_dependency_disabled() -> None:
    snapshot = AgentRecipeCompiler(PackRegistry.with_first_party_packs()).compile(
        ProfileResolutionRequest(
            recipePackConfig={"packs": {"disable": ["openmagi.web-acquisition"]}},
            runtimeContext={
                "explicitRecipeSelection": {
                    "mode": "this_turn",
                    "requiredRecipeRefs": [{"recipeId": "openmagi.research"}],
                }
            },
        )
    )

    assert snapshot.selected_pack_ids == (
        "openmagi.context-safety",
        "openmagi.evidence",
    )
    assert snapshot.recipe_selection.admission_blocked is True
    assert snapshot.recipe_selection.selection_source == "explicit"
    assert tuple(ref.recipe_id for ref in snapshot.recipe_selection.omitted_recipe_refs) == (
        "openmagi.research",
    )
    assert snapshot.recipe_selection.omission_reasons["openmagi.research"] == (
        "dependency_unavailable",
    )
    assert snapshot.tool_refs == ()
    assert snapshot.callback_refs == ()
    assert "approval:recipe-selection:blocked" in snapshot.approval_gate_refs
    assert "checkpoint:recipe-selection:required-recipe-omitted" in snapshot.checkpoint_refs


def test_missing_explicit_recipe_blocks_with_omission_reason() -> None:
    snapshot = AgentRecipeCompiler(PackRegistry.with_first_party_packs()).compile(
        ProfileResolutionRequest(
            runtimeContext={
                "explicitRecipeSelection": {
                    "mode": "this_turn",
                    "requiredRecipeRefs": [{"recipeId": "openmagi.missing-recipe"}],
                }
            }
        )
    )

    assert snapshot.selected_pack_ids == (
        "openmagi.context-safety",
        "openmagi.evidence",
    )
    assert snapshot.recipe_selection.admission_blocked is True
    assert snapshot.recipe_selection.omission_reasons["openmagi.missing-recipe"] == (
        "explicit_recipe_missing",
    )
    assert "openmagi.missing-recipe" not in snapshot.selected_pack_ids
    assert snapshot.tool_refs == ()
    assert snapshot.callback_refs == ()


def test_blocked_explicit_selection_clears_automatic_recipe_runtime_metadata() -> None:
    snapshot = AgentRecipeCompiler(PackRegistry.with_first_party_packs()).compile(
        ProfileResolutionRequest(
            taskProfile={"taskType": "coding"},
            runtimeContext={
                "explicitRecipeSelection": {
                    "mode": "this_turn",
                    "requiredRecipeRefs": [{"recipeId": "openmagi.missing-recipe"}],
                }
            },
        )
    )

    assert snapshot.recipe_selection.admission_blocked is True
    assert snapshot.recipe_selection.selection_source == "explicit"
    assert snapshot.selected_pack_ids == (
        "openmagi.context-safety",
        "openmagi.evidence",
    )
    assert snapshot.recipe_selection.applied_recipe_refs == ()
    assert snapshot.instruction_refs == ()
    assert snapshot.tool_refs == ()
    assert snapshot.callback_refs == ()
    assert snapshot.validator_refs == ()
    assert snapshot.evidence_refs == ()
    assert snapshot.adk_primitive_ownership == ()
    assert snapshot.openmagi_boundary_ownership == ()
    assert snapshot.callback_set_metadata == ()
    assert snapshot.approval_gate_metadata == ()
    assert snapshot.mission_lifecycle is None
    assert snapshot.approval_gate_refs == ("approval:recipe-selection:blocked",)
    assert snapshot.checkpoint_refs == (
        "checkpoint:recipe-selection:required-recipe-omitted",
    )
    assert snapshot.audit_refs == ("audit:recipe-selection-admission-block",)

    rendered = json.dumps(
        snapshot.model_dump(by_alias=True, mode="json", warnings=False),
        sort_keys=True,
    )
    assert "instruction:dev-coding:tdd" not in rendered
    assert "validator:dev-coding:tdd-verification" not in rendered
    assert "approval:dev-coding:workspace-mutation" not in rendered
    assert "evidence:git-diff" not in rendered


def test_disabled_explicit_recipe_blocks_without_silent_auto_fallback() -> None:
    snapshot = AgentRecipeCompiler(PackRegistry.with_first_party_packs()).compile(
        ProfileResolutionRequest(
            recipePackConfig={"packs": {"disable": ["openmagi.dev-coding"]}},
            runtimeContext={
                "explicitRecipeSelection": {
                    "mode": "this_turn",
                    "requiredRecipeRefs": [{"recipeId": "openmagi.dev-coding"}],
                }
            },
        )
    )

    assert snapshot.selected_pack_ids == (
        "openmagi.context-safety",
        "openmagi.evidence",
    )
    assert snapshot.recipe_selection.admission_blocked is True
    assert snapshot.recipe_selection.omission_reasons["openmagi.dev-coding"] == (
        "explicit_recipe_disabled",
    )
    assert "tool:test.run" not in snapshot.tool_refs


def test_unauthorized_explicit_recipe_blocks_when_authorized_refs_are_declared() -> None:
    snapshot = AgentRecipeCompiler(PackRegistry.with_first_party_packs()).compile(
        ProfileResolutionRequest(
            workspacePolicy={"authorizedRecipeRefs": ["openmagi.research"]},
            runtimeContext={
                "explicitRecipeSelection": {
                    "mode": "this_turn",
                    "requiredRecipeRefs": [{"recipeId": "openmagi.dev-coding"}],
                }
            },
        )
    )

    assert snapshot.recipe_selection.admission_blocked is True
    assert snapshot.recipe_selection.omission_reasons["openmagi.dev-coding"] == (
        "explicit_recipe_unauthorized",
    )
    assert "openmagi.dev-coding" not in snapshot.selected_pack_ids
    assert "tool:test.run" not in snapshot.tool_refs


def test_incompatible_explicit_recipe_blocks_fail_closed() -> None:
    registry = PackRegistry.with_first_party_packs()
    registry.register(
        RecipePackManifest(
            packId="openmagi.custom-incompatible",
            displayName="Custom Incompatible",
            description="Custom recipe with a future runtime contract.",
            taskProfileSelectors=("custom-incompatible",),
            compatibleRuntimeContractVersions=("future-runtime.v1",),
        )
    )

    snapshot = AgentRecipeCompiler(registry).compile(
        ProfileResolutionRequest(
            runtimeContext={
                "recipeRuntimeContractVersion": "recipe-pack.v1",
                "explicitRecipeSelection": {
                    "mode": "this_turn",
                    "requiredRecipeRefs": [{"recipeId": "openmagi.custom-incompatible"}],
                },
            }
        )
    )

    assert snapshot.recipe_selection.admission_blocked is True
    assert snapshot.recipe_selection.omission_reasons["openmagi.custom-incompatible"] == (
        "incompatible_runtime_contract",
    )
    assert "openmagi.custom-incompatible" not in snapshot.selected_pack_ids


def test_explicit_recipe_version_digest_policy_and_hard_invariants_are_admitted_fail_closed() -> None:
    snapshot = AgentRecipeCompiler(PackRegistry.with_first_party_packs()).compile(
        ProfileResolutionRequest(
            workspacePolicy={"forbiddenToolRefs": ["tool:test.run"]},
            runtimeContext={
                "projectionPolicy": "raw_text_allowed",
                "hardInvariants": {"validatorBeforeProjection": False},
                "explicitRecipeSelection": {
                    "mode": "this_turn",
                    "requiredRecipeRefs": [
                        {
                            "recipeId": "openmagi.dev-coding",
                            "version": "99",
                            "digest": "sha256:" + "0" * 64,
                        }
                    ],
                },
            },
        )
    )

    reasons = snapshot.recipe_selection.omission_reasons["openmagi.dev-coding"]
    assert "version_mismatch" in reasons
    assert "digest_mismatch" in reasons
    assert "forbidden_tool_ref" in reasons
    assert "forbidden_projection_policy" in reasons
    assert "hard_invariant_downgrade" in reasons
    assert snapshot.recipe_selection.admission_blocked is True
    assert "openmagi.dev-coding" not in snapshot.selected_pack_ids
    assert snapshot.tool_refs == ()
    assert snapshot.callback_refs == ()
    assert set(snapshot.attachment_flags.model_dump(by_alias=True).values()) == {False}


@pytest.mark.parametrize(
    "configured_mode",
    (
        "disabled",
        "enforce",
        "log_only",
        "true",
        "warn_only",
        "0",
        "0.0",
        "no",
        1,
        1.0,
        0,
        0.0,
        {"mode": 0.0},
        {"mode": "enforce"},
        {"enabled": True},
        {"mode": {"enabled": True}},
        {},
    ),
)
def test_explicit_recipe_string_hard_invariant_downgrades_fail_closed(
    configured_mode: object,
) -> None:
    snapshot = AgentRecipeCompiler(PackRegistry.with_first_party_packs()).compile(
        ProfileResolutionRequest(
            runtimeContext={
                "hardInvariants": {"validatorBeforeProjection": configured_mode},
                "explicitRecipeSelection": {
                    "mode": "this_turn",
                    "requiredRecipeRefs": [{"recipeId": "openmagi.research"}],
                },
            },
        )
    )

    reasons = snapshot.recipe_selection.omission_reasons.get("openmagi.research", ())
    assert "hard_invariant_downgrade" in reasons
    assert snapshot.recipe_selection.admission_blocked is True
    assert "openmagi.research" not in snapshot.selected_pack_ids


@pytest.mark.parametrize("hard_invariants_key", ("hardInvariants", "hard_invariants"))
@pytest.mark.parametrize(
    "configured_container",
    ("disabled", False, 0, 0.0, ("disabled",), ["disabled"], {}),
)
def test_explicit_recipe_malformed_hard_invariant_container_fails_closed(
    hard_invariants_key: str,
    configured_container: object,
) -> None:
    snapshot = AgentRecipeCompiler(PackRegistry.with_first_party_packs()).compile(
        ProfileResolutionRequest(
            runtimeContext={
                hard_invariants_key: configured_container,
                "explicitRecipeSelection": {
                    "mode": "this_turn",
                    "requiredRecipeRefs": [{"recipeId": "openmagi.research"}],
                },
            },
        )
    )

    reasons = snapshot.recipe_selection.omission_reasons.get("openmagi.research", ())
    assert "hard_invariant_downgrade" in reasons
    assert snapshot.recipe_selection.admission_blocked is True
    assert "openmagi.research" not in snapshot.selected_pack_ids


def test_explicit_and_automatic_recipe_merge_order_is_deterministic() -> None:
    snapshot = AgentRecipeCompiler(PackRegistry.with_first_party_packs()).compile(
        ProfileResolutionRequest(
            taskProfile={"taskType": "coding"},
            runtimeContext={
                "explicitRecipeSelection": {
                    "mode": "this_turn",
                    "requiredRecipeRefs": [{"recipeId": "openmagi.research"}],
                }
            },
        )
    )

    assert snapshot.selected_pack_ids == (
        "openmagi.context-safety",
        "openmagi.evidence",
        "openmagi.dev-coding",
        "openmagi.web-acquisition",
        "openmagi.research",
    )
    assert snapshot.recipe_selection.selection_source == "mixed"
    assert tuple(ref.recipe_id for ref in snapshot.recipe_selection.requested_recipe_refs) == (
        "openmagi.research",
    )
    assert tuple(ref.recipe_id for ref in snapshot.recipe_selection.applied_recipe_refs) == (
        "openmagi.context-safety",
        "openmagi.evidence",
        "openmagi.dev-coding",
        "openmagi.web-acquisition",
        "openmagi.research",
    )


def test_explicit_recipe_blocks_when_dependency_is_not_authorized() -> None:
    registry = PackRegistry.with_first_party_packs()
    registry.register(
        RecipePackManifest(
            packId="openmagi.private-source-adapter",
            displayName="Private Source Adapter",
            description="A dependency that must be authorized with the parent recipe.",
        )
    )
    registry.register(
        RecipePackManifest(
            packId="openmagi.private-research",
            displayName="Private Research",
            description="Research recipe with an explicitly declared dependency.",
            dependsOnPackIds=("openmagi.private-source-adapter",),
        )
    )

    snapshot = AgentRecipeCompiler(registry).compile(
        ProfileResolutionRequest(
            workspacePolicy={"authorizedRecipeRefs": ["openmagi.private-research"]},
            runtimeContext={
                "explicitRecipeSelection": {
                    "mode": "this_turn",
                    "requiredRecipeRefs": [{"recipeId": "openmagi.private-research"}],
                }
            },
        )
    )

    assert snapshot.recipe_selection.admission_blocked is True
    assert snapshot.recipe_selection.omission_reasons["openmagi.private-research"] == (
        "dependency_unauthorized",
    )
    assert "openmagi.private-research" not in snapshot.selected_pack_ids
    assert "openmagi.private-source-adapter" not in snapshot.selected_pack_ids


def test_automatic_recipe_selection_respects_forbidden_tool_admission() -> None:
    snapshot = AgentRecipeCompiler(PackRegistry.with_first_party_packs()).compile(
        ProfileResolutionRequest(
            taskProfile={"taskType": "coding"},
            workspacePolicy={"forbiddenToolRefs": ["tool:test.run"]},
        )
    )

    assert snapshot.recipe_selection.admission_blocked is True
    assert snapshot.recipe_selection.omission_reasons["openmagi.dev-coding"] == (
        "forbidden_tool_ref",
    )
    assert "openmagi.dev-coding" not in snapshot.selected_pack_ids
    assert snapshot.recipe_selection.applied_recipe_refs == ()
    assert snapshot.instruction_refs == ()
    assert snapshot.tool_refs == ()
    assert snapshot.callback_refs == ()
    assert snapshot.validator_refs == ()
    assert snapshot.evidence_refs == ()
    assert snapshot.approval_gate_refs == ("approval:recipe-selection:blocked",)
    assert snapshot.checkpoint_refs == (
        "checkpoint:recipe-selection:required-recipe-omitted",
    )
    assert snapshot.audit_refs == ("audit:recipe-selection-admission-block",)


def test_malformed_explicit_recipe_selection_fails_closed_without_auto_fallback() -> None:
    snapshot = AgentRecipeCompiler(PackRegistry.with_first_party_packs()).compile(
        ProfileResolutionRequest(
            taskProfile={"taskType": "research"},
            runtimeContext={"explicitRecipeSelection": "openmagi.research"},
        )
    )

    assert snapshot.selected_pack_ids == (
        "openmagi.context-safety",
        "openmagi.evidence",
    )
    assert snapshot.recipe_selection.selection_source == "explicit"
    assert snapshot.recipe_selection.admission_blocked is True
    assert snapshot.recipe_selection.omission_reasons[
        "openmagi.invalid-explicit-selection"
    ] == ("malformed_explicit_recipe_selection",)
    assert "openmagi.research" not in snapshot.selected_pack_ids


def test_runtime_context_cannot_self_authorize_explicit_recipe() -> None:
    snapshot = AgentRecipeCompiler(PackRegistry.with_first_party_packs()).compile(
        ProfileResolutionRequest(
            workspacePolicy={"authorizedRecipeRefs": ["openmagi.research"]},
            runtimeContext={
                "authorizedRecipeRefs": ["openmagi.dev-coding"],
                "explicitRecipeSelection": {
                    "mode": "this_turn",
                    "requiredRecipeRefs": [{"recipeId": "openmagi.dev-coding"}],
                },
            },
        )
    )

    assert snapshot.recipe_selection.admission_blocked is True
    assert snapshot.recipe_selection.omission_reasons["openmagi.dev-coding"] == (
        "explicit_recipe_unauthorized",
    )
    assert "openmagi.dev-coding" not in snapshot.selected_pack_ids
    assert "tool:test.run" not in snapshot.tool_refs


def test_unsafe_explicit_recipe_ref_is_not_stored_in_snapshot_metadata() -> None:
    unsafe_version = _fixture_value("Author", "ization: Bearer ", "sec", "ret")
    snapshot = AgentRecipeCompiler(PackRegistry.with_first_party_packs()).compile(
        ProfileResolutionRequest(
            runtimeContext={
                "explicitRecipeSelection": {
                    "mode": "this_turn",
                    "requiredRecipeRefs": [
                        {
                            "recipeId": "/Users/kevin/.kube/config",
                            "version": unsafe_version,
                            "digest": "not-a-digest",
                        }
                    ],
                }
            }
        )
    )

    dumped = snapshot.model_dump(by_alias=True, mode="json", warnings=False)
    rendered = json.dumps(dumped, sort_keys=True)
    assert "kevin" not in rendered
    assert unsafe_version not in rendered
    assert "/Users" not in rendered
    assert snapshot.recipe_selection.admission_blocked is True
    assert tuple(ref.recipe_id for ref in snapshot.recipe_selection.omitted_recipe_refs) == (
        "openmagi.invalid-explicit-selection",
    )


def test_resolved_profile_drops_raw_private_runtime_metadata() -> None:
    private_config_key = _fixture_value("private", "Config")
    private_config_value = _fixture_value("s", "k", "-proj-", "sec", "ret-", "tok", "en")
    provider_config_key = _fixture_value("provider", "Config")
    provider_config_secret_key = _fixture_value("api", "Key")
    provider_config_secret_value = _fixture_value(
        "openai:",
        "s",
        "k",
        "-",
        "abc",
        "1234",
        "567",
        "890",
    )
    snapshot = AgentRecipeCompiler(PackRegistry.with_first_party_packs()).compile(
        ProfileResolutionRequest(
            runtimeContext={
                "rawPolicySnapshot": "private active snapshot",
                private_config_key: {
                    _fixture_value("tok", "en"): private_config_value,
                },
                "safeDiagnosticRef": "diag_123",
                "diagnosticRef": "/Users/kevin/.kube/config",
                "output": "raw model output should never be snapshot metadata",
                provider_config_key: {provider_config_secret_key: provider_config_secret_value},
            }
        )
    )

    dumped = snapshot.model_dump(by_alias=True, mode="json", warnings=False)
    rendered = json.dumps(dumped, sort_keys=True)
    assert "private active snapshot" not in rendered
    assert private_config_value not in rendered
    assert provider_config_secret_value not in rendered
    assert provider_config_secret_key not in rendered
    assert "/Users" not in rendered
    assert "raw model output" not in rendered
    assert "safeDiagnosticRef" in dumped["resolvedProfile"]


def test_resolved_profile_drops_raw_instruction_and_system_config_metadata() -> None:
    system_instruction = _fixture_value("proprietary ", "system ", "instruction")
    developer_instruction = _fixture_value("private ", "developer ", "instruction")
    system_config_value = _fixture_value("internal ", "system ", "config")
    system_instruction_override = _fixture_value("alternate ", "system ", "instruction")
    developer_instruction_text = _fixture_value("alternate ", "developer ", "instruction")
    system_config_v2 = _fixture_value("alternate ", "system ", "config")
    dotted_system_instruction = _fixture_value("dotted ", "system ", "instruction")
    dotted_developer_instruction = _fixture_value("dotted ", "developer ", "instruction")
    dotted_system_config = _fixture_value("dotted ", "system ", "config")
    active_system_instruction = _fixture_value("active ", "system ", "instruction")
    compiled_developer_instruction = _fixture_value("compiled ", "developer ", "instruction")
    resolved_system_config = _fixture_value("resolved ", "system ", "config")
    workspace_system_config = _fixture_value("workspace ", "system ", "config")
    recipe_instruction = _fixture_value("recipe ", "instruction ", "text")
    workspace_recipe_config = _fixture_value("workspace ", "recipe ", "config")
    active_runtime_config = _fixture_value("active ", "runtime ", "config")

    snapshot = AgentRecipeCompiler(PackRegistry.with_first_party_packs()).compile(
        ProfileResolutionRequest(
            runtimeContext={
                "systemInstruction": system_instruction,
                "developerInstruction": developer_instruction,
                "systemConfig": {"policy": system_config_value},
                "systemInstructionOverride": system_instruction_override,
                "developerInstructionsText": developer_instruction_text,
                "systemConfigV2": {"policy": system_config_v2},
                "system.instruction.override": dotted_system_instruction,
                "developer.instructions.text": dotted_developer_instruction,
                "system.config.v2": {"policy": dotted_system_config},
                "activeSystemInstruction": active_system_instruction,
                "compiled.developer.instructions": compiled_developer_instruction,
                "resolvedSystemConfig": {"policy": resolved_system_config},
                "workspace.system.config": {"policy": workspace_system_config},
                "recipeInstruction": recipe_instruction,
                "workspace.recipe.config": {"policy": workspace_recipe_config},
                "activeRuntimeConfig": {"policy": active_runtime_config},
                "safeDiagnosticRef": "diag_123",
            }
        )
    )

    dumped = snapshot.model_dump(by_alias=True, mode="json", warnings=False)
    rendered = json.dumps(dumped, sort_keys=True)
    assert "systemInstruction" not in rendered
    assert "developerInstruction" not in rendered
    assert "systemConfig" not in rendered
    assert "systemInstructionOverride" not in rendered
    assert "developerInstructionsText" not in rendered
    assert "systemConfigV2" not in rendered
    assert "system.instruction.override" not in rendered
    assert "developer.instructions.text" not in rendered
    assert "system.config.v2" not in rendered
    assert "activeSystemInstruction" not in rendered
    assert "compiled.developer.instructions" not in rendered
    assert "resolvedSystemConfig" not in rendered
    assert "workspace.system.config" not in rendered
    assert "recipeInstruction" not in rendered
    assert "workspace.recipe.config" not in rendered
    assert "activeRuntimeConfig" not in rendered
    assert system_instruction not in rendered
    assert developer_instruction not in rendered
    assert system_config_value not in rendered
    assert system_instruction_override not in rendered
    assert developer_instruction_text not in rendered
    assert system_config_v2 not in rendered
    assert dotted_system_instruction not in rendered
    assert dotted_developer_instruction not in rendered
    assert dotted_system_config not in rendered
    assert active_system_instruction not in rendered
    assert compiled_developer_instruction not in rendered
    assert resolved_system_config not in rendered
    assert workspace_system_config not in rendered
    assert recipe_instruction not in rendered
    assert workspace_recipe_config not in rendered
    assert active_runtime_config not in rendered
    assert dumped["resolvedProfile"] == {"safeDiagnosticRef": "diag_123"}


def test_resolved_profile_drops_tool_and_hidden_runtime_metadata() -> None:
    raw_tool_arg_value = _fixture_value("fixture-", "tool-", "argument")
    raw_tool_result_value = _fixture_value("fixture-", "tool-", "result")
    hidden_config_value = _fixture_value("fixture-", "hidden-", "config")
    raw_tool_input_value = _fixture_value("fixture-", "tool-", "input")
    raw_tool_output_value = _fixture_value("fixture-", "tool-", "output")

    snapshot = AgentRecipeCompiler(PackRegistry.with_first_party_packs()).compile(
        ProfileResolutionRequest(
            runtimeContext={
                "toolArgs": {"query": raw_tool_arg_value},
                "toolResult": {"body": raw_tool_result_value},
                "hiddenConfig": {"mode": hidden_config_value},
                "nested": {
                    "toolInput": raw_tool_input_value,
                    "toolOutput": raw_tool_output_value,
                },
                "safeDiagnosticRef": "diag_123",
            }
        )
    )

    dumped = snapshot.model_dump(by_alias=True, mode="json", warnings=False)
    rendered = json.dumps(dumped, sort_keys=True)
    assert "toolArgs" not in rendered
    assert "toolResult" not in rendered
    assert "hiddenConfig" not in rendered
    assert "toolInput" not in rendered
    assert "toolOutput" not in rendered
    assert raw_tool_arg_value not in rendered
    assert raw_tool_result_value not in rendered
    assert hidden_config_value not in rendered
    assert raw_tool_input_value not in rendered
    assert raw_tool_output_value not in rendered
    assert dumped["resolvedProfile"] == {
        "nested": {},
        "safeDiagnosticRef": "diag_123",
    }


def test_resolved_profile_model_boundaries_redact_tool_and_hidden_metadata() -> None:
    raw_tool_arg_value = _fixture_value("fixture-", "tool-", "argument")
    hidden_config_value = _fixture_value("fixture-", "hidden-", "config")
    raw_tool_output_value = _fixture_value("fixture-", "tool-", "output")

    resolved = ResolvedRecipeProfile(
        resolvedProfile={
            "toolArgs": {"query": raw_tool_arg_value},
            "hiddenConfig": {"mode": hidden_config_value},
            "nested": {"toolOutput": raw_tool_output_value},
            "safeDiagnosticRef": "diag_123",
        },
        selectedPackIds=(),
    )
    snapshot = RecipeSnapshot(
        snapshotId=build_recipe_snapshot_id(()),
        resolvedProfile={
            "toolArgs": {"query": raw_tool_arg_value},
            "hiddenConfig": {"mode": hidden_config_value},
            "nested": {"toolOutput": raw_tool_output_value},
            "safeDiagnosticRef": "diag_123",
        },
        selectedPackIds=(),
    )

    rendered = json.dumps(
        {
            "resolved": resolved.model_dump(by_alias=True, mode="json", warnings=False),
            "snapshot": snapshot.model_dump(by_alias=True, mode="json", warnings=False),
        },
        sort_keys=True,
    )
    assert "toolArgs" not in rendered
    assert "hiddenConfig" not in rendered
    assert "toolOutput" not in rendered
    assert raw_tool_arg_value not in rendered
    assert hidden_config_value not in rendered
    assert raw_tool_output_value not in rendered
    assert resolved.model_dump(by_alias=True)["resolvedProfile"] == {
        "nested": {},
        "safeDiagnosticRef": "diag_123",
    }
    assert snapshot.model_dump(by_alias=True)["resolvedProfile"] == {
        "nested": {},
        "safeDiagnosticRef": "diag_123",
    }


def test_resolved_profile_model_boundaries_redact_tool_hidden_metadata_variants() -> None:
    tool_call_value = _fixture_value("fixture-", "tool-", "call")
    tool_metadata_value = _fixture_value("fixture-", "tool-", "metadata")
    hidden_metadata_value = _fixture_value("fixture-", "hidden-", "metadata")

    resolved = ResolvedRecipeProfile(
        resolvedProfile={
            "toolCall": tool_call_value,
            "toolCalls": [tool_call_value],
            "toolMetadata": {"kind": tool_metadata_value},
            "hiddenMetadata": {"kind": hidden_metadata_value},
            "safeDiagnosticRef": "diag_123",
        },
        selectedPackIds=(),
    )
    snapshot = RecipeSnapshot(
        snapshotId=build_recipe_snapshot_id(()),
        resolvedProfile={
            "toolCall": tool_call_value,
            "toolCalls": [tool_call_value],
            "toolMetadata": {"kind": tool_metadata_value},
            "hiddenMetadata": {"kind": hidden_metadata_value},
            "safeDiagnosticRef": "diag_123",
        },
        selectedPackIds=(),
    )

    rendered = json.dumps(
        {
            "resolved": resolved.model_dump(by_alias=True, mode="json", warnings=False),
            "snapshot": snapshot.model_dump(by_alias=True, mode="json", warnings=False),
        },
        sort_keys=True,
    )
    assert "toolCall" not in rendered
    assert "toolCalls" not in rendered
    assert "toolMetadata" not in rendered
    assert "hiddenMetadata" not in rendered
    assert tool_call_value not in rendered
    assert tool_metadata_value not in rendered
    assert hidden_metadata_value not in rendered
    assert resolved.model_dump(by_alias=True)["resolvedProfile"] == {
        "safeDiagnosticRef": "diag_123",
    }
    assert snapshot.model_dump(by_alias=True)["resolvedProfile"] == {
        "safeDiagnosticRef": "diag_123",
    }


def test_resolved_profile_model_boundaries_redact_raw_private_string_values() -> None:
    raw_child_transcript = _fixture_value("raw ", "child ", "transcript ", "payload")
    private_fixture_query = _fixture_value("private ", "fixture ", "query")

    resolved = ResolvedRecipeProfile(
        resolvedProfile={
            "diagnosticA": raw_child_transcript,
            "diagnosticB": private_fixture_query,
            "safeDiagnosticRef": "diag_123",
        },
        selectedPackIds=(),
    )
    snapshot = RecipeSnapshot(
        snapshotId=build_recipe_snapshot_id(()),
        resolvedProfile={
            "diagnosticA": raw_child_transcript,
            "diagnosticB": private_fixture_query,
            "safeDiagnosticRef": "diag_123",
        },
        selectedPackIds=(),
    )

    rendered = json.dumps(
        {
            "resolved": resolved.model_dump(by_alias=True, mode="json", warnings=False),
            "snapshot": snapshot.model_dump(by_alias=True, mode="json", warnings=False),
        },
        sort_keys=True,
    )
    assert "diagnosticA" not in rendered
    assert "diagnosticB" not in rendered
    assert raw_child_transcript not in rendered
    assert private_fixture_query not in rendered
    assert resolved.model_dump(by_alias=True)["resolvedProfile"] == {
        "safeDiagnosticRef": "diag_123",
    }
    assert snapshot.model_dump(by_alias=True)["resolvedProfile"] == {
        "safeDiagnosticRef": "diag_123",
    }


def test_resolved_profile_model_boundaries_redact_raw_private_camelcase_values() -> None:
    raw_tool_call_output = _fixture_value("raw", "Tool", "Call", "Output payload")
    raw_model_output = _fixture_value("raw_", "model_", "output payload")
    private_runtime_metadata = _fixture_value("private", "Runtime", "Metadata payload")

    resolved = ResolvedRecipeProfile(
        resolvedProfile={
            "diagnosticA": raw_tool_call_output,
            "diagnosticB": raw_model_output,
            "diagnosticC": private_runtime_metadata,
            "safeDiagnosticRef": "diag_123",
        },
        selectedPackIds=(),
    )
    snapshot = RecipeSnapshot(
        snapshotId=build_recipe_snapshot_id(()),
        resolvedProfile={
            "diagnosticA": raw_tool_call_output,
            "diagnosticB": raw_model_output,
            "diagnosticC": private_runtime_metadata,
            "safeDiagnosticRef": "diag_123",
        },
        selectedPackIds=(),
    )

    rendered = json.dumps(
        {
            "resolved": resolved.model_dump(by_alias=True, mode="json", warnings=False),
            "snapshot": snapshot.model_dump(by_alias=True, mode="json", warnings=False),
        },
        sort_keys=True,
    )
    assert "diagnosticA" not in rendered
    assert "diagnosticB" not in rendered
    assert "diagnosticC" not in rendered
    assert raw_tool_call_output not in rendered
    assert raw_model_output not in rendered
    assert private_runtime_metadata not in rendered
    assert resolved.model_dump(by_alias=True)["resolvedProfile"] == {
        "safeDiagnosticRef": "diag_123",
    }
    assert snapshot.model_dump(by_alias=True)["resolvedProfile"] == {
        "safeDiagnosticRef": "diag_123",
    }


def test_token_shaped_explicit_recipe_version_is_rejected_before_snapshot_storage() -> None:
    token_like_version = _fixture_value("s", "k", "-proj-", "sec", "ret-", "tok", "en")
    snapshot = AgentRecipeCompiler(PackRegistry.with_first_party_packs()).compile(
        ProfileResolutionRequest(
            runtimeContext={
                "explicitRecipeSelection": {
                    "mode": "this_turn",
                    "requiredRecipeRefs": [
                        {
                            "recipeId": "openmagi.research",
                            "version": token_like_version,
                        }
                    ],
                }
            }
        )
    )

    rendered = json.dumps(
        snapshot.model_dump(by_alias=True, mode="json", warnings=False),
        sort_keys=True,
    )
    assert token_like_version not in rendered
    assert snapshot.recipe_selection.admission_blocked is True
    assert snapshot.recipe_selection.omission_reasons[
        "openmagi.invalid-explicit-selection"
    ] == ("malformed_explicit_recipe_selection",)


def test_default_enabled_pack_cannot_bypass_admission_policy() -> None:
    registry = PackRegistry.with_first_party_packs()
    registry.register(
        RecipePackManifest(
            packId="openmagi.default-tool-pack",
            displayName="Default Tool Pack",
            description="A default pack still must satisfy admission policy.",
            defaultEnabled=True,
            toolRefs=("tool:test.run",),
        )
    )

    snapshot = AgentRecipeCompiler(registry).compile(
        ProfileResolutionRequest(
            workspacePolicy={
                "authorizedRecipeRefs": [
                    "openmagi.context-safety",
                    "openmagi.evidence",
                ],
                "forbiddenToolRefs": ["tool:test.run"],
            }
        )
    )

    assert snapshot.recipe_selection.admission_blocked is True
    assert snapshot.recipe_selection.omission_reasons["openmagi.default-tool-pack"] == (
        "explicit_recipe_unauthorized",
        "forbidden_tool_ref",
    )
    assert "openmagi.default-tool-pack" not in snapshot.selected_pack_ids
    assert "tool:test.run" not in snapshot.tool_refs


def test_hard_safety_pack_cannot_emit_forbidden_tool_refs() -> None:
    registry = PackRegistry.with_first_party_packs()
    registry.register(
        RecipePackManifest(
            packId="openmagi.custom-hard-safety",
            displayName="Custom Hard Safety",
            description="Hard-safety packs cannot carry forbidden tool refs.",
            hardSafety=True,
            optOutAllowed=False,
            customizable=False,
            toolRefs=("tool:test.run",),
        )
    )

    snapshot = AgentRecipeCompiler(registry).compile(
        ProfileResolutionRequest(workspacePolicy={"forbiddenToolRefs": ["tool:test.run"]})
    )

    assert snapshot.recipe_selection.admission_blocked is True
    assert snapshot.recipe_selection.omission_reasons["openmagi.custom-hard-safety"] == (
        "forbidden_tool_ref",
    )
    assert "openmagi.custom-hard-safety" not in snapshot.selected_pack_ids
    assert "tool:test.run" not in snapshot.tool_refs


def test_request_runtime_metadata_cannot_suppress_hard_safety_packs() -> None:
    snapshot = AgentRecipeCompiler(PackRegistry.with_first_party_packs()).compile(
        ProfileResolutionRequest(
            runtimeContext={
                "projectionPolicy": "raw_text_allowed",
                "hardInvariants": {"validatorBeforeProjection": False},
                "recipeRuntimeContractVersion": "future-runtime.v1",
            }
        )
    )

    assert snapshot.selected_pack_ids == (
        "openmagi.context-safety",
        "openmagi.evidence",
    )
    assert "validator:context-safety:public-redaction" in snapshot.validator_refs
    assert "audit:recipe-profile-resolution" in snapshot.audit_refs


def test_token_prefix_recipe_versions_are_rejected_before_snapshot_storage() -> None:
    for token_like_version in (
        _fixture_value("s", "k", "-", "abc", "1234", "567", "890"),
        _fixture_value("github", "_pat_", "abc", "1234", "567", "890"),
    ):
        snapshot = AgentRecipeCompiler(PackRegistry.with_first_party_packs()).compile(
            ProfileResolutionRequest(
                runtimeContext={
                    "explicitRecipeSelection": {
                        "mode": "this_turn",
                        "requiredRecipeRefs": [
                            {
                                "recipeId": "openmagi.research",
                                "version": token_like_version,
                            }
                        ],
                    }
                }
            )
        )

        rendered = json.dumps(
            snapshot.model_dump(by_alias=True, mode="json", warnings=False),
            sort_keys=True,
        )
        assert token_like_version not in rendered
        assert snapshot.recipe_selection.omission_reasons[
            "openmagi.invalid-explicit-selection"
        ] == ("malformed_explicit_recipe_selection",)


def test_malformed_explicit_selection_excludes_non_hard_default_packs() -> None:
    registry = PackRegistry.with_first_party_packs()
    registry.register(
        RecipePackManifest(
            packId="openmagi.default-benign-tool",
            displayName="Default Benign Tool",
            description="Non-hard default packs must not influence malformed requests.",
            defaultEnabled=True,
            instructionRefs=("instruction:malicious-default",),
            validatorRefs=("validator:malicious-default",),
        )
    )

    snapshot = AgentRecipeCompiler(registry).compile(
        ProfileResolutionRequest(runtimeContext={"explicitRecipeSelection": "not-a-map"})
    )

    assert snapshot.selected_pack_ids == (
        "openmagi.context-safety",
        "openmagi.evidence",
    )
    assert "instruction:malicious-default" not in snapshot.instruction_refs
    assert "validator:malicious-default" not in snapshot.validator_refs
