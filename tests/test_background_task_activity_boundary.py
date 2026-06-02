from __future__ import annotations

import json
import sys


def _request(**overrides: object) -> object:
    from openmagi_core_agent.runtime.long_running_activity import LongRunningActivityRequest

    payload = {
        "requestId": "activity-request",
        "activityId": "activity:daily-summary",
        "scopeRef": "scope:daily-mission",
        "runId": "run:daily",
        "turnId": "turn:daily",
        "event": "progress",
        "now": 1_600_000,
        "idempotencyKey": "idempotency:daily-progress",
        "evidenceRefs": ("evidence:mission-plan",),
        "progressMessage": "processed step",
    }
    payload.update(overrides)
    return LongRunningActivityRequest(**payload)


def _policy(**overrides: object) -> object:
    from openmagi_core_agent.runtime.long_running_activity import LongRunningActivityPolicy

    payload = {
        "policyRef": "policy:background-activity",
        "policySnapshotRef": "policy-snapshot:background-pr9",
        "localFakeActivityAllowed": True,
        "evidenceRequired": True,
        "approvalRequiredForCancellation": True,
        "allowedEvents": (
            "start",
            "heartbeat",
            "progress",
            "completion",
            "cancellation",
            "timeout",
            "failure",
        ),
        "allowedSideEffectSurfaces": (),
    }
    payload.update(overrides)
    return LongRunningActivityPolicy(**payload)


def test_background_activity_default_off_records_digest_only_receipt() -> None:
    from openmagi_core_agent.missions.background_tasks import BackgroundTaskActivityBoundary

    result = BackgroundTaskActivityBoundary().record_activity(
        request=_request(
            activityId="activity:raw-output-text-must-not-appear",
            progressMessage="raw transcript /Users/kevin/private Authorization: Bearer abcdefghijk",
            rawPrompt="secret prompt",
            rawOutput="secret output",
            rawToolArgs="secret args",
            authHeader="Authorization: Bearer abcdefghijk",
            cookieHeader="cookie=session",
            outputRefs=("artifact:raw-tool-result-must-not-appear",),
        ),
        policy=_policy(policySnapshotRef="policy-snapshot:raw-policy-text-must-not-appear"),
    )

    assert result.status == "disabled"
    assert result.receipt.local_fake_receipt_recorded is False
    assert result.receipt.long_running_function_tool_attached is False
    assert result.receipt.production_background_execution_enabled is False
    assert result.receipt.provider_call_attempted is False
    assert result.receipt.filesystem_mutation_attempted is False
    assert result.receipt.database_mutation_attempted is False
    assert result.receipt.network_call_attempted is False
    assert set(result.authority_flags.model_dump(by_alias=True).values()) == {False}
    rendered = json.dumps(
        [result.public_projection(), result.model_dump(by_alias=True, mode="json")],
        sort_keys=True,
    )
    for forbidden in (
        "raw-output-text-must-not-appear",
        "raw-tool-result-must-not-appear",
        "raw-policy-text-must-not-appear",
        "Authorization",
        "Bearer",
        "cookie=session",
        "/Users/kevin",
        "secret prompt",
        "secret output",
        "secret args",
    ):
        assert forbidden not in rendered


