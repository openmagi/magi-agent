# benchmarks/taubench/tau_env.py
"""Translate a τ-bench env's tools into ADK FunctionTools that route to env.step.

No tau_bench import here — the Action constructor is injected (action_factory)."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from benchmarks.taubench.episode import EpisodeState
from benchmarks.taubench.reliability import (
    ReliabilityConfig,
    WriteLedger,
    grounding_prompt,
    looks_like_error,
    validate_args,
)


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
    env: Any,
    *,
    state: EpisodeState,
    action_factory: Callable[..., Any],
    reliability: ReliabilityConfig | None = None,
    ledger: WriteLedger | None = None,
) -> dict[str, Callable]:
    """One async callable per tool. Applies the reliability levers (when enabled),
    then calls env.step(Action(name, kwargs)), records (reward, done) into state,
    and records write outcomes into `ledger`. Returns the observation string.

    Levers fail-open: an exception inside a lever degrades to no-intervention.
    """
    cfg = reliability or ReliabilityConfig()
    led = ledger if ledger is not None else WriteLedger()
    grounding_prompted: set[str] = set()
    callables: dict[str, Callable] = {}
    for spec in _tool_specs(env):
        name = spec["name"]
        params = spec["parameters"]

        def _make(tool_name: str, tool_params: dict) -> Callable:
            async def invoke(arguments: dict, tool_context: object = None) -> str:
                args = dict(arguments or {})
                if cfg.arg_validation:
                    try:
                        message = validate_args(tool_params, args)
                    except Exception:
                        message = None
                    if message:
                        return message
                try:
                    is_write = led.is_write(tool_name)
                except Exception:
                    is_write = False
                if cfg.dup_write_guard and is_write:
                    try:
                        repeat = led.is_repeat_write(tool_name, args)
                    except Exception:
                        repeat = False
                    if repeat:
                        return (
                            f"Duplicate write blocked: '{tool_name}' with these "
                            "arguments already completed successfully. Do not repeat it."
                        )
                if cfg.grounded_args and is_write:
                    try:
                        if tool_name not in grounding_prompted:
                            grounding_prompted.add(tool_name)
                            return grounding_prompt(tool_name, args)
                    except Exception:
                        pass
                try:
                    resp = env.step(action_factory(name=tool_name, kwargs=dict(args)))
                except Exception as exc:  # surface as observation, not infra error
                    if is_write:
                        try:
                            led.record(tool_name, args, ok=False)
                        except Exception:
                            pass
                    return f"Error: {exc}"
                state.observe(resp.reward, resp.done)
                obs = resp.observation
                if is_write:
                    try:
                        led.record(tool_name, args, ok=not looks_like_error(obs))
                    except Exception:
                        pass
                return obs

            invoke.__name__ = tool_name
            return invoke

        callables[name] = _make(name, params)
    return callables


def build_env_function_tools(
    env: Any,
    *,
    state: EpisodeState,
    action_factory: Callable[..., Any],
    reliability: ReliabilityConfig | None = None,
    ledger: WriteLedger | None = None,
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

    callables = build_env_tool_callables(
        env,
        state=state,
        action_factory=action_factory,
        reliability=reliability,
        ledger=ledger,
    )
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
