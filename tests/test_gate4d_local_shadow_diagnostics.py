from __future__ import annotations

from pathlib import Path

from magi_agent.shadow.gate4_consumer import Gate4LocalHandoff
from magi_agent.shadow.gate4c1_runner_shadow_invoker import (
    Gate4C1RunnerShadowInvocationResult,
)
from magi_agent.shadow.gate4c2_shadow_comparison_report import (
    Gate4C2ShadowComparisonConfig,
    build_gate4c2_shadow_comparison_report,
)
from magi_agent.shadow.gate4d_local_shadow_diagnostics import (
    Gate4DLocalShadowDiagnosticsConfig,
    Gate4DShadowAuthorityFlags,
    build_gate4d_local_shadow_diagnostics,
)


def _output_dir(tmp_path: Path) -> Path:
    path = tmp_path / "adk-shadow-capture" / "gate4d"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _handoff() -> Gate4LocalHandoff:
    return Gate4LocalHandoff(
        bundleId="bundle_local_shadow_a",
        sourceBundleId="source_bundle_a",
        sourcePath="adk-shadow-capture/gate4/report-000001.json",
        generatedAt="2026-05-18T00:00:00Z",
        parityStatus="match",
        redactionVerified=True,
        reportPath=Path("/tmp/adk-shadow-capture/gate4/reports/report-000001.json"),
    )


def _runner_result(**overrides: object) -> Gate4C1RunnerShadowInvocationResult:
    base = {
        "status": "completed",
        "reason": "runner_completed",
        "runnerInvoked": True,
        "modelCallViaAdkRunnerAttempted": True,
        "eventCount": 2,
        "outputPreview": "The redacted answer is ready.",
        "outputTruncated": False,
        "outputRedactionApplied": False,
        "latencyMs": 125,
        "timeoutMs": 30000,
        "maxOutputChars": 512,
        "maxCostUsd": 1.25,
        "maxQueueDepth": 25,
    }
    base.update(overrides)
    return Gate4C1RunnerShadowInvocationResult(**base)


def _comparison_report(tmp_path: Path, **runner_overrides: object):
    output_dir = tmp_path / "adk-shadow-capture" / "gate4c2"
    output_dir.mkdir(parents=True, exist_ok=True)
    return build_gate4c2_shadow_comparison_report(
        Gate4C2ShadowComparisonConfig(
            enabled=True,
            handoff=_handoff(),
            runnerResult=_runner_result(**runner_overrides),
            tsRecordedOutputPreview="The redacted answer is ready.",
            outputDir=output_dir,
        )
    )


def _config(tmp_path: Path, **overrides: object) -> Gate4DLocalShadowDiagnosticsConfig:
    base = {
        "enabled": True,
        "runnerResults": (_runner_result(),),
        "comparisonReports": (_comparison_report(tmp_path),),
        "outputDir": _output_dir(tmp_path),
        "killSwitchEnabled": False,
        "maxLatencyMs": 1000,
        "maxErrorRate": 0.25,
        "maxDivergenceRate": 0.25,
    }
    base.update(overrides)
    return Gate4DLocalShadowDiagnosticsConfig(**base)


def test_gate4d_metrics_snapshot_aggregates_runner_and_comparison_outputs(
    tmp_path: Path,
) -> None:
    snapshot = build_gate4d_local_shadow_diagnostics(_config(tmp_path))
    payload = snapshot.model_dump(by_alias=True, mode="json")

    assert snapshot.status == "healthy"
    assert snapshot.reason == "within_local_shadow_thresholds"
    assert snapshot.runner_invoked_count == 1
    assert snapshot.runner_error_count == 0
    assert snapshot.comparison_match_count == 1
    assert snapshot.comparison_divergence_count == 0
    assert snapshot.artifact_path is not None
    assert snapshot.artifact_path.is_file()
    assert payload["schemaVersion"] == "gate4d.localShadowDiagnostics.v1"
    assert payload["metricsMode"] == "local_diagnostic_shadow_metrics"
    assert payload["attachmentFlags"]["userVisibleOutputAttached"] is False
    assert payload["attachmentFlags"]["productionMetricsPublished"] is False
    assert payload["attachmentFlags"]["productionStorageWritten"] is False
    assert payload["attachmentFlags"]["canaryRouted"] is False


def test_gate4d_metrics_snapshot_marks_unhealthy_on_runner_errors(tmp_path: Path) -> None:
    snapshot = build_gate4d_local_shadow_diagnostics(
        _config(
            tmp_path,
            runnerResults=(
                _runner_result(status="error", reason="runner_error", runnerInvoked=True),
            ),
            comparisonReports=(_comparison_report(tmp_path, status="error", reason="runner_error"),),
        )
    )

    assert snapshot.status == "unhealthy"
    assert snapshot.reason == "error_rate_exceeded"
    assert snapshot.error_rate == 1.0
    assert snapshot.attachment_flags.production_metrics_published is False


