from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json

import pytest
from pydantic import ValidationError

from magi_agent.execution_authority.envelopes import (
    GenericJournalEventDraft,
    JournalEvent,
    JournalHead,
    OutboxDraft,
    OutboxItem,
    draft_journal_event,
)
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
    canonical_safe_object_json,
    journal_event_hash_preimage,
    journal_row_checksum_preimage,
    require_generic_event_type,
    require_journal_event_type,
    validate_canonical_safe_object_json,
    validate_journal_event_integrity,
)
from magi_agent.execution_authority.state_machine import OutboxState


D0 = "sha256:" + ("0" * 64)
D1 = "sha256:" + ("1" * 64)
D2 = "sha256:" + ("2" * 64)
D3 = "sha256:" + ("3" * 64)
D4 = "sha256:" + ("4" * 64)
D5 = "sha256:" + ("5" * 64)
NOW = datetime(2030, 1, 1, 12, 0, tzinfo=UTC)
PARTITION = "task:task_01:1"


def _draft(
    *,
    event_id: str = "event_01",
    event_type: str = "audit.note",
    partition_id: str = PARTITION,
    payload: dict[str, object] | None = None,
) -> GenericJournalEventDraft:
    return draft_journal_event(
        event_id=event_id,
        partition_id=partition_id,
        event_type=event_type,
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
        payload=payload or {"authorityPartitionId": partition_id, "result": "ok"},
    )


def _event(
    draft: GenericJournalEventDraft,
    *,
    sequence: int,
    previous_hash: str,
    created_at: datetime,
) -> JournalEvent:
    event_hash = canonical_journal_event_hash(
        draft,
        sequence=sequence,
        previous_hash=previous_hash,
        created_at=created_at,
    )
    values = draft.model_dump(by_alias=True, mode="json")
    provisional = JournalEvent(
        **values,
        sequence=sequence,
        previousHash=previous_hash,
        eventHash=event_hash,
        rowChecksum=D0,
        createdAt=created_at,
    )
    checksum = canonical_journal_row_checksum(provisional)
    return JournalEvent(
        **values,
        sequence=sequence,
        previousHash=previous_hash,
        eventHash=event_hash,
        rowChecksum=checksum,
        createdAt=created_at,
    )


def _outbox_draft(*, partition_id: str = PARTITION) -> OutboxDraft:
    payload_json = canonical_safe_object_json({"messageRef": "message_01"})
    return OutboxDraft(
        schemaVersion=1,
        outboxId="outbox_01",
        partitionId=partition_id,
        subjectId="completion_01",
        subjectDigest=D1,
        kind="projection_delivery",
        payloadDigest="sha256:" + sha256(payload_json.encode()).hexdigest(),
        payloadJson=payload_json,
    )


def _pending_item(event: JournalEvent, outbox: OutboxDraft) -> OutboxItem:
    return OutboxItem(
        schemaVersion=1,
        outboxId=outbox.outbox_id,
        partitionId=outbox.partition_id,
        subjectId=outbox.subject_id,
        subjectDigest=outbox.subject_digest,
        eventId=event.event_id,
        eventSequence=event.sequence,
        eventHash=event.event_hash,
        kind=outbox.kind,
        payloadDigest=outbox.payload_digest,
        payloadJson=outbox.payload_json,
        state=OutboxState.PENDING,
        claimOwnerId=None,
        claimFencingToken=None,
        claimExpiresAt=None,
        deliveryAttempt=0,
        acknowledgementDigest=None,
        compareVersion=1,
    )


def _claimed_item(
    item: OutboxItem,
    *,
    compare_version: int = 2,
    delivery_attempt: int = 0,
    expires_at: datetime = NOW + timedelta(minutes=5),
) -> OutboxItem:
    values = item.model_dump(by_alias=True, mode="json")
    values.update(
        {
            "state": OutboxState.CLAIMED,
            "claimOwnerId": "delivery_01",
            "claimFencingToken": 9,
            "claimExpiresAt": expires_at,
            "deliveryAttempt": delivery_attempt,
            "acknowledgementDigest": None,
            "compareVersion": compare_version,
        }
    )
    return OutboxItem.model_validate(values)


