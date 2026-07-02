from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from itertools import islice
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field

from magi_agent.runtime.public_events import rule_check_event_has_authority
from magi_agent.shared.tool_preview import sanitize_tool_preview


PublicEvent = Mapping[str, object]
SubagentStatus = Literal["running", "waiting", "done", "error", "cancelled"]
TurnPhase = Literal[
    "pending",
    "planning",
    "executing",
    "verifying",
    "committing",
    "committed",
    "aborted",
]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)
_MAX_EVENTS = 1_000
_MAX_TEXT = 2_000
_MAX_ITEM_TEXT = 240
_MAX_ITEMS = 50
_MAX_COUNTERS = 50
_RECEIPT_REF_RE = re.compile(r"^(?:receipt:)?sha256:[a-fA-F0-9]{64}$")
_RUNTIME_TYPED_DIGEST_RE = re.compile(r"^sha256:(?:activity|heartbeat):[a-fA-F0-9]{64}$")
_PUBLIC_EVIDENCE_REF_RE = re.compile(
    r"^(?:(?:receipt:)?sha256:[a-fA-F0-9]{64}|(?:evidence|source|file|result|tool-result):"
    r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,160})$"
)
_PUBLIC_REF_IN_TEXT_RE = re.compile(
    r"(?<![A-Za-z0-9_.:-])"
    r"((?:receipt:)?sha256:[a-fA-F0-9]{64}|(?:evidence|source|file|result|tool-result):"
    r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,160})"
    r"(?![A-Za-z0-9_.:-])"
)
_PRIVATE_PATH_RE = re.compile(
    r"(?:/Users|/home|/workspace|/data/bots|/var/lib/kubelet|/private|/root)"
    r"(?:/[^\s\"',}]+)*",
    re.IGNORECASE,
)
_PRIVATE_TEXT_RE = re.compile(
    r"raw[\s_-]*(?:prompt|payload|output|result|tool|source|transcript)|"
    r"(?:hidden|private)[\s_-]*(?:reasoning|prompt|payload|context|source|transcript|active[\s_-]*snapshot)|"
    r"chain[\s_-]*of[\s_-]*thought|authorization|bearer\s+|cookie|set-cookie|"
    r"api[_-]?key|secret|token|password|(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]+",
    re.IGNORECASE,
)
_SUPPORTED_BUT_NOT_SNAPSHOTTED = frozenset(
    {
        "browser_frame",
        "document_draft",
        "patch_preview",
        "response_clear",
    }
)
_RUNTIME_STATUS_EVENT_TYPES = frozenset(
    {
        "runtime_heartbeat_status",
        "runtime_stale_status",
        "runtime_resume_status",
        "runtime_watchdog_status",
    }
)
_REF_COUNTED_EVENT_TYPES = frozenset(
    {
        "turn_start",
        "turn_phase",
        "turn_end",
        "heartbeat",
        "text_delta",
        "tool_start",
        "tool_progress",
        "tool_end",
        "source_inspected",
        "rule_check",
        "spawn_started",
        "background_task",
        "spawn_result",
        "task_board",
        *_RUNTIME_STATUS_EVENT_TYPES,
        *_SUPPORTED_BUT_NOT_SNAPSHOTTED,
    }
)


class WorkConsoleSnapshot(BaseModel):
    model_config = _MODEL_CONFIG

    schema_version: Literal["workConsoleSnapshot.v1"] = Field(
        default="workConsoleSnapshot.v1",
        alias="schemaVersion",
    )
    turn_id: str | None = Field(default=None, alias="turnId")
    status: Literal["running"] = "running"
    detached: bool = False
    content: str = ""
    thinking: str = ""
    started_at: int | None = Field(default=None, alias="startedAt")
    updated_at: int | None = Field(default=None, alias="updatedAt")
    turn_phase: TurnPhase | None = Field(default=None, alias="turnPhase")
    heartbeat_elapsed_ms: int | None = Field(default=None, alias="heartbeatElapsedMs")
    active_tools: tuple[dict[str, object], ...] = Field(default=(), alias="activeTools")
    subagents: tuple[dict[str, object], ...] = ()
    task_board: dict[str, object] | None = Field(default=None, alias="taskBoard")
    runtime_statuses: tuple[dict[str, object], ...] = Field(
        default=(),
        alias="runtimeStatuses",
    )
    rule_statuses: tuple[dict[str, object], ...] = Field(default=(), alias="ruleStatuses")
    evidence_counts: dict[str, int] = Field(alias="evidenceCounts")
    unsupported_event_counters: dict[str, int] = Field(alias="unsupportedEventCounters")
    processed_event_count: int = Field(alias="processedEventCount")
    deduplicated_event_count: int = Field(alias="deduplicatedEventCount")
    projection_digest: str = Field(alias="projectionDigest")

    @classmethod
    def from_projection(cls, projection: Mapping[str, object]) -> Self:
        return cls.model_validate(projection)

    def public_projection(self) -> dict[str, object]:
        return self.model_dump(by_alias=True, mode="json")


