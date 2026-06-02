from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json

import pytest
from pydantic import ValidationError

from openmagi_core_agent.runtime.heartbeat_contract import (
    ActivityReceipt,
    HeartbeatReceipt,
    ResumeDecision,
    RunLease,
    StaleRunVerdict,
    activity_receipt_digest,
    heartbeat_receipt_digest,
)


def _digest(character: str) -> str:
    return "sha256:" + character * 64


def _heartbeat_digest(character: str) -> str:
    return "sha256:heartbeat:" + character * 64


def _activity_digest(character: str) -> str:
    return "sha256:activity:" + character * 64


def _digest_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()


def _lease_payload() -> dict[str, object]:
    return {
        "runId": "run:alpha",
        "turnId": "turn:001",
        "sessionKey": "sess:alpha",
        "workerId": "worker:west-1",
        "leaseId": "lease:primary",
        "leaseAcquiredAt": "2026-05-27T12:00:00Z",
        "leaseExpiresAt": "2026-05-27T12:05:00Z",
        "phase": "running",
        "activeBoundary": "turn-controller",
        "authorityScope": "runtime-contract-default-off",
        "generation": 3,
        "fencingToken": _digest("f"),
    }


def _heartbeat_payload(*, sequence: int = 1, digest: str | None = None) -> dict[str, object]:
    payload: dict[str, object] = {
        "schemaVersion": "openmagi.runtime.heartbeat.receipt.v1",
        "heartbeatId": "heartbeat:001",
        "runId": "run:alpha",
        "leaseId": "lease:primary",
        "sequence": sequence,
        "emittedAt": "2026-05-27T12:01:00Z",
        "lastActivityAt": "2026-05-27T12:00:30Z",
        "lastActivityReceiptDigest": _activity_digest("c"),
        "lastEventId": "event:stream-42",
        "lastReceiptId": "activity:001",
        "phase": "running",
        "activeToolName": "heartbeat_contract_check",
        "activeChildId": "child:research-1",
        "pendingApprovalIds": ["approval:read-only"],
        "publicSafe": True,
    }
    payload["digest"] = digest or heartbeat_receipt_digest(payload)
    return payload


def _activity_payload(*, digest: str | None = None) -> dict[str, object]:
    payload: dict[str, object] = {
        "schemaVersion": "openmagi.runtime.activity.receipt.v1",
        "activityId": "activity:001",
        "runId": "run:alpha",
        "leaseId": "lease:primary",
        "sequence": 2,
        "emittedAt": "2026-05-27T12:01:10Z",
        "activityType": "tool_event",
        "activityRef": "event:stream-42",
        "metadata": {
            "eventDigest": _digest("a"),
            "source": "runtime",
            "count": 1,
            "nested": {
                "activityRef": "event:stream-42",
                "refs": ["approval:read-only"],
            },
        },
        "publicSafe": True,
    }
    payload["digest"] = digest or activity_receipt_digest(payload)
    return payload


def test_runtime_heartbeat_contract_accepts_normal_records_and_values() -> None:
    lease = RunLease.model_validate(_lease_payload())
    heartbeat = HeartbeatReceipt.model_validate(_heartbeat_payload())
    activity = ActivityReceipt.model_validate(_activity_payload())

    assert lease.run_id == "run:alpha"
    assert heartbeat.pending_approval_ids == ("approval:read-only",)
    assert activity.metadata["source"] == "runtime"
    assert activity.metadata["nested"]["refs"] == ("approval:read-only",)
    assert heartbeat.digest == _heartbeat_payload()["digest"]
    assert activity.digest == _activity_payload()["digest"]

    with pytest.raises(ValidationError, match="frozen"):
        lease.generation = 4  # type: ignore[misc]
    with pytest.raises(ValueError, match="model_copy update"):
        heartbeat.model_copy(update={"sequence": 99})

    for verdict in (
        "healthy",
        "silent_but_within_threshold",
        "inactive_timeout",
        "lease_expired",
        "worker_lost",
        "rollback_required",
        "resume_pending",
        "cancelled",
        "blocked_for_operator",
    ):
        assert StaleRunVerdict(
            verdict=verdict,
            runId="run:alpha",
            checkedAt=datetime(2026, 5, 27, 12, 2, tzinfo=UTC),
            reasonCodes=("heartbeat_contract_check",),
            metadata={"source": "watchdog"},
        ).verdict == verdict

    for decision in (
        "resume_same_session",
        "resume_with_system_note",
        "retry_from_checkpoint",
        "cancel_and_project_failure",
        "block_for_operator",
        "ignore_completed",
    ):
        assert ResumeDecision(
            decision=decision,
            runId="run:alpha",
            decidedAt=datetime(2026, 5, 27, 12, 3, tzinfo=UTC),
            reasonCodes=("heartbeat_contract_check",),
        ).decision == decision


