from __future__ import annotations

import hashlib
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from magi_agent.shared.tool_preview import sanitize_tool_preview


ControlRequestKind = Literal["tool_permission", "plan_approval", "user_question"]
ControlRequestSource = Literal["turn", "mcp", "child-agent", "plan", "system"]
ControlRequestState = Literal[
    "pending",
    "approved",
    "denied",
    "answered",
    "cancelled",
    "timed_out",
]
ControlRequestDecision = Literal["approved", "denied", "answered"]

CONTROL_EVENT_TYPES = {
    "retry",
    "runtime_trace",
    "permission_decision",
    "control_request_created",
    "control_request_resolved",
    "control_request_cancelled",
    "control_request_timed_out",
    "plan_lifecycle",
    "tool_use_summary",
    "structured_output",
    "verification",
    "stop_reason",
    "task_board_snapshot",
    "child_started",
    "child_progress",
    "child_tool_request",
    "child_permission_decision",
    "child_cancelled",
    "child_failed",
    "child_completed",
    "compaction_boundary",
}


class ControlRequest(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    request_id: str = Field(alias="requestId")
    turn_id: str = Field(alias="turnId")
    tool_name: str = Field(alias="toolName")
    arguments: dict[str, object]
    reason: str


class ControlEventBase(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    v: Literal[1] = 1
    type: str
    event_id: str = Field(alias="eventId")
    seq: int
    ts: int | float
    session_key: str = Field(alias="sessionKey")
    turn_id: str | None = Field(default=None, alias="turnId")
    idempotency_key: str | None = Field(default=None, alias="idempotencyKey")


class ControlEventTranscriptReference(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    kind: Literal["control_event"] = "control_event"
    ts: int | float
    turn_id: str | None = Field(default=None, alias="turnId")
    seq: int
    event_id: str = Field(alias="eventId")
    event_type: str = Field(alias="eventType")


class PermissionDecisionControlEvent(ControlEventBase):
    type: Literal["permission_decision"] = "permission_decision"
    source: ControlRequestSource
    request_id: str | None = Field(default=None, alias="requestId")
    tool_name: str | None = Field(default=None, alias="toolName")
    decision: Literal["allow", "deny", "ask"]
    reason: str | None = None
    updated_input: object | None = Field(default=None, alias="updatedInput")

    @field_validator("updated_input", mode="before")
    @classmethod
    def _redact_updated_input(cls, value: object) -> object:
        return _sanitize_public_value(value)


class ControlRequestRecord(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    request_id: str = Field(alias="requestId")
    kind: ControlRequestKind
    state: ControlRequestState
    session_key: str = Field(alias="sessionKey")
    turn_id: str | None = Field(default=None, alias="turnId")
    channel_name: str | None = Field(default=None, alias="channelName")
    source: ControlRequestSource
    prompt: str
    proposed_input: object | None = Field(default=None, alias="proposedInput")
    created_at: int | float = Field(alias="createdAt")
    expires_at: int | float = Field(alias="expiresAt")
    resolved_at: int | float | None = Field(default=None, alias="resolvedAt")
    decision: ControlRequestDecision | None = None
    feedback: str | None = None
    updated_input: object | None = Field(default=None, alias="updatedInput")
    answer: str | None = None
    cancel_reason: str | None = Field(default=None, alias="cancelReason")
    idempotency_key: str | None = Field(default=None, alias="idempotencyKey")
    waiter_resolution: dict[str, object] | None = Field(
        default=None,
        alias="waiterResolution",
    )

    @field_validator("proposed_input", "updated_input", mode="before")
    @classmethod
    def _redact_public_inputs(cls, value: object) -> object:
        return _sanitize_public_value(value)


class ControlRequestCreatedEvent(ControlEventBase):
    type: Literal["control_request_created"] = "control_request_created"
    request: ControlRequestRecord


class ControlRequestResolvedEvent(ControlEventBase):
    type: Literal["control_request_resolved"] = "control_request_resolved"
    request_id: str = Field(alias="requestId")
    decision: ControlRequestDecision
    feedback: str | None = None
    updated_input: object | None = Field(default=None, alias="updatedInput")
    answer: str | None = None

    @field_validator("updated_input", mode="before")
    @classmethod
    def _redact_updated_input(cls, value: object) -> object:
        return _sanitize_public_value(value)


class ControlRequestCancelledEvent(ControlEventBase):
    type: Literal["control_request_cancelled"] = "control_request_cancelled"
    request_id: str = Field(alias="requestId")
    reason: str


class ControlRequestTimedOutEvent(ControlEventBase):
    type: Literal["control_request_timed_out"] = "control_request_timed_out"
    request_id: str = Field(alias="requestId")


class ControlEventLedger:
    def __init__(self) -> None:
        self.events: list[ControlEventBase] = []

    def append(self, event: ControlEventBase) -> None:
        if self.events and event.seq <= self.events[-1].seq:
            raise ValueError("control event sequence must be monotonic")
        self.events.append(event)


def make_transcript_reference(event: ControlEventBase) -> ControlEventTranscriptReference:
    return ControlEventTranscriptReference(
        ts=event.ts,
        turn_id=event.turn_id,
        seq=event.seq,
        event_id=event.event_id,
        event_type=event.type,
    )


ControlRequestTerminalEvent = (
    ControlRequestCreatedEvent
    | ControlRequestResolvedEvent
    | ControlRequestCancelledEvent
    | ControlRequestTimedOutEvent
)

_PRIVATE_PATH_RE = re.compile(
    r"(?:"
    r"/Users/[^/\s,;}\"']+(?:/[^\s,;}\"']*)?"
    r"|/home/[^/\s,;}\"']+(?:/[^\s,;}\"']*)?"
    r"|/private/var(?:/[^\s,;}\"']*)?"
    r"|/workspace(?:/[^\s,;}\"']*)?"
    r"|/data/bots(?:/[^\s,;}\"']*)?"
    r"|/var/lib/kubelet(?:/[^\s,;}\"']*)?"
    r"|/tmp/(?:opencode-inspect|openmagi-inspect|openmagi-workspace-[^/\s,;}\"']+|"
    r"[^/\s,;}\"']*(?:workspace|inspect)[^/\s,;}\"']*)(?:/[^\s,;}\"']*)?"
    r")"
)
_PATCH_BODY_RE = re.compile(
    r"\*\*\* Begin Patch[\s\S]*?(?:\*\*\* End Patch|$)",
    re.IGNORECASE,
)
_CAMEL_BOUNDARY_RE = re.compile(
    r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])"
)
_NON_ALNUM_RE = re.compile(r"[^0-9A-Za-z]+")
_MAX_PUBLIC_FIELD_LENGTH = 240


class ControlRequestStoreResult(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    record: ControlRequestRecord
    events: tuple[ControlRequestTerminalEvent, ...] = ()
    duplicate: bool = False

    @property
    def event(self) -> ControlRequestTerminalEvent | None:
        return self.events[0] if self.events else None


class ControlRequestStore:
    """Disabled-by-default in-memory control lifecycle boundary."""

    durable_writes_enabled: Literal[False] = False
    production_writes_enabled: Literal[False] = False

    def __init__(self) -> None:
        self._pending_by_id: dict[str, ControlRequestRecord] = {}
        self._terminal_by_id: dict[str, ControlRequestRecord] = {}
        self._idempotency_to_request_id: dict[str, str] = {}
        self._seq = 0
        self.ledger = ControlEventLedger()

    @property
    def pending_requests(self) -> tuple[ControlRequestRecord, ...]:
        return tuple(self._pending_by_id.values())

    @property
    def terminal_requests(self) -> tuple[ControlRequestRecord, ...]:
        return tuple(self._terminal_by_id.values())

    def get_pending(self, request_id: str) -> ControlRequestRecord | None:
        return self._pending_by_id.get(request_id)

    def get_terminal(self, request_id: str) -> ControlRequestRecord | None:
        return self._terminal_by_id.get(request_id)

    def create_tool_permission_request(
        self,
        *,
        session_key: str,
        turn_id: str | None,
        channel_name: str | None,
        source: ControlRequestSource,
        prompt: str,
        proposed_input: object | None,
        idempotency_key: str,
        now: int | float,
        timeout_ms: int | float,
    ) -> ControlRequestStoreResult:
        if not idempotency_key.strip():
            raise ValueError("idempotency_key must be non-empty")
        duplicate = self._record_for_idempotency(idempotency_key)
        if duplicate is not None:
            return ControlRequestStoreResult(record=duplicate, duplicate=True)

        request_id = _stable_request_id(idempotency_key)
        record = ControlRequestRecord(
            requestId=request_id,
            kind="tool_permission",
            state="pending",
            sessionKey=session_key,
            turnId=turn_id,
            channelName=channel_name,
            source=source,
            prompt=_sanitize_prompt_text(prompt),
            proposedInput=_sanitize_public_value(proposed_input),
            createdAt=now,
            expiresAt=now + timeout_ms,
            idempotencyKey=idempotency_key,
        )
        self._pending_by_id[record.request_id] = record
        self._idempotency_to_request_id[idempotency_key] = record.request_id
        event = ControlRequestCreatedEvent(
            **self._event_base(
                "created",
                session_key=session_key,
                turn_id=turn_id,
                idempotency_key=idempotency_key,
                ts=now,
            ),
            request=record,
        )
        self.ledger.append(event)
        return ControlRequestStoreResult(record=record, events=(event,))

    def create_user_question_request(
        self,
        *,
        session_key: str,
        turn_id: str | None,
        channel_name: str | None,
        source: ControlRequestSource,
        prompt: str,
        proposed_input: object | None,
        idempotency_key: str,
        now: int | float,
        timeout_ms: int | float,
    ) -> ControlRequestStoreResult:
        """Create a ``user_question`` control request (clarifying question).

        Mirrors :meth:`create_tool_permission_request` exactly but stamps the
        record ``kind`` as ``user_question`` so the existing resume flow
        (:meth:`resolve_request` with ``decision="answered"`` + ``answer``)
        applies unchanged. No new resume mechanism is introduced — this only
        widens the kind of request the existing store can open.
        """
        if not idempotency_key.strip():
            raise ValueError("idempotency_key must be non-empty")
        duplicate = self._record_for_idempotency(idempotency_key)
        if duplicate is not None:
            return ControlRequestStoreResult(record=duplicate, duplicate=True)

        request_id = _stable_request_id(idempotency_key)
        record = ControlRequestRecord(
            requestId=request_id,
            kind="user_question",
            state="pending",
            sessionKey=session_key,
            turnId=turn_id,
            channelName=channel_name,
            source=source,
            prompt=_sanitize_prompt_text(prompt),
            proposedInput=_sanitize_public_value(proposed_input),
            createdAt=now,
            expiresAt=now + timeout_ms,
            idempotencyKey=idempotency_key,
        )
        self._pending_by_id[record.request_id] = record
        self._idempotency_to_request_id[idempotency_key] = record.request_id
        event = ControlRequestCreatedEvent(
            **self._event_base(
                "created",
                session_key=session_key,
                turn_id=turn_id,
                idempotency_key=idempotency_key,
                ts=now,
            ),
            request=record,
        )
        self.ledger.append(event)
        return ControlRequestStoreResult(record=record, events=(event,))

    def resolve_request(
        self,
        request_id: str,
        *,
        decision: ControlRequestDecision,
        now: int | float,
        feedback: str | None = None,
        updated_input: object | None = None,
        answer: str | None = None,
    ) -> ControlRequestStoreResult:
        terminal = self._terminal_by_id.get(request_id)
        if terminal is not None:
            if terminal.decision != decision:
                raise ValueError("terminal control request cannot be resolved differently")
            return ControlRequestStoreResult(record=terminal, duplicate=True)
        pending = self._pending_by_id.pop(request_id, None)
        if pending is None:
            raise KeyError(f"unknown control request: {request_id}")

        record = pending.model_copy(
            update={
                "state": decision,
                "resolved_at": now,
                "decision": decision,
                "feedback": _sanitize_optional_public_text(feedback),
                "updated_input": _sanitize_public_value(updated_input),
                "answer": _sanitize_optional_public_text(answer),
            }
        )
        self._terminal_by_id[request_id] = record
        event = ControlRequestResolvedEvent(
            **self._event_base(
                "resolved",
                session_key=record.session_key,
                turn_id=record.turn_id,
                idempotency_key=f"{record.idempotency_key}:resolved:{decision}"
                if record.idempotency_key
                else None,
                ts=now,
            ),
            requestId=request_id,
            decision=decision,
            feedback=record.feedback,
            updatedInput=record.updated_input,
            answer=record.answer,
        )
        self.ledger.append(event)
        return ControlRequestStoreResult(record=record, events=(event,))

    def expire_request(
        self,
        request_id: str,
        *,
        now: int | float,
    ) -> ControlRequestStoreResult | None:
        terminal = self._terminal_by_id.get(request_id)
        if terminal is not None:
            if terminal.state == "timed_out":
                return ControlRequestStoreResult(record=terminal, duplicate=True)
            return None
        pending = self._pending_by_id.get(request_id)
        if pending is None or now < pending.expires_at:
            return None
        self._pending_by_id.pop(request_id, None)
        record = pending.model_copy(
            update={
                "state": "timed_out",
                "resolved_at": now,
                "waiter_resolution": {
                    "decision": "denied",
                    "execute": False,
                    "reason": "control_request_timed_out",
                },
            }
        )
        self._terminal_by_id[request_id] = record
        event = ControlRequestTimedOutEvent(
            **self._event_base(
                "timed-out",
                session_key=record.session_key,
                turn_id=record.turn_id,
                idempotency_key=f"{record.idempotency_key}:timed_out"
                if record.idempotency_key
                else None,
                ts=now,
            ),
            requestId=request_id,
        )
        self.ledger.append(event)
        return ControlRequestStoreResult(record=record, events=(event,))

    def cancel_request(
        self,
        request_id: str,
        *,
        reason: str,
        now: int | float,
    ) -> ControlRequestStoreResult:
        terminal = self._terminal_by_id.get(request_id)
        if terminal is not None:
            if terminal.state != "cancelled":
                raise ValueError("terminal control request cannot be cancelled")
            return ControlRequestStoreResult(record=terminal, duplicate=True)
        pending = self._pending_by_id.pop(request_id, None)
        if pending is None:
            raise KeyError(f"unknown control request: {request_id}")
        record = pending.model_copy(
            update={
                "state": "cancelled",
                "resolved_at": now,
                "cancel_reason": _sanitize_public_text(reason),
            }
        )
        self._terminal_by_id[request_id] = record
        event = ControlRequestCancelledEvent(
            **self._event_base(
                "cancelled",
                session_key=record.session_key,
                turn_id=record.turn_id,
                idempotency_key=f"{record.idempotency_key}:cancelled"
                if record.idempotency_key
                else None,
                ts=now,
            ),
            requestId=request_id,
            reason=record.cancel_reason or "cancelled",
        )
        self.ledger.append(event)
        return ControlRequestStoreResult(record=record, events=(event,))

    def _record_for_idempotency(self, idempotency_key: str) -> ControlRequestRecord | None:
        request_id = self._idempotency_to_request_id.get(idempotency_key)
        if request_id is None:
            return None
        return self._pending_by_id.get(request_id) or self._terminal_by_id.get(request_id)

    def _event_base(
        self,
        suffix: str,
        *,
        session_key: str,
        turn_id: str | None,
        idempotency_key: str | None,
        ts: int | float,
    ) -> dict[str, object]:
        self._seq += 1
        event_material = f"{session_key}:{turn_id}:{suffix}:{self._seq}"
        return {
            "eventId": f"ctrl_evt_{_sha256_short(event_material)}",
            "seq": self._seq,
            "ts": ts,
            "sessionKey": session_key,
            "turnId": turn_id,
            "idempotencyKey": idempotency_key,
        }


def _stable_request_id(idempotency_key: str) -> str:
    return f"ctrl_req_{_sha256_short(idempotency_key)}"


def _sha256_short(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _sanitize_optional_public_text(value: str | None) -> str | None:
    if value is None:
        return None
    return _sanitize_public_text(value)


def _sanitize_public_text(value: str) -> str:
    redacted = sanitize_tool_preview(value)
    redacted = _PATCH_BODY_RE.sub("[redacted-patch]", redacted)
    redacted = _PRIVATE_PATH_RE.sub("[redacted-path]", redacted)
    if len(redacted) > _MAX_PUBLIC_FIELD_LENGTH:
        return f"{redacted[: _MAX_PUBLIC_FIELD_LENGTH - 3]}..."
    return redacted


def _sanitize_prompt_text(_value: str) -> str:
    return "[redacted-prompt]"


def _sanitize_public_value(value: object) -> object:
    if value is None:
        return None
    if isinstance(value, dict):
        sanitized: dict[str, object] = {}
        for key, nested in value.items():
            key_text = str(key)
            normalized, compact = _public_key_forms(key_text)
            if _is_public_secret_key(normalized):
                sanitized[key_text] = "[redacted]"
            elif compact in {"command", "cmd", "shell"}:
                sanitized["commandPreview"] = "[redacted-command]"
            elif normalized in {"patch", "patch_body", "diff", "body"}:
                sanitized[f"{key_text}Preview"] = "[redacted-body]"
            elif compact == "prompt":
                sanitized[f"{key_text}Preview"] = "[redacted-prompt]"
            elif normalized in {"logs", "log", "stdout", "stderr", "output"}:
                sanitized[f"{key_text}Preview"] = "[redacted-output]"
            elif normalized.endswith("_path") or compact in {"path", "file"}:
                sanitized[key_text] = _sanitize_public_text(str(nested))
            else:
                sanitized[key_text] = _sanitize_public_value(nested)
        return sanitized
    if isinstance(value, list | tuple):
        return [_sanitize_public_value(item) for item in value]
    if isinstance(value, str):
        return _sanitize_public_text(value)
    return value


def _public_key_forms(key: str) -> tuple[str, str]:
    separated = _CAMEL_BOUNDARY_RE.sub("_", key)
    normalized = _NON_ALNUM_RE.sub("_", separated).strip("_").lower()
    compact = _NON_ALNUM_RE.sub("", key).lower()
    return normalized, compact


def _is_public_secret_key(normalized_key: str) -> bool:
    compact = normalized_key.replace("_", "")
    return any(
        fragment in normalized_key or fragment in compact
        for fragment in (
            "authorization",
            "cookie",
            "apikey",
            "api_key",
            "credential",
            "secret",
            "token",
            "password",
            "privatekey",
            "private_key",
            "servicerolekey",
            "service_role_key",
        )
    )
