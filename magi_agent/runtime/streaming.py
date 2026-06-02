from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal


_DISABLED_REASON = (
    "local streaming reducer is disabled/default-off; "
    "snapshot and write intent records are descriptive only"
)
_PROGRESS_EVENT_TYPES = frozenset(
    {
        "tool_start",
        "tool_progress",
        "tool_end",
        "turn_start",
        "turn_phase",
        "llm_progress",
        "heartbeat",
        "control_event",
        "control_replay_complete",
        "runtime_trace",
        "model_fallback",
        "child_started",
        "child_progress",
        "child_completed",
        "child_cancelled",
        "child_failed",
        "source_inspected",
        "research_artifact_delta",
        "rule_check",
        "browser_frame",
        "document_draft",
        "patch_preview",
        "task_board",
        "context_end",
    }
)
_PRIVATE_TEXT_RE = re.compile(
    r"(?:"
    r"authorization\s*:\s*bearer\s+[A-Za-z0-9._-]+|"
    r"\bbearer\s+[A-Za-z0-9._-]+|"
    r"\bcookie\s+[A-Za-z0-9._=;-]+|"
    r"\bsid=[A-Za-z0-9._-]+|"
    r"\bsk-[A-Za-z0-9._-]+|"
    r"\b[A-Za-z0-9_]*(?:api[_-]?key|secret[_-]?key|service[_-]?role[_-]?key|"
    r"secret|token|password|private[_-]?key|credential)[A-Za-z0-9_]*\b"
    r"(?:\s*[:=]\s*[\"']?[A-Za-z0-9._~+/=-]+[\"']?)?|"
    r"gh[opusr]_[A-Za-z0-9_]+|"
    r"github_pat_[A-Za-z0-9_]+|"
    r"xox[a-z]-[A-Za-z0-9._-]+|"
    r"AKIA[0-9A-Z]{8,}|"
    r"AIza[A-Za-z0-9_-]+|"
    r"/workspace(?:/[^\s,;}\"']*)?|"
    r"/data/bots(?:/[^\s,;}\"']*)?|"
    r"/Users(?:/[^\s,;}\"']*)?|"
    r"/home(?:/[^\s,;}\"']*)?|"
    r"/var/lib/kubelet(?:/[^\s,;}\"']*)?|"
    r"raw[_ -]?tool[_ -]?(?:log|args?|results?)?|"
    r"raw[_ -]?child[_ -]?(?:output|transcript|prompt)?|"
    r"raw[_ -]?(?:prompt|transcript|output|result|log|args)|"
    r"hidden[_ -]?reasoning|"
    r"chain[_ -]?of[_ -]?thought"
    r")",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class StreamingWriteIntent:
    target: str
    operation: str
    payload: dict[str, Any] = field(default_factory=dict)
    executed: Literal[False] = False
    enabled: Literal[False] = False
    defaultOff: Literal[True] = True
    disabledReason: str = _DISABLED_REASON


@dataclass(frozen=True)
class StreamingSnapshot:
    turnId: str | None
    active: bool
    text: str
    status: str | None
    error: str | None
    final: bool
    done: bool
    progressEvents: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class StreamingReduction:
    snapshot: StreamingSnapshot
    renderedChunks: list[str]
    suppressedLegacyDeltas: list[str]
    writeIntents: list[StreamingWriteIntent]
    enabled: Literal[False] = False
    defaultOff: Literal[True] = True
    productionWritesAuthorized: Literal[False] = False
    snapshotAuthority: Literal[False] = False
    disabledReason: str = _DISABLED_REASON


def reduce_streaming_events(
    events: Sequence[Mapping[str, Any] | str],
    *,
    turn_id: str | None = None,
) -> StreamingReduction:
    """Reduce safe public stream events into a local-only UI snapshot.

    This is intentionally descriptive and default-off. It never writes to
    transcript, SSE, control state, storage, or production services.
    """

    reducer = _StreamingReducer(turn_id=turn_id)
    for event in events:
        reducer.accept(event)
    return reducer.result()


class _StreamingReducer:
    def __init__(self, *, turn_id: str | None) -> None:
        self.turn_id = _sanitize_turn_id(turn_id) if turn_id is not None else None
        self.text_parts: list[str] = []
        self.rendered_chunks: list[str] = []
        self.progress_events: list[dict[str, Any]] = []
        self.suppressed_legacy_deltas: list[str] = []
        self.saw_agent_text_delta = False
        self.legacy_replay_index = 0
        self.active = True
        self.final = False
        self.done = False
        self.status: str | None = None
        self.error: str | None = None
        self.saw_turn_end = False

    def accept(self, raw_event: Mapping[str, Any] | str) -> None:
        if isinstance(raw_event, str):
            self._accept_string_marker(raw_event)
            return

        legacy_delta = _legacy_delta_content(raw_event)
        if legacy_delta is not None:
            self._accept_legacy_delta(legacy_delta)
            return

        if _is_legacy_finish(raw_event):
            self._mark_done()
            return

        event_type = raw_event.get("type")
        if not isinstance(event_type, str):
            return

        if self.turn_id is None:
            event_turn_id = raw_event.get("turnId")
            if isinstance(event_turn_id, str):
                self.turn_id = _sanitize_turn_id(event_turn_id)

        if event_type == "response_clear":
            self.text_parts.clear()
            self.rendered_chunks.clear()
            self.suppressed_legacy_deltas.clear()
            self.saw_agent_text_delta = False
            self.legacy_replay_index = 0
            self.error = None
            self.status = None
            self.final = False
            self.done = False
            self.active = True
            return

        if event_type == "text_delta":
            delta = raw_event.get("delta")
            if isinstance(delta, str):
                safe_delta = _sanitize_text(delta)
                if not self.saw_agent_text_delta and self._suppress_legacy_replay(safe_delta):
                    return
                self.saw_agent_text_delta = True
                self.text_parts.append(safe_delta)
                self.rendered_chunks.append(safe_delta)
                self.active = True
            return

        if event_type == "turn_end":
            self._accept_turn_end(raw_event)
            return

        if event_type == "error":
            self._accept_error(raw_event)
            return

        if event_type in _PROGRESS_EVENT_TYPES:
            self.progress_events.append(_sanitize_event(raw_event))

    def result(self) -> StreamingReduction:
        text = "".join(self.text_parts)
        snapshot = StreamingSnapshot(
            turnId=self.turn_id,
            active=self.active,
            text=text,
            status=self.status,
            error=self.error,
            final=self.final,
            done=self.done,
            progressEvents=list(self.progress_events),
        )
        return StreamingReduction(
            snapshot=snapshot,
            renderedChunks=list(self.rendered_chunks),
            suppressedLegacyDeltas=list(self.suppressed_legacy_deltas),
            writeIntents=self._write_intents(snapshot),
        )

    def _accept_string_marker(self, marker: str) -> None:
        if marker.strip() in {"[DONE]", "data: [DONE]"}:
            self._mark_done()

    def _accept_legacy_delta(self, delta: str) -> None:
        safe_delta = _sanitize_text(delta)
        if self.saw_agent_text_delta:
            self.suppressed_legacy_deltas.append(safe_delta)
            return
        self.text_parts.append(safe_delta)
        self.rendered_chunks.append(safe_delta)

    def _suppress_legacy_replay(self, delta: str) -> bool:
        if self.legacy_replay_index >= len(self.rendered_chunks):
            return False
        if self.rendered_chunks[self.legacy_replay_index] != delta:
            return False
        self.legacy_replay_index += 1
        self.suppressed_legacy_deltas.append(delta)
        return True

    def _accept_turn_end(self, event: Mapping[str, Any]) -> None:
        self.saw_turn_end = True
        status = event.get("status")
        self.status = status if isinstance(status, str) else "committed"
        self.final = True
        self.active = False

    def _accept_error(self, event: Mapping[str, Any]) -> None:
        self.error = _sanitize_text(_error_message(event))
        self.status = "error"
        self.final = True
        self.active = False

    def _mark_done(self) -> None:
        self.done = True
        self.final = True
        self.active = False
        if self.status is None and self.saw_turn_end:
            self.status = "committed"

    def _write_intents(
        self,
        snapshot: StreamingSnapshot,
    ) -> list[StreamingWriteIntent]:
        intents = [
            StreamingWriteIntent(
                target="local_runtime",
                operation="snapshot_update",
                payload={
                    "turnId": snapshot.turnId,
                    "active": snapshot.active,
                    "status": snapshot.status,
                    "final": snapshot.final,
                    "done": snapshot.done,
                },
            )
        ]
        if snapshot.text:
            intents.append(
                StreamingWriteIntent(
                    target="transcript",
                    operation="transcript_assistant_text",
                    payload={
                        "turnId": snapshot.turnId,
                        "text": snapshot.text,
                    },
                )
            )
        if snapshot.final:
            intents.append(
                StreamingWriteIntent(
                    target="sse",
                    operation="turn_end",
                    payload={
                        "turnId": snapshot.turnId,
                        "status": snapshot.status,
                        "error": snapshot.error,
                    },
                )
            )
        return intents


def _legacy_delta_content(event: Mapping[str, Any]) -> str | None:
    choices = event.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    if not isinstance(first, Mapping):
        return None
    delta = first.get("delta")
    if not isinstance(delta, Mapping):
        return None
    content = delta.get("content")
    return content if isinstance(content, str) else None


def _is_legacy_finish(event: Mapping[str, Any]) -> bool:
    choices = event.get("choices")
    if not isinstance(choices, list) or not choices:
        return False
    first = choices[0]
    return isinstance(first, Mapping) and isinstance(first.get("finish_reason"), str)


def _error_message(event: Mapping[str, Any]) -> str:
    for key in ("message", "error", "detail"):
        value = event.get(key)
        if isinstance(value, str) and value:
            return value
    return "streaming_error"


def _sanitize_event(event: Mapping[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key, value in event.items():
        key_text = str(key)
        if _PRIVATE_TEXT_RE.search(key_text):
            continue
        safe_value = _sanitize_value(value)
        if safe_value is not None:
            sanitized[key_text] = safe_value
    return sanitized


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, str):
        return _sanitize_text(value)
    if isinstance(value, bool | int | float) or value is None:
        return value
    if isinstance(value, Mapping):
        return _sanitize_event(value)
    if isinstance(value, list | tuple):
        return [_sanitize_value(item) for item in value]
    return _sanitize_text(str(value))


def _sanitize_text(value: str) -> str:
    return _PRIVATE_TEXT_RE.sub("[redacted-private]", value)


def _sanitize_turn_id(value: str) -> str:
    safe = _sanitize_text(value)
    if safe == value and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,180}", value):
        return value
    import hashlib

    return f"turn:{hashlib.sha1(value.encode('utf-8')).hexdigest()[:16]}"
