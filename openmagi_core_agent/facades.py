"""High-level entry-point facades that compose existing modules.

Each facade saves the caller >= 3 lines versus calling modules directly
while adding zero duplicated logic.
"""

from __future__ import annotations

from openmagi_core_agent.harness.resolved import ResolvedHarnessPresetState
from openmagi_core_agent.hooks.bus import HookBus, HookBusRunResult
from openmagi_core_agent.hooks.context import HookContext
from openmagi_core_agent.hooks.manifest import HookPoint
from openmagi_core_agent.tools.context import ToolContext
from openmagi_core_agent.tools.dispatcher import ToolDispatcher
from openmagi_core_agent.tools.manifest import RuntimeMode
from openmagi_core_agent.tools.result import ToolResult


async def execute_tool_with_hooks(
    dispatcher: ToolDispatcher,
    hook_bus: HookBus,
    *,
    tool_name: str,
    arguments: dict[str, object],
    context: ToolContext,
    hook_context: HookContext,
    harness_state: ResolvedHarnessPresetState,
    mode: RuntimeMode,
    exposed_tool_names: tuple[str, ...] | None = None,
) -> tuple[ToolResult, HookBusRunResult | None, HookBusRunResult | None]:
    """Tool dispatch through beforeToolUse hooks -> dispatch -> afterToolUse hooks.

    Returns ``(tool_result, before_hook_result, after_hook_result)``.
    If *beforeToolUse* blocks, returns a blocked ``ToolResult`` with the
    *before_hook_result* and ``None`` for after.
    """
    before_result = hook_bus.run(
        point=HookPoint.BEFORE_TOOL_USE,
        context=hook_context,
        harness_state=harness_state,
    )
    if before_result.final_action == "block":
        return (
            ToolResult(status="blocked", metadata={"blocked_by": "beforeToolUse_hook"}),
            before_result,
            None,
        )

    result = await dispatcher.dispatch(
        tool_name, arguments, context, mode=mode, exposed_tool_names=exposed_tool_names
    )

    after_result = hook_bus.run(
        point=HookPoint.AFTER_TOOL_USE,
        context=hook_context,
        harness_state=harness_state,
    )

    return result, before_result, after_result


__all__ = [
    "execute_tool_with_hooks",
]
