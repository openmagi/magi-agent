from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from openmagi_core_agent.runtime.control import ControlRequest


class AdkPolicyBoundary(BaseModel):
    model_config = ConfigDict(frozen=True)

    primitive: str
    product_owner: str


class AdkCallbackBoundary(AdkPolicyBoundary):
    @classmethod
    def for_hook_bus(cls) -> "AdkCallbackBoundary":
        return cls(
            primitive="google.adk.agents.callback_context.CallbackContext",
            product_owner="OpenMagi HookBus",
        )


class AdkPluginBoundary(AdkPolicyBoundary):
    @classmethod
    def for_native_plugins(cls) -> "AdkPluginBoundary":
        return cls(
            primitive="google.adk.plugins.base_plugin.BasePlugin",
            product_owner="OpenMagi plugin manifest",
        )


class AdkToolConfirmationBoundary(AdkPolicyBoundary):
    @classmethod
    def for_control_requests(cls) -> "AdkToolConfirmationBoundary":
        return cls(
            primitive="google.adk.tools.FunctionTool(require_confirmation=...)",
            product_owner="OpenMagi ControlRequest",
        )

    def project_control_request(self, request: ControlRequest) -> dict[str, object]:
        return {
            "requestId": request.request_id,
            "toolName": request.tool_name,
            "reason": request.reason,
            "primitive": self.primitive,
            "finalContract": self.product_owner,
        }
