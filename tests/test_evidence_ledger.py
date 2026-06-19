from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from magi_agent.evidence.ledger import EvidenceLedger, EvidenceLedgerEntry
from magi_agent.evidence.types import (
    EvidenceContractFailure,
    EvidenceContractVerdict,
    EvidenceRecord,
)


def _record(
    *,
    tool_call_id: str = "call_1",
    command: str = "pytest tests/test_evidence_ledger.py",
    evidence_type: str = "TestRun",
) -> EvidenceRecord:
    return EvidenceRecord.model_validate(
        {
            "type": evidence_type,
            "status": "ok",
            "observedAt": 1_779_999_999,
            "source": {
                "kind": "tool_trace",
                "toolName": "Bash",
                "toolCallId": tool_call_id,
                "metadata": {"producerSurface": "tool_host"},
            },
            "fields": {"command": command, "exitCode": 0},
            "preview": "pytest passed",
            "metadata": {"publicSafeFields": ["command", "exitCode"]},
        }
    )


def _verdict(record: EvidenceRecord) -> EvidenceContractVerdict:
    return EvidenceContractVerdict.model_validate(
        {
            "contractId": "coding-basic",
            "ok": True,
            "state": "pass",
            "enforcement": "audit",
            "missingRequirements": [],
            "matchedEvidence": [record],
            "failures": [],
        }
    )


def _missing_verdict() -> EvidenceContractVerdict:
    return EvidenceContractVerdict.model_validate(
        {
            "contractId": "coding-basic",
            "ok": False,
            "state": "audit",
            "enforcement": "audit",
            "missingRequirements": [{"type": "TestRun"}],
            "matchedEvidence": [],
            "failures": [
                EvidenceContractFailure(
                    code="EVIDENCE_CONTRACT_MISSING",
                    contractId="coding-basic",
                    requirementType="TestRun",
                    message="TestRun evidence missing.",
                )
            ],
        }
    )


def _ledger(**overrides: object) -> EvidenceLedger:
    payload = {
        "ledgerId": "ledger-session-1-turn-1",
        "sessionId": "session-1",
        "turnId": "turn-1",
        "runOn": "main",
        "agentRole": "coding",
        "spawnDepth": 0,
        "sourceKind": "tool_trace",
        "producerSurface": "tool_host",
    }
    payload.update(overrides)
    return EvidenceLedger.model_validate(payload)


def _verifier_verdict_entry_payload(
    *, payload: dict[str, object] | None = None
) -> dict[str, object]:
    verdict_payload: dict[str, object] = {
        "verdictId": "verdict-1",
        "contractId": "coding-basic",
        "state": "pass",
        "ok": True,
        "enforcement": "audit",
        "matchedEvidenceRefs": ["ledger-session-1-turn-1:0001:evidence_record"],
    }
    if payload is not None:
        verdict_payload = payload
    return {
        "kind": "verifier_verdict",
        "sequence": 1,
        "evidenceRef": "ledger-session-1-turn-1:0001:verifier_verdict",
        "sessionId": "session-1",
        "turnId": "turn-1",
        "runOn": "main",
        "agentRole": "coding",
        "spawnDepth": 0,
        "sourceKind": "verifier",
        "producerSurface": "verifier",
        "payload": verdict_payload,
    }


def test_appending_evidence_records_is_append_only_with_deterministic_sequence_order() -> None:
    ledger = _ledger()
    first_record = _record(tool_call_id="call_1")
    second_record = _record(tool_call_id="call_2", command="npm test")

    first = ledger.append_evidence_record(first_record)
    second = first.append_evidence_record(second_record)

    assert ledger.entries == ()
    assert [entry.sequence for entry in second.entries] == [1, 2]
    assert [entry.evidence_ref for entry in second.entries] == [
        "ledger-session-1-turn-1:0001:evidence_record",
        "ledger-session-1-turn-1:0002:evidence_record",
    ]
    assert [entry.kind for entry in second.entries] == ["evidence_record", "evidence_record"]
    assert second.entries[0].payload["record"]["source"]["toolCallId"] == "call_1"
    assert second.entries[1].payload["record"]["source"]["toolCallId"] == "call_2"


def test_previous_ledger_and_entry_cannot_be_mutated_after_append() -> None:
    ledger = _ledger()
    first = ledger.append_evidence_record(_record())
    second = first.append_transcript_ref("transcript-entry-1", metadata={"channel": "sse"})

    assert len(first.entries) == 1
    assert len(second.entries) == 2

    with pytest.raises(ValidationError):
        first.entries[0].sequence = 99  # type: ignore[misc]
    with pytest.raises(TypeError):
        first.entries[0].payload["record"] = {}  # type: ignore[index]
    with pytest.raises(TypeError):
        first.entries[0].metadata["tamper"] = True  # type: ignore[index]


def test_verifier_verdict_append_is_separate_and_does_not_rewrite_raw_evidence() -> None:
    raw_record = _record()
    with_record = _ledger().append_evidence_record(raw_record)
    raw_entry_before = with_record.entries[0].model_dump(by_alias=True)

    with_verdict = with_record.append_verifier_verdict(
        _verdict(raw_record),
        matched_evidence_refs=(with_record.entries[0].evidence_ref,),
        verdict_id="verdict-1",
    )

    assert with_verdict.entries[0].model_dump(by_alias=True) == raw_entry_before
    verdict_entry = with_verdict.entries[1]
    assert verdict_entry.kind == "verifier_verdict"
    assert verdict_entry.payload["verdictId"] == "verdict-1"
    assert verdict_entry.payload["state"] == "pass"
    assert verdict_entry.payload["matchedEvidenceRefs"] == (
        "ledger-session-1-turn-1:0001:evidence_record",
    )


def test_verifier_verdict_payload_preserves_refs_without_matched_evidence_preview_leaks() -> None:
    raw_preview = (
        "api_key=sk-secret-value, "
        "Authorization: Bearer live-token, "
        "done"
    )
    raw_record = _record(tool_call_id="call_secret").model_copy(
        update={"preview": raw_preview}
    )
    verdict = _verdict(raw_record)
    verdict_before = verdict.model_dump(by_alias=True)
    with_record = _ledger().append_evidence_record(raw_record)

    with_verdict = with_record.append_verifier_verdict(
        verdict,
        matched_evidence_refs=(with_record.entries[0].evidence_ref,),
        verdict_id="verdict-1",
    )

    dumped = with_verdict.model_dump(by_alias=True)
    dumped_json = json.dumps(dumped, sort_keys=True)
    verdict_entry = with_verdict.entries[1]

    assert verdict.model_dump(by_alias=True) == verdict_before
    assert verdict.matched_evidence[0].preview == raw_preview
    assert verdict_entry.payload["matchedEvidenceRefs"] == (
        "ledger-session-1-turn-1:0001:evidence_record",
    )
    assert "verdict" not in verdict_entry.payload
    assert verdict_entry.payload["missingRequirements"] == ()
    assert verdict_entry.payload["failures"] == ()
    assert "sk-secret-value" not in dumped_json
    assert "Bearer live-token" not in dumped_json


def test_verifier_verdict_failure_metadata_is_sanitized_without_mutating_verdict() -> None:
    raw_actual = "api_key=sk-secret-value Authorization: Bearer live-token"
    raw_message = "actual contained Authorization: Bearer live-token"
    record = _record()
    verdict = EvidenceContractVerdict.model_validate(
        {
            "contractId": "coding-basic",
            "ok": False,
            "state": "failed",
            "enforcement": "audit",
            "missingRequirements": [],
            "matchedEvidence": [record],
            "failures": [
                EvidenceContractFailure(
                    code="EVIDENCE_CONTRACT_FIELD_MISMATCH",
                    contractId="coding-basic",
                    requirementType="TestRun",
                    message=raw_message,
                    metadata={
                        "field": "command",
                        "actual": raw_actual,
                        "nested": {"authorization": raw_actual},
                    },
                )
            ],
        }
    )
    verdict_before = verdict.model_dump(by_alias=True)
    with_record = _ledger().append_evidence_record(record)

    with_verdict = with_record.append_verifier_verdict(
        verdict,
        matched_evidence_refs=(with_record.entries[0].evidence_ref,),
        verdict_id="verdict-1",
    )

    dumped_json = json.dumps(with_verdict.model_dump(by_alias=True), sort_keys=True)
    failure_payload = with_verdict.entries[1].payload["failures"][0]

    assert verdict.model_dump(by_alias=True) == verdict_before
    assert verdict.failures[0].metadata["actual"] == raw_actual
    assert "sk-secret-value" not in dumped_json
    assert "Bearer live-token" not in dumped_json
    assert failure_payload["metadata"]["actual"] == "api_key=[redacted] Authorization: Bearer [redacted]"
    assert failure_payload["metadata"]["nested"]["authorization"] == "[redacted]"
    assert failure_payload["message"] == "actual contained Authorization: Bearer [redacted]"


