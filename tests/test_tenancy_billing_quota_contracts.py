from __future__ import annotations

import json
import subprocess
import sys

import pytest
from pydantic import ValidationError

from magi_agent.billing.quota import (
    QuotaDecision,
    QuotaEvaluationConfig,
    QuotaLimit,
    QuotaRequest,
    evaluate_quota,
)
from magi_agent.billing.spend_guard import (
    SpendAmount,
    SpendCommitRequest,
    SpendReleaseRequest,
    SpendReservationReceipt,
    SpendReservationRequest,
    commit_spend_reservation,
    release_spend_reservation,
    reserve_spend,
)
from magi_agent.tenancy.context import (
    AuthorityScope,
    TenantContext,
    TenantRuntimeAuthorityFlags,
)


DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
DIGEST_C = "sha256:" + "c" * 64


def _authority_scope() -> AuthorityScope:
    return AuthorityScope(
        scopeId="scope:tenant-1",
        tenantId="tenant:alpha",
        ownerUserId="user:owner-1",
        botId="bot:assistant-1",
        env="local",
        allowedOperations=("operation:model-inference", "operation:tool-read"),
        policySnapshotDigest=DIGEST_A,
    )


def _tenant_context() -> TenantContext:
    return TenantContext(
        tenantId="tenant:alpha",
        ownerUserId="user:owner-1",
        botId="bot:assistant-1",
        env="local",
        authorityScope=_authority_scope(),
        policySnapshotDigest=DIGEST_A,
        metadata={"plan": "plan:local-free"},
    )


def test_tenant_context_projects_safe_authority_scope_only() -> None:
    context = _tenant_context()
    projection = context.public_projection()

    assert projection["tenantId"] == "tenant:alpha"
    assert projection["authorityFlags"] == TenantRuntimeAuthorityFlags().public_projection()
    assert projection["authorityFlags"]["liveBillingCallsEnabled"] is False
    assert projection["authorityFlags"]["stripeAttached"] is False
    assert projection["authorityFlags"]["supabaseAttached"] is False
    assert projection["authorityScope"]["scopeDigest"].startswith("sha256:")
    assert "allowedOperations" in projection["authorityScope"]
    encoded = json.dumps(projection, sort_keys=True)
    assert "raw" + "Prompt" not in encoded
    assert "Authorization" not in encoded
    assert "Cookie" not in encoded
    assert "/Users/" not in encoded


def test_tenant_context_rejects_raw_private_or_secret_material() -> None:
    with pytest.raises(ValidationError):
        TenantContext(
            tenantId="tenant:alpha",
            ownerUserId="user:owner-1",
            botId="bot:assistant-1",
            env="local",
            authorityScope=_authority_scope(),
            policySnapshotDigest=DIGEST_A,
            metadata={"auth" + "Header": "Bearer unsafe"},
        )
    with pytest.raises(ValidationError):
        TenantContext(
            tenantId="/Users/example",
            ownerUserId="user:owner-1",
            botId="bot:assistant-1",
            env="local",
            authorityScope=_authority_scope(),
            policySnapshotDigest=DIGEST_A,
        )


def test_tenant_authority_flags_cannot_be_forged() -> None:
    flags = TenantRuntimeAuthorityFlags.model_validate(
        {
            "liveBillingCallsEnabled": True,
            "stripeAttached": True,
            "supabaseAttached": True,
            "quotaMutationAttached": True,
            "productionAuthority": True,
        }
    )

    assert set(flags.public_projection().values()) == {False}
    # C-4 PR-I raise-to-coerce: model_copy(update=...) and model_construct
    # both route through model_validate (kernel) -- forged Literal[False]
    # assertions are coerced back to False instead of raising. The
    # force-false invariant is preserved.
    copied = flags.model_copy(update={"liveBillingCallsEnabled": True})
    assert copied.live_billing_calls_enabled is False
    constructed = TenantRuntimeAuthorityFlags.model_construct(liveBillingCallsEnabled=True)
    assert constructed.live_billing_calls_enabled is False


def test_quota_evaluation_is_fail_closed_by_default() -> None:
    decision = evaluate_quota(
        QuotaRequest(
            tenantContext=_tenant_context(),
            operationRef="operation:model-inference",
            quotaKey="quota:requests",
            requestedAmount=1,
            idempotencyKey="idem:turn-1",
            policySnapshotDigest=DIGEST_A,
        ),
        limits=(QuotaLimit(quotaKey="quota:requests", unit="requests", maxAmount=10, usedAmount=0),),
    )

    assert decision.status == "fail_closed"
    assert decision.allowed is False
    assert "local_quota_evaluation_disabled" in decision.reason_codes
    assert decision.live_billing_system_queried is False
    assert decision.production_quota_mutated is False