def test_runtime_heartbeat_contract_rejects_forged_or_mutated_records() -> None:
    forged = _heartbeat_payload()
    forged["sequence"] = 99
    with pytest.raises(ValidationError, match="digest"):
        HeartbeatReceipt.model_validate(forged)

    with pytest.raises(ValidationError, match="sha256"):
        ActivityReceipt.model_validate(_activity_payload(digest="not-a-digest"))

    heartbeat_digest_as_activity = _heartbeat_payload()
    heartbeat_digest_as_activity["lastActivityReceiptDigest"] = _heartbeat_digest("b")
    heartbeat_digest_as_activity["digest"] = heartbeat_receipt_digest(heartbeat_digest_as_activity)
    with pytest.raises(ValidationError, match="runtime contract validation failed"):
        HeartbeatReceipt.model_validate(heartbeat_digest_as_activity)

    generic_digest_as_activity = _heartbeat_payload()
    generic_digest_as_activity["lastActivityReceiptDigest"] = _digest("c")
    generic_digest_as_activity["digest"] = heartbeat_receipt_digest(generic_digest_as_activity)
    with pytest.raises(ValidationError, match="runtime contract validation failed"):
        HeartbeatReceipt.model_validate(generic_digest_as_activity)

    lease = RunLease.model_validate(_lease_payload())
    with pytest.raises(ValueError, match="model_copy update"):
        lease.model_copy(update={"leaseId": "lease:other"})

    activity = ActivityReceipt.model_validate(_activity_payload())
    with pytest.raises(ValueError, match="model_copy update"):
        activity.model_copy(update={"digest": _digest("b")})

    missing_activity_digest = _heartbeat_payload()
    missing_activity_digest.pop("lastActivityReceiptDigest")
    missing_activity_digest["digest"] = heartbeat_receipt_digest(missing_activity_digest)
    with pytest.raises(ValidationError, match="runtime contract validation failed"):
        HeartbeatReceipt.model_validate(missing_activity_digest)

    omitted_heartbeat_public_safe = _heartbeat_payload()
    omitted_heartbeat_public_safe.pop("publicSafe")
    omitted_heartbeat_public_safe["digest"] = heartbeat_receipt_digest(omitted_heartbeat_public_safe)
    with pytest.raises(ValidationError, match="runtime contract validation failed"):
        HeartbeatReceipt.model_validate(omitted_heartbeat_public_safe)

    omitted_activity_public_safe = _activity_payload()
    omitted_activity_public_safe.pop("publicSafe")
    omitted_activity_public_safe["digest"] = activity_receipt_digest(omitted_activity_public_safe)
    with pytest.raises(ValidationError, match="runtime contract validation failed"):
        ActivityReceipt.model_validate(omitted_activity_public_safe)

    false_activity_public_safe = _activity_payload()
    false_activity_public_safe["publicSafe"] = False
    false_activity_public_safe["digest"] = activity_receipt_digest(false_activity_public_safe)
    with pytest.raises(ValidationError, match="runtime contract validation failed"):
        ActivityReceipt.model_validate(false_activity_public_safe)

    snake_public_safe_heartbeat = _heartbeat_payload()
    snake_public_safe_heartbeat["public_safe"] = True
    snake_public_safe_heartbeat.pop("publicSafe")
    snake_public_safe_heartbeat["digest"] = heartbeat_receipt_digest(snake_public_safe_heartbeat)
    with pytest.raises(ValidationError, match="runtime contract validation failed"):
        HeartbeatReceipt.model_validate(snake_public_safe_heartbeat)

    snake_public_safe_activity = _activity_payload()
    snake_public_safe_activity["public_safe"] = True
    snake_public_safe_activity.pop("publicSafe")
    snake_public_safe_activity["digest"] = activity_receipt_digest(snake_public_safe_activity)
    with pytest.raises(ValidationError, match="runtime contract validation failed"):
        ActivityReceipt.model_validate(snake_public_safe_activity)

    snake_activity_digest = _heartbeat_payload()
    snake_activity_digest["last_activity_receipt_digest"] = _activity_digest("c")
    snake_activity_digest.pop("lastActivityReceiptDigest")
    snake_activity_digest["digest"] = heartbeat_receipt_digest(snake_activity_digest)
    with pytest.raises(ValidationError, match="runtime contract validation failed"):
        HeartbeatReceipt.model_validate(snake_activity_digest)


def test_metadata_is_immutable_after_validation() -> None:
    activity = ActivityReceipt.model_validate(_activity_payload())
    original_digest = activity.digest

    with pytest.raises(TypeError):
        activity.metadata["source"] = "other"
    with pytest.raises(TypeError):
        activity.metadata["nested"]["activityRef"] = "event:other"
    assert activity.digest == original_digest
    assert activity.public_projection()["digest"] == original_digest

    stale = StaleRunVerdict(
        verdict="healthy",
        runId="run:alpha",
        checkedAt=datetime(2026, 5, 27, 12, 2, tzinfo=UTC),
        metadata={"source": "watchdog", "nested": {"activityRef": "event:stream-42"}},
    )
    resume = ResumeDecision(
        decision="resume_same_session",
        runId="run:alpha",
        decidedAt=datetime(2026, 5, 27, 12, 3, tzinfo=UTC),
        metadata={"source": "resume", "nested": {"checkpoint": "checkpoint:001"}},
    )
    with pytest.raises(TypeError):
        stale.metadata["source"] = "other"
    with pytest.raises(TypeError):
        stale.metadata["nested"]["activityRef"] = "event:other"
    with pytest.raises(TypeError):
        resume.metadata["source"] = "other"
    with pytest.raises(TypeError):
        resume.metadata["nested"]["checkpoint"] = "checkpoint:002"


