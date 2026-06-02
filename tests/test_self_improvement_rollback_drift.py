from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta

import pytest

from magi_agent.harness.approval_receipts import build_approval_receipt
from magi_agent.self_improvement.drift_watch import (
    DriftWatchConfig,
    DriftWatchRequest,
    DriftWatchResult,
    DriftWatchService,
)
from magi_agent.self_improvement.rollback import (
    ReplayPolicySnapshotBinding,
    RollbackConfig,
    RollbackReceipt,
    RollbackRequest,
    RollbackService,
    compute_self_improvement_rollback_action_digest,
    preserve_replay_policy_snapshot,
)


NOW = datetime(2026, 5, 25, 9, 0, tzinfo=UTC)
PROMOTION_RECEIPT_DIGEST = "sha256:" + "a" * 64
PROMOTED_DIGEST = "sha256:" + "b" * 64
PREVIOUS_DIGEST = "sha256:" + "c" * 64
POLICY_DIGEST = "sha256:" + "d" * 64
CURRENT_POLICY_DIGEST = "sha256:" + "e" * 64
ORIGINAL_RUN_RECEIPT_DIGEST = "sha256:" + "f" * 64
RECIPE_DIGEST = "sha256:" + "1" * 64
HARNESS_CONFIG_DIGEST = "sha256:" + "2" * 64
PLUGIN_CONFIG_DIGEST = "sha256:" + "3" * 64
EVAL_THRESHOLD_DIGEST = "sha256:" + "4" * 64
PLUGIN_SUPPLY_CHAIN_DIGEST = "sha256:" + "5" * 64


def _rollback_approval_receipt(
    *,
    promotion_receipt_digest: str = PROMOTION_RECEIPT_DIGEST,
    promoted_artifact_digest: str = PROMOTED_DIGEST,
    previous_artifact_digest: str = PREVIOUS_DIGEST,
    rollback_scope: str = "recipe",
    policy_digest: str = POLICY_DIGEST,
    reason_codes: tuple[str, ...] = ("regression_detected",),
):
    action_digest = compute_self_improvement_rollback_action_digest(
        promotionReceiptDigest=promotion_receipt_digest,
        promotedArtifactDigest=promoted_artifact_digest,
        previousArtifactDigest=previous_artifact_digest,
        rollbackScope=rollback_scope,
        policySnapshotDigest=policy_digest,
        reasonCodes=reason_codes,
    )
    return build_approval_receipt(
        approvalId="approval:self-improvement-rollback-1",
        approverRef="approver:human-operator",
        approvalSource="human_operator",
        approvedActionKind="workflow_run",
        approvedActionDigest=action_digest,
        approvedScope="workflow_run",
        policyDecisionId="policy-decision:self-improvement-rollback",
        effectivePolicySnapshotDigest=policy_digest,
        issuedAt=NOW,
        expiresAt=NOW + timedelta(minutes=10),
        constraints={
            "promotionReceiptDigest": promotion_receipt_digest,
            "promotedArtifactDigest": promoted_artifact_digest,
            "previousArtifactDigest": previous_artifact_digest,
            "rollbackScope": rollback_scope,
            "policySnapshotDigest": policy_digest,
            "reasonCodes": list(reason_codes),
        },
    )


def _rollback_request(**overrides: object) -> RollbackRequest:
    payload: dict[str, object] = {
        "requestId": "self-improvement-rollback:req-1",
        "promotionReceiptDigest": PROMOTION_RECEIPT_DIGEST,
        "promotedArtifactDigest": PROMOTED_DIGEST,
        "previousArtifactDigest": PREVIOUS_DIGEST,
        "policySnapshotDigest": POLICY_DIGEST,
        "rollbackScope": "recipe",
        "reasonCodes": ("regression_detected",),
        "requestedAutomaticExecution": False,
        "now": NOW.isoformat().replace("+00:00", "Z"),
        "rawPrompt": "private prompt",
        "rawOutput": "private output",
        "rawPrivatePath": "private://rollback.patch",
        "toolLogs": "raw auth marker",
        "hiddenReasoning": "private chain of thought",
    }
    payload.update(overrides)
    return RollbackRequest.model_validate(payload)