def test_verifier_verdict_redacts_header_style_credentials_on_append() -> None:
    raw_retry_message = (
        "retry with Authorization: Basic dXNlcjpwYXNz and "
        "Proxy-Authorization: Basic cHJveHk6cGFzcw== and "
        "Cookie: sessionid=opaque-cookie and "
        "Set-Cookie: sessionid=opaque-set-cookie and "
        "credential=opaque-credential"
    )
    raw_failure_message = "actual contained Authorization: Basic dXNlcjpwYXNz"
    raw_metadata = (
        "Proxy-Authorization: Basic cHJveHk6cGFzcw== "
        "Cookie: sessionid=opaque-cookie "
        "credentials: opaque-credentials"
    )
    record = _record()
    verdict = EvidenceContractVerdict.model_validate(
        {
            "contractId": "coding-basic",
            "ok": False,
            "state": "failed",
            "enforcement": "audit",
            "missingRequirements": [],
            "matchedEvidence": [record],
            "retryMessage": raw_retry_message,
            "failures": [
                EvidenceContractFailure(
                    code="EVIDENCE_CONTRACT_FIELD_MISMATCH",
                    contractId="coding-basic",
                    requirementType="TestRun",
                    message=raw_failure_message,
                    metadata={
                        "actual": raw_metadata,
                        "nested": {"details": raw_metadata},
                    },
                )
            ],
        }
    )
    with_record = _ledger().append_evidence_record(record)

    with_verdict = with_record.append_verifier_verdict(
        verdict,
        matched_evidence_refs=(with_record.entries[0].evidence_ref,),
        verdict_id="verdict-1",
    )

    dumped_json = json.dumps(with_verdict.model_dump(by_alias=True), sort_keys=True)
    payload = with_verdict.entries[1].payload
    failure_payload = payload["failures"][0]

    for secret in (
        "dXNlcjpwYXNz",
        "cHJveHk6cGFzcw==",
        "sessionid=opaque-cookie",
        "sessionid=opaque-set-cookie",
        "opaque-credential",
        "opaque-credentials",
    ):
        assert secret not in dumped_json
    assert payload["retryMessage"] == (
        "retry with Authorization: Basic [redacted] and "
        "Proxy-Authorization: Basic [redacted] and "
        "Cookie: [redacted] "
        "Set-Cookie: [redacted] and "
        "credential=[redacted]"
    )
    assert failure_payload["message"] == (
        "actual contained Authorization: Basic [redacted]"
    )
    assert failure_payload["metadata"]["actual"] == (
        "Proxy-Authorization: Basic [redacted] "
        "Cookie: [redacted] "
        "credentials: [redacted]"
    )
    assert failure_payload["metadata"]["nested"]["details"] == (
        "Proxy-Authorization: Basic [redacted] "
        "Cookie: [redacted] "
        "credentials: [redacted]"
    )


def test_verifier_verdict_redacts_free_text_authorization_and_cookie_on_append() -> None:
    raw_actual = (
        "authorization=Basic dXNlcjpwYXNz "
        "cookie=sessionid=opaque-cookie"
    )
    record = _record()
    verdict = EvidenceContractVerdict.model_validate(
        {
            "contractId": "coding-basic",
            "ok": False,
            "state": "failed",
            "enforcement": "audit",
            "missingRequirements": [],
            "matchedEvidence": [record],
            "failures": [
                EvidenceContractFailure(
                    code="EVIDENCE_CONTRACT_FIELD_MISMATCH",
                    contractId="coding-basic",
                    requirementType="TestRun",
                    message=f"actual contained {raw_actual}",
                    metadata={
                        "actual": raw_actual,
                        "nested": {"details": raw_actual},
                    },
                )
            ],
        }
    )
    with_record = _ledger().append_evidence_record(record)

    with_verdict = with_record.append_verifier_verdict(
        verdict,
        matched_evidence_refs=(with_record.entries[0].evidence_ref,),
        verdict_id="verdict-1",
    )

    dumped_json = json.dumps(with_verdict.model_dump(by_alias=True), sort_keys=True)
    failure_payload = with_verdict.entries[1].payload["failures"][0]

    assert "dXNlcjpwYXNz" not in dumped_json
    assert "sessionid=opaque-cookie" not in dumped_json
    assert failure_payload["message"] == (
        "actual contained authorization=[redacted] cookie=[redacted]"
    )
    assert failure_payload["metadata"]["actual"] == (
        "authorization=[redacted] cookie=[redacted]"
    )
    assert failure_payload["metadata"]["nested"]["details"] == (
        "authorization=[redacted] cookie=[redacted]"
    )


@pytest.mark.parametrize(
    ("secret_key", "secret_value"),
    (
        ("authorization", "Basic dXNlcjpwYXNz"),
        ("cookie", "sessionid=opaque-cookie"),
    ),
)
def test_verifier_verdict_failure_metadata_redacts_bare_credential_key_values_on_append(
    secret_key: str,
    secret_value: str,
) -> None:
    record = _record()
    verdict = EvidenceContractVerdict.model_validate(
        {
            "contractId": "coding-basic",
            "ok": False,
            "state": "failed",
            "enforcement": "audit",
            "missingRequirements": [],
            "matchedEvidence": [record],
            "failures": [
                EvidenceContractFailure(
                    code="EVIDENCE_CONTRACT_FIELD_MISMATCH",
                    contractId="coding-basic",
                    requirementType="TestRun",
                    message="actual contained a credential",
                    metadata={
                        secret_key: secret_value,
                        "nested": {secret_key: secret_value},
                    },
                )
            ],
        }
    )
    with_record = _ledger().append_evidence_record(record)

    with_verdict = with_record.append_verifier_verdict(
        verdict,
        matched_evidence_refs=(with_record.entries[0].evidence_ref,),
        verdict_id="verdict-1",
    )

    dumped_json = json.dumps(with_verdict.model_dump(by_alias=True), sort_keys=True)
    failure_payload = with_verdict.entries[1].payload["failures"][0]

    assert failure_payload["metadata"][secret_key] == "[redacted]"
    assert failure_payload["metadata"]["nested"][secret_key] == "[redacted]"
    assert secret_value not in dumped_json


def test_replayed_verifier_verdict_payload_is_sanitized_during_entry_validation() -> None:
    entry = EvidenceLedgerEntry.model_validate(
        {
            "kind": "verifier_verdict",
            "sequence": 1,
            "evidenceRef": "ledger-session-1-turn-1:0001:verifier_verdict",
            "sessionId": "session-1",
            "turnId": "turn-1",
            "runOn": "main",
            "agentRole": "coding",
            "spawnDepth": 0,
            "sourceKind": "verifier",
            "producerSurface": "verifier",
            "payload": {
                "verdictId": "verdict-1",
                "contractId": "coding-basic",
                "state": "failed",
                "ok": False,
                "enforcement": "audit",
                "missingRequirements": [],
                "failures": [
                    {
                        "code": "EVIDENCE_CONTRACT_FIELD_MISMATCH",
                        "contractId": "coding-basic",
                        "requirementType": "TestRun",
                        "message": "actual contained Authorization: Bearer live-token",
                        "metadata": {
                            "actual": "api_key=sk-secret-value",
                            "nested": {
                                "authorization": "Authorization: Bearer live-token",
                            },
                        },
                    },
                ],
                "retryMessage": "retry with api_key=sk-secret-value and Authorization: Bearer live-token",
                "matchedEvidenceRefs": ["ledger-session-1-turn-1:0001:evidence_record"],
            },
        }
    )

    dumped_json = json.dumps(entry.model_dump(by_alias=True), sort_keys=True)
    payload = entry.payload
    failure_payload = payload["failures"][0]

    assert "sk-secret-value" not in dumped_json
    assert "Bearer live-token" not in dumped_json
    assert payload["retryMessage"] == (
        "retry with api_key=[redacted] Authorization: Bearer [redacted]"
    )
    assert failure_payload["message"] == "actual contained Authorization: Bearer [redacted]"
    assert failure_payload["metadata"]["actual"] == "api_key=[redacted]"
    assert failure_payload["metadata"]["nested"]["authorization"] == "[redacted]"


