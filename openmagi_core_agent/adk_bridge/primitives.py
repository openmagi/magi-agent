from __future__ import annotations

import inspect
from importlib import import_module
from typing import ClassVar

from pydantic import BaseModel, ConfigDict


class AdkPrimitiveBoundary(BaseModel):
    """Import-only boundary for official Google ADK primitives.

    Phase 1 deliberately does not instantiate or run these primitives. The
    fields document the official attachment points future adapters must use.
    """

    model_config = ConfigDict(frozen=True)

    REQUIRED: ClassVar[dict[str, tuple[str, str]]] = {
        "agent": ("google.adk.agents", "Agent"),
        "runner": ("google.adk.runners", "Runner"),
        "function_tool": ("google.adk.tools", "FunctionTool"),
        "long_running_function_tool": ("google.adk.tools", "LongRunningFunctionTool"),
        "session_service": ("google.adk.sessions", "BaseSessionService"),
        "memory_service": ("google.adk.memory", "BaseMemoryService"),
        "artifact_service": ("google.adk.artifacts", "BaseArtifactService"),
        "evaluator": ("google.adk.evaluation", "AgentEvaluator"),
        "callback_context": ("google.adk.agents.callback_context", "CallbackContext"),
        "plugin_base": ("google.adk.plugins.base_plugin", "BasePlugin"),
    }

    available: bool
    invoked: bool = False
    missing: tuple[str, ...] = ()
    agent: str | None = None
    runner: str | None = None
    function_tool: str | None = None
    long_running_function_tool: str | None = None
    session_service: str | None = None
    memory_service: str | None = None
    artifact_service: str | None = None
    evaluator: str | None = None
    function_tool_confirmation: str | None = None
    callback_context: str | None = None
    plugin_base: str | None = None

    @classmethod
    def declared(cls) -> "AdkPrimitiveBoundary":
        values = {
            field: f"{module_name}.{attr_name}"
            for field, (module_name, attr_name) in cls.REQUIRED.items()
        }
        values["function_tool_confirmation"] = "google.adk.tools.FunctionTool(require_confirmation=...)"
        return cls(available=True, **values)

    @classmethod
    def inspect(cls) -> "AdkPrimitiveBoundary":
        values: dict[str, str] = {}
        missing: list[str] = []
        for field, (module_name, attr_name) in cls.REQUIRED.items():
            try:
                module = import_module(module_name)
                getattr(module, attr_name)
                values[field] = f"{module_name}.{attr_name}"
            except Exception:
                missing.append(f"{module_name}.{attr_name}")

        try:
            tools_module = import_module("google.adk.tools")
            function_tool = getattr(tools_module, "FunctionTool")
            signature = inspect.signature(function_tool)
            if "require_confirmation" in signature.parameters:
                values["function_tool_confirmation"] = (
                    "google.adk.tools.FunctionTool(require_confirmation=...)"
                )
            else:
                missing.append("google.adk.tools.FunctionTool(require_confirmation=...)")
        except Exception:
            missing.append("google.adk.tools.FunctionTool(require_confirmation=...)")

        return cls(available=not missing, missing=tuple(missing), **values)