def test_runtime_heartbeat_contract_rejects_unsafe_public_fields_and_metadata() -> None:
    unsafe_lease = _lease_payload()
    unsafe_lease["workerId"] = "Authorization: Bearer sk-test-value"
    with pytest.raises(ValidationError, match="runtime contract validation failed"):
        RunLease.model_validate(unsafe_lease)

    unsafe_heartbeat = _heartbeat_payload()
    unsafe_heartbeat["activeToolName"] = "/Users/kevin/.ssh/id_rsa"
    unsafe_heartbeat["digest"] = heartbeat_receipt_digest(unsafe_heartbeat)
    with pytest.raises(ValidationError, match="runtime contract validation failed"):
        HeartbeatReceipt.model_validate(unsafe_heartbeat)

    not_public_safe = _heartbeat_payload()
    not_public_safe["publicSafe"] = False
    not_public_safe["digest"] = heartbeat_receipt_digest(not_public_safe)
    with pytest.raises(ValidationError, match="runtime contract validation failed"):
        HeartbeatReceipt.model_validate(not_public_safe)

    unsafe_metadata = _activity_payload()
    unsafe_metadata["metadata"] = {"rawPrompt": "raw prompt: read /data/bots/bot-1/state.json"}
    unsafe_metadata["digest"] = activity_receipt_digest(unsafe_metadata)
    with pytest.raises(ValidationError, match="runtime contract validation failed"):
        ActivityReceipt.model_validate(unsafe_metadata)

    unsafe_activity_ref = _activity_payload()
    unsafe_activity_ref["activityRef"] = "hidden chain-of-thought"
    unsafe_activity_ref["digest"] = activity_receipt_digest(unsafe_activity_ref)
    with pytest.raises(ValidationError, match="runtime contract validation failed"):
        ActivityReceipt.model_validate(unsafe_activity_ref)

    with pytest.raises(ValidationError, match="runtime contract validation failed"):
        StaleRunVerdict(
            verdict="blocked_for_operator",
            runId="run:alpha",
            checkedAt=datetime(2026, 5, 27, 12, 2, tzinfo=UTC),
            metadata={"rawOutput": "raw output from /Users/kevin/private.txt"},
        )

    with pytest.raises(ValidationError, match="runtime contract validation failed"):
        ResumeDecision(
            decision="block_for_operator",
            runId="run:alpha",
            decidedAt=datetime(2026, 5, 27, 12, 3, tzinfo=UTC),
            reasonCodes={"reason": "heartbeat_contract_check"},
        )