def test_replayed_verifier_verdict_redacts_header_style_credentials() -> None:
    entry = EvidenceLedgerEntry.model_validate(
        {
            "kind": "verifier_verdict",
            "sequence": 1,
            "evidenceRef": "ledger-session-1-turn-1:0001:verifier_verdict",
            "sessionId": "session-1",
            "turnId": "turn-1",
            "runOn": "main",
            "agentRole": "coding",
            "spawnDepth": 0,
            "sourceKind": "verifier",
            "producerSurface": "verifier",
            "payload": {
                "verdictId": "verdict-1",
                "contractId": "coding-basic",
                "state": "failed",
                "ok": False,
                "enforcement": "audit",
                "missingRequirements": [],
                "failures": [
                    {
                        "code": "EVIDENCE_CONTRACT_FIELD_MISMATCH",
                        "contractId": "coding-basic",
                        "requirementType": "TestRun",
                        "message": "actual contained Authorization: Basic dXNlcjpwYXNz",
                        "metadata": {
                            "actual": (
                                "Proxy-Authorization: Basic cHJveHk6cGFzcw== "
                                "Cookie: sessionid=opaque-cookie"
                            ),
                            "nested": {
                                "details": (
                                    "Set-Cookie: sessionid=opaque-set-cookie "
                                    "credential=opaque-credential"
                                ),
                            },
                        },
                    },
                ],
                "retryMessage": (
                    "retry with Authorization: Basic dXNlcjpwYXNz and "
                    "credentials: opaque-credentials"
                ),
                "matchedEvidenceRefs": ["ledger-session-1-turn-1:0001:evidence_record"],
            },
        }
    )

    dumped_json = json.dumps(entry.model_dump(by_alias=True), sort_keys=True)
    payload = entry.payload
    failure_payload = payload["failures"][0]

    for secret in (
        "dXNlcjpwYXNz",
        "cHJveHk6cGFzcw==",
        "sessionid=opaque-cookie",
        "sessionid=opaque-set-cookie",
        "opaque-credential",
        "opaque-credentials",
    ):
        assert secret not in dumped_json
    assert payload["retryMessage"] == (
        "retry with Authorization: Basic [redacted] and credentials: [redacted]"
    )
    assert failure_payload["message"] == (
        "actual contained Authorization: Basic [redacted]"
    )
    assert failure_payload["metadata"]["actual"] == (
        "Proxy-Authorization: Basic [redacted] Cookie: [redacted]"
    )
    assert failure_payload["metadata"]["nested"]["details"] == (
        "Set-Cookie: [redacted] credential=[redacted]"
    )


def test_replayed_verifier_verdict_redacts_free_text_authorization_and_cookie() -> None:
    entry = EvidenceLedgerEntry.model_validate(
        {
            "kind": "verifier_verdict",
            "sequence": 1,
            "evidenceRef": "ledger-session-1-turn-1:0001:verifier_verdict",
            "sessionId": "session-1",
            "turnId": "turn-1",
            "runOn": "main",
            "agentRole": "coding",
            "spawnDepth": 0,
            "sourceKind": "verifier",
            "producerSurface": "verifier",
            "payload": {
                "verdictId": "verdict-1",
                "contractId": "coding-basic",
                "state": "failed",
                "ok": False,
                "enforcement": "audit",
                "missingRequirements": [],
                "failures": [
                    {
                        "code": "EVIDENCE_CONTRACT_FIELD_MISMATCH",
                        "contractId": "coding-basic",
                        "requirementType": "TestRun",
                        "message": (
                            "actual contained authorization=Basic dXNlcjpwYXNz "
                            "cookie=sessionid=opaque-cookie"
                        ),
                        "metadata": {
                            "actual": (
                                "authorization=Basic dXNlcjpwYXNz "
                                "cookie=sessionid=opaque-cookie"
                            ),
                            "nested": {
                                "details": (
                                    "authorization=Basic dXNlcjpwYXNz "
                                    "cookie=sessionid=opaque-cookie"
                                ),
                            },
                        },
                    },
                ],
                "matchedEvidenceRefs": ["ledger-session-1-turn-1:0001:evidence_record"],
            },
        }
    )

    dumped_json = json.dumps(entry.model_dump(by_alias=True), sort_keys=True)
    failure_payload = entry.payload["failures"][0]

    assert "dXNlcjpwYXNz" not in dumped_json
    assert "sessionid=opaque-cookie" not in dumped_json
    assert failure_payload["message"] == (
        "actual contained authorization=[redacted] cookie=[redacted]"
    )
    assert failure_payload["metadata"]["actual"] == (
        "authorization=[redacted] cookie=[redacted]"
    )
    assert failure_payload["metadata"]["nested"]["details"] == (
        "authorization=[redacted] cookie=[redacted]"
    )


@pytest.mark.parametrize(
    ("secret_key", "secret_value"),
    (
        ("authorization", "Basic dXNlcjpwYXNz"),
        ("cookie", "sessionid=opaque-cookie"),
    ),
)
def test_replayed_verifier_verdict_failure_metadata_redacts_bare_credential_key_values(
    secret_key: str,
    secret_value: str,
) -> None:
    entry = EvidenceLedgerEntry.model_validate(
        {
            "kind": "verifier_verdict",
            "sequence": 1,
            "evidenceRef": "ledger-session-1-turn-1:0001:verifier_verdict",
            "sessionId": "session-1",
            "turnId": "turn-1",
            "runOn": "main",
            "agentRole": "coding",
            "spawnDepth": 0,
            "sourceKind": "verifier",
            "producerSurface": "verifier",
            "payload": {
                "verdictId": "verdict-1",
                "contractId": "coding-basic",
                "state": "failed",
                "ok": False,
                "enforcement": "audit",
                "missingRequirements": [],
                "failures": [
                    {
                        "code": "EVIDENCE_CONTRACT_FIELD_MISMATCH",
                        "contractId": "coding-basic",
                        "requirementType": "TestRun",
                        "message": "actual contained a credential",
                        "metadata": {
                            secret_key: secret_value,
                            "nested": {secret_key: secret_value},
                        },
                    },
                ],
                "matchedEvidenceRefs": ["ledger-session-1-turn-1:0001:evidence_record"],
            },
        }
    )

    dumped_json = json.dumps(entry.model_dump(by_alias=True), sort_keys=True)
    failure_payload = entry.payload["failures"][0]

    assert failure_payload["metadata"][secret_key] == "[redacted]"
    assert failure_payload["metadata"]["nested"][secret_key] == "[redacted]"
    assert secret_value not in dumped_json


def test_replayed_verifier_verdict_drops_top_level_matched_evidence_container() -> None:
    entry = EvidenceLedgerEntry.model_validate(
        {
            "kind": "verifier_verdict",
            "sequence": 1,
            "evidenceRef": "ledger-session-1-turn-1:0001:verifier_verdict",
            "sessionId": "session-1",
            "turnId": "turn-1",
            "runOn": "main",
            "agentRole": "coding",
            "spawnDepth": 0,
            "sourceKind": "verifier",
            "producerSurface": "verifier",
            "payload": {
                "verdictId": "verdict-1",
                "contractId": "coding-basic",
                "state": "pass",
                "ok": True,
                "enforcement": "audit",
                "matchedEvidence": [
                    {
                        "type": "TestRun",
                        "preview": "api_key=sk-secret-value Authorization: Bearer live-token",
                    }
                ],
                "matchedEvidenceRefs": ["ledger-session-1-turn-1:0001:evidence_record"],
            },
        }
    )

    dumped_json = json.dumps(entry.model_dump(by_alias=True), sort_keys=True)

    assert "matchedEvidence" not in entry.payload
    assert entry.payload["matchedEvidenceRefs"] == (
        "ledger-session-1-turn-1:0001:evidence_record",
    )
    assert "sk-secret-value" not in dumped_json
    assert "live-token" not in dumped_json


