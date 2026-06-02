from __future__ import annotations

import json
import subprocess
import sys


def _request(**overrides: object) -> object:
    from magi_agent.missions.cron_policy import CronMutationRequest

    payload = {
        "requestId": "cron-mutation-request",
        "missionId": "mission:daily",
        "runId": "run:daily",
        "turnId": "turn:daily",
        "operation": "create",
        "cronId": "cron:daily-summary",
        "scheduleExpression": "*/15 * * * *",
        "timezone": "UTC",
        "now": 1_600_000,
        "idempotencyKey": "idempotency:daily-summary",
        "approvalRef": "approval:cron-create",
        "evidenceRefs": ("evidence:mission-plan",),
        "compensationPolicy": "manual_review_required",
    }
    payload.update(overrides)
    return CronMutationRequest(**payload)


def _policy(**overrides: object) -> object:
    from magi_agent.missions.cron_policy import CronMutationPolicy

    payload = {
        "policyRef": "policy:cron-mutation",
        "policySnapshotRef": "policy-snapshot:cron-pr8",
        "localFakeMutationAllowed": True,
        "approvalRequired": True,
        "idempotencyRequired": True,
        "evidenceRequired": True,
        "allowedOperations": ("create", "update", "delete"),
        "allowedTimezones": ("UTC", "Asia/Seoul"),
        "compensationRequired": True,
    }
    payload.update(overrides)
    return CronMutationPolicy(**payload)


def test_cron_scheduler_mutation_is_default_off_and_digest_only() -> None:
    from magi_agent.missions.scheduler_adapter import CronSchedulerMutationBoundary

    result = CronSchedulerMutationBoundary().plan_mutation(
        request=_request(
            cronId="cron:raw-source-text-must-not-appear",
            idempotencyKey="idempotency:raw-policy-snapshot-text-must-not-appear",
            rawPrompt="Authorization: Bearer abcdefghijk",
        ),
        policy=_policy(policySnapshotRef="policy-snapshot:raw-control-payload-must-not-appear"),
    )

    assert result.status == "disabled"
    assert result.receipt is not None
    assert result.receipt.local_fake_receipt_recorded is False
    assert result.receipt.live_cron_mutation_enabled is False
    assert result.receipt.scheduler_attached is False
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
        "raw-source-text-must-not-appear",
        "raw-policy-snapshot-text-must-not-appear",
        "raw-control-payload-must-not-appear",
        "Authorization",
        "Bearer",
    ):
        assert forbidden not in rendered


def test_policy_requires_idempotency_evidence_approval_and_local_fake_admission() -> None:
    from magi_agent.missions.cron_policy import CronSchedulerMutationConfig
    from magi_agent.missions.scheduler_adapter import CronSchedulerMutationBoundary

    boundary = CronSchedulerMutationBoundary(
        CronSchedulerMutationConfig(enabled=True, localFakeSchedulerReceiptsEnabled=True),
    )
    missing_policy = boundary.plan_mutation(request=_request(), policy=None)
    missing_idempotency = boundary.plan_mutation(
        request=_request(idempotencyKey=None),
        policy=_policy(),
    )
    missing_evidence = boundary.plan_mutation(
        request=_request(evidenceRefs=()),
        policy=_policy(),
    )
    missing_approval = boundary.plan_mutation(
        request=_request(approvalRef=None),
        policy=_policy(),
    )
    local_fake_denied = boundary.plan_mutation(
        request=_request(),
        policy=_policy(localFakeMutationAllowed=False),
    )

    assert missing_policy.status == "blocked"
    assert missing_policy.reason_codes == ("missing_cron_mutation_policy",)
    assert missing_idempotency.status == "blocked"
    assert missing_idempotency.reason_codes == ("missing_cron_idempotency_key",)
    assert missing_evidence.status == "blocked"
    assert missing_evidence.reason_codes == ("missing_cron_mutation_evidence",)
    assert missing_approval.status == "approval_required"
    assert missing_approval.reason_codes == ("missing_cron_mutation_approval",)
    assert local_fake_denied.status == "blocked"
    assert local_fake_denied.reason_codes == ("cron_local_fake_mutation_denied",)


