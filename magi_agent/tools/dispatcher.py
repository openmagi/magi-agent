from __future__ import annotations

import asyncio
import time
from inspect import isawaitable

from magi_agent.evidence.coding_tool_receipts import (
    CodingToolReceiptBoundary,
)
from magi_agent.harness.general_automation.live_gate import (
    GeneralAutomationGateOutcome,
    GeneralAutomationLiveGate,
)
from magi_agent.telemetry.trace_context import get_trace

from .context import ToolContext
from .manifest import RuntimeMode, ToolManifest
from .permission import ToolPermissionPolicy
from .registry import ToolRegistry
from .result import ToolResult
from .schema_validation import validate_tool_arguments


class ToolDispatcher:
    def __init__(
        self,
        registry: ToolRegistry,
        *,
        permission_policy: ToolPermissionPolicy | None = None,
        coding_receipt_boundary: CodingToolReceiptBoundary | None = None,
        general_automation_live_gate: GeneralAutomationLiveGate | None = None,
        readonly_offload_enabled: bool | None = None,
        max_offload_concurrency: int | None = None,
    ) -> None:
        self.registry = registry
        self.permission_policy = permission_policy or ToolPermissionPolicy()
        self._coding_receipt_boundary = coding_receipt_boundary or CodingToolReceiptBoundary()
        self._general_automation_live_gate = (
            general_automation_live_gate or GeneralAutomationLiveGate()
        )
        # PR14 — readonly offload. Google ADK already fans out same-turn function
        # calls concurrently (asyncio.gather over create_task), but magi's
        # readonly handlers are synchronous and block the event loop, so there is
        # no real I/O overlap. When enabled, *readonly / concurrency_safe*
        # synchronous handlers are run via asyncio.to_thread (bounded by a shared
        # semaphore) so that ADK's existing gather produces genuine overlap.
        # Workspace-mutating / unsafe / async handlers always run inline,
        # preserving the write-barrier. Default is read from
        # ``MAGI_TOOL_CONCURRENCY_ENABLED`` (single source of truth in
        # ``magi_agent.config.env``); an explicit value overrides the env (for
        # tests / embedding callers).
        if readonly_offload_enabled is None or max_offload_concurrency is None:
            from magi_agent.config.env import (
                max_tool_concurrency,
                tool_concurrency_enabled,
            )

            import os as _os

            if readonly_offload_enabled is None:
                readonly_offload_enabled = tool_concurrency_enabled(_os.environ)
            if max_offload_concurrency is None:
                max_offload_concurrency = max_tool_concurrency(_os.environ)
        self._readonly_offload_enabled = bool(readonly_offload_enabled)
        self._max_offload_concurrency = max(1, int(max_offload_concurrency))
        # Semaphore is created lazily and keyed on the running event loop so it
        # binds to the correct loop (and survives loop teardown across separate
        # ``asyncio.run`` invocations, e.g. in tests).
        self._offload_semaphore: asyncio.Semaphore | None = None
        self._offload_semaphore_loop: asyncio.AbstractEventLoop | None = None

    async def dispatch(
        self,
        name: str,
        arguments: dict[str, object],
        context: ToolContext,
        *,
        mode: RuntimeMode,
        exposed_tool_names: tuple[str, ...] | None = None,
    ) -> ToolResult:
        registration = self.registry.resolve_registration(name)
        trace = get_trace()
        if trace is not None:
            trace.record("tool", "ToolDispatcher", "resolve", f"name={name}, found={registration is not None}")
        available_tools = _available_tool_names(self.registry, exposed_tool_names, mode=mode)
        if registration is None:
            return ToolResult(
                status="error",
                error_code="tool_not_found",
                error_message="tool not found",
                metadata={
                    "toolName": name,
                    "mode": mode,
                    "reason": "tool not found",
                    "availableTools": available_tools,
                },
            )

        manifest = registration.manifest
        if exposed_tool_names is not None and name not in exposed_tool_names:
            return ToolResult(
                status="error",
                error_code="tool_not_exposed",
                error_message="tool not exposed to this turn",
                metadata={
                    "toolName": manifest.name,
                    "permissionClass": manifest.permission,
                    "mode": mode,
                    "dangerous": manifest.dangerous,
                    "mutatesWorkspace": manifest.mutates_workspace,
                    "reason": "not exposed to this turn",
                    "availableTools": available_tools,
                },
            )

        if not registration.enabled:
            return ToolResult(
                status="blocked",
                metadata={
                    "toolName": manifest.name,
                    "permissionClass": manifest.permission,
                    "mode": mode,
                    "dangerous": manifest.dangerous,
                    "mutatesWorkspace": manifest.mutates_workspace,
                    "reason": "tool disabled",
                },
            )

        if mode not in manifest.available_in_modes:
            return ToolResult(
                status="blocked",
                metadata={
                    "toolName": manifest.name,
                    "permissionClass": manifest.permission,
                    "mode": mode,
                    "dangerous": manifest.dangerous,
                    "mutatesWorkspace": manifest.mutates_workspace,
                    "reason": f"tool unavailable in {mode} mode",
                },
            )

        schema_decision = validate_tool_arguments(manifest, arguments)
        if not schema_decision.valid:
            return ToolResult(
                status="blocked",
                error_code="tool_input_schema_invalid",
                error_message="tool input did not match manifest schema",
                metadata={
                    "toolName": manifest.name,
                    "mode": mode,
                    "reason": "input schema validation failed",
                    "schemaValidation": schema_decision.public_projection(),
                },
            )

        if registration.handler is None:
            return ToolResult(
                status="error",
                error_code="tool_handler_missing",
                error_message="tool handler missing",
                metadata={
                    "toolName": manifest.name,
                    "permissionClass": manifest.permission,
                    "mode": mode,
                    "dangerous": manifest.dangerous,
                    "mutatesWorkspace": manifest.mutates_workspace,
                    "reason": "tool handler missing",
                },
            )

        ga_outcome = self._general_automation_live_gate.classify_pre(
            name, arguments, context, mode=mode
        )
        if ga_outcome.active and ga_outcome.decision != "allow":
            ga_result = _general_automation_gate_result(manifest, mode, ga_outcome)
            if trace is not None:
                trace.record(
                    "tool",
                    "ToolDispatcher",
                    "ga_live_gate",
                    f"name={name}, decision={ga_outcome.decision}",
                )
            return ga_result

        decision = self.permission_policy.decide(manifest, arguments, context, mode=mode)
        if trace is not None:
            trace.record("tool", "ToolDispatcher", "permission_check", f"name={name}, decision={decision.action}")
        if decision.action == "deny":
            return ToolResult(status="blocked", metadata=decision.metadata)
        if decision.action == "ask":
            return ToolResult(status="needs_approval", metadata=decision.metadata)

        _t0 = time.monotonic_ns()
        handler = registration.handler
        if self._should_offload(manifest):
            # Readonly / concurrency_safe synchronous handler: run off the event
            # loop so ADK's same-turn gather actually overlaps the blocking I/O.
            # Bounded by a shared semaphore so the fan-out stays within
            # MAGI_MAX_TOOL_CONCURRENCY. The same permission/path checks above
            # have already run on this call path before we get here.
            semaphore = self._get_offload_semaphore()
            async with semaphore:
                result = await asyncio.to_thread(handler, arguments, context)
            # Defensive: a sync handler that returns an awaitable (rare) must
            # still be awaited — but on the event loop, not in the worker thread.
            if isawaitable(result):
                result = await result
        else:
            result = handler(arguments, context)
            if isawaitable(result):
                result = await result
        if trace is not None:
            _dur = (time.monotonic_ns() - _t0) // 1_000_000
            trace.record("tool", "ToolDispatcher", "execute", f"name={name}, status={result.status}", duration_ms=_dur)
        result = self._attach_coding_receipt(name, arguments, result)
        return result

    def _should_offload(self, manifest: ToolManifest) -> bool:
        """Whether *manifest*'s synchronous handler should run off the event loop.

        Only genuinely-readonly tools are offloaded. The manifest model already
        validates that a ``readonly`` tool cannot be ``dangerous`` or
        ``mutates_workspace`` (see ``ToolManifest`` validation), so offloading
        here can never push a write/exec to a worker thread — the write-barrier
        is structural. Async handlers are excluded because they already yield to
        the loop (``to_thread`` would not await the returned coroutine).
        """
        if not self._readonly_offload_enabled:
            return False
        if manifest.parallel_safety not in ("readonly", "concurrency_safe"):
            return False
        if manifest.mutates_workspace or manifest.dangerous:
            return False
        registration = self.registry.resolve_registration(manifest.name)
        handler = registration.handler if registration is not None else None
        if handler is None or asyncio.iscoroutinefunction(handler):
            return False
        return True

    def _get_offload_semaphore(self) -> asyncio.Semaphore:
        """Lazily create the per-dispatcher offload semaphore for the live loop.

        Bound to the running loop so a dispatcher built outside an event loop
        does not eagerly bind a semaphore to the wrong loop. If the running loop
        differs from the one a cached semaphore was bound to (e.g. a fresh
        ``asyncio.run``), a new semaphore is created.
        """
        loop = asyncio.get_running_loop()
        if self._offload_semaphore is None or self._offload_semaphore_loop is not loop:
            self._offload_semaphore = asyncio.Semaphore(self._max_offload_concurrency)
            self._offload_semaphore_loop = loop
        return self._offload_semaphore

    def _attach_coding_receipt(
        self,
        tool_name: str,
        arguments: dict[str, object],
        result: ToolResult,
    ) -> ToolResult:
        """Attach a coding mutation receipt to the result if applicable.

        Default-off: the boundary returns None unless its config is enabled.
        """
        receipt = self._coding_receipt_boundary.extract_receipt(
            tool_call_id=result.metadata.get("toolCallId", "unknown"),
            tool_name=tool_name,
            arguments=arguments,
            result=result,
        )
        if receipt is None:
            return result
        return ToolResult(
            status=result.status,
            output=result.output,
            llmOutput=result.llm_output,
            transcriptOutput=result.transcript_output,
            errorCode=result.error_code,
            errorMessage=result.error_message,
            durationMs=result.duration_ms,
            artifactRefs=result.artifact_refs,
            fileRefs=result.file_refs,
            deliveryReceipts=result.delivery_receipts,
            retryable=result.retryable,
            metadata=result.metadata,
            codingMutationReceipt=receipt.public_projection(),
        )


