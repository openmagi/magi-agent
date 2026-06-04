import asyncio
from importlib.util import find_spec

from magi_agent.adk_bridge.primitives import AdkPrimitiveBoundary
from magi_agent.config.models import BuildInfo, RuntimeConfig
from magi_agent.harness.profiles import DEFAULT_PROFILE_NAME
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime
from magi_agent.tools import ToolDispatcher
from magi_agent.tools.context import ToolContext


def make_config() -> RuntimeConfig:
    return RuntimeConfig(
        bot_id="bot-test",
        user_id="user-test",
        gateway_token="gateway-token",
        api_proxy_url="http://api-proxy.local",
        chat_proxy_url="http://chat-proxy.local",
        redis_url="redis://redis.local:6379/0",
        model="gpt-5.2",
        build=BuildInfo(version="0.1.0-adk-scaffold", build_sha="sha-test"),
    )


def test_adk_dependency_boundary_is_available_without_invoking_runner() -> None:
    assert find_spec("google.adk") is not None

    runtime = OpenMagiRuntime(config=make_config())

    assert runtime.adk_invocation_enabled is False
    assert runtime.status()["adk"] == {"available": True, "invoked": False}


def test_adk_primitive_boundary_names_official_future_attachment_points() -> None:
    boundary = AdkPrimitiveBoundary.inspect()

    assert boundary.available is True
    assert boundary.invoked is False
    assert boundary.agent == "google.adk.agents.Agent"
    assert boundary.runner == "google.adk.runners.Runner"
    assert boundary.function_tool == "google.adk.tools.FunctionTool"
    assert boundary.long_running_function_tool == "google.adk.tools.LongRunningFunctionTool"
    assert boundary.session_service == "google.adk.sessions.BaseSessionService"
    assert boundary.memory_service == "google.adk.memory.BaseMemoryService"
    assert boundary.artifact_service == "google.adk.artifacts.BaseArtifactService"
    assert boundary.evaluator == "google.adk.evaluation.AgentEvaluator"
    assert (
        boundary.function_tool_confirmation
        == "google.adk.tools.FunctionTool(require_confirmation=...)"
    )
    assert boundary.callback_context == "google.adk.agents.callback_context.CallbackContext"
    assert boundary.plugin_base == "google.adk.plugins.base_plugin.BasePlugin"


def test_runtime_owns_profile_and_exposes_first_party_tools_by_default() -> None:
    runtime = OpenMagiRuntime(config=make_config())

    assert runtime.profile.name == DEFAULT_PROFILE_NAME
    active_tools = set(runtime.list_active_tools())
    assert "FileRead" in active_tools
    assert "FileWrite" in active_tools
    assert "KnowledgeSearch" in active_tools
    assert "DocumentWrite" in active_tools
    assert "WebSearch" in active_tools
    assert "Browser" in active_tools
    assert "MissionLedger" in active_tools
    assert "CodeDiagnostics" in active_tools
    assert "SkillLoader" in active_tools
    assert "TaskBoard" in active_tools


def test_runtime_binds_default_first_party_native_tool_handlers() -> None:
    runtime = OpenMagiRuntime(config=make_config())
    context = ToolContext(
        bot_id="bot-test",
        turn_id="turn-test",
        workspace_root="/tmp/magi-agent-test-workspace",
        permission_scope={
            "mode": "selected_full_toolhost",
            "source": "selected_full_toolhost",
        },
    )

    remember_result = asyncio.run(
        ToolDispatcher(runtime.tool_registry).dispatch(
            "AgentMemoryRemember",
            {"content": "Prefer concise status updates."},
            context,
            mode="act",
        )
    )
    search_result = asyncio.run(
        ToolDispatcher(runtime.tool_registry).dispatch(
            "AgentMemorySearch",
            {"query": "status updates"},
            context,
            mode="plan",
        )
    )
    diagnostics_result = asyncio.run(
        ToolDispatcher(runtime.tool_registry).dispatch(
            "CodeDiagnostics",
            {},
            context,
            mode="plan",
        )
    )
    skill_result = asyncio.run(
        ToolDispatcher(runtime.tool_registry).dispatch(
            "SkillLoader",
            {},
            context,
            mode="plan",
        )
    )
    taskboard_result = asyncio.run(
        ToolDispatcher(runtime.tool_registry).dispatch(
            "TaskBoard",
            {"action": "list"},
            context,
            mode="plan",
        )
    )
    web_result = asyncio.run(
        ToolDispatcher(runtime.tool_registry).dispatch(
            "WebSearch",
            {"query": "Open Magi"},
            context,
            mode="act",
        )
    )
    browser_result = asyncio.run(
        ToolDispatcher(runtime.tool_registry).dispatch(
            "Browser",
            {"url": "https://example.com"},
            context,
            mode="act",
        )
    )
    document_result = asyncio.run(
        ToolDispatcher(runtime.tool_registry).dispatch(
            "DocumentWrite",
            {"path": "docs/report.md", "content": "hello"},
            context,
            mode="act",
        )
    )

    assert remember_result.status == "ok"
    assert remember_result.output is not None
    assert remember_result.metadata["toolName"] == "AgentMemoryRemember"
    assert remember_result.output["pathRef"] == ".magi/agentmemory.jsonl"
    assert search_result.status == "ok"
    assert search_result.output is not None
    assert search_result.metadata["toolName"] == "AgentMemorySearch"
    assert search_result.output["query"] == "status updates"
    assert diagnostics_result.status == "ok"
    assert diagnostics_result.output is not None
    assert diagnostics_result.output["checker"] == "local_static_inventory"
    assert skill_result.status == "ok"
    assert skill_result.output is not None
    assert skill_result.output["skillCount"] >= 14
    bundled_skills = set(skill_result.output["skills"])
    assert "bundled/superpowers/using-superpowers/SKILL.md" in bundled_skills
    assert "bundled/superpowers/systematic-debugging/SKILL.md" in bundled_skills
    assert "bundled/superpowers/test-driven-development/SKILL.md" in bundled_skills
    assert "bundled/superpowers/verification-before-completion/SKILL.md" in bundled_skills
    assert taskboard_result.status == "ok"
    assert taskboard_result.output is not None
    assert taskboard_result.output["pathRef"] == ".magi/taskboard.jsonl"
    assert web_result.status == "ok"
    assert web_result.output is not None
    assert web_result.output["query"] == "Open Magi"
    assert browser_result.status == "ok"
    assert browser_result.output is not None
    assert browser_result.metadata["toolName"] == "Browser"
    assert document_result.status == "ok"
    assert document_result.output is not None
    assert document_result.output["pathRef"] == "docs/report.md"
