from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

from magi_agent.evidence.tool_boundary import ToolEvidenceRecord
from magi_agent.runtime.public_events import (
    tool_blocked_event,
    tool_end_event,
    tool_progress_event,
    tool_start_event,
)
from magi_agent.shared.tool_preview import sanitize_tool_preview

if TYPE_CHECKING:
    from magi_agent.tools.kernel import (
        ToolExecutionOutcome,
        ToolExecutionRequest,
    )


PublicToolEvent = dict[str, object]

_TEXT_LIMIT = 400
_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{1,180}$")
_DIGEST_RE = re.compile(r"^(?:receipt:)?sha256:[a-fA-F0-9]{64}$")
_RESULT_REF_RE = re.compile(r"^result:sha256:[a-fA-F0-9]{64}$")
_PRIVATE_MARKER_RE = re.compile(
    r"(?:"
    r"raw[_ -]?(?:tool|child|prompt|transcript|output|result|log|args|payload|"
    r"event|provider|model|response|body)|"
    r"(?:tool|tool[_ -]?call|tool[_ -]?use|function|function[_ -]?call)"
    r"[_ -]?(?:args?|arguments?|input|output|result|response|logs?)|"
    r"hidden[_ -]?reasoning|chain[_ -]?of[_ -]?thought|"
    r"/Users(?:/[^\s,;}\"']*)?|"
    r"/home(?:/[^\s,;}\"']*)?|"
    r"/workspace(?:/[^\s,;}\"']*)?|"
    r"/data/bots(?:/[^\s,;}\"']*)?|"
    r"/var/lib/kubelet(?:/[^\s,;}\"']*)?"
    r")",
    re.IGNORECASE,
)
_SAFE_PROGRESS_STATUSES = frozenset(
    {
        "queued",
        "running",
        "reading",
        "writing",
        "waiting",
        "verifying",
        "complete",
        "ok",
        "error",
    }
)


def project_tool_execution_events(
    request: "ToolExecutionRequest",
    outcome: "ToolExecutionOutcome",
) -> tuple[PublicToolEvent, ...]:
    events: list[PublicToolEvent] = []
    call_record = _call_record(outcome)
    if _outcome_executed(outcome) and call_record is not None:
        call_ref = _safe_ref(call_record.args_hash)
        if call_ref is None:
            return tuple(project_tool_terminal_events(request, outcome))
        start_event = tool_start_event(
            tool_id=_tool_event_id(request, outcome),
            name=_tool_name(request, outcome),
            input_preview=_preview_mapping(call_record.arg_summary),
        )
        start_event["transcriptRefs"] = [call_ref]
        events.append(start_event)
        progress = _progress_event(request, outcome)
        if progress is not None:
            progress["transcriptRefs"] = [call_ref]
            events.append(progress)
    events.extend(project_tool_terminal_events(request, outcome))
    return tuple(events)


def project_tool_terminal_events(
    request: "ToolExecutionRequest",
    outcome: object,
) -> tuple[PublicToolEvent, ...]:
    terminal_record = _terminal_record(outcome)
    if terminal_record is None:
        return ()

    refs = _receipt_refs(outcome, terminal_record=terminal_record)
    if not refs:
        return ()

    if _is_blocked_outcome(outcome):
        reason = _blocked_reason(outcome, terminal_record)
        return (
            tool_blocked_event(
                tool_id=_tool_event_id(request, outcome),
                reason=reason,
                receipt_refs=refs,
                duration_ms=terminal_record.duration_ms,
            ),
        )

    return (
        tool_end_event(
            tool_id=_tool_event_id(request, outcome),
            status="ok" if _outcome_status(outcome) == "ok" else "error",
            output_preview=(
                _output_preview(outcome, terminal_record)
                or _terminal_error(outcome, terminal_record)
            ),
            error=_terminal_error(outcome, terminal_record),
            receipt_refs=refs,
            duration_ms=_duration_ms(outcome, terminal_record),
        ),
    )


