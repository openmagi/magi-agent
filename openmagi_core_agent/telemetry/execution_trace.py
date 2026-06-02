"""Execution trace recorder for per-turn observability.

This module provides append-only trace recording with zero overhead when
disabled. No imports from other openmagi_core_agent packages -- this is a
leaf module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(frozen=True)
class TraceEntry:
    """A single trace event. Immutable after creation."""

    timestamp: datetime
    layer: str        # "turn", "harness", "hook", "tool", "evidence", "context", "recovery", "prompt", "runner", "verifier"
    module: str       # e.g. "ToolDispatcher", "HookBus", "RecoveryEngine"
    action: str       # e.g. "resolve", "run", "evaluate", "recover"
    detail: str = ""  # e.g. "name=Read", "point=beforeToolUse, effective=3"
    duration_ms: int | None = None


class ExecutionTrace:
    """Append-only trace log for a single turn."""

    def __init__(self, turn_id: str) -> None:
        self._turn_id = turn_id
        self._entries: list[TraceEntry] = []

    @property
    def turn_id(self) -> str:
        return self._turn_id

    def record(
        self,
        layer: str,
        module: str,
        action: str,
        detail: str = "",
        duration_ms: int | None = None,
    ) -> None:
        """Append a trace entry with the current UTC timestamp."""
        self._entries.append(
            TraceEntry(
                timestamp=datetime.now(timezone.utc),
                layer=layer,
                module=module,
                action=action,
                detail=detail,
                duration_ms=duration_ms,
            )
        )

    def summary(self) -> str:
        """Human-readable multi-line summary."""
        lines: list[str] = [f"ExecutionTrace turn={self._turn_id} entries={len(self._entries)}"]
        for entry in self._entries:
            dur = f" ({entry.duration_ms}ms)" if entry.duration_ms is not None else ""
            det = f" {entry.detail}" if entry.detail else ""
            lines.append(f"  [{entry.layer}] {entry.module}.{entry.action}{det}{dur}")
        return "\n".join(lines)

    def to_json(self) -> list[dict[str, str | int | None]]:
        """Machine-readable list of dicts."""
        result: list[dict[str, str | int | None]] = []
        for entry in self._entries:
            result.append({
                "timestamp": entry.timestamp.isoformat(),
                "layer": entry.layer,
                "module": entry.module,
                "action": entry.action,
                "detail": entry.detail,
                "duration_ms": entry.duration_ms,
            })
        return result

    def duration_breakdown(self) -> dict[str, int]:
        """Per-layer total milliseconds. Entries without duration_ms are excluded."""
        totals: dict[str, int] = {}
        for entry in self._entries:
            if entry.duration_ms is not None:
                totals[entry.layer] = totals.get(entry.layer, 0) + entry.duration_ms
        return totals
