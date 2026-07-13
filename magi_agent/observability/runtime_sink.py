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

import logging
from typing import Callable, Iterable

logger = logging.getLogger(__name__)

EventSink = Callable[[dict, "str | None", "str | None"], None]

_active_sink: "EventSink | None" = None


def combine_sinks(sinks: "Iterable[EventSink | None]") -> "EventSink | None":
    """Fan one event out to several sinks. Each call is guarded so a failing
    sink never blocks the others or raises. Returns ``None`` when no usable sink
    is supplied (so callers can keep ``event_sink=None`` semantics)."""
    active = [s for s in sinks if s is not None]
    if not active:
        return None
    if len(active) == 1:
        return active[0]

    def _fanout(event: dict, session_id: "str | None", turn_id: "str | None") -> None:
        for sink in active:
            try:
                sink(event, session_id, turn_id)
            except Exception:
                logger.debug("combined event sink member failed", exc_info=True)

    return _fanout


def set_active_sink(sink: "EventSink | None") -> None:
    """Register *sink* as the process-global event sink (or clear with None)."""
    global _active_sink
    _active_sink = sink


def get_active_sink() -> "EventSink | None":
    """Return the currently registered sink, or None if none is set."""
    return _active_sink


def compose_active_observability_sink(inner_sink: object) -> object:
    """Compose the process-global observability sink into a driver ``event_sink``
    for the governed hosted path, mirroring the CLI wiring in ``cli/wiring.py``
    (``combine_sinks([get_active_sink(), get_active_transcript_sink()])``).

    The local ``build_headless_runtime`` path composes ``get_active_sink()`` into
    the driver ``event_sink`` so activity events reach ``observability.db``; the
    hosted governed serving path never wired this, so no activity rows are written
    on that path (fixed here). Sibling to
    :func:`magi_agent.observability.transcript.governed_transcript_event_sink`,
    which composes the transcript sink; both fan the SAME raw driver event out to
    an additional process-global sink.

    When no observability sink is registered (``MAGI_OBSERVABILITY_ENABLED``
    unset), this returns ``inner_sink`` UNCHANGED, so the driver ``event_sink``
    path is byte-identical to the pre-wiring behavior. When a sink IS registered,
    the returned 3-arg sink forwards each event to ``inner_sink`` and to
    ``get_active_sink()`` exactly as ``combine_sinks`` would. ``inner_sink`` may
    be ``None`` (the hosted SSE caller passes no public sink) or a 1-arg
    ``(event)`` callable; ``combine_sinks`` guards each member, so the 3-arg call
    the driver makes is caught fail-open and the public / SSE path is unchanged."""
    active = get_active_sink()
    if active is None:
        return inner_sink
    return combine_sinks([inner_sink, active])  # type: ignore[list-item]
