"""SQLite implementation of the integrity journal and transactional outbox."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
import sqlite3

from magi_agent.execution_authority.envelopes import JournalEvent, JournalHead, OutboxItem
from magi_agent.execution_authority.journal import JournalConflict, JournalIntegrityError
from magi_agent.execution_authority.journal_integrity import (
    AppendWithOutboxReceipt,
    AppendWithOutboxRequest,
    JournalChainAnchor,
    OutboxAckReceipt,
    OutboxAckRequest,
    OutboxAttemptReceipt,
    OutboxAttemptRequest,
    OutboxClaimReceipt,
    OutboxClaimRequest,
    ReadPartitionReceipt,
    ReadPartitionRequest,
    canonical_journal_event_hash,
    canonical_journal_genesis_hash,
    canonical_journal_row_checksum,
    validate_journal_event_integrity,
)
from magi_agent.execution_authority.migrations import migrate_authority_database
from magi_agent.execution_authority.state_machine import OutboxState


class SQLiteAuthorityJournal:
    """Serialize journal mutations with ``BEGIN IMMEDIATE`` and head CAS."""

    def __init__(self, path: Path, *, busy_timeout_ms: int = 5_000) -> None:
        if not isinstance(path, Path):
            raise TypeError("path must be a pathlib.Path")
        self._path = path
        self._busy_timeout_ms = busy_timeout_ms
        migrate_authority_database(path, busy_timeout_ms=busy_timeout_ms)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, isolation_level=None)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(f"PRAGMA busy_timeout={self._busy_timeout_ms}")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    @staticmethod
    def _head_for(connection: sqlite3.Connection, partition_id: str) -> JournalHead:
        row = connection.execute(
            "SELECT last_sequence, last_event_hash, compare_version "
            "FROM authority_heads WHERE partition_id = ?",
            (partition_id,),
        ).fetchone()
        if row is None:
            return JournalHead(
                partitionId=partition_id,
                sequence=0,
                eventHash=canonical_journal_genesis_hash(partition_id),
                compareVersion=0,
            )
        return JournalHead(
            partitionId=partition_id,
            sequence=row[0],
            eventHash=row[1],
            compareVersion=row[2],
        )

    def head(self, partition_id: str) -> JournalHead:
        if type(partition_id) is not str or not partition_id:
            raise ValueError("partition_id must be a non-empty exact string")
        with self._connect() as connection:
            return self._head_for(connection, partition_id)

    def append_with_outbox(
        self, request: AppendWithOutboxRequest
    ) -> AppendWithOutboxReceipt:
        now = datetime.now(UTC)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT OR IGNORE INTO authority_partitions(partition_id, state) "
                "VALUES (?, 'ready')",
                (request.draft.partition_id,),
            )
            actual = self._head_for(connection, request.draft.partition_id)
            if actual != request.expected_journal_head:
                raise JournalConflict("expected journal head is stale")
            sequence = actual.sequence + 1
            event_hash = canonical_journal_event_hash(
                request.draft,
                sequence=sequence,
                previous_hash=actual.event_hash,
                created_at=now,
            )
            values = request.draft.model_dump(by_alias=True, mode="json")
            provisional = JournalEvent(
                **values,
                sequence=sequence,
                previousHash=actual.event_hash,
                eventHash=event_hash,
                rowChecksum="sha256:" + "0" * 64,
                createdAt=now,
            )
            values.update(
                sequence=sequence,
                previousHash=actual.event_hash,
                eventHash=event_hash,
                rowChecksum=canonical_journal_row_checksum(provisional),
                createdAt=now,
            )
            event = JournalEvent.model_validate(values)
            event_json = event.model_dump_json(by_alias=True)
            connection.execute(
                "INSERT INTO authority_events VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event.partition_id,
                    event.sequence,
                    event.event_id,
                    event.event_type,
                    event.action_id,
                    event.attempt_id,
                    event.idempotency_key_digest,
                    event.task_contract_digest,
                    event.completion_epoch_id,
                    event.request_digest,
                    event.fencing_token,
                    event.previous_hash,
                    event.event_hash,
                    event.payload_digest,
                    event.row_checksum,
                    event.payload_json,
                    event_json,
                    event.created_at.isoformat(),
                ),
            )
            resulting_head = JournalHead(
                partitionId=event.partition_id,
                sequence=event.sequence,
                eventHash=event.event_hash,
                compareVersion=actual.compare_version + 1,
            )
            connection.execute(
                "INSERT INTO authority_heads VALUES (?, ?, ?, ?) "
                "ON CONFLICT(partition_id) DO UPDATE SET "
                "last_sequence=excluded.last_sequence, "
                "last_event_hash=excluded.last_event_hash, "
                "compare_version=excluded.compare_version",
                (
                    resulting_head.partition_id,
                    resulting_head.sequence,
                    resulting_head.event_hash,
                    resulting_head.compare_version,
                ),
            )
            draft = request.outbox
            item = OutboxItem(
                outboxId=draft.outbox_id,
                partitionId=draft.partition_id,
                subjectId=draft.subject_id,
                subjectDigest=draft.subject_digest,
                eventId=event.event_id,
                eventSequence=event.sequence,
                eventHash=event.event_hash,
                kind=draft.kind,
                payloadDigest=draft.payload_digest,
                payloadJson=draft.payload_json,
                state=OutboxState.PENDING,
                claimOwnerId=None,
                claimFencingToken=None,
                claimExpiresAt=None,
                deliveryAttempt=0,
                acknowledgementDigest=None,
                compareVersion=1,
            )
            connection.execute(
                "INSERT INTO authority_outbox VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    item.outbox_id,
                    item.partition_id,
                    item.event_sequence,
                    item.event_id,
                    item.event_hash,
                    item.subject_id,
                    item.subject_digest,
                    item.kind,
                    item.payload_digest,
                    item.payload_json,
                    item.state.value,
                    None,
                    None,
                    None,
                    0,
                    None,
                    1,
                    None,
                ),
            )
            connection.execute(
                "INSERT INTO authority_outbox_fences VALUES (?, 0)",
                (item.outbox_id,),
            )
            connection.commit()
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            raise JournalConflict("journal or outbox identity already exists") from exc
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return AppendWithOutboxReceipt(
            request=request,
            event=event,
            outboxItem=item,
            resultingJournalHead=resulting_head,
            committedAt=now,
        )

    def read_partition(self, request: ReadPartitionRequest) -> ReadPartitionReceipt:
        captured_at = datetime.now(UTC)
        with self._connect() as connection:
            head = self._head_for(connection, request.partition_id)
            if request.after_sequence == 0:
                anchor_hash = canonical_journal_genesis_hash(request.partition_id)
            else:
                anchor_row = connection.execute(
                    "SELECT event_hash FROM authority_events "
                    "WHERE partition_id = ? AND sequence = ?",
                    (request.partition_id, request.after_sequence),
                ).fetchone()
                if anchor_row is None:
                    raise JournalIntegrityError("requested chain anchor does not exist")
                anchor_hash = anchor_row[0]
            rows = connection.execute(
                "SELECT event_json FROM authority_events WHERE partition_id = ? "
                "AND sequence > ? AND sequence <= ? ORDER BY sequence LIMIT ?",
                (request.partition_id, request.after_sequence, head.sequence, request.limit),
            ).fetchall()
        events: list[JournalEvent] = []
        try:
            for row in rows:
                events.append(validate_journal_event_integrity(JournalEvent.model_validate_json(row[0])))
        except (ValueError, TypeError) as exc:
            raise JournalIntegrityError("persisted journal event failed integrity validation") from exc
        return ReadPartitionReceipt(
            request=request,
            startAnchor=JournalChainAnchor(
                partitionId=request.partition_id,
                sequence=request.after_sequence,
                eventHash=anchor_hash,
            ),
            captureHead=head,
            events=tuple(events),
            hasMore=request.after_sequence + len(events) < head.sequence,
            capturedAt=captured_at,
        )

    @staticmethod
    def _outbox_for(connection: sqlite3.Connection, outbox_id: str) -> OutboxItem:
        row = connection.execute(
            "SELECT outbox_id, partition_id, event_sequence, event_id, event_hash, "
            "subject_id, subject_digest, kind, payload_digest, payload_json, delivery_state, "
            "claim_owner_id, claim_fencing_token, claim_expires_at, delivery_attempts, "
            "acknowledgement_digest, compare_version FROM authority_outbox WHERE outbox_id = ?",
            (outbox_id,),
        ).fetchone()
        if row is None:
            raise JournalConflict("outbox item does not exist")
        return OutboxItem(
            outboxId=row[0],
            partitionId=row[1],
            eventSequence=row[2],
            eventId=row[3],
            eventHash=row[4],
            subjectId=row[5],
            subjectDigest=row[6],
            kind=row[7],
            payloadDigest=row[8],
            payloadJson=row[9],
            state=OutboxState(row[10]),
            claimOwnerId=row[11],
            claimFencingToken=row[12],
            claimExpiresAt=datetime.fromisoformat(row[13]) if row[13] else None,
            deliveryAttempt=row[14],
            acknowledgementDigest=row[15],
            compareVersion=row[16],
        )

    def claim_outbox(self, request: OutboxClaimRequest) -> OutboxClaimReceipt:
        claimed_at = datetime.now(UTC)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            previous = self._outbox_for(connection, request.outbox_id)
            high_water_row = connection.execute(
                "SELECT fencing_token_high_water FROM authority_outbox_fences WHERE outbox_id = ?",
                (request.outbox_id,),
            ).fetchone()
            if high_water_row is None:
                raise JournalIntegrityError("outbox fencing high-water is missing")
            previous_high_water = int(high_water_row[0])
            if (
                previous.state is not OutboxState.PENDING
                or previous.delivery_attempt != request.expected_delivery_attempt
                or previous.compare_version != request.expected_compare_version
                or previous_high_water != request.expected_fencing_token_high_water
            ):
                raise JournalConflict("outbox claim precondition is stale")
            resulting_high_water = previous_high_water + 1
            expires_at = claimed_at + timedelta(seconds=request.claim_ttl_seconds)
            connection.execute(
                "UPDATE authority_outbox SET delivery_state = 'claimed', claim_owner_id = ?, "
                "claim_fencing_token = ?, claim_expires_at = ?, compare_version = compare_version + 1 "
                "WHERE outbox_id = ?",
                (request.owner_id, resulting_high_water, expires_at.isoformat(), request.outbox_id),
            )
            connection.execute(
                "UPDATE authority_outbox_fences SET fencing_token_high_water = ? WHERE outbox_id = ?",
                (resulting_high_water, request.outbox_id),
            )
            resulting = self._outbox_for(connection, request.outbox_id)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return OutboxClaimReceipt(
            request=request,
            previousItem=previous,
            resultingItem=resulting,
            previousFencingTokenHighWater=previous_high_water,
            resultingFencingTokenHighWater=resulting_high_water,
            claimedAt=claimed_at,
        )

    def record_outbox_attempt(self, request: OutboxAttemptRequest) -> OutboxAttemptReceipt:
        attempted_at = datetime.now(UTC)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            previous = self._outbox_for(connection, request.outbox_id)
            if (
                previous.state is not OutboxState.CLAIMED
                or previous.claim_owner_id != request.owner_id
                or previous.claim_fencing_token != request.claim_fencing_token
                or previous.subject_digest != request.subject_digest
                or previous.payload_digest != request.payload_digest
                or previous.delivery_attempt != request.expected_delivery_attempt
                or previous.compare_version != request.expected_compare_version
                or previous.claim_expires_at is None
                or attempted_at > previous.claim_expires_at
            ):
                raise JournalConflict("outbox attempt precondition is stale")
            connection.execute(
                "UPDATE authority_outbox SET delivery_attempts = delivery_attempts + 1, "
                "compare_version = compare_version + 1 WHERE outbox_id = ?",
                (request.outbox_id,),
            )
            resulting = self._outbox_for(connection, request.outbox_id)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return OutboxAttemptReceipt(
            request=request,
            previousItem=previous,
            resultingItem=resulting,
            fencingTokenHighWater=request.claim_fencing_token,
            attemptedAt=attempted_at,
        )

    def ack_outbox(self, request: OutboxAckRequest) -> OutboxAckReceipt:
        acknowledged_at = datetime.now(UTC)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            previous = self._outbox_for(connection, request.outbox_id)
            if (
                previous.state is not OutboxState.CLAIMED
                or previous.claim_owner_id != request.owner_id
                or previous.claim_fencing_token != request.claim_fencing_token
                or previous.subject_digest != request.subject_digest
                or previous.payload_digest != request.payload_digest
                or previous.delivery_attempt != request.delivery_attempt
                or previous.compare_version != request.expected_compare_version
                or previous.claim_expires_at is None
                or acknowledged_at > previous.claim_expires_at
            ):
                raise JournalConflict("outbox acknowledgement precondition is stale")
            connection.execute(
                "UPDATE authority_outbox SET delivery_state = 'delivered', claim_owner_id = NULL, "
                "claim_fencing_token = NULL, claim_expires_at = NULL, acknowledgement_digest = ?, "
                "compare_version = compare_version + 1, delivered_at = ? WHERE outbox_id = ?",
                (request.acknowledgement_digest, acknowledged_at.isoformat(), request.outbox_id),
            )
            resulting = self._outbox_for(connection, request.outbox_id)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return OutboxAckReceipt(
            request=request,
            previousItem=previous,
            resultingItem=resulting,
            fencingTokenHighWater=request.claim_fencing_token,
            acknowledgedAt=acknowledged_at,
        )

    def pending_outbox_count(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT count(*) FROM authority_outbox WHERE delivery_state = 'pending'"
            ).fetchone()
        return int(row[0]) if row is not None else 0

    def _test_corrupt_event_json(self, event_id: str, event_json: str) -> None:
        """Test-only corruption hook; production callers must never use this method."""
        with self._connect() as connection:
            connection.execute(
                "UPDATE authority_events SET event_json = ? WHERE event_id = ?",
                (event_json, event_id),
            )


__all__ = ["SQLiteAuthorityJournal"]
