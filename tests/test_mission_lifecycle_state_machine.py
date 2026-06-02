from __future__ import annotations

import importlib
import json
import sys

import pytest


def test_mission_lifecycle_state_set_is_explicit_and_default_off() -> None:
    from openmagi_core_agent.missions.lifecycle import (
        MISSION_LIFECYCLE_STATES,
        MissionLifecycleConfig,
        MissionLifecyclePolicy,
        MissionLifecycleStateMachine,
        MissionTransitionRequest,
    )

    assert MISSION_LIFECYCLE_STATES == (
        "draft",
        "pending_approval",
        "scheduled",
        "running",
        "paused",
        "blocked",
        "completed",
        "failed",
        "cancelled",
    )

    result = MissionLifecycleStateMachine().transition(
        request=MissionTransitionRequest(
            missionId="mission:alpha",
            runId="run:alpha",
            turnId="turn:alpha",
            fromState="draft",
            toState="pending_approval",
            evidenceRefs=("evidence:plan",),
        ),
        policy=MissionLifecyclePolicy(
            policyRef="policy:mission-lifecycle",
            policySnapshotRef="policy-snapshot:mission-pr7",
        ),
    )

    assert result.status == "disabled"
    assert result.reason_codes == ("mission_lifecycle_disabled",)
    assert result.receipt.transition_allowed is False
    assert result.receipt.local_fake_transition_recorded is False
    assert result.receipt.production_mutation_enabled is False
    assert set(result.authority_flags.model_dump(by_alias=True).values()) == {False}
    assert set(MissionLifecycleConfig().model_dump(by_alias=True).values()) == {False}


def test_allowed_transition_records_local_fake_receipt_with_policy_and_receipt_digest() -> None:
    from openmagi_core_agent.missions.lifecycle import (
        MissionLifecycleConfig,
        MissionLifecyclePolicy,
        MissionLifecycleStateMachine,
        MissionTransitionRequest,
    )

    machine = MissionLifecycleStateMachine(
        MissionLifecycleConfig(enabled=True, localFakeTransitionEnabled=True),
    )
    request = MissionTransitionRequest(
        missionId="mission:alpha",
        runId="run:alpha",
        turnId="turn:alpha",
        fromState="draft",
        toState="pending_approval",
        evidenceRefs=("evidence:plan",),
        approvalRef="approval:mission-start",
        reason="operator requested a plan",
        now=123,
    )
    policy = MissionLifecyclePolicy(
        policyRef="policy:mission-lifecycle",
        policySnapshotRef="policy-snapshot:mission-pr7",
        localFakeTransitionAllowed=True,
        approvalRequired=True,
        evidenceRequired=True,
    )

    result = machine.transition(request=request, policy=policy)
    projection = result.public_projection()

    assert result.status == "applied_local_fake"
    assert result.receipt.transition_allowed is True
    assert result.receipt.local_test_only is True
    assert result.receipt.local_fake_transition_recorded is True
    assert result.receipt.policy_snapshot_digest.startswith("sha256:")
    assert result.receipt.receipt_digest.startswith("sha256:")
    assert result.receipt.receipt_id == f"mission-transition:{result.receipt.receipt_digest[7:23]}"
    assert result.receipt.policy_snapshot_ref == "policy-snapshot:mission-pr7"
    assert projection["receipt"]["fromState"] == "draft"
    assert projection["receipt"]["toState"] == "pending_approval"
    assert projection["receipt"]["authorityFlags"]["schedulerAttached"] is False
    assert projection["receipt"]["authorityFlags"]["productionMutationEnabled"] is False


@pytest.mark.parametrize(
    ("from_state", "to_state", "reason"),
    (
        ("completed", "running", "mission_transition_denied"),
        ("failed", "running", "mission_transition_denied"),
        ("cancelled", "running", "mission_transition_denied"),
        ("draft", "completed", "mission_transition_denied"),
    ),
)
def test_denied_transitions_are_blocked_before_local_fake_recording(
    from_state: str,
    to_state: str,
    reason: str,
) -> None:
    from openmagi_core_agent.missions.lifecycle import (
        MissionLifecycleConfig,
        MissionLifecyclePolicy,
        MissionLifecycleStateMachine,
        MissionTransitionRequest,
    )

    result = MissionLifecycleStateMachine(
        MissionLifecycleConfig(enabled=True, localFakeTransitionEnabled=True),
    ).transition(
        request=MissionTransitionRequest(
            missionId="mission:alpha",
            runId="run:alpha",
            turnId="turn:alpha",
            fromState=from_state,
            toState=to_state,
            evidenceRefs=("evidence:plan",),
            approvalRef="approval:mission-start",
        ),
        policy=MissionLifecyclePolicy(
            policyRef="policy:mission-lifecycle",
            policySnapshotRef="policy-snapshot:mission-pr7",
            localFakeTransitionAllowed=True,
        ),
    )

    assert result.status == "blocked"
    assert reason in result.reason_codes
    assert result.receipt.transition_allowed is False
    assert result.receipt.local_fake_transition_recorded is False


