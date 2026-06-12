from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable

import pytest

from magi_agent.tools import (
    ToolDispatcher,
    ToolRegistry,
    ToolResult,
    register_core_tool_manifests,
)
from magi_agent.tools.context import ToolContext as OpenMagiToolContext


LOCAL_READ_TOOLS = ("FileRead", "Glob", "Grep")
FORBIDDEN_RESEARCH_TOOLS = {
    "Bash",
    "TestRun",
    "FileWrite",
    "FileEdit",
    "GitDiff",
}
SCOUT_FIXTURE_TOOLS = (
    "FixtureRepoClone",
    "FixtureRepoOverview",
    "FixtureReferenceRead",
    "FixtureReferenceGrep",
    "FixtureReferenceGlob",
    "FixtureWebSearch",
    "FixtureWebFetch",
)


def make_context_factory() -> Callable[[object], OpenMagiToolContext]:
    def factory(adk_tool_context: object) -> OpenMagiToolContext:
        return OpenMagiToolContext(
            bot_id="bot-research",
            turn_id="turn-research",
            workspace_root="/tmp/workspace",
            adk_tool_context=adk_tool_context,
        )

    return factory


def enabled_core_registry(*tool_names: str) -> ToolRegistry:
    registry = ToolRegistry()
    register_core_tool_manifests(registry)
    for name in tool_names:
        registry.enable(name)
        registry.bind_handler(
            name,
            lambda _arguments, _context: ToolResult(status="ok", output={}),
            enabled_by_registry_policy=True,
        )
    return registry


def test_explore_research_agent_grants_no_mutation_shell_test_or_git_tools() -> None:
    from magi_agent.recipes.research_agents import materialize_research_agent

    decision = materialize_research_agent(
        "explore",
        available_tools=(*LOCAL_READ_TOOLS, *FORBIDDEN_RESEARCH_TOOLS),
    )

    assert decision.status == "ready"
    assert decision.spec is not None
    assert decision.spec.read_only is True
    assert decision.spec.min_search_operations == 3
    assert set(decision.granted_tool_names) == set(LOCAL_READ_TOOLS)
    assert set(decision.granted_tool_names).isdisjoint(FORBIDDEN_RESEARCH_TOOLS)
    assert all(grant.read_only and not grant.mutates_workspace for grant in decision.spec.tool_grants)


def test_plan_research_agent_grants_no_mutation_shell_test_or_git_tools() -> None:
    from magi_agent.recipes.research_agents import materialize_research_agent

    decision = materialize_research_agent(
        "plan",
        available_tools=(*LOCAL_READ_TOOLS, *FORBIDDEN_RESEARCH_TOOLS),
    )

    assert decision.status == "ready"
    assert decision.spec is not None
    assert decision.spec.read_only is True
    assert set(decision.granted_tool_names) == set(LOCAL_READ_TOOLS)
    assert set(decision.granted_tool_names).isdisjoint(FORBIDDEN_RESEARCH_TOOLS)
    assert "criticalFiles" in decision.spec.final_output_schema["properties"]


def test_verifier_research_agent_has_structured_pass_fail_partial_output_schema() -> None:
    from magi_agent.recipes.research_agents import materialize_research_agent

    decision = materialize_research_agent("verifier", available_tools=LOCAL_READ_TOOLS)

    assert decision.status == "ready"
    assert decision.spec is not None
    schema = decision.spec.final_output_schema
    assert schema["type"] == "object"
    assert schema["required"] == ("status", "evidenceRefs")
    assert schema["properties"]["status"]["enum"] == ("PASS", "FAIL", "PARTIAL")
    assert "evidenceRefs" in schema["properties"]


def test_scout_research_profile_is_fixture_only_default_off_metadata() -> None:
    from magi_agent.recipes.research_agents import (
        materialize_scout_research_profile,
    )

    profile = materialize_scout_research_profile()

    assert profile.profile_key == "opencode.scout_research_agent"
    assert profile.display_name == "ScoutResearchAgent"
    assert profile.default_off is True
    assert profile.local_only is True
    assert profile.fixture_only is True
    assert profile.fake_provider_only is True
    assert profile.live_authority_allowed is False
    assert profile.adk_runner_attached is False
    assert profile.function_tool_attached is False
    assert profile.toolhost_execution_allowed is False
    assert profile.provider_calls_allowed is False
    assert profile.browser_execution_allowed is False
    assert profile.model_calls_allowed is False
    assert profile.workspace_mutation_allowed is False
    assert profile.child_output_requires_runtime_envelope is True
    assert profile.child_summary_is_evidence is False
    assert profile.url_only_citations_allowed is False
    assert profile.raw_source_projection_allowed is False
    assert profile.raw_child_output_projection_allowed is False
    assert profile.adk_tools == ()
    assert tuple(grant.tool_name for grant in profile.tool_grants) == SCOUT_FIXTURE_TOOLS
    assert all(grant.read_only and not grant.mutates_workspace for grant in profile.tool_grants)
    assert all(grant.fixture_only for grant in profile.tool_grants)
    assert all(not grant.live_execution_allowed for grant in profile.tool_grants)
    assert profile.evidence_envelope_contract == "runtime-issued-child-evidence-envelope"
    assert "RepoOverview" in profile.prompt_contract
    assert "verified facts" in profile.prompt_contract