def _drift_request(**overrides: object) -> DriftWatchRequest:
    payload: dict[str, object] = {
        "requestId": "self-improvement-drift:req-1",
        "baselineRecipeDigest": RECIPE_DIGEST,
        "candidateRecipeDigest": RECIPE_DIGEST,
        "baselineHarnessConfigDigest": HARNESS_CONFIG_DIGEST,
        "candidateHarnessConfigDigest": HARNESS_CONFIG_DIGEST,
        "baselinePluginConfigDigest": PLUGIN_CONFIG_DIGEST,
        "candidatePluginConfigDigest": PLUGIN_CONFIG_DIGEST,
        "baselineModelTierRef": "model-tier:gpt-5.4",
        "candidateModelTierRef": "model-tier:gpt-5.4",
        "baselinePolicySnapshotDigest": POLICY_DIGEST,
        "candidatePolicySnapshotDigest": POLICY_DIGEST,
        "baselineEvalThresholdDigest": EVAL_THRESHOLD_DIGEST,
        "candidateEvalThresholdDigest": EVAL_THRESHOLD_DIGEST,
        "baselinePluginSupplyChainDigest": PLUGIN_SUPPLY_CHAIN_DIGEST,
        "candidatePluginSupplyChainDigest": PLUGIN_SUPPLY_CHAIN_DIGEST,
        "rawPrompt": "private prompt",
        "rawOutput": "private output",
        "rawPrivatePath": "private://drift.json",
        "toolLogs": "raw session marker",
        "hiddenReasoning": "private chain of thought",
    }
    payload.update(overrides)
    return DriftWatchRequest.model_validate(payload)


def test_rollback_is_default_off_and_authority_free() -> None:
    result = RollbackService(RollbackConfig()).record(_rollback_request())

    assert result.status == "disabled"
    assert result.rollback_receipt is None
    assert result.blocked_reason == "self_improvement_rollback_disabled"
    assert set(result.authority_flags.model_dump(by_alias=True).values()) == {False}


def test_local_fake_rollback_receipt_is_digest_only_and_non_executing() -> None:
    result = RollbackService(
        RollbackConfig(enabled=True, localFakeRollbackEnabled=True)
    ).record(_rollback_request())

    assert result.status == "rollback_recorded_local_fake"
    assert result.rollback_receipt is not None
    receipt = result.rollback_receipt
    assert receipt.rollback_scope == "recipe"
    assert receipt.promotion_receipt_digest == PROMOTION_RECEIPT_DIGEST
    assert receipt.promoted_artifact_digest == PROMOTED_DIGEST
    assert receipt.previous_artifact_digest == PREVIOUS_DIGEST
    assert receipt.policy_snapshot_digest == POLICY_DIGEST
    assert receipt.automatic_execution_decision == "denied"
    assert receipt.execution_default == "denied"
    assert receipt.rollback_receipt_digest.startswith("sha256:")

    encoded = json.dumps(result.model_dump(by_alias=True), sort_keys=True)
    for fragment in (
        "private prompt",
        "private output",
        "private://",
        "raw auth marker",
        "private chain of thought",
        "rawPrompt",
        "rawOutput",
        "toolLogs",
    ):
        assert fragment not in encoded