def test_quota_evaluation_allows_only_local_fake_when_enabled() -> None:
    request = QuotaRequest(
        tenantContext=_tenant_context(),
        operationRef="operation:model-inference",
        quotaKey="quota:requests",
        requestedAmount=3,
        idempotencyKey="idem:turn-2",
        policySnapshotDigest=DIGEST_A,
    )
    decision = evaluate_quota(
        request,
        limits=(
            QuotaLimit(
                quotaKey="quota:requests",
                unit="requests",
                maxAmount=10,
                usedAmount=2,
                reservedAmount=1,
            ),
        ),
        config=QuotaEvaluationConfig(localEvaluationEnabled=True),
    )

    assert decision.status == "allowed"
    assert decision.allowed is True
    assert decision.remaining_after_decision == 4
    assert decision.source == "local_contract"
    assert decision.live_billing_system_queried is False


def test_quota_evaluation_denies_exhausted_or_missing_limit_without_fallback() -> None:
    request = QuotaRequest(
        tenantContext=_tenant_context(),
        operationRef="operation:model-inference",
        quotaKey="quota:requests",
        requestedAmount=5,
        idempotencyKey="idem:turn-3",
        policySnapshotDigest=DIGEST_A,
    )
    exhausted = evaluate_quota(
        request,
        limits=(QuotaLimit(quotaKey="quota:requests", unit="requests", maxAmount=6, usedAmount=4),),
        config=QuotaEvaluationConfig(localEvaluationEnabled=True),
    )
    missing = evaluate_quota(
        request,
        limits=(),
        config=QuotaEvaluationConfig(localEvaluationEnabled=True),
    )

    assert exhausted.status == "denied"
    assert exhausted.allowed is False
    assert "quota_exhausted" in exhausted.reason_codes
    assert missing.status == "fail_closed"
    assert "quota_limit_missing" in missing.reason_codes


def test_spend_reserve_commit_release_are_digest_only_and_non_production() -> None:
    quota_decision = QuotaDecision(
        tenantContext=_tenant_context(),
        operationRef="operation:model-inference",
        quotaKey="quota:spend-usd-micros",
        unit="usd_micros",
        requestedAmount=500,
        allowed=True,
        status="allowed",
        source="local_contract",
        remainingAfterDecision=9500,
        policySnapshotDigest=DIGEST_A,
        reasonCodes=("quota_available",),
    )
    reservation = reserve_spend(
        SpendReservationRequest(
            tenantContext=_tenant_context(),
            reservationId="reservation:turn-1",
            operationRef="operation:model-inference",
            spendQuotaKey="quota:spend-usd-micros",
            idempotencyKey="idem:spend-1",
            amount=SpendAmount(currency="USD", micros=500),
            quotaDecision=quota_decision,
            policySnapshotDigest=DIGEST_A,
        )
    )
    committed = commit_spend_reservation(
        SpendCommitRequest(
            reservationReceipt=reservation,
            finalAmount=SpendAmount(currency="USD", micros=400),
            policySnapshotDigest=DIGEST_A,
        )
    )
    released = release_spend_reservation(
        SpendReleaseRequest(
            reservationReceipt=reservation,
            reasonCode="unused_reservation",
            policySnapshotDigest=DIGEST_A,
        )
    )

    assert reservation.status == "reserved"
    assert committed.status == "committed"
    assert released.status == "released"
    for receipt in (reservation, committed, released):
        projection = receipt.public_projection()
        assert projection["liveBillingCall"] is False
        assert projection["productionBillingCommitted"] is False
        assert projection["receiptDigest"].startswith("sha256:")
        encoded = json.dumps(projection, sort_keys=True)
        assert "raw" + "Prompt" not in encoded
        assert "session" + "Key" not in encoded
        assert "Cookie" not in encoded


def test_spend_reservation_fails_closed_when_quota_is_not_allowed() -> None:
    denied = QuotaDecision(
        tenantContext=_tenant_context(),
        operationRef="operation:model-inference",
        quotaKey="quota:spend-usd-micros",
        unit="usd_micros",
        requestedAmount=500,
        allowed=False,
        status="denied",
        source="local_contract",
        remainingAfterDecision=0,
        policySnapshotDigest=DIGEST_A,
        reasonCodes=("quota_exhausted",),
    )

    receipt = reserve_spend(
        SpendReservationRequest(
            tenantContext=_tenant_context(),
            reservationId="reservation:turn-2",
            operationRef="operation:model-inference",
            spendQuotaKey="quota:spend-usd-micros",
            idempotencyKey="idem:spend-2",
            amount=SpendAmount(currency="USD", micros=500),
            quotaDecision=denied,
            policySnapshotDigest=DIGEST_A,
        )
    )

    assert receipt.status == "fail_closed"
    assert receipt.production_billing_committed is False
    assert "quota_not_allowed" in receipt.reason_codes