def test_approved_create_update_delete_return_local_fake_receipts_with_preview() -> None:
    from magi_agent.missions.cron_policy import CronSchedulerMutationConfig
    from magi_agent.missions.scheduler_adapter import CronSchedulerMutationBoundary

    boundary = CronSchedulerMutationBoundary(
        CronSchedulerMutationConfig(enabled=True, localFakeSchedulerReceiptsEnabled=True),
    )
    created = boundary.plan_mutation(request=_request(operation="create"), policy=_policy())
    updated = boundary.plan_mutation(
        request=_request(
            requestId="cron-update",
            operation="update",
            idempotencyKey="idempotency:update-daily",
            scheduleExpression="0 9 * * *",
            timezone="Asia/Seoul",
        ),
        policy=_policy(),
    )
    deleted = boundary.plan_mutation(
        request=_request(
            requestId="cron-delete",
            operation="delete",
            idempotencyKey="idempotency:delete-daily",
            scheduleExpression=None,
            timezone="UTC",
            compensationPolicy="restore_previous_definition",
        ),
        policy=_policy(),
    )

    assert created.status == "recorded_local_fake"
    assert created.receipt is not None
    assert created.receipt.local_fake_receipt_recorded is True
    assert created.receipt.operation == "create"
    assert created.receipt.next_run_preview is not None
    assert created.receipt.next_run_preview.next_run_at > 1_600_000
    assert created.receipt.next_run_preview.timezone == "UTC"
    assert updated.status == "recorded_local_fake"
    assert updated.receipt is not None
    assert updated.receipt.next_run_preview is not None
    assert updated.receipt.next_run_preview.timezone == "Asia/Seoul"
    assert deleted.status == "recorded_local_fake"
    assert deleted.receipt is not None
    assert deleted.receipt.next_run_preview is None
    assert deleted.receipt.compensation_policy == "restore_previous_definition"
    for result in (created, updated, deleted):
        assert result.receipt is not None
        assert result.receipt.live_cron_mutation_enabled is False
        assert result.receipt.scheduler_attached is False
        assert result.receipt.background_execution_enabled is False


def test_invalid_timezone_and_operation_are_blocked_without_scheduler_loop() -> None:
    from magi_agent.missions.cron_policy import CronSchedulerMutationConfig
    from magi_agent.missions.scheduler_adapter import CronSchedulerMutationBoundary

    boundary = CronSchedulerMutationBoundary(
        CronSchedulerMutationConfig(enabled=True, localFakeSchedulerReceiptsEnabled=True),
    )
    invalid_timezone = boundary.plan_mutation(
        request=_request(timezone="Mars/Colony"),
        policy=_policy(),
    )
    unsafe_timezone_results = tuple(
        boundary.plan_mutation(
            request=_request(
                requestId=f"cron-unsafe-timezone-{index}",
                idempotencyKey=f"idempotency:unsafe-timezone-{index}",
                timezone=timezone,
            ),
            policy=_policy(),
        )
        for index, timezone in enumerate(
            (
                "raw-output-text-must-not-appear",
                "Authorization: Bearer abcdefghijk",
                "cookie=session",
                "private-cron-payload-must-not-appear",
                "raw-policy-control-timezone-must-not-appear",
            ),
        )
    )
    disallowed_timezone = boundary.plan_mutation(
        request=_request(timezone="America/Los_Angeles"),
        policy=_policy(allowedTimezones=("UTC",)),
    )
    disallowed_operation = boundary.plan_mutation(
        request=_request(operation="delete"),
        policy=_policy(allowedOperations=("create", "update")),
    )

    assert invalid_timezone.status == "blocked"
    assert invalid_timezone.reason_codes == ("invalid_cron_timezone",)
    for result in unsafe_timezone_results:
        assert result.status == "blocked"
        assert result.reason_codes == ("invalid_cron_timezone",)
        assert result.receipt is not None
        assert result.receipt.local_fake_receipt_recorded is False
        assert result.receipt.timezone == "[redacted-timezone]"
    assert disallowed_timezone.status == "blocked"
    assert disallowed_timezone.reason_codes == ("cron_timezone_not_allowed",)
    assert disallowed_operation.status == "blocked"
    assert disallowed_operation.reason_codes == ("cron_operation_not_allowed",)


def test_idempotency_duplicate_and_conflict_are_deterministic() -> None:
    from magi_agent.missions.cron_policy import CronSchedulerMutationConfig
    from magi_agent.missions.scheduler_adapter import CronSchedulerMutationBoundary

    boundary = CronSchedulerMutationBoundary(
        CronSchedulerMutationConfig(enabled=True, localFakeSchedulerReceiptsEnabled=True),
    )
    first = boundary.plan_mutation(request=_request(), policy=_policy())
    duplicate = boundary.plan_mutation(request=_request(), policy=_policy())
    conflict = boundary.plan_mutation(
        request=_request(scheduleExpression="0 9 * * *"),
        policy=_policy(),
    )

    assert first.status == "recorded_local_fake"
    assert duplicate.status == "duplicate"
    assert conflict.status == "blocked"
    assert conflict.reason_codes == ("cron_idempotency_conflict",)
    assert first.receipt is not None
    assert duplicate.receipt is not None
    assert conflict.receipt is not None
    assert duplicate.receipt.receipt_digest == first.receipt.receipt_digest
    assert conflict.receipt.local_fake_receipt_recorded is False


