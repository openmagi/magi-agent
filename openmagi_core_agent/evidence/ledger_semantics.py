from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


AuditLedgerMode = Literal["live", "cached", "replay"]
LedgerRecordKind = Literal[
    "tool_receipt",
    "model_receipt",
    "validator_verdict",
    "source_snapshot",
    "approval_receipt",
    "policy_snapshot",
    "projection_receipt",
    "cache_observation",
    "replay_observation",
    "tombstone",
    "redaction",
]

_DIGEST_PREFIX = "sha256:"
_SENSITIVE_REF_FRAGMENTS = ("api_key", "authorization", "cookie", "token=", "secret=")
_SENSITIVE_METADATA_KEYS = ("authorization", "cookie", "token", "secret", "api_key", "password", "prompt")


class ContentAddressedLedgerRecord(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    sequence: int
    kind: LedgerRecordKind
    payload_digest: str = Field(alias="payloadDigest")
    payload_ref: str = Field(alias="payloadRef")
    policy_snapshot_digest: str = Field(alias="policySnapshotDigest")
    previous_record_digest: str | None = Field(default=None, alias="previousRecordDigest")
    record_digest: str = Field(alias="recordDigest")
    target_record_digest: str | None = Field(default=None, alias="targetRecordDigest")
    metadata: Mapping[str, object] = Field(default_factory=dict)

    @field_validator("sequence")
    @classmethod
    def _validate_sequence(cls, value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError("sequence must be a positive integer")
        return value

    @field_validator(
        "payload_digest",
        "policy_snapshot_digest",
        "record_digest",
        "previous_record_digest",
        "target_record_digest",
    )
    @classmethod
    def _validate_optional_digest(cls, value: str | None, info: object) -> str | None:
        if value is None:
            return None
        return _require_digest(value, getattr(info, "field_name", "digest"))

    @field_validator("payload_ref")
    @classmethod
    def _validate_payload_ref(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("payloadRef must be non-empty")
        lowered = cleaned.lower()
        if any(fragment in lowered for fragment in _SENSITIVE_REF_FRAGMENTS):
            raise ValueError("payloadRef must not contain credential-like data")
        return cleaned

    @field_validator("metadata")
    @classmethod
    def _validate_metadata(cls, value: Mapping[str, object]) -> Mapping[str, object]:
        encoded = json.dumps(value, sort_keys=True, ensure_ascii=True).lower()
        if any(fragment in encoded for fragment in _SENSITIVE_METADATA_KEYS):
            raise ValueError("metadata must not contain raw prompts, credentials, or secret-like fields")
        return dict(value)

    @model_validator(mode="after")
    def _validate_target_digest_shape(self) -> Self:
        if self.kind in {"tombstone", "redaction"} and self.target_record_digest is None:
            raise ValueError("tombstone/redaction records require targetRecordDigest")
        if self.kind not in {"tombstone", "redaction"} and self.target_record_digest is not None:
            raise ValueError("targetRecordDigest is only valid for tombstone/redaction records")
        return self


class ContentAddressedLedger(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    ledger_id: str = Field(alias="ledgerId")
    session_id: str = Field(alias="sessionId")
    turn_id: str = Field(alias="turnId")
    mode: AuditLedgerMode
    records: tuple[ContentAddressedLedgerRecord, ...] = ()
    append_only: Literal[True] = Field(default=True, alias="appendOnly")
    content_addressed: Literal[True] = Field(default=True, alias="contentAddressed")
    source_ledger_digest: str | None = Field(default=None, alias="sourceLedgerDigest")

    @field_validator("ledger_id", "session_id", "turn_id")
    @classmethod
    def _reject_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("ledger identifiers must be non-empty")
        return value

    @field_validator("source_ledger_digest")
    @classmethod
    def _validate_source_digest(cls, value: str | None) -> str | None:
        if value is not None:
            return _require_digest(value, "sourceLedgerDigest")
        return None

    @property
    def ledger_digest(self) -> str:
        return _digest_json(
            {
                "ledgerId": self.ledger_id,
                "sessionId": self.session_id,
                "turnId": self.turn_id,
                "mode": self.mode,
                "recordDigests": [record.record_digest for record in self.records],
            }
        )

    @model_validator(mode="after")
    def _validate_record_chain_shape(self) -> Self:
        for index, record in enumerate(self.records, start=1):
            if record.sequence != index:
                raise ValueError("records must be sequential")
            expected_previous = None if index == 1 else self.records[index - 2].record_digest
            if record.previous_record_digest != expected_previous:
                raise ValueError("previousRecordDigest must match prior record")
        if self.mode in {"cached", "replay"} and self.source_ledger_digest is None:
            raise ValueError("cached/replay ledgers require sourceLedgerDigest")
        return self


class LedgerVerificationReport(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    ok: bool
    mode: AuditLedgerMode
    record_count: int = Field(alias="recordCount")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")


def append_ledger_record(
    ledger: ContentAddressedLedger,
    *,
    kind: LedgerRecordKind,
    payloadDigest: str,
    payloadRef: str,
    policySnapshotDigest: str,
    targetRecordDigest: str | None = None,
    metadata: Mapping[str, object] | None = None,
) -> ContentAddressedLedger:
    if ledger.mode == "cached" and kind != "cache_observation":
        raise ValueError("cached ledgers may only append cache_observation records")
    if ledger.mode == "replay" and kind != "replay_observation":
        raise ValueError("replay ledgers may only append replay_observation records")
    sequence = len(ledger.records) + 1
    previous = ledger.records[-1].record_digest if ledger.records else None
    provisional = {
        "sequence": sequence,
        "kind": kind,
        "payloadDigest": payloadDigest,
        "payloadRef": payloadRef,
        "policySnapshotDigest": policySnapshotDigest,
        "previousRecordDigest": previous,
        "targetRecordDigest": targetRecordDigest,
        "metadata": dict(metadata or {}),
    }
    record = ContentAddressedLedgerRecord(
        **provisional,
        recordDigest=_digest_json(provisional),
    )
    return ledger.model_copy(update={"records": (*ledger.records, record)})


def verify_ledger_chain(ledger: ContentAddressedLedger) -> LedgerVerificationReport:
    reason_codes: list[str] = []
    for index, record in enumerate(ledger.records, start=1):
        if record.sequence != index:
            reason_codes.append("sequence_mismatch")
            continue
        expected_previous = None if index == 1 else ledger.records[index - 2].record_digest
        if record.previous_record_digest != expected_previous:
            reason_codes.append("previous_record_digest_mismatch")
        expected_digest = _digest_json(
            {
                "sequence": record.sequence,
                "kind": record.kind,
                "payloadDigest": record.payload_digest,
                "payloadRef": record.payload_ref,
                "policySnapshotDigest": record.policy_snapshot_digest,
                "previousRecordDigest": record.previous_record_digest,
                "targetRecordDigest": record.target_record_digest,
                "metadata": dict(record.metadata),
            }
        )
        if record.record_digest != expected_digest:
            reason_codes.append("record_digest_mismatch")
    return LedgerVerificationReport(
        ok=not reason_codes,
        mode=ledger.mode,
        recordCount=len(ledger.records),
        reasonCodes=tuple(dict.fromkeys(reason_codes)),
    )


def _require_digest(value: str, field_name: str) -> str:
    suffix = value.removeprefix(_DIGEST_PREFIX)
    if not value.startswith(_DIGEST_PREFIX) or len(suffix) != 64 or any(
        char not in "0123456789abcdef" for char in suffix
    ):
        raise ValueError(f"{field_name} must be a sha256 digest")
    return value


def _digest_json(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return _DIGEST_PREFIX + hashlib.sha256(payload.encode("utf-8")).hexdigest()
