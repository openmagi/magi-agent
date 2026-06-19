"""Process-local registry of in-flight streaming-chat turns.

The SSE chat route streams one agent turn as a live byte stream. While that turn
is running, three sibling routes need to reach into it:

- the **control-response** route resolves a parked tool-permission ask by calling
  ``turn.sink.deliver(...)``;
- the **interrupt** route cancels the turn by calling ``turn.cancel.set()``;
- the **inject** route (a later task) mutates the in-flight turn.

This module provides a tiny, dependency-light lookup so those routes can find a
running turn. The table is keyed by ``(session_id, turn_id)`` and uses **claim**
semantics: ``try_register`` acquires ownership of a turn and REFUSES to silently
clobber a different live turn that already holds the same key. Control/cancel
routes can target a specific ``turn_id``; when none is supplied they fall back to
session-only resolution **only** when exactly one turn is live for that session
(``get_single`` returns ``"ambiguous"`` otherwise so the route can answer 409).

Single-worker / local-only semantics
------------------------------------
This registry is **process-local** and not thread-safe; it is intended to be
touched only from a single asyncio event loop in a single worker process. It
does NOT coordinate across workers — two workers each keep their own table, so a
turn started on worker A is not addressable from worker B. Shared multi-worker
coordination is out of scope here; deployments that need it must run a single
worker for the streaming-chat routes (or front them with sticky routing).

The module is intentionally free of FastAPI / engine imports to avoid import
cycles; the ``sink`` field is typed loosely (see ``TYPE_CHECKING``).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

if TYPE_CHECKING:
    from magi_agent.cli.permissions import HeadlessSink

__all__ = [
    "ActiveTurn",
    "ActiveTurnClaim",
    "ActiveTurnExists",
    "ActiveTurnTable",
    "ACTIVE_TURNS",
]


class ActiveTurnExists(RuntimeError):
    """Raised by the compat :meth:`ActiveTurnTable.register` shim when a turn for
    the same ``(session_id, turn_id)`` is already registered.

    This converts the historical *last-writer-wins silent overwrite* into a
    fail-loud error so a not-yet-migrated caller cannot orphan a live turn.
    """


@dataclass(frozen=True)
class ActiveTurnClaim:
    """Ownership token returned by :meth:`ActiveTurnTable.try_register`.

    Holding a claim is proof that *this* caller acquired the ``(session_id,
    turn_id)`` slot. Passing the claim back to :meth:`ActiveTurnTable.unregister`
    releases the slot ONLY if the stored turn still matches the claim's
    ``owner_id`` — so a stale owner can never evict a newer turn that has since
    reused the same key.
    """

    session_id: str
    turn_id: str
    owner_id: str


@dataclass
class ActiveTurn:
    """A handle to a single in-flight streaming-chat turn.

    Attributes
    ----------
    session_id:
        Session the turn belongs to (first half of the registry key).
    turn_id:
        Identifier of this specific turn (second half of the registry key). Also
        used by :meth:`ActiveTurnTable.unregister` to avoid removing a NEWER turn
        that has already replaced this one.
    cancel:
        The :class:`asyncio.Event` the engine watches; setting it requests a
        cooperative abort of the turn.
    sink:
        The streaming prompt sink (a :class:`HeadlessSink`). The control-response
        route calls ``sink.deliver(ControlResponse(...))`` on it to resolve a
        parked tool-permission ask.
    task:
        Optional handle to the :class:`asyncio.Task` driving this turn. The
        gate5b user-visible chat path (``chat_routes.py``) runs as an asyncio
        task and threads NO cooperative ``cancel`` poll, so the interrupt route
        hard-cancels this task to abort the turn (the live-runner boundary
        catches :class:`asyncio.CancelledError` and reports ``client_aborted``).
        ``None`` when the turn does not run as a cancellable task (e.g. the
        cooperative streaming path that only watches ``cancel``).
    owner_id:
        The claim owner that registered this turn (set by ``try_register``).
        Used by ``unregister`` to verify a release came from the rightful owner.
        ``None`` for turns inserted by the historical session-only path.
    """

    session_id: str
    turn_id: str
    cancel: asyncio.Event
    sink: "HeadlessSink"
    task: "asyncio.Task[object] | None" = None
    owner_id: str | None = None


class ActiveTurnTable:
    """A process-local ``(session_id, turn_id) -> ActiveTurn`` claim registry.

    Not thread-safe; intended to be touched only from a single asyncio event
    loop. ``try_register`` acquires a claim and refuses to clobber an existing
    live turn for the same key (single-flight per ``(session_id, turn_id)``).
    See the module docstring for the single-worker / local-only limitation.
    """

    def __init__(self) -> None:
        self._turns: dict[tuple[str, str], ActiveTurn] = {}

    def try_register(self, turn: ActiveTurn) -> ActiveTurnClaim | None:
        """Claim the ``(session_id, turn_id)`` slot for *turn*.

        Returns a fresh :class:`ActiveTurnClaim` on success, or ``None`` if a
        turn is already registered for that key (does NOT overwrite — the
        existing live turn is preserved).
        """
        key = (turn.session_id, turn.turn_id)
        if key in self._turns:
            return None
        owner_id = uuid4().hex
        turn.owner_id = owner_id
        self._turns[key] = turn
        return ActiveTurnClaim(
            session_id=turn.session_id,
            turn_id=turn.turn_id,
            owner_id=owner_id,
        )

    def register(self, turn: ActiveTurn) -> ActiveTurnClaim:
        """Compat shim around :meth:`try_register`.

        Kept so callers that have not migrated to the claim API still compile.
        On a duplicate ``(session_id, turn_id)`` it raises
        :class:`ActiveTurnExists` (fail-loud) instead of the old silent
        last-writer-wins overwrite. New code should call ``try_register`` and
        handle the ``None`` return explicitly.
        """
        claim = self.try_register(turn)
        if claim is None:
            raise ActiveTurnExists(
                f"active turn already registered for "
                f"({turn.session_id!r}, {turn.turn_id!r})"
            )
        return claim

    def get(self, session_id: str, turn_id: str | None = None) -> ActiveTurn | None:
        """Resolve a turn for *session_id*.

        - With *turn_id*: return the exact ``(session_id, turn_id)`` turn or
          ``None``.
        - Without *turn_id*: session-only resolution — return the lone live turn
          for the session, or ``None`` if there are zero **or** more than one
          (ambiguous). Use :meth:`get_single` to distinguish "none" from
          "ambiguous".
        """
        if turn_id is not None:
            return self._turns.get((session_id, turn_id))
        result = self.get_single(session_id)
        if result == "ambiguous":
            return None
        return result

    def get_single(
        self, session_id: str
    ) -> "ActiveTurn | None | Literal['ambiguous']":
        """Return the lone live turn for *session_id*.

        ``None`` when no turn is live; ``"ambiguous"`` when more than one turn is
        live for the session (the caller cannot pick one without a ``turn_id``).
        """
        matches = [
            turn for (sess, _turn_id), turn in self._turns.items() if sess == session_id
        ]
        if not matches:
            return None
        if len(matches) > 1:
            return "ambiguous"
        return matches[0]

    def unregister(
        self,
        claim_or_session: "ActiveTurnClaim | str",
        turn_id: str | None = None,
    ) -> None:
        """Release a turn from the registry.

        Two call shapes:

        - ``unregister(claim)`` — release using an :class:`ActiveTurnClaim`;
          removes the stored turn ONLY if it still matches the claim's
          ``(session_id, turn_id)`` AND its recorded ``owner_id`` (so a stale
          owner cannot evict a newer turn that reused the same key).
        - ``unregister(session_id, turn_id)`` — back-compat positional form;
          removes the stored turn IFF its ``turn_id`` matches (guards the
          teardown race where an older turn's ``finally`` would evict a newer
          turn under the same session).
        """
        if isinstance(claim_or_session, ActiveTurnClaim):
            claim = claim_or_session
            key = (claim.session_id, claim.turn_id)
            current = self._turns.get(key)
            if current is not None and current.owner_id == claim.owner_id:
                del self._turns[key]
            return

        session_id = claim_or_session
        if turn_id is None:
            return
        key = (session_id, turn_id)
        current = self._turns.get(key)
        if current is not None and current.turn_id == turn_id:
            del self._turns[key]


# Module-level singleton for the process.
ACTIVE_TURNS = ActiveTurnTable()
