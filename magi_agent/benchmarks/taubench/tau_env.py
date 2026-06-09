# magi_agent/benchmarks/taubench/tau_env.py
"""Translate a τ-bench env's tools into ADK FunctionTools that route to env.step.

No tau_bench import here — the Action constructor is injected (action_factory)."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from magi_agent.benchmarks.taubench.episode import EpisodeState


def _tool_specs(env: Any) -> list[dict]:
    specs = []
    for entry in env.tools_info:
        fn = entry.get("function", entry) if isinstance(entry, dict) else {}
        name = fn.get("name")
        if name:
            specs.append({"name": name, "description": fn.get("description", ""),
                          "parameters": fn.get("parameters", {"type": "object", "properties": {}})})
    return specs


def build_env_tool_callables(
    env: Any, *, state: EpisodeState, action_factory: Callable[[str, dict], Any]
) -> dict[str, Callable]:
    """One async callable per tool. Each calls env.step(Action(name, kwargs)),
    records (reward, done) into state, returns the observation."""
    callables: dict[str, Callable] = {}
    for spec in _tool_specs(env):
        name = spec["name"]

        def _make(tool_name: str) -> Callable:
            async def invoke(arguments: dict, tool_context: object = None) -> str:
                try:
                    resp = env.step(action_factory(tool_name, dict(arguments or {})))
                except Exception as exc:  # surface to the agent as an observation, not an infra error
                    return f"Error: {exc}"
                state.observe(resp.reward, resp.done)
                return resp.observation
            invoke.__name__ = tool_name
            return invoke

        callables[name] = _make(name)
    return callables


def build_env_function_tools(
    env: Any, *, state: EpisodeState, action_factory: Callable[[str, dict], Any]
) -> list[object]:
    """Wrap each callable as an ADK FunctionTool with the τ-bench parameter schema.

    Mirrors ``magi_agent/adk_bridge/tool_adapter.py`` ``_enrich_arguments_schema``:
    after building a FunctionTool whose invoke signature has a generic
    ``arguments`` parameter, monkey-patches ``_get_declaration`` so that the
    ``arguments`` property in the ADK declaration is replaced with the real
    τ-bench parameter schema.  This exposes the true parameter names (e.g.
    ``id``) to the model instead of an opaque ``OBJECT``.

    Imports of google.adk / google.genai stay inside this function
    (cold-start discipline)."""
    from google.adk.tools import FunctionTool  # noqa: PLC0415

    from magi_agent.adk_bridge.tool_adapter import _json_schema_to_genai_schema  # noqa: PLC0415

    callables = build_env_tool_callables(env, state=state, action_factory=action_factory)
    specs = {s["name"]: s for s in _tool_specs(env)}
    tools: list[object] = []
    for name, invoke in callables.items():
        invoke.__doc__ = specs[name]["description"]
        tool: Any = FunctionTool(invoke, require_confirmation=False)

        # Enrich the ADK declaration so the model sees real parameter names
        # rather than a single opaque ``arguments: OBJECT``.
        param_schema = _json_schema_to_genai_schema(specs[name]["parameters"])
        base_get_declaration = tool._get_declaration  # type: ignore[attr-defined]

        def _make_enriched(base: Any = base_get_declaration, schema: Any = param_schema) -> Any:
            def _enriched() -> Any:
                decl = base()
                if decl is None:
                    return None
                params = getattr(decl, "parameters", None)
                props = getattr(params, "properties", None) if params is not None else None
                if isinstance(props, dict) and "arguments" in props:
                    props["arguments"] = schema
                return decl

            return _enriched

        tool._get_declaration = _make_enriched()  # type: ignore[method-assign,attr-defined]
        tools.append(tool)
    return tools
