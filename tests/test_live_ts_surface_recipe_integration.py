from __future__ import annotations

import json
import subprocess
import sys

from openmagi_core_agent.recipes.compiler import (
    AgentRecipeCompiler,
    PackRegistry,
    ProfileResolutionRequest,
    RecipeSnapshot,
    build_recipe_snapshot_id,
)
from openmagi_core_agent.recipes.materializer import RecipeMaterializer


def _plan(
    *,
    task_profile: dict[str, object],
    runtime_context: dict[str, object] | None = None,
    recipe_pack_config: dict[str, object] | None = None,
) -> object:
    snapshot = AgentRecipeCompiler(PackRegistry.with_first_party_packs()).compile(
        ProfileResolutionRequest(
            taskProfile=task_profile,
            runtimeContext=runtime_context or {},
            recipePackConfig=recipe_pack_config or {},
        )
    )
    return RecipeMaterializer.with_reliability_defaults().materialize(
        snapshot,
        modelProvider="google",
        modelLabel="gemini-3.5-flash",
    )


def _false_attachment_flags(plan: object) -> None:
    assert set(plan.attachment_flags.values()) == {False}
    assert plan.live_attachment_refs == ()


def test_research_web_acquisition_and_citation_enforcement_materialize_provider_intents() -> None:
    plan = _plan(task_profile={"taskTypes": ["web-acquisition", "research"]})

    assert plan.selected_pack_ids[:4] == (
        "openmagi.context-safety",
        "openmagi.evidence",
        "openmagi.web-acquisition",
        "openmagi.research",
    )
    assert plan.provider_intents[:4] == (
        "provider:web.search",
        "provider:web.fetch",
        "provider:reader.extract",
        "provider:browser.snapshot_fallback",
    )
    assert "evidence:web-acquisition:source-ledger-input" in plan.evidence_requirements
    assert "evidence:inspected-source" in plan.evidence_requirements
    assert "approval:web-acquisition:provider-opt-in" in plan.approval_gates
    assert "validator:research:citation-support" in plan.final_gate_policy.required_validators
    assert "kill-switch:web-acquisition-provider" in plan.kill_switch_refs
    assert "rollback:provider-intents-to-metadata-only" in plan.rollback_refs
    assert plan.materialization_order_refs == (
        "order:01-hard-safety",
        "order:02-identity-session",
        "order:03-recipe-dependencies",
        "order:04-provider-intents",
        "order:05-tool-intents",
        "order:06-validators",
        "order:07-evidence",
        "order:08-channel-artifact-delivery",
        "order:09-final-output-gate",
    )
    _false_attachment_flags(plan)


def test_generic_materializer_does_not_inject_research_specific_workflow_requirements() -> None:
    snapshot = RecipeSnapshot(
        snapshotId=build_recipe_snapshot_id(("openmagi.research",)),
        resolvedProfile={"taskType": "research"},
        selectedPackIds=("openmagi.research",),
    )
    plan = RecipeMaterializer.with_reliability_defaults().materialize(
        snapshot,
        modelProvider="google",
        modelLabel="gemini-3.5-flash",
    )
    phase_routes = plan.phase_routing.phase_routes

    assert "openmagi.research" in plan.selected_pack_ids
    assert "requirement:citation-policy" not in plan.evidence_requirements
    assert "requirement:fact-grounding" not in plan.evidence_requirements
    assert "source_acquisition" not in phase_routes
    assert "source_extraction" not in phase_routes


def test_coding_file_send_and_file_deliver_materialize_tool_and_artifact_intents() -> None:
    plan = _plan(task_profile={"taskTypes": ["coding", "artifact-delivery"]})

    assert "openmagi.dev-coding" in plan.selected_pack_ids
    assert "openmagi.office-automation" in plan.selected_pack_ids
    assert "openmagi.artifact-delivery" in plan.selected_pack_ids
    assert {
        "tool:file.read",
        "tool:test.run",
        "tool:artifact.prepare-delivery",
        "tool:file.delivery-plan",
        "tool:FileDeliver",
        "tool:FileSend",
    }.issubset(set(plan.tool_intents))
    assert plan.artifact_intents == (
        "artifact:prepare-delivery",
        "artifact:file-deliver",
        "artifact:file-send",
    )
    assert "approval:dev-coding:workspace-mutation" in plan.approval_gates
    assert "approval:artifact-delivery:channel-send" in plan.approval_gates
    assert "rollback:artifact-delivery-to-ref-only" in plan.rollback_refs
    _false_attachment_flags(plan)


def test_mission_cron_and_notify_user_materialize_scheduler_and_channel_intents() -> None:
    plan = _plan(
        task_profile={"taskTypes": ["mission", "scheduled-work"], "taskIntents": ["notify-user"]},
        runtime_context={"channel": "web"},
    )

    assert "openmagi.missions" in plan.selected_pack_ids
    assert "openmagi.scheduled-work" in plan.selected_pack_ids
    assert "openmagi.channel-delivery" in plan.selected_pack_ids
    assert {
        "scheduler:cron.create",
        "scheduler:cron.list",
        "scheduler:cron.update",
        "scheduler:task.wait",
        "scheduler:task.output",
        "scheduler:task.stop",
        "scheduler:notify-user",
    }.issubset(set(plan.scheduler_intents))
    assert {"tool:CronList", "tool:TaskWait", "tool:TaskOutput", "tool:TaskStop"}.issubset(set(plan.tool_intents))
    assert "channel:web.push" in plan.channel_intents
    assert "approval:missions:cancel-retry-resume-control" in plan.approval_gates
    assert "kill-switch:scheduler-runtime" in plan.kill_switch_refs
    _false_attachment_flags(plan)


