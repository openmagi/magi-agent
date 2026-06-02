from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field

from openmagi_core_agent.runtime.transcript import (
    CompactionBoundaryEntry,
    ToolResultEntry,
    TranscriptEntry,
    TurnStartedEntry,
)


_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_SECRET_OR_PRIVATE_RE = re.compile(
    r"(?:"
    r"authorization\s*:|"
    r"\bbearer\b|"
    r"\bcookie\b|"
    r"\bcredential\b|"
    r"\bsession[_-]?key\b|"
    r"\bapi[_-]?key\b|"
    r"\bsecret\b|"
    r"\bpassword\b|"
    r"\btoken\b|"
    r"^sk-|"
    r"gh[opusr]_|"
    r"github_pat_|"
    r"xox[a-z]-|"
    r"AIza|"
    r"REDACT_ME_[A-Z0-9_]+|"
    r"api\.telegram\.org|"
    r"\btelegram[_ -]?token\b|"
    r"\bbot\d+:[A-Za-z0-9_-]{8,}\b|"
    r"/workspace(?:/|\b)|"
    r"/data/bots(?:/|\b)"
    r")",
    re.IGNORECASE,
)
_REF_RE = re.compile(r"^[a-z][a-z0-9+.-]*://[A-Za-z0-9][A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=-]{0,511}$")
_SCOPED_REF_RE = re.compile(
    r"^[a-z][a-z0-9+.-]*:[A-Za-z0-9][A-Za-z0-9._-]{0,127}"
    r"(?::[A-Za-z0-9][A-Za-z0-9._-]{0,127}){0,15}$"
)
_NESTED_URI_RE = re.compile(r"[a-z][a-z0-9+.-]*://", re.IGNORECASE)
_SAFE_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_CHILD_ENVELOPE_REF_SCHEMES = frozenset({"child-envelope"})
_EVIDENCE_REF_SCHEMES = frozenset({"evidence"})
_CONTROL_REF_SCHEMES = frozenset({"control"})
_SUMMARY_REF_SCHEMES = frozenset({"summary"})
_MEMORY_RECALL_REF_SCHEMES = frozenset({"memory-ref", "memory"})
_RAW_CHILD_KEYS = frozenset(
    {
        "childTranscript",
        "rawChildTranscript",
        "rawToolLogs",
        "toolLogs",
        "hiddenReasoning",
        "intermediateOutput",
        "rawChildOutput",
        "childPrompt",
        "childPrompts",
    }
)
_RAW_CONTROL_KEYS = frozenset(
    {
        "approvalPayload",
        "rawApprovalPayload",
        "auth",
        "cookies",
        "credentials",
        "sessionKey",
    }
)


class _ProjectionModel(BaseModel):
    model_config = _MODEL_CONFIG

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        return cls(**values)


class ProjectedTranscriptEvent(_ProjectionModel):
    role: Literal["user", "assistant", "system"]
    turn_id: str = Field(alias="turnId")
    text: str
    source: Literal["transcript", "compaction_summary", "metadata_ref"]
    metadata: dict[str, object] = Field(default_factory=dict)


class TranscriptProjection(_ProjectionModel):
    events: tuple[ProjectedTranscriptEvent, ...]
    rejected_count: int = Field(default=0, alias="rejectedCount")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    compaction_applied: bool = Field(default=False, alias="compactionApplied")
    dropped_pre_boundary_count: int = Field(default=0, alias="droppedPreBoundaryCount")


def project_transcript_entries(entries: Sequence[TranscriptEntry]) -> TranscriptProjection:
    window, compaction_applied, dropped_pre_boundary_count = apply_compaction(entries)
    projected = _project_entries(window)
    return TranscriptProjection(
        events=projected.events,
        rejectedCount=projected.rejected_count,
        reasonCodes=projected.reason_codes,
        compactionApplied=compaction_applied,
        droppedPreBoundaryCount=dropped_pre_boundary_count,
    )


def apply_compaction(
    entries: Sequence[TranscriptEntry],
) -> tuple[list[TranscriptEntry], bool, int]:
    approved_boundary_index = -1
    for index, entry in enumerate(entries):
        if _entry_kind(entry) == "compaction_boundary" and approved_compaction_boundary(
            entry
        ):
            approved_boundary_index = index
    if approved_boundary_index < 0:
        return list(entries), False, 0

    window: list[TranscriptEntry] = []
    boundary = entries[approved_boundary_index]
    window.append(boundary)
    window.extend(entries[approved_boundary_index + 1 :])
    return window, True, approved_boundary_index