def test_replayed_verifier_verdict_drops_nested_verdict_matched_evidence_container() -> None:
    entry = EvidenceLedgerEntry.model_validate(
        {
            "kind": "verifier_verdict",
            "sequence": 1,
            "evidenceRef": "ledger-session-1-turn-1:0001:verifier_verdict",
            "sessionId": "session-1",
            "turnId": "turn-1",
            "runOn": "main",
            "agentRole": "coding",
            "spawnDepth": 0,
            "sourceKind": "verifier",
            "producerSurface": "verifier",
            "payload": {
                "verdictId": "verdict-1",
                "contractId": "coding-basic",
                "state": "pass",
                "ok": True,
                "enforcement": "audit",
                "verdict": {
                    "contractId": "coding-basic",
                    "matchedEvidence": [
                        {
                            "type": "TestRun",
                            "preview": "api_key=sk-secret-value Authorization: Bearer live-token",
                        }
                    ],
                },
                "matchedEvidenceRefs": ["ledger-session-1-turn-1:0001:evidence_record"],
            },
        }
    )

    dumped_json = json.dumps(entry.model_dump(by_alias=True), sort_keys=True)

    assert "verdict" not in entry.payload
    assert entry.payload["matchedEvidenceRefs"] == (
        "ledger-session-1-turn-1:0001:evidence_record",
    )
    assert "sk-secret-value" not in dumped_json
    assert "live-token" not in dumped_json


def test_replayed_verifier_verdict_drops_arbitrary_nested_matched_evidence_container() -> None:
    entry = EvidenceLedgerEntry.model_validate(
        {
            "kind": "verifier_verdict",
            "sequence": 1,
            "evidenceRef": "ledger-session-1-turn-1:0001:verifier_verdict",
            "sessionId": "session-1",
            "turnId": "turn-1",
            "runOn": "main",
            "agentRole": "coding",
            "spawnDepth": 0,
            "sourceKind": "verifier",
            "producerSurface": "verifier",
            "payload": {
                "verdictId": "verdict-1",
                "contractId": "coding-basic",
                "state": "pass",
                "ok": True,
                "enforcement": "audit",
                "audit": {
                    "matchedEvidence": [
                        {
                            "type": "TestRun",
                            "preview": "api_key=sk-secret-value Authorization: Bearer live-token",
                        }
                    ],
                },
                "debug": "api_key=sk-secret-value",
                "matchedEvidenceRefs": ["ledger-session-1-turn-1:0001:evidence_record"],
            },
        }
    )

    dumped_json = json.dumps(entry.model_dump(by_alias=True), sort_keys=True)

    assert "audit" not in entry.payload
    assert "debug" not in entry.payload
    assert entry.payload["matchedEvidenceRefs"] == (
        "ledger-session-1-turn-1:0001:evidence_record",
    )
    assert "sk-secret-value" not in dumped_json
    assert "live-token" not in dumped_json


def test_replayed_verifier_verdict_rejects_missing_matched_evidence_refs() -> None:
    payload = {
        "verdictId": "verdict-1",
        "contractId": "coding-basic",
        "state": "pass",
        "ok": True,
        "enforcement": "audit",
    }

    with pytest.raises(ValidationError, match="matchedEvidenceRefs"):
        EvidenceLedgerEntry.model_validate(_verifier_verdict_entry_payload(payload=payload))


def test_replayed_verifier_verdict_rejects_blank_matched_evidence_refs() -> None:
    payload = {
        "verdictId": "verdict-1",
        "contractId": "coding-basic",
        "state": "pass",
        "ok": True,
        "enforcement": "audit",
        "matchedEvidenceRefs": ["  "],
    }

    with pytest.raises(ValidationError, match="matchedEvidenceRefs"):
        EvidenceLedgerEntry.model_validate(_verifier_verdict_entry_payload(payload=payload))


def test_replayed_verifier_verdict_rejects_non_string_matched_evidence_refs() -> None:
    payload = {
        "verdictId": "verdict-1",
        "contractId": "coding-basic",
        "state": "pass",
        "ok": True,
        "enforcement": "audit",
        "matchedEvidenceRefs": [123],
    }

    with pytest.raises(ValidationError, match="matchedEvidenceRefs"):
        EvidenceLedgerEntry.model_validate(_verifier_verdict_entry_payload(payload=payload))


def test_replayed_verifier_verdict_normalizes_matched_evidence_refs_whitespace() -> None:
    entry = EvidenceLedgerEntry.model_validate(
        _verifier_verdict_entry_payload(
            payload={
                "verdictId": "verdict-1",
                "contractId": "coding-basic",
                "state": "pass",
                "ok": True,
                "enforcement": "audit",
                "matchedEvidenceRefs": [
                    "  ledger-session-1-turn-1:0001:evidence_record  ",
                ],
            }
        )
    )

    assert entry.payload["matchedEvidenceRefs"] == (
        "ledger-session-1-turn-1:0001:evidence_record",
    )


def test_replayed_verifier_verdict_rejects_duplicate_matched_evidence_refs() -> None:
    payload = {
        "verdictId": "verdict-1",
        "contractId": "coding-basic",
        "state": "pass",
        "ok": True,
        "enforcement": "audit",
        "matchedEvidenceRefs": [
            "ledger-session-1-turn-1:0001:evidence_record",
            " ledger-session-1-turn-1:0001:evidence_record ",
        ],
    }

    with pytest.raises(ValidationError, match="matchedEvidenceRefs"):
        EvidenceLedgerEntry.model_validate(_verifier_verdict_entry_payload(payload=payload))


def test_replayed_verifier_verdict_rejects_ok_true_with_empty_matched_evidence_refs() -> None:
    payload = {
        "verdictId": "verdict-1",
        "contractId": "coding-basic",
        "state": "audit",
        "ok": True,
        "enforcement": "audit",
        "missingRequirements": [],
        "failures": [],
        "matchedEvidenceRefs": [],
    }

    with pytest.raises(ValidationError, match="matchedEvidenceRefs"):
        EvidenceLedgerEntry.model_validate(_verifier_verdict_entry_payload(payload=payload))


def test_replayed_verifier_verdict_rejects_pass_state_with_empty_matched_evidence_refs() -> None:
    payload = {
        "verdictId": "verdict-1",
        "contractId": "coding-basic",
        "state": "pass",
        "ok": False,
        "enforcement": "audit",
        "missingRequirements": [
            {"type": "TestRun"},
        ],
        "failures": [
            {
                "code": "EVIDENCE_CONTRACT_MISSING",
                "contractId": "coding-basic",
                "requirementType": "TestRun",
                "message": "TestRun evidence missing.",
            },
        ],
        "matchedEvidenceRefs": [],
    }

    with pytest.raises(ValidationError, match="verifier verdict"):
        EvidenceLedgerEntry.model_validate(_verifier_verdict_entry_payload(payload=payload))


def test_replayed_verifier_verdict_rejects_ok_true_with_failures_or_missing_requirements() -> None:
    payload = {
        "verdictId": "verdict-1",
        "contractId": "coding-basic",
        "state": "audit",
        "ok": True,
        "enforcement": "audit",
        "missingRequirements": [
            {"type": "TestRun"},
        ],
        "failures": [
            {
                "code": "EVIDENCE_CONTRACT_MISSING",
                "contractId": "coding-basic",
                "requirementType": "TestRun",
                "message": "TestRun evidence missing.",
            },
        ],
        "matchedEvidenceRefs": [
            "ledger-session-1-turn-1:0001:evidence_record",
        ],
    }

    with pytest.raises(ValidationError, match="ok verifier verdict"):
        EvidenceLedgerEntry.model_validate(_verifier_verdict_entry_payload(payload=payload))


def test_replayed_verifier_verdict_rejects_ok_false_with_pass_state() -> None:
    payload = {
        "verdictId": "verdict-1",
        "contractId": "coding-basic",
        "state": "pass",
        "ok": False,
        "enforcement": "audit",
        "missingRequirements": [],
        "failures": [],
        "matchedEvidenceRefs": [
            "ledger-session-1-turn-1:0001:evidence_record",
        ],
    }

    with pytest.raises(ValidationError, match="verifier verdict"):
        EvidenceLedgerEntry.model_validate(_verifier_verdict_entry_payload(payload=payload))


def test_replayed_verifier_verdict_rejects_empty_matched_refs_without_diagnostics() -> None:
    payload = {
        "verdictId": "verdict-1",
        "contractId": "coding-basic",
        "state": "failed",
        "ok": False,
        "enforcement": "audit",
        "missingRequirements": [],
        "failures": [],
        "matchedEvidenceRefs": [],
    }

    with pytest.raises(ValidationError, match="verifier verdict"):
        EvidenceLedgerEntry.model_validate(_verifier_verdict_entry_payload(payload=payload))


