from __future__ import annotations

from copy import deepcopy
import os
import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from magi_agent.shadow.gate3b_local_consumer import (
    Gate3BLocalConsumedBundle,
    Gate3BLocalConsumerConfig,
    consume_gate3b_local_files,
)
from magi_agent.shadow.gate3b_local_report import (
    build_gate3b_local_comparison_reports,
)
from magi_agent.shadow.gate3b_metrics import (
    Gate3BLocalMetricAttachmentFlags,
    Gate3BLocalMetricRecord,
    build_gate3b_local_metrics_snapshot,
)


FIXTURES = Path(__file__).parent / "fixtures" / "gate3b" / "local_sink"


def _isolated_dir(tmp_path: Path) -> Path:
    path = tmp_path / "adk-shadow-capture" / "gate3b"
    path.mkdir(parents=True)
    return path


def _copy_fixture(name: str, target_dir: Path, *, target_name: str | None = None) -> Path:
    target = target_dir / (target_name or name)
    shutil.copyfile(FIXTURES / name, target)
    return target


def _consume_with_skips(tmp_path: Path):
    capture_dir = _isolated_dir(tmp_path)
    first = _copy_fixture("valid_bundle_a.json", capture_dir, target_name="a-valid.json")
    duplicate = _copy_fixture("valid_bundle_a.json", capture_dir, target_name="b-duplicate.json")
    invalid = _copy_fixture(
        "redaction_violation.json",
        capture_dir,
        target_name="c-invalid.json",
    )
    os.utime(first, ns=(1_000_000_000, 1_000_000_000))
    os.utime(duplicate, ns=(2_000_000_000, 2_000_000_000))
    os.utime(invalid, ns=(3_000_000_000, 3_000_000_000))

    return consume_gate3b_local_files(
        Gate3BLocalConsumerConfig(enabled=True, input_dir=capture_dir)
    )


def _malformed_handoff(consumed: Gate3BLocalConsumedBundle) -> Gate3BLocalConsumedBundle:
    payload = deepcopy(consumed.recorded_bundle_payload)
    payload["schemaVersion"] = "not-gate3a"
    return consumed.model_copy(update={"recorded_bundle_payload": payload})


def test_local_metrics_snapshot_aggregates_consumer_and_report_counts(
    tmp_path: Path,
) -> None:
    consumer_result = _consume_with_skips(tmp_path)
    assert len(consumer_result.consumed) == 1
    assert len(consumer_result.skipped) == 2
    reports = build_gate3b_local_comparison_reports(
        (
            consumer_result.consumed[0],
            _malformed_handoff(consumer_result.consumed[0]),
        )
    )

    snapshot = build_gate3b_local_metrics_snapshot(
        consumer_result=consumer_result,
        reports=reports,
    )
    payload = snapshot.model_dump(by_alias=True, mode="json")

    assert payload["schemaVersion"] == "gate3b.localMetricsSnapshot.v1"
    assert payload["metricsMode"] == "local_diagnostic_metadata_only"
    assert payload["attachmentFlags"]["adkRunnerInvoked"] is False
    assert payload["adkRunnerInvoked"] is False
    assert payload["liveShadowExecuted"] is False
    assert payload["productionStorageAttached"] is False
    assert payload["productionQueueAttached"] is False
    assert snapshot.counts.accepted == 1
    assert snapshot.counts.skipped == 2
    assert snapshot.counts.file_count == 3
    assert snapshot.counts.byte_count > 0
    assert snapshot.skip_reason_counts.duplicate_bundle_id == 1
    assert snapshot.skip_reason_counts.validation_failed == 1
    assert snapshot.report_verdict_counts.schema_pass == 1
    assert snapshot.report_verdict_counts.invalid_handoff == 1
    assert snapshot.ordering.observed_bundle_count == 1
    assert snapshot.ordering.deterministic_ordering is True
    assert "bundle_local_sink_a" not in repr(payload)
    assert all(item.startswith("sha256:") for item in payload["duplicateBundleIdDigests"])
    assert payload["metricRecords"]
    assert {item["metricName"] for item in payload["metricRecords"]} >= {
        "gate3b.capture.accepted",
        "gate3b.capture.skipped",
        "gate3b.consumer.bytes",
        "gate3b.report.verdict",
    }
    assert payload["duplicateBundleIdDigests"] == []


