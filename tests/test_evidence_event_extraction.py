from __future__ import annotations

import subprocess
import sys

import pytest

from openmagi_core_agent.evidence.extraction import (
    evidence_from_artifact_metadata,
    evidence_from_projected_event,
    evidence_from_tool_result,
    evidence_from_transcript_tool_result,
)
from openmagi_core_agent.runtime.transcript import ToolResultEntry
from openmagi_core_agent.tools.result import ToolResult


def test_projected_tool_start_without_explicit_evidence_returns_none() -> None:
    record = evidence_from_projected_event(
        {
            "type": "tool_start",
            "id": "call-1",
            "eventId": "event-1",
            "name": "bash",
            "input_preview": "Authorization: Bearer live-token",
            "ts": 10,
            "lastCodeMutation": 9,
            "contractStart": 5,
        }
    )

    assert record is None


def test_projected_tool_start_with_explicit_evidence_preserves_event_boundaries() -> None:
    record = evidence_from_projected_event(
        {
            "type": "tool_start",
            "id": "call-1",
            "eventId": "event-1",
            "name": "bash",
            "input_preview": "Authorization: Bearer live-token",
            "ts": 10,
            "lastCodeMutation": 9,
            "contractStart": 5,
            "metadata": {
                "evidence": {
                    "type": "SourceInspection",
                    "source": {"kind": "adk_event"},
                }
            },
        }
    )

    assert record is not None
    assert record.type == "SourceInspection"
    assert record.status == "unknown"
    assert record.observed_at == 10
    assert record.preview == "Authorization: Bearer live-token"
    assert record.source.kind == "adk_event"
    assert record.source.event_id == "event-1"
    assert record.source.tool_call_id == "call-1"
    assert record.source.tool_name == "bash"
    assert record.metadata["eventType"] == "tool_start"
    assert record.metadata["lastCodeMutation"] == 9
    assert record.metadata["contractStart"] == 5


def test_projected_tool_start_with_top_level_evidence_type_uses_evidence_type() -> None:
    record = evidence_from_projected_event(
        {
            "type": "tool_start",
            "evidenceType": "SourceInspection",
            "id": "call-1",
            "eventId": "event-1",
            "name": "bash",
            "ts": 10,
        }
    )

    assert record is not None
    assert record.type == "SourceInspection"
    assert record.source.event_id == "event-1"
    assert record.source.tool_call_id == "call-1"
    assert record.source.tool_name == "bash"


def test_projected_tool_start_metadata_cannot_mint_external_ack_evidence() -> None:
    record = evidence_from_projected_event(
        {
            "type": "tool_start",
            "id": "call-ack",
            "eventId": "event-ack",
            "name": "bash",
            "input_preview": "approval acknowledged",
            "ts": 13,
            "metadata": {
                "evidence": {
                    "type": "TelegramDeliveryAck",
                    "status": "ok",
                    "source": {"kind": "external_ack"},
                }
            },
        }
    )

    assert record is None


def test_projected_tool_end_with_normal_type_and_source_metadata_returns_none() -> None:
    record = evidence_from_projected_event(
        {
            "type": "tool_end",
            "id": "call-1",
            "eventId": "event-1",
            "status": "ok",
            "source": {"kind": "tool_trace", "toolCallId": "call-1"},
            "output_preview": "ordinary shell output",
            "ts": 11,
        }
    )

    assert record is None