def _delivered_item(
    item: OutboxItem,
    *,
    compare_version: int,
    acknowledgement_digest: str = D5,
) -> OutboxItem:
    values = item.model_dump(by_alias=True, mode="json")
    values.update(
        {
            "state": OutboxState.DELIVERED,
            "claimOwnerId": None,
            "claimFencingToken": None,
            "claimExpiresAt": None,
            "acknowledgementDigest": acknowledgement_digest,
            "compareVersion": compare_version,
        }
    )
    return OutboxItem.model_validate(values)


def test_canonical_payload_accepts_public_authority_partition_binding() -> None:
    payload = {
        "authorityPartitionId": PARTITION,
        "nested": {"count": 3, "ready": True, "value": None},
    }

    encoded = canonical_safe_object_json(payload)

    assert encoded == (
        '{"authorityPartitionId":"task:task_01:1","nested":{"count":3,"ready":true,"value":null}}'
    )
    assert validate_canonical_safe_object_json(encoded) == payload


@pytest.mark.parametrize("payload_json", ["[]", "null", '"text"', "1", "true"])
def test_payload_json_requires_an_object_root(payload_json: str) -> None:
    with pytest.raises(ValueError, match="object root"):
        validate_canonical_safe_object_json(payload_json)


def test_payload_json_rejects_duplicates_noncanonical_bytes_and_floats() -> None:
    with pytest.raises(ValueError, match="duplicate key"):
        validate_canonical_safe_object_json('{"a":1,"a":2}')
    with pytest.raises(ValueError, match="canonical"):
        validate_canonical_safe_object_json('{ "a": 1 }')
    with pytest.raises(ValueError, match="floating-point"):
        validate_canonical_safe_object_json('{"value":1.0}')
    with pytest.raises(ValueError, match="floating-point"):
        canonical_safe_object_json({"value": 1.0})


@pytest.mark.parametrize("value", [2**53, -(2**53)])
def test_payload_json_rejects_integers_outside_the_ijson_safe_range(value: int) -> None:
    with pytest.raises(ValueError, match="I-JSON"):
        canonical_safe_object_json({"value": value})
    with pytest.raises(ValueError, match="I-JSON"):
        validate_canonical_safe_object_json(json.dumps({"value": value}))


def test_payload_json_enforces_depth_node_and_byte_budgets_before_acceptance() -> None:
    deep = '{"v":' * 33 + "null" + "}" * 33
    with pytest.raises(ValueError, match="depth"):
        validate_canonical_safe_object_json(deep)

    with pytest.raises(ValueError, match="node budget"):
        canonical_safe_object_json({"values": [None] * 10_001})

    with pytest.raises(ValueError, match="byte limit"):
        canonical_safe_object_json({"value": "x" * 1_048_576})


@pytest.mark.parametrize(
    "key",
    (
        "api-key",
        "a.p.i_k-e-y",
        "aws_secret-access-key",
        "client secret",
        "slack.signing-secret",
        "private_key",
        "refresh-token",
    ),
)
def test_payload_json_rejects_separator_obfuscated_sensitive_keys(key: str) -> None:
    with pytest.raises(ValueError, match="sensitive key"):
        canonical_safe_object_json({key: "redacted"})


@pytest.mark.parametrize(
    "value",
    (
        "AKIA" + ("A" * 16),
        "AIza" + ("A" * 35),
        "xoxb-" + ("1" * 12) + "-" + ("A" * 24),
        "eyJ" + ("a" * 20) + "." + ("b" * 20) + "." + ("c" * 20),
        "-----BEGIN " + "PRIVATE KEY-----\nmaterial\n-----END PRIVATE KEY-----",
        "Authorization: Bearer opaque-value",
        "/Users/example/.aws/credentials",
    ),
)
def test_payload_json_rejects_cloud_slack_jwt_pem_and_private_path_values(
    value: str,
) -> None:
    with pytest.raises(ValueError, match="sensitive value"):
        canonical_safe_object_json({"note": value})


@pytest.mark.parametrize(
    "event_type",
    ("audit.note", "research.source_2", "custom-event.v1", "x"),
)
def test_journal_event_type_grammar_is_exact_lowercase(event_type: str) -> None:
    assert require_journal_event_type(event_type) == event_type