def test_replayed_verifier_verdict_rejects_empty_matched_refs_without_ok_or_diagnostics() -> None:
    payload = {
        "verdictId": "verdict-1",
        "contractId": "coding-basic",
        "state": "failed",
        "enforcement": "audit",
        "missingRequirements": [],
        "failures": [],
        "matchedEvidenceRefs": [],
    }

    with pytest.raises(ValidationError, match="verifier verdict"):
        EvidenceLedgerEntry.model_validate(_verifier_verdict_entry_payload(payload=payload))


def test_replayed_verifier_verdict_rejects_non_boolean_ok() -> None:
    payload = {
        "verdictId": "verdict-1",
        "contractId": "coding-basic",
        "state": "failed",
        "ok": "false",
        "enforcement": "audit",
        "missingRequirements": [{"type": "TestRun"}],
        "failures": [
            {
                "code": "EVIDENCE_CONTRACT_MISSING",
                "contractId": "coding-basic",
                "requirementType": "TestRun",
                "message": "TestRun evidence missing.",
            },
        ],
        "matchedEvidenceRefs": [],
    }

    with pytest.raises(ValidationError, match="ok"):
        EvidenceLedgerEntry.model_validate(_verifier_verdict_entry_payload(payload=payload))


def test_replayed_verifier_verdict_allows_empty_matched_refs_for_missing_audit() -> None:
    entry = EvidenceLedgerEntry.model_validate(
        _verifier_verdict_entry_payload(
            payload={
                "verdictId": "verdict-1",
                "contractId": "coding-basic",
                "state": "audit",
                "ok": False,
                "enforcement": "audit",
                "missingRequirements": [{"type": "TestRun"}],
                "failures": [
                    {
                        "code": "EVIDENCE_CONTRACT_MISSING",
                        "contractId": "coding-basic",
                        "requirementType": "TestRun",
                        "message": "TestRun evidence missing.",
                    }
                ],
                "matchedEvidenceRefs": [],
            }
        )
    )

    assert entry.payload["matchedEvidenceRefs"] == ()


def test_replayed_verifier_verdict_allows_empty_matched_refs_for_invalid_config_failure() -> None:
    entry = EvidenceLedgerEntry.model_validate(
        _verifier_verdict_entry_payload(
            payload={
                "verdictId": "verdict-1",
                "contractId": "coding-basic",
                "state": "audit",
                "ok": False,
                "enforcement": "audit",
                "missingRequirements": [],
                "failures": [
                    {
                        "code": "EVIDENCE_CONTRACT_INVALID_CONFIG",
                        "contractId": "coding-basic",
                        "requirementType": "TestRun",
                        "message": "Evidence contract config is invalid.",
                    }
                ],
                "matchedEvidenceRefs": [],
            }
        )
    )

    assert entry.payload["matchedEvidenceRefs"] == ()


def test_replayed_ledger_rejects_verifier_verdict_nonexistent_matched_evidence_refs() -> None:
    record = _record()
    ledger = _ledger().append_evidence_record(record).append_verifier_verdict(
        _verdict(record),
        matched_evidence_refs=("ledger-session-1-turn-1:0001:evidence_record",),
        verdict_id="verdict-1",
    )
    payload = ledger.model_dump(by_alias=True)
    payload["entries"][1]["payload"]["matchedEvidenceRefs"] = (
        "ledger-session-1-turn-1:0099:evidence_record",
    )

    with pytest.raises(ValidationError, match="matchedEvidenceRefs"):
        EvidenceLedger.model_validate(payload)


def test_replayed_ledger_rejects_verifier_verdict_matched_transcript_ref() -> None:
    ledger = (
        _ledger()
        .append_transcript_ref("transcript-entry-1")
        .append_source_summary("summary-1")
    )
    payload = ledger.model_dump(by_alias=True)
    payload["entries"] = (
        *payload["entries"],
        {
            "kind": "verifier_verdict",
            "sequence": 3,
            "evidenceRef": "ledger-session-1-turn-1:0003:verifier_verdict",
            "sessionId": "session-1",
            "turnId": "turn-1",
            "runOn": "main",
            "agentRole": "coding",
            "spawnDepth": 0,
            "sourceKind": "verifier",
            "producerSurface": "verifier",
            "payload": {
                "verdictId": "verdict-1",
                "contractId": "coding-basic",
                "state": "pass",
                "ok": True,
                "enforcement": "audit",
                "matchedEvidenceRefs": [
                    "ledger-session-1-turn-1:0001:transcript_ref",
                ],
            },
        },
    )

    with pytest.raises(ValidationError, match="matchedEvidenceRefs"):
        EvidenceLedger.model_validate(payload)


@pytest.mark.parametrize(
    "matched_ref",
    (
        "ledger-session-1-turn-1:0002:verifier_verdict",
        "ledger-session-1-turn-1:0003:source_summary",
    ),
)
def test_replayed_ledger_rejects_verifier_verdict_future_or_self_matched_evidence_refs(
    matched_ref: str,
) -> None:
    record = _record()
    ledger = (
        _ledger()
        .append_evidence_record(record)
        .append_verifier_verdict(
            _verdict(record),
            matched_evidence_refs=("ledger-session-1-turn-1:0001:evidence_record",),
            verdict_id="verdict-1",
        )
        .append_source_summary("summary-1")
    )
    payload = ledger.model_dump(by_alias=True)
    payload["entries"][1]["payload"]["matchedEvidenceRefs"] = (matched_ref,)

    with pytest.raises(ValidationError, match="matchedEvidenceRefs"):
        EvidenceLedger.model_validate(payload)


def test_child_source_summary_preserves_scope_distinct_from_parent() -> None:
    parent = _ledger()
    child = _ledger(
        ledgerId="ledger-session-1-turn-1-child-1",
        runOn="child",
        agentRole="research",
        spawnDepth=2,
        sourceKind="custom_extractor",
        producerSurface="harness_engine",
    ).append_source_summary(
        "child-search-summary",
        metadata={"runOn": "child", "agentRole": "research", "spawnDepth": 2},
    )

    assert parent.run_on == "main"
    assert child.run_on == "child"
    assert child.agent_role == "research"
    assert child.spawn_depth == 2
    assert child.entries[0].run_on == "child"
    assert child.entries[0].agent_role == "research"
    assert child.entries[0].spawn_depth == 2
    assert child.entries[0].payload["summaryId"] == "child-search-summary"


def test_task_channel_and_workspace_producer_surfaces_are_validated() -> None:
    for producer_surface in ("task", "channel", "workspace"):
        ledger = _ledger(producerSurface=producer_surface).append_source_summary("summary-1")

        assert ledger.producer_surface == producer_surface
        assert ledger.entries[0].producer_surface == producer_surface


def test_transcript_artifact_and_control_refs_are_metadata_only_and_preserve_refs() -> None:
    ledger = (
        _ledger()
        .append_transcript_ref("transcript-entry-1", metadata={"channel": "sse"})
        .append_artifact_ref("artifact-1", metadata={"artifactServiceRef": "adk-artifact-1"})
        .append_control_ref("approval-1", metadata={"decision": "approved"})
    )

    assert [entry.kind for entry in ledger.entries] == [
        "transcript_ref",
        "artifact_ref",
        "control_ref",
    ]
    assert ledger.entries[0].payload == {"transcriptEntryId": "transcript-entry-1"}
    assert ledger.entries[1].payload == {"artifactId": "artifact-1"}
    assert ledger.entries[2].payload == {"controlId": "approval-1"}
    assert ledger.entries[2].metadata["decision"] == "approved"
    assert all(entry.traffic_attached is False for entry in ledger.entries)
    assert all(entry.execution_attached is False for entry in ledger.entries)
    assert all(entry.route_attached is False for entry in ledger.entries)


