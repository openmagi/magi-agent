"""Single-flight registry for active turns, keyed by session.

This module hosts :class:`ActiveTurnRegistry`, extracted verbatim out of
``runtime/runner_session_boundary.py`` so the lone live part of that
otherwise-dead reference stack no longer blocks its deletion. The registry is
ADK-free (pure ``threading.Lock`` + ``asyncio`` task plumbing), so it is safe to
import at module top from any consumer.

Behaviour is preserved exactly: ``cli/engine.py`` reuses it to reject a second
concurrent turn for the same session id.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from threading import Lock
from typing import Any


class ActiveTurnRegistry:
    def __init__(self) -> None:
        self._lock = Lock()
        self._active_by_session: dict[str, str] = {}

    def try_acquire(self, *, session_key: str, turn_id: str) -> bool:
        with self._lock:
            if session_key in self._active_by_session:
                return False
            self._active_by_session[session_key] = turn_id
            return True

    def release(self, *, session_key: str, turn_id: str) -> None:
        with self._lock:
            if self._active_by_session.get(session_key) == turn_id:
                self._active_by_session.pop(session_key, None)

    def release_when_done(
        self,
        *,
        session_key: str,
        turn_id: str,
        task: asyncio.Task[Any],
    ) -> None:
        task.add_done_callback(
            lambda completed_task: self._release_completed_task(
                completed_task,
                session_key=session_key,
                turn_id=turn_id,
            )
        )

    def _release_completed_task(
        self,
        task: asyncio.Task[Any],
        *,
        session_key: str,
        turn_id: str,
    ) -> None:
        _consume_task_result(task)
        self.release(session_key=session_key, turn_id=turn_id)


def _consume_task_result(task: asyncio.Task[Any]) -> None:
    with suppress(asyncio.CancelledError, Exception):
        task.result()


__all__ = ["ActiveTurnRegistry"]