@pytest.mark.parametrize(
    "event_type",
    ("Audit.note", " audit.note", "audit.note ", "audit..note", "audit/note", "1audit"),
)
def test_journal_event_type_grammar_rejects_aliases(event_type: str) -> None:
    with pytest.raises(ValueError, match="eventType"):
        require_journal_event_type(event_type)


@pytest.mark.parametrize(
    "namespace",
    (
        "action",
        "attempt",
        "authority",
        "completion",
        "epoch",
        "evidence",
        "finalization",
        "integrity",
        "lease",
        "outbox",
        "partition",
        "projection",
        "recovery",
        "task",
        "task_contract",
        "user_decision",
        "workspace",
    ),
)
def test_generic_event_types_cannot_claim_reserved_lifecycle_namespaces(
    namespace: str,
) -> None:
    assert require_journal_event_type(f"{namespace}.recorded") == f"{namespace}.recorded"
    with pytest.raises(ValueError, match="reserved"):
        require_generic_event_type(f"{namespace}.recorded")


def test_event_hash_preimage_is_domain_separated_and_contains_every_header() -> None:
    draft = _draft()
    previous_hash = canonical_journal_genesis_hash(PARTITION)

    preimage = journal_event_hash_preimage(
        draft,
        sequence=1,
        previous_hash=previous_hash,
        created_at=NOW,
    )
    expected_headers = draft.model_dump(by_alias=True, mode="json")
    expected_headers.pop("payloadJson")

    assert preimage == {
        "domain": "magi.journal.event_hash",
        "schemaVersion": 1,
        "headers": expected_headers,
        "sequence": 1,
        "previousHash": previous_hash,
        "createdAt": "2030-01-01T12:00:00.000000Z",
    }
    expected = (
        "sha256:"
        + sha256(
            json.dumps(
                preimage,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode()
        ).hexdigest()
    )
    assert (
        canonical_journal_event_hash(
            draft,
            sequence=1,
            previous_hash=previous_hash,
            created_at=NOW,
        )
        == expected
    )


def test_genesis_hash_is_partition_scoped_and_sequence_rules_fail_closed() -> None:
    first_genesis = canonical_journal_genesis_hash(PARTITION)
    other_genesis = canonical_journal_genesis_hash("task:task_02:1")
    assert first_genesis != other_genesis

    draft = _draft()
    with pytest.raises(ValueError, match="positive"):
        canonical_journal_event_hash(
            draft,
            sequence=0,
            previous_hash=first_genesis,
            created_at=NOW,
        )
    with pytest.raises(ValueError, match="genesis"):
        canonical_journal_event_hash(
            draft,
            sequence=1,
            previous_hash=D0,
            created_at=NOW,
        )
    with pytest.raises(ValueError, match="genesis"):
        canonical_journal_event_hash(
            draft,
            sequence=2,
            previous_hash=first_genesis,
            created_at=NOW,
        )


def test_row_checksum_covers_the_complete_persisted_row_and_validates_both_hashes() -> None:
    draft = _draft()
    event = _event(
        draft,
        sequence=1,
        previous_hash=canonical_journal_genesis_hash(PARTITION),
        created_at=NOW,
    )

    preimage = journal_row_checksum_preimage(event)
    expected_row = event.model_dump(by_alias=True, mode="json")
    expected_row.pop("rowChecksum")
    assert preimage == {
        "domain": "magi.journal.row_checksum",
        "schemaVersion": 1,
        "row": expected_row,
    }
    assert canonical_journal_row_checksum(event) == event.row_checksum
    assert validate_journal_event_integrity(event) is event

    corrupt_hash = event.model_dump(by_alias=True, mode="json")
    corrupt_hash["eventHash"] = D0
    with pytest.raises(ValueError, match="eventHash"):
        validate_journal_event_integrity(JournalEvent.model_validate(corrupt_hash))

    corrupt_checksum = event.model_dump(by_alias=True, mode="json")
    corrupt_checksum["rowChecksum"] = D0
    with pytest.raises(ValueError, match="rowChecksum"):
        validate_journal_event_integrity(JournalEvent.model_validate(corrupt_checksum))


def test_append_with_outbox_receipt_crossbinds_the_atomic_result() -> None:
    draft = _draft()
    genesis = canonical_journal_genesis_hash(PARTITION)
    expected_head = JournalHead(
        schemaVersion=1,
        partitionId=PARTITION,
        sequence=0,
        eventHash=genesis,
        compareVersion=4,
    )
    outbox = _outbox_draft()
    request = AppendWithOutboxRequest(
        schemaVersion=1,
        draft=draft,
        outbox=outbox,
        expectedJournalHead=expected_head,
    )
    event = _event(draft, sequence=1, previous_hash=genesis, created_at=NOW)
    item = _pending_item(event, outbox)
    resulting_head = JournalHead(
        schemaVersion=1,
        partitionId=PARTITION,
        sequence=1,
        eventHash=event.event_hash,
        compareVersion=5,
    )

    receipt = AppendWithOutboxReceipt(
        schemaVersion=1,
        request=request,
        event=event,
        outboxItem=item,
        resultingJournalHead=resulting_head,
        committedAt=NOW,
    )

    assert receipt.event.event_id == draft.event_id
    assert receipt.outbox_item.event_hash == event.event_hash
    assert receipt.resulting_journal_head.compare_version == 5


def test_append_with_outbox_rejects_cross_partition_requests() -> None:
    with pytest.raises(ValidationError, match="partition"):
        AppendWithOutboxRequest(
            schemaVersion=1,
            draft=_draft(),
            outbox=_outbox_draft(partition_id="task:task_02:1"),
            expectedJournalHead=JournalHead(
                schemaVersion=1,
                partitionId=PARTITION,
                sequence=0,
                eventHash=canonical_journal_genesis_hash(PARTITION),
                compareVersion=0,
            ),
        )


@pytest.mark.parametrize(
    ("target", "field", "value", "message"),
    (
        ("event", "actorId", "actor_other", "event"),
        ("outbox", "subjectId", "completion_other", "outbox"),
        ("outbox", "kind", "diagnostic_delivery", "outbox"),
        ("outbox", "payloadJson", '{"messageRef":"other"}', "payload"),
        ("head", "compareVersion", 9, "compareVersion"),
    ),
)
def test_append_with_outbox_rejects_any_result_drift(
    target: str,
    field: str,
    value: object,
    message: str,
) -> None:
    draft = _draft()
    genesis = canonical_journal_genesis_hash(PARTITION)
    expected_head = JournalHead(
        schemaVersion=1,
        partitionId=PARTITION,
        sequence=0,
        eventHash=genesis,
        compareVersion=7,
    )
    request = AppendWithOutboxRequest(
        schemaVersion=1,
        draft=draft,
        outbox=_outbox_draft(),
        expectedJournalHead=expected_head,
    )
    event = _event(draft, sequence=1, previous_hash=genesis, created_at=NOW)
    item = _pending_item(event, request.outbox)
    head = JournalHead(
        schemaVersion=1,
        partitionId=PARTITION,
        sequence=1,
        eventHash=event.event_hash,
        compareVersion=8,
    )
    payload = {
        "schemaVersion": 1,
        "request": request,
        "event": event,
        "outboxItem": item,
        "resultingJournalHead": head,
        "committedAt": NOW,
    }
    selected = payload[
        {"event": "event", "outbox": "outboxItem", "head": "resultingJournalHead"}[target]
    ]
    assert hasattr(selected, "model_dump")
    mutated = selected.model_dump(by_alias=True, mode="json")
    mutated[field] = value
    if target == "event":
        payload["event"] = JournalEvent.model_validate(mutated)
    elif target == "outbox":
        if field == "payloadJson":
            mutated["payloadDigest"] = "sha256:" + sha256(str(value).encode()).hexdigest()
        payload["outboxItem"] = OutboxItem.model_validate(mutated)
    else:
        payload["resultingJournalHead"] = JournalHead.model_validate(mutated)

    with pytest.raises(ValidationError, match=message):
        AppendWithOutboxReceipt.model_validate(payload)


def test_outbox_claim_receipt_binds_cas_new_fence_and_expiry() -> None:
    draft = _draft()
    event = _event(
        draft,
        sequence=1,
        previous_hash=canonical_journal_genesis_hash(PARTITION),
        created_at=NOW,
    )
    pending = _pending_item(event, _outbox_draft())
    request = OutboxClaimRequest(
        schemaVersion=1,
        outboxId=pending.outbox_id,
        ownerId="delivery_01",
        claimTtlSeconds=300,
        expectedDeliveryAttempt=0,
        expectedCompareVersion=1,
        expectedFencingTokenHighWater=8,
    )
    claimed = _claimed_item(pending)

    receipt = OutboxClaimReceipt(
        schemaVersion=1,
        request=request,
        previousItem=pending,
        resultingItem=claimed,
        previousFencingTokenHighWater=8,
        resultingFencingTokenHighWater=9,
        claimedAt=NOW,
    )

    assert receipt.resulting_item.claim_fencing_token == 9
    assert receipt.resulting_item.compare_version == pending.compare_version + 1


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("expectedCompareVersion", 0, "expectedCompareVersion"),
        ("expectedFencingTokenHighWater", 7, "high-water"),
        ("expectedDeliveryAttempt", 1, "expectedDeliveryAttempt"),
    ),
)
def test_outbox_claim_rejects_stale_expected_state(
    field: str,
    value: object,
    message: str,
) -> None:
    event = _event(
        _draft(),
        sequence=1,
        previous_hash=canonical_journal_genesis_hash(PARTITION),
        created_at=NOW,
    )
    pending = _pending_item(event, _outbox_draft())
    claimed = _claimed_item(pending)
    request_payload = {
        "schemaVersion": 1,
        "outboxId": pending.outbox_id,
        "ownerId": "delivery_01",
        "claimTtlSeconds": 300,
        "expectedDeliveryAttempt": 0,
        "expectedCompareVersion": 1,
        "expectedFencingTokenHighWater": 8,
    }
    request_payload[field] = value

    with pytest.raises(ValidationError, match=message):
        OutboxClaimReceipt(
            schemaVersion=1,
            request=OutboxClaimRequest.model_validate(request_payload),
            previousItem=pending,
            resultingItem=claimed,
            previousFencingTokenHighWater=8,
            resultingFencingTokenHighWater=9,
            claimedAt=NOW,
        )