def test_all_activity_events_emit_local_fake_receipts_without_execution() -> None:
    from openmagi_core_agent.missions.background_tasks import BackgroundTaskActivityBoundary
    from openmagi_core_agent.runtime.long_running_activity import ADK_LONG_RUNNING_FUNCTION_TOOL_REF

    boundary = BackgroundTaskActivityBoundary(
        {"enabled": True, "localFakeActivityEnabled": True},
    )
    events = (
        "start",
        "heartbeat",
        "progress",
        "completion",
        "cancellation",
        "timeout",
        "failure",
    )

    results = tuple(
        boundary.record_activity(
            request=_request(
                requestId=f"activity-request-{event}",
                event=event,
                idempotencyKey=f"idempotency:{event}",
                approvalRef="approval:cancel" if event == "cancellation" else None,
                timeoutMs=1000 if event == "timeout" else None,
                failureReason="provider timeout" if event == "failure" else None,
            ),
            policy=_policy(),
        )
        for event in events
    )

    assert tuple(result.status for result in results) == ("recorded_local_fake",) * len(events)
    for event, result in zip(events, results, strict=True):
        assert result.receipt.event == event
        assert result.receipt.local_fake_receipt_recorded is True
        assert result.receipt.local_test_only is True
        assert result.receipt.adk_long_running_function_tool_ref == ADK_LONG_RUNNING_FUNCTION_TOOL_REF
        assert result.receipt.long_running_function_tool_attached is False
        assert result.receipt.production_background_execution_enabled is False
        assert result.receipt.traffic_attached is False
        assert result.receipt.user_visible_output_enabled is False
        assert set(result.receipt.authority_flags.model_dump(by_alias=True).values()) == {False}


def test_side_effect_surfaces_block_until_policy_explicitly_allows_local_fake_receipt() -> None:
    from openmagi_core_agent.missions.background_tasks import BackgroundTaskActivityBoundary

    boundary = BackgroundTaskActivityBoundary(
        {"enabled": True, "localFakeActivityEnabled": True},
    )
    requested = ("workspace", "memory", "channel", "cron", "artifact")

    denied = boundary.record_activity(
        request=_request(requestedSideEffectSurfaces=requested),
        policy=_policy(),
    )
    allowed = boundary.record_activity(
        request=_request(
            requestId="activity-request-allowed-surfaces",
            idempotencyKey="idempotency:allowed-surfaces",
            requestedSideEffectSurfaces=requested,
        ),
        policy=_policy(allowedSideEffectSurfaces=requested),
    )

    assert denied.status == "blocked"
    assert denied.reason_codes == ("activity_side_effect_surface_not_allowed",)
    assert denied.receipt.local_fake_receipt_recorded is False
    assert allowed.status == "recorded_local_fake"
    assert allowed.receipt.requested_side_effect_surfaces == requested
    assert allowed.receipt.allowed_side_effect_surfaces == requested
    projection = allowed.public_projection()
    flags = projection["authorityFlags"]
    receipt = projection["receipt"]
    assert flags["workspaceMutationEnabled"] is False
    assert flags["memoryMutationEnabled"] is False
    assert flags["channelDeliveryEnabled"] is False
    assert flags["cronMutationEnabled"] is False
    assert flags["artifactDeliveryEnabled"] is False
    assert receipt["filesystemMutationAttempted"] is False
    assert receipt["databaseMutationAttempted"] is False
    assert receipt["networkCallAttempted"] is False


def test_evidence_cancellation_approval_policy_and_local_fake_admission_are_required() -> None:
    from openmagi_core_agent.missions.background_tasks import BackgroundTaskActivityBoundary

    boundary = BackgroundTaskActivityBoundary(
        {"enabled": True, "localFakeActivityEnabled": True},
    )
    missing_policy = boundary.record_activity(request=_request(), policy=None)
    missing_evidence = boundary.record_activity(
        request=_request(evidenceRefs=()),
        policy=_policy(),
    )
    missing_idempotency = boundary.record_activity(
        request=_request(idempotencyKey=None),
        policy=_policy(),
    )
    missing_cancel_approval = boundary.record_activity(
        request=_request(event="cancellation"),
        policy=_policy(),
    )
    local_fake_denied = boundary.record_activity(
        request=_request(),
        policy=_policy(localFakeActivityAllowed=False),
    )
    config_fake_denied = BackgroundTaskActivityBoundary({"enabled": True}).record_activity(
        request=_request(),
        policy=_policy(),
    )

    assert missing_policy.status == "blocked"
    assert missing_policy.reason_codes == ("missing_long_running_activity_policy",)
    assert missing_evidence.status == "blocked"
    assert missing_evidence.reason_codes == ("missing_activity_evidence",)
    assert missing_idempotency.status == "blocked"
    assert missing_idempotency.reason_codes == ("missing_activity_idempotency_key",)
    assert missing_cancel_approval.status == "approval_required"
    assert missing_cancel_approval.reason_codes == ("missing_activity_cancellation_approval",)
    assert local_fake_denied.status == "blocked"
    assert local_fake_denied.reason_codes == ("long_running_activity_local_fake_denied",)
    assert config_fake_denied.status == "blocked"
    assert config_fake_denied.reason_codes == ("local_fake_activity_disabled",)