def test_automatic_rollback_requires_approval_and_local_fake_execution_gate() -> None:
    service = RollbackService(RollbackConfig(enabled=True, localFakeRollbackEnabled=True))

    denied = service.record(_rollback_request(requestedAutomaticExecution=True))
    assert denied.status == "blocked"
    assert denied.blocked_reason == "rollback_approval_receipt_required"
    assert denied.rollback_receipt is None

    digest_only_denied = service.record(
        _rollback_request(
            requestedAutomaticExecution=True,
            rollbackApprovalDigest="sha256:" + "f" * 64,
        )
    )
    assert digest_only_denied.status == "blocked"
    assert digest_only_denied.blocked_reason == "rollback_approval_receipt_required"

    still_denied = service.record(
        _rollback_request(
            requestedAutomaticExecution=True,
            rollbackApprovalReceipt=_rollback_approval_receipt().model_dump(
                by_alias=True,
                mode="json",
            ),
        )
    )
    assert still_denied.status == "blocked"
    assert still_denied.blocked_reason == "local_fake_rollback_execution_disabled"

    allowed_local_fake = RollbackService(
        RollbackConfig(
            enabled=True,
            localFakeRollbackEnabled=True,
            localFakeRollbackExecutionEnabled=True,
        )
    ).record(
        _rollback_request(
            requestedAutomaticExecution=True,
            rollbackApprovalReceipt=_rollback_approval_receipt().model_dump(
                by_alias=True,
                mode="json",
            ),
        )
    )
    assert allowed_local_fake.status == "rollback_recorded_local_fake"
    assert allowed_local_fake.rollback_receipt is not None
    assert allowed_local_fake.rollback_receipt.automatic_execution_decision == "allowed_local_fake"
    assert set(allowed_local_fake.authority_flags.model_dump(by_alias=True).values()) == {False}


def test_automatic_rollback_rejects_mismatched_approval_receipt() -> None:
    service = RollbackService(
        RollbackConfig(
            enabled=True,
            localFakeRollbackEnabled=True,
            localFakeRollbackExecutionEnabled=True,
        )
    )

    reused_receipt = _rollback_approval_receipt(promotion_receipt_digest="sha256:" + "6" * 64)
    result = service.record(
        _rollback_request(
            requestedAutomaticExecution=True,
            rollbackApprovalReceipt=reused_receipt.model_dump(by_alias=True, mode="json"),
        )
    )

    assert result.status == "blocked"
    assert result.blocked_reason in {
        "approval_action_digest_mismatch",
        "rollback_approval_constraints_mismatch",
    }
    assert result.rollback_receipt is None


def test_replay_keeps_original_effective_policy_snapshot_and_no_side_effects() -> None:
    binding = preserve_replay_policy_snapshot(
        originalRunId="run:self-improvement-eval-1",
        originalRunReceiptDigest=ORIGINAL_RUN_RECEIPT_DIGEST,
        originalPolicySnapshotDigest=POLICY_DIGEST,
        currentPolicySnapshotDigest=CURRENT_POLICY_DIGEST,
    )

    assert binding.original_run_id == "run:self-improvement-eval-1"
    assert binding.original_run_receipt_digest == ORIGINAL_RUN_RECEIPT_DIGEST
    assert binding.effective_policy_snapshot_digest == POLICY_DIGEST
    assert binding.current_policy_snapshot_digest == CURRENT_POLICY_DIGEST
    assert binding.replay_creates_side_effects is False
    assert binding.binding_digest.startswith("sha256:")


def test_drift_watch_is_default_off() -> None:
    result = DriftWatchService(DriftWatchConfig()).evaluate(_drift_request())

    assert result.status == "disabled"
    assert result.reason_codes == ("self_improvement_drift_watch_disabled",)
    assert set(result.authority_flags.model_dump(by_alias=True).values()) == {False}


def test_drift_watch_detects_model_policy_threshold_and_plugin_supply_chain_changes() -> None:
    result = DriftWatchService(
        DriftWatchConfig(enabled=True, localFakeDriftWatchEnabled=True)
    ).evaluate(
        _drift_request(
            candidateModelTierRef="model-tier:gpt-5.5",
            candidatePolicySnapshotDigest=CURRENT_POLICY_DIGEST,
            candidateEvalThresholdDigest="sha256:" + "3" * 64,
            candidatePluginSupplyChainDigest="sha256:" + "4" * 64,
        )
    )

    assert result.status == "drift_detected"
    assert result.reason_codes == (
        "model_tier_changed",
        "policy_snapshot_changed",
        "eval_threshold_changed",
        "plugin_supply_chain_digest_changed",
    )
    assert result.drift_digest.startswith("sha256:")
    assert set(result.authority_flags.model_dump(by_alias=True).values()) == {False}