def build_work_console_snapshot(
    events: Iterable[PublicEvent],
    *,
    max_events: int = _MAX_EVENTS,
) -> WorkConsoleSnapshot:
    if isinstance(max_events, bool) or not isinstance(max_events, int) or max_events < 1:
        raise ValueError("max_events must be a positive integer")

    builder = _SnapshotBuilder(max_events=min(max_events, _MAX_EVENTS))
    for event in islice(events, builder.max_events):
        builder.apply(event)
    return WorkConsoleSnapshot.from_projection(builder.projection())


class _SnapshotBuilder:
    def __init__(self, *, max_events: int) -> None:
        self.max_events = max_events
        self.turn_id: str | None = None
        self.turn_phase: TurnPhase | None = None
        self.heartbeat_elapsed_ms: int | None = None
        self.content = ""
        self.tools: dict[str, dict[str, object]] = {}
        self.subagents: dict[str, dict[str, object]] = {}
        self.task_board: dict[str, object] | None = None
        self.runtime_statuses: dict[str, dict[str, object]] = {}
        self.rule_statuses: dict[str, dict[str, object]] = {}
        self.unsupported_event_counters: dict[str, int] = {}
        self.source_count = 0
        self.evidence_ref_count = 0
        self.seen_event_ids: set[str] = set()
        self.processed_event_count = 0
        self.deduplicated_event_count = 0
        self.parent_turn_done = False

    def apply(self, event: PublicEvent) -> None:
        if not isinstance(event, Mapping):
            return
        event_id = _optional_id(event.get("eventId"))
        if event_id is not None:
            if event_id in self.seen_event_ids:
                self.deduplicated_event_count += 1
                return
            self.seen_event_ids.add(event_id)

        event_type = _optional_id(event.get("type"))
        if event_type is None:
            return

        self.processed_event_count += 1
        sequence = self.processed_event_count
        if _should_count_event_refs(event_type):
            self.evidence_ref_count += _evidence_ref_count(event)

        if event_type == "turn_start":
            self.turn_id = _optional_id(event.get("turnId")) or self.turn_id
            self.turn_phase = self.turn_phase or "pending"
            return
        if event_type == "turn_phase":
            self.turn_id = _optional_id(event.get("turnId")) or self.turn_id
            phase = event.get("phase")
            if phase in {"pending", "planning", "executing", "verifying", "committing", "committed", "aborted"}:
                self.turn_phase = phase
            return
        if event_type == "turn_end":
            self.turn_id = _optional_id(event.get("turnId")) or self.turn_id
            status = event.get("status")
            if status == "committed":
                self.turn_phase = "committed"
                self.parent_turn_done = True
            elif status == "aborted":
                self.turn_phase = "aborted"
                self.parent_turn_done = True
            return
        if event_type == "heartbeat":
            self.turn_id = _optional_id(event.get("turnId")) or self.turn_id
            elapsed = _finite_int(event.get("elapsedMs"))
            if elapsed is not None:
                self.heartbeat_elapsed_ms = elapsed
            return
        if event_type in _RUNTIME_STATUS_EVENT_TYPES:
            self._runtime_status(event_type, event, sequence)
            return
        if event_type == "text_delta":
            delta = _safe_text(event.get("delta"), limit=_MAX_TEXT, drop_private=True)
            if delta:
                self.content = (self.content + delta)[:_MAX_TEXT]
            return
        if event_type == "thinking_delta":
            self._unsupported(event_type)
            return
        if event_type == "tool_start":
            self._tool_start(event, sequence)
            return
        if event_type == "tool_progress":
            self._tool_progress(event, sequence)
            return
        if event_type == "tool_end":
            self._tool_end(event)
            return
        if event_type == "source_inspected":
            if isinstance(event.get("source"), Mapping) and _has_public_work_ref(event):
                self.source_count += 1
            return
        if event_type == "rule_check":
            self._rule_check(event, sequence)
            return
        if event_type in {"spawn_started", "background_task", "spawn_result"}:
            self._spawn_event(event_type, event, sequence)
            return
        if event_type.startswith("child_"):
            self._child_event(event_type, event, sequence)
            return
        if event_type == "task_board":
            self._task_board(event, sequence)
            return
        if event_type in _SUPPORTED_BUT_NOT_SNAPSHOTTED:
            return
        self._unsupported(event_type)

    def projection(self) -> dict[str, object]:
        detached = self.parent_turn_done and any(
            subagent.get("status") in {"running", "waiting"}
            for subagent in self.subagents.values()
        )
        payload: dict[str, object] = {
            "schemaVersion": "workConsoleSnapshot.v1",
            "turnId": self.turn_id,
            "status": "running",
            "detached": detached,
            "content": self.content,
            "thinking": "",
            "startedAt": None,
            "updatedAt": self.processed_event_count or None,
            "turnPhase": self.turn_phase,
            "heartbeatElapsedMs": self.heartbeat_elapsed_ms,
            "activeTools": tuple(_sort_records(self.tools.values(), "id")),
            "subagents": tuple(_sort_records(self.subagents.values(), "taskId")),
            "taskBoard": self.task_board,
            "runtimeStatuses": tuple(self.runtime_statuses.values()),
            "ruleStatuses": tuple(_sort_records(self.rule_statuses.values(), "ruleId")),
            "evidenceCounts": {
                "evidenceRefs": self.evidence_ref_count,
                "sources": self.source_count,
            },
            "unsupportedEventCounters": _bounded_counter_dict(self.unsupported_event_counters),
            "processedEventCount": self.processed_event_count,
            "deduplicatedEventCount": self.deduplicated_event_count,
        }
        payload["projectionDigest"] = _digest_projection(payload)
        return payload

    def _tool_start(self, event: PublicEvent, sequence: int) -> None:
        tool_id = _optional_id(event.get("id"))
        if tool_id is None:
            return
        if not _has_public_work_ref(event):
            return
        label = _safe_text(event.get("name"), fallback="tool")
        self.tools[tool_id] = {
            "id": tool_id,
            "label": label,
            "status": "running",
            "startedAt": sequence,
            "updatedAt": sequence,
        }

    def _tool_progress(self, event: PublicEvent, sequence: int) -> None:
        tool_id = _optional_id(event.get("id"))
        if tool_id is None or tool_id not in self.tools:
            return
        label = _safe_text(event.get("label"), fallback="")
        if label:
            self.tools[tool_id]["label"] = label
        self.tools[tool_id]["status"] = "running"
        self.tools[tool_id]["updatedAt"] = sequence

    def _tool_end(self, event: PublicEvent) -> None:
        tool_id = _optional_id(event.get("id"))
        if tool_id is not None:
            self.tools.pop(tool_id, None)

    def _rule_check(self, event: PublicEvent, sequence: int) -> None:
        rule_id = _optional_id(event.get("ruleId"))
        verdict = event.get("verdict")
        if (
            rule_id is None
            or verdict not in {"pending", "ok", "violation"}
            or not _has_public_work_ref(event)
            or (verdict != "pending" and not rule_check_event_has_authority(event))
        ):
            return
        record: dict[str, object] = {
            "ruleId": rule_id,
            "verdict": verdict,
            "checkedAt": _finite_int(event.get("checkedAt")) or sequence,
        }
        detail = _safe_text(event.get("detail"), fallback="")
        if detail:
            record["detail"] = detail
        self.rule_statuses[rule_id] = record

    def _runtime_status(self, event_type: str, event: PublicEvent, sequence: int) -> None:
        record: dict[str, object] = {
            "type": event_type,
            "updatedAt": sequence,
        }
        for key in ("status", "decision", "alertKind"):
            text = _safe_text(event.get(key), fallback="", limit=120)
            if text:
                record[key] = text
        for key in (
            "runDigest",
            "heartbeatDigest",
            "leaseDigest",
            "activityDigest",
            "checkpointDigest",
            "verdictDigest",
            "watchdogDigest",
            "tickDigest",
            "jobDigest",
            "stdoutDigest",
        ):
            digest = _public_runtime_digest(event.get(key))
            if digest is not None:
                record[key] = digest
        for key in ("sequence", "exitCode", "durationMs"):
            number = _finite_int(event.get(key))
            if number is not None:
                record[key] = number
        for key in ("alertRequired", "timedOut", "recursiveSchedulerDenied"):
            value = event.get(key)
            if isinstance(value, bool):
                record[key] = value
        self.runtime_statuses[event_type] = record

    def _spawn_event(self, event_type: str, event: PublicEvent, sequence: int) -> None:
        task_id = _optional_id(event.get("taskId"))
        if task_id is None:
            return
        if not _has_public_work_ref(event):
            return
        existing = self.subagents.get(task_id)
        role = _safe_text(event.get("persona"), fallback=str(existing.get("role", "child")) if existing else "child")
        status: SubagentStatus = "running"
        if event_type == "background_task":
            status = _background_status(event.get("status"))
        elif event_type == "spawn_result":
            status = _spawn_result_status(event.get("status"))
        detail = _safe_text(event.get("detail", event.get("errorMessage")), fallback="")
        self.subagents[task_id] = {
            "taskId": task_id,
            "role": role,
            "status": status,
            **({"detail": detail} if detail else {}),
            "startedAt": int(existing.get("startedAt", sequence)) if existing else sequence,
            "updatedAt": sequence,
        }

    def _child_event(self, event_type: str, event: PublicEvent, sequence: int) -> None:
        task_id = _optional_id(event.get("taskId"))
        if task_id is None:
            return
        if not _has_public_work_ref(event):
            return
        existing = self.subagents.get(task_id)
        status: SubagentStatus = "running"
        if event_type == "child_completed":
            status = "done"
        elif event_type == "child_failed":
            status = "error"
        elif event_type in {"child_cancelled", "child_abort"}:
            status = "cancelled"
        elif event_type == "child_tool_request":
            status = "waiting"
        detail = _safe_text(
            event.get("detail", event.get("reason", event.get("errorMessage", event.get("toolName")))),
            fallback="",
        )
        self.subagents[task_id] = {
            "taskId": task_id,
            "role": str(existing.get("role", "child")) if existing else "child",
            "status": status,
            **({"detail": detail} if detail else {}),
            "startedAt": int(existing.get("startedAt", sequence)) if existing else sequence,
            "updatedAt": sequence,
        }

    def _task_board(self, event: PublicEvent, sequence: int) -> None:
        tasks = event.get("tasks")
        if not isinstance(tasks, Sequence) or isinstance(tasks, str | bytes):
            return
        safe_tasks: list[dict[str, object]] = []
        for item in tasks[:_MAX_ITEMS]:
            if not isinstance(item, Mapping):
                continue
            task_id = _optional_id(item.get("id"))
            title = _safe_text(item.get("title"), fallback="")
            status = item.get("status")
            if (
                task_id is None
                or not title
                or status not in {"pending", "in_progress", "completed", "cancelled"}
            ):
                continue
            task: dict[str, object] = {
                "id": task_id,
                "title": title,
                "description": _safe_text(item.get("description"), fallback=""),
                "status": status,
            }
            safe_tasks.append(task)
        self.task_board = {"receivedAt": sequence, "tasks": tuple(safe_tasks)}

    def _unsupported(self, event_type: str) -> None:
        if len(self.unsupported_event_counters) >= _MAX_COUNTERS:
            return
        self.unsupported_event_counters[event_type] = (
            self.unsupported_event_counters.get(event_type, 0) + 1
        )