def test_authority_flags_cannot_be_forged_by_payload_construct_or_copy() -> None:
    from magi_agent.missions.cron_policy import (
        CronSchedulerMutationAuthorityFlags,
        CronSchedulerMutationConfig,
        CronSchedulerMutationReceipt,
        CronSchedulerMutationResult,
        sha256_ref,
    )

    forged_config = CronSchedulerMutationConfig.model_construct(
        enabled=True,
        liveCronMutationEnabled=True,
        schedulerAttached=True,
        backgroundExecutionEnabled=True,
        productionWritesEnabled=True,
        providerCallAllowed=True,
    )
    copied_config = CronSchedulerMutationConfig().model_copy(
        update={
            "enabled": True,
            "liveCronMutationEnabled": True,
            "schedulerAttached": True,
            "productionWritesEnabled": True,
        },
    )
    forged_flags = CronSchedulerMutationAuthorityFlags.model_construct(
        liveCronMutationEnabled=True,
        schedulerAttached=True,
        backgroundExecutionEnabled=True,
        productionWritesEnabled=True,
    )
    receipt = CronSchedulerMutationReceipt(
        receiptId="cron-mutation:forge",
        receiptDigest=sha256_ref("receipt"),
        requestDigest=sha256_ref("request"),
        operation="create",
        cronId="cron:forge",
        missionId="mission:forge",
        runId="run:forge",
        turnId="turn:forge",
        status="blocked",
        idempotencyKeyDigest=sha256_ref("idempotency"),
        timezone="UTC",
        scheduleDigest=sha256_ref("schedule"),
        compensationPolicy="manual_review_required",
        policySnapshotDigest=sha256_ref("policy"),
        policySnapshotRef="policy-snapshot:forge",
        evidenceRefs=("evidence:forge",),
        approvalRef="approval:forge",
        reasonCodes=("cron_reason",),
        localFakeReceiptRecorded=False,
        authorityFlags=forged_flags,
        liveCronMutationEnabled=True,
        schedulerAttached=True,
        productionWritesEnabled=True,
    )
    result = CronSchedulerMutationResult(
        status="blocked",
        reasonCodes=("cron_reason",),
        receipt=receipt,
        authorityFlags=forged_flags,
    ).model_copy(
        update={
            "authorityFlags": {"liveCronMutationEnabled": True},
        },
    )

    assert forged_config.live_cron_mutation_enabled is False
    assert copied_config.live_cron_mutation_enabled is False
    assert set(forged_flags.model_dump(by_alias=True).values()) == {False}
    assert result.receipt is not None
    assert set(result.authority_flags.model_dump(by_alias=True).values()) == {False}
    assert set(result.public_projection()["authorityFlags"].values()) == {False}
    assert set(result.public_projection()["receipt"]["authorityFlags"].values()) == {False}


def test_receipts_redact_raw_private_scheduler_payloads_and_safe_looking_refs() -> None:
    from magi_agent.missions.cron_policy import CronSchedulerMutationConfig
    from magi_agent.missions.scheduler_adapter import CronSchedulerMutationBoundary

    result = CronSchedulerMutationBoundary(
        CronSchedulerMutationConfig(enabled=True, localFakeSchedulerReceiptsEnabled=True),
    ).plan_mutation(
        request=_request(
            cronId="cron:raw-scheduler-payload-must-not-appear",
            missionId="mission:raw-control-payload-must-not-appear",
            runId="run:private-cron-payload-must-not-appear",
            turnId="turn:raw-policy-snapshot-text-must-not-appear",
            approvalRef="approval:raw-output-text-must-not-appear",
            evidenceRefs=(
                "evidence:raw-source-text-must-not-appear",
                "source:private-memory-payload-must-not-appear",
            ),
            idempotencyKey="idempotency:raw-tool-log-must-not-appear",
            privateSchedulerPayload=True,
            rawOutput="raw output with /Users/kevin/private and cookie=session",
            childPrompt="child prompt must not appear",
        ),
        policy=_policy(
            policyRef="policy:raw-recipe-prompt-must-not-appear",
            policySnapshotRef="policy-snapshot:raw-policy-snapshot-text-must-not-appear",
        ),
    )

    assert result.status == "blocked"
    assert result.reason_codes == ("private_scheduler_payload_denied",)
    assert result.receipt is not None
    encoded = json.dumps(
        [result.public_projection(), result.model_dump(by_alias=True, mode="json")],
        sort_keys=True,
    )
    for forbidden in (
        "raw-scheduler-payload-must-not-appear",
        "raw-control-payload-must-not-appear",
        "private-cron-payload-must-not-appear",
        "raw-policy-snapshot-text-must-not-appear",
        "raw-output-text-must-not-appear",
        "raw-source-text-must-not-appear",
        "private-memory-payload-must-not-appear",
        "raw-tool-log-must-not-appear",
        "raw-recipe-prompt-must-not-appear",
        "/Users/kevin",
        "cookie",
        "child prompt",
    ):
        assert forbidden not in encoded


def test_cron_scheduler_modules_have_no_live_runtime_imports() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

for module_name in (
    "magi_agent.missions.cron_policy",
    "magi_agent.missions.scheduler_adapter",
):
    importlib.import_module(module_name)
forbidden_prefixes = (
    "google.adk",
    "google.genai",
    "magi_agent.adk_bridge.runner",
    "magi_agent.transport",
    "magi_agent.routing",
    "magi_agent.deploy",
    "magi_agent.chat_proxy",
    "magi_agent.runtime_selector",
    "magi_agent.k8s",
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