def test_activity_idempotency_duplicate_and_conflict_are_deterministic() -> None:
    from openmagi_core_agent.missions.background_tasks import BackgroundTaskActivityBoundary

    boundary = BackgroundTaskActivityBoundary(
        {"enabled": True, "localFakeActivityEnabled": True},
    )
    first = boundary.record_activity(request=_request(), policy=_policy())
    duplicate = boundary.record_activity(request=_request(), policy=_policy())
    conflict = boundary.record_activity(
        request=_request(progressMessage="different progress"),
        policy=_policy(),
    )

    assert first.status == "recorded_local_fake"
    assert duplicate.status == "duplicate"
    assert conflict.status == "blocked"
    assert conflict.reason_codes == ("activity_idempotency_conflict",)
    assert duplicate.receipt.receipt_digest == first.receipt.receipt_digest
    assert duplicate.receipt.request_digest == first.receipt.request_digest
    assert conflict.receipt.local_fake_receipt_recorded is False


def test_idempotency_is_scoped_by_activity_scope_and_run() -> None:
    from openmagi_core_agent.missions.background_tasks import BackgroundTaskActivityBoundary

    boundary = BackgroundTaskActivityBoundary(
        {"enabled": True, "localFakeActivityEnabled": True},
    )
    first = boundary.record_activity(
        request=_request(scopeRef="scope:daily-a", idempotencyKey="idempotency:shared"),
        policy=_policy(),
    )
    second = boundary.record_activity(
        request=_request(
            requestId="activity-request-scope-b",
            scopeRef="scope:daily-b",
            idempotencyKey="idempotency:shared",
        ),
        policy=_policy(),
    )
    mission_mapping = boundary.record_activity(
        request={
            "requestId": "activity-request-mission-mapping",
            "activityId": "activity:daily-summary",
            "missionId": "mission:daily",
            "runId": "run:daily",
            "turnId": "turn:daily",
            "event": "progress",
            "idempotencyKey": "idempotency:mission-mapping",
            "evidenceRefs": ("evidence:mission-plan",),
        },
        policy=_policy(),
    )

    assert first.status == "recorded_local_fake"
    assert second.status == "recorded_local_fake"
    assert mission_mapping.status == "recorded_local_fake"
    assert mission_mapping.receipt.scope_ref.startswith("ref:") or mission_mapping.receipt.scope_ref.startswith("scope:")


def test_policy_snapshot_digest_binds_effective_side_effect_policy() -> None:
    from openmagi_core_agent.missions.background_tasks import BackgroundTaskActivityBoundary

    boundary = BackgroundTaskActivityBoundary(
        {"enabled": True, "localFakeActivityEnabled": True},
    )
    base_policy = _policy()
    changed_policy = _policy(allowedSideEffectSurfaces=("workspace",))
    base = boundary.record_activity(
        request=_request(idempotencyKey="idempotency:policy-base"),
        policy=base_policy,
    )
    changed = boundary.record_activity(
        request=_request(
            requestId="activity-request-policy-changed",
            idempotencyKey="idempotency:policy-changed",
            requestedSideEffectSurfaces=("workspace",),
        ),
        policy=changed_policy,
    )

    assert base.status == "recorded_local_fake"
    assert changed.status == "recorded_local_fake"
    assert base.receipt.policy_snapshot_ref == changed.receipt.policy_snapshot_ref
    assert base.receipt.policy_snapshot_digest != changed.receipt.policy_snapshot_digest


