from importlib.util import find_spec

from openmagi_core_agent.adk_bridge.primitives import AdkPrimitiveBoundary
from openmagi_core_agent.config.models import BuildInfo, RuntimeConfig
from openmagi_core_agent.harness.profiles import DEFAULT_PROFILE_NAME
from openmagi_core_agent.runtime.openmagi_runtime import OpenMagiRuntime


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


def test_runtime_owns_profile_and_does_not_register_executable_tools_yet() -> None:
    runtime = OpenMagiRuntime(config=make_config())

    assert runtime.profile.name == DEFAULT_PROFILE_NAME
    assert runtime.list_active_tools() == []
