from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from magi_agent.shadow.gate4_bridge import (
    Gate4LocalBridgeConfig,
    Gate4LocalBridgeError,
    run_gate4_local_bridge,
)


FIXTURES = Path(__file__).parent / "fixtures" / "gate3b" / "local_sink"


def _capture_dir(tmp_path: Path) -> Path:
    path = tmp_path / "adk-shadow-capture" / "gate3b"
    path.mkdir(parents=True)
    return path


def _output_dir(tmp_path: Path) -> Path:
    path = tmp_path / "adk-shadow-capture" / "gate4"
    path.mkdir(parents=True)
    return path


def _copy_fixture(name: str, target_dir: Path, *, target_name: str | None = None) -> Path:
    target = target_dir / (target_name or name)
    shutil.copyfile(FIXTURES / name, target)
    return target


def _config(
    capture_dir: Path,
    output_dir: Path,
    **overrides: object,
) -> Gate4LocalBridgeConfig:
    return Gate4LocalBridgeConfig(
        enabled=True,
        inputDir=capture_dir,
        outputDir=output_dir,
        **overrides,
    )


def test_gate4_bridge_is_default_off_without_touching_paths(tmp_path: Path) -> None:
    capture_dir = _capture_dir(tmp_path)
    output_dir = _output_dir(tmp_path)
    _copy_fixture("valid_bundle_a.json", capture_dir)

    result = run_gate4_local_bridge(
        Gate4LocalBridgeConfig(enabled=False, inputDir=capture_dir, outputDir=output_dir)
    )

    assert result.consumed == ()
    assert result.skipped == ()
    assert result.reports == ()
    assert result.report_paths == ()
    assert result.metrics is None
    assert result.metrics_path is None
    assert result.attachment_flags.adk_runner_invoked is False
    assert result.attachment_flags.live_shadow_executed is False
    assert list((output_dir / "reports").glob("*.json")) == []


def test_gate4_bridge_processes_valid_bundles_into_local_reports_and_metrics(
    tmp_path: Path,
) -> None:
    capture_dir = _capture_dir(tmp_path)
    output_dir = _output_dir(tmp_path)
    later = _copy_fixture("valid_bundle_b.json", capture_dir, target_name="b.json")
    earlier = _copy_fixture("valid_bundle_a.json", capture_dir, target_name="a.json")
    os.utime(later, ns=(2_000_000_000, 2_000_000_000))
    os.utime(earlier, ns=(1_000_000_000, 1_000_000_000))

    result = run_gate4_local_bridge(_config(capture_dir, output_dir))

    assert [item.bundle_id for item in result.consumed] == [
        "bundle_local_sink_a",
        "bundle_local_sink_b",
    ]
    assert [report.bundle_id for report in result.reports] == [
        "bundle_local_sink_a",
        "bundle_local_sink_b",
    ]
    assert [path.name for path in result.report_paths] == [
        "report-000001.json",
        "report-000002.json",
    ]
    assert result.metrics is not None
    assert result.metrics_path == output_dir / "metrics" / "metrics-snapshot.json"
    assert result.metrics.counts.accepted == 2
    assert result.metrics.counts.report_count == 2
    assert result.metrics.counts.skipped == 0

    first_report_payload = json.loads(result.report_paths[0].read_text(encoding="utf-8"))
    metrics_payload = json.loads(result.metrics_path.read_text(encoding="utf-8"))
    assert first_report_payload["schemaVersion"] == "gate3b.localComparisonReport.v1"
    assert metrics_payload["schemaVersion"] == "gate3b.localMetricsSnapshot.v1"
    assert first_report_payload["adkRunnerInvoked"] is False
    assert first_report_payload["liveShadowExecuted"] is False
    assert first_report_payload["toolsExecuted"] is False
    assert first_report_payload["userVisibleOutputAttached"] is False
    assert metrics_payload["adkRunnerInvoked"] is False
    assert metrics_payload["liveShadowExecuted"] is False
    assert metrics_payload["productionStorageAttached"] is False
    assert metrics_payload["productionQueueAttached"] is False
    assert metrics_payload["telegramAttached"] is False


@pytest.mark.parametrize(
    ("input_path", "output_path"),
    (
        ("/workspace/adk-shadow-capture", None),
        ("/data/bots/bot-a/adk-shadow-capture", None),
        (None, "/workspace/adk-shadow-capture"),
        (None, "/var/lib/kubelet/pods/adk-shadow-capture"),
    ),
)
def test_gate4_bridge_rejects_non_isolated_or_production_like_paths(
    tmp_path: Path,
    input_path: str | None,
    output_path: str | None,
) -> None:
    capture_dir = _capture_dir(tmp_path)
    output_dir = _output_dir(tmp_path)

    with pytest.raises(Gate4LocalBridgeError):
        run_gate4_local_bridge(
            _config(
                Path(input_path) if input_path is not None else capture_dir,
                Path(output_path) if output_path is not None else output_dir,
            )
        )


def test_gate4_bridge_rejects_same_input_and_output_directory(tmp_path: Path) -> None:
    capture_dir = _capture_dir(tmp_path)
    _copy_fixture("valid_bundle_a.json", capture_dir)

    with pytest.raises(Gate4LocalBridgeError):
        run_gate4_local_bridge(_config(capture_dir, capture_dir))


