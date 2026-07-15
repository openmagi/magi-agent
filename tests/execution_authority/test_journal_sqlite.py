from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256

import pytest

from magi_agent.execution_authority.envelopes import (
    JournalHead,
    OutboxDraft,
    draft_journal_event,
)
from magi_agent.execution_authority.journal import (
    JournalConflict,
    JournalIntegrityError,
)
from magi_agent.execution_authority.journal_integrity import (
    AppendWithOutboxRequest,
    ReadPartitionRequest,
    canonical_journal_genesis_hash,
    canonical_safe_object_json,
)
from magi_agent.execution_authority.journal_sqlite import SQLiteAuthorityJournal


D1 = "sha256:" + "1" * 64
D2 = "sha256:" + "2" * 64
D3 = "sha256:" + "3" * 64
D4 = "sha256:" + "4" * 64
D5 = "sha256:" + "5" * 64
PARTITION = "task:task_01:1"


def _request(*, event_id: str, expected: JournalHead) -> AppendWithOutboxRequest:
    draft = draft_journal_event(
        event_id=event_id,
        partition_id=PARTITION,
        event_type="audit.note",
        action_id="action_01",
        attempt_id="attempt_01",
        task_contract_id="task_01",
        task_version=1,
        task_contract_digest=D1,
        completion_epoch_id="epoch_01",
        admission_sequence=7,
        authority_contract_id="authority_01",
        request_digest=D2,
        idempotency_key_digest=D3,
        fencing_token=11,
        actor_id="actor_01",
        policy_digest=D4,
        causation_id="turn_01",
        correlation_id="run_01",
        identity_digest=D5,
        payload={"authorityPartitionId": PARTITION, "result": "ok"},
    )
    payload_json = canonical_safe_object_json({"messageRef": event_id})
    return AppendWithOutboxRequest(
        draft=draft,
        outbox=OutboxDraft(
            outboxId=f"outbox_{event_id}",
            partitionId=PARTITION,
            subjectId="completion_01",
            subjectDigest=D1,
            kind="projection_delivery",
            payloadDigest="sha256:" + sha256(payload_json.encode()).hexdigest(),
            payloadJson=payload_json,
        ),
        expectedJournalHead=expected,
    )


def test_append_and_read_validate_the_persisted_hash_chain(tmp_path) -> None:
    journal = SQLiteAuthorityJournal(tmp_path / "authority.db")
    genesis = journal.head(PARTITION)

    first = journal.append_with_outbox(_request(event_id="event_01", expected=genesis))
    second = journal.append_with_outbox(
        _request(event_id="event_02", expected=first.resulting_journal_head)
    )
    page = journal.read_partition(
        ReadPartitionRequest(partitionId=PARTITION, afterSequence=0, limit=10)
    )

    assert [event.event_id for event in page.events] == ["event_01", "event_02"]
    assert page.capture_head == second.resulting_journal_head
    assert page.start_anchor.event_hash == canonical_journal_genesis_hash(PARTITION)
    assert page.has_more is False


def test_stale_head_and_duplicate_identity_fail_without_partial_outbox(tmp_path) -> None:
    journal = SQLiteAuthorityJournal(tmp_path / "authority.db")
    genesis = journal.head(PARTITION)
    journal.append_with_outbox(_request(event_id="event_01", expected=genesis))

    with pytest.raises(JournalConflict):
        journal.append_with_outbox(_request(event_id="event_02", expected=genesis))

    assert journal.pending_outbox_count() == 1


def test_read_fails_closed_when_a_physical_row_is_corrupted(tmp_path) -> None:
    path = tmp_path / "authority.db"
    journal = SQLiteAuthorityJournal(path)
    journal.append_with_outbox(_request(event_id="event_01", expected=journal.head(PARTITION)))
    journal._test_corrupt_event_json("event_01", '{"corrupt":true}')

    with pytest.raises(JournalIntegrityError):
        journal.read_partition(
            ReadPartitionRequest(partitionId=PARTITION, afterSequence=0, limit=10)
        )

