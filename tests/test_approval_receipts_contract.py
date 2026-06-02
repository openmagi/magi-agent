from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from magi_agent.harness.approval_receipts import (
    ApprovalReceipt,
    ApprovalScope,
    build_approval_receipt,
    verify_approval_receipt_for_action,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "deterministic_runtime"
NOW = datetime(2026, 5, 21, 12, 0, tzinfo=UTC)


def _digest(char: str) -> str:
    return "sha256:" + char * 64


def test_approval_receipt_binds_approver_action_scope_policy_and_expiry() -> None:
    receipt = build_approval_receipt(
        approvalId="approval-001",
        approverRef="user-digest:owner",
        approvalSource="human_operator",
        approvedActionKind="tool_call",
        approvedActionDigest=_digest("1"),
        approvedScope="single_tool_call",
        policyDecisionId="decision-001",
        effectivePolicySnapshotDigest=_digest("2"),
        issuedAt=NOW,
        expiresAt=NOW + timedelta(minutes=5),
        constraints={"tool": "FileRead", "maxBytes": 16384},
    )

    assert receipt.approval_digest.startswith("sha256:")
    assert verify_approval_receipt_for_action(
        receipt,
        actionDigest=_digest("1"),
        requiredScope="single_tool_call",
        now=NOW + timedelta(minutes=1),
    ).ok is True


def test_scope_mismatch_and_expiry_fail_closed() -> None:
    receipt = build_approval_receipt(
        approvalId="approval-002",
        approverRef="user-digest:owner",
        approvalSource="human_operator",
        approvedActionKind="gate_activation",
        approvedActionDigest=_digest("3"),
        approvedScope="single_turn",
        policyDecisionId="decision-002",
        effectivePolicySnapshotDigest=_digest("4"),
        issuedAt=NOW,
        expiresAt=NOW + timedelta(seconds=30),
        constraints={},
    )

    scope_report = verify_approval_receipt_for_action(
        receipt,
        actionDigest=_digest("3"),
        requiredScope="time_window",
        now=NOW + timedelta(seconds=10),
    )
    expiry_report = verify_approval_receipt_for_action(
        receipt,
        actionDigest=_digest("3"),
        requiredScope="single_turn",
        now=NOW + timedelta(minutes=1),
    )

    assert scope_report.ok is False
    assert "approval_scope_mismatch" in scope_report.reason_codes
    assert expiry_report.ok is False
    assert "approval_expired" in expiry_report.reason_codes


def test_revocation_is_a_separate_receipt_and_invalidates_original() -> None:
    original = build_approval_receipt(
        approvalId="approval-003",
        approverRef="user-digest:owner",
        approvalSource="human_operator",
        approvedActionKind="gate_activation",
        approvedActionDigest=_digest("5"),
        approvedScope="time_window",
        policyDecisionId="decision-003",
        effectivePolicySnapshotDigest=_digest("6"),
        issuedAt=NOW,
        expiresAt=NOW + timedelta(minutes=10),
        constraints={"gate": "Gate1A"},
    )
    revoked = original.revoke(
        revocationId="approval-revoke-003",
        revokedByRef="user-digest:owner",
        revokedAt=NOW + timedelta(minutes=2),
        reasonCode="operator_closed_gate",
    )

    assert revoked.revokes_approval_digest == original.approval_digest
    report = verify_approval_receipt_for_action(
        original,
        actionDigest=_digest("5"),
        requiredScope="time_window",
        now=NOW + timedelta(minutes=3),
        revocations=(revoked,),
    )
    assert report.ok is False
    assert "approval_revoked" in report.reason_codes


def test_approval_rejects_raw_secret_or_prompt_fields() -> None:
    with pytest.raises(ValidationError, match="constraints"):
        build_approval_receipt(
            approvalId="approval-004",
            approverRef="user-digest:owner",
            approvalSource="human_operator",
            approvedActionKind="tool_call",
            approvedActionDigest=_digest("7"),
            approvedScope="single_tool_call",
            policyDecisionId="decision-004",
            effectivePolicySnapshotDigest=_digest("8"),
            issuedAt=NOW,
            expiresAt=NOW + timedelta(minutes=5),
            constraints={"Authorization": "Bearer secret-token"},
        )


def test_scope_values_are_closed() -> None:
    assert set(ApprovalScope.__args__) == {
        "single_tool_call",
        "single_turn",
        "time_window",
        "gate_activation",
        "workflow_run",
    }


def test_approval_receipt_fixture_is_digest_only_and_valid() -> None:
    fixture = json.loads((FIXTURE_DIR / "approval_receipt.json").read_text())
    receipt = ApprovalReceipt.model_validate(fixture)

    assert receipt.approval_digest.startswith("sha256:")
    encoded = json.dumps(fixture, sort_keys=True).lower()
    assert "authorization" not in encoded
    assert "cookie" not in encoded
    assert "bearer" not in encoded