def test_scout_research_profile_rejects_live_tool_names_and_adk_attachment() -> None:
    from pydantic import ValidationError

    from magi_agent.recipes.research_agents import (
        ScoutResearchAgentProfile,
        materialize_scout_research_profile,
    )

    live_tools = materialize_scout_research_profile(
        available_tools=(*SCOUT_FIXTURE_TOOLS, "RepoClone", "WebSearch", "Bash"),
        attach_enabled=False,
    )
    attached = materialize_scout_research_profile(
        available_tools=SCOUT_FIXTURE_TOOLS,
        attach_enabled=True,
    )

    assert tuple(grant.tool_name for grant in live_tools.tool_grants) == SCOUT_FIXTURE_TOOLS
    assert live_tools.adk_tools == ()
    assert attached.adk_tools == ()
    assert attached.attachment_flags["attachEnabled"] is True
    assert attached.attachment_flags["adkFunctionToolsBuilt"] is False
    assert attached.attachment_flags["routeAttached"] is False
    assert attached.attachment_flags["productionAttached"] is False
    assert attached.toolhost_execution_allowed is False

    with pytest.raises(ValidationError, match="fixture-only"):
        materialize_scout_research_profile(
            available_tools=("RepoClone", "RepoOverview", "WebSearch"),
            require_fixture_tools=True,
        )

    with pytest.raises(ValidationError, match="attachmentFlags"):
        ScoutResearchAgentProfile.model_validate(
            {
                **attached.model_dump(by_alias=True),
                "attachmentFlags": {
                    "attachEnabled": True,
                    "adkFunctionToolsBuilt": False,
                    "routeAttached": True,
                    "productionAttached": False,
                    "providerCalled": False,
                    "userVisibleOutputAllowed": False,
                    "writeMutationAllowed": False,
                    "shellExecutionAllowed": False,
                    "toolHostDispatched": True,
                },
            }
        )

    with pytest.raises(ValidationError, match="promptContract"):
        ScoutResearchAgentProfile.model_validate(
            {
                **attached.model_dump(by_alias=True),
                "promptContract": "Read /Users/private/source and include https://example.test",
            }
        )


def test_scout_profile_does_not_add_live_route_type() -> None:
    from magi_agent.recipes.research_agents import materialize_research_agent

    with pytest.raises(ValueError, match="unsupported research route"):
        materialize_research_agent("scout", available_tools=SCOUT_FIXTURE_TOOLS)


def test_direct_route_never_spawns_child_agents() -> None:
    from magi_agent.recipes.research_agents import materialize_research_agent

    decision = materialize_research_agent("direct", available_tools=LOCAL_READ_TOOLS)

    assert decision.status == "direct"
    assert decision.agent_type == "direct"
    assert decision.should_spawn_child_agent is False
    assert decision.granted_tool_names == ()
    assert decision.adk_tools == ()


def test_broad_codebase_route_selects_explore() -> None:
    from magi_agent.harness.research_routing import classify_research_route

    route = classify_research_route(
        "Investigate how auth state flows across the repo and summarize the key files.",
        available_tools=LOCAL_READ_TOOLS,
    )

    assert route.agent_type == "explore"
    assert route.route_reason == "broad_codebase_research"
    assert route.requires_web_tools is False


def test_implementation_planning_route_selects_plan() -> None:
    from magi_agent.harness.research_routing import classify_research_route

    route = classify_research_route(
        "Create an implementation plan and architecture proposal for the retry layer.",
        available_tools=LOCAL_READ_TOOLS,
    )

    assert route.agent_type == "plan"
    assert route.route_reason == "implementation_planning"


def test_known_file_lookup_with_check_and_current_changes_stays_direct() -> None:
    from magi_agent.harness.research_routing import classify_research_route

    generic_check = classify_research_route(
        "Check README.md for the package instructions.",
        available_tools=LOCAL_READ_TOOLS,
    )
    current_changes = classify_research_route(
        "Check current changes under magi-agent/magi_agent/recipes/research_agents.py.",
        available_tools=LOCAL_READ_TOOLS,
    )

    assert generic_check.agent_type == "direct"
    assert generic_check.route_reason == "known_local_lookup"
    assert current_changes.agent_type == "direct"
    assert current_changes.requires_web_tools is False


