"""Durable journal port and fail-closed storage errors."""

from __future__ import annotations

from typing import Protocol

from magi_agent.execution_authority.envelopes import JournalHead
from magi_agent.execution_authority.journal_integrity import (
    AppendWithOutboxReceipt,
    AppendWithOutboxRequest,
    ReadPartitionReceipt,
    ReadPartitionRequest,
)


class JournalError(RuntimeError):
    """Base class for durable journal failures."""


class JournalConflict(JournalError):
    """Optimistic journal head or unique identity no longer matches."""


class JournalIntegrityError(JournalError):
    """Persisted data cannot prove its declared integrity."""


class AuthorityJournal(Protocol):
    def head(self, partition_id: str) -> JournalHead: ...

    def append_with_outbox(
        self, request: AppendWithOutboxRequest
    ) -> AppendWithOutboxReceipt: ...

    def read_partition(self, request: ReadPartitionRequest) -> ReadPartitionReceipt: ...


__all__ = [
    "AuthorityJournal",
    "JournalConflict",
    "JournalError",
    "JournalIntegrityError",
]
