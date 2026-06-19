"""Tool-level concurrency configuration.

``ConcurrencyConfig`` is consumed by ``adk_bridge/tool_adapter.build_concurrency_config``
and ``ConcurrentToolDispatcher`` to bound offloaded readonly / concurrency-safe
handler execution.

The batch-partitioning machinery (``ToolCall`` / ``ToolBatch`` /
``partition_tool_calls``) was deleted in P2.5 (H-5): it only supported the dead
``ConcurrentToolDispatcher.dispatch_batch`` fan-out path, which the live ADK
Runner never invokes (ADK owns the tool loop and dispatches one call at a time).
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ConcurrencyConfig(BaseModel):
    """Configuration for tool-level concurrency."""

    model_config = ConfigDict(frozen=True)

    max_concurrency: int = 8
    enabled: bool = False
