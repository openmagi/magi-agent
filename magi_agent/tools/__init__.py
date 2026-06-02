from .base import ToolArguments, ToolHandler, ToolRegistration
from .manifest import Budget, ToolManifest, ToolSource
from .permission import ToolPermissionDecision, ToolPermissionPolicy
from .result import ToolResult

__all__ = [
    "Budget",
    "ToolArguments",
    "ToolDispatcher",
    "ToolHandler",
    "ToolManifest",
    "ToolPermissionDecision",
    "ToolPermissionPolicy",
    "ToolRegistration",
    "ToolRegistry",
    "ToolResult",
    "ToolSource",
    "core_tool_manifests",
    "register_core_tool_manifests",
]


def __getattr__(name: str) -> object:
    if name == "ToolDispatcher":
        from .dispatcher import ToolDispatcher

        return ToolDispatcher
    if name == "ToolRegistry":
        from .registry import ToolRegistry

        return ToolRegistry
    if name in {"core_tool_manifests", "register_core_tool_manifests"}:
        from .catalog import core_tool_manifests, register_core_tool_manifests

        exports = {
            "core_tool_manifests": core_tool_manifests,
            "register_core_tool_manifests": register_core_tool_manifests,
        }
        return exports[name]
    raise AttributeError(name)