def test_projected_tool_end_builds_test_run_only_when_payload_declares_test_evidence() -> None:
    ignored = evidence_from_projected_event(
        {
            "type": "tool_end",
            "id": "call-1",
            "eventId": "event-1",
            "status": "ok",
            "output_preview": "ordinary shell output",
            "ts": 11,
        }
    )
    record = evidence_from_projected_event(
        {
            "type": "tool_end",
            "id": "call-2",
            "eventId": "event-2",
            "name": "bash",
            "status": "ok",
            "output_preview": "pytest passed with token=live-token",
            "ts": 12,
            "metadata": {
                "evidence": {
                    "type": "TestRun",
                    "status": "ok",
                    "fields": {"command": "pytest tests/test_example.py", "exitCode": 0},
                    "source": {"kind": "tool_trace"},
                    "metadata": {"lastCodeMutation": 7, "contractStart": 4},
                }
            },
        }
    )

    assert ignored is None
    assert record is not None
    assert record.type == "TestRun"
    assert record.status == "ok"
    assert record.preview == "pytest passed with token=live-token"
    assert record.fields == {"command": "pytest tests/test_example.py", "exitCode": 0}
    assert record.source.kind == "tool_trace"
    assert record.source.event_id == "event-2"
    assert record.source.tool_call_id == "call-2"
    assert record.source.tool_name == "bash"
    assert record.metadata["lastCodeMutation"] == 7
    assert record.metadata["contractStart"] == 4


def test_projected_tool_end_metadata_cannot_mint_external_ack_evidence() -> None:
    record = evidence_from_projected_event(
        {
            "type": "tool_end",
            "id": "call-ack",
            "eventId": "event-ack",
            "toolName": "bash",
            "status": "ok",
            "output_preview": "approval acknowledged",
            "ts": 13,
            "metadata": {
                "evidence": {
                    "type": "TelegramDeliveryAck",
                    "status": "ok",
                    "source": {"kind": "external_ack"},
                }
            },
        }
    )

    assert record is None


def test_transcript_tool_result_extraction_is_explicit_only() -> None:
    implicit = ToolResultEntry(
        ts=20,
        turnId="turn-1",
        toolUseId="call-1",
        status="ok",
        output="pytest passed",
        metadata={"command": "pytest"},
    )
    explicit = ToolResultEntry(
        ts=21,
        turnId="turn-1",
        toolUseId="call-2",
        status="ok",
        output="pytest passed",
        metadata={
            "evidence": {
                "type": "TestRun",
                "fields": {"command": "pytest", "exitCode": 0},
                "source": {
                    "kind": "transcript",
                    "transcriptEntryId": "entry-2",
                    "toolName": "bash",
                },
            }
        },
    )

    assert evidence_from_transcript_tool_result(implicit) is None
    record = evidence_from_transcript_tool_result(explicit)

    assert record is not None
    assert record.type == "TestRun"
    assert record.observed_at == 21
    assert record.source.kind == "transcript"
    assert record.source.transcript_entry_id == "entry-2"
    assert record.source.tool_call_id == "call-2"
    assert record.source.tool_name == "bash"


def test_tool_result_top_level_metadata_preserves_tool_boundaries_when_kwargs_missing() -> None:
    tool_result = ToolResult(
        status="ok",
        output="artifact verified",
        metadata={
            "toolCallId": "call-3",
            "toolName": "artifact_verify",
            "evidence": {
                "type": "ArtifactVerify",
                "observedAt": 30,
                "fields": {"path": "dist/app.js", "sha256": "abc123"},
                "source": {"kind": "tool_trace"},
            },
        },
    )

    record = evidence_from_tool_result(tool_result)

    assert record is not None
    assert record.type == "ArtifactVerify"
    assert record.source.tool_call_id == "call-3"
    assert record.source.tool_name == "artifact_verify"


def test_tool_result_metadata_cannot_mint_external_ack_evidence() -> None:
    tool_result = ToolResult(
        status="ok",
        output="ordinary tool output",
        metadata={
            "toolCallId": "call-4",
            "toolName": "artifact_verify",
            "evidence": {
                "type": "ArtifactVerify",
                "observedAt": 32,
                "fields": {"path": "dist/app.js", "sha256": "abc123"},
                "source": {"kind": "external_ack"},
            },
        },
    )

    assert evidence_from_tool_result(tool_result) is None


