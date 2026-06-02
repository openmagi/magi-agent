from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from openmagi_core_agent.shadow.gate3a_bundle import (
    Gate3ARecordedBundle,
    Gate3ARecordedToolResult,
    load_gate3a_recorded_bundle,
)


FIXTURES = Path(__file__).parent / "fixtures" / "gate3a"


def _valid_bundle_payload() -> dict[str, object]:
    return {
        "schemaVersion": "gate3a.recordedBundle.v1",
        "bundleId": "bundle_local_20260516_0001",
        "sourceRuntime": "typescript-core-agent",
        "recordingMode": "recorded_redacted",
        "redactionStatus": "verified",
        "createdAt": "2026-05-16T00:00:00Z",
        "sourceProvenance": {
            "sourceKind": "local_fixture",
            "sourcePath": "local-fixtures/research-basic.jsonl",
            "productionPathIncluded": False,
            "liveCaptureIncluded": False,
        },
        "turn": {
            "sessionRef": "redacted-session",
            "turnId": "turn_local_0001",
            "agentRole": "research",
            "spawnDepth": 0,
            "channel": "local_replay",
        },
        "recipe": {
            "recipeSnapshotId": "recipe_local_research_v1",
            "packIds": ["openmagi.research"],
            "hardSafetyEnabled": True,
        },
        "transcriptEntries": [
            {
                "entryId": "transcript_local_0001",
                "role": "assistant",
                "publicText": "Redacted public answer.",
            }
        ],
        "agentEvents": [
            {
                "eventId": "evt_local_0001",
                "eventType": "text",
                "publicText": "Redacted public answer.",
            }
        ],
        "recordedToolResults": [
            {
                "toolCallId": "tool_call_local_0001",
                "toolName": "search.readonly",
                "status": "recorded",
                "outputMetadata": {"resultCount": 1, "preview": "Redacted result."},
                "dispatchedLive": False,
            }
        ],
        "controlEvents": [],
        "evidenceRecords": [{"recordId": "evidence_local_0001", "summary": "Redacted."}],
    }


def test_valid_recorded_bundle_accepts_camel_case_aliases() -> None:
    bundle = Gate3ARecordedBundle.model_validate(_valid_bundle_payload())

    assert bundle.bundle_id == "bundle_local_20260516_0001"
    assert bundle.source_provenance.live_capture_included is False
    assert bundle.source_provenance.production_path_included is False
    assert bundle.turn.channel == "local_replay"
    assert bundle.recorded_tool_results[0].dispatched_live is False


def test_valid_recorded_bundle_accepts_snake_case_input() -> None:
    payload = _valid_bundle_payload()
    payload["schema_version"] = payload.pop("schemaVersion")
    payload["bundle_id"] = payload.pop("bundleId")
    payload["source_runtime"] = payload.pop("sourceRuntime")
    payload["recording_mode"] = payload.pop("recordingMode")
    payload["redaction_status"] = payload.pop("redactionStatus")
    payload["created_at"] = payload.pop("createdAt")
    payload["source_provenance"] = payload.pop("sourceProvenance")
    payload["source_provenance"]["source_kind"] = payload["source_provenance"].pop("sourceKind")  # type: ignore[index]
    payload["source_provenance"]["source_path"] = payload["source_provenance"].pop("sourcePath")  # type: ignore[index]
    payload["source_provenance"]["production_path_included"] = payload["source_provenance"].pop("productionPathIncluded")  # type: ignore[index]
    payload["source_provenance"]["live_capture_included"] = payload["source_provenance"].pop("liveCaptureIncluded")  # type: ignore[index]
    payload["transcript_entries"] = payload.pop("transcriptEntries")
    payload["agent_events"] = payload.pop("agentEvents")
    payload["recorded_tool_results"] = payload.pop("recordedToolResults")
    payload["control_events"] = payload.pop("controlEvents")
    payload["evidence_records"] = payload.pop("evidenceRecords")

    bundle = Gate3ARecordedBundle.model_validate(payload)

    assert bundle.schema_version == "gate3a.recordedBundle.v1"
    assert bundle.source_provenance.source_kind == "local_fixture"