def test_outbox_attempt_receipt_increments_only_attempt_and_cas() -> None:
    event = _event(
        _draft(),
        sequence=1,
        previous_hash=canonical_journal_genesis_hash(PARTITION),
        created_at=NOW,
    )
    claimed = _claimed_item(_pending_item(event, _outbox_draft()))
    attempted = _claimed_item(claimed, compare_version=3, delivery_attempt=1)
    request = OutboxAttemptRequest(
        schemaVersion=1,
        outboxId=claimed.outbox_id,
        ownerId="delivery_01",
        claimFencingToken=9,
        subjectDigest=claimed.subject_digest,
        payloadDigest=claimed.payload_digest,
        expectedDeliveryAttempt=0,
        expectedCompareVersion=2,
    )

    receipt = OutboxAttemptReceipt(
        schemaVersion=1,
        request=request,
        previousItem=claimed,
        resultingItem=attempted,
        fencingTokenHighWater=9,
        attemptedAt=NOW + timedelta(minutes=1),
    )

    assert receipt.resulting_item.delivery_attempt == 1
    assert receipt.resulting_item.compare_version == 3


def test_outbox_attempt_rejects_expired_claim_and_identity_drift() -> None:
    event = _event(
        _draft(),
        sequence=1,
        previous_hash=canonical_journal_genesis_hash(PARTITION),
        created_at=NOW,
    )
    claimed = _claimed_item(_pending_item(event, _outbox_draft()))
    attempted = _claimed_item(claimed, compare_version=3, delivery_attempt=1)
    request = OutboxAttemptRequest(
        schemaVersion=1,
        outboxId=claimed.outbox_id,
        ownerId="delivery_01",
        claimFencingToken=9,
        subjectDigest=D2,
        payloadDigest=claimed.payload_digest,
        expectedDeliveryAttempt=0,
        expectedCompareVersion=2,
    )

    with pytest.raises(ValidationError, match="subjectDigest"):
        OutboxAttemptReceipt(
            schemaVersion=1,
            request=request,
            previousItem=claimed,
            resultingItem=attempted,
            fencingTokenHighWater=9,
            attemptedAt=NOW + timedelta(minutes=6),
        )

    correct_request = OutboxAttemptRequest.model_validate(
        {**request.model_dump(by_alias=True), "subjectDigest": claimed.subject_digest}
    )
    with pytest.raises(ValidationError, match="expired"):
        OutboxAttemptReceipt(
            schemaVersion=1,
            request=correct_request,
            previousItem=claimed,
            resultingItem=attempted,
            fencingTokenHighWater=9,
            attemptedAt=NOW + timedelta(minutes=6),
        )


