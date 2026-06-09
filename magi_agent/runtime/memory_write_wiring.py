"""Gate-aware factory for the MemoryWrite tool host (Task D, PR2).

``build_memory_write_host`` resolves the writable-memory execution mode from
the readiness gate and returns a ``MemoryWriteToolHost`` configured for that
mode:

  disabled → enabled=False, provider=None
  shadow   → enabled=True,  provider=None  (simulated success, no real writes)
  live     → enabled=True,  provider=LocalFileMemoryProvider(write_enabled=True)

The local single-user developer short-circuit (``MAGI_MEMORY_LOCAL_DEV=1``)
bypasses the canary-promotion ladder and goes directly to "live" — see
``gates.memory_write_readiness`` for the safety invariants.

Governance
----------
- Do NOT set ``enabled=True`` without the gate resolving to "shadow" or "live".
- Do NOT inject a ``LocalFileMemoryProvider`` without the gate resolving to "live".
- The ``LocalFileMemoryProvider``'s own write gate (``write_enabled=True`` or
  ``MAGI_MEMORY_WRITE_ENABLED=1``) must also be satisfied for real file writes.
"""
from __future__ import annotations

from pathlib import Path

from magi_agent.gates.memory_write_readiness import (
    MemoryWriteReadinessConfig,
    resolve_memory_write_execution_mode,
)
from magi_agent.harness.memory_write_tool import (
    MemoryWriteToolHost,
    MemoryWriteToolHostConfig,
)


def build_memory_write_host(
    *,
    workspace_root: Path,
    bot_id: str,
    user_id: str,
    readiness_config: MemoryWriteReadinessConfig | None = None,
) -> MemoryWriteToolHost:
    """Build a gate-aware MemoryWriteToolHost.

    Parameters
    ----------
    workspace_root:
        Workspace directory passed to ``LocalFileMemoryProvider`` in live mode.
    bot_id:
        Bot identifier used for scope digest matching in the readiness gate.
    user_id:
        Owner user identifier used for scope digest matching.
    readiness_config:
        Optional explicit readiness gate config.  When not provided a default
        (all-OFF) ``MemoryWriteReadinessConfig`` is used, which resolves to
        ``"disabled"`` unless ``MAGI_MEMORY_LOCAL_DEV=1`` is set.

    Returns
    -------
    MemoryWriteToolHost
        - disabled: ``config.enabled=False``, ``provider=None``
        - shadow:   ``config.enabled=True``,  ``provider=None``
        - live:     ``config.enabled=True``,  ``provider=LocalFileMemoryProvider``
    """
    config = readiness_config if readiness_config is not None else MemoryWriteReadinessConfig()
    mode = resolve_memory_write_execution_mode(config, bot_id=bot_id, user_id=user_id)

    enabled = mode in ("shadow", "live")
    provider = None

    if mode == "live":
        from magi_agent.memory.adapters.local_file_writable import (
            LocalFileMemoryConfig,
            LocalFileMemoryProvider,
        )

        provider = LocalFileMemoryProvider(
            LocalFileMemoryConfig(
                workspaceRoot=workspace_root,
                enabled=True,
                writeEnabled=True,
            )
        )

    return MemoryWriteToolHost(
        config=MemoryWriteToolHostConfig(enabled=enabled),
        provider=provider,
    )


__all__ = ["build_memory_write_host"]