def approved_compaction_boundary(entry: CompactionBoundaryEntry) -> bool:
    extra = _entry_extra(entry)
    if "approved" in extra:
        return extra.get("approved") is True and _safe_label(entry.boundary_id) and (
            _safe_text(entry.summary_text) or _safe_summary_ref(extra.get("summaryRef"))
        )

    return _canonical_ts_compaction_boundary(entry)


def safe_memory_recall_ref(value: object) -> bool:
    return _safe_typed_ref(value, allowed_schemes=_MEMORY_RECALL_REF_SCHEMES)


class _ProjectionOutcome(_ProjectionModel):
    events: tuple[ProjectedTranscriptEvent, ...]
    rejected_count: int
    reason_codes: tuple[str, ...]


def _canonical_ts_compaction_boundary(entry: CompactionBoundaryEntry) -> bool:
    before_tokens = entry.before_token_count
    after_tokens = entry.after_token_count
    if not _safe_token_count(before_tokens) or not _safe_token_count(after_tokens):
        return False

    return _safe_label(entry.boundary_id) and (
        _safe_label(entry.turn_id)
        and _safe_summary_hash(entry.summary_hash)
        and _safe_text(entry.summary_text)
        and _safe_created_at(entry.created_at)
        and after_tokens <= before_tokens
    )


def _project_entries(entries: Sequence[TranscriptEntry]) -> _ProjectionOutcome:
    route_by_turn: dict[str, dict[str, object]] = {}
    events: list[ProjectedTranscriptEvent] = []
    rejected_count = 0
    reason_codes: list[str] = []

    for entry in entries:
        entry_kind = _entry_kind(entry)
        if entry_kind == "turn_started":
            route_by_turn[entry.turn_id] = _sanitize_route_metadata(entry)
            continue

        if entry_kind == "user_message":
            events.append(
                _text_event(
                    role="user",
                    turn_id=entry.turn_id,
                    text=entry.text,
                    metadata=_metadata_for_entry(entry, route_by_turn),
                )
            )
            continue

        if entry_kind == "assistant_text":
            events.append(
                _text_event(
                    role="assistant",
                    turn_id=entry.turn_id,
                    text=entry.text,
                    metadata=_metadata_for_entry(entry, route_by_turn),
                )
            )
            continue

        if entry_kind == "compaction_boundary":
            event = _compaction_event(entry)
            if event is not None:
                events.append(event)
            else:
                rejected_count += 1
                reason_codes.append("unapproved_compaction_boundary_rejected")
            continue

        if entry_kind == "tool_call":
            rejected_count += 1
            reason_codes.append("raw_tool_payload_rejected")
            continue

        if entry_kind == "tool_result":
            raw_reasons = _raw_tool_result_reasons(entry)
            if raw_reasons:
                rejected_count += 1
                reason_codes.extend(raw_reasons)
            ref_event = _sanitized_ref_event(entry)
            if ref_event is not None:
                events.append(ref_event)
            continue

        if entry_kind == "control_event":
            if _has_raw_control_payload(entry):
                rejected_count += 1
                reason_codes.append("raw_control_payload_rejected")
                continue
            ref_event = _control_ref_event(entry)
            if ref_event is not None:
                events.append(ref_event)

    return _ProjectionOutcome(
        events=tuple(events),
        rejected_count=rejected_count,
        reason_codes=tuple(dict.fromkeys(reason_codes)),
    )


def _entry_kind(entry: object) -> str:
    return str(getattr(entry, "kind", ""))


def _metadata_for_entry(
    entry: TranscriptEntry,
    route_by_turn: Mapping[str, dict[str, object]],
) -> dict[str, object]:
    metadata = {
        "openmagi.sessionContinuity": {
            "source": "ts_transcript_read_committed",
            "kind": entry.kind,
        }
    }
    turn_id = entry.turn_id
    if turn_id is not None and turn_id in route_by_turn:
        metadata["openmagi.modelRouting"] = route_by_turn[turn_id]
    return metadata


def _sanitize_route_metadata(entry: TurnStartedEntry) -> dict[str, object]:
    route: dict[str, object] = {}
    if _safe_label(entry.declared_route):
        route["declaredRoute"] = entry.declared_route

    raw = _entry_extra(entry).get("routingMetadata")
    if isinstance(raw, Mapping):
        provider_label = raw.get("providerLabel")
        model_label = raw.get("modelLabel")
        if isinstance(provider_label, str) and _safe_label(provider_label):
            route["providerLabel"] = provider_label
        if isinstance(model_label, str) and _safe_label(model_label):
            route["modelLabel"] = model_label
    if "providerLabel" in route or "modelLabel" in route:
        route["credentialRefSource"] = "server_config"
    return route


