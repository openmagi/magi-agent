from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta

import pytest

from openmagi_core_agent.harness.approval_receipts import build_approval_receipt
from openmagi_core_agent.self_improvement.promotion_gate import (
    SelfImprovementPromotionConfig,
    SelfImprovementPromotionGate,
    SelfImprovementPromotionRequest,
    SelfImprovementPromotionResult,
    compute_self_improvement_promotion_action_digest,
)
from openmagi_core_agent.self_improvement.review_gate import (
    SelfImprovementReviewConfig,
    SelfImprovementReviewGate,
    SelfImprovementReviewRequest,
    SelfImprovementReviewResult,
)


NOW = datetime(2026, 5, 25, 7, 0, tzinfo=UTC)
PROPOSAL_DIGEST = "sha256:" + "a" * 64
AFFECTED_DIGEST = "sha256:" + "b" * 64
POLICY_DIGEST = "sha256:" + "c" * 64


def _review_request(**overrides: object) -> SelfImprovementReviewRequest:
    payload: dict[str, object] = {
        "reviewId": "self-improvement-review:review-1",
        "proposalDigest": PROPOSAL_DIGEST,
        "affectedDigestRefs": (AFFECTED_DIGEST,),
        "promotionScope": "recipe",
        "policySnapshotDigest": POLICY_DIGEST,
        "reviewerRefs": ("reviewer:human-operator",),
        "decision": "approved_for_promotion",
        "reasonCodes": ("proposal_has_eval_evidence",),
        "rawPrompt": "private prompt",
        "rawOutput": "private output",
        "rawPrivatePath": "/Users/kevin/private/proposal.diff",
        "toolLogs": "Authorization: Bearer review-token-value",
        "hiddenReasoning": "private chain of thought",
    }
    payload.update(overrides)
    return SelfImprovementReviewRequest.model_validate(payload)


def _approval_receipt(
    *,
    proposal_digest: str = PROPOSAL_DIGEST,
    affected_digest: str = AFFECTED_DIGEST,
    promotion_scope: str = "recipe",
    policy_digest: str = POLICY_DIGEST,
):
    action_digest = compute_self_improvement_promotion_action_digest(
        proposalDigest=proposal_digest,
        affectedDigest=affected_digest,
        promotionScope=promotion_scope,
        policySnapshotDigest=policy_digest,
    )
    return build_approval_receipt(
        approvalId="approval:self-improvement-promotion-1",
        approverRef="approver:human-operator",
        approvalSource="human_operator",
        approvedActionKind="workflow_run",
        approvedActionDigest=action_digest,
        approvedScope="workflow_run",
        policyDecisionId="policy-decision:self-improvement-promotion",
        effectivePolicySnapshotDigest=policy_digest,
        issuedAt=NOW,
        expiresAt=NOW + timedelta(minutes=10),
        constraints={
            "proposalDigest": proposal_digest,
            "affectedDigest": affected_digest,
            "promotionScope": promotion_scope,
            "policySnapshotDigest": policy_digest,
        },
    )


def _promotion_request(**overrides: object) -> SelfImprovementPromotionRequest:
    receipt = _approval_receipt()
    payload: dict[str, object] = {
        "requestId": "self-improvement-promotion:req-1",
        "proposalDigest": PROPOSAL_DIGEST,
        "affectedDigest": AFFECTED_DIGEST,
        "promotionScope": "recipe",
        "policySnapshotDigest": POLICY_DIGEST,
        "approvalReceipt": receipt.model_dump(by_alias=True, mode="json"),
        "now": NOW.isoformat().replace("+00:00", "Z"),
        "evalGateOk": True,
        "evalGateReasonCodes": (),
        "selectorFallbackOccurred": False,
        "rawProjectionFixturePassed": False,
        "pluginSandboxOverreachFixturePassed": False,
        "hardInvariantDowngraded": False,
        "rawPrompt": "private prompt",
        "rawOutput": "private output",
        "rawPrivatePath": "/Users/kevin/private/adoption.patch",
        "toolLogs": "cookie=session-value",
        "hiddenReasoning": "private chain of thought",
    }
    payload.update(overrides)
    return SelfImprovementPromotionRequest.model_validate(payload)