def test_runtime_heartbeat_contract_rejects_authority_shaped_public_labels_and_ids() -> None:
    lease_phase = _lease_payload()
    lease_phase["phase"] = "provider:openai"
    with pytest.raises(ValidationError, match="runtime contract validation failed"):
        RunLease.model_validate(lease_phase)

    heartbeat_phase = _heartbeat_payload()
    heartbeat_phase["phase"] = "browser.open"
    heartbeat_phase["digest"] = heartbeat_receipt_digest(heartbeat_phase)
    with pytest.raises(ValidationError, match="runtime contract validation failed"):
        HeartbeatReceipt.model_validate(heartbeat_phase)

    heartbeat_event = _heartbeat_payload()
    heartbeat_event["lastEventId"] = "db.write"
    heartbeat_event["digest"] = heartbeat_receipt_digest(heartbeat_event)
    with pytest.raises(ValidationError, match="runtime contract validation failed"):
        HeartbeatReceipt.model_validate(heartbeat_event)

    heartbeat_approval = _heartbeat_payload()
    heartbeat_approval["pendingApprovalIds"] = ["workspace.write"]
    heartbeat_approval["digest"] = heartbeat_receipt_digest(heartbeat_approval)
    with pytest.raises(ValidationError, match="runtime contract validation failed"):
        HeartbeatReceipt.model_validate(heartbeat_approval)

    lease_prefixed_authority = _lease_payload()
    lease_prefixed_authority["runId"] = "run:provider:openai"
    with pytest.raises(ValidationError, match="runtime contract validation failed"):
        RunLease.model_validate(lease_prefixed_authority)

    for prefixed_authority_id in ("run:provider-openai", "run:provider_openai"):
        lease_prefixed_authority = _lease_payload()
        lease_prefixed_authority["runId"] = prefixed_authority_id
        with pytest.raises(ValidationError, match="runtime contract validation failed"):
            RunLease.model_validate(lease_prefixed_authority)

    for prefixed_activation_id in (
        "run:activationRequired",
        "run:liveAuthority",
        "run:trafficAttached",
        "run:schedulerAttached",
        "run:modelCallEnabled",
        "run:channelDelivery",
    ):
        lease_prefixed_activation = _lease_payload()
        lease_prefixed_activation["runId"] = prefixed_activation_id
        with pytest.raises(ValidationError, match="runtime contract validation failed"):
            RunLease.model_validate(lease_prefixed_activation)

    heartbeat_prefixed_authority = _heartbeat_payload()
    heartbeat_prefixed_authority["heartbeatId"] = "heartbeat:provider:openai"
    heartbeat_prefixed_authority["digest"] = heartbeat_receipt_digest(heartbeat_prefixed_authority)
    with pytest.raises(ValidationError, match="runtime contract validation failed"):
        HeartbeatReceipt.model_validate(heartbeat_prefixed_authority)

    heartbeat_prefixed_authority = _heartbeat_payload()
    heartbeat_prefixed_authority["heartbeatId"] = "heartbeat:workspace-write"
    heartbeat_prefixed_authority["digest"] = heartbeat_receipt_digest(heartbeat_prefixed_authority)
    with pytest.raises(ValidationError, match="runtime contract validation failed"):
        HeartbeatReceipt.model_validate(heartbeat_prefixed_authority)

    activity_prefixed_authority = _activity_payload()
    activity_prefixed_authority["activityId"] = "activity:db:write"
    activity_prefixed_authority["digest"] = activity_receipt_digest(activity_prefixed_authority)
    with pytest.raises(ValidationError, match="runtime contract validation failed"):
        ActivityReceipt.model_validate(activity_prefixed_authority)

    activity_prefixed_authority = _activity_payload()
    activity_prefixed_authority["activityId"] = "activity:db_write"
    activity_prefixed_authority["digest"] = activity_receipt_digest(activity_prefixed_authority)
    with pytest.raises(ValidationError, match="runtime contract validation failed"):
        ActivityReceipt.model_validate(activity_prefixed_authority)

    for field_name, unsafe_value in (
        ("activeBoundary", "db.write"),
        ("activeBoundary", "browser:open"),
        ("authorityScope", "provider:openai"),
        ("authorityScope", "permission.workspace"),
        ("authorityScope", "liveAuthority.true"),
        ("authorityScope", "scheduler.tick"),
        ("authorityScope", "activation.required"),
        ("authorityScope", "traffic.attached"),
        ("authorityScope", "channel.delivery"),
        ("authorityScope", "model.call"),
    ):
        unsafe_lease = _lease_payload()
        unsafe_lease[field_name] = unsafe_value
        with pytest.raises(ValidationError, match="runtime contract validation failed"):
            RunLease.model_validate(unsafe_lease)

    for unsafe_tool_name in ("browser.open", "tool.browser"):
        unsafe_heartbeat_tool = _heartbeat_payload()
        unsafe_heartbeat_tool["activeToolName"] = unsafe_tool_name
        unsafe_heartbeat_tool["digest"] = heartbeat_receipt_digest(unsafe_heartbeat_tool)
        with pytest.raises(ValidationError, match="runtime contract validation failed"):
            HeartbeatReceipt.model_validate(unsafe_heartbeat_tool)

    activity_authority_id = _activity_payload()
    activity_authority_id["activityId"] = "provider:openai"
    activity_authority_id["digest"] = activity_receipt_digest(activity_authority_id)
    with pytest.raises(ValidationError, match="runtime contract validation failed"):
        ActivityReceipt.model_validate(activity_authority_id)

    for field_name, unsafe_value in (
        ("activityType", "workspace.write"),
        ("activityType", "workspace:write"),
        ("activityType", "capability.workspace"),
        ("activityRef", "memory.write"),
        ("activityRef", "memory:write"),
        ("activityRef", "mission:run"),
    ):
        unsafe_activity = _activity_payload()
        unsafe_activity[field_name] = unsafe_value
        unsafe_activity["digest"] = activity_receipt_digest(unsafe_activity)
        with pytest.raises(ValidationError, match="runtime contract validation failed"):
            ActivityReceipt.model_validate(unsafe_activity)

    activity_wrong_run_id = _activity_payload()
    activity_wrong_run_id["runId"] = "activity:run-shaped-wrong"
    activity_wrong_run_id["digest"] = activity_receipt_digest(activity_wrong_run_id)
    with pytest.raises(ValidationError, match="runtime contract validation failed"):
        ActivityReceipt.model_validate(activity_wrong_run_id)

    activity_wrong_lease_id = _activity_payload()
    activity_wrong_lease_id["leaseId"] = "run:lease-shaped-wrong"
    activity_wrong_lease_id["digest"] = activity_receipt_digest(activity_wrong_lease_id)
    with pytest.raises(ValidationError, match="runtime contract validation failed"):
        ActivityReceipt.model_validate(activity_wrong_lease_id)

    with pytest.raises(ValidationError, match="runtime contract validation failed"):
        StaleRunVerdict(
            verdict="healthy",
            runId="provider:openai",
            checkedAt=datetime(2026, 5, 27, 12, 2, tzinfo=UTC),
        )

    with pytest.raises(ValidationError, match="runtime contract validation failed"):
        StaleRunVerdict(
            verdict="healthy",
            runId="activity:alpha",
            checkedAt=datetime(2026, 5, 27, 12, 2, tzinfo=UTC),
        )

    with pytest.raises(ValidationError, match="runtime contract validation failed"):
        ResumeDecision(
            decision="resume_same_session",
            runId="provider:openai",
            decidedAt=datetime(2026, 5, 27, 12, 3, tzinfo=UTC),
        )

    with pytest.raises(ValidationError, match="runtime contract validation failed"):
        ResumeDecision(
            decision="resume_same_session",
            runId="lease:alpha",
            decidedAt=datetime(2026, 5, 27, 12, 3, tzinfo=UTC),
        )

    for reason_code in (
        "mission.run",
        "mission:run",
        "provider:openai",
        "tool.browser",
        "capability.workspace",
        "permission.workspace",
    ):
        with pytest.raises(ValidationError, match="runtime contract validation failed"):
            StaleRunVerdict(
                verdict="healthy",
                runId="run:alpha",
                checkedAt=datetime(2026, 5, 27, 12, 2, tzinfo=UTC),
                reasonCodes=(reason_code,),
            )
        with pytest.raises(ValidationError, match="runtime contract validation failed"):
            ResumeDecision(
                decision="resume_same_session",
                runId="run:alpha",
                decidedAt=datetime(2026, 5, 27, 12, 3, tzinfo=UTC),
                reasonCodes=(reason_code,),
            )


@pytest.mark.parametrize(
    "metadata",
    (
        {"provider": "runtime"},
        {"nested": {"tool": "browser.open"}},
        {"source": "provider:openai"},
        {"source": "db.write"},
        {"source": "workspace.write"},
        {"source": "memory.write"},
        {"source": "mission.run"},
        {"nested": {"capability": "workspace.write"}},
        {"source": "Provider:openai"},
        {"source": "provider.openai"},
        {"source": "tool:browser.open"},
        {"source": "capability:workspace.write"},
        {"source": "permission:workspace.write"},
        {"source": "browser:open"},
        {"source": "db:write"},
        {"source": "workspace:write"},
        {"source": "memory:write"},
        {"source": "mission:run"},
        {"source": "tool.browser"},
        {"source": "capability.workspace"},
        {"source": "permission.workspace"},
        {"source": "Browser.open"},
        {"source": "DB.write"},
    ),
)
def test_runtime_heartbeat_contract_rejects_authority_shaped_metadata(
    metadata: dict[str, object],
) -> None:
    unsafe_metadata = _activity_payload()
    unsafe_metadata["metadata"] = metadata
    unsafe_metadata["digest"] = activity_receipt_digest(unsafe_metadata)
    with pytest.raises(ValidationError, match="runtime contract validation failed"):
        ActivityReceipt.model_validate(unsafe_metadata)