def test_receipt_digest_fields_are_hash_only_even_when_constructed_directly() -> None:
    from openmagi_core_agent.runtime.long_running_activity import LongRunningActivityReceipt

    receipt = LongRunningActivityReceipt(
        receiptId="activity-receipt:direct",
        receiptDigest="not-a-digest",
        requestDigest="raw prompt should hash",
        activityId="activity:direct",
        scopeRef="scope:direct",
        runId="run:direct",
        turnId="turn:direct",
        event="progress",
        status="recorded_local_fake",
        progressDigest="processed-step",
        outputRefDigests=("artifact:customer-report",),
        failureReasonDigest="Authorization: Bearer abcdefghijk",
        policySnapshotDigest="policy text",
        policySnapshotRef="policy-snapshot:direct",
        reasonCodes=("local_fake_activity_receipt_only",),
    )
    projection = receipt.public_projection()
    rendered = json.dumps(projection, sort_keys=True)

    assert projection["receiptDigest"].startswith("sha256:")
    assert projection["requestDigest"].startswith("sha256:")
    assert projection["progressDigest"].startswith("sha256:")
    assert projection["outputRefDigests"][0].startswith("sha256:")
    assert projection["failureReasonDigest"].startswith("sha256:")
    assert projection["policySnapshotDigest"].startswith("sha256:")
    assert "processed-step" not in rendered
    assert "customer-report" not in rendered
    assert "Authorization" not in rendered
    assert "Bearer" not in rendered


def test_request_repr_does_not_leak_raw_private_fields() -> None:
    request = _request(
        rawPrompt="secret prompt",
        rawOutput="secret output",
        rawToolArgs="secret args",
        toolLogs="hidden reasoning",
        authHeader="Authorization: Bearer abcdefghijk",
        cookieHeader="cookie=session",
    )

    rendered = repr(request)
    for forbidden in (
        "secret prompt",
        "secret output",
        "secret args",
        "hidden reasoning",
        "Authorization",
        "Bearer",
        "cookie=session",
    ):
        assert forbidden not in rendered