def test_metric_records_serialize_with_camel_case_aliases() -> None:
    record = Gate3BLocalMetricRecord(
        metricName="gate3b.capture.accepted",
        sourceSlice="3b-4",
        sourceRuntime="local-diagnostic",
        value=3,
        redactionStatus="verified",
        categoricalStatus="schema_pass",
        stopConditionCategory="none",
    )

    payload = record.model_dump(by_alias=True, mode="json")

    assert payload["schemaVersion"] == "gate3b.localMetricRecord.v1"
    assert payload["metricName"] == "gate3b.capture.accepted"
    assert payload["sourceSlice"] == "3b-4"
    assert payload["sourceRuntime"] == "local-diagnostic"
    assert payload["categoricalStatus"] == "schema_pass"
    assert payload["stopConditionCategory"] == "none"
    assert payload["attachmentFlags"]["liveRunnerAttached"] is False


def test_unknown_metric_names_are_rejected() -> None:
    with pytest.raises(ValidationError):
        Gate3BLocalMetricRecord(
            metricName="gate3b.unknown.metric",
            sourceSlice="3b-4",
            sourceRuntime="local-diagnostic",
            value=1,
            redactionStatus="verified",
        )


@pytest.mark.parametrize(
    "unsafe_value",
    (
        "Authorization: Bearer unsafe-token",
        "sk-unsafeunsafeunsafe",
        "STRIPE_SECRET_KEY=unsafe",
        "SUPABASE_SERVICE_ROLE_KEY=unsafe",
        "cookie: session=unsafe",
        "hidden_reasoning: private",
        "private_tool_preview: raw",
        "/workspace/bot-a/file.txt",
        "/data/bots/bot-a/transcript.jsonl",
        "https://openmagi.ai/admin/bot-a",
        "bot-prod-123",
        "session-key-123",
    ),
)
def test_metric_records_reject_unsanitized_dimension_values(unsafe_value: str) -> None:
    with pytest.raises(ValidationError):
        Gate3BLocalMetricRecord(
            metricName="gate3b.capture.skipped",
            sourceSlice="3b-4",
            sourceRuntime="local-diagnostic",
            value=1,
            redactionStatus="verified",
            dimensionValues={"detail": unsafe_value},
        )


@pytest.mark.parametrize(
    "unsafe_value",
    (
        "-----BEGIN PRIVATE KEY-----\nunsafe\n-----END PRIVATE KEY-----",
        "-----BEGIN OPENSSH PRIVATE KEY-----\nunsafe\n-----END OPENSSH PRIVATE KEY-----",
        "/Users/kevin/.kube/config",
        "postgresql://user:pass@db.example/app",
        "supabase://project/service-role",
        "s3://bucket/gate3b",
        "infra/k8s/prod/deployment.yaml",
        "deploy.sh",
        "runtime-selector",
        "telegram polling enabled",
        "api route attached",
        "api proxy attached",
        "api attached",
        "dashboard proxy path",
        "dashboard route attached",
        "dashboard attached",
        "proxy route attached",
        "proxy attached",
        "kube path attached",
        "shell command executed",
        "code runner executed",
        "org_prod_123",
        "bot_prod_123",
        "user_prod_123",
        "session_prod_123",
    ),
)
def test_metric_records_reject_broader_public_sanitizer_violations(
    unsafe_value: str,
) -> None:
    with pytest.raises(ValidationError):
        Gate3BLocalMetricRecord(
            metricName="gate3b.capture.skipped",
            sourceSlice="3b-4",
            sourceRuntime="local-diagnostic",
            value=1,
            redactionStatus="verified",
            dimensionValues={"detail": unsafe_value},
        )


@pytest.mark.parametrize(
    "unsafe_key",
    (
        "organizationId",
        "organization_id",
        "sessionId",
        "session_id",
        "userId",
        "user_id",
        "rawSessionId",
        "raw_session_id",
        "rawUserId",
        "raw_user_id",
        "rawOrgId",
        "raw_org_id",
        "botId",
        "bot_id",
        "rawBotId",
        "raw_bot_id",
    ),
)
def test_metric_records_reject_raw_identity_dimension_keys(unsafe_key: str) -> None:
    with pytest.raises(ValidationError):
        Gate3BLocalMetricRecord(
            metricName="gate3b.capture.skipped",
            sourceSlice="3b-4",
            sourceRuntime="local-diagnostic",
            value=1,
            redactionStatus="verified",
            dimensionValues={unsafe_key: "redacted"},
        )