def test_runtime_heartbeat_public_projections_digest_phase_labels() -> None:
    unsafe_lease_payload = _lease_payload()
    unsafe_lease_payload["phase"] = "provider_openai"
    with pytest.raises(ValidationError, match="runtime contract validation failed"):
        RunLease.model_validate(unsafe_lease_payload)

    unsafe_heartbeat_payload = _heartbeat_payload()
    unsafe_heartbeat_payload["phase"] = "provider_openai"
    unsafe_heartbeat_payload["digest"] = heartbeat_receipt_digest(unsafe_heartbeat_payload)
    with pytest.raises(ValidationError, match="runtime contract validation failed"):
        HeartbeatReceipt.model_validate(unsafe_heartbeat_payload)

    projections = (
        RunLease.model_validate(_lease_payload()).public_projection(),
        HeartbeatReceipt.model_validate(_heartbeat_payload()).public_projection(),
    )

    encoded = json.dumps(projections, sort_keys=True)
    assert "phaseDigest" in encoded
    assert '"phase":' not in encoded
    assert "running" not in encoded


@pytest.mark.parametrize(
    "metadata",
    (
        {"providerName": "runtime"},
        {"provider_id": "runtime"},
        {"providerRef": "runtime"},
        {"toolName": "runtime"},
        {"capabilityScope": "runtime"},
        {"permissionGrant": "runtime"},
        {"provider_name_ref": "runtime"},
        {"browserTool": "runtime"},
        {"trafficAttached": True},
        {"executionAttached": False},
        {"routeAttached": True},
        {"schedulerAttached": True},
        {"scriptName": "deploy"},
        {"watchdogScript": "deploy"},
        {"channelDelivery": True},
        {"dbWriteEnabled": True},
        {"workspaceMutation": "enabled"},
        {"providerNames": "runtime"},
        {"providerRefs": ["runtime"]},
        {"activationRequired": True},
        {"activationEnabled": True},
        {"runtimeActivation": True},
        {"missionRuntimeEnabled": True},
        {"liveSchedulerEnabled": True},
        {"gate2Activation": True},
        {"gate8Activation": True},
        {"nested": {"providerName": "runtime"}},
        {"nested": {"provider_id": "runtime"}},
        {"nested": {"providerRef": "runtime"}},
        {"nested": {"toolName": "runtime"}},
        {"nested": {"capabilityScope": "runtime"}},
        {"nested": {"permissionGrant": "runtime"}},
        {"nested": {"provider_name_ref": "runtime"}},
        {"nested": {"browserTool": "runtime"}},
        {"nested": {"trafficAttached": True}},
        {"nested": {"executionAttached": False}},
        {"nested": {"routeAttached": True}},
        {"nested": {"schedulerAttached": True}},
        {"nested": {"scriptName": "deploy"}},
        {"nested": {"watchdogScript": "deploy"}},
        {"nested": {"channelDelivery": True}},
        {"nested": {"dbWriteEnabled": True}},
        {"nested": {"workspaceMutation": "enabled"}},
        {"nested": {"activationRequired": True}},
        {"nested": {"activationEnabled": True}},
        {"nested": {"runtimeActivation": True}},
        {"nested": {"missionRuntimeEnabled": True}},
        {"nested": {"liveSchedulerEnabled": True}},
        {"nested": {"gate2Activation": True}},
        {"nested": {"gate8Activation": True}},
        {"providerReference": "runtime"},
        {"toolExecution": "runtime"},
        {"toolCallEnabled": True},
        {"capabilityGrant": "runtime"},
        {"permissionScope": "runtime"},
        {"browserAutomation": "runtime"},
        {"dbWrite": True},
        {"workspaceWrite": True},
        {"memoryWrite": True},
        {"missionRuntime": True},
        {"activationPolicy": "runtime"},
        {"trafficAttachment": True},
        {"schedulerTick": "runtime"},
        {"channelDeliveryPolicy": "runtime"},
        {"modelCall": "runtime"},
        {"modelProvider": "runtime"},
        {"wakeAgentPolicy": "runtime"},
        {"k8sPatch": "runtime"},
        {"envPatch": "runtime"},
        {"nested": {"providerReference": "runtime"}},
        {"nested": {"toolExecution": "runtime"}},
        {"nested": {"toolCallEnabled": True}},
        {"nested": {"capabilityGrant": "runtime"}},
        {"nested": {"permissionScope": "runtime"}},
        {"nested": {"browserAutomation": "runtime"}},
        {"nested": {"dbWrite": True}},
        {"nested": {"workspaceWrite": True}},
        {"nested": {"memoryWrite": True}},
        {"nested": {"missionRuntime": True}},
        {"nested": {"activationPolicy": "runtime"}},
        {"nested": {"trafficAttachment": True}},
        {"nested": {"schedulerTick": "runtime"}},
        {"nested": {"channelDeliveryPolicy": "runtime"}},
        {"nested": {"modelCall": "runtime"}},
        {"nested": {"modelProvider": "runtime"}},
        {"nested": {"wakeAgentPolicy": "runtime"}},
        {"nested": {"k8sPatch": "runtime"}},
        {"nested": {"envPatch": "runtime"}},
    ),
)
def test_runtime_heartbeat_contract_rejects_authority_metadata_key_variants(
    metadata: dict[str, object],
) -> None:
    unsafe_metadata = _activity_payload()
    unsafe_metadata["metadata"] = metadata
    unsafe_metadata["digest"] = activity_receipt_digest(unsafe_metadata)
    with pytest.raises(ValidationError, match="runtime contract validation failed"):
        ActivityReceipt.model_validate(unsafe_metadata)