def test_policy_evidence_and_approval_are_required_when_configured() -> None:
    from openmagi_core_agent.missions.lifecycle import (
        MissionLifecycleConfig,
        MissionLifecyclePolicy,
        MissionLifecycleStateMachine,
        MissionTransitionRequest,
    )

    machine = MissionLifecycleStateMachine(
        MissionLifecycleConfig(enabled=True, localFakeTransitionEnabled=True),
    )
    request = MissionTransitionRequest(
        missionId="mission:alpha",
        runId="run:alpha",
        turnId="turn:alpha",
        fromState="pending_approval",
        toState="scheduled",
        evidenceRefs=("evidence:plan",),
    )
    policy = MissionLifecyclePolicy(
        policyRef="policy:mission-lifecycle",
        policySnapshotRef="policy-snapshot:mission-pr7",
        localFakeTransitionAllowed=True,
        approvalRequired=True,
        evidenceRequired=True,
    )

    missing_policy = machine.transition(request=request, policy=None)
    missing_evidence = machine.transition(
        request=request.model_copy(update={"evidenceRefs": ()}),
        policy=policy,
    )
    missing_approval = machine.transition(request=request, policy=policy)

    assert missing_policy.status == "blocked"
    assert missing_policy.reason_codes == ("missing_mission_lifecycle_policy",)
    assert missing_evidence.status == "blocked"
    assert missing_evidence.reason_codes == ("missing_mission_transition_evidence",)
    assert missing_approval.status == "approval_required"
    assert missing_approval.reason_codes == ("missing_mission_transition_approval",)


def test_authority_flags_cannot_be_forged_by_construct_copy_or_payload() -> None:
    from openmagi_core_agent.missions.lifecycle import (
        MissionLifecycleConfig,
        MissionTransitionResult,
        MissionLifecycleStateMachine,
    )
    from openmagi_core_agent.missions.receipts import (
        MissionLifecycleAuthorityFlags,
        MissionTransitionReceipt,
        sha256_ref,
    )

    constructed_config = MissionLifecycleConfig.model_construct(
        enabled=True,
        productionMutationEnabled=True,
        schedulerAttached=True,
        cronMutationEnabled=True,
        backgroundExecutionEnabled=True,
        toolHostDispatchEnabled=True,
        channelDeliveryEnabled=True,
        workspaceMutationEnabled=True,
        memoryMutationEnabled=True,
        trafficAttached=True,
    )
    copied_config = MissionLifecycleConfig().model_copy(
        update={
            "enabled": True,
            "productionMutationEnabled": True,
            "schedulerAttached": True,
        },
    )
    forged_flags = MissionLifecycleAuthorityFlags.model_construct(
        productionMutationEnabled=True,
        schedulerAttached=True,
        cronMutationEnabled=True,
        backgroundExecutionEnabled=True,
        toolHostDispatchEnabled=True,
        channelDeliveryEnabled=True,
        workspaceMutationEnabled=True,
        memoryMutationEnabled=True,
        trafficAttached=True,
    )
    receipt = MissionTransitionReceipt(
        receiptId="mission-transition:forge",
        receiptDigest=sha256_ref("receipt"),
        missionId="mission:forge",
        runId="run:forge",
        turnId="turn:forge",
        fromState="running",
        toState="paused",
        status="blocked",
        transitionAllowed=False,
        policySnapshotDigest=sha256_ref("policy"),
        policySnapshotRef="policy-snapshot:forge",
        reasonCodes=("mission_transition_denied",),
        reasonDigest=sha256_ref("reason"),
    )
    result = MissionTransitionResult(
        status="blocked",
        reasonCodes=("mission_transition_denied",),
        receipt=receipt,
        policySnapshotDigest=sha256_ref("policy"),
    )
    forged_result_copy = result.model_copy(
        update={
            "authority_flags": {"productionMutationEnabled": True},
            "authorityFlags": {"productionMutationEnabled": True},
        },
    )
    forged_result_construct = MissionTransitionResult.model_construct(
        status="blocked",
        reasonCodes=("mission_transition_denied",),
        receipt=receipt,
        policySnapshotDigest=sha256_ref("policy"),
        authorityFlags={"productionMutationEnabled": True},
    )

    assert constructed_config.enabled is True
    assert constructed_config.local_fake_transition_enabled is False
    assert set(constructed_config.authority_flags().model_dump(by_alias=True).values()) == {False}
    assert copied_config.enabled is True
    assert set(copied_config.authority_flags().model_dump(by_alias=True).values()) == {False}
    assert set(forged_flags.model_dump(by_alias=True).values()) == {False}
    assert set(MissionLifecycleStateMachine(copied_config).config.authority_flags().model_dump(by_alias=True).values()) == {
        False
    }
    assert set(forged_result_copy.authority_flags.model_dump(by_alias=True).values()) == {False}
    assert set(forged_result_construct.authority_flags.model_dump(by_alias=True).values()) == {
        False
    }
    assert set(forged_result_copy.public_projection()["authorityFlags"].values()) == {False}
    assert set(forged_result_construct.public_projection()["authorityFlags"].values()) == {False}