def _progress_event(
    request: "ToolExecutionRequest",
    outcome: "ToolExecutionOutcome",
) -> PublicToolEvent | None:
    metadata = _result_metadata(outcome)
    status = _safe_progress_status(metadata.get("status"))
    detail = _safe_text(metadata.get("detail"))
    message = _safe_text(metadata.get("message"))
    label = _safe_text(metadata.get("label"))
    progress = _safe_progress(metadata.get("progress"))
    if (
        status is None
        and detail is None
        and message is None
        and label is None
        and progress is None
    ):
        return None
    return tool_progress_event(
        tool_id=_tool_event_id(request, outcome),
        label=label,
        status=status,
        message=message,
        detail=detail,
        progress=progress,
    )


def _receipt_refs(
    outcome: object,
    *,
    terminal_record: ToolEvidenceRecord,
) -> tuple[str, ...]:
    refs: list[str] = []
    terminal_ref_seen = False
    for record in _evidence_records(outcome):
        for ref in (record.args_hash, record.result_hash):
            safe_ref = _safe_ref(ref)
            if safe_ref is not None:
                if record is terminal_record and ref == record.result_hash:
                    terminal_ref_seen = True
                refs.append(safe_ref)
    output_projection = getattr(outcome, "output_projection", None)
    if isinstance(output_projection, Mapping):
        for key in ("resultRef", "digest", "storeRef"):
            safe_ref = _safe_ref(output_projection.get(key))
            if safe_ref is not None:
                refs.append(safe_ref)
    if not terminal_ref_seen:
        refs.append(_record_digest_ref(terminal_record))
    return tuple(dict.fromkeys(refs))


def _output_preview(outcome: object, terminal_record: ToolEvidenceRecord) -> str | None:
    output_projection = getattr(outcome, "output_projection", None)
    if isinstance(output_projection, Mapping):
        counts = output_projection.get("counts")
        truncation = output_projection.get("truncation")
        projection_summary = {
            "resultRef": output_projection.get("resultRef"),
            "digest": output_projection.get("digest"),
            "counts": counts if isinstance(counts, Mapping) else None,
            "truncation": truncation if isinstance(truncation, Mapping) else None,
        }
        return _preview_mapping(projection_summary)
    if terminal_record.result_summary:
        return _preview_mapping(terminal_record.result_summary)
    return None


def _terminal_error(outcome: object, terminal_record: ToolEvidenceRecord) -> str | None:
    result = getattr(outcome, "result", None)
    error_code = getattr(result, "error_code", None)
    if isinstance(error_code, str) and error_code.strip():
        return _safe_text(error_code)
    if terminal_record.error_code:
        return _safe_text(terminal_record.error_code)
    if _outcome_status(outcome) != "ok":
        return _reason_code(outcome) or "tool_error"
    return None


def _blocked_reason(outcome: object, terminal_record: ToolEvidenceRecord) -> str:
    result = getattr(outcome, "result", None)
    metadata = getattr(result, "metadata", None)
    if isinstance(metadata, Mapping):
        reason = _safe_text(metadata.get("reason"))
        if reason is not None:
            return reason
    if terminal_record.error_message:
        safe_message = _safe_text(terminal_record.error_message)
        if safe_message is not None:
            return safe_message
    return _reason_code(outcome) or "tool blocked"


def _duration_ms(outcome: object, terminal_record: ToolEvidenceRecord) -> int | float | None:
    result = getattr(outcome, "result", None)
    duration = getattr(result, "duration_ms", None)
    if isinstance(duration, bool):
        return terminal_record.duration_ms
    if isinstance(duration, int | float) and math.isfinite(float(duration)) and duration >= 0:
        return duration
    return terminal_record.duration_ms


def _tool_event_id(request: "ToolExecutionRequest", outcome: object) -> str:
    for candidate in (
        getattr(_terminal_record(outcome), "tool_call_id", None),
        getattr(_call_record(outcome), "tool_call_id", None),
        getattr(request, "tool_call_id", None),
    ):
        safe_id = _safe_id(candidate)
        if safe_id is not None:
            return safe_id
    return _hashed_id(f"{getattr(request, 'context', None)}:{_tool_name(request, outcome)}")


def _tool_name(request: "ToolExecutionRequest", outcome: object) -> str:
    for candidate in (
        getattr(_terminal_record(outcome), "tool_name", None),
        getattr(_call_record(outcome), "tool_name", None),
        getattr(request, "tool_name", None),
    ):
        if isinstance(candidate, str) and candidate.strip():
            return _safe_display_text(candidate)
    return "tool"