@pytest.mark.parametrize(
    ("activity_type", "activity_ref"),
    (
        ("heartbeat", "event:stream-42"),
        ("heartbeat_event", "event:stream-42"),
        ("heartbeat_receipt", "event:stream-42"),
        ("Heartbeat", "event:stream-42"),
        ("HEARTBEAT", "event:stream-42"),
        ("heartbeat.event", "event:stream-42"),
        ("heartbeat-receipt", "event:stream-42"),
        ("heartbeatReceipt", "event:stream-42"),
        ("tool_event", "heartbeat:001"),
        ("tool_event", "Heartbeat:001"),
        ("tool_event", "heartbeat.001"),
    ),
)
def test_runtime_heartbeat_contract_rejects_heartbeat_activity_receipts(
    activity_type: str,
    activity_ref: str,
) -> None:
    heartbeat_activity = _activity_payload()
    heartbeat_activity["activityType"] = activity_type
    heartbeat_activity["activityRef"] = activity_ref
    heartbeat_activity["digest"] = activity_receipt_digest(heartbeat_activity)

    with pytest.raises(ValidationError, match="runtime contract validation failed"):
        ActivityReceipt.model_validate(heartbeat_activity)


@pytest.mark.parametrize(
    "metadata_value",
    (
        "providerOpenAI",
        "provider-openai",
        "provider_openai",
        "browserOpen",
        "browser-open",
        "dbWrite",
        "workspaceWrite",
        "memoryWrite",
        "missionRun",
        "liveAuthority.true",
        "scheduler.tick",
        "activation.required",
        "traffic.attached",
        "channel.delivery",
        "model.call",
    ),
)
def test_runtime_heartbeat_contract_rejects_authority_metadata_value_variants(
    metadata_value: str,
) -> None:
    unsafe_metadata = _activity_payload()
    unsafe_metadata["metadata"] = {"source": metadata_value}
    unsafe_metadata["digest"] = activity_receipt_digest(unsafe_metadata)

    with pytest.raises(ValidationError, match="runtime contract validation failed"):
        ActivityReceipt.model_validate(unsafe_metadata)


def test_runtime_heartbeat_contract_rejects_boolean_numeric_fields() -> None:
    boolean_generation = _lease_payload()
    boolean_generation["generation"] = True
    with pytest.raises(ValidationError, match="runtime contract validation failed"):
        RunLease.model_validate(boolean_generation)

    boolean_heartbeat_sequence = _heartbeat_payload()
    boolean_heartbeat_sequence["sequence"] = True
    boolean_heartbeat_sequence["digest"] = heartbeat_receipt_digest(boolean_heartbeat_sequence)
    with pytest.raises(ValidationError, match="runtime contract validation failed"):
        HeartbeatReceipt.model_validate(boolean_heartbeat_sequence)

    boolean_activity_sequence = _activity_payload()
    boolean_activity_sequence["sequence"] = True
    boolean_activity_sequence["digest"] = activity_receipt_digest(boolean_activity_sequence)
    with pytest.raises(ValidationError, match="runtime contract validation failed"):
        ActivityReceipt.model_validate(boolean_activity_sequence)


def test_runtime_heartbeat_contract_preserves_safe_metadata_refs() -> None:
    safe_metadata = _activity_payload()
    safe_metadata["metadata"] = {
        "eventDigest": _digest("a"),
        "source": "runtime",
        "count": 1,
        "nested": {
            "activityRef": "event:stream-42",
            "checkpoint": "checkpoint:001",
            "refs": ["approval:read-only"],
        },
    }
    safe_metadata["digest"] = activity_receipt_digest(safe_metadata)

    activity = ActivityReceipt.model_validate(safe_metadata)

    assert activity.metadata["eventDigest"] == _digest("a")
    assert activity.metadata["source"] == "runtime"
    assert activity.metadata["count"] == 1
    assert activity.metadata["nested"]["activityRef"] == "event:stream-42"
    assert activity.metadata["nested"]["checkpoint"] == "checkpoint:001"
    assert activity.metadata["nested"]["refs"] == ("approval:read-only",)


def test_runtime_heartbeat_contract_rejects_naive_timestamps_and_invalid_ordering() -> None:
    naive_lease = _lease_payload()
    naive_lease["leaseAcquiredAt"] = "2026-05-27T12:00:00"
    with pytest.raises(ValidationError, match="runtime contract validation failed"):
        RunLease.model_validate(naive_lease)

    inverted_lease = _lease_payload()
    inverted_lease["leaseExpiresAt"] = "2026-05-27T11:59:59Z"
    with pytest.raises(ValidationError, match="runtime contract validation failed"):
        RunLease.model_validate(inverted_lease)

    late_activity = _heartbeat_payload()
    late_activity["lastActivityAt"] = "2026-05-27T12:01:01Z"
    late_activity["digest"] = heartbeat_receipt_digest(late_activity)
    with pytest.raises(ValidationError, match="runtime contract validation failed"):
        HeartbeatReceipt.model_validate(late_activity)

    same_time_activity = _heartbeat_payload()
    same_time_activity["lastActivityAt"] = same_time_activity["emittedAt"]
    same_time_activity["digest"] = heartbeat_receipt_digest(same_time_activity)
    with pytest.raises(ValidationError, match="runtime contract validation failed"):
        HeartbeatReceipt.model_validate(same_time_activity)

    naive_activity = _activity_payload()
    naive_activity["emittedAt"] = "2026-05-27T12:01:10"
    naive_activity["digest"] = activity_receipt_digest(naive_activity)
    with pytest.raises(ValidationError, match="runtime contract validation failed"):
        ActivityReceipt.model_validate(naive_activity)

    with pytest.raises(ValidationError, match="runtime contract validation failed"):
        StaleRunVerdict(
            verdict="healthy",
            runId="run:alpha",
            checkedAt=datetime(2026, 5, 27, 12, 2),
        )
    with pytest.raises(ValidationError, match="runtime contract validation failed"):
        ResumeDecision(
            decision="resume_same_session",
            runId="run:alpha",
            decidedAt=datetime(2026, 5, 27, 12, 3),
        )