def test_review_gate_is_default_off_and_authority_free() -> None:
    gate = SelfImprovementReviewGate(SelfImprovementReviewConfig())

    result = gate.review(_review_request())

    assert result.status == "disabled"
    assert result.review_record is None
    assert result.blocked_reason == "self_improvement_review_disabled"
    assert set(result.authority_flags.model_dump(by_alias=True).values()) == {False}


def test_local_fake_review_records_digest_only_approval_readiness() -> None:
    gate = SelfImprovementReviewGate(
        SelfImprovementReviewConfig(enabled=True, localFakeReviewEnabled=True)
    )

    result = gate.review(_review_request())

    assert result.status == "review_recorded_local_fake"
    assert result.review_record is not None
    record = result.review_record
    assert record.proposal_digest == PROPOSAL_DIGEST
    assert record.affected_digest_refs == (AFFECTED_DIGEST,)
    assert record.promotion_scope == "recipe"
    assert record.policy_snapshot_digest == POLICY_DIGEST
    assert record.review_digest.startswith("sha256:")
    assert record.execution_default == "denied"

    encoded = json.dumps(result.model_dump(by_alias=True), sort_keys=True)
    for fragment in (
        "private prompt",
        "private output",
        "/Users/kevin",
        "Authorization: Bearer",
        "private chain of thought",
        "rawPrompt",
        "rawOutput",
        "toolLogs",
    ):
        assert fragment not in encoded


def test_promotion_gate_is_default_off_and_authority_free() -> None:
    gate = SelfImprovementPromotionGate(SelfImprovementPromotionConfig())

    result = gate.evaluate(_promotion_request())

    assert result.status == "disabled"
    assert result.blocked_reason == "self_improvement_promotion_disabled"
    assert result.reason_codes == ("self_improvement_promotion_disabled",)
    assert set(result.authority_flags.model_dump(by_alias=True).values()) == {False}


def test_valid_local_fake_promotion_requires_bound_approval_receipt() -> None:
    gate = SelfImprovementPromotionGate(
        SelfImprovementPromotionConfig(enabled=True, localFakePromotionEnabled=True)
    )

    result = gate.evaluate(_promotion_request())

    assert result.status == "promotion_ready_local_fake"
    assert result.reason_codes == ()
    assert result.approval_verification_ok is True
    assert result.promotion_action_digest == compute_self_improvement_promotion_action_digest(
        proposalDigest=PROPOSAL_DIGEST,
        affectedDigest=AFFECTED_DIGEST,
        promotionScope="recipe",
        policySnapshotDigest=POLICY_DIGEST,
    )
    assert result.promotion_receipt_digest.startswith("sha256:")
    assert result.execution_default == "denied"
    assert set(result.authority_flags.model_dump(by_alias=True).values()) == {False}


def test_approval_receipt_cannot_be_reused_for_different_proposal_digest() -> None:
    gate = SelfImprovementPromotionGate(
        SelfImprovementPromotionConfig(enabled=True, localFakePromotionEnabled=True)
    )

    reused_receipt = _approval_receipt(proposal_digest=PROPOSAL_DIGEST)
    result = gate.evaluate(
        _promotion_request(
            proposalDigest="sha256:" + "d" * 64,
            approvalReceipt=reused_receipt.model_dump(by_alias=True, mode="json"),
        )
    )

    assert result.status == "blocked"
    assert result.approval_verification_ok is False
    assert "approval_action_digest_mismatch" in result.reason_codes
    assert result.execution_default == "denied"


