from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from openmagi_core_agent.shadow.gate4_bridge import (
    Gate4LocalBridgeConfig,
    run_gate4_local_bridge,
)
from openmagi_core_agent.shadow.gate4_consumer import (
    Gate4LocalConsumerConfig,
    Gate4LocalConsumerError,
    consume_gate4_local_bridge_outputs,
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


def _run_bridge(tmp_path: Path, *, duplicate: bool = False):
    capture_dir = _capture_dir(tmp_path)
    output_dir = _output_dir(tmp_path)
    first = _copy_fixture("valid_bundle_a.json", capture_dir, target_name="a.json")
    second = _copy_fixture("valid_bundle_b.json", capture_dir, target_name="b.json")
    os.utime(first, ns=(1_000_000_000, 1_000_000_000))
    os.utime(second, ns=(2_000_000_000, 2_000_000_000))
    if duplicate:
        dup = _copy_fixture("valid_bundle_a.json", capture_dir, target_name="c-duplicate.json")
        os.utime(dup, ns=(3_000_000_000, 3_000_000_000))
    bridge = run_gate4_local_bridge(
        Gate4LocalBridgeConfig(enabled=True, inputDir=capture_dir, outputDir=output_dir)
    )
    return output_dir, bridge


def _config(output_dir: Path, **overrides: object) -> Gate4LocalConsumerConfig:
    return Gate4LocalConsumerConfig(enabled=True, inputDir=output_dir, **overrides)


def test_gate4_consumer_is_default_off_without_reading_outputs(tmp_path: Path) -> None:
    output_dir, _bridge = _run_bridge(tmp_path)

    result = consume_gate4_local_bridge_outputs(
        Gate4LocalConsumerConfig(enabled=False, inputDir=output_dir)
    )

    assert result.handoffs == ()
    assert result.skipped == ()
    assert result.attachment_flags.adk_runner_invoked is False
    assert result.attachment_flags.model_called is False


def test_gate4_consumer_reads_local_bridge_reports_and_metrics_deterministically(
    tmp_path: Path,
) -> None:
    output_dir, _bridge = _run_bridge(tmp_path)

    result = consume_gate4_local_bridge_outputs(_config(output_dir))

    assert [handoff.bundle_id for handoff in result.handoffs] == [
        "bundle_local_sink_a",
        "bundle_local_sink_b",
    ]
    assert result.skipped == ()
    assert result.metrics is not None
    assert result.metrics.counts.accepted == 2
    assert result.local_diagnostic_artifact_count == 3
    payload = result.model_dump(by_alias=True, mode="json")
    assert payload["attachmentFlags"]["adkRunnerInvoked"] is False
    assert payload["attachmentFlags"]["modelCalled"] is False
    assert payload["attachmentFlags"]["toolsExecuted"] is False
    assert payload["attachmentFlags"]["userVisibleOutputAttached"] is False
    assert payload["attachmentFlags"]["productionStorageAttached"] is False
    assert payload["attachmentFlags"]["telegramAttached"] is False


def test_gate4_consumer_rejects_non_isolated_input_directory() -> None:
    with pytest.raises(Gate4LocalConsumerError):
        consume_gate4_local_bridge_outputs(
            Gate4LocalConsumerConfig(enabled=True, inputDir=Path("/workspace/adk-shadow-capture"))
        )


def test_gate4_consumer_rejects_missing_metrics_snapshot(tmp_path: Path) -> None:
    output_dir, _bridge = _run_bridge(tmp_path)
    (output_dir / "metrics" / "metrics-snapshot.json").unlink()

    with pytest.raises(Gate4LocalConsumerError):
        consume_gate4_local_bridge_outputs(_config(output_dir))


def test_gate4_consumer_skips_corrupted_or_partial_report_json(tmp_path: Path) -> None:
    output_dir, _bridge = _run_bridge(tmp_path)
    corrupt = output_dir / "reports" / "report-000000.json"
    corrupt.write_text('{"schemaVersion": ', encoding="utf-8")

    result = consume_gate4_local_bridge_outputs(_config(output_dir))

    assert [handoff.bundle_id for handoff in result.handoffs] == [
        "bundle_local_sink_a",
        "bundle_local_sink_b",
    ]
    assert [(item.path.name, item.reason) for item in result.skipped] == [
        ("report-000000.json", "invalid_json")
    ]


def test_gate4_consumer_rejects_redaction_violation_in_report(tmp_path: Path) -> None:
    output_dir, _bridge = _run_bridge(tmp_path)
    report_path = output_dir / "reports" / "report-000001.json"
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    payload["publicSummary"]["preview"] = "Authorization: Bearer unsafe-token"
    report_path.write_text(json.dumps(payload), encoding="utf-8")

    result = consume_gate4_local_bridge_outputs(_config(output_dir))

    assert [handoff.bundle_id for handoff in result.handoffs] == ["bundle_local_sink_b"]
    assert [(item.path.name, item.reason) for item in result.skipped] == [
        ("report-000001.json", "validation_failed")
    ]
    assert "Bearer unsafe-token" not in result.skipped[0].message


def test_gate4_consumer_skips_report_with_redaction_violation_status(
    tmp_path: Path,
) -> None:
    output_dir, _bridge = _run_bridge(tmp_path)
    report_path = output_dir / "reports" / "report-000001.json"
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    payload["redaction"] = {
        "inputVerified": False,
        "outputVerified": False,
        "violations": ("public summary redaction failed",),
    }
    payload["publicSummary"] = {
        "status": "redaction_violation",
        "preview": "Gate 4 local bridge report contained a redaction violation.",
    }
    report_path.write_text(json.dumps(payload), encoding="utf-8")

    result = consume_gate4_local_bridge_outputs(_config(output_dir))

    assert [handoff.bundle_id for handoff in result.handoffs] == ["bundle_local_sink_b"]
    assert [(item.path.name, item.reason) for item in result.skipped] == [
        ("report-000001.json", "validation_failed")
    ]


def test_gate4_consumer_preserves_duplicate_idempotency_from_bridge_metrics(
    tmp_path: Path,
) -> None:
    output_dir, _bridge = _run_bridge(tmp_path, duplicate=True)

    result = consume_gate4_local_bridge_outputs(_config(output_dir))

    assert [handoff.bundle_id for handoff in result.handoffs] == [
        "bundle_local_sink_a",
        "bundle_local_sink_b",
    ]
    assert result.metrics is not None
    assert result.metrics.counts.duplicate_bundle_ids == 1


def test_gate4_consumer_skips_previously_processed_report_bundle_ids(tmp_path: Path) -> None:
    output_dir, _bridge = _run_bridge(tmp_path)

    result = consume_gate4_local_bridge_outputs(
        _config(output_dir, processedBundleIds=("bundle_local_sink_a",))
    )

    assert [handoff.bundle_id for handoff in result.handoffs] == ["bundle_local_sink_b"]
    assert [(item.path.name, item.reason) for item in result.skipped] == [
        ("report-000001.json", "duplicate_bundle_id")
    ]


@pytest.mark.parametrize("unsafe_value", (True, 1, "true"))
def test_gate4_consumer_rejects_truthy_authority_flags_in_report(
    tmp_path: Path,
    unsafe_value: object,
) -> None:
    output_dir, _bridge = _run_bridge(tmp_path)
    report_path = output_dir / "reports" / "report-000001.json"
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    payload["adkRunnerInvoked"] = unsafe_value
    report_path.write_text(json.dumps(payload), encoding="utf-8")

    result = consume_gate4_local_bridge_outputs(_config(output_dir))

    assert [handoff.bundle_id for handoff in result.handoffs] == ["bundle_local_sink_b"]
    assert [(item.path.name, item.reason) for item in result.skipped] == [
        ("report-000001.json", "validation_failed")
    ]


@pytest.mark.parametrize("authority_key", ("storageWritten", "queueEnqueued"))
@pytest.mark.parametrize("unsafe_value", (True, 1, "true"))
def test_gate4_consumer_rejects_truthy_write_authority_flags_in_report(
    tmp_path: Path,
    authority_key: str,
    unsafe_value: object,
) -> None:
    output_dir, _bridge = _run_bridge(tmp_path)
    report_path = output_dir / "reports" / "report-000001.json"
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    payload[authority_key] = unsafe_value
    report_path.write_text(json.dumps(payload), encoding="utf-8")

    result = consume_gate4_local_bridge_outputs(_config(output_dir))

    assert [handoff.bundle_id for handoff in result.handoffs] == ["bundle_local_sink_b"]
    assert [(item.path.name, item.reason) for item in result.skipped] == [
        ("report-000001.json", "validation_failed")
    ]


@pytest.mark.parametrize("unsafe_value", (True, 1, "true"))
def test_gate4_consumer_rejects_truthy_authority_flags_in_metrics(
    tmp_path: Path,
    unsafe_value: object,
) -> None:
    output_dir, _bridge = _run_bridge(tmp_path)
    metrics_path = output_dir / "metrics" / "metrics-snapshot.json"
    payload = json.loads(metrics_path.read_text(encoding="utf-8"))
    payload["adkRunnerInvoked"] = unsafe_value
    metrics_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValidationError):
        consume_gate4_local_bridge_outputs(_config(output_dir))


def test_gate4_consumer_attachment_flags_cannot_be_enabled_by_model_copy_or_construct(
    tmp_path: Path,
) -> None:
    output_dir, _bridge = _run_bridge(tmp_path)
    result = consume_gate4_local_bridge_outputs(_config(output_dir))

    copied = result.attachment_flags.model_copy(
        update={
            "adkRunnerInvoked": True,
            "modelCalled": True,
            "toolsExecuted": True,
            "productionStorageAttached": True,
            "evidenceBlockEnabled": True,
        }
    )
    constructed = type(result.attachment_flags).model_construct(
        adk_runner_invoked=True,
        model_called=True,
        tools_executed=True,
        production_storage_attached=True,
        evidence_block_enabled=True,
    )

    assert copied.adk_runner_invoked is False
    assert copied.model_called is False
    assert copied.tools_executed is False
    assert copied.production_storage_attached is False
    assert copied.evidence_block_enabled is False
    assert constructed.adk_runner_invoked is False
    assert constructed.model_called is False
    assert constructed.tools_executed is False
    assert constructed.production_storage_attached is False
    assert constructed.evidence_block_enabled is False