def test_timestamp_public_projection_normalizes_offsets_to_utc() -> None:
    lease_payload = _lease_payload()
    lease_payload["leaseAcquiredAt"] = "2026-05-27T21:00:00+09:00"
    lease_payload["leaseExpiresAt"] = "2026-05-27T21:05:00+09:00"
    assert RunLease.model_validate(lease_payload).public_projection()["leaseExpiresAt"] == (
        "2026-05-27T12:05:00Z"
    )

    heartbeat_payload = _heartbeat_payload()
    heartbeat_payload["emittedAt"] = "2026-05-27T21:01:00+09:00"
    heartbeat_payload["lastActivityAt"] = "2026-05-27T21:00:30+09:00"
    heartbeat_payload["digest"] = heartbeat_receipt_digest(heartbeat_payload)
    heartbeat_projection = HeartbeatReceipt.model_validate(heartbeat_payload).public_projection()
    assert heartbeat_projection["emittedAt"] == "2026-05-27T12:01:00Z"
    assert heartbeat_projection["lastActivityAt"] == "2026-05-27T12:00:30Z"

    activity_payload = _activity_payload()
    activity_payload["emittedAt"] = "2026-05-27T21:01:10+09:00"
    activity_payload["digest"] = activity_receipt_digest(activity_payload)
    assert ActivityReceipt.model_validate(activity_payload).public_projection()["emittedAt"] == (
        "2026-05-27T12:01:10Z"
    )


def test_digest_helpers_reject_non_json_safe_datetime_objects() -> None:
    heartbeat_payload = _heartbeat_payload()
    heartbeat_payload.pop("digest")
    heartbeat_payload["emittedAt"] = datetime(2026, 5, 27, 12, 1, tzinfo=UTC)
    with pytest.raises(ValueError, match="JSON-safe"):
        heartbeat_receipt_digest(heartbeat_payload)

    activity_payload = _activity_payload()
    activity_payload.pop("digest")
    activity_payload["emittedAt"] = datetime(2026, 5, 27, 12, 1, 10, tzinfo=UTC)
    with pytest.raises(ValueError, match="JSON-safe"):
        activity_receipt_digest(activity_payload)


def test_digest_helpers_match_model_defaults_for_optional_fields() -> None:
    heartbeat_payload = {
        "heartbeatId": "heartbeat:defaults",
        "runId": "run:alpha",
        "leaseId": "lease:primary",
        "sequence": 5,
        "emittedAt": "2026-05-27T12:01:00Z",
        "lastActivityAt": "2026-05-27T12:00:30Z",
        "lastActivityReceiptDigest": _activity_digest("d"),
        "phase": "running",
        "publicSafe": True,
    }
    heartbeat = HeartbeatReceipt.model_validate(
        {**heartbeat_payload, "digest": heartbeat_receipt_digest(heartbeat_payload)}
    )
    assert heartbeat.last_event_id is None
    assert heartbeat.last_receipt_id is None
    assert heartbeat.pending_approval_ids == ()

    activity_payload = {
        "activityId": "activity:defaults",
        "runId": "run:alpha",
        "leaseId": "lease:primary",
        "sequence": 6,
        "emittedAt": "2026-05-27T12:01:10Z",
        "activityType": "tool_event",
        "activityRef": "event:stream-42",
        "publicSafe": True,
    }
    activity = ActivityReceipt.model_validate(
        {**activity_payload, "digest": activity_receipt_digest(activity_payload)}
    )
    assert activity.metadata == {}


def test_public_projections_are_deterministic_and_redact_private_runtime_ids() -> None:
    lease = RunLease.model_validate(_lease_payload())
    heartbeat = HeartbeatReceipt.model_validate(_heartbeat_payload())
    activity = ActivityReceipt.model_validate(_activity_payload())

    assert lease.public_projection() == {
        "schemaVersion": "openmagi.runtime.lease.public.v1",
        "runId": "run:alpha",
        "turnId": "turn:001",
        "leaseDigest": _digest_text("lease:primary"),
        "workerDigest": _digest_text("worker:west-1"),
        "leaseExpiresAt": "2026-05-27T12:05:00Z",
        "phaseDigest": _digest_text("running"),
        "activeBoundaryDigest": _digest_text("turn-controller"),
        "authorityScopeDigest": _digest_text("runtime-contract-default-off"),
        "generation": 3,
        "fencingTokenDigest": _digest("f"),
        "publicSafe": True,
        "liveAuthority": False,
        "trafficAttached": False,
        "trustedLeaseAuthority": False,
        "authorityProof": "requires_trusted_lease_store",
    }
    assert heartbeat.public_projection() == {
        "schemaVersion": "openmagi.runtime.heartbeat.receipt.public.v1",
        "heartbeatId": "heartbeat:001",
        "runId": "run:alpha",
        "leaseDigest": _digest_text("lease:primary"),
        "sequence": 1,
        "emittedAt": "2026-05-27T12:01:00Z",
        "lastActivityAt": "2026-05-27T12:00:30Z",
        "lastActivityReceiptDigest": _activity_digest("c"),
        "lastEventDigest": _digest_text("event:stream-42"),
        "phaseDigest": _digest_text("running"),
        "activeToolDigest": _digest_text("heartbeat_contract_check"),
        "activeChildDigest": _digest_text("child:research-1"),
        "pendingApprovalDigests": [_digest_text("approval:read-only")],
        "digest": heartbeat.digest,
        "publicSafe": True,
        "liveAuthority": False,
        "trafficAttached": False,
        "trustedLeaseAuthority": False,
        "authorityProof": "requires_trusted_lease_store",
    }
    assert activity.public_projection() == {
        "schemaVersion": "openmagi.runtime.activity.receipt.public.v1",
        "activityId": "activity:001",
        "runId": "run:alpha",
        "leaseDigest": _digest_text("lease:primary"),
        "sequence": 2,
        "emittedAt": "2026-05-27T12:01:10Z",
        "activityTypeDigest": _digest_text("tool_event"),
        "activityRefDigest": _digest_text("event:stream-42"),
        "digest": activity.digest,
        "publicSafe": True,
        "liveAuthority": False,
        "trafficAttached": False,
        "trustedLeaseAuthority": False,
        "authorityProof": "requires_trusted_lease_store",
    }

    encoded = json.dumps(
        {
            "lease": lease.public_projection(),
            "heartbeat": heartbeat.public_projection(),
            "activity": activity.public_projection(),
        },
        sort_keys=True,
    )
    assert "sessionKey" not in encoded
    assert "sess:alpha" not in encoded
    assert "workerId" not in encoded
    assert "worker:west-1" not in encoded
    assert "leaseId" not in encoded
    assert "lease:primary" not in encoded
    assert "lastReceiptId" not in encoded
    assert "lastEventId" not in encoded
    assert "event:stream-42" not in encoded
    assert "pendingApprovalIds" not in encoded
    assert "approval:read-only" not in encoded
    assert "metadata" not in encoded


