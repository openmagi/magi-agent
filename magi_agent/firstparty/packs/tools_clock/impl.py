"""First-party Clock tool provider (no privilege, typed-ctx only).

Receives ONLY the narrow ``ToolProvideContext`` (D5) — identical capability to
any user-authored tool provider — and registers a ``ToolManifest`` via its single
``register`` capability. No god-object, no first-party-only kwarg.
"""
from __future__ import annotations

from magi_agent.packs.context import ToolProvideContext
from magi_agent.tools.catalog import CORE_TOOL_INPUT_SCHEMA, CORE_TOOL_SOURCE
from magi_agent.tools.manifest import Budget, ToolManifest


def provide_clock(context: ToolProvideContext) -> None:
    context.register(
        ToolManifest(
            name="Clock",
            description="Read current time metadata.",
            kind="core",
            source=CORE_TOOL_SOURCE,
            permission="meta",
            input_schema=CORE_TOOL_INPUT_SCHEMA,
            timeout_ms=30_000,
            budget=Budget(max_calls_per_turn=10, max_parallel=1),
            dangerous=False,
            is_concurrency_safe=True,
            mutates_workspace=False,
            parallel_safety="readonly",
            available_in_modes=("plan", "act"),
            tags=("utility", "time", "meta"),
            enabled_by_default=True,
            opt_out=True,
        )
    )
