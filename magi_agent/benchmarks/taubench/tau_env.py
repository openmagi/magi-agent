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
                resp = env.step(action_factory(tool_name, dict(arguments or {})))
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

    MIRROR `magi_agent/adk_bridge/tool_adapter.py`:
    - reuse `_json_schema_to_genai_schema(spec["parameters"])` for the declaration,
    - set `invoke.__doc__ = description`,
    - wrap via `google.adk.tools.FunctionTool(invoke, require_confirmation=False)`.
    CONFIRM the exact way tool_adapter attaches the explicit genai Schema to the
    FunctionTool (it does so for core tools) and replicate it here. Imports of
    google.adk / google.genai stay inside this function (cold-start discipline)."""
    from google.adk.tools import FunctionTool  # noqa: PLC0415

    callables = build_env_tool_callables(env, state=state, action_factory=action_factory)
    specs = {s["name"]: s for s in _tool_specs(env)}
    tools: list[object] = []
    for name, invoke in callables.items():
        invoke.__doc__ = specs[name]["description"]
        tools.append(FunctionTool(invoke, require_confirmation=False))
    return tools