def test_receipt_and_projection_redact_raw_private_mission_payloads() -> None:
    from openmagi_core_agent.missions.lifecycle import (
        MissionLifecycleConfig,
        MissionLifecyclePolicy,
        MissionLifecycleStateMachine,
        MissionTransitionRequest,
    )

    result = MissionLifecycleStateMachine(
        MissionLifecycleConfig(enabled=True, localFakeTransitionEnabled=True),
    ).transition(
        request=MissionTransitionRequest(
            missionId="mission:raw-policy-snapshot-text-must-not-appear",
            runId="run-raw-source-text-must-not-appear",
            turnId="turn-raw-control-metadata-must-not-appear",
            fromState="running",
            toState="paused",
            evidenceRefs=(
                "evidence:raw-source-text-must-not-appear",
                "memory:raw-policy-snapshot-text-must-not-appear",
            ),
            approvalRef="approval:raw-output-text-must-not-appear",
            reason="Use /Users/kevin/private and token ghp_missionSecret",
            rawPrompt="raw prompt hidden reasoning",
            rawOutput="raw output tool log",
            toolLogs="tool log secret",
            childPrompt="child prompt secret",
            privateMissionPayload=True,
        ),
        policy=MissionLifecyclePolicy(
            policyRef="policy:raw-config-payload-must-not-appear",
            policySnapshotRef="policy-snapshot:raw-policy-snapshot-text-must-not-appear",
            localFakeTransitionAllowed=True,
        ),
    )

    assert result.status == "blocked"
    assert "private_mission_payload_denied" in result.reason_codes
    encoded = json.dumps(
        [
            result.public_projection(),
            result.model_dump(by_alias=True, mode="json"),
        ],
        sort_keys=True,
    )
    forbidden = (
        "raw-policy-snapshot-text-must-not-appear",
        "raw-source-text-must-not-appear",
        "raw-control-metadata-must-not-appear",
        "raw-output-text-must-not-appear",
        "/Users/kevin",
        "ghp_missionSecret",
        "hidden reasoning",
        "tool log secret",
        "child prompt secret",
    )
    for fragment in forbidden:
        assert fragment not in encoded
    assert "sha256:" in encoded
    assert set(result.receipt.authority_flags.model_dump(by_alias=True).values()) == {False}


def test_direct_receipt_construction_sanitizes_unsafe_reason_codes() -> None:
    from openmagi_core_agent.missions.receipts import MissionTransitionReceipt, sha256_ref

    receipt = MissionTransitionReceipt(
        receiptId="mission-transition:reason",
        receiptDigest=sha256_ref("receipt"),
        missionId="mission:reason",
        runId="run:reason",
        turnId="turn:reason",
        fromState="running",
        toState="paused",
        status="blocked",
        transitionAllowed=False,
        policySnapshotDigest=sha256_ref("policy"),
        policySnapshotRef="policy-snapshot:reason",
        reasonCodes=(
            "raw-output-text-must-not-appear",
            "private-mission-payload-must-not-appear",
        ),
        reasonDigest=sha256_ref("reason"),
    )
    encoded = json.dumps(
        [
            receipt.public_projection(),
            receipt.model_dump(by_alias=True, mode="json"),
        ],
        sort_keys=True,
    )

    assert "raw-output-text-must-not-appear" not in encoded
    assert "private-mission-payload-must-not-appear" not in encoded
    assert receipt.reason_codes == (
        "mission_lifecycle_reason",
        "mission_lifecycle_reason",
    )


def test_mission_lifecycle_modules_have_no_live_imports() -> None:
    for module_name in (
        "openmagi_core_agent.missions.lifecycle",
        "openmagi_core_agent.missions.receipts",
    ):
        importlib.import_module(module_name)

    import subprocess

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

for module_name in (
    "openmagi_core_agent.missions.lifecycle",
    "openmagi_core_agent.missions.receipts",
):
    importlib.import_module(module_name)

forbidden = (
    "google.adk.runners",
    "google.adk.agents",
    "openmagi_core_agent.runtime.runner",
    "openmagi_core_agent.tools.host",
    "subprocess",
    "telegram",
    "requests",
    "httpx",
)
loaded = [name for name in forbidden if name in sys.modules]
if loaded:
    raise AssertionError(f"forbidden modules loaded: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