def test_metric_digest_fields_must_be_digests_not_raw_ids() -> None:
    for field_name in ("bundleIdDigest", "recipeSnapshotDigest"):
        with pytest.raises(ValidationError):
            Gate3BLocalMetricRecord(
                metricName="gate3b.capture.accepted",
                sourceSlice="3b-4",
                sourceRuntime="local-diagnostic",
                value=1,
                redactionStatus="verified",
                **{field_name: "bundle_local_sink_a"},
            )
        record = Gate3BLocalMetricRecord(
            metricName="gate3b.capture.accepted",
            sourceSlice="3b-4",
            sourceRuntime="local-diagnostic",
            value=1,
            redactionStatus="verified",
            **{field_name: "sha256:" + "a" * 64},
        )
        assert record.model_dump(by_alias=True)[field_name] == "sha256:" + "a" * 64


def test_ordering_digest_fields_must_be_full_sha256_digests() -> None:
    from magi_agent.shadow.gate3b_metrics import Gate3BLocalOrderingStats

    with pytest.raises(ValidationError):
        Gate3BLocalOrderingStats(
            observedBundleCount=1,
            deterministicOrdering=True,
            firstBundleDigest="bundle_local_sink_a",
            lastBundleDigest="sha256:" + "a" * 64,
        )
    ordering = Gate3BLocalOrderingStats(
        observedBundleCount=1,
        deterministicOrdering=True,
        firstBundleDigest="sha256:" + "a" * 64,
        lastBundleDigest="sha256:" + "b" * 64,
    )
    with pytest.raises(ValidationError):
        ordering.model_copy(update={"firstBundleDigest": "bundle_local_sink_a"})


def test_metric_records_reject_non_finite_or_negative_values() -> None:
    for value in (-1, float("nan"), float("inf")):
        with pytest.raises(ValidationError):
            Gate3BLocalMetricRecord(
                metricName="gate3b.storage.bytes",
                sourceSlice="3b-4",
                sourceRuntime="local-diagnostic",
                value=value,
                redactionStatus="verified",
            )


def test_stop_condition_categories_are_representable_without_payloads() -> None:
    categories = {
        "sanitizer_miss",
        "tool_policy_bypass",
        "queue_backpressure",
        "typescript_response_impact",
        "python_user_output_path",
        "production_state_mutation",
        "evidence_block",
    }

    records = [
        Gate3BLocalMetricRecord(
            metricName="gate3b.capture.redaction_miss",
            sourceSlice="3b-4",
            sourceRuntime="local-diagnostic",
            value=1,
            redactionStatus="verified",
            stopConditionCategory=category,
        )
        for category in categories
    ]

    assert {record.stop_condition_category for record in records} == categories
    assert "payload" not in repr([record.model_dump(by_alias=True) for record in records])


def test_false_only_flags_survive_model_copy_and_construct(tmp_path: Path) -> None:
    consumer_result = _consume_with_skips(tmp_path)
    snapshot = build_gate3b_local_metrics_snapshot(
        consumer_result=consumer_result,
        reports=build_gate3b_local_comparison_reports(consumer_result.consumed),
    )

    copied = snapshot.model_copy(
        update={
            "adkRunnerInvoked": True,
            "liveShadowExecuted": True,
            "productionStorageAttached": True,
            "productionQueueAttached": True,
            "attachmentFlags": {
                "adkRunnerInvoked": True,
                "liveRunnerAttached": True,
                "productionStorageAttached": True,
                "productionQueueAttached": True,
            },
        }
    )
    constructed_flags = Gate3BLocalMetricAttachmentFlags.model_construct(
        adk_runner_invoked=True,
        live_runner_attached=True,
        production_storage_attached=True,
    )

    assert copied.adk_runner_invoked is False
    assert copied.live_shadow_executed is False
    assert copied.production_storage_attached is False
    assert copied.production_queue_attached is False
    assert copied.attachment_flags.live_runner_attached is False
    assert constructed_flags.adk_runner_invoked is False
    assert constructed_flags.live_runner_attached is False
    assert constructed_flags.production_storage_attached is False
