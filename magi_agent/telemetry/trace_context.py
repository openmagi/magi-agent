"""Async-safe per-turn trace context using contextvars.

Zero overhead when disabled: ``get_trace()`` returns ``None`` and callers
check before recording. No imports from other magi_agent packages.
"""

from __future__ import annotations

import contextvars
import os

from .execution_trace import ExecutionTrace

_current_trace: contextvars.ContextVar[ExecutionTrace | None] = contextvars.ContextVar(
    "_current_trace", default=None
)

_TRUTHY_VALUES = frozenset({"1", "true", "yes"})


def get_trace() -> ExecutionTrace | None:
    """Return the current turn's trace, or ``None`` if tracing is not active."""
    return _current_trace.get()


def set_trace(trace: ExecutionTrace | None) -> None:
    """Set the trace for the current async context."""
    _current_trace.set(trace)


def trace_enabled() -> bool:
    """Check if tracing is enabled via the ``MAGI_EXECUTION_TRACE`` env var.

    Truthy values: ``"1"``, ``"true"``, ``"yes"`` (case-insensitive).
    Everything else (including unset) is ``False``.
    """
    return os.environ.get("MAGI_EXECUTION_TRACE", "").lower() in _TRUTHY_VALUES