def test_approval_receipt_constraints_must_match_requested_promotion() -> None:
    gate = SelfImprovementPromotionGate(
        SelfImprovementPromotionConfig(enabled=True, localFakePromotionEnabled=True)
    )
    receipt = _approval_receipt()
    forged_payload = receipt.model_dump(by_alias=True, mode="json")
    forged_payload["constraints"] = {
        "proposalDigest": PROPOSAL_DIGEST,
        "affectedDigest": "sha256:" + "e" * 64,
        "promotionScope": "recipe",
    }
    forged_payload.pop("approvalDigest")
    forged_receipt = build_approval_receipt(
        approvalId=forged_payload["approvalId"],
        approverRef=forged_payload["approverRef"],
        approvalSource="human_operator",
        approvedActionKind="workflow_run",
        approvedActionDigest=forged_payload["approvedActionDigest"],
        approvedScope="workflow_run",
        policyDecisionId=forged_payload["policyDecisionId"],
        effectivePolicySnapshotDigest=forged_payload["effectivePolicySnapshotDigest"],
        issuedAt=NOW,
        expiresAt=NOW + timedelta(minutes=10),
        constraints=forged_payload["constraints"],
    )

    result = gate.evaluate(
        _promotion_request(approvalReceipt=forged_receipt.model_dump(by_alias=True, mode="json"))
    )

    assert result.status == "blocked"
    assert result.approval_verification_ok is False
    assert result.reason_codes == ("approval_constraints_mismatch",)


def test_promotion_approval_binds_action_kind_and_policy_snapshot_digest() -> None:
    gate = SelfImprovementPromotionGate(
        SelfImprovementPromotionConfig(enabled=True, localFakePromotionEnabled=True)
    )
    receipt = _approval_receipt()
    action_kind_payload = receipt.model_dump(by_alias=True, mode="json")
    action_kind_payload["approvedActionKind"] = "model_call"
    action_kind_payload.pop("approvalDigest")
    wrong_kind = build_approval_receipt(
        approvalId=action_kind_payload["approvalId"],
        approverRef=action_kind_payload["approverRef"],
        approvalSource="human_operator",
        approvedActionKind="model_call",
        approvedActionDigest=action_kind_payload["approvedActionDigest"],
        approvedScope="workflow_run",
        policyDecisionId=action_kind_payload["policyDecisionId"],
        effectivePolicySnapshotDigest=action_kind_payload["effectivePolicySnapshotDigest"],
        issuedAt=NOW,
        expiresAt=NOW + timedelta(minutes=10),
        constraints=action_kind_payload["constraints"],
    )

    wrong_policy = _approval_receipt(policy_digest="sha256:" + "d" * 64)

    wrong_kind_result = gate.evaluate(
        _promotion_request(approvalReceipt=wrong_kind.model_dump(by_alias=True, mode="json"))
    )
    wrong_policy_result = gate.evaluate(
        _promotion_request(
            approvalReceipt=wrong_policy.model_dump(by_alias=True, mode="json"),
        )
    )

    assert wrong_kind_result.status == "blocked"
    assert "approval_action_kind_mismatch" in wrong_kind_result.reason_codes
    assert wrong_policy_result.status == "blocked"
    assert "approval_action_digest_mismatch" in wrong_policy_result.reason_codes
    assert "approval_policy_snapshot_mismatch" in wrong_policy_result.reason_codes
    assert "approval_constraints_mismatch" in wrong_policy_result.reason_codes


def test_promotion_blocks_regression_bypass_and_invariant_failures() -> None:
    gate = SelfImprovementPromotionGate(
        SelfImprovementPromotionConfig(enabled=True, localFakePromotionEnabled=True)
    )

    result = gate.evaluate(
        _promotion_request(
            evalGateOk=False,
            evalGateReasonCodes=("unsupported_claim_rate_exceeds_threshold",),
            selectorFallbackOccurred=True,
            rawProjectionFixturePassed=True,
            pluginSandboxOverreachFixturePassed=True,
            hardInvariantDowngraded=True,
        )
    )

    assert result.status == "blocked"
    assert result.approval_verification_ok is True
    assert result.reason_codes == (
        "eval_regression_detected",
        "unsupported_claim_rate_exceeds_threshold",
        "selector_fallback_detected",
        "raw_projection_detected",
        "plugin_sandbox_overreach_detected",
        "hard_invariant_downgrade_detected",
    )