def test_drift_watch_detects_recipe_harness_and_plugin_config_changes() -> None:
    result = DriftWatchService(
        DriftWatchConfig(enabled=True, localFakeDriftWatchEnabled=True)
    ).evaluate(
        _drift_request(
            candidateRecipeDigest="sha256:" + "6" * 64,
            candidateHarnessConfigDigest="sha256:" + "7" * 64,
            candidatePluginConfigDigest="sha256:" + "8" * 64,
        )
    )

    assert result.status == "drift_detected"
    assert result.reason_codes == (
        "recipe_digest_changed",
        "harness_config_digest_changed",
        "plugin_config_digest_changed",
    )
    assert result.drift_digest.startswith("sha256:")
    assert set(result.authority_flags.model_dump(by_alias=True).values()) == {False}


def test_rollback_and_drift_copy_construct_cannot_enable_authority_or_spoof_digest() -> None:
    rollback = RollbackService(
        RollbackConfig(enabled=True, localFakeRollbackEnabled=True)
    ).record(_rollback_request()).rollback_receipt
    assert rollback is not None
    with pytest.raises(ValueError, match="rollbackReceiptDigest"):
        RollbackReceipt.model_validate(
            rollback.model_dump(by_alias=True)
            | {"reasonCodes": ("different",), "rollbackReceiptDigest": "sha256:" + "9" * 64}
        )
    with pytest.raises(ValueError, match="copy is disabled"):
        rollback.copy(update={"executionDefault": "allowed"})
    constructed = type(rollback).model_construct(
        **(rollback.model_dump(by_alias=True) | {"authorityFlags": {"deployEnabled": True}})
    )
    assert set(constructed.authority_flags.model_dump(by_alias=True).values()) == {False}

    result = RollbackService(
        RollbackConfig(enabled=True, localFakeRollbackEnabled=True)
    ).record(_rollback_request())
    copied_result = result.copy(update={"authorityFlags": {"deployEnabled": True}})
    assert set(copied_result.authority_flags.model_dump(by_alias=True).values()) == {False}

    binding = preserve_replay_policy_snapshot(
        originalRunId="run:self-improvement-eval-1",
        originalRunReceiptDigest=ORIGINAL_RUN_RECEIPT_DIGEST,
        originalPolicySnapshotDigest=POLICY_DIGEST,
        currentPolicySnapshotDigest=CURRENT_POLICY_DIGEST,
    )
    with pytest.raises(ValueError, match="bindingDigest"):
        ReplayPolicySnapshotBinding.model_validate(
            binding.model_dump(by_alias=True) | {"bindingDigest": "sha256:" + "8" * 64}
        )
    copied_binding = binding.copy(update={"replayCreatesSideEffects": True})
    assert copied_binding.replay_creates_side_effects is False

    drift = DriftWatchService(
        DriftWatchConfig(enabled=True, localFakeDriftWatchEnabled=True)
    ).evaluate(
        _drift_request(
            candidatePolicySnapshotDigest=CURRENT_POLICY_DIGEST,
        )
    )
    with pytest.raises(ValueError, match="driftDigest"):
        DriftWatchResult.model_validate(
            drift.model_dump(by_alias=True) | {"driftDigest": "sha256:" + "7" * 64}
        )
    copied_drift = drift.copy(update={"authorityFlags": {"deployEnabled": True}})
    assert set(copied_drift.authority_flags.model_dump(by_alias=True).values()) == {False}


def test_rollback_and_drift_import_boundary_does_not_initialize_live_runtime() -> None:
    forbidden = {
        "google.adk.runners",
        "google.adk.agents",
        "magi_agent.tools.host",
        "magi_agent.transport.chat",
        "magi_agent.memory.hipocampus",
        "magi_agent.memory.qmd",
    }
    for module_name in forbidden:
        sys.modules.pop(module_name, None)

    __import__("magi_agent.self_improvement.rollback")
    __import__("magi_agent.self_improvement.drift_watch")

    assert forbidden.isdisjoint(sys.modules)
