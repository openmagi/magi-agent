from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from openmagi_core_agent.runtime.activity_boundary import (
    ActivityRequest,
    ActivityStore,
    evaluate_activity_request,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "programmable_determinism"


def test_external_write_without_idempotency_key_fails_closed() -> None:
    request = ActivityRequest(
        activityId="activity-001",
        kind="channel_delivery",
        targetSystemRef="telegram:chat-digest",
        actionDigest="sha256:" + "1" * 64,
        sideEffecting=True,
        idempotencyKey=None,
        approvalReceiptDigest="sha256:" + "2" * 64,
        timeoutMs=5000,
        retryPolicy="none",
        compensationPolicyRef=None,
        reversible=False,
    )

    result = evaluate_activity_request(request, ActivityStore())
    assert result.ok is False
    assert result.status == "blocked"
    assert "idempotency_key_required" in result.reason_codes
    assert result.receipt_digest is None


def test_retry_with_same_idempotency_key_returns_existing_receipt() -> None:
    store = ActivityStore()
    request = ActivityRequest(
        activityId="activity-002",
        kind="file_write",
        targetSystemRef="workspace:sandbox",
        actionDigest="sha256:" + "3" * 64,
        sideEffecting=True,
        idempotencyKey="idem-write-1",
        approvalReceiptDigest="sha256:" + "4" * 64,
        timeoutMs=5000,
        retryPolicy="none",
        compensationPolicyRef="compensate:file-restore",
        reversible=True,
    )

    first = evaluate_activity_request(request, store)
    second = evaluate_activity_request(request, store)

    assert first.ok is True
    assert second.ok is True
    assert second.status == "deduped_existing_success"
    assert second.receipt_digest == first.receipt_digest


def test_non_reversible_side_effect_requires_approval() -> None:
    request = ActivityRequest(
        activityId="activity-003",
        kind="scheduler_mutation",
        targetSystemRef="cron:selected-bot",
        actionDigest="sha256:" + "5" * 64,
        sideEffecting=True,
        idempotencyKey="idem-cron-1",
        approvalReceiptDigest=None,
        timeoutMs=5000,
        retryPolicy="none",
        compensationPolicyRef=None,
        reversible=False,
    )

    result = evaluate_activity_request(request, ActivityStore())
    assert result.ok is False
    assert "non_reversible_action_requires_approval" in result.reason_codes


def test_reversible_side_effect_requires_compensation_policy() -> None:
    request = ActivityRequest(
        activityId="activity-004",
        kind="file_write",
        targetSystemRef="workspace:sandbox",
        actionDigest="sha256:" + "6" * 64,
        sideEffecting=True,
        idempotencyKey="idem-write-2",
        approvalReceiptDigest="sha256:" + "7" * 64,
        timeoutMs=5000,
        retryPolicy="none",
        compensationPolicyRef=None,
        reversible=True,
    )

    result = evaluate_activity_request(request, ActivityStore())
    assert result.ok is False
    assert result.reason_codes == ("compensation_policy_required_for_reversible_action",)


def test_idempotency_key_conflict_blocks_without_reusing_receipt() -> None:
    store = ActivityStore()
    first = ActivityRequest(
        activityId="activity-005",
        kind="file_write",
        targetSystemRef="workspace:sandbox",
        actionDigest="sha256:" + "8" * 64,
        sideEffecting=True,
        idempotencyKey="idem-write-conflict",
        approvalReceiptDigest="sha256:" + "9" * 64,
        timeoutMs=5000,
        retryPolicy="none",
        compensationPolicyRef="compensate:file-restore",
        reversible=True,
    )
    conflicting = ActivityRequest(
        activityId="activity-006",
        kind="file_write",
        targetSystemRef="workspace:sandbox",
        actionDigest="sha256:" + "a" * 64,
        sideEffecting=True,
        idempotencyKey="idem-write-conflict",
        approvalReceiptDigest="sha256:" + "9" * 64,
        timeoutMs=5000,
        retryPolicy="none",
        compensationPolicyRef="compensate:file-restore",
        reversible=True,
    )

    first_result = evaluate_activity_request(first, store)
    conflict = evaluate_activity_request(conflicting, store)

    assert first_result.ok is True
    assert conflict.ok is False
    assert conflict.status == "blocked"
    assert conflict.reason_codes == ("idempotency_key_conflict",)
    assert conflict.receipt_digest is None


def test_activity_contract_rejects_bad_digests_coerced_booleans_and_raw_refs() -> None:
    with pytest.raises(ValidationError, match="sha256"):
        ActivityRequest(
            activityId="activity-bad-digest",
            kind="file_read",
            targetSystemRef="workspace:sandbox",
            actionDigest="raw-action",
            sideEffecting=False,
            idempotencyKey=None,
            approvalReceiptDigest=None,
            timeoutMs=5000,
            retryPolicy="none",
            compensationPolicyRef=None,
            reversible=False,
        )
    with pytest.raises(ValidationError, match="sideEffecting"):
        ActivityRequest(
            activityId="activity-bad-bool",
            kind="file_read",
            targetSystemRef="workspace:sandbox",
            actionDigest="sha256:" + "b" * 64,
            sideEffecting="false",
            idempotencyKey=None,
            approvalReceiptDigest=None,
            timeoutMs=5000,
            retryPolicy="none",
            compensationPolicyRef=None,
            reversible=False,
        )
    with pytest.raises(ValidationError, match="targetSystemRef"):
        ActivityRequest(
            activityId="activity-raw-target",
            kind="file_read",
            targetSystemRef="/Users/example/.env",
            actionDigest="sha256:" + "c" * 64,
            sideEffecting=False,
            idempotencyKey=None,
            approvalReceiptDigest=None,
            timeoutMs=5000,
            retryPolicy="none",
            compensationPolicyRef=None,
            reversible=False,
        )


def test_activity_contract_rejects_obfuscated_sensitive_markers_and_pathlike_refs() -> None:
    for target_ref in (
        "provider:to-ken-digest",
        "provider:coo.kie-digest",
        "provider:sess-ion-digest",
        "provider:priv-ate-digest",
        "provider:pro.mpt-digest",
        "provider:author-ization-digest",
        "C:Users:kevin:.env",
        "Users.kevin.ssh.id_rsa",
    ):
        with pytest.raises(ValidationError, match="targetSystemRef"):
            ActivityRequest(
                activityId="activity-protected-ref",
                kind="file_read",
                targetSystemRef=target_ref,
                actionDigest="sha256:" + "d" * 64,
                sideEffecting=False,
                idempotencyKey=None,
                approvalReceiptDigest=None,
                timeoutMs=5000,
                retryPolicy="none",
                compensationPolicyRef=None,
                reversible=False,
            )


def test_receipts_are_digest_only_and_model_copy_update_is_disabled() -> None:
    request = ActivityRequest(
        activityId="activity-007",
        kind="external_api_call",
        targetSystemRef="provider:public-digest",
        actionDigest="sha256:" + "d" * 64,
        sideEffecting=True,
        idempotencyKey="idem-api-1",
        approvalReceiptDigest="sha256:" + "e" * 64,
        timeoutMs=5000,
        retryPolicy="none",
        compensationPolicyRef="compensate:provider-cancel",
        reversible=True,
    )

    result = evaluate_activity_request(request, ActivityStore())

    assert result.receipt_digest is not None
    encoded = json.dumps(result.model_dump(by_alias=True, mode="json"), sort_keys=True).lower()
    assert "provider:public-digest" not in encoded
    assert "idem-api-1" not in encoded
    with pytest.raises(ValueError, match="model_copy update"):
        result.model_copy(update={"ok": False})


def test_activity_fixture_validates_without_raw_payloads() -> None:
    payload = json.loads((FIXTURE_DIR / "activity_idempotency.json").read_text())
    store = ActivityStore()
    request = ActivityRequest.model_validate(payload["request"])
    first = evaluate_activity_request(request, store)
    second = evaluate_activity_request(request, store)

    assert first.status == "accepted"
    assert second.status == "deduped_existing_success"
    assert second.receipt_digest == first.receipt_digest == payload["expectedReceiptDigest"]
    encoded_values = " ".join(_string_values(payload)).lower()
    forbidden_fragments = (
        "pro" + "mpt",
        "author" + "ization",
        "coo" + "kie",
        "to" + "ken",
        "sess" + "ion",
        "priv" + "ate",
        "/users/",
        ".env",
    )
    assert all(fragment not in encoded_values for fragment in forbidden_fragments)


def _string_values(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        values: list[str] = []
        for item in value.values():
            values.extend(_string_values(item))
        return values
    if isinstance(value, list):
        values = []
        for item in value:
            values.extend(_string_values(item))
        return values
    return []
