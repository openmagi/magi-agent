"""First-party journal integrity and transactional outbox wire contracts.

The module is deliberately persistence-free.  It defines the values a durable
store must calculate and return, but it does not activate a database adapter or
choose a migration.  Every hash is domain separated and every mutation receipt
binds the caller's expected compare version to the exact post-state.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from hashlib import sha256
import json
import re
from typing import Literal, Self

from pydantic import Field, model_validator

from magi_agent.execution_authority.envelopes import (
    EnvelopeModel,
    GenericJournalEventDraft,
    JournalEvent,
    JournalEventDraft,
    JournalHead,
    OutboxDraft,
    OutboxItem,
)
from magi_agent.execution_authority.state_machine import OutboxState
from magi_agent.ops.safety import require_digest


JOURNAL_EVENT_HASH_DOMAIN = "magi.journal.event_hash"
JOURNAL_ROW_CHECKSUM_DOMAIN = "magi.journal.row_checksum"
JOURNAL_GENESIS_DOMAIN = "magi.journal.genesis"

MAX_PAYLOAD_BYTES = 1_048_576
MAX_PAYLOAD_DEPTH = 32
MAX_PAYLOAD_NODES = 10_000
MAX_PARTITION_READ_LIMIT = 1_000
MAX_IJSON_INTEGER = (2**53) - 1

RESERVED_EVENT_NAMESPACES: frozenset[str] = frozenset(
    {
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
    }
)

_EVENT_TYPE_RE = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_SENSITIVE_COMPACT_KEY_PARTS: tuple[str, ...] = (
    "accesstoken",
    "refreshtoken",
    "idtoken",
    "authtoken",
    "authorization",
    "cookie",
    "setcookie",
    "password",
    "passwd",
    "clientsecret",
    "signingsecret",
    "webhooksecret",
    "apikey",
    "privatekey",
    "serviceaccountkey",
    "awsaccesskeyid",
    "awssecretaccesskey",
    "credential",
    "sessionkey",
    "connectortoken",
)
_SENSITIVE_COMPACT_KEYS: frozenset[str] = frozenset(
    {
        "token",
        "secret",
        "authorization",
        "cookie",
        "password",
        "credential",
        "apikey",
        "privatekey",
    }
)
_SENSITIVE_VALUE_RE = re.compile(
    r"(?:"
    r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b|"
    r"\bAIza[A-Za-z0-9_-]{35}\b|"
    r"\b(?:xox[baprs]|xapp)-[A-Za-z0-9-]{12,}\b|"
    r"\bsk-[A-Za-z0-9._-]{8,}\b|"
    r"\bgh[opusr]_[A-Za-z0-9_]{8,}\b|"
    r"\b[rs]k_(?:live|test)_[A-Za-z0-9_]{8,}\b|"
    r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b|"
    r"(?:^|[^A-Za-z0-9_-])[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\."
    r"[A-Za-z0-9_-]{10,}(?:$|[^A-Za-z0-9_-])|"
    r"(?s:-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----)|"
    r"\b(?:Proxy-)?Authorization\s*:\s*[^\n\r,;}\"']+|"
    r"\bBearer\s+[A-Za-z0-9._~+/=-]+|"
    r"\bx-amz-(?:credential|signature)\b|"
    r"\bx-goog-(?:credential|signature)\b|"
    r"(?:^|[?&])sig=[^&\s]+|"
    r"(?:^|[\s\"'])/(?:Users|home|workspace|data/bots|private/var|var/lib)"
    r"(?:/[^\s,;}\"']*)?"
    r")",
    re.IGNORECASE,
)

_STORED_EVENT_FIELDS: frozenset[str] = frozenset(
    {"sequence", "previousHash", "eventHash", "rowChecksum", "createdAt"}
)
_OUTBOX_IDENTITY_FIELDS: tuple[str, ...] = (
    "outbox_id",
    "partition_id",
    "subject_id",
    "subject_digest",
    "event_id",
    "event_sequence",
    "event_hash",
    "kind",
    "payload_digest",
    "payload_json",
)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _canonical_digest(value: Mapping[str, object]) -> str:
    try:
        encoded = _canonical_json(value).encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("canonical JSON must contain valid UTF-8 text") from exc
    return "sha256:" + sha256(encoded).hexdigest()


def _preflight_json_depth(payload_json: str) -> None:
    depth = 0
    in_string = False
    escaped = False
    for character in payload_json:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character in "[{":
            depth += 1
            if depth > MAX_PAYLOAD_DEPTH:
                raise ValueError("payload JSON exceeds the validation depth budget")
        elif character in "]}":
            depth -= 1
            if depth < 0:
                raise ValueError("payload JSON has unbalanced containers")


def _parse_payload_json(payload_json: str) -> dict[str, object]:
    if type(payload_json) is not str:
        raise TypeError("payload JSON must be an exact string")
    try:
        encoded = payload_json.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("payload JSON must contain valid UTF-8 text") from exc
    if len(encoded) > MAX_PAYLOAD_BYTES:
        raise ValueError("payload JSON exceeds the byte limit")
    _preflight_json_depth(payload_json)

    def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("payload JSON contains a duplicate key")
            result[key] = value
        return result

    try:
        parsed = json.loads(
            payload_json,
            object_pairs_hook=_pairs,
            parse_float=lambda value: (_ for _ in ()).throw(
                ValueError(f"payload JSON contains floating-point number {value}")
            ),
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"payload JSON contains non-finite number {value}")
            ),
        )
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("payload JSON is invalid") from exc
    if type(parsed) is not dict:
        raise ValueError("payload JSON requires an object root")
    return parsed


def _compact_key(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def _is_sensitive_key(key: str) -> bool:
    compact = _compact_key(key)
    return compact in _SENSITIVE_COMPACT_KEYS or any(
        part in compact for part in _SENSITIVE_COMPACT_KEY_PARTS
    )


def _is_sensitive_value(value: str) -> bool:
    return _SENSITIVE_VALUE_RE.search(value) is not None


def _validate_payload_tree(value: object, *, inspect_secrets: bool = True) -> None:
    pending: list[tuple[object, int]] = [(value, 0)]
    visited = 0
    while pending:
        current, depth = pending.pop()
        visited += 1
        if visited > MAX_PAYLOAD_NODES:
            raise ValueError("payload exceeds the validation node budget")
        if depth > MAX_PAYLOAD_DEPTH:
            raise ValueError("payload exceeds the validation depth budget")
        if type(current) is dict:
            for key, child in current.items():
                if type(key) is not str:
                    raise ValueError("payload object keys must be exact strings")
                try:
                    key.encode("utf-8")
                except UnicodeEncodeError as exc:
                    raise ValueError("payload keys must contain valid UTF-8 text") from exc
                if inspect_secrets and _is_sensitive_key(key):
                    raise ValueError("payload contains a sensitive key")
                pending.append((child, depth + 1))
        elif type(current) is list:
            pending.extend((child, depth + 1) for child in current)
        elif type(current) is str:
            try:
                current.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise ValueError("payload strings must contain valid UTF-8 text") from exc
            if inspect_secrets and _is_sensitive_value(current):
                raise ValueError("payload contains a sensitive value")
        elif type(current) is int:
            if abs(current) > MAX_IJSON_INTEGER:
                raise ValueError("payload integer exceeds the I-JSON safe range")
        elif type(current) in (bool, type(None)):
            continue
        elif type(current) is float:
            raise ValueError("payload must not contain floating-point values")
        else:
            raise ValueError("payload must contain only exact canonical JSON values")


def canonical_safe_object_json(payload: dict[str, object]) -> str:
    """Serialize one secret-free exact JSON object into canonical v1 bytes."""

    if type(payload) is not dict:
        raise ValueError("payload requires an exact object root")
    # Reject Python-only containers, unsafe numbers, depth bombs, and node bombs
    # before invoking either the JSON encoder or secret regexes.
    _validate_payload_tree(payload, inspect_secrets=False)
    try:
        encoded = _canonical_json(payload)
        byte_length = len(encoded.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ValueError("payload JSON must contain valid UTF-8 text") from exc
    except (TypeError, ValueError) as exc:
        raise ValueError("payload must contain only canonical JSON values") from exc
    if byte_length > MAX_PAYLOAD_BYTES:
        raise ValueError("payload JSON exceeds the byte limit")
    # Bound raw bytes before running the intentionally broad secret patterns;
    # regex work must never scale beyond the accepted payload budget.
    _validate_payload_tree(payload)
    return encoded


def validate_canonical_safe_object_json(payload_json: str) -> dict[str, object]:
    """Parse and validate canonical, duplicate-free, object-root JSON."""

    parsed = _parse_payload_json(payload_json)
    _validate_payload_tree(parsed)
    if _canonical_json(parsed) != payload_json:
        raise ValueError("payload JSON is not canonical")
    return parsed


def require_journal_event_type(event_type: str) -> str:
    """Require the lowercase v1 journal event-type grammar."""

    if (
        type(event_type) is not str
        or len(event_type) > 128
        or not _EVENT_TYPE_RE.fullmatch(event_type)
    ):
        raise ValueError("eventType must use the exact lowercase journal grammar")
    return event_type


def require_generic_event_type(event_type: str) -> str:
    """Require a valid event type outside every first-party lifecycle namespace."""

    event_type = require_journal_event_type(event_type)
    if event_type.split(".", 1)[0] in RESERVED_EVENT_NAMESPACES:
        raise ValueError("eventType uses a reserved first-party lifecycle namespace")
    return event_type


def canonical_journal_genesis_hash(partition_id: str) -> str:
    """Return the domain-separated, partition-scoped v1 genesis anchor."""

    if type(partition_id) is not str or not partition_id or partition_id != partition_id.strip():
        raise ValueError("partitionId must be a non-empty exact string")
    return _canonical_digest(
        {
            "domain": JOURNAL_GENESIS_DOMAIN,
            "schemaVersion": 1,
            "partitionId": partition_id,
        }
    )


def _canonical_utc_timestamp(value: datetime, *, field_name: str) -> str:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{field_name} must be an exact UTC datetime")
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _require_sequence_and_previous_hash(
    *,
    partition_id: str,
    sequence: int,
    previous_hash: str,
) -> None:
    if type(sequence) is not int or sequence < 1:
        raise ValueError("journal sequence must be a positive exact integer")
    require_digest(previous_hash)
    genesis = canonical_journal_genesis_hash(partition_id)
    if sequence == 1 and previous_hash != genesis:
        raise ValueError("sequence 1 must use the partition genesis hash")
    if sequence > 1 and previous_hash == genesis:
        raise ValueError("only sequence 1 may use the partition genesis hash")


def _draft_headers(draft: JournalEventDraft) -> dict[str, object]:
    values = draft.model_dump(by_alias=True, mode="json")
    values.pop("payloadJson")
    return values


def journal_event_hash_preimage(
    draft: JournalEventDraft,
    *,
    sequence: int,
    previous_hash: str,
    created_at: datetime,
) -> dict[str, object]:
    """Build the exact v1 event-hash preimage.

    ``payloadJson`` is represented by its already-validated ``payloadDigest``;
    the row checksum separately covers the physical payload bytes.
    """

    if not isinstance(draft, JournalEventDraft):
        raise TypeError("draft must be a JournalEventDraft")
    require_journal_event_type(draft.event_type)
    validate_canonical_safe_object_json(draft.payload_json)
    _require_sequence_and_previous_hash(
        partition_id=draft.partition_id,
        sequence=sequence,
        previous_hash=previous_hash,
    )
    return {
        "domain": JOURNAL_EVENT_HASH_DOMAIN,
        "schemaVersion": 1,
        "headers": _draft_headers(draft),
        "sequence": sequence,
        "previousHash": previous_hash,
        "createdAt": _canonical_utc_timestamp(created_at, field_name="createdAt"),
    }


def canonical_journal_event_hash(
    draft: JournalEventDraft,
    *,
    sequence: int,
    previous_hash: str,
    created_at: datetime,
) -> str:
    """Hash one exact journal event using the v1 domain."""

    return _canonical_digest(
        journal_event_hash_preimage(
            draft,
            sequence=sequence,
            previous_hash=previous_hash,
            created_at=created_at,
        )
    )


def _event_as_draft(event: JournalEvent) -> JournalEventDraft:
    values = event.model_dump(by_alias=True, mode="json")
    for field in _STORED_EVENT_FIELDS:
        values.pop(field)
    return JournalEventDraft.model_validate(values)


def journal_row_checksum_preimage(event: JournalEvent) -> dict[str, object]:
    """Build the exact v1 checksum preimage for the complete physical row."""

    if not isinstance(event, JournalEvent):
        raise TypeError("event must be a JournalEvent")
    expected_event_hash = canonical_journal_event_hash(
        _event_as_draft(event),
        sequence=event.sequence,
        previous_hash=event.previous_hash,
        created_at=event.created_at,
    )
    if event.event_hash != expected_event_hash:
        raise ValueError("eventHash does not match the canonical event preimage")
    row = event.model_dump(by_alias=True, mode="json")
    row.pop("rowChecksum")
    return {
        "domain": JOURNAL_ROW_CHECKSUM_DOMAIN,
        "schemaVersion": 1,
        "row": row,
    }


def canonical_journal_row_checksum(event: JournalEvent) -> str:
    """Checksum every persisted event column except ``rowChecksum`` itself."""

    return _canonical_digest(journal_row_checksum_preimage(event))


def validate_journal_event_integrity(event: JournalEvent) -> JournalEvent:
    """Validate both the logical event hash and physical row checksum."""

    expected_checksum = canonical_journal_row_checksum(event)
    if event.row_checksum != expected_checksum:
        raise ValueError("rowChecksum does not match the complete persisted row")
    return event


def _validate_head_genesis(head: JournalHead) -> None:
    genesis = canonical_journal_genesis_hash(head.partition_id)
    if head.sequence == 0 and head.event_hash != genesis:
        raise ValueError("an empty journal head must use the partition genesis hash")
    if head.sequence > 0 and head.event_hash == genesis:
        raise ValueError("a non-empty journal head cannot use the partition genesis hash")


def _require_same_outbox_identity(first: OutboxItem, second: OutboxItem) -> None:
    for field in _OUTBOX_IDENTITY_FIELDS:
        if getattr(first, field) != getattr(second, field):
            alias = type(first).model_fields[field].alias or field
            raise ValueError(f"resulting outbox {alias} differs from its previous item")


def _validate_outbox_payload(item: OutboxItem | OutboxDraft) -> None:
    validate_canonical_safe_object_json(item.payload_json)


class AppendWithOutboxRequest(EnvelopeModel):
    """One generic event and delivery request committed in one transaction."""

    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    draft: GenericJournalEventDraft
    outbox: OutboxDraft
    expected_journal_head: JournalHead = Field(alias="expectedJournalHead")

    @model_validator(mode="after")
    def _crossbind_request(self) -> Self:
        require_generic_event_type(self.draft.event_type)
        validate_canonical_safe_object_json(self.draft.payload_json)
        _validate_outbox_payload(self.outbox)
        _validate_head_genesis(self.expected_journal_head)
        partitions = {
            self.draft.partition_id,
            self.outbox.partition_id,
            self.expected_journal_head.partition_id,
        }
        if len(partitions) != 1:
            raise ValueError("append, outbox, and expected journal head must share one partition")
        return self


class AppendWithOutboxReceipt(EnvelopeModel):
    """Store-authored proof of one atomic journal+outbox commit."""

    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    request: AppendWithOutboxRequest
    event: JournalEvent
    outbox_item: OutboxItem = Field(alias="outboxItem")
    resulting_journal_head: JournalHead = Field(alias="resultingJournalHead")
    committed_at: datetime = Field(alias="committedAt")

    @model_validator(mode="after")
    def _crossbind_result(self) -> Self:
        validate_journal_event_integrity(self.event)
        request = self.request
        if _event_as_draft(self.event).model_dump(
            by_alias=True, mode="json"
        ) != request.draft.model_dump(
            by_alias=True,
            mode="json",
        ):
            raise ValueError("stored event differs from the append request")
        expected_head = request.expected_journal_head
        if self.event.sequence != expected_head.sequence + 1:
            raise ValueError("stored event must directly follow expectedJournalHead")
        if self.event.previous_hash != expected_head.event_hash:
            raise ValueError("stored event previousHash does not match expectedJournalHead")
        if self.event.created_at > self.committed_at:
            raise ValueError("committedAt cannot precede the stored event")

        draft = request.outbox
        item = self.outbox_item
        expected_outbox_values = {
            "outbox_id": draft.outbox_id,
            "partition_id": draft.partition_id,
            "subject_id": draft.subject_id,
            "subject_digest": draft.subject_digest,
            "kind": draft.kind,
            "payload_digest": draft.payload_digest,
            "payload_json": draft.payload_json,
        }
        for field, expected in expected_outbox_values.items():
            if getattr(item, field) != expected:
                alias = type(item).model_fields[field].alias or field
                if field in {"payload_digest", "payload_json"}:
                    raise ValueError(f"outbox payload {alias} differs from the request")
                raise ValueError(f"outbox {alias} differs from the request")
        if (
            item.event_id != self.event.event_id
            or item.event_sequence != self.event.sequence
            or item.event_hash != self.event.event_hash
        ):
            raise ValueError("outbox event binding differs from the stored event")
        _validate_outbox_payload(item)
        if item.state is not OutboxState.PENDING:
            raise ValueError("new outbox items must start pending")
        if item.delivery_attempt != 0:
            raise ValueError("new outbox items must start at deliveryAttempt 0")
        if item.compare_version != 1:
            raise ValueError("new outbox items must start at compareVersion 1")

        head = self.resulting_journal_head
        if (
            head.partition_id != self.event.partition_id
            or head.sequence != self.event.sequence
            or head.event_hash != self.event.event_hash
        ):
            raise ValueError("resultingJournalHead does not identify the committed event")
        if head.compare_version != expected_head.compare_version + 1:
            raise ValueError("resultingJournalHead compareVersion must advance by one")
        return self


class OutboxClaimRequest(EnvelopeModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    outbox_id: str = Field(alias="outboxId", min_length=1)
    owner_id: str = Field(alias="ownerId", min_length=1)
    claim_ttl_seconds: int = Field(alias="claimTtlSeconds", ge=1, le=86_400, strict=True)
    expected_delivery_attempt: int = Field(
        alias="expectedDeliveryAttempt",
        ge=0,
        strict=True,
    )
    expected_compare_version: int = Field(
        alias="expectedCompareVersion",
        ge=0,
        strict=True,
    )
    expected_fencing_token_high_water: int = Field(
        alias="expectedFencingTokenHighWater",
        ge=0,
        strict=True,
    )


class OutboxClaimReceipt(EnvelopeModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    request: OutboxClaimRequest
    previous_item: OutboxItem = Field(alias="previousItem")
    resulting_item: OutboxItem = Field(alias="resultingItem")
    previous_fencing_token_high_water: int = Field(
        alias="previousFencingTokenHighWater",
        ge=0,
        strict=True,
    )
    resulting_fencing_token_high_water: int = Field(
        alias="resultingFencingTokenHighWater",
        ge=1,
        strict=True,
    )
    claimed_at: datetime = Field(alias="claimedAt")

    @model_validator(mode="after")
    def _validate_claim_transition(self) -> Self:
        request = self.request
        previous = self.previous_item
        result = self.resulting_item
        _validate_outbox_payload(previous)
        _validate_outbox_payload(result)
        _require_same_outbox_identity(previous, result)
        if request.outbox_id != previous.outbox_id:
            raise ValueError("claim outboxId does not match previousItem")
        if previous.state is not OutboxState.PENDING:
            raise ValueError("outbox claims require a pending previousItem")
        if previous.delivery_attempt != request.expected_delivery_attempt:
            raise ValueError("expectedDeliveryAttempt does not match previousItem")
        if previous.delivery_attempt != 0:
            raise ValueError("a pending outbox item must have deliveryAttempt 0")
        if previous.compare_version != request.expected_compare_version:
            raise ValueError("expectedCompareVersion does not match previousItem")
        if request.expected_fencing_token_high_water != self.previous_fencing_token_high_water:
            raise ValueError("expected fencing high-water does not match the receipt")
        if self.resulting_fencing_token_high_water != self.previous_fencing_token_high_water + 1:
            raise ValueError("claim fencing high-water must advance by one")
        if result.state is not OutboxState.CLAIMED:
            raise ValueError("claim result must be claimed")
        if result.claim_owner_id != request.owner_id:
            raise ValueError("claim result ownerId does not match the request")
        if result.claim_fencing_token != self.resulting_fencing_token_high_water:
            raise ValueError("claim result fencing token must equal the new high-water")
        if result.claim_expires_at != self.claimed_at + timedelta(
            seconds=request.claim_ttl_seconds
        ):
            raise ValueError("claimExpiresAt must equal claimedAt plus claimTtlSeconds")
        if result.delivery_attempt != previous.delivery_attempt:
            raise ValueError("claim must not change deliveryAttempt")
        if result.compare_version != previous.compare_version + 1:
            raise ValueError("claim compareVersion must advance by one")
        return self


class OutboxAttemptRequest(EnvelopeModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    outbox_id: str = Field(alias="outboxId", min_length=1)
    owner_id: str = Field(alias="ownerId", min_length=1)
    claim_fencing_token: int = Field(alias="claimFencingToken", ge=1, strict=True)
    subject_digest: str = Field(alias="subjectDigest")
    payload_digest: str = Field(alias="payloadDigest")
    expected_delivery_attempt: int = Field(
        alias="expectedDeliveryAttempt",
        ge=0,
        strict=True,
    )
    expected_compare_version: int = Field(
        alias="expectedCompareVersion",
        ge=0,
        strict=True,
    )


def _validate_claim_identity(
    request: OutboxAttemptRequest | OutboxAckRequest,
    previous: OutboxItem,
) -> None:
    if request.outbox_id != previous.outbox_id:
        raise ValueError("outboxId does not match the claimed item")
    if request.owner_id != previous.claim_owner_id:
        raise ValueError("ownerId does not match the claimed item")
    if request.claim_fencing_token != previous.claim_fencing_token:
        raise ValueError("claimFencingToken does not match the claimed item")
    if request.subject_digest != previous.subject_digest:
        raise ValueError("subjectDigest does not match the claimed item")
    if request.payload_digest != previous.payload_digest:
        raise ValueError("payloadDigest does not match the claimed item")


class OutboxAttemptReceipt(EnvelopeModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    request: OutboxAttemptRequest
    previous_item: OutboxItem = Field(alias="previousItem")
    resulting_item: OutboxItem = Field(alias="resultingItem")
    fencing_token_high_water: int = Field(alias="fencingTokenHighWater", ge=1, strict=True)
    attempted_at: datetime = Field(alias="attemptedAt")

    @model_validator(mode="after")
    def _validate_attempt_transition(self) -> Self:
        request = self.request
        previous = self.previous_item
        result = self.resulting_item
        _validate_outbox_payload(previous)
        _validate_outbox_payload(result)
        _require_same_outbox_identity(previous, result)
        if previous.state is not OutboxState.CLAIMED or result.state is not OutboxState.CLAIMED:
            raise ValueError("delivery attempts require claimed previous and resulting items")
        _validate_claim_identity(request, previous)
        if request.expected_delivery_attempt != previous.delivery_attempt:
            raise ValueError("expectedDeliveryAttempt does not match the claimed item")
        if request.expected_compare_version != previous.compare_version:
            raise ValueError("expectedCompareVersion does not match the claimed item")
        if self.fencing_token_high_water != previous.claim_fencing_token:
            raise ValueError("fencing high-water must equal the active claim token")
        if previous.claim_expires_at is None or self.attempted_at > previous.claim_expires_at:
            raise ValueError("delivery attempt used an expired claim")
        if (
            result.claim_owner_id != previous.claim_owner_id
            or result.claim_fencing_token != previous.claim_fencing_token
            or result.claim_expires_at != previous.claim_expires_at
        ):
            raise ValueError("delivery attempt must preserve the active claim")
        if result.delivery_attempt != previous.delivery_attempt + 1:
            raise ValueError("delivery attempt must increment deliveryAttempt by one")
        if result.compare_version != previous.compare_version + 1:
            raise ValueError("delivery attempt compareVersion must advance by one")
        return self


class OutboxAckRequest(EnvelopeModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    outbox_id: str = Field(alias="outboxId", min_length=1)
    owner_id: str = Field(alias="ownerId", min_length=1)
    claim_fencing_token: int = Field(alias="claimFencingToken", ge=1, strict=True)
    subject_digest: str = Field(alias="subjectDigest")
    payload_digest: str = Field(alias="payloadDigest")
    acknowledgement_digest: str = Field(alias="acknowledgementDigest")
    delivery_attempt: int = Field(alias="deliveryAttempt", ge=1, strict=True)
    expected_compare_version: int = Field(
        alias="expectedCompareVersion",
        ge=0,
        strict=True,
    )


class OutboxAckReceipt(EnvelopeModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    request: OutboxAckRequest
    previous_item: OutboxItem = Field(alias="previousItem")
    resulting_item: OutboxItem = Field(alias="resultingItem")
    fencing_token_high_water: int = Field(alias="fencingTokenHighWater", ge=1, strict=True)
    acknowledged_at: datetime = Field(alias="acknowledgedAt")

    @model_validator(mode="after")
    def _validate_ack_transition(self) -> Self:
        request = self.request
        previous = self.previous_item
        result = self.resulting_item
        _validate_outbox_payload(previous)
        _validate_outbox_payload(result)
        _require_same_outbox_identity(previous, result)
        if previous.state is not OutboxState.CLAIMED:
            raise ValueError("acknowledgement requires a claimed previousItem")
        if result.state is not OutboxState.DELIVERED:
            raise ValueError("acknowledgement result must be delivered")
        _validate_claim_identity(request, previous)
        if request.delivery_attempt != previous.delivery_attempt:
            raise ValueError("deliveryAttempt does not match the attempted delivery")
        if previous.delivery_attempt < 1:
            raise ValueError("acknowledgement requires at least one recorded deliveryAttempt")
        if request.expected_compare_version != previous.compare_version:
            raise ValueError("expectedCompareVersion does not match the claimed item")
        if self.fencing_token_high_water != previous.claim_fencing_token:
            raise ValueError("fencing high-water must equal the active claim token")
        if previous.claim_expires_at is None or self.acknowledged_at > previous.claim_expires_at:
            raise ValueError("acknowledgement used an expired claim")
        if result.delivery_attempt != previous.delivery_attempt:
            raise ValueError("acknowledgement must preserve deliveryAttempt")
        if result.acknowledgement_digest != request.acknowledgement_digest:
            raise ValueError("acknowledgementDigest differs from the request")
        if result.compare_version != previous.compare_version + 1:
            raise ValueError("acknowledgement compareVersion must advance by one")
        return self


class JournalChainAnchor(EnvelopeModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    partition_id: str = Field(alias="partitionId", min_length=1)
    sequence: int = Field(ge=0, strict=True)
    event_hash: str = Field(alias="eventHash")

    @model_validator(mode="after")
    def _validate_genesis(self) -> Self:
        genesis = canonical_journal_genesis_hash(self.partition_id)
        if self.sequence == 0 and self.event_hash != genesis:
            raise ValueError("sequence 0 anchor must use the partition genesis hash")
        if self.sequence > 0 and self.event_hash == genesis:
            raise ValueError("non-zero anchor cannot use the partition genesis hash")
        return self


class ReadPartitionRequest(EnvelopeModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    partition_id: str = Field(alias="partitionId", min_length=1)
    after_sequence: int = Field(alias="afterSequence", ge=0, strict=True)
    limit: int = Field(ge=1, le=MAX_PARTITION_READ_LIMIT, strict=True)


class ReadPartitionReceipt(EnvelopeModel):
    """Bounded page proven against one head captured before the read."""

    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    request: ReadPartitionRequest
    start_anchor: JournalChainAnchor = Field(alias="startAnchor")
    capture_head: JournalHead = Field(alias="captureHead")
    events: tuple[JournalEvent, ...]
    has_more: bool = Field(alias="hasMore", strict=True)
    captured_at: datetime = Field(alias="capturedAt")

    @model_validator(mode="after")
    def _validate_captured_page(self) -> Self:
        request = self.request
        anchor = self.start_anchor
        head = self.capture_head
        _validate_head_genesis(head)
        if request.partition_id != anchor.partition_id or request.partition_id != head.partition_id:
            raise ValueError("request, startAnchor, and captureHead must share one partition")
        if request.after_sequence != anchor.sequence:
            raise ValueError("startAnchor sequence must equal afterSequence")
        if anchor.sequence > head.sequence:
            raise ValueError("startAnchor cannot be beyond captureHead")
        if anchor.sequence == head.sequence and anchor.event_hash != head.event_hash:
            raise ValueError("equal sequence anchors must have the same eventHash")
        if len(self.events) > request.limit:
            raise ValueError("partition result exceeds the requested limit")

        if not self.events:
            if anchor.sequence != head.sequence:
                raise ValueError("partition result omitted events available at captureHead")
            if self.has_more:
                raise ValueError("hasMore cannot be true for an empty complete partition page")
            return self

        first = self.events[0]
        if first.sequence != anchor.sequence + 1 or first.previous_hash != anchor.event_hash:
            raise ValueError("first event does not directly follow startAnchor")
        previous_sequence = anchor.sequence
        previous_hash = anchor.event_hash
        previous_created_at: datetime | None = None
        seen_event_ids: set[str] = set()
        seen_event_hashes: set[str] = set()
        for event in self.events:
            validate_journal_event_integrity(event)
            if event.partition_id != request.partition_id:
                raise ValueError("partition result contains an event from another partition")
            if event.sequence != previous_sequence + 1 or event.previous_hash != previous_hash:
                raise ValueError("partition result contains a journal chain gap")
            if event.event_id in seen_event_ids or event.event_hash in seen_event_hashes:
                raise ValueError("partition result repeats an event identity")
            if previous_created_at is not None and event.created_at < previous_created_at:
                raise ValueError("partition event createdAt values must be monotonic")
            if event.created_at > self.captured_at:
                raise ValueError("partition event createdAt cannot follow capturedAt")
            previous_sequence = event.sequence
            previous_hash = event.event_hash
            previous_created_at = event.created_at
            seen_event_ids.add(event.event_id)
            seen_event_hashes.add(event.event_hash)

        last = self.events[-1]
        if last.sequence > head.sequence:
            raise ValueError("partition result extends beyond captureHead")
        if last.sequence == head.sequence:
            if last.event_hash != head.event_hash:
                raise ValueError("captureHead eventHash differs from the terminal result event")
            if self.has_more:
                raise ValueError("hasMore cannot be true after reaching captureHead")
        else:
            if not self.has_more:
                raise ValueError("hasMore must be true before reaching captureHead")
            if len(self.events) != request.limit:
                raise ValueError("a partial partition page must fill the requested limit")
        return self


__all__ = [
    "AppendWithOutboxReceipt",
    "AppendWithOutboxRequest",
    "JOURNAL_EVENT_HASH_DOMAIN",
    "JOURNAL_GENESIS_DOMAIN",
    "JOURNAL_ROW_CHECKSUM_DOMAIN",
    "JournalChainAnchor",
    "MAX_PARTITION_READ_LIMIT",
    "OutboxAckReceipt",
    "OutboxAckRequest",
    "OutboxAttemptReceipt",
    "OutboxAttemptRequest",
    "OutboxClaimReceipt",
    "OutboxClaimRequest",
    "RESERVED_EVENT_NAMESPACES",
    "ReadPartitionReceipt",
    "ReadPartitionRequest",
    "canonical_journal_event_hash",
    "canonical_journal_genesis_hash",
    "canonical_journal_row_checksum",
    "canonical_safe_object_json",
    "journal_event_hash_preimage",
    "journal_row_checksum_preimage",
    "require_generic_event_type",
    "require_journal_event_type",
    "validate_canonical_safe_object_json",
    "validate_journal_event_integrity",
]
