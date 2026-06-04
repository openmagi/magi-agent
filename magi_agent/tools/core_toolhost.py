from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from .context import ToolContext
from .registry import ToolRegistry
from .result import ToolResult


CORE_TOOLHOST_DIRECT_TOOL_NAMES = (
    "Clock",
    "Calculation",
    "FileRead",
    "Glob",
    "Grep",
    "FileWrite",
    "FileEdit",
    "PatchApply",
    "Bash",
)


class CoreToolhostHandlerSet:
    """Bind the built-in Gate 5B local tool implementations to a registry."""

    def __init__(
        self,
        *,
        allowed_tool_names: tuple[str, ...] = CORE_TOOLHOST_DIRECT_TOOL_NAMES,
        max_tool_calls_per_turn: int = 64,
        command_timeout_ms: int = 5000,
        max_per_tool_output_bytes: int = 8192,
        read_quality_enabled: bool = False,
        read_ledger_enabled: bool = True,
        apply_patch_enabled: bool = True,
    ) -> None:
        self.allowed_tool_names = allowed_tool_names
        self.read_ledger_enabled = read_ledger_enabled
        self._config: dict[str, object] = {
            "enabled": True,
            "killSwitchEnabled": False,
            "routeAttachmentEnabled": True,
            "environment": "local",
            "environmentAllowlist": ("local",),
            "allowedToolNames": allowed_tool_names,
            "maxToolCallsPerTurn": max_tool_calls_per_turn,
            "maxPerToolOutputBytes": max_per_tool_output_bytes,
            "commandTimeoutMs": command_timeout_ms,
            "readQualityEnabled": read_quality_enabled,
            "applyPatchEnabled": apply_patch_enabled,
        }
        self._hosts: dict[tuple[str, str], Any] = {}

    def bind(self, registry: ToolRegistry) -> tuple[str, ...]:
        bound: list[str] = []
        for name in self.allowed_tool_names:
            registration = registry.resolve_registration(name)
            if registration is None or registration.handler is not None:
                continue
            registry.bind_handler(
                name,
                self._handler_for(name),
                enabled_by_registry_policy=True,
            )
            bound.append(name)
        return tuple(bound)

    def _handler_for(self, tool_name: str):
        async def handler(arguments: dict[str, object], context: ToolContext) -> ToolResult:
            workspace_root = _workspace_root(context)
            host = self._host_for(workspace_root, context)
            request_digest = _request_digest(context)
            tool_call_id = _tool_call_id(tool_name, arguments, context)
            outcome = await host.dispatch(
                tool_name,
                arguments,
                request_digest=request_digest,
                tool_call_id=tool_call_id,
            )
            return _tool_result_from_outcome(outcome)

        return handler

    def _host_for(self, workspace_root: Path, context: ToolContext) -> Any:
        key = (str(workspace_root), context.turn_id or context.session_id or "local-turn")
        host = self._hosts.get(key)
        if host is None:
            from magi_agent.gates.gate5b_full_toolhost import (
                Gate5BFullToolHost,
                Gate5BFullToolHostConfig,
            )

            host = Gate5BFullToolHost(
                config=Gate5BFullToolHostConfig.model_validate(self._config),
                workspace_root=workspace_root,
                exposed_tool_names=self.allowed_tool_names,
                now_ms=lambda: 0,
                read_ledger_enabled=self.read_ledger_enabled,
            )
            self._hosts[key] = host
        return host


def bind_core_toolhost_handlers(registry: ToolRegistry) -> tuple[str, ...]:
    """Attach local first-party core tool handlers to ``registry``.

    The plain catalog remains metadata-only. Runtime builders call this helper
    when they want the default local Magi Agent toolhost to execute core tools.
    """

    return CoreToolhostHandlerSet().bind(registry)


def _workspace_root(context: ToolContext) -> Path:
    raw = context.workspace_root or os.getcwd()
    root = Path(raw).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _request_digest(context: ToolContext) -> str:
    return _digest(
        {
            "botId": context.bot_id,
            "sessionId": context.session_id,
            "turnId": context.turn_id,
            "traceId": context.trace_id,
        }
    )


def _tool_call_id(
    tool_name: str,
    arguments: dict[str, object],
    context: ToolContext,
) -> str:
    if context.tool_use_id:
        return context.tool_use_id
    return f"core-toolhost:{tool_name}:{_digest(arguments)[7:23]}"


def _tool_result_from_outcome(outcome) -> ToolResult:
    receipt = outcome.receipt.model_dump(by_alias=True, mode="json", warnings=False)
    metadata: dict[str, object] = {
        "toolName": outcome.receipt.tool_name,
        "reason": outcome.reason,
        "gate5bFullToolhostReceipt": receipt,
    }
    if outcome.coding_mutation_receipt is not None:
        metadata["codingMutationReceipt"] = outcome.coding_mutation_receipt.public_projection()
    if outcome.code_diagnostics_receipt is not None:
        metadata["codeDiagnosticsReceipt"] = outcome.code_diagnostics_receipt.public_projection()
    if outcome.status == "ok":
        return ToolResult(
            status="ok",
            output=outcome.output_preview,
            metadata=metadata,
            codingMutationReceipt=outcome.coding_mutation_receipt,
        )
    if outcome.status == "duplicate":
        metadata["duplicateToolCall"] = True
        return ToolResult(status="ok", output=outcome.output_preview, metadata=metadata)
    if outcome.status == "blocked":
        return ToolResult(
            status="blocked",
            errorCode=outcome.reason,
            errorMessage=outcome.reason,
            metadata=metadata,
        )
    return ToolResult(
        status="error",
        errorCode=outcome.reason,
        errorMessage=outcome.reason,
        output=outcome.output_preview,
        metadata=metadata,
        codingMutationReceipt=outcome.coding_mutation_receipt,
    )


def _digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = [
    "CORE_TOOLHOST_DIRECT_TOOL_NAMES",
    "CoreToolhostHandlerSet",
    "bind_core_toolhost_handlers",
]