def test_authority_and_attachment_flags_cannot_be_forged_by_construct_or_copy() -> None:
    from openmagi_core_agent.runtime.long_running_activity import (
        LongRunningActivityAuthorityFlags,
        LongRunningActivityConfig,
        LongRunningActivityReceipt,
        LongRunningActivityResult,
    )

    forged_config = LongRunningActivityConfig.model_construct(
        enabled=True,
        localFakeActivityEnabled=True,
        longRunningFunctionToolAttached=True,
        productionBackgroundExecutionEnabled=True,
        trafficAttached=True,
        userVisibleOutputEnabled=True,
        productionWritesEnabled=True,
        providerCallAllowed=True,
    )
    forged_flags = LongRunningActivityAuthorityFlags.model_construct(
        longRunningFunctionToolAttached=True,
        productionBackgroundExecutionEnabled=True,
        trafficAttached=True,
        userVisibleOutputEnabled=True,
        productionWritesEnabled=True,
        providerCallAllowed=True,
        workspaceMutationEnabled=True,
        memoryMutationEnabled=True,
        channelDeliveryEnabled=True,
        cronMutationEnabled=True,
        artifactDeliveryEnabled=True,
        filesystemMutationAllowed=True,
        databaseMutationAllowed=True,
        networkCallAllowed=True,
    )
    result = LongRunningActivityResult(
        status="recorded_local_fake",
        receipt=LongRunningActivityReceipt(
            receiptId="activity-receipt:abc",
            receiptDigest="sha256:" + "a" * 64,
            requestDigest="sha256:" + "b" * 64,
            activityId="activity:x",
            scopeRef="scope:x",
            runId="run:x",
            turnId="turn:x",
            event="progress",
            status="recorded_local_fake",
            policySnapshotDigest="sha256:" + "c" * 64,
            policySnapshotRef="policy-snapshot:x",
            reasonCodes=("local_fake_activity_receipt_only",),
            longRunningFunctionToolAttached=True,
            productionBackgroundExecutionEnabled=True,
            trafficAttached=True,
            userVisibleOutputEnabled=True,
            productionWritesEnabled=True,
            providerCallAttempted=True,
            filesystemMutationAttempted=True,
            databaseMutationAttempted=True,
            networkCallAttempted=True,
            authorityFlags={"workspaceMutationEnabled": True},
        ),
        authorityFlags={"workspaceMutationEnabled": True},
    ).model_copy(
        update={
            "authorityFlags": {"workspaceMutationEnabled": True},
            "receipt": LongRunningActivityReceipt(
                receiptId="activity-receipt:def",
                receiptDigest="sha256:" + "d" * 64,
                requestDigest="sha256:" + "e" * 64,
                activityId="activity:y",
                scopeRef="scope:y",
                runId="run:y",
                turnId="turn:y",
                event="completion",
                status="recorded_local_fake",
                policySnapshotDigest="sha256:" + "f" * 64,
                policySnapshotRef="policy-snapshot:y",
                reasonCodes=("local_fake_activity_receipt_only",),
                longRunningFunctionToolAttached=True,
                productionBackgroundExecutionEnabled=True,
                trafficAttached=True,
                userVisibleOutputEnabled=True,
                productionWritesEnabled=True,
                providerCallAttempted=True,
                filesystemMutationAttempted=True,
                databaseMutationAttempted=True,
                networkCallAttempted=True,
                authorityFlags={"workspaceMutationEnabled": True},
            ),
        }
    )

    assert forged_config.long_running_function_tool_attached is False
    assert forged_config.production_background_execution_enabled is False
    assert forged_config.traffic_attached is False
    assert forged_config.user_visible_output_enabled is False
    assert forged_config.production_writes_enabled is False
    assert forged_config.provider_call_allowed is False
    assert set(forged_flags.model_dump(by_alias=True).values()) == {False}
    assert set(result.authority_flags.model_dump(by_alias=True).values()) == {False}
    assert result.receipt.long_running_function_tool_attached is False
    assert result.receipt.production_background_execution_enabled is False
    assert result.receipt.provider_call_attempted is False
    assert result.receipt.filesystem_mutation_attempted is False
    assert result.receipt.database_mutation_attempted is False
    assert result.receipt.network_call_attempted is False
    assert set(result.receipt.authority_flags.model_dump(by_alias=True).values()) == {False}


def test_background_activity_modules_have_no_live_imports() -> None:
    import subprocess

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

importlib.import_module("openmagi_core_agent.runtime.long_running_activity")
importlib.import_module("openmagi_core_agent.missions.background_tasks")
forbidden_prefixes = (
    "google.adk",
    "google.genai",
    "openmagi_core_agent.adk_bridge",
    "openmagi_core_agent.transport",
    "openmagi_core_agent.routing",
    "openmagi_core_agent.deploy",
    "openmagi_core_agent.chat_proxy",
    "openmagi_core_agent.runtime_selector",
    "openmagi_core_agent.k8s",
    "subprocess",
    "kubernetes",
    "telegram",
    "discord",
    "requests",
    "httpx",
    "aiohttp",
    "socket",
    "urllib",
    "playwright",
    "selenium",
)
loaded = [
    name
    for name in sys.modules
    if any(name == prefix or name.startswith(f"{prefix}.") for prefix in forbidden_prefixes)
]
if loaded:
    raise AssertionError(f"forbidden modules loaded: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