def _safe_text(
    value: object,
    *,
    fallback: str = "",
    limit: int = _MAX_ITEM_TEXT,
    drop_private: bool = False,
) -> str:
    if not isinstance(value, str) or not value.strip():
        return fallback
    if _is_private_text(value):
        return fallback if drop_private else _safe_fallback(fallback)
    text = sanitize_tool_preview(value.strip())
    if _is_private_text(text):
        return fallback if drop_private else _safe_fallback(fallback)
    return text[:limit]


def _safe_fallback(value: str) -> str:
    return value if value and not _is_private_text(value) else ""


def _is_private_text(value: str) -> bool:
    return _PRIVATE_PATH_RE.search(value) is not None or _PRIVATE_TEXT_RE.search(value) is not None


def _optional_id(value: object) -> str | None:
    text = _safe_text(value, fallback="", limit=120)
    return text or None


def _finite_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if not math.isfinite(float(value)):
        return None
    return max(0, int(value))


def _evidence_ref_count(value: object) -> int:
    refs: set[str] = set()

    def visit(item: object) -> None:
        if isinstance(item, Mapping):
            for key, nested in item.items():
                if key in {
                    "evidenceRef",
                    "contentHash",
                    "childReceiptRef",
                    "inputDigest",
                    "outputDigest",
                    "receiptRef",
                }:
                    maybe_ref = _public_ref(nested)
                    if maybe_ref is not None:
                        refs.add(maybe_ref)
                elif key == "transcriptRefs" and isinstance(nested, Sequence):
                    for ref in nested:
                        maybe_ref = _public_ref(ref)
                        if maybe_ref is not None:
                            refs.add(maybe_ref)
                elif key == "source" and isinstance(nested, Mapping):
                    visit(nested)
                elif isinstance(nested, str):
                    refs.update(_public_refs_in_text(nested))

    visit(value)
    return len(refs)


