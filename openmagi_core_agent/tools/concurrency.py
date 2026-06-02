"""Tool batch partitioning for concurrent execution.

This module provides types and logic for partitioning a sequence of tool calls
into batches that can be executed concurrently or exclusively, based on the
``parallel_safety`` metadata of each tool.

Rules
-----
- Tools with ``parallel_safety in ("readonly", "concurrency_safe")`` may be
  grouped together into a *concurrent* batch.
- Tools with ``parallel_safety == "unsafe"`` must run alone in an *exclusive*
  batch.
- After a tool that has ``mutates_workspace=True``, the immediately following
  tool must also run exclusively, even if that tool's ``parallel_safety``
  would otherwise allow concurrency. This guards against reading stale state
  that the previous write may have produced.
- An unknown tool (``registry.resolve`` returns ``None``) is treated as
  exclusive.
"""
from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

from openmagi_core_agent.tools.registry import ToolRegistry


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class ConcurrencyConfig(BaseModel):
    """Configuration for tool-level concurrency."""

    model_config = ConfigDict(frozen=True)

    max_concurrency: int = 8
    enabled: bool = False


@dataclass(frozen=True)
class ToolCall:
    """A single pending tool invocation."""

    name: str
    arguments: dict[str, object]
    tool_use_id: str


@dataclass(frozen=True)
class ToolBatch:
    """An ordered group of tool calls that share an execution strategy."""

    is_concurrent: bool
    calls: tuple[ToolCall, ...]


# ---------------------------------------------------------------------------
# Partitioning algorithm
# ---------------------------------------------------------------------------


def partition_tool_calls(
    calls: tuple[ToolCall, ...],
    registry: ToolRegistry,
) -> tuple[ToolBatch, ...]:
    """Partition *calls* into sequential batches for execution.

    Consecutive concurrent-safe calls are grouped into a single
    ``ToolBatch(is_concurrent=True, ...)``.  Each unsafe (or unknown) call
    becomes its own ``ToolBatch(is_concurrent=False, ...)``.

    After any tool whose manifest declares ``mutates_workspace=True`` the
    very next tool is forced into an exclusive batch regardless of its own
    ``parallel_safety``.

    Parameters
    ----------
    calls:
        The tool calls to partition, in execution order.
    registry:
        Registry used to look up ``ToolManifest`` entries by tool name.

    Returns
    -------
    tuple[ToolBatch, ...]
        Ordered sequence of batches ready for execution.
    """
    batches: list[ToolBatch] = []
    current_concurrent: list[ToolCall] = []
    # When True, the current call must be exclusive even if its parallel_safety
    # would otherwise allow concurrency.
    force_exclusive_next: bool = False

    for call in calls:
        manifest = registry.resolve(call.name)

        is_safe = (
            manifest is not None
            and manifest.parallel_safety in ("readonly", "concurrency_safe")
            and not force_exclusive_next
        )

        if is_safe:
            current_concurrent.append(call)
            # force_exclusive_next was False to reach here; stays False.
        else:
            # Flush any accumulated concurrent batch first.
            if current_concurrent:
                batches.append(
                    ToolBatch(is_concurrent=True, calls=tuple(current_concurrent))
                )
                current_concurrent = []
            batches.append(ToolBatch(is_concurrent=False, calls=(call,)))

        # After processing this call, decide whether to force the next one
        # exclusive.  Only a workspace-mutating tool triggers the guard.
        if manifest is not None and manifest.mutates_workspace:
            force_exclusive_next = True
        elif not is_safe:
            # Non-safe tool that does NOT mutate workspace: the guard is
            # consumed by this exclusive call — reset it.
            force_exclusive_next = False
        # If is_safe was True, force_exclusive_next was already False and
        # remains False (non-mutating safe tool can't set it).

    # Flush any remaining concurrent calls.
    if current_concurrent:
        batches.append(ToolBatch(is_concurrent=True, calls=tuple(current_concurrent)))

    return tuple(batches)
