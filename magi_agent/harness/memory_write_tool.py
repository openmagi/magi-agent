"""MemoryWriteToolHost — agent-callable tool surface for declarative memory writes (D2).

This module binds the ``MemoryWrite`` tool handler to a registry.  The tool
is gated default-OFF at both:

  1. Registry level: ``MemoryWrite`` manifest has ``enabled_by_default=False``
     — the tool is not presented to the model unless the registry enables it.
  2. Handler level: ``MemoryWriteToolHostConfig.enabled=False`` (default) →
     every call returns ``blocked`` immediately.

When both gates are open AND a writable ``LocalFileMemoryProvider`` is injected,
the handler flows through the write boundary → declarative filter → real or
simulated persistence.

Governance
----------
- Do NOT flip ``enabled=True`` in production without also setting
  ``MAGI_MEMORY_WRITE_ENABLED=1`` in the environment.
- Real persistence requires BOTH the env gate AND an injected provider.
- Default: simulated / local-fake path only.
"""
from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from magi_agent.harness.memory_write import (
    MemoryWriteHarness,
    MemoryWriteHarnessConfig,
    MemoryWritePolicy,
    MemoryWriteRequest,
)
from magi_agent.tools.context import ToolContext
from magi_agent.tools.registry import ToolRegistry
from magi_agent.tools.result import ToolResult

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
)

_DEFAULT_POLICY_REF = "policy:memory-write-tool-d2"
_DEFAULT_POLICY_SNAP = "policy:memory-write-tool-d2-snap"
_DEFAULT_TURN_ID = "tool-turn-d2"


class MemoryWriteToolHostConfig(BaseModel):
    """Configuration for the MemoryWriteToolHost gate.

    ``enabled=False`` (default): every ``MemoryWrite`` call is immediately
    blocked at the handler level regardless of the registry enable state.

    ``enabled=True``: calls flow through the write harness.  Real persistence
    additionally requires ``MAGI_MEMORY_WRITE_ENABLED=1`` in the environment
    AND an injected writable ``LocalFileMemoryProvider``.
    """

    model_config = _MODEL_CONFIG

    enabled: bool = False
    evidence_required: bool = Field(default=False, alias="evidenceRequired")
    local_fake_success_allowed: bool = Field(default=True, alias="localFakeSuccessAllowed")