def test_gate4_bridge_preserves_duplicate_idempotency_in_reports_and_metrics(
    tmp_path: Path,
) -> None:
    capture_dir = _capture_dir(tmp_path)
    output_dir = _output_dir(tmp_path)
    first = _copy_fixture("valid_bundle_a.json", capture_dir, target_name="first.json")
    second = _copy_fixture("valid_bundle_a.json", capture_dir, target_name="second.json")
    os.utime(first, ns=(1_000_000_000, 1_000_000_000))
    os.utime(second, ns=(2_000_000_000, 2_000_000_000))

    result = run_gate4_local_bridge(_config(capture_dir, output_dir))

    assert [item.bundle_id for item in result.consumed] == ["bundle_local_sink_a"]
    assert [item.reason for item in result.skipped] == ["duplicate_bundle_id"]
    assert len(result.report_paths) == 1
    assert result.metrics is not None
    assert result.metrics.counts.accepted == 1
    assert result.metrics.counts.duplicate_bundle_ids == 1


def test_gate4_bridge_skips_previously_processed_bundle_ids(tmp_path: Path) -> None:
    capture_dir = _capture_dir(tmp_path)
    output_dir = _output_dir(tmp_path)
    _copy_fixture("valid_bundle_a.json", capture_dir)

    result = run_gate4_local_bridge(
        _config(
            capture_dir,
            output_dir,
            processedBundleIds=("bundle_local_sink_a",),
        )
    )

    assert result.consumed == ()
    assert [item.reason for item in result.skipped] == ["duplicate_bundle_id"]
    assert result.reports == ()
    assert result.metrics is not None
    assert result.metrics.counts.accepted == 0
    assert result.metrics.counts.duplicate_bundle_ids == 1


def test_gate4_bridge_skips_corrupted_partial_json_without_blocking_valid_bundle(
    tmp_path: Path,
) -> None:
    capture_dir = _capture_dir(tmp_path)
    output_dir = _output_dir(tmp_path)
    _copy_fixture("corrupted_partial.json", capture_dir, target_name="a-corrupt.json")
    _copy_fixture("valid_bundle_b.json", capture_dir, target_name="b-valid.json")

    result = run_gate4_local_bridge(_config(capture_dir, output_dir))

    assert [item.bundle_id for item in result.consumed] == ["bundle_local_sink_b"]
    assert [(item.path.name, item.reason) for item in result.skipped] == [
        ("a-corrupt.json", "invalid_json")
    ]
    assert len(result.report_paths) == 1
    assert result.metrics is not None
    assert result.metrics.skip_reason_counts.invalid_json == 1


def test_gate4_bridge_rejects_redaction_violations_without_writing_reports(
    tmp_path: Path,
) -> None:
    capture_dir = _capture_dir(tmp_path)
    output_dir = _output_dir(tmp_path)
    _copy_fixture("redaction_violation.json", capture_dir)

    result = run_gate4_local_bridge(_config(capture_dir, output_dir))

    assert result.consumed == ()
    assert [item.reason for item in result.skipped] == ["validation_failed"]
    assert result.reports == ()
    assert result.report_paths == ()
    assert result.metrics is not None
    assert result.metrics.counts.redaction_failures == 1
    assert list((output_dir / "reports").glob("*.json")) == []


def test_gate4_bridge_attachment_flags_cannot_be_enabled_by_model_copy_or_construct(
    tmp_path: Path,
) -> None:
    capture_dir = _capture_dir(tmp_path)
    output_dir = _output_dir(tmp_path)
    _copy_fixture("valid_bundle_a.json", capture_dir)
    result = run_gate4_local_bridge(_config(capture_dir, output_dir))

    copied = result.attachment_flags.model_copy(
        update={
            "adkRunnerInvoked": True,
            "liveShadowExecuted": True,
            "toolsExecuted": True,
            "productionStorageAttached": True,
            "productionQueueAttached": True,
            "evidenceBlockEnabled": True,
        }
    )
    constructed = type(result.attachment_flags).model_construct(
        adk_runner_invoked=True,
        live_shadow_executed=True,
        tools_executed=True,
        production_storage_attached=True,
        production_queue_attached=True,
        evidence_block_enabled=True,
    )

    assert copied.adk_runner_invoked is False
    assert copied.live_shadow_executed is False
    assert copied.tools_executed is False
    assert copied.production_storage_attached is False
    assert copied.production_queue_attached is False
    assert copied.evidence_block_enabled is False
    assert constructed.adk_runner_invoked is False
    assert constructed.live_shadow_executed is False
    assert constructed.tools_executed is False
    assert constructed.production_storage_attached is False
    assert constructed.production_queue_attached is False
    assert constructed.evidence_block_enabled is False


def test_gate4_bridge_artifact_outputs_are_deterministic_and_remove_stale_reports(
    tmp_path: Path,
) -> None:
    capture_dir = _capture_dir(tmp_path)
    output_dir = _output_dir(tmp_path)
    _copy_fixture("valid_bundle_a.json", capture_dir)

    first = run_gate4_local_bridge(_config(capture_dir, output_dir))
    stale = output_dir / "reports" / "report-999999.json"
    stale.write_text('{"stale": true}\n', encoding="utf-8")
    first_report_bytes = first.report_paths[0].read_bytes()
    first_metrics_bytes = first.metrics_path.read_bytes()

    second = run_gate4_local_bridge(_config(capture_dir, output_dir))

    assert stale.exists() is False
    assert second.report_paths[0].read_bytes() == first_report_bytes
    assert second.metrics_path.read_bytes() == first_metrics_bytes
    assert [path.name for path in sorted((output_dir / "reports").glob("report-*.json"))] == [
        "report-000001.json"
    ]
