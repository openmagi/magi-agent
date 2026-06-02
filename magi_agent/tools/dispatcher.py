from __future__ import annotations

import time
from inspect import isawaitable

from magi_agent.evidence.coding_tool_receipts import (
    CodingToolReceiptBoundary,
)
from magi_agent.telemetry.trace_context import get_trace

from .context import ToolContext
from .manifest import RuntimeMode
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
    ) -> None:
        self.registry = registry
        self.permission_policy = permission_policy or ToolPermissionPolicy()
        self._coding_receipt_boundary = coding_receipt_boundary or CodingToolReceiptBoundary()

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

        decision = self.permission_policy.decide(manifest, arguments, context, mode=mode)
        if trace is not None:
            trace.record("tool", "ToolDispatcher", "permission_check", f"name={name}, decision={decision.action}")
        if decision.action == "deny":
            return ToolResult(status="blocked", metadata=decision.metadata)
        if decision.action == "ask":
            return ToolResult(status="needs_approval", metadata=decision.metadata)

        _t0 = time.monotonic_ns()
        result = registration.handler(arguments, context)
        if isawaitable(result):
            result = await result
        if trace is not None:
            _dur = (time.monotonic_ns() - _t0) // 1_000_000
            trace.record("tool", "ToolDispatcher", "execute", f"name={name}, status={result.status}", duration_ms=_dur)
        result = self._attach_coding_receipt(name, arguments, result)
        return result

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


def _available_tool_names(
    registry: ToolRegistry,
    exposed_tool_names: tuple[str, ...] | None,
    *,
    mode: RuntimeMode,
) -> tuple[str, ...]:
    if exposed_tool_names is not None:
        return tuple(sorted(dict.fromkeys(exposed_tool_names)))
    return tuple(tool.name for tool in registry.list_available(mode=mode))
