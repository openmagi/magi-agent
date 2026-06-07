"""Process-global event-sink registry.

A single sink can be registered per process (single-pod runtime).  The sink
receives each sanitized public engine event dict together with the session_id
and turn_id of the originating turn.

The sink is intentionally untyped at the module level (``object | None``) so
that callers do not need to import the ``Callable`` alias — the engine.py
``_observe_event`` helper calls it duck-typed and wraps the call in a
try/except so it is always fail-open.
"""
from __future__ import annotations

from typing import Callable

EventSink = Callable[[dict, "str | None", "str | None"], None]

_active_sink: "EventSink | None" = None


def set_active_sink(sink: "EventSink | None") -> None:
    """Register *sink* as the process-global event sink (or clear with None)."""
    global _active_sink
    _active_sink = sink


def get_active_sink() -> "EventSink | None":
    """Return the currently registered sink, or None if none is set."""
    return _active_sink