class MemoryWriteToolHost:
    """Bind the ``MemoryWrite`` tool handler to a ToolRegistry.

    Parameters
    ----------
    config:
        Gate configuration.  Defaults to all-OFF.
    provider:
        Optional ``LocalFileMemoryProvider`` instance.  When provided AND the
        env gate is set, real writes are attempted.  When not provided, the
        write boundary falls back to the simulated path.
    """

    def __init__(
        self,
        config: MemoryWriteToolHostConfig | Mapping[str, object] | None = None,
        *,
        provider: object | None = None,
    ) -> None:
        self.config = (
            config
            if isinstance(config, MemoryWriteToolHostConfig)
            else MemoryWriteToolHostConfig.model_validate(config or {})
        )
        self.provider = provider

    def bind(self, registry: ToolRegistry) -> None:
        """Bind the MemoryWrite handler to the registry.

        The handler is ALWAYS bound (so an execution-time dispatch returns a
        structured ``blocked`` result rather than a hard KeyError), but the tool
        is only ADVERTISED to the model (registry ``enabled=True``) when the host
        gate is enabled (shadow/live).  When the host is disabled the tool is
        bound-but-not-advertised: ``is_enabled``/``list_available`` omit it.
        """
        registration = registry.resolve_registration("MemoryWrite")
        if registration is None:
            return  # manifest not registered — nothing to bind
        if registration.handler is not None:
            return  # already bound

        host = self  # capture for closure

        async def _handler(
            arguments: dict[str, object],
            context: ToolContext,
        ) -> ToolResult:
            return await host._handle(arguments, context)

        registry.bind_handler(
            "MemoryWrite",
            _handler,
            enabled_by_registry_policy=self.config.enabled,
        )

    async def _handle(
        self,
        arguments: dict[str, object],
        context: ToolContext,
    ) -> ToolResult:
        if not self.config.enabled:
            return ToolResult(
                status="blocked",
                error_code="memory_write_tool_disabled",
                error_message="MemoryWrite tool is not enabled (gate-off).",
                metadata={"toolName": "MemoryWrite", "reason": "gate_off"},
            )

        fact = str(arguments.get("fact", "")).strip()
        if not fact:
            return ToolResult(
                status="blocked",
                error_code="memory_write_empty_fact",
                error_message="fact must not be empty.",
                metadata={"toolName": "MemoryWrite", "reason": "empty_fact"},
            )

        target_file = str(arguments.get("target_file", "MEMORY.md")).strip()
        if target_file not in {"MEMORY.md", "USER.md"}:
            return ToolResult(
                status="blocked",
                error_code="memory_write_forbidden_target",
                error_message=(
                    f"target_file {target_file!r} is not writable by the agent"
                    " (allowed: MEMORY.md, USER.md)."
                ),
                metadata={
                    "toolName": "MemoryWrite",
                    "reason": "forbidden_target",
                    "requestedTarget": target_file[:64],
                    "allowedTargets": ["MEMORY.md", "USER.md"],
                },
            )

        turn_id = context.turn_id or _DEFAULT_TURN_ID
        provider_id = _resolve_provider_id(self.provider)

        harness_config = MemoryWriteHarnessConfig(
            enabled=True,
            localFakeAdapterEnabled=True,
        )
        policy = MemoryWritePolicy(
            policyRef=_DEFAULT_POLICY_REF,
            policySnapshotRef=_DEFAULT_POLICY_SNAP,
            evidenceRequired=self.config.evidence_required,
            localFakeSuccessAllowed=self.config.local_fake_success_allowed,
        )
        request = MemoryWriteRequest(
            providerId=provider_id,
            turnId=turn_id,
            operation="remember",
            content=fact,
        )

        harness = MemoryWriteHarness(harness_config, adapter=self.provider)
        result = await harness.write(request=request, policy=policy)

        if result.status == "success":
            is_real = (
                result.evidence_record is not None
                and result.evidence_record.is_real_write
            )
            return ToolResult(
                status="ok",
                output={
                    "written": True,
                    "realWrite": is_real,
                    "fact": fact[:200],
                    "targetFile": target_file,
                },
                metadata={
                    "toolName": "MemoryWrite",
                    "receiptId": (
                        result.receipt.receipt_id if result.receipt is not None else None
                    ),
                    "reasonCodes": list(result.reason_codes),
                },
            )

        if result.status == "blocked":
            rejection_reason = None
            if result.evidence_record is not None:
                rejection_reason = result.evidence_record.rejection_reason
            return ToolResult(
                status="blocked",
                error_code=(result.reason_codes[0] if result.reason_codes else "memory_write_blocked"),
                error_message=rejection_reason or "Memory write was blocked by policy.",
                metadata={
                    "toolName": "MemoryWrite",
                    "reasonCodes": list(result.reason_codes),
                    "rejectionReason": rejection_reason,
                },
            )

        # disabled / approval_required / unexpected
        return ToolResult(
            status="blocked",
            error_code="memory_write_not_available",
            error_message=f"Memory write not available: {result.status}",
            metadata={
                "toolName": "MemoryWrite",
                "harnessStatus": result.status,
                "reasonCodes": list(result.reason_codes),
            },
        )


def _resolve_provider_id(provider: object | None) -> str:
    if provider is None:
        return "local-file-memory-unattached"
    is_active = getattr(provider, "_write_active", False)
    if is_active:
        return "local-file-memory-writable"
    return "local-file-memory-readonly"


__all__ = [
    "MemoryWriteToolHost",
    "MemoryWriteToolHostConfig",
]