def _has_public_work_ref(event: Mapping[str, object]) -> bool:
    return _evidence_ref_count(event) > 0


def _should_count_event_refs(event_type: str) -> bool:
    return event_type in _REF_COUNTED_EVENT_TYPES or event_type.startswith("child_")


def _public_refs_in_text(value: str) -> set[str]:
    refs: set[str] = set()
    for match in _PUBLIC_REF_IN_TEXT_RE.finditer(value):
        maybe_ref = _public_ref(match.group(1))
        if maybe_ref is not None:
            refs.add(maybe_ref)
    return refs


def _public_ref(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    if _RUNTIME_TYPED_DIGEST_RE.fullmatch(value) is not None:
        return value
    if _RECEIPT_REF_RE.fullmatch(value) is not None:
        return value
    if _PUBLIC_EVIDENCE_REF_RE.fullmatch(value) is not None:
        return value
    return None


def _public_runtime_digest(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    if _RUNTIME_TYPED_DIGEST_RE.fullmatch(value) is not None:
        return value
    if _RECEIPT_REF_RE.fullmatch(value) is not None:
        return value
    if re.fullmatch(r"^sha256:[a-fA-F0-9]{64}$", value) is not None:
        return value
    return None


def _background_status(value: object) -> SubagentStatus:
    if value == "completed":
        return "done"
    if value == "failed":
        return "error"
    if value == "aborted":
        return "cancelled"
    return "running"


def _spawn_result_status(value: object) -> SubagentStatus:
    if value == "ok":
        return "done"
    if value == "aborted":
        return "cancelled"
    return "error"


def _sort_records(records: object, key: str) -> tuple[dict[str, object], ...]:
    if not isinstance(records, Sequence):
        records = tuple(records)
    return tuple(
        sorted(
            (dict(record) for record in records if isinstance(record, Mapping)),
            key=lambda record: str(record.get(key, "")),
        )
    )


def _bounded_counter_dict(counters: Mapping[str, int]) -> dict[str, int]:
    return {
        key: int(value)
        for key, value in sorted(counters.items())[:_MAX_COUNTERS]
        if value > 0
    }


def _digest_projection(payload: Mapping[str, object]) -> str:
    digest_payload = dict(payload)
    digest_payload.pop("projectionDigest", None)
    encoded = json.dumps(digest_payload, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


__all__ = ["WorkConsoleSnapshot", "build_work_console_snapshot"]
