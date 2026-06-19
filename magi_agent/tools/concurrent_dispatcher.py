"""Concurrent tool dispatcher wrapping the base ToolDispatcher.

This module provides ``ConcurrentToolDispatcher``, a drop-in wrapper around the
single-call ``ToolDispatcher`` used by the ADK ``FunctionTool`` adapters. ADK
owns the tool loop and invokes one tool at a time via ``dispatcher.dispatch()``,
so this wrapper forwards a single call to the base dispatcher (whose own
``_should_offload`` / ``_get_offload_semaphore`` seam handles bounded
concurrency for readonly / concurrency-safe handlers).

The dead ``dispatch_batch`` fan-out path (``partition_tool_calls`` →
``ToolBatch`` → ``asyncio.gather``) was deleted in P2.5 (H-5): it had zero live
callers because the live ADK Runner never hands magi a *batch*.
"""
from __future__ import annotations

from .concurrency import ConcurrencyConfig
from .context import ToolContext
from .manifest import RuntimeMode
from .result import ToolResult


# ---------------------------------------------------------------------------
# ConcurrentToolDispatcher
# ---------------------------------------------------------------------------


class ConcurrentToolDispatcher:
    """Wraps a ``ToolDispatcher`` and forwards single-call dispatch.

    Parameters
    ----------
    base_dispatcher:
        The underlying dispatcher used to execute individual tool calls.
        Must expose a ``dispatch`` coroutine method and a ``registry``
        attribute.
    config:
        Concurrency configuration.  Defaults to ``ConcurrencyConfig()``
        (concurrency disabled, max_concurrency=8).
    """

    def __init__(
        self,
        base_dispatcher: object,
        config: ConcurrencyConfig | None = None,
    ) -> None:
        self._base = base_dispatcher
        self._config = config or ConcurrencyConfig()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def registry(self) -> object:
        return self._base.registry  # type: ignore[attr-defined]

    async def dispatch(
        self,
        name: str,
        arguments: dict[str, object],
        context: ToolContext,
        *,
        mode: RuntimeMode,
        exposed_tool_names: tuple[str, ...] | None = None,
    ) -> ToolResult:
        """Dispatch a single tool call to the base dispatcher.

        This method makes ``ConcurrentToolDispatcher`` a drop-in replacement
        for the plain ``ToolDispatcher`` in ADK ``FunctionTool`` wrappers,
        which invoke one tool at a time via ``dispatcher.dispatch()``.

        Parameters
        ----------
        name:
            Tool name to invoke.
        arguments:
            Arguments forwarded verbatim to the tool handler.
        context:
            Execution context for the tool call.
        mode:
            Runtime mode (``"plan"`` or ``"act"``).
        exposed_tool_names:
            Optional allowlist of tool names.

        Returns
        -------
        ToolResult
            The result from the base dispatcher.
        """
        return await self._base.dispatch(  # type: ignore[attr-defined]
            name,
            arguments,
            context,
            mode=mode,
            exposed_tool_names=exposed_tool_names,
        )