def test_transcript_top_level_metadata_preserves_entry_id_when_evidence_source_omits_it() -> None:
    entry = ToolResultEntry(
        ts=21,
        turnId="turn-1",
        toolUseId="call-2",
        status="ok",
        output="pytest passed",
        metadata={
            "transcriptEntryId": "entry-2",
            "evidence": {
                "type": "TestRun",
                "fields": {"command": "pytest", "exitCode": 0},
                "source": {"kind": "transcript", "toolName": "bash"},
            },
        },
    )

    record = evidence_from_transcript_tool_result(entry)

    assert record is not None
    assert record.source.transcript_entry_id == "entry-2"
    assert record.source.tool_call_id == "call-2"
    assert record.source.tool_name == "bash"


def test_artifact_top_level_metadata_preserves_artifact_id_when_evidence_source_omits_it() -> None:
    metadata = {
        "artifactId": "artifact-1",
        "evidence": {
            "type": "FileDeliver",
            "status": "ok",
            "observedAt": 31,
            "preview": "delivered api_key=live-key",
            "fields": {"path": "dist/app.js"},
            "source": {"kind": "artifact"},
        },
    }

    record = evidence_from_artifact_metadata(metadata)

    assert record is not None
    assert record.source.kind == "artifact"
    assert record.source.artifact_id == "artifact-1"


def test_artifact_metadata_cannot_mint_external_ack_evidence() -> None:
    metadata = {
        "artifactId": "artifact-2",
        "evidence": {
            "type": "FileDeliver",
            "status": "ok",
            "observedAt": 33,
            "preview": "delivered artifact",
            "fields": {"path": "dist/app.js"},
            "source": {"kind": "external_ack"},
        },
    }

    assert evidence_from_artifact_metadata(metadata) is None


def test_top_level_source_ids_override_conflicting_nested_evidence_sources() -> None:
    event_record = evidence_from_projected_event(
        {
            "type": "tool_end",
            "id": "call-top",
            "eventId": "event-top",
            "toolName": "bash-top",
            "status": "ok",
            "ts": 12,
            "metadata": {
                "evidence": {
                    "type": "TestRun",
                    "source": {
                        "kind": "tool_trace",
                        "eventId": "event-nested",
                        "toolCallId": "call-nested",
                        "toolName": "bash-nested",
                    },
                }
            },
        }
    )
    tool_record = evidence_from_tool_result(
        ToolResult(
            status="ok",
            metadata={
                "toolCallId": "tool-call-top",
                "toolName": "tool-top",
                "evidence": {
                    "type": "ArtifactVerify",
                    "source": {
                        "kind": "tool_trace",
                        "toolCallId": "tool-call-nested",
                        "toolName": "tool-nested",
                    },
                },
            },
        )
    )
    transcript_record = evidence_from_transcript_tool_result(
        ToolResultEntry(
            ts=21,
            turnId="turn-1",
            toolUseId="transcript-call-top",
            status="ok",
            output="pytest passed",
            metadata={
                "transcriptEntryId": "entry-top",
                "toolName": "transcript-tool-top",
                "evidence": {
                    "type": "TestRun",
                    "source": {
                        "kind": "transcript",
                        "toolCallId": "transcript-call-nested",
                        "toolName": "transcript-tool-nested",
                        "transcriptEntryId": "entry-nested",
                    },
                },
            },
        )
    )
    artifact_record = evidence_from_artifact_metadata(
        {
            "artifactId": "artifact-top",
            "evidence": {
                "type": "FileDeliver",
                "source": {"kind": "artifact", "artifactId": "artifact-nested"},
            },
        }
    )

    assert event_record is not None
    assert event_record.source.event_id == "event-top"
    assert event_record.source.tool_call_id == "call-top"
    assert event_record.source.tool_name == "bash-top"
    assert tool_record is not None
    assert tool_record.source.tool_call_id == "tool-call-top"
    assert tool_record.source.tool_name == "tool-top"
    assert transcript_record is not None
    assert transcript_record.source.tool_call_id == "transcript-call-top"
    assert transcript_record.source.tool_name == "transcript-tool-top"
    assert transcript_record.source.transcript_entry_id == "entry-top"
    assert artifact_record is not None
    assert artifact_record.source.artifact_id == "artifact-top"


