from __future__ import annotations

from copy import deepcopy
import os
import shutil
from pathlib import Path

from magi_agent.shadow.gate3b_local_consumer import (
    Gate3BLocalConsumedBundle,
    Gate3BLocalConsumerConfig,
    consume_gate3b_local_files,
)
from magi_agent.shadow.gate3b_local_report import (
    Gate3BLocalReportAttachmentFlags,
    build_gate3b_local_comparison_reports,
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


def _consume_one(tmp_path: Path) -> Gate3BLocalConsumedBundle:
    capture_dir = _isolated_dir(tmp_path)
    _copy_fixture("valid_bundle_a.json", capture_dir)
    result = consume_gate3b_local_files(
        Gate3BLocalConsumerConfig(enabled=True, input_dir=capture_dir)
    )
    assert len(result.consumed) == 1
    return result.consumed[0]


def test_valid_local_consumed_bundle_builds_runner_free_diagnostic_report(
    tmp_path: Path,
) -> None:
    consumed = _consume_one(tmp_path)

    report = build_gate3b_local_comparison_reports((consumed,))[0]
    payload = report.model_dump(by_alias=True, mode="json")

    assert report.schema_version == "gate3b.localComparisonReport.v1"
    assert report.report_mode == "local_diagnostic_metadata_only"
    assert report.handoff_mode == "gate3b_local_file_to_gate3a_recorded_handoff"
    assert report.bundle_id == "bundle_local_sink_a"
    assert report.source_runtime == "typescript-core-agent"
    assert report.shadow_runtime == "python-adk"
    assert report.recipe_snapshot_id == "recipe_snapshot_gate3b_local_sink_a"
    assert report.parity.handoff_validation == "schema_pass"
    assert report.parity.runner_execution == "not_run"
    assert report.counts.transcript_entries >= 1
    assert report.redaction.input_verified is True
    assert report.redaction.output_verified is True
    assert report.public_summary.status == "schema_pass"
    assert payload["adkRunnerInvoked"] is False
    assert payload["liveShadowExecuted"] is False
    assert payload["toolsExecuted"] is False
    assert payload["shellOrCodeExecuted"] is False
    assert payload["storageWritten"] is False
    assert payload["queueEnqueued"] is False
    assert payload["userVisibleOutputAttached"] is False
    assert payload["evidenceBlockEnabled"] is False
    assert payload["attachmentFlags"]["adkRunnerInvoked"] is False


def test_malformed_handoff_payload_produces_invalid_handoff_diagnostic(
    tmp_path: Path,
) -> None:
    consumed = _consume_one(tmp_path)
    malformed = consumed.model_copy(
        update={"recorded_bundle_payload": {"schemaVersion": "not-gate3a"}}
    )

    report = build_gate3b_local_comparison_reports((malformed,))[0]

    assert report.parity.handoff_validation == "invalid_handoff"
    assert report.public_summary.status == "invalid_handoff"
    assert report.adk_runner_invoked is False
    assert report.live_shadow_executed is False
    assert report.failures == (
        "Gate 3B local handoff failed Gate 3A recorded bundle validation",
    )


def test_report_generation_is_deterministic_for_multiple_consumed_bundles(
    tmp_path: Path,
) -> None:
    capture_dir = _isolated_dir(tmp_path)
    later = _copy_fixture("valid_bundle_b.json", capture_dir, target_name="b.json")
    earlier = _copy_fixture("valid_bundle_a.json", capture_dir, target_name="a.json")
    os.utime(later, ns=(2_000_000_000, 2_000_000_000))
    os.utime(earlier, ns=(1_000_000_000, 1_000_000_000))
    result = consume_gate3b_local_files(
        Gate3BLocalConsumerConfig(enabled=True, input_dir=capture_dir)
    )

    reports = build_gate3b_local_comparison_reports(tuple(reversed(result.consumed)))

    assert [report.bundle_id for report in reports] == [
        "bundle_local_sink_a",
        "bundle_local_sink_b",
    ]


def test_redaction_violation_in_handoff_metadata_is_reported_safely(
    tmp_path: Path,
) -> None:
    consumed = _consume_one(tmp_path)
    unsafe = consumed.model_copy(
        update={
            "handoff_metadata": {
                **consumed.handoff_metadata,
                "unsafeDetail": "Authorization: Bearer unsafe-token",
            }
        }
    )

    report = build_gate3b_local_comparison_reports((unsafe,))[0]
    payload = report.model_dump(by_alias=True, mode="json")

    assert report.parity.handoff_validation == "redaction_violation"
    assert report.public_summary.status == "redaction_violation"
    assert "Bearer unsafe-token" not in repr(payload)
    assert "[REDACTED]" in repr(payload)


def test_live_execution_markers_in_handoff_payload_are_invalid(
    tmp_path: Path,
) -> None:
    consumed = _consume_one(tmp_path)
    payload = deepcopy(consumed.recorded_bundle_payload)
    tool_result = payload["recordedToolResults"][0]
    tool_result["dispatchedLive"] = True
    tool_result["outputMetadata"]["executionSurface"]["shellExecuted"] = True

    report = build_gate3b_local_comparison_reports(
        (consumed.model_copy(update={"recorded_bundle_payload": payload}),)
    )[0]

    assert report.parity.handoff_validation == "invalid_handoff"
    assert report.public_summary.status == "invalid_handoff"
    assert report.adk_runner_invoked is False
    assert report.tools_executed is False
    assert report.shell_or_code_executed is False


def test_malformed_recorded_tool_entries_are_invalid_handoff(
    tmp_path: Path,
) -> None:
    consumed = _consume_one(tmp_path)
    payload = deepcopy(consumed.recorded_bundle_payload)
    payload["recordedToolResults"] = ["not-a-recorded-tool-result"]

    report = build_gate3b_local_comparison_reports(
        (consumed.model_copy(update={"recorded_bundle_payload": payload}),)
    )[0]

    assert report.parity.handoff_validation == "invalid_handoff"
    assert report.public_summary.status == "invalid_handoff"


def test_truthy_live_attachment_aliases_are_invalid_handoff(
    tmp_path: Path,
) -> None:
    consumed = _consume_one(tmp_path)
    payload = deepcopy(consumed.recorded_bundle_payload)
    payload["liveShadowExecuted"] = 1
    payload["storageWritten"] = "true"

    report = build_gate3b_local_comparison_reports(
        (consumed.model_copy(update={"recorded_bundle_payload": payload}),)
    )[0]

    assert report.parity.handoff_validation == "invalid_handoff"
    assert report.public_summary.status == "invalid_handoff"


def test_truthy_live_attachment_aliases_in_handoff_metadata_are_invalid(
    tmp_path: Path,
) -> None:
    consumed = _consume_one(tmp_path)
    unsafe = consumed.model_copy(
        update={
            "handoff_metadata": {
                **consumed.handoff_metadata,
                "liveShadowExecuted": "true",
            }
        }
    )

    report = build_gate3b_local_comparison_reports((unsafe,))[0]

    assert report.parity.handoff_validation == "invalid_handoff"
    assert report.public_summary.status == "invalid_handoff"


def test_non_string_or_credential_keyed_pack_ids_are_invalid_and_redacted(
    tmp_path: Path,
) -> None:
    consumed = _consume_one(tmp_path)
    payload = deepcopy(consumed.recorded_bundle_payload)
    payload["recipe"]["packIds"] = [{"token": "abc123"}]

    report = build_gate3b_local_comparison_reports(
        (consumed.model_copy(update={"recorded_bundle_payload": payload}),)
    )[0]
    dumped = report.model_dump(by_alias=True, mode="json")

    assert report.parity.handoff_validation == "invalid_handoff"
    assert report.public_summary.status == "invalid_handoff"
    assert "abc123" not in repr(dumped)
    assert report.pack_ids == ()


def test_unsafe_source_path_is_redacted_from_public_report_payload(
    tmp_path: Path,
) -> None:
    consumed = _consume_one(tmp_path)
    unsafe = consumed.model_copy(
        update={"source_path": "/private/tmp/openmagi.ai/bot-abc/local.json"}
    )

    report = build_gate3b_local_comparison_reports((unsafe,))[0]
    dumped = report.model_dump(by_alias=True, mode="json")

    assert "/private/tmp" not in repr(dumped)
    assert "openmagi.ai" not in repr(dumped)
    assert "[REDACTED]" in repr(dumped)


def test_attachment_flags_cannot_be_flipped_by_model_copy_or_construct(
    tmp_path: Path,
) -> None:
    report = build_gate3b_local_comparison_reports((_consume_one(tmp_path),))[0]

    copied = report.model_copy(
        update={
            "adkRunnerInvoked": True,
            "liveShadowExecuted": True,
            "attachmentFlags": {
                "adkRunnerInvoked": True,
                "storageWritten": True,
                "queueEnqueued": True,
                "evidenceBlockEnabled": True,
            },
        }
    )
    constructed_flags = Gate3BLocalReportAttachmentFlags.model_construct(
        adk_runner_invoked=True,
        live_shadow_executed=True,
        tools_executed=True,
        storage_written=True,
        queue_enqueued=True,
        evidence_block_enabled=True,
    )

    constructed = type(report).model_construct(
        **{
            **report.model_dump(by_alias=False, mode="python"),
            "adk_runner_invoked": True,
            "live_shadow_executed": True,
            "tools_executed": True,
            "storage_written": True,
            "queue_enqueued": True,
            "evidence_block_enabled": True,
            "attachment_flags": constructed_flags,
        }
    )

    for candidate in (copied, constructed):
        payload = candidate.model_dump(by_alias=True, mode="json")
        assert payload["adkRunnerInvoked"] is False
        assert payload["liveShadowExecuted"] is False
        assert payload["toolsExecuted"] is False
        assert payload["storageWritten"] is False
        assert payload["queueEnqueued"] is False
        assert payload["evidenceBlockEnabled"] is False
        assert all(value is False for value in payload["attachmentFlags"].values())