def _text_event(
    *,
    role: Literal["user", "assistant"],
    turn_id: str,
    text: str,
    metadata: dict[str, object],
) -> ProjectedTranscriptEvent:
    return ProjectedTranscriptEvent(
        role=role,
        turnId=turn_id,
        text=_redact_text(text),
        source="transcript",
        metadata=metadata,
    )


def _compaction_event(entry: CompactionBoundaryEntry) -> ProjectedTranscriptEvent | None:
    if not approved_compaction_boundary(entry):
        return None
    extra = _entry_extra(entry)
    metadata: dict[str, object] = {
        "openmagi.sessionContinuity": {
            "source": "ts_transcript_read_committed",
            "kind": "compaction_boundary",
        },
        "openmagi.compaction": {
            "boundaryId": entry.boundary_id,
        },
    }
    compaction = metadata["openmagi.compaction"]
    assert isinstance(compaction, dict)
    if _safe_text(entry.summary_hash):
        compaction["summaryHash"] = entry.summary_hash
    summary_ref = extra.get("summaryRef")
    if _safe_summary_ref(summary_ref):
        compaction["summaryRef"] = summary_ref
    summary_text = entry.summary_text if _safe_text(entry.summary_text) else ""
    return ProjectedTranscriptEvent(
        role="system",
        turnId=entry.turn_id,
        text=summary_text,
        source="compaction_summary",
        metadata=metadata,
    )


def _sanitized_ref_event(entry: ToolResultEntry) -> ProjectedTranscriptEvent | None:
    metadata = _safe_ref_metadata(entry.metadata or {})
    if not metadata:
        return None
    metadata["openmagi.sessionContinuity"] = {
        "source": "ts_transcript_read_committed",
        "kind": "sanitized_tool_ref",
    }
    return ProjectedTranscriptEvent(
        role="system",
        turnId=entry.turn_id,
        text=_metadata_ref_text(metadata),
        source="metadata_ref",
        metadata=metadata,
    )


def _control_ref_event(entry: TranscriptEntry) -> ProjectedTranscriptEvent | None:
    extra = _entry_extra(entry)
    metadata = _safe_ref_metadata(extra)
    if not metadata:
        metadata = {
            "openmagi.controlEvent": {
                "seq": entry.seq,
                "eventId": entry.event_id,
                "eventType": entry.event_type,
            }
        }
    metadata["openmagi.sessionContinuity"] = {
        "source": "ts_transcript_read_committed",
        "kind": "sanitized_control_ref",
    }
    return ProjectedTranscriptEvent(
        role="system",
        turnId=entry.turn_id or "",
        text=_metadata_ref_text(metadata),
        source="metadata_ref",
        metadata=metadata,
    )


def _metadata_ref_text(metadata: Mapping[str, object]) -> str:
    text = safe_projected_metadata_ref_text(metadata)
    if text:
        return text

    refs: list[str] = []
    for key in (
        "openmagi.childEnvelopeRef",
        "openmagi.evidenceRefs",
        "openmagi.controlRefs",
    ):
        value = metadata.get(key)
        if isinstance(value, str):
            refs.append(value)
        elif isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
            refs.extend(str(item) for item in value if isinstance(item, str))
    return " ".join(refs)


def safe_projected_metadata_ref_text(metadata: Mapping[str, object]) -> str:
    refs: list[str] = []
    child_ref = metadata.get("openmagi.childEnvelopeRef")
    if _safe_child_envelope_ref(child_ref):
        refs.append(str(child_ref))

    evidence_refs = metadata.get("openmagi.evidenceRefs")
    if isinstance(evidence_refs, Sequence) and not isinstance(evidence_refs, str):
        refs.extend(str(ref) for ref in evidence_refs if _safe_evidence_ref(ref))

    control_refs = metadata.get("openmagi.controlRefs")
    if isinstance(control_refs, Sequence) and not isinstance(control_refs, str):
        refs.extend(str(ref) for ref in control_refs if _safe_control_ref(ref))

    return " ".join(refs)


def _safe_ref_metadata(raw: Mapping[str, object]) -> dict[str, object]:
    metadata: dict[str, object] = {}

    child_ref = raw.get("childEnvelopeRef")
    if _safe_child_envelope_ref(child_ref):
        metadata["openmagi.childEnvelopeRef"] = child_ref

    evidence_refs = _safe_refs(raw.get("evidenceRefs"), validator=_safe_evidence_ref)
    if evidence_refs:
        metadata["openmagi.evidenceRefs"] = evidence_refs

    control_refs = _safe_refs(raw.get("controlRefs"), validator=_safe_control_ref)
    if control_refs:
        metadata["openmagi.controlRefs"] = control_refs

    control_ref = raw.get("controlRef")
    if _safe_control_ref(control_ref):
        metadata["openmagi.controlRefs"] = [control_ref]

    return metadata