@pytest.mark.parametrize(
    "mutation",
    (
        pytest.param(
            lambda payload: payload["sourceProvenance"].update({"liveCaptureIncluded": True}),  # type: ignore[index, union-attr]
            id="live-capture-included",
        ),
        pytest.param(
            lambda payload: payload["sourceProvenance"].update({"productionPathIncluded": True}),  # type: ignore[index, union-attr]
            id="production-path-included",
        ),
        pytest.param(
            lambda payload: payload["sourceProvenance"].update({"sourcePath": "/data/bots/bot-123/workspace/transcript.jsonl"}),  # type: ignore[index, union-attr]
            id="production-pvc-workspace-path",
        ),
        pytest.param(
            lambda payload: payload.update({"headers": {"authorization": "Bearer abcdefghijklmnop"}}),
            id="auth-header",
        ),
        pytest.param(
            lambda payload: payload.update({"apiKey": "sk-abcdefghijklmnop"}),
            id="api-key",
        ),
        pytest.param(
            lambda payload: payload.update({"password": "not-redacted"}),
            id="password-key",
        ),
        pytest.param(
            lambda payload: payload["agentEvents"].append({"hiddenReasoning": "private chain of thought"}),  # type: ignore[index, union-attr]
            id="hidden-reasoning",
        ),
        pytest.param(
            lambda payload: payload["recordedToolResults"].append({"privateToolPreview": "raw private tool output"}),  # type: ignore[index, union-attr]
            id="private-tool-preview",
        ),
    ),
)
def test_invalid_bundle_rejects_live_paths_secrets_reasoning_and_private_tool_previews(
    mutation: object,
) -> None:
    payload = _valid_bundle_payload()
    mutation(payload)  # type: ignore[operator]

    with pytest.raises(ValidationError):
        Gate3ARecordedBundle.model_validate(payload)


@pytest.mark.parametrize(
    ("container_key", "dangerous_key"),
    (
        ("transcriptEntries", "liveSurfaceAttached"),
        ("agentEvents", "outputAttachment"),
        ("controlEvents", "childExecution"),
        ("evidenceRecords", "workspaceMutation"),
        ("transcriptEntries", "schedulerRun"),
        ("agentEvents", "customExtractor"),
        ("controlEvents", "signedAck"),
        ("evidenceRecords", "evidenceBlockMode"),
    ),
)
def test_recorded_bundle_rejects_dangerous_nested_mapping_keys(
    container_key: str,
    dangerous_key: str,
) -> None:
    payload = _valid_bundle_payload()
    payload[container_key] = [{"entryId": "local-safe", "metadata": {dangerous_key: False}}]

    with pytest.raises(ValidationError):
        Gate3ARecordedBundle.model_validate(payload)


def test_recorded_bundle_rejects_dangerous_tool_output_metadata_keys() -> None:
    payload = _valid_bundle_payload()
    tool_result = payload["recordedToolResults"][0]  # type: ignore[index]
    tool_result["outputMetadata"] = {"nested": {"productionRouteAttached": False}}  # type: ignore[index]

    with pytest.raises(ValidationError):
        Gate3ARecordedBundle.model_validate(payload)


def test_recorded_bundle_accepts_execution_surface_as_recorded_metadata_only() -> None:
    payload = _valid_bundle_payload()
    tool_result = payload["recordedToolResults"][0]  # type: ignore[index]
    tool_result["outputMetadata"] = {  # type: ignore[index]
        "executionSurface": "generated_code",
        "recordedOnly": True,
        "scriptEvidence": {
            "commandPreview": "redacted local transform",
            "autoExecuted": False,
        },
    }

    bundle = Gate3ARecordedBundle.model_validate(payload)

    assert bundle.recorded_tool_results[0].output_metadata["executionSurface"] == "generated_code"


def test_recorded_bundle_rejects_synthetic_recorded_tool_results() -> None:
    payload = _valid_bundle_payload()
    tool_result = payload["recordedToolResults"][0]  # type: ignore[index]
    tool_result["status"] = "synthetic"  # type: ignore[index]

    with pytest.raises(ValidationError):
        Gate3ARecordedBundle.model_validate(payload)


def test_recorded_tool_result_model_copy_revalidates_status_update() -> None:
    payload = _valid_bundle_payload()
    tool_result_payload = payload["recordedToolResults"][0]  # type: ignore[index]
    tool_result = Gate3ARecordedToolResult.model_validate(tool_result_payload)

    with pytest.raises(ValidationError):
        tool_result.model_copy(update={"status": "synthetic"})


def test_recorded_tool_result_model_construct_dump_forces_recorded_status() -> None:
    tool_result = Gate3ARecordedToolResult.model_construct(
        tool_call_id="tool_call_local_0001",
        tool_name="search.readonly",
        status="synthetic",
        output_metadata={},
        dispatched_live=False,
    )

    assert tool_result.model_dump()["status"] == "recorded"
    assert tool_result.model_dump(mode="json", by_alias=True)["status"] == "recorded"


