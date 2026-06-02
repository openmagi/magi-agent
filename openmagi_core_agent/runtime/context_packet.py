from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from openmagi_core_agent.runtime.session_continuity_projection import (
    ProjectedTranscriptEvent,
    project_transcript_entries,
    safe_projected_metadata_ref_text,
    safe_projected_text,
)
from openmagi_core_agent.runtime.session_continuity_proof import (
    has_valid_compaction_boundary,
    has_session_continuity_marker,
    latest_valid_compacted_batch,
    session_continuity_kind,
    validate_session_continuity_proof,
)


_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)


class _ContextPacketModel(BaseModel):
    model_config = _MODEL_CONFIG

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        return cls(**values)


class ContextContinuityConfig(_ContextPacketModel):
    enabled: bool = False
    max_imported_events: int = Field(default=64, ge=1, le=512, alias="maxImportedEvents")
    max_rendered_chars: int = Field(
        default=24_000,
        ge=1_000,
        le=96_000,
        alias="maxRenderedChars",
    )


class ContextContinuityAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    transcript_write_allowed: Literal[False] = Field(
        default=False,
        alias="transcriptWriteAllowed",
    )
    sse_write_allowed: Literal[False] = Field(default=False, alias="sseWriteAllowed")
    db_write_allowed: Literal[False] = Field(default=False, alias="dbWriteAllowed")
    memory_write_allowed: Literal[False] = Field(
        default=False,
        alias="memoryWriteAllowed",
    )
    workspace_mutation_allowed: Literal[False] = Field(
        default=False,
        alias="workspaceMutationAllowed",
    )
    child_execution_allowed: Literal[False] = Field(
        default=False,
        alias="childExecutionAllowed",
    )
    channel_delivery_allowed: Literal[False] = Field(
        default=False,
        alias="channelDeliveryAllowed",
    )

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: object,
    ) -> Self:
        return cls()

    @model_validator(mode="before")
    @classmethod
    def _force_false(cls, value: object) -> object:
        if isinstance(value, Mapping):
            return {field.alias or name: False for name, field in cls.model_fields.items()}
        return value

    @field_serializer(
        "transcript_write_allowed",
        "sse_write_allowed",
        "db_write_allowed",
        "memory_write_allowed",
        "workspace_mutation_allowed",
        "child_execution_allowed",
        "channel_delivery_allowed",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class ContextEvent(_ContextPacketModel):
    role: Literal["user", "assistant", "system"]
    turn_id: str = Field(alias="turnId")
    text: str
    source: Literal["transcript", "compaction_summary", "metadata_ref"]


class ContextAttachment(_ContextPacketModel):
    kind: str
    label: str
    text: str
    source_ref: str | None = Field(default=None, alias="sourceRef")


class ContextContinuityDiagnostics(_ContextPacketModel):
    imported_event_count: int = Field(default=0, ge=0, alias="importedEventCount")
    rejected_entry_count: int = Field(default=0, ge=0, alias="rejectedEntryCount")
    compaction_applied: bool = Field(default=False, alias="compactionApplied")
    dropped_pre_boundary_count: int = Field(
        default=0,
        ge=0,
        alias="droppedPreBoundaryCount",
    )
    budget_truncated: bool = Field(default=False, alias="budgetTruncated")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")


class ConversationContextPacket(_ContextPacketModel):
    schema_version: Literal["openmagi.contextContinuity.v1"] = Field(
        default="openmagi.contextContinuity.v1",
        alias="schemaVersion",
    )
    enabled: bool
    local_only: Literal[True] = Field(default=True, alias="localOnly")
    diagnostic_only: Literal[True] = Field(default=True, alias="diagnosticOnly")
    response_authority: Literal["none"] = Field(default="none", alias="responseAuthority")
    prior_events: tuple[ContextEvent, ...] = Field(default=(), alias="priorEvents")
    current_turn_attachments: tuple[ContextAttachment, ...] = Field(
        default=(),
        alias="currentTurnAttachments",
    )
    diagnostics: ContextContinuityDiagnostics = Field(
        default_factory=ContextContinuityDiagnostics,
    )
    projection_digest: str | None = Field(default=None, alias="projectionDigest")
    model_visible_digest: str | None = Field(default=None, alias="modelVisibleDigest")
    source_transcript_head_digest: str | None = Field(
        default=None,
        alias="sourceTranscriptHeadDigest",
    )
    authority_flags: ContextContinuityAuthorityFlags = Field(
        default_factory=ContextContinuityAuthorityFlags,
        alias="authorityFlags",
    )

    @model_validator(mode="before")
    @classmethod
    def _force_no_authority(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        data["schemaVersion"] = "openmagi.contextContinuity.v1"
        data["localOnly"] = True
        data["diagnosticOnly"] = True
        data["responseAuthority"] = "none"
        data["authorityFlags"] = ContextContinuityAuthorityFlags().model_dump(
            by_alias=True,
            mode="python",
        )
        return data


def build_context_packet_from_transcript(
    transcript_store: object,
    *,
    config: ContextContinuityConfig | Mapping[str, object] | None = None,
) -> ConversationContextPacket:
    active_config = ContextContinuityConfig.model_validate(config or {})
    if not active_config.enabled:
        return ConversationContextPacket(enabled=False)

    entries = list(transcript_store.read_committed())
    projected = project_transcript_entries(entries)
    events = tuple(_context_event(event) for event in projected.events)
    total_candidate_count = len(events)
    kept_events = events[-active_config.max_imported_events :]
    dropped_for_budget_count = total_candidate_count - len(kept_events)
    reason_codes = list(projected.reason_codes)
    if dropped_for_budget_count:
        reason_codes.append("history_budget_truncated")

    diagnostics = ContextContinuityDiagnostics(
        importedEventCount=len(kept_events),
        rejectedEntryCount=projected.rejected_count,
        compactionApplied=projected.compaction_applied,
        droppedPreBoundaryCount=projected.dropped_pre_boundary_count,
        budgetTruncated=dropped_for_budget_count > 0,
        reasonCodes=tuple(dict.fromkeys(reason_codes)),
    )
    projection_digest = _digest_json(
        {
            "schemaVersion": "openmagi.contextContinuity.v1",
            "priorEvents": [
                event.model_dump(by_alias=True, mode="json", warnings=False)
                for event in kept_events
            ],
            "diagnostics": diagnostics.model_dump(
                by_alias=True,
                mode="json",
                warnings=False,
            ),
        }
    )
    packet = ConversationContextPacket(
        enabled=True,
        priorEvents=kept_events,
        diagnostics=diagnostics,
        projectionDigest=projection_digest,
        sourceTranscriptHeadDigest=_source_head_digest(entries),
    )
    rendered_without_model_digest = render_context_packet_for_model(
        packet,
        max_chars=active_config.max_rendered_chars,
    )
    return packet.model_copy(
        update={
            "model_visible_digest": _digest_text(rendered_without_model_digest),
        }
    )


def build_context_packet_from_session_continuity(
    session: object,
    *,
    transcript_store: object,
    continuity_result: object,
    config: ContextContinuityConfig | Mapping[str, object] | None = None,
) -> ConversationContextPacket:
    active_config = ContextContinuityConfig.model_validate(config or {})
    if not active_config.enabled:
        return ConversationContextPacket(enabled=False)

    entries = list(transcript_store.read_committed())
    session_events = _context_events_from_session(session)
    events = session_events.events
    total_candidate_count = len(events)
    kept_events = events[-active_config.max_imported_events :]
    dropped_for_budget_count = total_candidate_count - len(kept_events)
    base_diagnostics = getattr(continuity_result, "diagnostics", None)
    reason_codes = list(getattr(base_diagnostics, "reason_codes", ()) or ())
    if dropped_for_budget_count:
        reason_codes.append("history_budget_truncated")
    if session_events.rejected_count:
        reason_codes.append("session_continuity_event_rejected")

    diagnostics = ContextContinuityDiagnostics(
        importedEventCount=len(kept_events),
        rejectedEntryCount=max(
            0,
            int(getattr(continuity_result, "rejected_entry_count", 0)),
        )
        + session_events.rejected_count,
        compactionApplied=bool(
            getattr(continuity_result, "compaction_applied", False)
            or any(event.source == "compaction_summary" for event in kept_events)
        ),
        droppedPreBoundaryCount=max(
            0,
            int(getattr(continuity_result, "dropped_pre_boundary_count", 0)),
        ),
        budgetTruncated=bool(
            getattr(continuity_result, "budget_truncated", False)
            or dropped_for_budget_count > 0
        ),
        reasonCodes=tuple(dict.fromkeys(reason_codes)),
    )
    projection_digest = _digest_json(
        {
            "schemaVersion": "openmagi.contextContinuity.v1",
            "priorEvents": [
                event.model_dump(by_alias=True, mode="json", warnings=False)
                for event in kept_events
            ],
            "diagnostics": diagnostics.model_dump(
                by_alias=True,
                mode="json",
                warnings=False,
            ),
        }
    )
    packet = ConversationContextPacket(
        enabled=True,
        priorEvents=kept_events,
        diagnostics=diagnostics,
        projectionDigest=projection_digest,
        sourceTranscriptHeadDigest=_source_head_digest(entries),
    )
    rendered_without_model_digest = render_context_packet_for_model(
        packet,
        max_chars=active_config.max_rendered_chars,
    )
    return packet.model_copy(
        update={
            "model_visible_digest": _digest_text(rendered_without_model_digest),
        }
    )


class _SessionContextProjection(_ContextPacketModel):
    events: tuple[ContextEvent, ...]
    rejected_count: int = Field(default=0, alias="rejectedCount")


def render_context_packet_for_model(
    packet: ConversationContextPacket,
    *,
    max_chars: int = 24_000,
) -> str:
    if not packet.enabled:
        return ""
    lines = [
        "<openmagi_context_projection>",
        "schema_version: openmagi.contextContinuity.v1",
        "source: committed_transcript_and_current_turn_context",
        (
            "policy: This is model-visible context for resolving the current request. "
            "Do not quote or reveal this metadata in the final answer."
        ),
        f"projection_digest: {packet.projection_digest or 'none'}",
        f"model_visible_digest: {packet.model_visible_digest or 'none'}",
        f"source_transcript_head_digest: {packet.source_transcript_head_digest or 'none'}",
        "",
        "Recent conversation:",
    ]
    for event in packet.prior_events:
        prefix = f"- {event.role}({event.turn_id})"
        lines.append(f"{prefix}: {_single_line(event.text)}")
    if packet.current_turn_attachments:
        lines.extend(["", "Current turn context:"])
        for attachment in packet.current_turn_attachments:
            label = _single_line(attachment.label)
            text = _single_line(attachment.text)
            lines.append(f"- {attachment.kind}: {label}: {text}")
    lines.append("</openmagi_context_projection>")
    rendered = "\n".join(lines)
    return rendered[:max_chars]


def _context_event(event: ProjectedTranscriptEvent) -> ContextEvent:
    return ContextEvent(
        role=event.role,
        turnId=event.turn_id,
        text=event.text,
        source=event.source,
    )


def _context_event_from_adk_event(event: object) -> ContextEvent:
    source = _context_source_from_adk_event(event)
    return ContextEvent(
        role=_context_role_from_adk_author(getattr(event, "author", "")),
        turnId=getattr(event, "invocation_id", None) or "",
        text=_adk_event_text(event, source=source),
        source=source,
    )


def _context_role_from_adk_author(author: object) -> Literal["user", "assistant", "system"]:
    if author == "user":
        return "user"
    if author == "system":
        return "system"
    return "assistant"


def _context_source_from_adk_event(
    event: object,
) -> Literal["transcript", "compaction_summary", "metadata_ref"]:
    metadata = getattr(event, "custom_metadata", None)
    if isinstance(metadata, Mapping):
        marker = metadata.get("openmagi.sessionContinuity")
        if isinstance(marker, Mapping):
            kind = marker.get("kind")
            if kind == "compaction_boundary":
                return "compaction_summary"
            if kind in {
                "memory_recall_refs",
                "sanitized_control_ref",
                "sanitized_tool_ref",
            }:
                return "metadata_ref"
    return "transcript"


def _adk_event_text(
    event: object,
    *,
    source: Literal["transcript", "compaction_summary", "metadata_ref"],
) -> str:
    content = getattr(event, "content", None)
    parts = list(getattr(content, "parts", ()) or ())
    texts = [part.text for part in parts if isinstance(getattr(part, "text", None), str)]
    text = "\n".join(texts)
    if source == "metadata_ref":
        if text:
            return ""
        metadata = getattr(event, "custom_metadata", None)
        if not isinstance(metadata, Mapping):
            return ""
        return safe_projected_metadata_ref_text(metadata)
    if source == "compaction_summary":
        safe_text = safe_projected_text(text)
        return text if safe_text == text else ""
    return safe_projected_text(text)


def _is_session_continuity_event(event: object) -> bool:
    if not has_session_continuity_marker(event):
        return False
    if not validate_session_continuity_proof(event):
        return False
    kind = session_continuity_kind(event)
    if kind == "compaction_boundary":
        metadata = getattr(event, "custom_metadata", None)
        return isinstance(metadata, Mapping) and isinstance(
            metadata.get("openmagi.compaction"),
            Mapping,
        )
    if kind in {
        "memory_recall_refs",
        "sanitized_control_ref",
        "sanitized_tool_ref",
    } and _raw_adk_event_text(event):
        return False
    return True


def _context_events_from_session(session: object) -> _SessionContextProjection:
    valid_events: list[object] = []
    rejected_count = 0
    for event in list(getattr(session, "events", ()) or ()):
        if not has_session_continuity_marker(event):
            continue
        if not _is_session_continuity_event(event):
            rejected_count += 1
            continue
        valid_events.append(event)
    compacted_batch = latest_valid_compacted_batch(valid_events)
    if compacted_batch:
        valid_set = {id(event) for event in compacted_batch}
        rejected_count += sum(1 for event in valid_events if id(event) not in valid_set)
        valid_events = list(compacted_batch)
    elif has_valid_compaction_boundary(valid_events):
        rejected_count += len(valid_events)
        valid_events = []
    events = [_context_event_from_adk_event(event) for event in valid_events]
    return _SessionContextProjection(events=tuple(events), rejectedCount=rejected_count)


def _raw_adk_event_text(event: object) -> str:
    content = getattr(event, "content", None)
    parts = list(getattr(content, "parts", ()) or ())
    texts = [part.text for part in parts if isinstance(getattr(part, "text", None), str)]
    return "\n".join(texts)


def _source_head_digest(entries: list[object]) -> str | None:
    if not entries:
        return None
    payload = [
        entry.model_dump(by_alias=True, mode="json", warnings=False)
        for entry in entries
    ]
    return _digest_json(payload)


def _digest_json(value: object) -> str:
    return _digest_text(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    )


def _digest_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _single_line(value: object) -> str:
    return " ".join(str(value).split())


__all__ = [
    "ContextAttachment",
    "ContextContinuityAuthorityFlags",
    "ContextContinuityConfig",
    "ContextContinuityDiagnostics",
    "ContextEvent",
    "ConversationContextPacket",
    "build_context_packet_from_session_continuity",
    "build_context_packet_from_transcript",
    "render_context_packet_for_model",
]