def test_outbox_ack_receipt_delivers_exact_attempt_and_retains_fence_high_water() -> None:
    event = _event(
        _draft(),
        sequence=1,
        previous_hash=canonical_journal_genesis_hash(PARTITION),
        created_at=NOW,
    )
    attempted = _claimed_item(
        _pending_item(event, _outbox_draft()),
        compare_version=3,
        delivery_attempt=1,
    )
    delivered = _delivered_item(attempted, compare_version=4)
    request = OutboxAckRequest(
        schemaVersion=1,
        outboxId=attempted.outbox_id,
        ownerId="delivery_01",
        claimFencingToken=9,
        subjectDigest=attempted.subject_digest,
        payloadDigest=attempted.payload_digest,
        acknowledgementDigest=D5,
        deliveryAttempt=1,
        expectedCompareVersion=3,
    )

    receipt = OutboxAckReceipt(
        schemaVersion=1,
        request=request,
        previousItem=attempted,
        resultingItem=delivered,
        fencingTokenHighWater=9,
        acknowledgedAt=NOW + timedelta(minutes=2),
    )

    assert receipt.resulting_item.state is OutboxState.DELIVERED
    assert receipt.resulting_item.delivery_attempt == 1
    assert receipt.resulting_item.acknowledgement_digest == D5


