from __future__ import annotations

from collections.abc import Mapping

from magi_agent.evidence.types import EvidenceRecord, EvidenceSource
from magi_agent.runtime.transcript import ToolResultEntry
from magi_agent.tools.result import ToolResult


_EVIDENCE_KEY = "evidence"
_BOUNDARY_METADATA_KEYS = ("lastCodeMutation", "contractStart")
_TEST_COMMAND_PREFIXES = (
    "pytest",
    "python -m pytest",
    "npm test",
    "npm run test",
    "pnpm test",
    "pnpm run test",
    "yarn test",
)


def evidence_from_projected_event(event: Mapping[str, object]) -> EvidenceRecord | None:
    event_type = _string_value(event.get("type"))
    if event_type == "tool_start":
        metadata = _mapping_value(event.get("metadata"))
        declaration = _explicit_evidence_declaration(event) or _explicit_evidence_declaration(
            metadata
        )
        if declaration is None:
            return None
        if _declares_source_kind(declaration, "external_ack"):
            return None
        return _record_from_declaration(
            declaration,
            observed_at=_observed_at(event),
            status=_status_from_event(event),
            preview=_event_preview(event),
            default_source_kind="adk_event",
            source_overrides=_source_overrides_from_event(event),
            boundary_metadata={"eventType": "tool_start", **_boundary_metadata_from(event)},
        )
    if event_type != "tool_end":
        return None

    metadata = _mapping_value(event.get("metadata"))
    declaration = _explicit_evidence_declaration(event) or _explicit_evidence_declaration(
        metadata
    )
    if declaration is None and not _projected_event_declares_test_run(event):
        return None
    if declaration is not None and _declares_source_kind(declaration, "external_ack"):
        return None
    if declaration is None:
        declaration = {
            "type": "TestRun",
            "fields": _test_fields_from_projected_event(event),
        }
    return _record_from_declaration(
        declaration,
        observed_at=_observed_at(event),
        status=_status_from_event(event),
        preview=_event_preview(event),
        default_source_kind="tool_trace",
        source_overrides=_source_overrides_from_event(event),
        boundary_metadata=_boundary_metadata_from(event),
    )


def evidence_from_transcript_tool_result(entry: ToolResultEntry) -> EvidenceRecord | None:
    declaration = _explicit_evidence_declaration(entry.metadata or {})
    if declaration is None:
        return None
    return _record_from_declaration(
        declaration,
        observed_at=entry.ts,
        status=_status_from_tool_status(entry.status),
        preview=entry.output,
        default_source_kind="transcript",
        source_overrides={
            "toolCallId": entry.tool_use_id,
            "toolName": _string_value((entry.metadata or {}).get("toolName")),
            "transcriptEntryId": _string_value((entry.metadata or {}).get("transcriptEntryId"))
            or _string_value(declaration.get("transcriptEntryId")),
        },
        boundary_metadata=_boundary_metadata_from(entry.metadata or {}),
    )


def evidence_from_tool_result(
    result: ToolResult,
    *,
    tool_call_id: str | None = None,
    tool_name: str | None = None,
) -> EvidenceRecord | None:
    declaration = _explicit_evidence_declaration(result.metadata)
    if declaration is None:
        return None
    if _declares_source_kind(declaration, "external_ack"):
        return None
    return _record_from_declaration(
        declaration,
        observed_at=_observed_at(declaration),
        status=_status_from_tool_status(result.status),
        preview=_preview_from_tool_result(result),
        default_source_kind="tool_trace",
        source_overrides={
            "toolCallId": tool_call_id or _string_value(result.metadata.get("toolCallId")),
            "toolName": tool_name or _string_value(result.metadata.get("toolName")),
        },
        boundary_metadata=_boundary_metadata_from(result.metadata),
    )


def evidence_from_artifact_metadata(metadata: Mapping[str, object]) -> EvidenceRecord | None:
    declaration = _explicit_evidence_declaration(metadata)
    if declaration is None:
        return None
    if _declares_source_kind(declaration, "external_ack"):
        return None
    return _record_from_declaration(
        declaration,
        observed_at=_observed_at(declaration),
        status=_status_from_declaration(declaration),
        preview=_string_value(declaration.get("preview")),
        default_source_kind="artifact",
        source_overrides={"artifactId": _string_value(metadata.get("artifactId"))},
        boundary_metadata=_boundary_metadata_from(metadata),
    )


def _record_from_declaration(
    declaration: Mapping[str, object],
    *,
    observed_at: int | float,
    status: str,
    preview: str | None,
    default_source_kind: str,
    source_overrides: Mapping[str, str | None],
    boundary_metadata: Mapping[str, object],
) -> EvidenceRecord:
    source_payload = _source_payload(
        declaration.get("source"),
        default_kind=default_source_kind,
        overrides=source_overrides,
    )
    metadata = _metadata_from_declaration(declaration)
    metadata.update(boundary_metadata)
    return EvidenceRecord(
        type=_required_string(_evidence_type_from_declaration(declaration), "evidence.type"),
        status=status,
        observedAt=_observed_at(declaration, fallback=observed_at),
        preview=_string_value(declaration.get("preview")) or preview,
        fields=_mapping_value(declaration.get("fields")),
        source=EvidenceSource.model_validate(source_payload),
        metadata=metadata,
    )