@pytest.mark.parametrize(
    "output_metadata",
    (
        {"scriptEvidence": {"autoExecuted": True}},
        {"generatedScriptExecuted": True},
    ),
)
def test_recorded_bundle_rejects_positive_execution_claim_metadata(
    output_metadata: dict[str, object],
) -> None:
    payload = _valid_bundle_payload()
    tool_result = payload["recordedToolResults"][0]  # type: ignore[index]
    tool_result["outputMetadata"] = output_metadata  # type: ignore[index]

    with pytest.raises(ValidationError):
        Gate3ARecordedBundle.model_validate(payload)


def test_execution_surface_metadata_cannot_claim_live_execution_attachment() -> None:
    payload = _valid_bundle_payload()
    tool_result = payload["recordedToolResults"][0]  # type: ignore[index]
    tool_result["outputMetadata"] = {  # type: ignore[index]
        "executionSurface": {
            "surface": "generated_code",
            "liveExecutionAttached": False,
        }
    }

    with pytest.raises(ValidationError):
        Gate3ARecordedBundle.model_validate(payload)


def test_recorded_bundle_rejects_false_attachment_keys_outside_typed_schema() -> None:
    payload = _valid_bundle_payload()
    payload["transcriptEntries"] = [
        {"entryId": "local-safe", "metadata": {"productionPathIncluded": False}},
    ]

    with pytest.raises(ValidationError):
        Gate3ARecordedBundle.model_validate(payload)


def test_recorded_bundle_validation_errors_hide_raw_input_values() -> None:
    payload = _valid_bundle_payload()
    raw_path = "/data/bots/bot-123/workspace/transcript.jsonl"
    payload["sourceProvenance"]["sourcePath"] = raw_path  # type: ignore[index]

    with pytest.raises(ValidationError) as exc_info:
        Gate3ARecordedBundle.model_validate(payload)

    assert raw_path not in str(exc_info.value)


def test_valid_recorded_bundle_fixture_loads() -> None:
    bundle = load_gate3a_recorded_bundle(
        "redacted_research_bundle.json",
        bundle_root=FIXTURES,
    )

    assert bundle.bundle_id == "bundle_local_fixture_research"
    assert bundle.redaction_status == "verified"


def test_invalid_recorded_bundle_fixture_fails_before_replay() -> None:
    with pytest.raises(ValidationError):
        load_gate3a_recorded_bundle(
            "redaction_violation_bundle.json",
            bundle_root=FIXTURES,
        )


def test_recorded_bundle_loader_rejects_path_escape_before_opening(tmp_path: Path) -> None:
    root = tmp_path / "fixtures"
    root.mkdir()
    escaped = tmp_path / "escaped.json"
    escaped.write_text(json.dumps(_valid_bundle_payload()), encoding="utf-8")

    with pytest.raises(ValueError, match="bundle_root"):
        load_gate3a_recorded_bundle("../escaped.json", bundle_root=root)


@pytest.mark.parametrize(
    "path",
    (
        "/data/bots/bot-123/recorded.json",
        "/workspace/bot-123/recorded.json",
    ),
)
def test_recorded_bundle_loader_rejects_raw_production_paths_before_opening(
    path: str,
) -> None:
    with pytest.raises(ValueError, match="local-only"):
        load_gate3a_recorded_bundle(path)


@pytest.mark.parametrize(
    "bundle_root",
    (
        "/data/bots/bot-123/gate3a",
        "/workspace/bot-123/gate3a",
    ),
)
def test_recorded_bundle_loader_rejects_production_bundle_roots_before_opening(
    bundle_root: str,
) -> None:
    with pytest.raises(ValueError, match="local-only"):
        load_gate3a_recorded_bundle("recorded.json", bundle_root=bundle_root)


def test_recorded_bundle_loader_rejects_symlinked_production_like_root_before_opening(
    tmp_path: Path,
) -> None:
    production_like_root = tmp_path / "data" / "bots" / "bot-123"
    production_like_root.mkdir(parents=True)
    (production_like_root / "recorded.json").write_text(
        json.dumps(_valid_bundle_payload()),
        encoding="utf-8",
    )
    symlink_root = tmp_path / "local-fixtures"
    symlink_root.symlink_to(production_like_root, target_is_directory=True)

    with pytest.raises(ValueError, match="local-only"):
        load_gate3a_recorded_bundle("recorded.json", bundle_root=symlink_root)