def test_telegram_and_discord_artifact_delivery_materialize_channel_specific_intents() -> None:
    telegram = _plan(
        task_profile={"taskTypes": ["artifact-delivery"], "taskIntents": ["telegram"]},
        runtime_context={"channel": "telegram"},
    )
    discord = _plan(
        task_profile={"taskTypes": ["artifact-delivery"], "taskIntents": ["discord"]},
        runtime_context={"channel": "discord"},
    )

    assert "openmagi.channel-delivery" in telegram.selected_pack_ids
    assert "openmagi.channel-delivery" in discord.selected_pack_ids
    assert {
        "channel:dispatcher.push",
        "channel:telegram.send_message",
        "channel:telegram.send_document",
    }.issubset(set(telegram.channel_intents))
    assert {
        "channel:dispatcher.push",
        "channel:discord.send_message",
        "channel:discord.send_file",
    }.issubset(set(discord.channel_intents))
    assert telegram.artifact_intents == (
        "artifact:prepare-delivery",
        "artifact:file-deliver",
        "artifact:file-send",
    )
    assert discord.artifact_intents == telegram.artifact_intents
    assert "kill-switch:channel-delivery" in telegram.kill_switch_refs
    assert "kill-switch:channel-delivery" in discord.kill_switch_refs
    _false_attachment_flags(telegram)
    _false_attachment_flags(discord)


def test_web_acquisition_browser_fallback_requires_browser_provider_and_approval_intents() -> None:
    plan = _plan(task_profile={"taskTypes": ["web-acquisition", "browser-automation"]})

    assert "openmagi.web-acquisition" in plan.selected_pack_ids
    assert "openmagi.browser-automation" in plan.selected_pack_ids
    assert {
        "provider:browser.snapshot_fallback",
        "provider:browser.worker",
        "provider:browser.screenshot",
    }.issubset(set(plan.provider_intents))
    assert "approval:web-acquisition:browser-fallback" in plan.approval_gates
    assert "approval:browser-automation:external-action" in plan.approval_gates
    assert "evidence:browser-inspection" in plan.evidence_requirements
    _false_attachment_flags(plan)


def test_research_coding_and_file_delivery_one_turn_does_not_drop_domain_controls() -> None:
    plan = _plan(task_profile={"taskTypes": ["research", "coding", "artifact-delivery"]})

    assert plan.selected_pack_ids == (
        "openmagi.context-safety",
        "openmagi.evidence",
        "openmagi.web-acquisition",
        "openmagi.research",
        "openmagi.dev-coding",
        "openmagi.office-automation",
        "openmagi.artifact-delivery",
    )
    assert {"provider:web.search", "provider:web.fetch"}.issubset(set(plan.provider_intents))
    assert {"tool:file.read", "tool:test.run", "tool:FileDeliver"}.issubset(set(plan.tool_intents))
    assert "artifact:file-send" in plan.artifact_intents
    assert {
        "approval:research:external-source-use",
        "approval:dev-coding:workspace-mutation",
        "approval:artifact-delivery:channel-send",
    }.issubset(set(plan.approval_gates))
    assert {
        "evidence:inspected-source",
        "evidence:git-diff",
        "evidence:test-run",
        "evidence:artifact-delivery-ref",
    }.issubset(set(plan.evidence_requirements))
    assert "kill-switch:toolhost" in plan.kill_switch_refs
    assert "rollback:provider-intents-to-metadata-only" in plan.rollback_refs
    _false_attachment_flags(plan)


def test_recipe_materializer_integration_import_boundary() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

importlib.import_module("openmagi_core_agent.recipes.materializer")
forbidden_prefixes = (
    "google.adk.runners",
    "google.adk.agents",
    "google.adk.sessions",
    "google.adk.tools",
    "openmagi_core_agent.adk_bridge.runner_adapter",
    "openmagi_core_agent.adk_bridge.tool_adapter",
    "openmagi_core_agent.transport",
    "openmagi_core_agent.channels",
    "openmagi_core_agent.browser.live_provider_pack",
    "openmagi_core_agent.web_acquisition.live_provider_pack",
    "openmagi_core_agent.deploy",
    "openmagi_core_agent.canary",
    "openai",
    "anthropic",
    "requests",
    "httpx",
    "aiohttp",
    "socket",
    "subprocess",
    "telegram",
    "discord",
    "playwright",
    "selenium",
    "kubernetes",
)
loaded = [
    name
    for name in sys.modules
    if any(name == prefix or name.startswith(f"{prefix}.") for prefix in forbidden_prefixes)
]
if loaded:
    raise AssertionError(f"forbidden modules loaded: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_recipe_materializer_projection_contains_no_live_attachment_or_secret_metadata() -> None:
    plan = _plan(
        task_profile={"taskTypes": ["research", "browser-automation", "artifact-delivery"]},
        runtime_context={"channel": "telegram"},
    )

    encoded = json.dumps(plan.model_dump(by_alias=True), sort_keys=True)

    assert "GATEWAY_TOKEN" not in encoded
    assert "FIRECRAWL_API_KEY" not in encoded
    assert "adkRunnerInvoked\": true" not in encoded
    assert "providerCalled\": true" not in encoded
    assert "routeAttached\": true" not in encoded
    assert "productionWriteAllowed\": true" not in encoded
    assert "userVisibleOutputAllowed\": true" not in encoded