def test_outbox_ack_rejects_unattempted_or_stale_claims() -> None:
    event = _event(
        _draft(),
        sequence=1,
        previous_hash=canonical_journal_genesis_hash(PARTITION),
        created_at=NOW,
    )
    claimed = _claimed_item(_pending_item(event, _outbox_draft()))
    delivered = _delivered_item(claimed, compare_version=3)
    request = OutboxAckRequest(
        schemaVersion=1,
        outboxId=claimed.outbox_id,
        ownerId="delivery_01",
        claimFencingToken=9,
        subjectDigest=claimed.subject_digest,
        payloadDigest=claimed.payload_digest,
        acknowledgementDigest=D5,
        deliveryAttempt=1,
        expectedCompareVersion=2,
    )

    with pytest.raises(ValidationError, match="deliveryAttempt"):
        OutboxAckReceipt(
            schemaVersion=1,
            request=request,
            previousItem=claimed,
            resultingItem=delivered,
            fencingTokenHighWater=9,
            acknowledgedAt=NOW + timedelta(minutes=1),
        )


def _three_event_chain() -> tuple[JournalEvent, JournalEvent, JournalEvent]:
    genesis = canonical_journal_genesis_hash(PARTITION)
    first = _event(_draft(event_id="event_01"), sequence=1, previous_hash=genesis, created_at=NOW)
    second = _event(
        _draft(event_id="event_02"),
        sequence=2,
        previous_hash=first.event_hash,
        created_at=NOW + timedelta(seconds=1),
    )
    third = _event(
        _draft(event_id="event_03"),
        sequence=3,
        previous_hash=second.event_hash,
        created_at=NOW + timedelta(seconds=2),
    )
    return first, second, third


