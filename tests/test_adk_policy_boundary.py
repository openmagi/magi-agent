from magi_agent.adk_bridge.policy_boundary import (
    AdkCallbackBoundary,
    AdkPluginBoundary,
    AdkToolConfirmationBoundary,
)
from magi_agent.runtime.control import ControlRequest


def test_adk_boundaries_reference_official_primitives_without_owning_product_policy() -> None:
    callback = AdkCallbackBoundary.for_hook_bus()
    plugin = AdkPluginBoundary.for_native_plugins()
    confirmation = AdkToolConfirmationBoundary.for_control_requests()

    assert callback.primitive == "google.adk.agents.callback_context.CallbackContext"
    assert callback.product_owner == "OpenMagi HookBus"
    assert plugin.primitive == "google.adk.plugins.base_plugin.BasePlugin"
    assert plugin.product_owner == "OpenMagi plugin manifest"
    assert confirmation.primitive == "google.adk.tools.FunctionTool(require_confirmation=...)"
    assert confirmation.product_owner == "OpenMagi ControlRequest"


def test_adk_tool_confirmation_boundary_keeps_control_request_as_final_contract() -> None:
    control_request = ControlRequest(
        request_id="ctrl-1",
        turn_id="turn-1",
        tool_name="Bash",
        arguments={"command": "rm -rf /tmp/example"},
        reason="dangerous command",
    )

    confirmation = AdkToolConfirmationBoundary.for_control_requests()
    projection = confirmation.project_control_request(control_request)

    assert projection["requestId"] == "ctrl-1"
    assert projection["toolName"] == "Bash"
    assert projection["primitive"] == "google.adk.tools.FunctionTool(require_confirmation=...)"
    assert projection["finalContract"] == "OpenMagi ControlRequest"
    assert "arguments" not in projection
