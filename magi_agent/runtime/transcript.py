from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class TranscriptEntryBase(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="allow")

    kind: str
    ts: int | float
    turn_id: str | None = Field(default=None, alias="turnId")


class UserMessageEntry(TranscriptEntryBase):
    kind: Literal["user_message"] = "user_message"
    turn_id: str = Field(alias="turnId")
    text: str


class AssistantTextEntry(TranscriptEntryBase):
    kind: Literal["assistant_text"] = "assistant_text"
    turn_id: str = Field(alias="turnId")
    text: str


class ToolCallEntry(TranscriptEntryBase):
    kind: Literal["tool_call"] = "tool_call"
    turn_id: str = Field(alias="turnId")
    tool_use_id: str = Field(alias="toolUseId")
    name: str
    input: object


class ToolResultEntry(TranscriptEntryBase):
    kind: Literal["tool_result"] = "tool_result"
    turn_id: str = Field(alias="turnId")
    tool_use_id: str = Field(alias="toolUseId")
    status: str
    output: str | None = None
    is_error: bool | None = Field(default=None, alias="isError")
    # Real wall-clock tool duration in milliseconds. ``None`` when unknown (a
    # response with no correlated call-side start time); history replay omits the
    # ``durationMs`` field entirely in that case rather than reporting a
    # misleading ``0``.
    duration_ms: int | None = Field(default=None, alias="durationMs")
    metadata: dict[str, object] | None = None


class TurnStartedEntry(TranscriptEntryBase):
    kind: Literal["turn_started"] = "turn_started"
    turn_id: str = Field(alias="turnId")
    declared_route: str = Field(alias="declaredRoute")


class TurnCommittedEntry(TranscriptEntryBase):
    kind: Literal["turn_committed"] = "turn_committed"
    turn_id: str = Field(alias="turnId")
    input_tokens: int = Field(alias="inputTokens")
    output_tokens: int = Field(alias="outputTokens")


class TurnAbortedEntry(TranscriptEntryBase):
    kind: Literal["turn_aborted"] = "turn_aborted"
    turn_id: str = Field(alias="turnId")
    reason: str


class CompactionBoundaryEntry(TranscriptEntryBase):
    kind: Literal["compaction_boundary"] = "compaction_boundary"
    turn_id: str = Field(alias="turnId")
    boundary_id: str = Field(alias="boundaryId")
    before_token_count: int | None = Field(default=None, alias="beforeTokenCount")
    after_token_count: int | None = Field(default=None, alias="afterTokenCount")
    summary_hash: str | None = Field(default=None, alias="summaryHash")
    summary_text: str | None = Field(default=None, alias="summaryText")
    created_at: int | float | None = Field(default=None, alias="createdAt")


class CanonicalMessageEntry(TranscriptEntryBase):
    kind: Literal["canonical_message"] = "canonical_message"
    turn_id: str = Field(alias="turnId")
    message_id: str = Field(alias="messageId")
    parent_id: str | None = Field(default=None, alias="parentId")
    role: Literal["user", "assistant", "system"]
    content: list[object]


class ControlEventTranscriptEntry(TranscriptEntryBase):
    kind: Literal["control_event"] = "control_event"
    seq: int
    event_id: str = Field(alias="eventId")
    event_type: str = Field(alias="eventType")


TranscriptEntry = Annotated[
    UserMessageEntry
    | AssistantTextEntry
    | ToolCallEntry
    | ToolResultEntry
    | TurnStartedEntry
    | TurnCommittedEntry
    | TurnAbortedEntry
    | CompactionBoundaryEntry
    | CanonicalMessageEntry
    | ControlEventTranscriptEntry,
    Field(discriminator="kind"),
]

_ENTRY_ADAPTER = TypeAdapter(TranscriptEntry)


class TranscriptStore:
    def __init__(
        self,
        sessions_dir: str | Path | None = None,
        session_key: str | None = None,
        *,
        file_path: str | Path | None = None,
    ) -> None:
        if file_path is not None:
            self.file_path = Path(file_path)
            return
        if sessions_dir is None or session_key is None:
            raise ValueError("sessions_dir and session_key are required without file_path")
        digest = hashlib.sha1(session_key.encode("utf-8")).hexdigest()[:16]
        self.file_path = Path(sessions_dir) / f"{digest}.jsonl"

    def append(self, entry: TranscriptEntry) -> None:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        payload = _dump_entry(entry)
        with self.file_path.open("a", encoding="utf-8") as handle:
            handle.write(payload)
            handle.write("\n")

    def read_all(self) -> list[TranscriptEntry]:
        try:
            text = self.file_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        entries: list[TranscriptEntry] = []
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
                entries.append(_ENTRY_ADAPTER.validate_python(raw))
            except Exception:
                continue
        return entries

    def read_committed(self) -> list[TranscriptEntry]:
        entries = self.read_all()
        last_complete = -1
        for index in range(len(entries) - 1, -1, -1):
            if entries[index].kind in {"turn_committed", "turn_aborted"}:
                last_complete = index
                break
        if last_complete < 0:
            return []
        end = last_complete + 1
        for index in range(last_complete + 1, len(entries)):
            if entries[index].kind in {
                "canonical_message",
                "compaction_boundary",
                "control_event",
            }:
                end = index + 1
                continue
            break
        return entries[:end]


def _dump_entry(entry: TranscriptEntry) -> str:
    return json.dumps(
        entry.model_dump(by_alias=True, exclude_none=True),
        separators=(",", ":"),
    )