def _explicit_evidence_declaration(source: Mapping[str, object]) -> Mapping[str, object] | None:
    nested = source.get(_EVIDENCE_KEY)
    if isinstance(nested, Mapping):
        return nested
    evidence_type = source.get("evidenceType")
    if isinstance(evidence_type, str):
        return source
    return None


def _declares_source_kind(declaration: Mapping[str, object], kind: str) -> bool:
    source = _mapping_value(declaration.get("source"))
    return source.get("kind") == kind


def _evidence_type_from_declaration(declaration: Mapping[str, object]) -> object:
    return declaration.get("evidenceType") or declaration.get("type")


def _projected_event_declares_test_run(event: Mapping[str, object]) -> bool:
    metadata = _mapping_value(event.get("metadata"))
    declaration = _explicit_evidence_declaration(metadata)
    if declaration is not None and declaration.get("type") == "TestRun":
        return True
    fields = _mapping_value(metadata.get("fields"))
    command = _string_value(metadata.get("command")) or _string_value(fields.get("command"))
    return _is_test_command(command)


def _test_fields_from_projected_event(event: Mapping[str, object]) -> dict[str, object]:
    metadata = _mapping_value(event.get("metadata"))
    fields = dict(_mapping_value(metadata.get("fields")))
    command = _string_value(metadata.get("command"))
    exit_code = metadata.get("exitCode")
    if command and "command" not in fields:
        fields["command"] = command
    if exit_code is not None and "exitCode" not in fields:
        fields["exitCode"] = exit_code
    return fields


def _is_test_command(command: str | None) -> bool:
    if command is None:
        return False
    normalized = " ".join(command.strip().split()).lower()
    return any(
        normalized == prefix or normalized.startswith(f"{prefix} ")
        for prefix in _TEST_COMMAND_PREFIXES
    )


def _source_payload(
    raw_source: object,
    *,
    default_kind: str,
    overrides: Mapping[str, str | None],
) -> dict[str, object]:
    payload: dict[str, object] = {"kind": default_kind}
    if isinstance(raw_source, Mapping):
        payload.update({key: value for key, value in raw_source.items() if value is not None})
    for key, value in overrides.items():
        if value is not None:
            payload[key] = value
    return payload


def _source_overrides_from_event(event: Mapping[str, object]) -> dict[str, str | None]:
    return {
        "eventId": _string_value(event.get("eventId")),
        "toolCallId": _tool_call_id_from_event(event),
        "toolName": _string_value(event.get("toolName")) or _string_value(event.get("name")),
    }


def _tool_call_id_from_event(event: Mapping[str, object]) -> str | None:
    return _string_value(event.get("toolCallId")) or _string_value(event.get("id"))


def _metadata_from_declaration(declaration: Mapping[str, object]) -> dict[str, object]:
    metadata = dict(_mapping_value(declaration.get("metadata")))
    for key in _BOUNDARY_METADATA_KEYS:
        if key in declaration:
            metadata[key] = declaration[key]
    return metadata


def _boundary_metadata_from(source: Mapping[str, object]) -> dict[str, object]:
    metadata = dict(_mapping_value(source.get("metadata")))
    return {
        key: value
        for key in _BOUNDARY_METADATA_KEYS
        if (value := source.get(key, metadata.get(key))) is not None
    }


def _status_from_event(event: Mapping[str, object]) -> str:
    return _status_from_tool_status(_string_value(event.get("status")) or "unknown")


def _status_from_declaration(declaration: Mapping[str, object]) -> str:
    return _status_from_tool_status(_string_value(declaration.get("status")) or "unknown")


def _status_from_tool_status(status: str) -> str:
    if status == "ok":
        return "ok"
    if status in {"error", "failed", "blocked"}:
        return "failed"
    return "unknown"


def _observed_at(source: Mapping[str, object], fallback: int | float = 0) -> int | float:
    value = source.get("observedAt", source.get("observed_at", source.get("ts", fallback)))
    if isinstance(value, bool) or not isinstance(value, int | float):
        return fallback
    return value


def _event_preview(event: Mapping[str, object]) -> str | None:
    return _string_value(event.get("output_preview")) or _string_value(event.get("input_preview"))


def _preview_from_tool_result(result: ToolResult) -> str | None:
    for value in (result.transcript_output, result.llm_output, result.output, result.error_message):
        if value is not None:
            return value if isinstance(value, str) else repr(value)
    return None


def _mapping_value(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _string_value(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _required_string(value: object, field_name: str) -> str:
    text = _string_value(value)
    if text is None:
        raise ValueError(f"{field_name} must be declared")
    return text


__all__ = [
    "evidence_from_artifact_metadata",
    "evidence_from_projected_event",
    "evidence_from_tool_result",
    "evidence_from_transcript_tool_result",
]
