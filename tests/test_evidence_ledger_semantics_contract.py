from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from magi_agent.evidence.ledger_semantics import (
    AuditLedgerMode,
    ContentAddressedLedger,
    ContentAddressedLedgerRecord,
    LedgerRecordKind,
    append_ledger_record,
    verify_ledger_chain,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "deterministic_runtime"


def _digest(char: str) -> str:
    return "sha256:" + char * 64


def _base_ledger() -> ContentAddressedLedger:
    return ContentAddressedLedger(
        ledgerId="ledger-test-001",
        sessionId="session-test",
        turnId="turn-test",
        mode="live",
        records=(),
        appendOnly=True,
        contentAddressed=True,
    )


def test_append_only_records_are_content_addressed_and_chained() -> None:
    ledger = _base_ledger()

    ledger = append_ledger_record(
        ledger,
        kind="tool_receipt",
        payloadDigest=_digest("1"),
        payloadRef="artifact:tool-output-1",
        policySnapshotDigest=_digest("2"),
    )
    ledger = append_ledger_record(
        ledger,
        kind="validator_verdict",
        payloadDigest=_digest("3"),
        payloadRef="verdict:quote-match-1",
        policySnapshotDigest=_digest("2"),
    )

    first, second = ledger.records
    assert first.sequence == 1
    assert first.previous_record_digest is None
    assert first.record_digest.startswith("sha256:")
    assert second.sequence == 2
    assert second.previous_record_digest == first.record_digest
    assert second.record_digest != first.record_digest

    report = verify_ledger_chain(ledger)
    assert report.ok is True
    assert report.record_count == 2
    assert report.mode == "live"


def test_reordered_or_tampered_records_fail_replay_verification() -> None:
    ledger = append_ledger_record(
        _base_ledger(),
        kind="tool_receipt",
        payloadDigest=_digest("1"),
        payloadRef="artifact:tool-output-1",
        policySnapshotDigest=_digest("2"),
    )
    ledger = append_ledger_record(
        ledger,
        kind="validator_verdict",
        payloadDigest=_digest("3"),
        payloadRef="verdict:quote-match-1",
        policySnapshotDigest=_digest("2"),
    )

    tampered = ledger.model_copy(
        update={
            "records": (
                ledger.records[0].model_copy(update={"payload_ref": "artifact:changed"}),
                ledger.records[1],
            )
        }
    )

    report = verify_ledger_chain(tampered)
    assert report.ok is False
    assert "record_digest_mismatch" in report.reason_codes


def test_cached_and_replay_modes_preserve_original_record_digest() -> None:
    live = append_ledger_record(
        _base_ledger(),
        kind="source_snapshot",
        payloadDigest=_digest("4"),
        payloadRef="source:snapshot-1",
        policySnapshotDigest=_digest("5"),
    )
    original_digest = live.records[0].record_digest

    cached = ContentAddressedLedger(
        ledgerId="ledger-cache-001",
        sessionId="session-test",
        turnId="turn-test",
        mode="cached",
        records=live.records,
        appendOnly=True,
        contentAddressed=True,
        sourceLedgerDigest=live.ledger_digest,
    )
    replay = ContentAddressedLedger(
        ledgerId="ledger-replay-001",
        sessionId="session-test",
        turnId="turn-test",
        mode="replay",
        records=live.records,
        appendOnly=True,
        contentAddressed=True,
        sourceLedgerDigest=live.ledger_digest,
    )

    assert cached.records[0].record_digest == original_digest
    assert replay.records[0].record_digest == original_digest
    assert verify_ledger_chain(cached).ok is True
    assert verify_ledger_chain(replay).ok is True


def test_cached_and_replay_modes_cannot_append_new_live_evidence() -> None:
    live = append_ledger_record(
        _base_ledger(),
        kind="source_snapshot",
        payloadDigest=_digest("4"),
        payloadRef="source:snapshot-1",
        policySnapshotDigest=_digest("5"),
    )
    cached = ContentAddressedLedger(
        ledgerId="ledger-cache-002",
        sessionId="session-test",
        turnId="turn-test",
        mode="cached",
        records=live.records,
        appendOnly=True,
        contentAddressed=True,
        sourceLedgerDigest=live.ledger_digest,
    )
    replay = ContentAddressedLedger(
        ledgerId="ledger-replay-002",
        sessionId="session-test",
        turnId="turn-test",
        mode="replay",
        records=live.records,
        appendOnly=True,
        contentAddressed=True,
        sourceLedgerDigest=live.ledger_digest,
    )

    with pytest.raises(ValueError, match="cached ledgers may only append cache_observation"):
        append_ledger_record(
            cached,
            kind="tool_receipt",
            payloadDigest=_digest("6"),
            payloadRef="artifact:tool-output-2",
            policySnapshotDigest=_digest("5"),
        )
    with pytest.raises(ValueError, match="replay ledgers may only append replay_observation"):
        append_ledger_record(
            replay,
            kind="validator_verdict",
            payloadDigest=_digest("7"),
            payloadRef="verdict:quote-match-2",
            policySnapshotDigest=_digest("5"),
        )


def test_deletion_is_tombstone_not_record_removal() -> None:
    ledger = append_ledger_record(
        _base_ledger(),
        kind="tool_receipt",
        payloadDigest=_digest("6"),
        payloadRef="artifact:tool-output-2",
        policySnapshotDigest=_digest("7"),
    )
    target_digest = ledger.records[0].record_digest

    ledger = append_ledger_record(
        ledger,
        kind="tombstone",
        payloadDigest=_digest("8"),
        payloadRef="tombstone:privacy-redaction-1",
        policySnapshotDigest=_digest("7"),
        targetRecordDigest=target_digest,
    )

    assert len(ledger.records) == 2
    assert ledger.records[0].record_digest == target_digest
    assert ledger.records[1].kind == "tombstone"
    assert ledger.records[1].target_record_digest == target_digest
    assert verify_ledger_chain(ledger).ok is True


def test_record_rejects_raw_payload_and_requires_digests() -> None:
    with pytest.raises(ValidationError, match="payload_digest"):
        ContentAddressedLedgerRecord(
            sequence=1,
            kind="tool_receipt",
            payloadDigest="raw content is not allowed",
            payloadRef="artifact:tool-output-1",
            policySnapshotDigest=_digest("2"),
            previousRecordDigest=None,
            recordDigest=_digest("9"),
        )


def test_record_kind_values_are_closed() -> None:
    assert set(LedgerRecordKind.__args__) == {
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
    }
    assert set(AuditLedgerMode.__args__) == {"live", "cached", "replay"}


def test_golden_fixture_contains_valid_digest_only_ledger_chain() -> None:
    fixture = json.loads((FIXTURE_DIR / "evidence_ledger_chain.json").read_text())
    ledger = ContentAddressedLedger.model_validate(fixture)

    assert verify_ledger_chain(ledger).ok is True
    encoded = json.dumps(fixture, sort_keys=True)
    assert "raw" not in encoded.lower()
    assert "authorization" not in encoded.lower()
    assert "cookie" not in encoded.lower()
