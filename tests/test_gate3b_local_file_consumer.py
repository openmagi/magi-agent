from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from openmagi_core_agent.shadow.gate3b_local_consumer import (
    Gate3BLocalConsumerAttachmentFlags,
    Gate3BLocalConsumerConfig,
    Gate3BLocalConsumerError,
    consume_gate3b_local_files,
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


def _config(path: Path, **overrides: object) -> Gate3BLocalConsumerConfig:
    values = {
        "enabled": True,
        "input_dir": path,
        "max_files": 100,
        "max_total_bytes": 10_485_760,
        "max_bundle_bytes": 262_144,
        **overrides,
    }
    return Gate3BLocalConsumerConfig(**values)


def test_consumer_is_default_off_even_when_input_dir_is_present(tmp_path: Path) -> None:
    capture_dir = _isolated_dir(tmp_path)
    _copy_fixture("valid_bundle_a.json", capture_dir)

    result = consume_gate3b_local_files(
        Gate3BLocalConsumerConfig(enabled=False, input_dir=capture_dir)
    )

    assert result.consumed == ()
    assert result.skipped == ()
    assert result.attachment_flags.live_shadow_executed is False
    assert result.attachment_flags.adk_runner_invoked is False


def test_valid_local_sink_bundles_convert_to_gate3a_handoff_in_deterministic_order(
    tmp_path: Path,
) -> None:
    capture_dir = _isolated_dir(tmp_path)
    later = _copy_fixture("valid_bundle_b.json", capture_dir, target_name="b.json")
    earlier = _copy_fixture("valid_bundle_a.json", capture_dir, target_name="a.json")
    os.utime(later, ns=(2_000_000_000, 2_000_000_000))
    os.utime(earlier, ns=(1_000_000_000, 1_000_000_000))

    result = consume_gate3b_local_files(_config(capture_dir))

    assert [item.bundle_id for item in result.consumed] == [
        "bundle_local_sink_a",
        "bundle_local_sink_b",
    ]
    assert result.skipped == ()
    assert result.consumed[0].handoff_mode == "gate3b_local_file_to_gate3a_recorded_handoff"
    assert result.consumed[0].recorded_bundle_payload["schemaVersion"] == (
        "gate3a.recordedBundle.v1"
    )
    assert result.consumed[0].handoff_metadata["adkRunnerInvoked"] is False
    assert result.consumed[0].handoff_metadata["toolsExecuted"] is False
    assert result.consumed[0].handoff_metadata["liveShadowExecuted"] is False
    assert result.consumed[0].handoff_metadata["storageWritten"] is False
    assert result.consumed[0].handoff_metadata["queueEnqueued"] is False


@pytest.mark.parametrize(
    "path_text",
    (
        "/data/bots/bot-1/adk-shadow-capture",
        "/workspace/adk-shadow-capture",
        "/var/lib/kubelet/pods/adk-shadow-capture",
        "postgresql://db.example/gate3b",
        "s3://bucket/gate3b",
    ),
)
def test_rejects_non_isolated_or_production_like_input_paths(path_text: str) -> None:
    with pytest.raises(Gate3BLocalConsumerError):
        consume_gate3b_local_files(_config(Path(path_text)))


def test_rejects_plain_temp_path_without_isolated_capture_segment(tmp_path: Path) -> None:
    plain_dir = tmp_path / "ordinary"
    plain_dir.mkdir()

    with pytest.raises(Gate3BLocalConsumerError):
        consume_gate3b_local_files(_config(plain_dir))


def test_rejects_symlinked_bundle_files_without_reading_target(tmp_path: Path) -> None:
    capture_dir = _isolated_dir(tmp_path)
    target = FIXTURES / "valid_bundle_a.json"
    symlink = capture_dir / "linked.json"
    symlink.symlink_to(target)

    result = consume_gate3b_local_files(_config(capture_dir))

    assert result.consumed == ()
    assert [(item.path.name, item.reason) for item in result.skipped] == [
        ("linked.json", "symlink_not_allowed")
    ]


def test_rejects_redaction_violations_as_skipped_diagnostics(tmp_path: Path) -> None:
    capture_dir = _isolated_dir(tmp_path)
    _copy_fixture("redaction_violation.json", capture_dir)

    result = consume_gate3b_local_files(_config(capture_dir))

    assert result.consumed == ()
    assert [item.reason for item in result.skipped] == ["validation_failed"]
    assert "Bearer unsafe-token" not in result.skipped[0].message


def test_duplicate_bundle_ids_are_idempotent(tmp_path: Path) -> None:
    capture_dir = _isolated_dir(tmp_path)
    first = _copy_fixture("valid_bundle_a.json", capture_dir, target_name="first.json")
    duplicate = _copy_fixture("valid_bundle_a.json", capture_dir, target_name="second.json")
    os.utime(first, ns=(1_000_000_000, 1_000_000_000))
    os.utime(duplicate, ns=(2_000_000_000, 2_000_000_000))

    result = consume_gate3b_local_files(_config(capture_dir))

    assert [item.bundle_id for item in result.consumed] == ["bundle_local_sink_a"]
    assert [(item.path.name, item.reason) for item in result.skipped] == [
        ("second.json", "duplicate_bundle_id")
    ]


def test_previously_processed_bundle_ids_are_skipped(tmp_path: Path) -> None:
    capture_dir = _isolated_dir(tmp_path)
    _copy_fixture("valid_bundle_a.json", capture_dir)

    result = consume_gate3b_local_files(
        _config(capture_dir, processed_bundle_ids=("bundle_local_sink_a",))
    )

    assert result.consumed == ()
    assert [item.reason for item in result.skipped] == ["duplicate_bundle_id"]


def test_corrupted_partial_json_is_skipped_without_blocking_other_files(tmp_path: Path) -> None:
    capture_dir = _isolated_dir(tmp_path)
    _copy_fixture("corrupted_partial.json", capture_dir, target_name="a-corrupt.json")
    _copy_fixture("valid_bundle_b.json", capture_dir, target_name="b-valid.json")

    result = consume_gate3b_local_files(_config(capture_dir))

    assert [item.bundle_id for item in result.consumed] == ["bundle_local_sink_b"]
    assert [(item.path.name, item.reason) for item in result.skipped] == [
        ("a-corrupt.json", "invalid_json")
    ]


def test_file_and_byte_caps_are_enforced_before_payload_validation(tmp_path: Path) -> None:
    capture_dir = _isolated_dir(tmp_path)
    _copy_fixture("valid_bundle_a.json", capture_dir, target_name="a.json")
    _copy_fixture("valid_bundle_b.json", capture_dir, target_name="b.json")

    file_limited = consume_gate3b_local_files(_config(capture_dir, max_files=1))
    assert len(file_limited.consumed) == 1
    assert [item.reason for item in file_limited.skipped] == ["file_limit_exceeded"]

    oversized = consume_gate3b_local_files(_config(capture_dir, max_bundle_bytes=10))
    assert oversized.consumed == ()
    assert {item.reason for item in oversized.skipped} == {"file_too_large"}

    total_limited = consume_gate3b_local_files(_config(capture_dir, max_total_bytes=1000))
    assert total_limited.consumed == ()
    assert {item.reason for item in total_limited.skipped} == {"total_bytes_exceeded"}


def test_invalid_and_duplicate_files_still_consume_total_byte_budget(
    tmp_path: Path,
) -> None:
    capture_dir = _isolated_dir(tmp_path)
    invalid = _copy_fixture(
        "redaction_violation.json",
        capture_dir,
        target_name="a-invalid.json",
    )
    valid = _copy_fixture("valid_bundle_a.json", capture_dir, target_name="b-valid.json")
    os.utime(invalid, ns=(1_000_000_000, 1_000_000_000))
    os.utime(valid, ns=(2_000_000_000, 2_000_000_000))

    result = consume_gate3b_local_files(_config(capture_dir, max_total_bytes=2500))

    assert result.consumed == ()
    assert [(item.path.name, item.reason) for item in result.skipped] == [
        ("a-invalid.json", "validation_failed"),
        ("b-valid.json", "total_bytes_exceeded"),
    ]

    duplicate_dir = _isolated_dir(tmp_path / "duplicate")
    duplicate_dir.mkdir(parents=True, exist_ok=True)
    duplicate = _copy_fixture("valid_bundle_a.json", duplicate_dir, target_name="a.json")
    later_valid = _copy_fixture("valid_bundle_b.json", duplicate_dir, target_name="b.json")
    os.utime(duplicate, ns=(1_000_000_000, 1_000_000_000))
    os.utime(later_valid, ns=(2_000_000_000, 2_000_000_000))

    duplicate_result = consume_gate3b_local_files(
        _config(
            duplicate_dir,
            max_total_bytes=3000,
            processed_bundle_ids=("bundle_local_sink_a",),
        )
    )

    assert duplicate_result.consumed == ()
    assert [(item.path.name, item.reason) for item in duplicate_result.skipped] == [
        ("a.json", "duplicate_bundle_id"),
        ("b.json", "total_bytes_exceeded"),
    ]


def test_invalid_utf8_json_is_skipped_without_blocking_other_files(tmp_path: Path) -> None:
    capture_dir = _isolated_dir(tmp_path)
    invalid = capture_dir / "a-invalid-utf8.json"
    invalid.write_bytes(b'{"schemaVersion": "\xff"}')
    valid = _copy_fixture("valid_bundle_b.json", capture_dir, target_name="b-valid.json")
    os.utime(invalid, ns=(1_000_000_000, 1_000_000_000))
    os.utime(valid, ns=(2_000_000_000, 2_000_000_000))

    result = consume_gate3b_local_files(_config(capture_dir))

    assert [item.bundle_id for item in result.consumed] == ["bundle_local_sink_b"]
    assert [(item.path.name, item.reason) for item in result.skipped] == [
        ("a-invalid-utf8.json", "invalid_json")
    ]


def test_model_copy_cannot_enable_live_attachment_flags(tmp_path: Path) -> None:
    capture_dir = _isolated_dir(tmp_path)
    _copy_fixture("valid_bundle_a.json", capture_dir)
    result = consume_gate3b_local_files(_config(capture_dir))

    copied = result.attachment_flags.model_copy(update={"adkRunnerInvoked": True})

    assert copied.adk_runner_invoked is False
    assert copied.live_shadow_executed is False


def test_model_construct_cannot_enable_live_attachment_flags() -> None:
    flags = Gate3BLocalConsumerAttachmentFlags.model_construct(
        adk_runner_invoked=True,
        live_shadow_executed=True,
        tools_executed=True,
        production_storage_written=True,
        production_queue_enqueued=True,
        evidence_block_enabled=True,
    )

    assert flags.adk_runner_invoked is False
    assert flags.live_shadow_executed is False
    assert flags.tools_executed is False
    assert flags.production_storage_written is False
    assert flags.production_queue_enqueued is False
    assert flags.evidence_block_enabled is False
    assert all(
        value is False
        for value in flags.model_dump(by_alias=True, mode="json").values()
    )


def test_consumer_never_writes_acknowledgement_or_processed_state(tmp_path: Path) -> None:
    capture_dir = _isolated_dir(tmp_path)
    original = _copy_fixture("valid_bundle_a.json", capture_dir)
    before = json.loads(original.read_text(encoding="utf-8"))

    consume_gate3b_local_files(_config(capture_dir))

    assert json.loads(original.read_text(encoding="utf-8")) == before
    assert sorted(path.name for path in capture_dir.iterdir()) == ["valid_bundle_a.json"]
