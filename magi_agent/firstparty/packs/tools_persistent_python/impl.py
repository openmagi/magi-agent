"""First-party PersistentPython tool provider (no privilege, typed-ctx only).

Receives ONLY the narrow ``ToolProvideContext`` (D5) — identical capability to
any user-authored tool provider — and registers a ``ToolManifest`` via its single
``register`` capability. No god-object, no first-party-only kwarg. Modeled on
``magi_agent/firstparty/packs/tools_clock/impl.py``.

This declares the CodeAct lever: a Python tool whose interpreter namespace
persists across steps in a turn so one richer code step composes many operations
(load/parse/loop/cross-reference) instead of one stateless shell call per step.
Pure declaration — the execution lives in the additive first-party toolhost
binder ``magi_agent.tools.persistent_python_toolhost`` (the handler-binding seam
is still first-party today; a pack-authored handler is a future authoring-ABI
gap). Removable via ``config.toml [packs] disable``.
"""
from __future__ import annotations

from magi_agent.packs.context import ToolProvideContext
from magi_agent.tools.catalog import CORE_TOOL_SOURCE
from magi_agent.tools.manifest import Budget, ToolManifest

PERSISTENT_PYTHON_TOOL_NAME = "PersistentPython"

_DESCRIPTION = (
    "Run Python code in a persistent interpreter namespace: variables, imports, "
    "and loaded data survive across calls within the same turn/session. Use "
    "print() for output; the value of a final bare expression is also returned. "
    "Output is head+tail capped. Prefer ONE richer code step that loads data and "
    "computes the result over many small tool calls; base your answer on the "
    "printed program output."
)

# Input schema = a single required string ``code`` (per the Step B spec).
_INPUT_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "code": {
            "type": "string",
            "description": (
                "Python source to execute in the persistent session namespace."
            ),
        },
    },
    "required": ["code"],
}


def provide_persistent_python(context: ToolProvideContext) -> None:
    context.register(
        ToolManifest(
            name=PERSISTENT_PYTHON_TOOL_NAME,
            description=_DESCRIPTION,
            kind="core",
            source=CORE_TOOL_SOURCE,
            permission="execute",
            input_schema=_INPUT_SCHEMA,
            timeout_ms=120_000,
            budget=Budget(max_calls_per_turn=64, max_parallel=1),
            dangerous=True,
            is_concurrency_safe=False,
            mutates_workspace=True,
            # Exclusive / non-readonly: stateful interpreter, never offloaded.
            parallel_safety="unsafe",
            available_in_modes=("act",),
            tags=("workspace", "code", "execute", "requires-approval"),
            enabled_by_default=True,
            opt_out=True,
        )
    )


__all__ = ["PERSISTENT_PYTHON_TOOL_NAME", "provide_persistent_python"]