def test_spend_commit_rejects_amount_above_reservation() -> None:
    decision = QuotaDecision(
        tenantContext=_tenant_context(),
        operationRef="operation:model-inference",
        quotaKey="quota:spend-usd-micros",
        unit="usd_micros",
        requestedAmount=500,
        allowed=True,
        status="allowed",
        source="local_contract",
        remainingAfterDecision=9500,
        policySnapshotDigest=DIGEST_A,
        reasonCodes=("quota_available",),
    )
    reservation = reserve_spend(
        SpendReservationRequest(
            tenantContext=_tenant_context(),
            reservationId="reservation:turn-3",
            operationRef="operation:model-inference",
            spendQuotaKey="quota:spend-usd-micros",
            idempotencyKey="idem:spend-3",
            amount=SpendAmount(currency="USD", micros=500),
            quotaDecision=decision,
            policySnapshotDigest=DIGEST_A,
        )
    )

    with pytest.raises(ValidationError):
        commit_spend_reservation(
            SpendCommitRequest(
                reservationReceipt=reservation,
                finalAmount=SpendAmount(currency="USD", micros=501),
                policySnapshotDigest=DIGEST_A,
            )
        )


def test_quota_decision_rejects_operation_outside_authority_scope() -> None:
    with pytest.raises(ValidationError, match="inside tenant authority scope"):
        QuotaDecision(
            tenantContext=_tenant_context(),
            operationRef="operation:unauthorized",
            quotaKey="quota:requests",
            unit="requests",
            requestedAmount=1,
            allowed=True,
            status="allowed",
            source="local_contract",
            remainingAfterDecision=9,
            policySnapshotDigest=DIGEST_A,
            reasonCodes=("quota_available",),
        )


def test_spend_reservation_requires_spend_quota_key_and_usd_micros_unit() -> None:
    request_quota = QuotaDecision(
        tenantContext=_tenant_context(),
        operationRef="operation:model-inference",
        quotaKey="quota:requests",
        unit="requests",
        requestedAmount=500,
        allowed=True,
        status="allowed",
        source="local_contract",
        remainingAfterDecision=9500,
        policySnapshotDigest=DIGEST_A,
        reasonCodes=("quota_available",),
    )

    with pytest.raises(ValidationError, match="quota decision key mismatch"):
        SpendReservationRequest(
            tenantContext=_tenant_context(),
            reservationId="reservation:turn-4",
            operationRef="operation:model-inference",
            spendQuotaKey="quota:spend-usd-micros",
            idempotencyKey="idem:spend-4",
            amount=SpendAmount(currency="USD", micros=500),
            quotaDecision=request_quota,
            policySnapshotDigest=DIGEST_A,
        )

    with pytest.raises(ValidationError, match="usd_micros"):
        SpendReservationRequest(
            tenantContext=_tenant_context(),
            reservationId="reservation:turn-5",
            operationRef="operation:model-inference",
            spendQuotaKey="quota:requests",
            idempotencyKey="idem:spend-5",
            amount=SpendAmount(currency="USD", micros=500),
            quotaDecision=request_quota,
            policySnapshotDigest=DIGEST_A,
        )


def test_spend_receipt_transition_requires_parent_digest() -> None:
    with pytest.raises(ValidationError, match="parent receipt digest"):
        SpendReservationReceipt(
            tenantContext=_tenant_context(),
            reservationId="reservation:forged",
            operationRef="operation:model-inference",
            amount=SpendAmount(currency="USD", micros=100),
            status="committed",
            policySnapshotDigest=DIGEST_A,
            reasonCodes=("local_spend_commit_recorded",),
            requestDigest=DIGEST_B,
        )
    with pytest.raises(ValidationError, match="must not include parent"):
        SpendReservationReceipt(
            tenantContext=_tenant_context(),
            reservationId="reservation:forged",
            operationRef="operation:model-inference",
            amount=SpendAmount(currency="USD", micros=100),
            status="reserved",
            policySnapshotDigest=DIGEST_A,
            reasonCodes=("local_spend_reserved",),
            requestDigest=DIGEST_B,
            parentReceiptDigest=DIGEST_C,
        )


def test_spend_receipt_rejects_operation_outside_authority_scope() -> None:
    with pytest.raises(ValidationError, match="inside tenant authority scope"):
        SpendReservationReceipt(
            tenantContext=_tenant_context(),
            reservationId="reservation:forged-op",
            operationRef="operation:unauthorized",
            amount=SpendAmount(currency="USD", micros=100),
            status="reserved",
            policySnapshotDigest=DIGEST_A,
            reasonCodes=("local_spend_reserved",),
            requestDigest=DIGEST_B,
        )
    with pytest.raises(ValidationError, match="inside tenant authority scope"):
        SpendReservationReceipt(
            tenantContext=_tenant_context(),
            reservationId="reservation:forged-op",
            operationRef="operation:unauthorized",
            amount=SpendAmount(currency="USD", micros=100),
            status="committed",
            policySnapshotDigest=DIGEST_A,
            reasonCodes=("local_spend_commit_recorded",),
            requestDigest=DIGEST_B,
            parentReceiptDigest=DIGEST_C,
        )


def test_billing_quota_import_boundary_has_no_live_system_imports() -> None:
    script = """
import sys
import magi_agent.billing.quota
import magi_agent.billing.spend_guard
import magi_agent.tenancy.context
for name in (
    'stripe',
    'supabase',
    'psycopg',
    'httpx',
    'requests',
    'kubernetes',
    'google.adk.runners',
):
    if name in sys.modules:
        raise SystemExit(name)
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