def test_partition_read_receipt_is_bounded_and_anchored_to_one_capture_head() -> None:
    first, second, third = _three_event_chain()
    request = ReadPartitionRequest(
        schemaVersion=1,
        partitionId=PARTITION,
        afterSequence=0,
        limit=2,
    )
    receipt = ReadPartitionReceipt(
        schemaVersion=1,
        request=request,
        startAnchor=JournalChainAnchor(
            schemaVersion=1,
            partitionId=PARTITION,
            sequence=0,
            eventHash=canonical_journal_genesis_hash(PARTITION),
        ),
        captureHead=JournalHead(
            schemaVersion=1,
            partitionId=PARTITION,
            sequence=3,
            eventHash=third.event_hash,
            compareVersion=3,
        ),
        events=(first, second),
        hasMore=True,
        capturedAt=NOW + timedelta(seconds=3),
    )

    assert len(receipt.events) == request.limit
    assert receipt.events[-1].sequence < receipt.capture_head.sequence

    final_page = ReadPartitionReceipt(
        schemaVersion=1,
        request=ReadPartitionRequest(
            schemaVersion=1,
            partitionId=PARTITION,
            afterSequence=2,
            limit=2,
        ),
        startAnchor=JournalChainAnchor(
            schemaVersion=1,
            partitionId=PARTITION,
            sequence=2,
            eventHash=second.event_hash,
        ),
        captureHead=receipt.capture_head,
        events=(third,),
        hasMore=False,
        capturedAt=NOW + timedelta(seconds=3),
    )
    assert final_page.events[-1].event_hash == final_page.capture_head.event_hash


@pytest.mark.parametrize("mutation", ("gap", "anchor", "capture", "partition", "has_more"))
def test_partition_read_receipt_rejects_unanchored_or_unbounded_results(
    mutation: str,
) -> None:
    first, second, third = _three_event_chain()
    request = ReadPartitionRequest(
        schemaVersion=1,
        partitionId=PARTITION,
        afterSequence=0,
        limit=2,
    )
    anchor = JournalChainAnchor(
        schemaVersion=1,
        partitionId=PARTITION,
        sequence=0,
        eventHash=canonical_journal_genesis_hash(PARTITION),
    )
    head = JournalHead(
        schemaVersion=1,
        partitionId=PARTITION,
        sequence=3,
        eventHash=third.event_hash,
        compareVersion=3,
    )
    events: tuple[JournalEvent, ...] = (first, second)
    has_more = True
    if mutation == "gap":
        events = (first, third)
    elif mutation == "anchor":
        anchor = JournalChainAnchor(
            schemaVersion=1,
            partitionId=PARTITION,
            sequence=1,
            eventHash=first.event_hash,
        )
    elif mutation == "capture":
        head = JournalHead(
            schemaVersion=1,
            partitionId=PARTITION,
            sequence=2,
            eventHash=D0,
            compareVersion=2,
        )
    elif mutation == "partition":
        request = ReadPartitionRequest(
            schemaVersion=1,
            partitionId="task:task_02:1",
            afterSequence=0,
            limit=2,
        )
    else:
        has_more = False

    with pytest.raises(ValidationError):
        ReadPartitionReceipt(
            schemaVersion=1,
            request=request,
            startAnchor=anchor,
            captureHead=head,
            events=events,
            hasMore=has_more,
            capturedAt=NOW + timedelta(seconds=3),
        )


def test_partition_read_receipt_cannot_hide_available_results_or_future_events() -> None:
    first, _, third = _three_event_chain()
    request = ReadPartitionRequest(
        schemaVersion=1,
        partitionId=PARTITION,
        afterSequence=0,
        limit=2,
    )
    anchor = JournalChainAnchor(
        schemaVersion=1,
        partitionId=PARTITION,
        sequence=0,
        eventHash=canonical_journal_genesis_hash(PARTITION),
    )
    head = JournalHead(
        schemaVersion=1,
        partitionId=PARTITION,
        sequence=3,
        eventHash=third.event_hash,
        compareVersion=3,
    )
    with pytest.raises(ValidationError, match="available"):
        ReadPartitionReceipt(
            schemaVersion=1,
            request=request,
            startAnchor=anchor,
            captureHead=head,
            events=(),
            hasMore=True,
            capturedAt=NOW + timedelta(seconds=3),
        )

    with pytest.raises(ValidationError, match="capturedAt"):
        ReadPartitionReceipt(
            schemaVersion=1,
            request=ReadPartitionRequest(
                schemaVersion=1,
                partitionId=PARTITION,
                afterSequence=0,
                limit=1,
            ),
            startAnchor=anchor,
            captureHead=head,
            events=(first,),
            hasMore=True,
            capturedAt=NOW - timedelta(seconds=1),
        )