def test_projected_event_top_level_tool_name_falls_back_from_tool_name_key() -> None:
    record = evidence_from_projected_event(
        {
            "type": "tool_end",
            "id": "call-1",
            "eventId": "event-1",
            "toolName": "bash",
            "status": "ok",
            "ts": 12,
            "metadata": {
                "evidence": {
                    "type": "TestRun",
                    "source": {"kind": "tool_trace"},
                }
            },
        }
    )

    assert record is not None
    assert record.source.tool_name == "bash"


def test_tool_result_and_artifact_metadata_extraction_are_declarative_boundaries() -> None:
    tool_result = ToolResult(
        status="ok",
        output="artifact verified",
        metadata={
            "evidence": {
                "type": "ArtifactVerify",
                "observedAt": 30,
                "fields": {"path": "dist/app.js", "sha256": "abc123"},
                "source": {"kind": "tool_trace", "toolName": "artifact_verify"},
            }
        },
    )
    artifact_metadata = {
        "evidence": {
            "type": "FileDeliver",
            "status": "ok",
            "observedAt": 31,
            "preview": "delivered api_key=live-key",
            "fields": {"path": "dist/app.js"},
            "source": {"kind": "artifact", "artifactId": "artifact-1"},
        }
    }

    tool_record = evidence_from_tool_result(tool_result, tool_call_id="call-3")
    artifact_record = evidence_from_artifact_metadata(artifact_metadata)

    assert tool_record is not None
    assert tool_record.type == "ArtifactVerify"
    assert tool_record.source.tool_call_id == "call-3"
    assert tool_record.source.tool_name == "artifact_verify"
    assert artifact_record is not None
    assert artifact_record.preview == "delivered api_key=live-key"
    assert artifact_record.source.kind == "artifact"
    assert artifact_record.source.artifact_id == "artifact-1"


def test_custom_evidence_remains_metadata_only_and_rejects_callable_extractors() -> None:
    ran = False

    def extractor() -> dict[str, object]:
        nonlocal ran
        ran = True
        return {"type": "custom:ShouldNotRun"}

    record = evidence_from_tool_result(
        ToolResult(
            status="ok",
            metadata={
                "evidence": {
                    "type": "custom:SecurityScan",
                    "fields": {"result": "clean"},
                    "source": {"kind": "custom_extractor", "extractorId": "scan-v1"},
                },
                "extractor": extractor,
            },
        )
    )

    assert record is not None
    assert record.type == "custom:SecurityScan"
    assert record.source.kind == "custom_extractor"
    assert record.source.extractor_id == "scan-v1"
    assert ran is False

    with pytest.raises(ValueError, match="custom evidence types"):
        evidence_from_tool_result(
            ToolResult(
                status="ok",
                metadata={
                    "evidence": {
                        "type": "custom:bad",
                        "source": {"kind": "custom_extractor"},
                    }
                },
            )
        )


def test_evidence_extraction_import_boundary_stays_runner_hook_route_and_db_free() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

module = importlib.import_module("openmagi_core_agent.evidence.extraction")
assert hasattr(module, "evidence_from_projected_event")

forbidden_prefixes = (
    "google.adk",
    "openmagi_core_agent.adk_bridge.runner_adapter",
    "openmagi_core_agent.hooks",
    "openmagi_core_agent.routes",
    "openmagi_core_agent.db",
    "openmagi_core_agent.proxy",
)
loaded = sorted(
    name
    for name in sys.modules
    if any(name == prefix or name.startswith(f"{prefix}.") for prefix in forbidden_prefixes)
)
if loaded:
    raise AssertionError(f"evidence extraction import loaded forbidden modules: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