def test_compaction_and_replay_metadata_dump_reload_preserves_ordering_and_refs() -> None:
    ledger = (
        _ledger(compactionRef="snapshot-1", replayRef="replay-1")
        .append_evidence_record(_record())
        .append_transcript_ref("transcript-entry-1")
        .append_source_summary("summary-1", metadata={"snapshotRef": "snapshot-1"})
    )

    dumped = ledger.model_dump(by_alias=True)
    reloaded = EvidenceLedger.model_validate(dumped)

    assert reloaded.ledger_id == "ledger-session-1-turn-1"
    assert reloaded.compaction_ref == "snapshot-1"
    assert reloaded.replay_ref == "replay-1"
    assert [entry.sequence for entry in reloaded.entries] == [1, 2, 3]
    assert [entry.evidence_ref for entry in reloaded.entries] == [
        "ledger-session-1-turn-1:0001:evidence_record",
        "ledger-session-1-turn-1:0002:transcript_ref",
        "ledger-session-1-turn-1:0003:source_summary",
    ]


def test_public_source_summary_redacts_and_truncates_secret_metadata() -> None:
    ledger = _ledger().append_source_summary(
        "summary-1",
        metadata={
            "apiToken": "sk-secret-value",
            "preview": "x" * 500,
            "nested": {"clientSecret": "super-secret"},
        },
        public=True,
    )

    public_summary = ledger.entries[0].payload["publicSummary"]

    assert public_summary["apiToken"] == "[redacted]"
    assert public_summary["nested"]["clientSecret"] == "[redacted]"
    assert len(public_summary["preview"]) <= 200
    assert public_summary["preview"].endswith("...")
    assert "sk-secret-value" not in str(public_summary)


def test_public_source_summary_does_not_leak_secret_metadata_in_full_dump() -> None:
    ledger = _ledger().append_source_summary(
        "summary-1",
        metadata={
            "apiToken": "sk-secret-value",
            "preview": "Authorization: Bearer live-token",
            "nested": {"clientSecret": "super-secret"},
        },
        public=True,
    )

    dumped_json = json.dumps(ledger.model_dump(by_alias=True), sort_keys=True)
    entry_dumped_json = json.dumps(ledger.entries[0].model_dump(by_alias=True), sort_keys=True)

    assert "sk-secret-value" not in dumped_json
    assert "live-token" not in dumped_json
    assert "super-secret" not in dumped_json
    assert "sk-secret-value" not in entry_dumped_json
    assert "live-token" not in entry_dumped_json
    assert "super-secret" not in entry_dumped_json


def test_public_source_summary_redacts_header_style_credential_strings_on_append() -> None:
    raw_preview = (
        "Authorization: Basic dXNlcjpwYXNz, "
        "Proxy-Authorization: Basic cHJveHk6cGFzcw==, "
        "Cookie: sessionid=opaque-cookie, "
        "Set-Cookie: sessionid=opaque-set-cookie, "
        "credential=opaque-credential, "
        "credentials: opaque-credentials"
    )

    ledger = _ledger().append_source_summary(
        "summary-1",
        metadata={
            "preview": raw_preview,
            "nested": {"details": raw_preview},
        },
        public=True,
    )

    dumped_json = json.dumps(ledger.model_dump(by_alias=True), sort_keys=True)
    public_summary = ledger.entries[0].payload["publicSummary"]

    for secret in (
        "dXNlcjpwYXNz",
        "cHJveHk6cGFzcw==",
        "sessionid=opaque-cookie",
        "sessionid=opaque-set-cookie",
        "opaque-credential",
        "opaque-credentials",
    ):
        assert secret not in dumped_json
    assert public_summary["preview"] == (
        "Authorization: Basic [redacted], "
        "Proxy-Authorization: Basic [redacted], "
        "Cookie: [redacted], "
        "Set-Cookie: [redacted], "
        "credential=[redacted], "
        "credentials: [redacted]"
    )
    assert public_summary["nested"]["details"] == public_summary["preview"]
    assert ledger.entries[0].metadata["preview"] == public_summary["preview"]
    assert ledger.entries[0].metadata["nested"]["details"] == public_summary["preview"]


def test_public_source_summary_redacts_free_text_authorization_and_cookie_on_append() -> None:
    raw_preview = (
        "authorization=Basic dXNlcjpwYXNz "
        "cookie=sessionid=opaque-cookie"
    )

    ledger = _ledger().append_source_summary(
        "summary-1",
        metadata={
            "preview": raw_preview,
            "nested": {"details": raw_preview},
        },
        public=True,
    )

    dumped_json = json.dumps(ledger.model_dump(by_alias=True), sort_keys=True)
    public_summary = ledger.entries[0].payload["publicSummary"]

    assert "dXNlcjpwYXNz" not in dumped_json
    assert "sessionid=opaque-cookie" not in dumped_json
    assert public_summary["preview"] == (
        "authorization=[redacted] cookie=[redacted]"
    )
    assert public_summary["nested"]["details"] == public_summary["preview"]
    assert ledger.entries[0].metadata["preview"] == public_summary["preview"]
    assert ledger.entries[0].metadata["nested"]["details"] == public_summary["preview"]


@pytest.mark.parametrize(
    ("secret_key", "secret_value"),
    (
        ("authorization", "Basic dXNlcjpwYXNz"),
        ("Authorization", "Basic dXNlcjpwYXNz"),
        ("proxyAuthorization", "Basic cHJveHk6cGFzcw=="),
        ("ProxyAuthorization", "Basic cHJveHk6cGFzcw=="),
        ("proxy_authorization", "Basic cHJveHlfdXNlcjpwYXNz"),
        ("cookie", "sessionid=opaque-cookie"),
        ("Cookie", "sessionid=opaque-cookie"),
        ("setCookie", "sessionid=opaque-set-cookie"),
        ("SetCookie", "sessionid=opaque-set-cookie"),
        ("set_cookie", "sessionid=opaque-set-cookie-snake"),
        ("credential", "opaque-credential"),
        ("Credential", "opaque-credential"),
        ("credentials", "opaque-credentials"),
        ("Credentials", "opaque-credentials"),
    ),
)
def test_public_source_summary_redacts_authorization_header_credentials_on_append(
    secret_key: str,
    secret_value: str,
) -> None:
    ledger = _ledger().append_source_summary(
        "summary-1",
        metadata={
            secret_key: secret_value,
            "nested": {secret_key: secret_value},
        },
        public=True,
    )

    public_summary = ledger.entries[0].payload["publicSummary"]
    dumped_json = json.dumps(ledger.model_dump(by_alias=True), sort_keys=True)

    assert public_summary[secret_key] == "[redacted]"
    assert public_summary["nested"][secret_key] == "[redacted]"
    assert ledger.entries[0].metadata[secret_key] == "[redacted]"
    assert ledger.entries[0].metadata["nested"][secret_key] == "[redacted]"
    assert secret_value not in dumped_json


@pytest.mark.parametrize(
    ("secret_key", "secret_value"),
    (
        ("authorization", "Basic dXNlcjpwYXNz"),
        ("Authorization", "Basic dXNlcjpwYXNz"),
        ("proxyAuthorization", "Basic cHJveHk6cGFzcw=="),
        ("ProxyAuthorization", "Basic cHJveHk6cGFzcw=="),
        ("proxy_authorization", "Basic cHJveHlfdXNlcjpwYXNz"),
        ("cookie", "sessionid=opaque-cookie"),
        ("Cookie", "sessionid=opaque-cookie"),
        ("setCookie", "sessionid=opaque-set-cookie"),
        ("SetCookie", "sessionid=opaque-set-cookie"),
        ("set_cookie", "sessionid=opaque-set-cookie-snake"),
        ("credential", "opaque-credential"),
        ("Credential", "opaque-credential"),
        ("credentials", "opaque-credentials"),
        ("Credentials", "opaque-credentials"),
    ),
)
def test_replayed_public_source_summary_redacts_authorization_header_credentials(
    secret_key: str,
    secret_value: str,
) -> None:
    entry = EvidenceLedgerEntry.model_validate(
        {
            "kind": "source_summary",
            "sequence": 1,
            "evidenceRef": "ledger-session-1-turn-1:0001:source_summary",
            "sessionId": "session-1",
            "turnId": "turn-1",
            "runOn": "main",
            "agentRole": "coding",
            "spawnDepth": 0,
            "sourceKind": "tool_trace",
            "producerSurface": "channel",
            "payload": {
                "summaryId": "summary-1",
                "publicSummary": {
                    secret_key: secret_value,
                    "nested": {secret_key: secret_value},
                },
            },
            "metadata": {
                secret_key: secret_value,
                "nested": {secret_key: secret_value},
            },
        }
    )

    public_summary = entry.payload["publicSummary"]
    dumped_json = json.dumps(entry.model_dump(by_alias=True), sort_keys=True)

    assert public_summary[secret_key] == "[redacted]"
    assert public_summary["nested"][secret_key] == "[redacted]"
    assert entry.metadata[secret_key] == "[redacted]"
    assert entry.metadata["nested"][secret_key] == "[redacted]"
    assert secret_value not in dumped_json