def test_runtime_heartbeat_contract_rejects_authority_shaped_runtime_fields() -> None:
    unsafe_cases: tuple[tuple[type[object], dict[str, object]], ...] = (
        (RunLease, {**_lease_payload(), "authorityScope": "provider:openai"}),
        (RunLease, {**_lease_payload(), "activeBoundary": "db.write"}),
        (
            HeartbeatReceipt,
            {
                **_heartbeat_payload(),
                "activeToolName": "browser.open",
            },
        ),
        (
            ActivityReceipt,
            {
                **_activity_payload(),
                "activityType": "workspace.write",
            },
        ),
        (
            ActivityReceipt,
            {
                **_activity_payload(),
                "activityRef": "memory.write",
            },
        ),
    )

    for model, payload in unsafe_cases:
        if model is HeartbeatReceipt:
            payload["digest"] = heartbeat_receipt_digest(payload)
        elif model is ActivityReceipt:
            payload["digest"] = activity_receipt_digest(payload)
        with pytest.raises(ValidationError, match="runtime contract validation failed"):
            model.model_validate(payload)


@pytest.mark.parametrize(
    ("model", "payload", "snake_name", "alias_name"),
    (
        (RunLease, _lease_payload(), "run_id", "runId"),
        (RunLease, _lease_payload(), "lease_id", "leaseId"),
        (HeartbeatReceipt, _heartbeat_payload(), "emitted_at", "emittedAt"),
        (HeartbeatReceipt, _heartbeat_payload(), "pending_approval_ids", "pendingApprovalIds"),
        (ActivityReceipt, _activity_payload(), "activity_ref", "activityRef"),
        (
            StaleRunVerdict,
            {
                "verdict": "healthy",
                "runId": "run:alpha",
                "checkedAt": "2026-05-27T12:02:00Z",
                "reasonCodes": ["heartbeat_contract_check"],
            },
            "reason_codes",
            "reasonCodes",
        ),
        (
            ResumeDecision,
            {
                "decision": "resume_same_session",
                "runId": "run:alpha",
                "decidedAt": "2026-05-27T12:03:00Z",
                "reasonCodes": ["heartbeat_contract_check"],
            },
            "reason_codes",
            "reasonCodes",
        ),
    ),
)
def test_wire_payloads_reject_snake_case_alias_field_names(
    model: type[RunLease | HeartbeatReceipt | ActivityReceipt | StaleRunVerdict | ResumeDecision],
    payload: dict[str, object],
    snake_name: str,
    alias_name: str,
) -> None:
    snake_payload = dict(payload)
    snake_payload[snake_name] = snake_payload.pop(alias_name)
    if model is HeartbeatReceipt:
        snake_payload["digest"] = heartbeat_receipt_digest(snake_payload)
    elif model is ActivityReceipt:
        snake_payload["digest"] = activity_receipt_digest(snake_payload)

    with pytest.raises(ValidationError, match="runtime contract validation failed"):
        model.model_validate(snake_payload)


def test_digest_matches_are_not_trusted_activity_or_live_authority() -> None:
    arbitrary = _heartbeat_payload(sequence=99)
    arbitrary["heartbeatId"] = "heartbeat:arbitrary"
    arbitrary["digest"] = heartbeat_receipt_digest(arbitrary)

    heartbeat = HeartbeatReceipt.model_validate(arbitrary)
    projection = heartbeat.public_projection()

    assert heartbeat.digest == heartbeat_receipt_digest(arbitrary)
    assert heartbeat.digest.startswith("sha256:heartbeat:")
    assert arbitrary["lastActivityReceiptDigest"].startswith("sha256:activity:")
    assert activity_receipt_digest(_activity_payload()).startswith("sha256:activity:")
    assert _digest_text("lease:primary").startswith("sha256:")
    assert projection["digest"] == heartbeat.digest
    assert projection["liveAuthority"] is False
    assert projection["trafficAttached"] is False
    assert projection["trustedLeaseAuthority"] is False
    assert projection["authorityProof"] == "requires_trusted_lease_store"