def test_promotion_result_cannot_enable_mutation_or_activation_by_copy_construct() -> None:
    gate = SelfImprovementPromotionGate(
        SelfImprovementPromotionConfig(enabled=True, localFakePromotionEnabled=True)
    )
    result = gate.evaluate(_promotion_request())

    copied = result.model_copy(
        update={
            "executionDefault": "allowed",
            "authorityFlags": {"repoMutationEnabled": True, "deployEnabled": True},
        }
    )
    deprecated_copy = result.copy(
        update={
            "execution_default": "allowed",
            "authority_flags": {"productionWriteEnabled": True},
        }
    )
    constructed = type(result).model_construct(
        status="promotion_ready_local_fake",
        promotionActionDigest=result.promotion_action_digest,
        promotionReceiptDigest=result.promotion_receipt_digest,
        approvalVerificationOk=True,
        reasonCodes=(),
        executionDefault="allowed",
        authorityFlags={"modelCallEnabled": True},
    )
    for value in (copied, deprecated_copy, constructed):
        assert value.execution_default == "denied"
        assert set(value.authority_flags.model_dump(by_alias=True).values()) == {False}

    with pytest.raises(ValueError, match="promotionReceiptDigest"):
        SelfImprovementPromotionResult.model_validate(
            result.model_dump(by_alias=True)
            | {"reasonCodes": ("approval_bypass",), "promotionReceiptDigest": "sha256:" + "e" * 64}
        )


def test_review_and_promotion_import_boundary_does_not_initialize_live_runtime() -> None:
    forbidden = {
        "google.adk.runners",
        "google.adk.agents",
        "openmagi_core_agent.tools.host",
        "openmagi_core_agent.transport.chat",
        "openmagi_core_agent.memory.hipocampus",
        "openmagi_core_agent.memory.qmd",
    }
    for module_name in forbidden:
        sys.modules.pop(module_name, None)

    __import__("openmagi_core_agent.self_improvement.review_gate")
    __import__("openmagi_core_agent.self_improvement.promotion_gate")

    assert forbidden.isdisjoint(sys.modules)


def test_review_result_revalidates_digest_and_rejects_raw_projection_spoofing() -> None:
    gate = SelfImprovementReviewGate(
        SelfImprovementReviewConfig(enabled=True, localFakeReviewEnabled=True)
    )
    result = gate.review(_review_request())
    assert result.review_record is not None

    with pytest.raises(ValueError, match="model_copy"):
        result.review_record.model_copy(update={"summary": "/Users/kevin/private"})
    with pytest.raises(ValueError, match="copy is disabled"):
        result.review_record.copy(update={"reviewDigest": "sha256:" + "e" * 64})
    with pytest.raises(ValueError, match="reviewDigest"):
        type(result.review_record).model_validate(
            result.review_record.model_dump(by_alias=True) | {"reviewDigest": "sha256:" + "e" * 64}
        )
    with pytest.raises(ValueError, match="summary"):
        type(result.review_record).model_validate(
            result.review_record.model_dump(by_alias=True)
            | {"summary": "raw output: /Users/kevin/private"}
        )

    copied_result = result.model_copy(
        update={"authorityFlags": {"repoMutationEnabled": True}}
    )
    constructed_result = SelfImprovementReviewResult.model_construct(
        status="review_recorded_local_fake",
        reviewRecord=result.review_record,
        authorityFlags={"deployEnabled": True},
    )
    for value in (copied_result, constructed_result):
        assert set(value.authority_flags.model_dump(by_alias=True).values()) == {False}


def test_review_and_promotion_configs_cannot_enable_live_or_automatic_paths() -> None:
    review_config = SelfImprovementReviewConfig.model_construct(
        enabled=True,
        localFakeReviewEnabled=True,
        liveAdkRunnerEnabled=True,
        automaticPromotionEnabled=True,
    )
    promotion_config = SelfImprovementPromotionConfig.model_construct(
        enabled=True,
        localFakePromotionEnabled=True,
        productionMutationEnabled=True,
        automaticPromotionEnabled=True,
    )

    assert review_config.model_dump(by_alias=True)["liveAdkRunnerEnabled"] is False
    assert review_config.model_copy(
        update={"automaticPromotionEnabled": True}
    ).model_dump(by_alias=True)["automaticPromotionEnabled"] is False
    assert promotion_config.model_dump(by_alias=True)["productionMutationEnabled"] is False
    assert promotion_config.copy(
        update={"production_mutation_enabled": True}
    ).model_dump(by_alias=True)["productionMutationEnabled"] is False