def test_public_source_summary_rejects_non_string_metadata_keys_before_redaction() -> None:
    with pytest.raises(ValueError, match="mapping keys must be strings"):
        _ledger().append_source_summary(
            "summary-1",
            metadata={1: "api_key=sk-secret-value"},
            public=True,
        )

    with pytest.raises(ValueError, match="mapping keys must be strings"):
        _ledger().append_source_summary(
            "summary-1",
            metadata={"nested": {1: "api_key=sk-secret-value"}},
            public=True,
        )


def test_replayed_public_summary_rejects_non_string_nested_keys_during_validation() -> None:
    with pytest.raises(ValidationError, match="mapping keys must be strings"):
        EvidenceLedgerEntry.model_validate(
            {
                "kind": "source_summary",
                "sequence": 1,
                "evidenceRef": "ledger-session-1-turn-1:0001:source_summary",
                "sessionId": "session-1",
                "turnId": "turn-1",
                "runOn": "main",
                "agentRole": "coding",
                "spawnDepth": 0,
                "sourceKind": "tool_trace",
                "producerSurface": "channel",
                "payload": {
                    "summaryId": "summary-1",
                    "publicSummary": {
                        "nested": {1: "api_key=sk-secret-value"},
                    },
                },
            }
        )


def test_replayed_public_summary_is_redacted_and_truncated_during_validation() -> None:
    entry = EvidenceLedgerEntry.model_validate(
        {
            "kind": "source_summary",
            "sequence": 1,
            "evidenceRef": "ledger-session-1-turn-1:0001:source_summary",
            "sessionId": "session-1",
            "turnId": "turn-1",
            "runOn": "main",
            "agentRole": "coding",
            "spawnDepth": 0,
            "sourceKind": "tool_trace",
            "producerSurface": "channel",
            "payload": {
                "summaryId": "summary-1",
                "publicSummary": {
                    "apiToken": "sk-secret-value",
                    "preview": "x" * 500,
                    "nested": {"clientSecret": "super-secret"},
                },
            },
        }
    )

    public_summary = entry.payload["publicSummary"]

    assert public_summary["apiToken"] == "[redacted]"
    assert public_summary["nested"]["clientSecret"] == "[redacted]"
    assert len(public_summary["preview"]) <= 200
    assert public_summary["preview"].endswith("...")
    assert "sk-secret-value" not in str(public_summary)


def test_replayed_public_source_summary_metadata_is_redacted_and_truncated_during_validation() -> None:
    entry = EvidenceLedgerEntry.model_validate(
        {
            "kind": "source_summary",
            "sequence": 1,
            "evidenceRef": "ledger-session-1-turn-1:0001:source_summary",
            "sessionId": "session-1",
            "turnId": "turn-1",
            "runOn": "main",
            "agentRole": "coding",
            "spawnDepth": 0,
            "sourceKind": "tool_trace",
            "producerSurface": "channel",
            "payload": {
                "summaryId": "summary-1",
                "publicSummary": {
                    "preview": "public summary",
                },
            },
            "metadata": {
                "apiToken": "sk-secret-value",
                "preview": "Authorization: Bearer live-token",
                "longText": "x" * 500,
                "nested": {"clientSecret": "super-secret"},
            },
        }
    )

    dumped_json = json.dumps(entry.model_dump(by_alias=True), sort_keys=True)

    assert "sk-secret-value" not in dumped_json
    assert "Bearer live-token" not in dumped_json
    assert "super-secret" not in dumped_json
    assert entry.metadata["apiToken"] == "[redacted]"
    assert entry.metadata["preview"] == "Authorization: Bearer [redacted]"
    assert entry.metadata["nested"]["clientSecret"] == "[redacted]"
    assert isinstance(entry.metadata["longText"], str)
    assert len(entry.metadata["longText"]) <= 200
    assert entry.metadata["longText"].endswith("...")


def test_replayed_scalar_public_summary_is_redacted_then_truncated_during_validation() -> None:
    raw_summary = (
        "api_key=sk-secret-value, "
        "Authorization: Bearer live-token, "
        + ("x" * 500)
    )

    entry = EvidenceLedgerEntry.model_validate(
        {
            "kind": "source_summary",
            "sequence": 1,
            "evidenceRef": "ledger-session-1-turn-1:0001:source_summary",
            "sessionId": "session-1",
            "turnId": "turn-1",
            "runOn": "main",
            "agentRole": "coding",
            "spawnDepth": 0,
            "sourceKind": "tool_trace",
            "producerSurface": "channel",
            "payload": {
                "summaryId": "summary-1",
                "publicSummary": raw_summary,
            },
        }
    )

    public_summary = entry.payload["publicSummary"]

    assert isinstance(public_summary, str)
    assert "Bearer live-token" not in public_summary
    assert "sk-secret-value" not in public_summary
    assert "Bearer [redacted]" in public_summary
    assert len(public_summary) <= 200
    assert public_summary.endswith("...")


def test_replayed_public_summary_redacts_header_style_credential_strings() -> None:
    raw_nested_summary = (
        "Authorization: Basic dXNlcjpwYXNz, "
        "Proxy-Authorization: Basic cHJveHk6cGFzcw==, "
        "Cookie: sessionid=opaque-cookie, "
        "Set-Cookie: sessionid=opaque-set-cookie, "
        "credential=opaque-credential, "
        "credentials: opaque-credentials"
    )
    raw_scalar_summary = (
        "Authorization: Basic dXNlcjpwYXNz and "
        "Cookie: sessionid=opaque-cookie and "
        "credential=opaque-credential"
    )

    nested_entry = EvidenceLedgerEntry.model_validate(
        {
            "kind": "source_summary",
            "sequence": 1,
            "evidenceRef": "ledger-session-1-turn-1:0001:source_summary",
            "sessionId": "session-1",
            "turnId": "turn-1",
            "runOn": "main",
            "agentRole": "coding",
            "spawnDepth": 0,
            "sourceKind": "tool_trace",
            "producerSurface": "channel",
            "payload": {
                "summaryId": "summary-1",
                "publicSummary": {
                    "preview": raw_nested_summary,
                    "nested": {"details": raw_nested_summary},
                },
            },
        }
    )
    scalar_entry = EvidenceLedgerEntry.model_validate(
        {
            "kind": "source_summary",
            "sequence": 1,
            "evidenceRef": "ledger-session-1-turn-1:0001:source_summary",
            "sessionId": "session-1",
            "turnId": "turn-1",
            "runOn": "main",
            "agentRole": "coding",
            "spawnDepth": 0,
            "sourceKind": "tool_trace",
            "producerSurface": "channel",
            "payload": {
                "summaryId": "summary-1",
                "publicSummary": raw_scalar_summary,
            },
        }
    )

    dumped_json = json.dumps(
        {
            "nested": nested_entry.model_dump(by_alias=True),
            "scalar": scalar_entry.model_dump(by_alias=True),
        },
        sort_keys=True,
    )
    nested_summary = nested_entry.payload["publicSummary"]
    scalar_summary = scalar_entry.payload["publicSummary"]

    for secret in (
        "dXNlcjpwYXNz",
        "cHJveHk6cGFzcw==",
        "sessionid=opaque-cookie",
        "sessionid=opaque-set-cookie",
        "opaque-credential",
        "opaque-credentials",
    ):
        assert secret not in dumped_json
    assert nested_summary["preview"] == (
        "Authorization: Basic [redacted], "
        "Proxy-Authorization: Basic [redacted], "
        "Cookie: [redacted], "
        "Set-Cookie: [redacted], "
        "credential=[redacted], "
        "credentials: [redacted]"
    )
    assert nested_summary["nested"]["details"] == nested_summary["preview"]
    assert scalar_summary == (
        "Authorization: Basic [redacted] and "
        "Cookie: [redacted] "
        "credential=[redacted]"
    )