def _safe_refs(value: object, *, validator: Callable[[object], bool]) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, str):
        return []
    return [item for item in value if validator(item)]


def _raw_tool_result_reasons(entry: ToolResultEntry) -> tuple[str, ...]:
    metadata = entry.metadata or {}
    reasons: list[str] = []
    if _contains_any_key(metadata, _RAW_CHILD_KEYS) or _unsafe_value(entry.output):
        if _contains_any_key(metadata, _RAW_CHILD_KEYS):
            reasons.append("raw_child_payload_rejected")
        else:
            reasons.append("raw_tool_payload_rejected")
    if _contains_any_key(metadata, _RAW_CONTROL_KEYS):
        reasons.append("raw_tool_payload_rejected")
    return tuple(dict.fromkeys(reasons))


def _has_raw_control_payload(entry: TranscriptEntry) -> bool:
    extra = _entry_extra(entry)
    return _contains_any_key(extra, _RAW_CONTROL_KEYS) or _unsafe_value(extra)


def _contains_any_key(value: Mapping[str, object], keys: frozenset[str]) -> bool:
    return any(key in value for key in keys)


def _entry_extra(entry: TranscriptEntry) -> dict[str, object]:
    extra = getattr(entry, "model_extra", None)
    return dict(extra or {})


def _safe_label(value: object) -> bool:
    return isinstance(value, str) and bool(_SAFE_LABEL_RE.fullmatch(value)) and not _unsafe_value(value)


def _safe_child_envelope_ref(value: object) -> bool:
    return _safe_typed_ref(value, allowed_schemes=_CHILD_ENVELOPE_REF_SCHEMES)


def _safe_evidence_ref(value: object) -> bool:
    return _safe_typed_ref(value, allowed_schemes=_EVIDENCE_REF_SCHEMES)


def _safe_control_ref(value: object) -> bool:
    return _safe_typed_ref(value, allowed_schemes=_CONTROL_REF_SCHEMES)


def _safe_summary_ref(value: object) -> bool:
    return _safe_typed_ref(value, allowed_schemes=_SUMMARY_REF_SCHEMES)


def _safe_typed_ref(value: object, *, allowed_schemes: frozenset[str]) -> bool:
    if not isinstance(value, str) or _unsafe_value(value):
        return False
    scheme = _ref_scheme(value)
    if scheme not in allowed_schemes:
        return False
    if _private_memory_ref(value):
        return False
    if _nested_ref_payload(value, scheme):
        return False
    return bool(_REF_RE.fullmatch(value) or _SCOPED_REF_RE.fullmatch(value))


def _ref_scheme(value: str) -> str:
    if "://" in value:
        return value.split("://", 1)[0]
    return value.split(":", 1)[0]


def _private_memory_ref(value: str) -> bool:
    lowered = value.lower()
    return (
        lowered.startswith("memory://private/")
        or lowered == "memory://private"
        or lowered.startswith("memory:private:")
        or lowered == "memory:private"
    )


def _nested_ref_payload(value: str, scheme: str) -> bool:
    uri_prefix = f"{scheme}://"
    if value.startswith(uri_prefix):
        payload = value[len(uri_prefix) :]
    else:
        scoped_prefix = f"{scheme}:"
        if not value.startswith(scoped_prefix):
            return False
        payload = value[len(scoped_prefix) :]

    return _private_memory_ref(payload) or _NESTED_URI_RE.search(payload) is not None


def _safe_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip()) and not _unsafe_value(value)


def safe_projected_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return _redact_text(value)


def _safe_summary_hash(value: object) -> bool:
    return _safe_label(value)


def _safe_created_at(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return False
    return value >= 0 and math.isfinite(float(value))


def _safe_token_count(value: object) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= 1_000_000_000
    )


def _unsafe_value(value: object) -> bool:
    return _SECRET_OR_PRIVATE_RE.search(str(value)) is not None


def _redact_text(text: str) -> str:
    if _unsafe_value(text):
        return "[redacted unsafe transcript text]"
    return text


__all__ = [
    "ProjectedTranscriptEvent",
    "TranscriptProjection",
    "apply_compaction",
    "approved_compaction_boundary",
    "project_transcript_entries",
    "safe_memory_recall_ref",
    "safe_projected_metadata_ref_text",
    "safe_projected_text",
]