def _general_automation_gate_result(
    manifest: ToolManifest,
    mode: RuntimeMode,
    outcome: GeneralAutomationGateOutcome,
) -> ToolResult:
    """Project a GA live-gate deny/ask outcome onto a ToolResult.

    ``deny`` reuses the existing permission-denied path (``status="blocked"``);
    ``ask`` reuses the ``pending_control_request`` path (``status="needs_approval"``)
    and surfaces the control projection + the gated receipt as result metadata so
    downstream consumers can record the evidence. The classifiers are never
    bypassed — this only *projects* their decision onto the existing control flow.
    """
    metadata: dict[str, object] = {
        "toolName": manifest.name,
        "permissionClass": manifest.permission,
        "mode": mode,
        "dangerous": manifest.dangerous,
        "mutatesWorkspace": manifest.mutates_workspace,
        "reason": outcome.reason or f"general_automation_{outcome.decision}",
        "generalAutomationLiveGate": True,
    }
    if outcome.permission_boundary is not None:
        metadata["permissionBoundary"] = outcome.permission_boundary.model_dump()
    if outcome.control_projection is not None:
        metadata["controlProjection"] = outcome.control_projection.public_projection()
    if outcome.receipt is not None:
        metadata["generalAutomationReceipt"] = outcome.receipt.public_projection()

    if outcome.decision == "deny":
        return ToolResult(status="blocked", metadata=metadata)
    return ToolResult(status="needs_approval", metadata=metadata)


def _available_tool_names(
    registry: ToolRegistry,
    exposed_tool_names: tuple[str, ...] | None,
    *,
    mode: RuntimeMode,
) -> tuple[str, ...]:
    if exposed_tool_names is not None:
        return tuple(sorted(dict.fromkeys(exposed_tool_names)))
    return tuple(tool.name for tool in registry.list_available(mode=mode))