def test_replayed_public_summary_redacts_free_text_authorization_and_cookie() -> None:
    raw_summary = (
        "Authorization=Basic dXNlcjpwYXNz "
        "Cookie=sessionid=opaque-cookie "
        "ProxyAuthorization=Basic cHJveHk6cGFzcw== "
        "ProxyAuthorization: Basic cHJveHk6cGFzcw=="
    )

    entry = EvidenceLedgerEntry.model_validate(
        {
            "kind": "source_summary",
            "sequence": 1,
            "evidenceRef": "ledger-session-1-turn-1:0001:source_summary",
            "sessionId": "session-1",
            "turnId": "turn-1",
            "runOn": "main",
            "agentRole": "coding",
            "spawnDepth": 0,
            "sourceKind": "tool_trace",
            "producerSurface": "channel",
            "payload": {
                "summaryId": "summary-1",
                "publicSummary": {
                    "preview": raw_summary,
                    "nested": {"details": raw_summary},
                },
            },
        }
    )

    dumped_json = json.dumps(entry.model_dump(by_alias=True), sort_keys=True)
    public_summary = entry.payload["publicSummary"]

    assert "dXNlcjpwYXNz" not in dumped_json
    assert "sessionid=opaque-cookie" not in dumped_json
    assert "cHJveHk6cGFzcw==" not in dumped_json
    assert public_summary["preview"] == (
        "Authorization=[redacted] "
        "Cookie=[redacted] "
        "ProxyAuthorization=[redacted] "
        "ProxyAuthorization: [redacted]"
    )
    assert public_summary["nested"]["details"] == public_summary["preview"]


def test_appending_evidence_record_sanitizes_copied_preview_without_mutating_record() -> None:
    raw_preview = (
        "api_key=sk-secret-value, "
        "Authorization: Bearer live-token, "
        + ("x" * 500)
    )
    record = _record(command="pytest", tool_call_id="call_secret").model_copy(
        update={"preview": raw_preview}
    )

    ledger = _ledger().append_evidence_record(record)
    stored_preview = ledger.entries[0].payload["record"]["preview"]

    assert record.preview == raw_preview
    assert isinstance(stored_preview, str)
    assert "Bearer live-token" not in stored_preview
    assert "sk-secret-value" not in stored_preview
    assert "Bearer [redacted]" in stored_preview
    assert len(stored_preview) <= 200
    assert stored_preview.endswith("...")


def test_replayed_evidence_record_preview_is_sanitized_during_entry_validation() -> None:
    raw_preview = (
        "api_key=sk-secret-value, "
        "Authorization: Bearer live-token, "
        + ("x" * 500)
    )
    record_payload = _record(tool_call_id="call_secret").model_dump(by_alias=True)
    record_payload["preview"] = raw_preview

    entry = EvidenceLedgerEntry.model_validate(
        {
            "kind": "evidence_record",
            "sequence": 1,
            "evidenceRef": "ledger-session-1-turn-1:0001:evidence_record",
            "sessionId": "session-1",
            "turnId": "turn-1",
            "runOn": "main",
            "agentRole": "coding",
            "spawnDepth": 0,
            "sourceKind": "tool_trace",
            "producerSurface": "tool_host",
            "payload": {"record": record_payload},
        }
    )

    stored_preview = entry.payload["record"]["preview"]

    assert record_payload["preview"] == raw_preview
    assert isinstance(stored_preview, str)
    assert "Bearer live-token" not in stored_preview
    assert "sk-secret-value" not in stored_preview
    assert "Bearer [redacted]" in stored_preview
    assert len(stored_preview) <= 200
    assert stored_preview.endswith("...")


def test_verifier_verdict_rejects_blank_matched_evidence_refs() -> None:
    record = _record()
    ledger = _ledger().append_evidence_record(record)

    with pytest.raises(ValueError, match="matched_evidence_refs"):
        ledger.append_verifier_verdict(
            _verdict(record),
            matched_evidence_refs=("  ",),
            verdict_id="verdict-1",
        )


def test_verifier_verdict_rejects_nonexistent_matched_evidence_refs() -> None:
    record = _record()
    ledger = _ledger().append_evidence_record(record)

    with pytest.raises(ValueError, match="matched_evidence_refs"):
        ledger.append_verifier_verdict(
            _verdict(record),
            matched_evidence_refs=("ledger-session-1-turn-1:9999:evidence_record",),
            verdict_id="verdict-1",
        )


def test_verifier_verdict_rejects_duplicate_matched_evidence_refs() -> None:
    record = _record()
    ledger = _ledger().append_evidence_record(record)
    evidence_ref = ledger.entries[0].evidence_ref

    with pytest.raises(ValueError, match="matched_evidence_refs"):
        ledger.append_verifier_verdict(
            _verdict(record),
            matched_evidence_refs=(evidence_ref, f" {evidence_ref} "),
            verdict_id="verdict-1",
        )


def test_verifier_verdict_rejects_refs_that_do_not_match_verdict_evidence() -> None:
    gitdiff_record = _record(evidence_type="GitDiff", tool_call_id="call_diff")
    testrun_record = _record(evidence_type="TestRun", tool_call_id="call_tests")
    ledger = _ledger().append_evidence_record(gitdiff_record).append_evidence_record(testrun_record)

    with pytest.raises(ValueError, match="matched_evidence_refs"):
        ledger.append_verifier_verdict(
            _verdict(testrun_record),
            matched_evidence_refs=(ledger.entries[0].evidence_ref,),
            verdict_id="verdict-1",
        )


def test_verifier_verdict_rejects_partial_refs_for_multi_record_verdict() -> None:
    testrun_record = _record(evidence_type="TestRun", tool_call_id="call_tests")
    gitdiff_record = _record(evidence_type="GitDiff", tool_call_id="call_diff")
    ledger = _ledger().append_evidence_record(testrun_record)
    verdict = EvidenceContractVerdict.model_validate(
        {
            "contractId": "coding-basic",
            "ok": True,
            "state": "pass",
            "enforcement": "audit",
            "missingRequirements": [],
            "matchedEvidence": [testrun_record, gitdiff_record],
            "failures": [],
        }
    )

    with pytest.raises(ValueError, match="matched_evidence_refs"):
        ledger.append_verifier_verdict(
            verdict,
            matched_evidence_refs=(ledger.entries[0].evidence_ref,),
            verdict_id="verdict-1",
        )


def test_missing_audit_verifier_verdict_appends_without_matched_evidence_refs() -> None:
    ledger = _ledger()

    with_verdict = ledger.append_verifier_verdict(
        _missing_verdict(),
        matched_evidence_refs=(),
        verdict_id="verdict-1",
    )

    assert with_verdict.entries[0].kind == "verifier_verdict"
    assert with_verdict.entries[0].payload["matchedEvidenceRefs"] == ()
    assert with_verdict.entries[0].payload["missingRequirements"] == (
        {"type": "TestRun", "after": None, "commandPattern": None, "exitCode": None, "fields": {}},
    )


def test_verifier_verdict_rejects_matched_refs_to_non_evidence_record_entries() -> None:
    ledger = _ledger().append_transcript_ref("transcript-entry-1")

    with pytest.raises(ValueError, match="matched_evidence_refs"):
        ledger.append_verifier_verdict(
            _verdict(_record()),
            matched_evidence_refs=(ledger.entries[0].evidence_ref,),
            verdict_id="verdict-1",
        )


def test_attachment_flags_stay_false_and_model_copy_force_falses_true_flags() -> None:
    ledger = _ledger()
    entry = ledger.append_evidence_record(_record()).entries[0]

    assert ledger.traffic_attached is False
    assert ledger.execution_attached is False
    assert ledger.route_attached is False
    assert entry.traffic_attached is False
    assert entry.execution_attached is False
    assert entry.route_attached is False

    # C-4: ``Literal[False]`` attachment flags are owned by the
    # ``FalseOnlyAuthorityModel`` kernel; a caller asserting True via
    # ``model_copy(update=...)`` is force-falsed (strictly stronger than the
    # legacy raise -- the security contract holds on every construction
    # surface, including this escape hatch).
    coerced_ledger = ledger.model_copy(update={"trafficAttached": True})
    assert coerced_ledger.traffic_attached is False
    coerced_entry = entry.model_copy(update={"routeAttached": True})
    assert coerced_entry.route_attached is False


def test_non_json_metadata_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _ledger(metadata={"bad": {object()}})

    with pytest.raises(ValidationError):
        _ledger().append_transcript_ref("transcript-entry-1", metadata={"bad": {1, 2, 3}})
