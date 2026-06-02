from __future__ import annotations

from pathlib import Path

import pytest

from openmagi_core_agent.shadow.gate4_consumer import Gate4LocalHandoff
from openmagi_core_agent.shadow.gate4c1_runner_shadow_invoker import (
    Gate4C1RunnerShadowInvocationResult,
)
from openmagi_core_agent.shadow.gate4c2_shadow_comparison_report import (
    Gate4C2AuthorityFlags,
    Gate4C2ShadowComparisonConfig,
    build_gate4c2_shadow_comparison_report,
)


def _output_dir(tmp_path: Path) -> Path:
    path = tmp_path / "adk-shadow-capture" / "gate4c2"
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
        "agentKwargsKeys": ("description", "generate_content_config", "instruction", "model", "name", "tools"),
        "runnerKwargsKeys": ("agent", "app_name", "auto_create_session", "session_service"),
        "runAsyncKwargsKeys": ("new_message", "session_id", "user_id"),
    }
    base.update(overrides)
    return Gate4C1RunnerShadowInvocationResult(**base)


def _config(tmp_path: Path, **overrides: object) -> Gate4C2ShadowComparisonConfig:
    base = {
        "enabled": True,
        "handoff": _handoff(),
        "runnerResult": _runner_result(),
        "tsRecordedOutputPreview": "The redacted answer is ready.",
        "outputDir": _output_dir(tmp_path),
        "maxPreviewChars": 128,
    }
    base.update(overrides)
    return Gate4C2ShadowComparisonConfig(**base)


def test_gate4c2_comparison_report_matches_local_ts_and_runner_outputs(tmp_path: Path) -> None:
    report = build_gate4c2_shadow_comparison_report(_config(tmp_path))
    payload = report.model_dump(by_alias=True, mode="json")

    assert report.status == "match"
    assert report.reason == "normalized_preview_match"
    assert report.runner_status == "completed"
    assert report.local_diagnostic is True
    assert report.artifact_path is not None
    assert report.artifact_path.is_file()
    assert payload["schemaVersion"] == "gate4c2.shadowComparisonReport.v1"
    assert payload["comparisonMode"] == "local_diagnostic_runner_output_comparison"
    assert payload["attachmentFlags"]["userVisibleOutputAttached"] is False
    assert payload["attachmentFlags"]["productionTranscriptWritten"] is False
    assert payload["attachmentFlags"]["productionSseWritten"] is False
    assert payload["attachmentFlags"]["dbWritten"] is False
    assert payload["attachmentFlags"]["channelDelivered"] is False
    assert payload["attachmentFlags"]["toolHostDispatched"] is False
    assert payload["attachmentFlags"]["memoryProviderCalled"] is False
    assert payload["attachmentFlags"]["canaryRouted"] is False


def test_gate4c2_comparison_report_records_divergence_without_public_authority(
    tmp_path: Path,
) -> None:
    report = build_gate4c2_shadow_comparison_report(
        _config(
            tmp_path,
            runnerResult=_runner_result(outputPreview="Different redacted answer."),
        )
    )

    assert report.status == "diverged"
    assert report.reason == "normalized_preview_mismatch"
    assert report.diff_summary.changed is True
    assert report.attachment_flags.user_visible_output_attached is False
    assert report.attachment_flags.production_storage_written is False


def test_gate4c2_comparison_report_skips_when_runner_not_completed(tmp_path: Path) -> None:
    report = build_gate4c2_shadow_comparison_report(
        _config(
            tmp_path,
            runnerResult=_runner_result(status="error", reason="runner_error", outputPreview=""),
        )
    )

    assert report.status == "skipped"
    assert report.reason == "runner_not_completed"
    assert report.artifact_path is not None
    assert report.attachment_flags.user_visible_output_attached is False