def _call_record(outcome: object) -> ToolEvidenceRecord | None:
    for record in _evidence_records(outcome):
        if record.kind == "tool_call":
            return record
    return None


def _terminal_record(outcome: object) -> ToolEvidenceRecord | None:
    for record in _evidence_records(outcome):
        if record.terminal:
            return record
    return None


def _evidence_records(outcome: object) -> tuple[ToolEvidenceRecord, ...]:
    records = getattr(outcome, "evidence_records", None)
    if not isinstance(records, tuple):
        return ()
    return tuple(record for record in records if isinstance(record, ToolEvidenceRecord))


def _is_blocked_outcome(outcome: object) -> bool:
    return (
        _outcome_status(outcome) in {"blocked", "needs_approval"}
        or getattr(outcome, "executed", False) is False
        and getattr(outcome, "blocking", False) is True
    )


def _outcome_executed(outcome: object) -> bool:
    return getattr(outcome, "executed", False) is True


def _outcome_status(outcome: object) -> str | None:
    status = getattr(outcome, "status", None)
    return status if isinstance(status, str) else None


def _reason_code(outcome: object) -> str | None:
    reason_code = getattr(outcome, "reason_code", None)
    return _safe_text(reason_code)


def _result_metadata(outcome: object) -> Mapping[str, object]:
    result = getattr(outcome, "result", None)
    metadata = getattr(result, "metadata", None)
    return metadata if isinstance(metadata, Mapping) else {}


def _preview_mapping(value: object) -> str:
    text = json.dumps(
        _safe_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return _safe_display_text(text)


def _safe_value(value: object) -> object:
    if isinstance(value, Mapping):
        safe: dict[str, object] = {}
        for key, nested in value.items():
            key_text = str(key)
            if _PRIVATE_MARKER_RE.search(key_text) or _is_sensitive_key(key_text):
                safe[f"redactedKey:{_digest(key_text)[:12]}"] = "[redacted]"
                continue
            safe[key_text] = _safe_value(nested)
        return safe
    if isinstance(value, list | tuple):
        return [_safe_value(item) for item in value[:25]]
    if isinstance(value, str):
        return _safe_display_text(value)
    if isinstance(value, bool | int | float) or value is None:
        return value
    return _safe_display_text(str(value))


def _safe_progress_status(value: object) -> str | None:
    text = _safe_text(value)
    if text is None:
        return None
    normalized = text.lower().replace(" ", "_")
    return normalized if normalized in _SAFE_PROGRESS_STATUSES else None


def _safe_progress(value: object) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if not math.isfinite(float(value)) or value < 0 or value > 100:
        return None
    return value


def _safe_text(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return _safe_display_text(value)


def _safe_display_text(value: str) -> str:
    if "[redacted-private]" in value or _PRIVATE_MARKER_RE.search(value):
        return "[redacted-private]"
    redacted = sanitize_tool_preview(value)
    if len(redacted) > _TEXT_LIMIT:
        return f"{redacted[: _TEXT_LIMIT - 3]}..."
    return redacted


def _safe_id(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    if _PRIVATE_MARKER_RE.search(value):
        return _hashed_id(value)
    if _ID_RE.fullmatch(value):
        return value
    return _hashed_id(value)


def _safe_ref(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if _DIGEST_RE.fullmatch(candidate) or _RESULT_REF_RE.fullmatch(candidate):
        return candidate
    return None


def _record_digest_ref(record: ToolEvidenceRecord) -> str:
    return "sha256:" + _digest(
        record.model_dump(by_alias=True, mode="json", warnings=False)
    )


def _hashed_id(value: str) -> str:
    return f"tool-call-{_digest(value)[:16]}"


def _digest(value: object) -> str:
    material = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _is_sensitive_key(value: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", value.casefold())
    return any(
        marker in normalized
        for marker in (
            "authorization",
            "cookie",
            "credential",
            "secret",
            "token",
            "password",
            "privatekey",
            "apikey",
            "servicekey",
            "key",
            "raw",
            "prompt",
            "hidden",
        )
    )


__all__ = [
    "PublicToolEvent",
    "project_tool_execution_events",
    "project_tool_terminal_events",
]
