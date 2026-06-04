"""Concurrent tool dispatcher wrapping the base ToolDispatcher.

This module provides ``ConcurrentToolDispatcher``, which extends the single-call
``ToolDispatcher`` with a ``dispatch_batch`` method that fans out concurrent-safe
tool calls in parallel while keeping exclusive (unsafe) tool calls sequential.

Execution model
---------------
1. ``partition_tool_calls`` splits the incoming call sequence into ordered
   ``ToolBatch`` objects — each batch is either *concurrent* (all calls may
   run simultaneously) or *exclusive* (calls run one at a time).
2. Concurrent batches are dispatched via ``asyncio.gather``, gated by an
   ``asyncio.Semaphore(config.max_concurrency)`` so the fan-out is bounded.
3. Exclusive batches are dispatched sequentially with ``await``.
4. If a single call in a concurrent batch raises an exception it is caught
   and converted to an ``error`` ``ToolResult``; the other calls in the same
   batch are unaffected.
5. When ``config.enabled=False`` (the default) the dispatcher falls back to
   fully sequential execution, preserving the same observable behaviour as a
   plain ``ToolDispatcher``.

Evidence
--------
A ``ToolBatchExecution`` frozen dataclass is produced for every batch that
is executed.  These are not surfaced through the public ``dispatch_batch``
return value — callers that need them should subclass and override
``_record_evidence``.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from magi_agent.telemetry.trace_context import get_trace

from .concurrency import ConcurrencyConfig, ToolBatch, ToolCall, partition_tool_calls
from .context import ToolContext
from .manifest import RuntimeMode
from .result import ToolResult


# ---------------------------------------------------------------------------
# Evidence record
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolBatchExecution:
    """Immutable evidence record for a single batch execution."""

    batch_index: int
    tool_count: int
    is_concurrent: bool
    total_duration_ms: int
    individual_durations: tuple[int, ...]
    tool_names: tuple[str, ...]
    statuses: tuple[str, ...]


# ---------------------------------------------------------------------------
# ConcurrentToolDispatcher
# ---------------------------------------------------------------------------


class ConcurrentToolDispatcher:
    """Wraps a ``ToolDispatcher`` and adds concurrent batch dispatch.

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

    async def dispatch_batch(
        self,
        calls: tuple[ToolCall, ...],
        context: ToolContext,
        *,
        mode: RuntimeMode,
        exposed_tool_names: tuple[str, ...] | None = None,
    ) -> tuple[ToolResult, ...]:
        """Dispatch *calls* with concurrent batching when enabled.

        Parameters
        ----------
        calls:
            Ordered sequence of tool calls to dispatch.
        context:
            Execution context forwarded to each individual dispatch.
        mode:
            Runtime mode (``"plan"`` or ``"act"``).
        exposed_tool_names:
            Optional allowlist of tool names forwarded to the base dispatcher.

        Returns
        -------
        tuple[ToolResult, ...]
            Results in the same order as *calls*.
        """
        if not self._config.enabled or len(calls) <= 1:
            return await self._sequential(
                calls, context, mode=mode, exposed_tool_names=exposed_tool_names
            )

        batches = partition_tool_calls(calls, self._base.registry)  # type: ignore[attr-defined]
        trace = get_trace()
        if trace is not None:
            trace.record(
                "tool",
                "ConcurrentToolDispatcher",
                "partition",
                f"batch_count={len(batches)}, total_calls={len(calls)}",
            )
        results: list[ToolResult] = []

        for batch_index, batch in enumerate(batches):
            batch_start = time.monotonic_ns()
            if trace is not None:
                trace.record(
                    "tool",
                    "ConcurrentToolDispatcher",
                    "batch_start",
                    (
                        f"index={batch_index}, concurrent={batch.is_concurrent}, "
                        f"tool_count={len(batch.calls)}"
                    ),
                )

            if batch.is_concurrent:
                batch_results = await self._execute_concurrent(
                    batch, context, mode=mode, exposed_tool_names=exposed_tool_names
                )
            else:
                batch_results = await self._execute_exclusive(
                    batch, context, mode=mode, exposed_tool_names=exposed_tool_names
                )

            batch_duration_ms = (time.monotonic_ns() - batch_start) // 1_000_000
            if trace is not None:
                trace.record(
                    "tool",
                    "ConcurrentToolDispatcher",
                    "batch_end",
                    (
                        f"index={batch_index}, concurrent={batch.is_concurrent}, "
                        f"tool_count={len(batch.calls)}"
                    ),
                    duration_ms=batch_duration_ms,
                )
            self._record_evidence(
                ToolBatchExecution(
                    batch_index=batch_index,
                    tool_count=len(batch.calls),
                    is_concurrent=batch.is_concurrent,
                    total_duration_ms=batch_duration_ms,
                    individual_durations=tuple(r.duration_ms or 0 for r in batch_results),
                    tool_names=tuple(c.name for c in batch.calls),
                    statuses=tuple(str(r.status) for r in batch_results),
                )
            )
            results.extend(batch_results)

        return tuple(results)

    # ------------------------------------------------------------------
    # Extension point
    # ------------------------------------------------------------------

    def _record_evidence(self, evidence: ToolBatchExecution) -> None:  # noqa: B027
        """Called after each batch completes.  Override to persist evidence."""

    # ------------------------------------------------------------------
    # Private execution helpers
    # ------------------------------------------------------------------

    async def _sequential(
        self,
        calls: tuple[ToolCall, ...],
        context: ToolContext,
        *,
        mode: RuntimeMode,
        exposed_tool_names: tuple[str, ...] | None,
    ) -> tuple[ToolResult, ...]:
        results: list[ToolResult] = []
        for call in calls:
            start = time.monotonic_ns()
            result: ToolResult = await self._base.dispatch(  # type: ignore[attr-defined]
                call.name,
                call.arguments,
                context,
                mode=mode,
                exposed_tool_names=exposed_tool_names,
            )
            duration_ms = (time.monotonic_ns() - start) // 1_000_000
            if result.duration_ms is None:
                result = _with_duration(result, duration_ms)
            results.append(result)
        return tuple(results)

    async def _execute_concurrent(
        self,
        batch: ToolBatch,
        context: ToolContext,
        *,
        mode: RuntimeMode,
        exposed_tool_names: tuple[str, ...] | None,
    ) -> tuple[ToolResult, ...]:
        # TODO(pr14): this semaphore is BATCH-scoped — a fresh
        # asyncio.Semaphore(max_concurrency) is created per concurrent batch, so
        # the cap bounds concurrency *within* one batch, not across overlapping
        # batches. This path is dormant on the live ADK Runner (ADK owns dispatch
        # and never calls dispatch_batch), so there is no live impact today. If
        # dispatch_batch ever becomes reachable, decide whether the cap should be
        # dispatcher-scoped (one shared semaphore on the instance) instead.
        semaphore = asyncio.Semaphore(self._config.max_concurrency)

        async def run_one(call: ToolCall) -> ToolResult:
            async with semaphore:
                start = time.monotonic_ns()
                try:
                    result: ToolResult = await self._base.dispatch(  # type: ignore[attr-defined]
                        call.name,
                        call.arguments,
                        context,
                        mode=mode,
                        exposed_tool_names=exposed_tool_names,
                    )
                except Exception as exc:  # noqa: BLE001
                    result = ToolResult(
                        status="error",
                        error_code="concurrent_dispatch_error",
                        error_message=str(exc),
                    )
                duration_ms = (time.monotonic_ns() - start) // 1_000_000
                if result.duration_ms is None:
                    result = _with_duration(result, duration_ms)
                return result

        raw = await asyncio.gather(
            *(run_one(c) for c in batch.calls),
            return_exceptions=True,
        )
        final: list[ToolResult] = []
        for item in raw:
            if isinstance(item, BaseException):
                final.append(
                    ToolResult(
                        status="error",
                        error_code="concurrent_dispatch_error",
                        error_message=str(item),
                    )
                )
            else:
                final.append(item)
        return tuple(final)

    async def _execute_exclusive(
        self,
        batch: ToolBatch,
        context: ToolContext,
        *,
        mode: RuntimeMode,
        exposed_tool_names: tuple[str, ...] | None,
    ) -> tuple[ToolResult, ...]:
        results: list[ToolResult] = []
        for call in batch.calls:
            start = time.monotonic_ns()
            try:
                result: ToolResult = await self._base.dispatch(  # type: ignore[attr-defined]
                    call.name,
                    call.arguments,
                    context,
                    mode=mode,
                    exposed_tool_names=exposed_tool_names,
                )
            except Exception as exc:  # noqa: BLE001
                result = ToolResult(
                    status="error",
                    error_code="exclusive_dispatch_error",
                    error_message=str(exc),
                )
            duration_ms = (time.monotonic_ns() - start) // 1_000_000
            if result.duration_ms is None:
                result = _with_duration(result, duration_ms)
            results.append(result)
        return tuple(results)


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def _with_duration(result: ToolResult, duration_ms: int) -> ToolResult:
    """Return a new ``ToolResult`` with *duration_ms* set, all other fields preserved."""
    return ToolResult(
        status=result.status,
        output=result.output,
        llm_output=result.llm_output,
        transcript_output=result.transcript_output,
        error_code=result.error_code,
        error_message=result.error_message,
        duration_ms=duration_ms,
        artifact_refs=result.artifact_refs,
        file_refs=result.file_refs,
        delivery_receipts=result.delivery_receipts,
        retryable=result.retryable,
        metadata=result.metadata,
    )
