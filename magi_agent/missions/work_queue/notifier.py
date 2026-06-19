"""Work-queue terminal-event notifier — tail-from-now delivery via injected sink.

Provides a Protocol-safe, network-free delivery abstraction so a real channel
sink (e.g. Telegram, Slack) can be injected without coupling the core module to
any authority.

Forbidden imports: google.adk, socket, subprocess, urllib, requests, http.
The LoggingNotifySink constructs NO network/channel authority — it is the safe
default. Real delivery sinks are injected by the operator wiring.
"""
from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# NotifySink Protocol — injected delivery abstraction
# ---------------------------------------------------------------------------


@runtime_checkable
class NotifySink(Protocol):
    """Delivery abstraction for terminal work-queue events.

    Implementations MUST NOT raise: an unhandled exception from ``deliver``
    is caught by the notifier and logged, so the batch continues and the
    cursor still advances (a poison event must not wedge delivery forever).
    """

    def deliver(self, event: dict) -> None:
        """Deliver a single terminal event to an external channel."""
        ...


# ---------------------------------------------------------------------------
# LoggingNotifySink — safe default (no network/channel authority)
# ---------------------------------------------------------------------------


class LoggingNotifySink:
    """Log-only sink: records the event at INFO/DEBUG; constructs no authority.

    This is the safe default wired into ``build_local_work_queue_notifier``.
    Real channel sinks (Telegram, Slack, …) are injected by the operator in a
    later P6 phase; they must never be constructed here.
    """

    def deliver(self, event: dict) -> None:
        task_id = event.get("task_id", "<unknown>")
        kind = event.get("kind", "<unknown>")
        _log.info("work-queue terminal event: task_id=%s kind=%s", task_id, kind)
        _log.debug("work-queue terminal event detail: %r", event)


# ---------------------------------------------------------------------------
# WorkQueueNotifier — poll-based tail-from-now notifier
# ---------------------------------------------------------------------------


class WorkQueueNotifier:
    """Tail-from-now terminal event notifier for the durable work queue.

    On the FIRST ``poll_once`` call the cursor is initialised to the current
    highest terminal event id so pre-existing events are NEVER replayed (tail-
    from-now semantics).  Subsequent calls deliver only new events via the
    injected sink, advancing the cursor past every event — even one whose
    delivery raised — so a poison event never wedges the cursor.

    Args:
        store: Any object implementing ``latest_terminal_event_id()`` and
               ``terminal_events_since(since_id, *, limit) -> list[dict]``.
        sink:  Any object implementing ``NotifySink.deliver(event)``; exceptions
               from ``deliver`` are caught + logged and do not propagate.
        batch_limit: Maximum events fetched per ``poll_once`` call (default 200).
    """

    def __init__(self, store: object, sink: object, *, batch_limit: int = 200) -> None:
        self._store = store
        self._sink = sink
        self._batch_limit = batch_limit
        self._cursor: int | None = None

    def poll_once(self) -> int:
        """Fetch and deliver new terminal events since the last poll.

        First call: initialises the cursor to the current highest terminal
        event id and returns 0 (tail-from-now — no replay of pre-existing
        events).

        Subsequent calls: fetches events with id > cursor, calls
        ``sink.deliver(ev)`` for each inside a try/except (a failing sink
        never stops the batch), advances the cursor to the max seen id
        regardless of delivery errors, and returns the count processed.

        Returns:
            Number of events processed (a raising sink still counts; 0 on the
            first call).
        """
        if self._cursor is None:
            self._cursor = self._store.latest_terminal_event_id()  # type: ignore[union-attr]
            return 0

        events: list[dict] = self._store.terminal_events_since(  # type: ignore[union-attr]
            self._cursor, limit=self._batch_limit
        )
        if not events:
            return 0

        max_id = self._cursor
        for ev in events:
            try:
                self._sink.deliver(ev)  # type: ignore[union-attr]
            except Exception:  # noqa: BLE001 — poison events must not wedge cursor
                _log.warning(
                    "work-queue notify sink failed for event id=%s task_id=%s kind=%s",
                    ev.get("id"),
                    ev.get("task_id"),
                    ev.get("kind"),
                    exc_info=True,
                )
            ev_id = ev.get("id")
            if isinstance(ev_id, int) and ev_id > max_id:
                max_id = ev_id

        self._cursor = max_id
        return len(events)


__all__ = [
    "LoggingNotifySink",
    "NotifySink",
    "WorkQueueNotifier",
]