def test_gate4d_metrics_snapshot_marks_unhealthy_on_comparison_redaction_violation(
    tmp_path: Path,
) -> None:
    report = _comparison_report(tmp_path).model_copy(
        update={"status": "redaction_violation", "reason": "unsafe_runner_output"}
    )

    snapshot = build_gate4d_local_shadow_diagnostics(
        _config(tmp_path, comparisonReports=(report,))
    )

    assert snapshot.status == "rollback_recommended"
    assert snapshot.reason == "comparison_redaction_violation"
    assert snapshot.comparison_redaction_violation_count == 1
    assert snapshot.attachment_flags.production_metrics_published is False


def test_gate4d_metrics_snapshot_marks_unhealthy_on_comparison_error(tmp_path: Path) -> None:
    report = _comparison_report(tmp_path).model_copy(
        update={"status": "error", "reason": "artifact_write_error"}
    )

    snapshot = build_gate4d_local_shadow_diagnostics(
        _config(tmp_path, comparisonReports=(report,))
    )

    assert snapshot.status == "unhealthy"
    assert snapshot.reason == "comparison_error"


def test_gate4d_metrics_snapshot_marks_unhealthy_on_comparison_skipped(tmp_path: Path) -> None:
    report = _comparison_report(tmp_path).model_copy(
        update={"status": "skipped", "reason": "runner_not_completed"}
    )

    snapshot = build_gate4d_local_shadow_diagnostics(
        _config(tmp_path, comparisonReports=(report,))
    )

    assert snapshot.status == "unhealthy"
    assert snapshot.reason == "comparison_incomplete"


def test_gate4d_metrics_snapshot_marks_rollback_recommended_on_kill_switch(
    tmp_path: Path,
) -> None:
    snapshot = build_gate4d_local_shadow_diagnostics(
        _config(tmp_path, killSwitchEnabled=True)
    )

    assert snapshot.status == "rollback_recommended"
    assert snapshot.reason == "kill_switch_enabled"
    assert snapshot.kill_switch_enabled is True
    assert snapshot.attachment_flags.canary_routed is False


def test_gate4d_metrics_snapshot_rejects_secret_text(tmp_path: Path) -> None:
    snapshot = build_gate4d_local_shadow_diagnostics(
        _config(
            tmp_path,
            runnerResults=(
                _runner_result(
                    status="error",
                    reason="runner_error",
                    errorPreview="Cookie: sessionid=unsafe",
                ),
            ),
        )
    )

    assert snapshot.status == "redaction_violation"
    assert snapshot.reason == "unsafe_diagnostic_input"
    assert snapshot.artifact_path is not None


def test_gate4d_metrics_snapshot_rejects_non_isolated_output_path() -> None:
    snapshot = build_gate4d_local_shadow_diagnostics(
        Gate4DLocalShadowDiagnosticsConfig(
            enabled=True,
            runnerResults=(_runner_result(),),
            comparisonReports=(),
            outputDir=Path("/workspace/adk-shadow-capture/gate4d"),
        )
    )

    assert snapshot.status == "error"
    assert snapshot.reason == "artifact_write_error"
    assert snapshot.artifact_path is None


def test_gate4d_metrics_snapshot_is_default_off_without_touching_paths() -> None:
    snapshot = build_gate4d_local_shadow_diagnostics(
        Gate4DLocalShadowDiagnosticsConfig(
            enabled=False,
            runnerResults=(_runner_result(),),
            comparisonReports=(),
            outputDir=Path("/workspace/adk-shadow-capture/gate4d"),
        )
    )

    assert snapshot.status == "skipped"
    assert snapshot.reason == "diagnostics_disabled"
    assert snapshot.artifact_path is None


def test_gate4d_authority_flags_cannot_be_enabled_by_copy_or_construct() -> None:
    flags = Gate4DShadowAuthorityFlags()

    copied = flags.model_copy(
        update={
            "userVisibleOutputAttached": True,
            "productionMetricsPublished": True,
            "productionTranscriptWritten": True,
            "productionSseWritten": True,
            "dbWritten": True,
            "channelDelivered": True,
            "workspaceMutated": True,
            "memoryWritten": True,
            "memoryProviderCalled": True,
            "toolHostDispatched": True,
            "liveToolsExecuted": True,
            "productionStorageWritten": True,
            "productionQueueEnqueued": True,
            "telegramAttached": True,
            "billingAuthMutated": True,
            "modelRoutingMutated": True,
            "canaryRouted": True,
        }
    )
    constructed = Gate4DShadowAuthorityFlags.model_construct(
        user_visible_output_attached=True,
        production_metrics_published=True,
        production_transcript_written=True,
        production_sse_written=True,
        db_written=True,
        channel_delivered=True,
        workspace_mutated=True,
        memory_written=True,
        memory_provider_called=True,
        toolhost_dispatched=True,
        live_tools_executed=True,
        production_storage_written=True,
        production_queue_enqueued=True,
        telegram_attached=True,
        billing_auth_mutated=True,
        model_routing_mutated=True,
        canary_routed=True,
    )

    for payload in (
        copied.model_dump(by_alias=True, mode="json"),
        constructed.model_dump(by_alias=True, mode="json"),
    ):
        assert all(value is False for value in payload.values())
