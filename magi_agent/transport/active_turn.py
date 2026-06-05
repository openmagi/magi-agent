"""Process-local registry of in-flight streaming-chat turns.

The SSE chat route streams one agent turn as a live byte stream. While that turn
is running, three sibling routes need to reach into it:

- the **control-response** route resolves a parked tool-permission ask by calling
  ``turn.sink.deliver(...)``;
- the **interrupt** route cancels the turn by calling ``turn.cancel.set()``;
- the **inject** route (a later task) mutates the in-flight turn.

This module provides a tiny, dependency-light lookup keyed by ``session_id`` so
those routes can find the running turn. There is at most ONE in-flight turn per
session (the engine's single-flight ``ActiveTurnRegistry`` enforces this at the
engine layer), so the table is a simple ``session_id -> ActiveTurn`` map.

The module is intentionally free of FastAPI / engine imports to avoid import
cycles; the ``sink`` field is typed loosely (see ``TYPE_CHECKING``).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from magi_agent.cli.permissions import HeadlessSink

__all__ = [
    "ActiveTurn",
    "ActiveTurnTable",
    "ACTIVE_TURNS",
]


@dataclass
class ActiveTurn:
    """A handle to a single in-flight streaming-chat turn.

    Attributes
    ----------
    session_id:
        Session the turn belongs to (the registry key).
    turn_id:
        Identifier of this specific turn. Used by :meth:`ActiveTurnTable.unregister`
        to avoid removing a NEWER turn that has already replaced this one.
    cancel:
        The :class:`asyncio.Event` the engine watches; setting it requests a
        cooperative abort of the turn.
    sink:
        The streaming prompt sink (a :class:`HeadlessSink`). The control-response
        route calls ``sink.deliver(ControlResponse(...))`` on it to resolve a
        parked tool-permission ask.
    """

    session_id: str
    turn_id: str
    cancel: asyncio.Event
    sink: "HeadlessSink"


class ActiveTurnTable:
    """A process-local ``session_id -> ActiveTurn`` registry.

    Not thread-safe; intended to be touched only from a single asyncio event
    loop. ``register`` is last-writer-wins so a session that legitimately starts
    a new turn (after the previous one finished) replaces the stale entry.
    """

    def __init__(self) -> None:
        self._turns: dict[str, ActiveTurn] = {}

    def register(self, turn: ActiveTurn) -> None:
        """Record *turn* as the in-flight turn for its session.

        Keyed by ``session_id``; one in-flight turn per session. A subsequent
        register for the same session overwrites the previous entry.
        """
        self._turns[turn.session_id] = turn

    def get(self, session_id: str) -> ActiveTurn | None:
        """Return the in-flight turn for *session_id*, or ``None`` if none."""
        return self._turns.get(session_id)

    def unregister(self, session_id: str, turn_id: str) -> None:
        """Remove the registered turn for *session_id* IFF it matches *turn_id*.

        The ``turn_id`` guard avoids a race where a turn's teardown ``finally``
        removes a NEWER turn that has already registered under the same session.
        """
        current = self._turns.get(session_id)
        if current is not None and current.turn_id == turn_id:
            del self._turns[session_id]


# Module-level singleton for the process.
ACTIVE_TURNS = ActiveTurnTable()
