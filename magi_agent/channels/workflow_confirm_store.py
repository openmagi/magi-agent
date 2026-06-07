from __future__ import annotations

from typing import Protocol

from magi_agent.channels.research_command import ResearchCommandResult

__all__ = ["PendingConfirmationStore", "InMemoryPendingConfirmationStore"]


class PendingConfirmationStore(Protocol):
    def put(self, session_id: str, pending: ResearchCommandResult) -> None: ...
    def pop(self, session_id: str) -> ResearchCommandResult | None: ...
    def clear(self, session_id: str) -> None: ...


class InMemoryPendingConfirmationStore:
    """In-memory pending-confirmation store. Default impl; a hosted deployment
    may swap a Redis-backed store implementing the same Protocol."""

    def __init__(self) -> None:
        self._data: dict[str, ResearchCommandResult] = {}

    def put(self, session_id: str, pending: ResearchCommandResult) -> None:
        self._data[session_id] = pending

    def pop(self, session_id: str) -> ResearchCommandResult | None:
        return self._data.pop(session_id, None)

    def clear(self, session_id: str) -> None:
        self._data.pop(session_id, None)