def test_web_current_route_blocks_with_missing_web_tools_until_web_tools_exist() -> None:
    from magi_agent.harness.research_routing import classify_research_route
    from magi_agent.recipes.research_agents import materialize_research_agent

    route = classify_research_route(
        "Find the latest public facts about the current OpenAI API model lineup.",
        available_tools=LOCAL_READ_TOOLS,
    )

    blocked = materialize_research_agent(route, available_tools=LOCAL_READ_TOOLS)
    ready = materialize_research_agent(route, available_tools=(*LOCAL_READ_TOOLS, "WebSearch"))

    assert route.agent_type == "explore"
    assert route.requires_web_tools is True
    assert blocked.status == "blocked"
    assert blocked.block_reason == "missing_web_tools"
    assert blocked.should_spawn_child_agent is False
    assert ready.status == "ready"
    assert "WebSearch" in ready.granted_tool_names


def test_route_carried_available_tools_are_used_for_materialization() -> None:
    from magi_agent.harness.research_routing import classify_research_route
    from magi_agent.recipes.research_agents import materialize_research_agent

    route = classify_research_route(
        "Find the latest public facts about OpenMagi.",
        available_tools=(*LOCAL_READ_TOOLS, "WebSearch"),
    )

    decision = materialize_research_agent(route)

    assert decision.status == "ready"
    assert "WebSearch" in decision.granted_tool_names


def test_research_contract_payloads_are_deeply_immutable() -> None:
    from magi_agent.recipes.research_agents import materialize_research_agent

    decision = materialize_research_agent("verifier", available_tools=LOCAL_READ_TOOLS)

    assert decision.spec is not None
    try:
        decision.spec.final_output_schema["type"] = "mutated"
    except TypeError:
        pass
    else:  # pragma: no cover - assertion clarity
        raise AssertionError("final output schema must be immutable")

    try:
        decision.attachment_flags["routeAttached"] = True
    except TypeError:
        pass
    else:  # pragma: no cover - assertion clarity
        raise AssertionError("attachment flags must be immutable")

    dumped = decision.model_dump(by_alias=True)
    json_dumped = decision.model_dump_json(by_alias=True)

    assert dumped["spec"]["finalOutputSchema"]["type"] == "object"
    assert dumped["attachmentFlags"]["routeAttached"] is False
    assert "mappingproxy" not in json_dumped


def test_attach_enabled_false_yields_no_adk_tools_even_when_registry_is_available() -> None:
    from magi_agent.recipes.research_agents import materialize_research_agent

    registry = enabled_core_registry(*LOCAL_READ_TOOLS)
    decision = materialize_research_agent(
        "explore",
        available_tools=LOCAL_READ_TOOLS,
        attach_enabled=False,
        registry=registry,
        dispatcher=ToolDispatcher(registry),
        mode="act",
        tool_context_factory=make_context_factory(),
    )

    assert decision.status == "ready"
    assert decision.granted_tool_names == LOCAL_READ_TOOLS
    assert decision.adk_tools == ()


def test_granted_tool_attachment_preserves_grant_order() -> None:
    from magi_agent.adk_bridge.tool_adapter import build_adk_function_tools_for_granted_names

    registry = enabled_core_registry(*LOCAL_READ_TOOLS)

    tools = build_adk_function_tools_for_granted_names(
        registry,
        ToolDispatcher(registry),
        mode="act",
        tool_context_factory=make_context_factory(),
        granted_tool_names=("Grep", "FileRead", "Glob"),
        attach_enabled=True,
    )

    assert [tool.name for tool in tools] == ["Grep", "FileRead", "Glob"]


def test_attach_enabled_true_exposes_only_granted_local_readonly_tools() -> None:
    from magi_agent.recipes.research_agents import materialize_research_agent

    registry = enabled_core_registry(
        *LOCAL_READ_TOOLS,
        "Bash",
        "TestRun",
        "FileWrite",
        "FileEdit",
        "GitDiff",
    )

    decision = materialize_research_agent(
        "explore",
        available_tools=registry.list_available(mode="act"),
        attach_enabled=True,
        registry=registry,
        dispatcher=ToolDispatcher(registry),
        mode="act",
        tool_context_factory=make_context_factory(),
    )

    assert decision.status == "ready"
    assert [tool.name for tool in decision.adk_tools] == list(LOCAL_READ_TOOLS)
    assert set(tool.name for tool in decision.adk_tools).isdisjoint(FORBIDDEN_RESEARCH_TOOLS)


def test_research_route_and_recipe_import_boundaries_do_not_load_adk_or_runtime_surfaces() -> None:
    script = """
import sys
import magi_agent.harness.research_routing  # noqa: F401
import magi_agent.recipes.research_agents  # noqa: F401

forbidden_prefixes = (
    "google.adk",
    "magi_agent.adk_bridge",
    "magi_agent.deploy",
    "magi_agent.runtime",
    "magi_agent.transport",
)
loaded = sorted(
    name for name in sys.modules
    if any(name == prefix or name.startswith(prefix + ".") for prefix in forbidden_prefixes)
)
if loaded:
    raise SystemExit("forbidden imports: " + ", ".join(loaded))
"""

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(sys.path[0]),
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr or result.stdout
