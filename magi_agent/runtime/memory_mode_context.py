"""Async-safe per-request memory-mode context for the live serve path.

The hosted chat-proxy forwards the channel's memory mode to this runtime as the
HTTP header ``x-core-agent-memory-mode``. This module exposes a default-OFF gate
plus a per-request contextvar so the serve path can inject the existing
``read_only`` / ``incognito`` system-prompt blocks for the live turn.

Zero overhead when the gate is off: ``memory_mode_request_scope`` does not touch
the contextvar, so ``current_memory_mode()`` stays :data:`MemoryMode.NORMAL` and
prompt assembly is byte-identical to today.
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar

from magi_agent.runtime.session_identity import MemoryMode, _memory_mode_from_header

MAGI_MEMORY_MODE_ROUTING_ENABLED_ENV: str = "MAGI_MEMORY_MODE_ROUTING_ENABLED"

_TRUTHY_VALUES = frozenset({"1", "true", "yes", "on"})

_current_memory_mode: ContextVar[MemoryMode] = ContextVar(
    "_current_memory_mode", default=MemoryMode.NORMAL
)


def memory_mode_routing_enabled() -> bool:
    """Return True when ``MAGI_MEMORY_MODE_ROUTING_ENABLED`` is truthy (default off)."""
    return (
        os.environ.get(MAGI_MEMORY_MODE_ROUTING_ENABLED_ENV, "").strip().lower()
        in _TRUTHY_VALUES
    )


def current_memory_mode() -> MemoryMode:
    """Return the current request's memory mode (``NORMAL`` when not set)."""
    return _current_memory_mode.get()


@contextmanager
def memory_mode_request_scope(headers: Mapping[str, object]) -> Iterator[None]:
    """Bind the per-request memory mode from ``x-core-agent-memory-mode``.

    When the gate is off this is a no-op: the contextvar stays ``NORMAL`` so the
    serve path is byte-identical to today. When on, the header is parsed via
    :func:`_memory_mode_from_header` and reset on exit.
    """
    if not memory_mode_routing_enabled():
        yield
        return
    raw = headers.get("x-core-agent-memory-mode")
    mode = _memory_mode_from_header(raw if isinstance(raw, str) else None)
    token = _current_memory_mode.set(mode)
    try:
        yield
    finally:
        _current_memory_mode.reset(token)


__all__ = [
    "MAGI_MEMORY_MODE_ROUTING_ENABLED_ENV",
    "current_memory_mode",
    "memory_mode_request_scope",
    "memory_mode_routing_enabled",
]