def test_gate4c2_comparison_report_rejects_unredacted_recorded_output(tmp_path: Path) -> None:
    report = build_gate4c2_shadow_comparison_report(
        _config(tmp_path, tsRecordedOutputPreview="Cookie: sessionid=unsafe")
    )

    assert report.status == "redaction_violation"
    assert report.reason == "unsafe_recorded_output"
    assert report.artifact_path is not None
    assert "sessionid" not in report.ts_recorded_output_preview


def test_gate4c2_comparison_report_rejects_structured_provider_tokens(
    tmp_path: Path,
) -> None:
    report = build_gate4c2_shadow_comparison_report(
        _config(tmp_path, tsRecordedOutputPreview='{"api_key": "AIza' + "a" * 32 + '"}')
    )

    assert report.status == "redaction_violation"
    assert report.reason == "unsafe_recorded_output"
    assert report.artifact_path is not None
    assert "AIza" not in report.ts_recorded_output_preview


def test_gate4c2_comparison_report_rejects_raw_slack_tokens(tmp_path: Path) -> None:
    report = build_gate4c2_shadow_comparison_report(
        _config(tmp_path, tsRecordedOutputPreview="xoxc-1234567890-unsafe")
    )

    assert report.status == "redaction_violation"
    assert report.reason == "unsafe_recorded_output"
    assert report.artifact_path is not None
    assert "xoxc" not in report.ts_recorded_output_preview


def test_gate4c2_comparison_report_rejects_non_isolated_output_path() -> None:
    report = build_gate4c2_shadow_comparison_report(
        Gate4C2ShadowComparisonConfig(
            enabled=True,
            handoff=_handoff(),
            runnerResult=_runner_result(),
            tsRecordedOutputPreview="The redacted answer is ready.",
            outputDir=Path("/workspace/adk-shadow-capture/gate4c2"),
        )
    )

    assert report.status == "error"
    assert report.reason == "artifact_write_error"
    assert report.artifact_path is None


def test_gate4c2_comparison_report_rejects_symlinked_nested_report_directory(
    tmp_path: Path,
) -> None:
    output_dir = _output_dir(tmp_path)
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    (output_dir / "shadow-comparison").symlink_to(outside_dir, target_is_directory=True)

    report = build_gate4c2_shadow_comparison_report(_config(tmp_path, outputDir=output_dir))

    assert report.status == "error"
    assert report.reason == "artifact_write_error"
    assert report.artifact_path is None
    assert not (outside_dir / "gate4c2-shadow-comparison.json").exists()


def test_gate4c2_comparison_report_rejects_symlinked_temp_file(tmp_path: Path) -> None:
    output_dir = _output_dir(tmp_path)
    reports_dir = output_dir / "shadow-comparison"
    reports_dir.mkdir()
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    outside_file = outside_dir / "escaped.json"
    (reports_dir / ".gate4c2-shadow-comparison.json.tmp").symlink_to(outside_file)

    report = build_gate4c2_shadow_comparison_report(_config(tmp_path, outputDir=output_dir))

    assert report.status == "error"
    assert report.reason == "artifact_write_error"
    assert report.artifact_path is None
    assert not outside_file.exists()


def test_gate4c2_comparison_report_is_default_off_without_touching_paths() -> None:
    report = build_gate4c2_shadow_comparison_report(
        Gate4C2ShadowComparisonConfig(
            enabled=False,
            handoff=_handoff(),
            runnerResult=_runner_result(),
            tsRecordedOutputPreview="The redacted answer is ready.",
            outputDir=Path("/workspace/adk-shadow-capture/gate4c2"),
        )
    )

    assert report.status == "skipped"
    assert report.reason == "comparison_disabled"
    assert report.artifact_path is None


def test_gate4c2_authority_flags_cannot_be_enabled_by_copy_or_construct() -> None:
    flags = Gate4C2AuthorityFlags()

    copied = flags.model_copy(
        update={
            "adkRunnerInvoked": True,
            "userVisibleOutputAttached": True,
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
    constructed = Gate4C2AuthorityFlags.model_construct(
        adk_runner_invoked=True,
        user_visible_output_attached=True,
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
