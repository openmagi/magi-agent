"""Thread-safe single-flight registry for active turns.

Extracted from the retired ``runner_session_boundary`` module. Maps a session
key to the id of its currently-running turn so a second concurrent turn for
the same session can be rejected. ADK-free by construction.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from threading import Lock
from typing import Any

__all__ = ["ActiveTurnRegistry"]


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
