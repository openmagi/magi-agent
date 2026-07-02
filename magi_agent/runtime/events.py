from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Iterable, Mapping
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from magi_agent.runtime.transcript import (
    AssistantTextEntry,
    ControlEventTranscriptEntry,
    ToolCallEntry,
    ToolResultEntry,
    TranscriptEntry,
    TurnAbortedEntry,
    TurnCommittedEntry,
    TurnStartedEntry,
)
from magi_agent.runtime.heartbeat_contract import (
    HeartbeatReceipt,
    ResumeDecision,
    StaleRunVerdict,
)
from magi_agent.runtime.no_agent_watchdog import NoAgentWatchdogDecision
from magi_agent.runtime.public_events import (
    authorize_rule_check_event,
    copy_rule_check_authority,
    is_rule_check_authority_field,
    rule_check_event_has_authority,
)
from magi_agent.shared.tool_preview import sanitize_tool_preview


EventKind = Literal["status", "token", "tool", "control", "artifact", "error"]


class RuntimeEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: EventKind
    payload: dict[str, object]
    turn_id: str | None = None


NormalizedEventType = Literal[
    "turn.started",
    "runtime.phase",
    "runtime.heartbeat",
    "runtime.heartbeat.status",
    "runtime.stale_run.status",
    "runtime.resume.status",
    "runtime.watchdog.status",
    "runtime.trace",
    "model.message.delta",
    "model.message.completed",
    "tool.call.started",
    "tool.call.progress",
    "tool.call.needs_approval",
    "tool.call.completed",
    "tool.call.denied",
    "tool.call.failed",
    "source.inspected",
    "rule.check",
    "child.started",
    "child.progress",
    "child.completed",
    "child.cancelled",
    "child.failed",
    "control.requested",
    "control.resumed",
    "turn.completed",
    "turn.failed",
]
NormalizedEventSource = Literal["adk", "tool_kernel", "control", "runtime"]

_DIGEST_PREFIX = "sha256:"
_PRIVATE_PATH_RE = re.compile(
    r"(?:/data/bots|/workspace|/var/lib/kubelet|/Users|/home|/private|/mnt)"
    r"(?:/[^\s\"',}]+)*",
    re.IGNORECASE,
)
_SENSITIVE_REF_FRAGMENTS = (
    "auth",
    "cookie",
    "credential",
    "key",
    "password",
    "private",
    "secret",
    "session",
    "token",
)
_PRIVATE_METADATA_KEY_FRAGMENTS = (
    "authorization",
    "childoutput",
    "childprompt",
    "childtranscript",
    "cookie",
    "credential",
    "hiddenreasoning",
    "memorypayload",
    "privatecontext",
    "privatememory",
    "prompt",
    "rawargs",
    "rawarguments",
    "rawinput",
    "rawoutput",
    "rawresponse",
    "rawresult",
    "rawtoolargs",
    "rawtoolresult",
    "rawtooloutput",
    "sessionkey",
)
_PRIVATE_TEXT_RE = re.compile(
    r"\b(?:"
    r"hidden\s+reasoning|"
    r"chain[-\s]?of[-\s]?thought|"
    r"raw\s+(?:(?:[a-z0-9_-]+\s+){0,3}(?:payload|response|output|"
    r"result|body|transcript)|prompt|adk\s+event)|"
    r"(?:raw\s+)?tool\s+(?:args?|arguments?|inputs?|outputs?|results?|responses?|logs?)|"
    r"(?:raw\s+)?source\s+snapshot|"
    r"(?:raw\s+)?(?:system\s+|developer\s+|user\s+)?prompt|"
    r"private\s+(?:active\s+snapshot|prompt|payload|context|memory|transcript|source)"
    r")\b",
    re.IGNORECASE,
)
_PRIVATE_TEXT_MARKER_FRAGMENTS = (
    "hiddenreasoning",
    "chainofthought",
    "rawpayload",
    "rawproviderpayload",
    "rawmodelpayload",
    "rawproviderresponse",
    "rawmodelresponse",
    "rawchildtranscript",
    "rawchildoutput",
    "rawadkevent",
    "rawprompt",
    "rawtoolargs",
    "rawtoolarguments",
    "rawtoolinput",
    "rawtooloutput",
    "rawtoolresult",
    "rawtoolresponse",
    "toolargs",
    "toolarguments",
    "toolinput",
    "tooloutput",
    "toolresult",
    "toolresponse",
    "toollog",
    "toollogs",
    "sourcesnapshot",
    "rawsourcesnapshot",
    "systemprompt",
    "developerprompt",
    "userprompt",
    "privateactivesnapshot",
    "privateprompt",
    "privatepayload",
    "privatecontext",
    "privatememory",
    "privatetranscript",
    "privatesource",
)
_DIGEST_REF_RE = re.compile(r"^sha256:[a-fA-F0-9]{64}$")
_TYPED_DIGEST_REF_RE = re.compile(r"^sha256:(?:activity|heartbeat):[a-fA-F0-9]{64}$")
_PUBLIC_EVIDENCE_REF_RE = re.compile(
    r"^(?:(?:receipt:)?sha256:[a-fA-F0-9]{64}|(?:evidence|source|file|result|tool-result):"
    r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,160})$"
)
_RECEIPT_REF_RE = re.compile(r"^(?:receipt:)?sha256:[a-fA-F0-9]{64}$")
_PUBLIC_REASON_CODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}$")
_PRIVATE_PUBLIC_REF_FRAGMENTS = frozenset(_PRIVATE_TEXT_MARKER_FRAGMENTS)
_SOURCE_KINDS = frozenset(
    {
        "web_search",
        "web_fetch",
        "browser",
        "kb",
        "file",
        "external_repo",
        "external_doc",
        "subagent_result",
    }
)
_TRUST_TIERS = frozenset({"primary", "official", "secondary", "unknown"})
_TURN_PHASES = frozenset(
    {
        "pending",
        "planning",
        "executing",
        "verifying",
        "committing",
        "committed",
        "aborted",
    }
)
_RULE_VERDICTS = frozenset({"pending", "ok", "violation"})
_RUNTIME_TRACE_PHASES = frozenset(
    {"verifier_blocked", "retry_scheduled", "retry_aborted", "terminal_abort"}
)
_RUNTIME_TRACE_SEVERITIES = frozenset({"info", "warning", "error"})
_RUNTIME_STATUS_EVENT_TYPES = frozenset(
    {
        "runtime.heartbeat.status",
        "runtime.stale_run.status",
        "runtime.resume.status",
        "runtime.watchdog.status",
    }
)
_RUNTIME_STATUS_PUBLIC_TYPES = {
    "runtime.heartbeat.status": "runtime_heartbeat_status",
    "runtime.stale_run.status": "runtime_stale_status",
    "runtime.resume.status": "runtime_resume_status",
    "runtime.watchdog.status": "runtime_watchdog_status",
}
_RUNTIME_STATUS_FALSE_FIELDS = (
    "liveAuthority",
    "trafficAttached",
    "wakeAgent",
    "schedulerAttached",
    "modelCallEnabled",
    "providerCallEnabled",
    "toolExecutionEnabled",
    "channelDeliveryEnabled",
    "workspaceMutationEnabled",
    "memoryWriteEnabled",
    "productionWritesEnabled",
    "runnerInvoked",
    "resumeExecutionAllowed",
)
_RUNTIME_STATUS_BY_TYPE = {
    "runtime.heartbeat.status": frozenset({"heartbeat_recorded"}),
    "runtime.stale_run.status": frozenset(
        {
            "healthy",
            "silent_but_within_threshold",
            "inactive_timeout",
            "lease_expired",
            "worker_lost",
            "rollback_required",
            "resume_pending",
            "cancelled",
            "blocked_for_operator",
        }
    ),
    "runtime.watchdog.status": frozenset(
        {
            "silent_healthy",
            "alert_output",
            "alert_failure",
            "alert_timeout",
            "blocked_recursive_scheduler",
        }
    ),
}
_RUNTIME_WATCHDOG_ALERT_KINDS = frozenset(
    {"none", "output", "failure", "timeout", "recursive_scheduler_denied"}
)
_RUNTIME_RESUME_DECISIONS = frozenset(
    {
        "resume_same_session",
        "resume_with_system_note",
        "retry_from_checkpoint",
        "cancel_and_project_failure",
        "block_for_operator",
        "ignore_completed",
    }
)
_PUBLIC_TEXT_LIMIT = 240
_PUBLIC_DETAIL_LIMIT = 400


class NormalizedProjectionContract(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    schema_version: Literal["normalizedProjectionContract.v1"] = Field(
        default="normalizedProjectionContract.v1",
        alias="schemaVersion",
    )
    network_enabled: Literal[False] = Field(default=False, alias="networkEnabled")
    model_calls_enabled: Literal[False] = Field(default=False, alias="modelCallsEnabled")
    production_writes_enabled: Literal[False] = Field(
        default=False,
        alias="productionWritesEnabled",
    )
    transcript_writes_enabled: Literal[False] = Field(
        default=False,
        alias="transcriptWritesEnabled",
    )
    sse_writes_enabled: Literal[False] = Field(default=False, alias="sseWritesEnabled")
    memory_writes_enabled: Literal[False] = Field(
        default=False,
        alias="memoryWritesEnabled",
    )


class NormalizedEvent(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        populate_by_name=True,
        extra="forbid",
        validate_default=True,
    )

    type: NormalizedEventType
    event_id: str = Field(alias="eventId")
    ts: int | float
    turn_id: str = Field(alias="turnId")
    source: NormalizedEventSource
    call_id: str | None = Field(default=None, alias="callId")
    tool_name: str | None = Field(default=None, alias="toolName")
    payload: dict[str, object] = Field(default_factory=dict)
    metadata: dict[str, object] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _sanitize_public_surface(self) -> "NormalizedEvent":
        object.__setattr__(self, "event_id", _safe_public_ref(self.event_id, prefix="event"))
        object.__setattr__(self, "turn_id", _safe_public_ref(self.turn_id, prefix="turn"))
        if self.call_id is not None:
            object.__setattr__(self, "call_id", _safe_public_ref(self.call_id, prefix="call"))
        if self.tool_name is not None:
            object.__setattr__(self, "tool_name", _public_text(self.tool_name))
        safe_metadata = _sanitize_metadata(self.metadata)
        object.__setattr__(self, "metadata", safe_metadata)
        object.__setattr__(
            self,
            "payload",
            _sanitize_payload(self.type, self.payload, safe_metadata),
        )
        return self

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: object,
    ) -> Self:
        _ = _fields_set
        return cls.model_validate(values)

    def model_copy(
        self,
        *,
        update: Mapping[str, object] | None = None,
        deep: bool = False,
    ) -> Self:
        data = self.model_dump(by_alias=False, mode="python", warnings=False)
        if update:
            alias_to_name = {
                field.alias: name
                for name, field in type(self).model_fields.items()
                if field.alias is not None
            }
            data.update({alias_to_name.get(key, key): value for key, value in update.items()})
        return type(self).model_validate(data)

    @classmethod
    def from_control_event(cls, event: object) -> "NormalizedEvent":
        event_type = getattr(event, "type", "")
        normalized_type: NormalizedEventType
        if event_type == "control_request_created":
            normalized_type = "control.requested"
        elif event_type in {
            "control_request_resolved",
            "control_request_cancelled",
            "control_request_timed_out",
            "permission_decision",
        }:
            normalized_type = "control.resumed"
        else:
            normalized_type = "control.resumed"
        turn_id = getattr(event, "turn_id", None) or "turn"
        request_id = (
            getattr(event, "request_id", None)
            or getattr(getattr(event, "request", None), "request_id", None)
            or getattr(event, "event_id", "control")
        )
        metadata: dict[str, object] = {
            "controlRefs": [_safe_public_ref(str(request_id), prefix="control")],
            "controlEventRefs": [
                _safe_public_ref(
                    str(getattr(event, "event_id", request_id)),
                    prefix="control-event",
                )
            ],
        }
        return cls(
            type=normalized_type,
            eventId=str(getattr(event, "event_id", request_id)),
            ts=getattr(event, "ts", 0),
            turnId=str(turn_id),
            source="control",
            metadata=metadata,
            payload={
                "seq": getattr(event, "seq", 0),
                "eventType": "control_requested"
                if normalized_type == "control.requested"
                else "control_resumed",
            },
        )


def runtime_heartbeat_status_event(
    *,
    event_id: str,
    turn_id: str,
    ts: int | float,
    heartbeat: HeartbeatReceipt | Mapping[str, object],
) -> NormalizedEvent:
    projection = _record_public_projection(heartbeat, HeartbeatReceipt)
    payload = {
        "status": "heartbeat_recorded",
        "runDigest": _digest_text(str(projection["runId"])),
        "heartbeatDigest": projection["digest"],
        "leaseDigest": projection["leaseDigest"],
        "sequence": projection["sequence"],
        "emittedAt": projection["emittedAt"],
        "lastActivityAt": projection["lastActivityAt"],
        "lastActivityReceiptDigest": projection["lastActivityReceiptDigest"],
        "phaseDigest": projection["phaseDigest"],
        "activeToolDigest": projection.get("activeToolDigest"),
        "activeChildDigest": projection.get("activeChildDigest"),
        "pendingApprovalDigests": projection.get("pendingApprovalDigests", []),
        **_runtime_status_false_markers(),
    }
    return _runtime_status_event(
        event_type="runtime.heartbeat.status",
        event_id=event_id,
        turn_id=turn_id,
        ts=ts,
        payload=payload,
    )


def runtime_stale_status_event(
    *,
    event_id: str,
    turn_id: str,
    ts: int | float,
    stale_verdict: StaleRunVerdict | Mapping[str, object],
) -> NormalizedEvent:
    projection = _record_public_projection(stale_verdict, StaleRunVerdict)
    payload = {
        "status": projection["verdict"],
        "runDigest": _digest_text(str(projection["runId"])),
        "checkedAt": projection["checkedAt"],
        "reasonCodeDigests": projection.get("reasonCodeDigests", []),
        "heartbeatDigest": projection.get("heartbeatDigest"),
        "activityDigest": projection.get("activityDigest"),
        "leaseDigest": projection.get("leaseDigest"),
        **_runtime_status_false_markers(),
    }
    return _runtime_status_event(
        event_type="runtime.stale_run.status",
        event_id=event_id,
        turn_id=turn_id,
        ts=ts,
        payload=payload,
    )


def runtime_resume_status_event(
    *,
    event_id: str,
    turn_id: str,
    ts: int | float,
    resume_decision: ResumeDecision | Mapping[str, object],
) -> NormalizedEvent:
    projection = _record_public_projection(resume_decision, ResumeDecision)
    payload = {
        "decision": projection["decision"],
        "runDigest": _digest_text(str(projection["runId"])),
        "decidedAt": projection["decidedAt"],
        "reasonCodeDigests": projection.get("reasonCodeDigests", []),
        "checkpointDigest": projection.get("checkpointDigest"),
        "verdictDigest": projection.get("verdictDigest"),
        **_runtime_status_false_markers(),
    }
    return _runtime_status_event(
        event_type="runtime.resume.status",
        event_id=event_id,
        turn_id=turn_id,
        ts=ts,
        payload=payload,
    )


def runtime_watchdog_status_event(
    *,
    event_id: str,
    turn_id: str,
    ts: int | float,
    watchdog_decision: NoAgentWatchdogDecision | Mapping[str, object],
) -> NormalizedEvent:
    projection = _record_public_projection(watchdog_decision, NoAgentWatchdogDecision)
    reason_codes = projection.get("reasonCodes", [])
    payload = {
        "status": projection["status"],
        "alertKind": projection["alertKind"],
        "alertRequired": projection["alertRequired"],
        "watchdogDigest": _digest_text(str(projection["watchdogId"])),
        "tickDigest": _digest_text(str(projection["tickId"])),
        "jobDigest": _digest_text(str(projection["jobRef"])),
        "stdoutDigest": projection.get("stdoutDigest"),
        "exitCode": projection["exitCode"],
        "timedOut": projection["timedOut"],
        "recursiveSchedulerDenied": projection["recursiveSchedulerDenied"],
        "durationMs": projection["durationMs"],
        "reasonCodeDigests": [
            _digest_text(str(reason))
            for reason in reason_codes
            if isinstance(reason, str)
        ],
        **_runtime_status_false_markers(),
    }
    return _runtime_status_event(
        event_type="runtime.watchdog.status",
        event_id=event_id,
        turn_id=turn_id,
        ts=ts,
        payload=payload,
    )


def normalized_events_to_transcript(
    events: Iterable[NormalizedEvent | Mapping[str, object]],
) -> list[TranscriptEntry]:
    entries: list[TranscriptEntry] = []
    for raw_event in events:
        event = NormalizedEvent.model_validate(raw_event)
        if event.type == "turn.started":
            entries.append(
                TurnStartedEntry(
                    ts=event.ts,
                    turnId=event.turn_id,
                    declaredRoute=_declared_route(event.metadata),
                )
            )
            continue
        if event.type == "model.message.completed":
            text = event.payload.get("textPreview")
            if isinstance(text, str):
                entries.append(
                    AssistantTextEntry(ts=event.ts, turnId=event.turn_id, text=text)
                )
            continue
        if event.type == "tool.call.started" and event.call_id:
            entries.append(
                ToolCallEntry(
                    ts=event.ts,
                    turnId=event.turn_id,
                    toolUseId=event.call_id,
                    name=event.tool_name or "unknown_tool",
                    input=_safe_tool_input(event),
                )
            )
            continue
        if event.type in {
            "tool.call.needs_approval",
            "tool.call.completed",
            "tool.call.denied",
            "tool.call.failed",
        } and event.call_id:
            entries.append(_tool_result_entry_from_normalized(event))
            continue
        if event.type in {"control.requested", "control.resumed"}:
            entries.append(
                ControlEventTranscriptEntry(
                    ts=event.ts,
                    turnId=event.turn_id,
                    seq=_int_payload(event.payload.get("seq")),
                    eventId=event.event_id,
                    eventType=(
                        "control_requested"
                        if event.type == "control.requested"
                        else "control_resumed"
                    ),
                )
            )
            continue
        if event.type == "turn.completed":
            usage = event.payload.get("usage")
            usage_map = usage if isinstance(usage, Mapping) else {}
            entries.append(
                TurnCommittedEntry(
                    ts=event.ts,
                    turnId=event.turn_id,
                    inputTokens=_non_negative_int(usage_map.get("inputTokens")),
                    outputTokens=_non_negative_int(usage_map.get("outputTokens")),
                )
            )
            continue
        if event.type == "turn.failed":
            reason = event.payload.get("reasonPreview")
            entries.append(
                TurnAbortedEntry(
                    ts=event.ts,
                    turnId=event.turn_id,
                    reason=str(reason or "turn_failed"),
                )
            )
    return entries


def transcript_entries_to_agent_events(
    entries: Iterable[TranscriptEntry | Mapping[str, object]],
) -> list[dict[str, object]]:
    agent_events: list[dict[str, object]] = []
    for raw_entry in entries:
        entry = raw_entry
        kind = getattr(entry, "kind", None)
        if kind == "turn_started":
            agent_events.append(
                {
                    "type": "turn_start",
                    "turnId": _public_entry_ref(getattr(entry, "turn_id"), prefix="turn"),
                    "declaredRoute": _public_text(getattr(entry, "declared_route")),
                }
            )
        elif kind == "assistant_text":
            agent_events.append(
                {"type": "text_delta", "delta": _public_text(entry.text)}
            )
        elif kind == "tool_call":
            agent_events.append(
                {
                    "type": "tool_start",
                    "id": _public_entry_ref(entry.tool_use_id, prefix="call"),
                    "name": _public_text(entry.name),
                    "input_preview": _public_preview(entry.input),
                }
            )
        elif kind == "tool_result":
            agent_events.append(
                {
                    "type": "tool_end",
                    "id": _public_entry_ref(entry.tool_use_id, prefix="call"),
                    "status": _public_tool_status(entry.status),
                    "output_preview": _public_tool_output_preview(entry.output),
                    "durationMs": 0,
                }
            )
        elif kind == "turn_committed":
            agent_events.append(
                {
                    "type": "turn_end",
                    "turnId": _public_entry_ref(entry.turn_id, prefix="turn"),
                    "status": "committed",
                }
            )
        elif kind == "turn_aborted":
            agent_events.append(
                {
                    "type": "turn_end",
                    "turnId": _public_entry_ref(entry.turn_id, prefix="turn"),
                    "status": "aborted",
                    "reason": _public_text(entry.reason),
                }
            )
        elif kind == "control_event":
            agent_events.append(
                {
                    "type": "control_event",
                    "eventId": _public_entry_ref(entry.event_id, prefix="event"),
                    "turnId": _public_entry_ref(entry.turn_id, prefix="turn"),
                    "seq": entry.seq,
                    "eventType": _public_text(entry.event_type),
                }
            )
    return agent_events


def normalized_events_to_agent_events(
    events: Iterable[NormalizedEvent | Mapping[str, object]],
) -> list[dict[str, object]]:
    agent_events: list[dict[str, object]] = []
    for raw_event in events:
        event = NormalizedEvent.model_validate(raw_event)
        projected = _normalized_event_to_agent_event(event)
        if projected is None:
            continue
        agent_events.extend(projected)
    return agent_events


def _normalized_event_to_agent_event(
    event: NormalizedEvent,
) -> list[dict[str, object]] | None:
    if event.type == "turn.started":
        return [
            {
                "type": "turn_start",
                "eventId": event.event_id,
                "turnId": event.turn_id,
                "declaredRoute": _declared_route(event.metadata),
            }
        ]
    if event.type == "runtime.phase":
        return [_runtime_phase_agent_event(event)]
    if event.type == "runtime.heartbeat":
        return [_runtime_heartbeat_agent_event(event)]
    if event.type in _RUNTIME_STATUS_EVENT_TYPES:
        return [_runtime_status_agent_event(event)]
    if event.type == "runtime.trace":
        return [_runtime_trace_agent_event(event)]
    if event.type == "model.message.delta":
        text = event.payload.get("textPreview")
        if isinstance(text, str):
            return [
                {
                    "type": "text_delta",
                    "eventId": event.event_id,
                    "delta": _public_text(text),
                }
            ]
        return None
    if event.type == "tool.call.started" and event.call_id:
        public_event = {
            "type": "tool_start",
            "eventId": event.event_id,
            "id": event.call_id,
            "name": event.tool_name or "unknown_tool",
            "input_preview": _json_preview({"digest": event.payload.get("inputDigest")}),
        }
        input_digest = _public_digest_ref(
            event.payload.get("inputDigest"),
            event.metadata.get("inputDigest"),
        )
        if input_digest is not None:
            public_event["inputDigest"] = input_digest
        return [public_event]
    if event.type == "tool.call.progress" and event.call_id:
        receipt = _public_receipt_ref(
            event.payload.get("receiptRef"),
            event.metadata.get("receiptRef"),
        )
        if receipt is None:
            return [
                _blocked_projection_event(
                    event,
                    detail="tool.call.progress omitted: missing public tool receipt",
                )
            ]
        public_event = _tool_progress_agent_event(event)
        public_event["receiptRef"] = receipt
        return [public_event]
    if event.type in {
        "tool.call.completed",
        "tool.call.denied",
        "tool.call.failed",
        "tool.call.needs_approval",
    } and event.call_id:
        return [_tool_terminal_agent_event(event)]
    if event.type == "source.inspected":
        source_event = _source_inspected_agent_event(event)
        if source_event is None:
            return [
                _blocked_projection_event(
                    event,
                    detail="source.inspected omitted: missing public evidence receipt",
                )
            ]
        return [source_event]
    if event.type == "rule.check":
        verdict = event.payload.get("verdict")
        if verdict != "pending" and _rule_evidence_ref(event) is None:
            return [
                _blocked_projection_event(
                    event,
                    detail="rule.check omitted: missing public evidence receipt",
                )
            ]
        return [_rule_check_agent_event(event)]
    if event.type in {
        "child.started",
        "child.progress",
        "child.completed",
        "child.cancelled",
        "child.failed",
    }:
        child_event = _child_agent_event(event)
        if child_event is None:
            return [
                _blocked_projection_event(
                    event,
                    detail=f"{event.type} omitted: missing public child receipt",
                )
            ]
        return [child_event]
    if event.type == "turn.completed":
        receipt_ref = _public_receipt_ref(
            event.payload.get("receiptRef"),
            event.metadata.get("receiptRef"),
        )
        if receipt_ref is None:
            return [
                _blocked_projection_event(
                    event,
                    detail="turn.completed omitted: missing public runtime receipt",
                )
            ]
        public_event = {
            "type": "turn_end",
            "eventId": event.event_id,
            "turnId": event.turn_id,
            "status": "committed",
            "usage": event.payload.get("usage", {}),
            "receiptRef": receipt_ref,
        }
        return [public_event]
    if event.type == "turn.failed":
        reason = event.payload.get("reasonPreview")
        return [
            {
                "type": "turn_end",
                "eventId": event.event_id,
                "turnId": event.turn_id,
                "status": "aborted",
                "reason": reason if isinstance(reason, str) else "turn_failed",
            }
        ]
    return None


def _runtime_phase_agent_event(event: NormalizedEvent) -> dict[str, object]:
    phase = event.payload.get("phase")
    public_event: dict[str, object] = {
        "type": "turn_phase",
        "eventId": event.event_id,
        "turnId": event.turn_id,
        "phase": phase if phase in _TURN_PHASES else "pending",
    }
    for key in ("status", "label", "message"):
        _put_agent_text(public_event, key, event.payload.get(key))
    _put_agent_text(
        public_event,
        "detail",
        event.payload.get("detail"),
        limit=_PUBLIC_DETAIL_LIMIT,
    )
    for key in ("sequence", "createdAt"):
        _put_agent_number(public_event, key, event.payload.get(key))
    return public_event


def _runtime_heartbeat_agent_event(event: NormalizedEvent) -> dict[str, object]:
    public_event: dict[str, object] = {
        "type": "heartbeat",
        "eventId": event.event_id,
        "turnId": event.turn_id,
    }
    for key in ("iter", "elapsedMs", "lastEventAt"):
        _put_agent_number(public_event, key, event.payload.get(key))
    return public_event


def _runtime_status_agent_event(event: NormalizedEvent) -> dict[str, object]:
    public_event: dict[str, object] = {
        "type": _RUNTIME_STATUS_PUBLIC_TYPES[event.type],
        "eventId": event.event_id,
        "turnId": event.turn_id,
    }
    public_event.update(event.payload)
    return public_event


def _runtime_trace_agent_event(event: NormalizedEvent) -> dict[str, object]:
    phase = event.payload.get("phase")
    severity = event.payload.get("severity")
    public_event: dict[str, object] = {
        "type": "runtime_trace",
        "eventId": event.event_id,
        "turnId": event.turn_id,
        "phase": phase if phase in _RUNTIME_TRACE_PHASES else "verifier_blocked",
        "severity": severity if severity in _RUNTIME_TRACE_SEVERITIES else "info",
    }
    for key in ("title", "requiredAction"):
        _put_agent_text(public_event, key, event.payload.get(key))
    _put_agent_text(
        public_event,
        "detail",
        event.payload.get("detail"),
        limit=_PUBLIC_DETAIL_LIMIT,
    )
    for key in ("reasonCode", "ruleId"):
        code = _public_reason_code(event.payload.get(key), event.metadata.get(key))
        if code is not None:
            public_event[key] = code
    for key in ("attempt", "maxAttempts"):
        _put_agent_number(public_event, key, event.payload.get(key))
    retryable = event.payload.get("retryable")
    if isinstance(retryable, bool):
        public_event["retryable"] = retryable
    return public_event


def _tool_progress_agent_event(event: NormalizedEvent) -> dict[str, object]:
    public_event: dict[str, object] = {
        "type": "tool_progress",
        "eventId": event.event_id,
        "id": event.call_id or "call:unknown",
    }
    for key in ("label", "status", "message"):
        _put_agent_text(public_event, key, event.payload.get(key))
    _put_agent_text(
        public_event,
        "detail",
        event.payload.get("detail"),
        limit=_PUBLIC_DETAIL_LIMIT,
    )
    for key in ("progress", "createdAt"):
        _put_agent_number(public_event, key, event.payload.get(key))
    return public_event


def _tool_terminal_agent_event(event: NormalizedEvent) -> dict[str, object]:
    public_event: dict[str, object] = {
        "type": "tool_end",
        "eventId": event.event_id,
        "id": event.call_id or "call:unknown",
        "status": _tool_status_from_normalized(event),
    }
    output_preview = event.payload.get("outputPreview")
    if isinstance(output_preview, str) and output_preview.strip():
        public_event["output_preview"] = _bounded_public_text(
            output_preview,
            limit=_PUBLIC_DETAIL_LIMIT,
        )
    else:
        output = _safe_tool_output(event)
        if output:
            public_event["output_preview"] = _bounded_public_text(
                output,
                limit=_PUBLIC_DETAIL_LIMIT,
            )
    output_digest = _public_digest_ref(
        event.payload.get("outputDigest"),
        event.payload.get("errorDigest"),
        event.payload.get("reasonDigest"),
        event.metadata.get("outputDigest"),
        event.metadata.get("errorDigest"),
        event.metadata.get("reasonDigest"),
    )
    if output_digest is not None:
        public_event["outputDigest"] = output_digest
    receipt_ref = _public_receipt_ref(
        event.payload.get("receiptRef"),
        event.metadata.get("receiptRef"),
        event.metadata.get("toolReceiptRef"),
    )
    if receipt_ref is not None:
        public_event["receiptRef"] = receipt_ref
    transcript_refs = _public_terminal_ref_list(
        event.metadata.get("toolResultRefs"),
        event.metadata.get("sourceRefs"),
    )
    if transcript_refs:
        public_event["transcriptRefs"] = transcript_refs
    return public_event


def _tool_status_from_normalized(event: NormalizedEvent) -> str:
    if event.type == "tool.call.completed":
        return "ok"
    if event.type == "tool.call.needs_approval":
        return "needs_approval"
    if event.type == "tool.call.denied":
        return "blocked"
    return "error"


def _source_inspected_agent_event(event: NormalizedEvent) -> dict[str, object] | None:
    content_hash = _public_evidence_ref(
        event.payload.get("contentHash"),
        event.payload.get("evidenceRef"),
        event.metadata.get("contentHash"),
        event.metadata.get("evidenceRef"),
        event.metadata.get("sourceRef"),
    )
    if content_hash is None:
        return None
    source_id = _string_payload(event.payload.get("sourceId"))
    uri = _string_payload(event.payload.get("uri"))
    if source_id is None or uri is None:
        return None
    kind = event.payload.get("kind")
    trust_tier = event.payload.get("trustTier")
    source: dict[str, object] = {
        "sourceId": _bounded_public_text(source_id, limit=120),
        "kind": kind if kind in _SOURCE_KINDS else "web_fetch",
        "uri": _bounded_public_text(uri, limit=4_000),
        "trustTier": trust_tier if trust_tier in _TRUST_TIERS else "unknown",
        "contentHash": content_hash,
        "turnId": event.turn_id,
    }
    if event.tool_name is not None:
        source["toolName"] = event.tool_name
    if event.call_id is not None:
        source["toolUseId"] = event.call_id
    for key in ("title", "contentType"):
        _put_agent_text(source, key, event.payload.get(key))
    _put_agent_number(source, "inspectedAt", event.payload.get("inspectedAt"))
    if "inspectedAt" not in source and math.isfinite(float(event.ts)):
        source["inspectedAt"] = event.ts
    snippets: list[str] = []
    snippet = event.payload.get("snippet")
    if isinstance(snippet, str):
        snippets.append(_bounded_public_text(snippet, limit=_PUBLIC_DETAIL_LIMIT))
    raw_snippets = event.payload.get("snippets")
    if isinstance(raw_snippets, list):
        for item in raw_snippets[:5]:
            if isinstance(item, str):
                snippets.append(_bounded_public_text(item, limit=_PUBLIC_DETAIL_LIMIT))
    if snippets:
        source["snippets"] = snippets[:5]
    return {
        "type": "source_inspected",
        "eventId": event.event_id,
        "source": source,
    }


def _rule_check_agent_event(event: NormalizedEvent) -> dict[str, object]:
    rule_id = event.payload.get("ruleId")
    verdict = event.payload.get("verdict")
    public_event: dict[str, object] = {
        "type": "rule_check",
        "eventId": event.event_id,
        "turnId": event.turn_id,
        "ruleId": _bounded_public_text(
            rule_id if isinstance(rule_id, str) and rule_id else "rule",
            limit=120,
        ),
        "verdict": verdict if verdict in _RULE_VERDICTS else "pending",
    }
    _put_agent_number(public_event, "checkedAt", event.payload.get("checkedAt"))
    if "checkedAt" not in public_event and math.isfinite(float(event.ts)):
        public_event["checkedAt"] = event.ts
    _put_agent_text(
        public_event,
        "detail",
        event.payload.get("detail"),
        limit=_PUBLIC_DETAIL_LIMIT,
    )
    evidence_ref = _rule_evidence_ref(event)
    if evidence_ref is not None:
        public_event["evidenceRef"] = evidence_ref
    if rule_check_event_has_authority(event.metadata):
        authorize_rule_check_event(public_event)
    return public_event


def _rule_evidence_ref(event: NormalizedEvent) -> str | None:
    return _public_receipt_ref(
        event.payload.get("evidenceRef"),
        event.metadata.get("evidenceRef"),
    ) or _public_digest_ref(
        event.payload.get("evidenceRef"),
        event.metadata.get("evidenceRef"),
    )


def _child_agent_event(event: NormalizedEvent) -> dict[str, object] | None:
    child_receipt = _public_receipt_ref(
        event.payload.get("childReceiptRef"),
        event.metadata.get("childReceiptRef"),
        event.metadata.get("receiptRef"),
    )
    if child_receipt is None:
        return None
    task_id = _string_payload(event.payload.get("taskId"))
    if task_id is None:
        return None
    event_type_by_normalized = {
        "child.started": "child_started",
        "child.progress": "child_progress",
        "child.completed": "child_completed",
        "child.cancelled": "child_cancelled",
        "child.failed": "child_failed",
    }
    public_event: dict[str, object] = {
        "type": event_type_by_normalized[event.type],
        "eventId": event.event_id,
        "taskId": _bounded_public_text(task_id, limit=120),
        "childReceiptRef": child_receipt,
    }
    parent_turn_id = event.payload.get("parentTurnId")
    if isinstance(parent_turn_id, str):
        public_event["parentTurnId"] = _bounded_public_text(parent_turn_id, limit=120)
    elif event.type == "child.started":
        public_event["parentTurnId"] = event.turn_id
    for key in ("detail", "reason"):
        _put_agent_text(public_event, key, event.payload.get(key))
    _put_agent_text(
        public_event,
        "errorMessage",
        event.payload.get("errorMessage"),
        limit=_PUBLIC_DETAIL_LIMIT,
    )
    return public_event


def _blocked_projection_event(event: NormalizedEvent, *, detail: str) -> dict[str, object]:
    return {
        "type": "runtime_trace",
        "eventId": f"{event.event_id}:blocked",
        "turnId": event.turn_id,
        "phase": "verifier_blocked",
        "severity": "warning",
        "title": "Public event omitted",
        "detail": detail,
        "reasonCode": "public_projection_missing_receipt",
        "requiredAction": "retain_typescript_fallback",
    }


def _put_agent_text(
    event: dict[str, object],
    key: str,
    value: object,
    *,
    limit: int = _PUBLIC_TEXT_LIMIT,
) -> None:
    if isinstance(value, str) and value.strip():
        event[key] = _bounded_public_text(value, limit=limit)


def _put_agent_number(event: dict[str, object], key: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return
    if math.isfinite(float(value)):
        event[key] = value


def _string_payload(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _bounded_public_text(value: str, *, limit: int) -> str:
    public = _public_text(value)
    if len(public) > limit:
        return f"{public[:limit - 3]}..."
    return public


def _first_public_ref(*values: object) -> str | None:
    for value in values:
        candidates = value if isinstance(value, list | tuple | set) else (value,)
        for candidate in candidates:
            if not isinstance(candidate, str):
                continue
            public_ref = candidate.strip()
            if _PUBLIC_EVIDENCE_REF_RE.fullmatch(
                public_ref
            ) and _is_safe_public_authority_ref(public_ref):
                return public_ref
    return None


def _public_reason_code(*values: object) -> str | None:
    ref = _first_public_ref(*values)
    if ref is not None:
        return ref
    for value in values:
        candidates = value if isinstance(value, list | tuple | set) else (value,)
        for candidate in candidates:
            if not isinstance(candidate, str):
                continue
            public_code = candidate.strip()
            if (
                _PUBLIC_REASON_CODE_RE.fullmatch(public_code)
                and not _is_sensitive_key_shape(_normalize_key(public_code))
                and not _has_private_text_marker(public_code)
            ):
                return _bounded_public_text(public_code, limit=120)
    return None


def _public_evidence_ref(*values: object) -> str | None:
    for value in values:
        candidates = value if isinstance(value, list | tuple | set) else (value,)
        for candidate in candidates:
            if not isinstance(candidate, str):
                continue
            public_ref = candidate.strip()
            if (
                _PUBLIC_EVIDENCE_REF_RE.fullmatch(public_ref)
                and _is_safe_public_authority_ref(public_ref)
            ):
                return public_ref
    return None


def _public_digest_ref(*values: object) -> str | None:
    for value in values:
        candidates = value if isinstance(value, list | tuple | set) else (value,)
        for candidate in candidates:
            if not isinstance(candidate, str):
                continue
            public_ref = candidate.strip()
            if _DIGEST_REF_RE.fullmatch(public_ref) and _is_safe_public_ref(public_ref):
                return public_ref
    return None


def _public_receipt_ref(*values: object) -> str | None:
    for value in values:
        candidates = value if isinstance(value, list | tuple | set) else (value,)
        for candidate in candidates:
            if not isinstance(candidate, str):
                continue
            public_ref = candidate.strip()
            if (
                _RECEIPT_REF_RE.fullmatch(public_ref)
                and _is_safe_public_ref(public_ref)
            ):
                return public_ref
    return None


def _public_terminal_ref(*values: object) -> str | None:
    return _public_receipt_ref(*values) or _public_digest_ref(*values) or _public_evidence_ref(
        *values
    )


def _public_terminal_ref_list(*values: object) -> list[str]:
    refs: list[str] = []
    for value in values:
        candidates = value if isinstance(value, list | tuple | set) else (value,)
        for candidate in candidates:
            public_ref = _public_terminal_ref(candidate)
            if public_ref is not None and public_ref not in refs:
                refs.append(public_ref)
    return refs


def _public_ref_list(*values: object) -> list[str]:
    refs: list[str] = []
    for value in values:
        candidates = value if isinstance(value, list | tuple | set) else (value,)
        for candidate in candidates:
            if not isinstance(candidate, str):
                continue
            public_ref = _first_public_ref(candidate)
            if public_ref is not None and public_ref not in refs:
                refs.append(public_ref)
    return refs


def public_terminal_refs(value: object) -> tuple[str, ...]:
    return tuple(_public_terminal_ref_list(value))


def metadata_digest(value: object) -> str:
    return _digest_json(value)


def public_refs(value: object, *, prefix: str = "ref") -> tuple[str, ...]:
    if value is None:
        return ()
    candidates = value if isinstance(value, list | tuple | set) else (value,)
    refs: list[str] = []
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            refs.append(_safe_public_ref(candidate, prefix=prefix))
    return tuple(dict.fromkeys(refs))


def _safe_tool_input(event: NormalizedEvent) -> dict[str, object]:
    result: dict[str, object] = {}
    preview = event.payload.get("inputPreview")
    if isinstance(preview, str):
        result["inputPreview"] = preview
    digest = event.metadata.get("inputDigest") or event.payload.get("inputDigest")
    if isinstance(digest, str):
        result["inputDigest"] = digest
    return result


def _tool_result_entry_from_normalized(event: NormalizedEvent) -> ToolResultEntry:
    status_by_type = {
        "tool.call.needs_approval": "needs_approval",
        "tool.call.completed": "ok",
        "tool.call.denied": "blocked",
        "tool.call.failed": "error",
    }
    is_error = event.type in {"tool.call.denied", "tool.call.failed"}
    return ToolResultEntry(
        ts=event.ts,
        turnId=event.turn_id,
        toolUseId=event.call_id or "unknown-call",
        status=status_by_type[event.type],
        output=_safe_tool_output(event),
        isError=is_error,
        metadata=event.metadata or None,
    )


def _safe_tool_output(event: NormalizedEvent) -> str | None:
    refs = event.metadata.get("toolResultRefs")
    if refs:
        return _json_preview({"toolResultRefs": refs})
    digest = (
        event.metadata.get("outputDigest")
        or event.metadata.get("errorDigest")
        or event.payload.get("outputDigest")
        or event.payload.get("errorDigest")
    )
    if digest:
        return _json_preview({"digest": digest})
    output = event.payload.get("outputPreview") or event.payload.get("reasonPreview")
    if isinstance(output, str):
        return output
    return None


def _json_preview(value: object) -> str:
    return json.dumps(
        _json_safe_value(value),
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
    )


def _json_safe_value(value: object) -> object:
    if isinstance(value, bool):
        return value
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, Mapping):
        return {str(key): _json_safe_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe_value(item) for item in value]
    return value


def _public_preview(value: object) -> str:
    return _public_structured_text(
        json.dumps(
            _public_json_safe_value(value),
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        )
    )


def _public_json_safe_value(value: object) -> object:
    if isinstance(value, bool):
        return value
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for key, item in value.items():
            key_text = str(key)
            normalized_key = _normalize_key(key_text)
            if _is_private_metadata_key(normalized_key):
                result[f"{_public_key_text(key_text)}Digest"] = _digest_json(item)
                continue
            result[key_text] = _public_json_safe_value(item)
        return result
    if isinstance(value, list | tuple):
        return [_public_json_safe_value(item) for item in value]
    if isinstance(value, str):
        parsed = _parse_json_container(value)
        if parsed is not None:
            return _public_json_safe_value(parsed)
        if _mentions_private_metadata_key(value):
            return {"digest": _digest_json(value)}
        return _public_text(value)
    return value


def _public_tool_output_preview(value: str | None) -> str:
    if not value:
        return ""
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        if _mentions_private_metadata_key(value):
            return _public_preview({"digest": _digest_json(value)})
        return _public_text(value)
    return _public_preview(parsed)


def _declared_route(metadata: Mapping[str, object]) -> str:
    declared_route = metadata.get("declaredRoute")
    if declared_route in {"direct", "subagent", "pipeline"}:
        return str(declared_route)
    return "direct"


def _int_payload(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int | float) and math.isfinite(float(value)):
        return int(value)
    return 0


def _non_negative_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int | float) and math.isfinite(float(value)):
        return max(0, int(value))
    return 0


def _public_tool_status(value: str) -> str:
    if value == "ok":
        return "ok"
    return "error"


def _digest_json(value: object) -> str:
    payload = json.dumps(
        _json_safe_value(value),
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )
    return _DIGEST_PREFIX + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _digest_text(value: str) -> str:
    return _DIGEST_PREFIX + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _record_public_projection(
    value: object,
    record_type: type[HeartbeatReceipt]
    | type[StaleRunVerdict]
    | type[ResumeDecision]
    | type[NoAgentWatchdogDecision],
) -> dict[str, object]:
    record = value if isinstance(value, record_type) else record_type.model_validate(value)
    return record.public_projection()


def _runtime_status_event(
    *,
    event_type: NormalizedEventType,
    event_id: str,
    turn_id: str,
    ts: int | float,
    payload: Mapping[str, object],
) -> NormalizedEvent:
    return NormalizedEvent(
        type=event_type,
        eventId=event_id,
        ts=ts,
        turnId=turn_id,
        source="runtime",
        payload=dict(payload),
        metadata={"projectionOnly": True},
    )


def _runtime_status_false_markers() -> dict[str, bool]:
    return {
        "publicSafe": True,
        **{key: False for key in _RUNTIME_STATUS_FALSE_FIELDS},
    }


def _sanitize_payload(
    event_type: NormalizedEventType,
    payload: Mapping[str, object],
    metadata: Mapping[str, object],
) -> dict[str, object]:
    if event_type == "runtime.phase":
        result: dict[str, object] = {}
        phase = payload.get("phase")
        result["phase"] = phase if phase in _TURN_PHASES else "pending"
        for key in ("status", "label", "message"):
            _copy_public_text(result, key, payload.get(key))
        _copy_public_text(
            result,
            "detail",
            payload.get("detail"),
            limit=_PUBLIC_DETAIL_LIMIT,
        )
        for key in ("sequence", "createdAt"):
            _copy_finite_number(result, key, payload.get(key))
        return result
    if event_type == "runtime.heartbeat":
        result: dict[str, object] = {}
        for key in ("iter", "elapsedMs", "lastEventAt"):
            _copy_finite_number(result, key, payload.get(key))
        return result
    if event_type in _RUNTIME_STATUS_EVENT_TYPES:
        return _sanitize_runtime_status_payload(event_type, payload)
    if event_type == "runtime.trace":
        result = {}
        phase = payload.get("phase")
        severity = payload.get("severity")
        result["phase"] = phase if phase in _RUNTIME_TRACE_PHASES else "verifier_blocked"
        result["severity"] = severity if severity in _RUNTIME_TRACE_SEVERITIES else "info"
        for key in ("title", "requiredAction"):
            _copy_public_text(result, key, payload.get(key))
        _copy_public_text(
            result,
            "detail",
            payload.get("detail"),
            limit=_PUBLIC_DETAIL_LIMIT,
        )
        for key in ("reasonCode", "ruleId"):
            code = _public_reason_code(payload.get(key), metadata.get(key))
            if code is not None:
                result[key] = code
        for key in ("attempt", "maxAttempts"):
            _copy_finite_number(result, key, payload.get(key))
        retryable = payload.get("retryable")
        if isinstance(retryable, bool):
            result["retryable"] = retryable
        return result
    if event_type in {"model.message.delta", "model.message.completed"}:
        text = payload.get("textPreview", payload.get("text", ""))
        return _preview_payload("textPreview", text)
    if event_type == "tool.call.started":
        value = payload.get("inputPreview", payload.get("input", payload.get("args", {})))
        digest = metadata.get("inputDigest")
        return {"inputDigest": digest if isinstance(digest, str) else _digest_json(value)}
    if event_type == "tool.call.progress":
        result = {}
        for key in ("label", "status", "message"):
            _copy_public_text(result, key, payload.get(key))
        _copy_public_text(
            result,
            "detail",
            payload.get("detail"),
            limit=_PUBLIC_DETAIL_LIMIT,
        )
        for key in ("progress", "createdAt"):
            _copy_finite_number(result, key, payload.get(key))
        receipt_ref = _public_receipt_ref(payload.get("receiptRef"), metadata.get("receiptRef"))
        if receipt_ref is not None:
            result["receiptRef"] = receipt_ref
        return result
    if event_type in {"tool.call.completed", "tool.call.failed"}:
        value = payload.get(
            "outputPreview",
            payload.get("output", payload.get("result", payload.get("response", {}))),
        )
        result: dict[str, object] = {}
        output_digest = metadata.get("outputDigest") or metadata.get("errorDigest")
        if isinstance(output_digest, str):
            result["outputDigest"] = output_digest
        else:
            result["outputDigest"] = _digest_json(value)
        error_digest = metadata.get("errorDigest")
        if isinstance(error_digest, str):
            result["errorDigest"] = error_digest
        if isinstance(payload.get("outputPreview"), str):
            result["outputPreview"] = _bounded_public_text(
                str(payload["outputPreview"]),
                limit=_PUBLIC_DETAIL_LIMIT,
            )
        status = payload.get("status")
        if isinstance(status, str):
            result["status"] = _public_text(status)
        receipt_ref = _public_receipt_ref(
            payload.get("receiptRef"),
            metadata.get("receiptRef"),
            metadata.get("toolReceiptRef"),
        )
        if receipt_ref is not None:
            result["receiptRef"] = receipt_ref
        return result
    if event_type in {"tool.call.needs_approval", "tool.call.denied", "turn.failed"}:
        value = payload.get("reasonPreview", payload.get("reason", payload.get("output", "")))
        result = _preview_payload("reasonPreview", value, parse_string_containers=True)
        if event_type in {"tool.call.needs_approval", "tool.call.denied"}:
            receipt_ref = _public_receipt_ref(
                payload.get("receiptRef"),
                metadata.get("receiptRef"),
                metadata.get("toolReceiptRef"),
            )
            if receipt_ref is not None:
                result["receiptRef"] = receipt_ref
        return result
    if event_type == "turn.completed":
        usage = payload.get("usage")
        result = {"usage": _sanitize_usage(usage)}
        receipt_ref = _public_receipt_ref(payload.get("receiptRef"), metadata.get("receiptRef"))
        if receipt_ref is not None:
            result["receiptRef"] = receipt_ref
        return result
    if event_type == "source.inspected":
        result = {}
        for key in ("sourceId", "uri", "title", "contentType", "snippet"):
            limit = 4_000 if key == "uri" else _PUBLIC_DETAIL_LIMIT
            _copy_public_text(result, key, payload.get(key), limit=limit)
        kind = payload.get("kind")
        result["kind"] = kind if kind in _SOURCE_KINDS else "web_fetch"
        trust_tier = payload.get("trustTier")
        result["trustTier"] = trust_tier if trust_tier in _TRUST_TIERS else "unknown"
        for key in ("contentHash", "evidenceRef"):
            ref = _public_evidence_ref(payload.get(key), metadata.get(key))
            if ref is not None:
                result[key] = ref
        _copy_finite_number(result, "inspectedAt", payload.get("inspectedAt"))
        snippets = payload.get("snippets")
        if isinstance(snippets, list):
            result["snippets"] = [
                _bounded_public_text(item, limit=_PUBLIC_DETAIL_LIMIT)
                for item in snippets[:5]
                if isinstance(item, str) and item.strip()
            ]
        return result
    if event_type == "rule.check":
        result = {}
        _copy_public_text(result, "ruleId", payload.get("ruleId"), limit=120)
        verdict = payload.get("verdict")
        result["verdict"] = verdict if verdict in _RULE_VERDICTS else "pending"
        _copy_public_text(
            result,
            "detail",
            payload.get("detail"),
            limit=_PUBLIC_DETAIL_LIMIT,
        )
        evidence_ref = _public_evidence_ref(
            payload.get("evidenceRef"),
            metadata.get("evidenceRef"),
        )
        if evidence_ref is not None:
            result["evidenceRef"] = evidence_ref
        elif (
            payload.get("_evidenceRefPresent") is True
            or "evidenceRef" in payload
            or "evidenceRef" in metadata
        ):
            result["_evidenceRefPresent"] = True
        _copy_finite_number(result, "checkedAt", payload.get("checkedAt"))
        return result
    if event_type in {
        "child.started",
        "child.progress",
        "child.completed",
        "child.cancelled",
        "child.failed",
    }:
        result = {}
        for key in ("taskId", "parentTurnId", "detail", "reason", "errorMessage"):
            _copy_public_text(
                result,
                key,
                payload.get(key),
                limit=_PUBLIC_DETAIL_LIMIT,
            )
        child_receipt = _public_receipt_ref(
            payload.get("childReceiptRef"),
            metadata.get("childReceiptRef"),
            metadata.get("receiptRef"),
        )
        if child_receipt is not None:
            result["childReceiptRef"] = child_receipt
        return result
    if event_type in {"control.requested", "control.resumed"}:
        result: dict[str, object] = {
            "seq": _int_payload(payload.get("seq")),
        }
        event_type_value = payload.get("eventType")
        if isinstance(event_type_value, str):
            result["eventType"] = _public_text(event_type_value)
        return result
    if event_type == "turn.started":
        route = payload.get("declaredRoute")
        return {"declaredRoute": _public_text(route) if isinstance(route, str) else "direct"}
    return {}


def _sanitize_runtime_status_payload(
    event_type: NormalizedEventType,
    payload: Mapping[str, object],
) -> dict[str, object]:
    result = _runtime_status_false_markers()
    status = _runtime_status_value(event_type, payload.get("status"))
    if status is not None:
        result["status"] = status
    decision = _runtime_resume_decision(payload.get("decision"))
    if decision is not None:
        result["decision"] = decision
    alert_kind = _runtime_watchdog_alert_kind(payload.get("alertKind"))
    if alert_kind is not None:
        result["alertKind"] = alert_kind

    for key in (
        "runDigest",
        "heartbeatDigest",
        "leaseDigest",
        "lastActivityReceiptDigest",
        "phaseDigest",
        "activeToolDigest",
        "activeChildDigest",
        "activityDigest",
        "checkpointDigest",
        "verdictDigest",
        "watchdogDigest",
        "tickDigest",
        "jobDigest",
        "stdoutDigest",
    ):
        digest = _public_runtime_digest(payload.get(key))
        if digest is not None:
            result[key] = digest

    for key in ("reasonCodeDigests", "pendingApprovalDigests"):
        digests = _public_runtime_digest_list(payload.get(key))
        if digests:
            result[key] = digests

    for key in ("emittedAt", "lastActivityAt", "checkedAt", "decidedAt"):
        _copy_public_text(result, key, payload.get(key), limit=80)

    for key in (
        "sequence",
        "exitCode",
        "durationMs",
    ):
        _copy_finite_number(result, key, payload.get(key))

    for key in (
        "alertRequired",
        "timedOut",
        "recursiveSchedulerDenied",
    ):
        value = payload.get(key)
        if isinstance(value, bool):
            result[key] = value

    return result


def _runtime_status_value(
    event_type: NormalizedEventType,
    value: object,
) -> str | None:
    if not isinstance(value, str):
        return None
    allowed = _RUNTIME_STATUS_BY_TYPE.get(event_type)
    return value if allowed is not None and value in allowed else None


def _runtime_resume_decision(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return value if value in _RUNTIME_RESUME_DECISIONS else None


def _runtime_watchdog_alert_kind(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return value if value in _RUNTIME_WATCHDOG_ALERT_KINDS else None


def _public_runtime_digest(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    public_ref = value.strip()
    if (
        _DIGEST_REF_RE.fullmatch(public_ref) is not None
        or _TYPED_DIGEST_REF_RE.fullmatch(public_ref) is not None
    ) and _is_safe_public_ref(public_ref):
        return public_ref
    return None


def _public_runtime_digest_list(value: object) -> list[str]:
    if not isinstance(value, list | tuple):
        return []
    digests: list[str] = []
    for item in value[:50]:
        digest = _public_runtime_digest(item)
        if digest is not None and digest not in digests:
            digests.append(digest)
    return digests


def _sanitize_metadata(metadata: Mapping[str, object]) -> dict[str, object]:
    safe: dict[str, object] = {}
    copy_rule_check_authority(metadata, safe)
    for key, value in metadata.items():
        if is_rule_check_authority_field(key):
            continue
        key_text = _public_key_text(str(key))
        normalized = key_text.replace("-", "_").lower()
        normalized_compact = _normalize_key(normalized)
        if _is_private_metadata_key(normalized_compact) or _is_sensitive_key_shape(
            normalized_compact
        ):
            digest_key = key_text if normalized.endswith("digest") else f"{key_text}Digest"
            safe[digest_key] = _public_digest_ref(value) or _digest_json(value)
            continue
        if normalized.endswith("digest") or normalized in {
            "contentdigest",
            "inputdigest",
            "outputdigest",
            "errordigest",
            "reasondigest",
        }:
            safe[key_text] = _public_digest_ref(value) or _digest_json(value)
            continue
        if normalized.endswith("refs") or normalized.endswith("ref"):
            safe[key_text] = list(public_refs(value, prefix=normalized.removesuffix("s")))
            continue
        if isinstance(value, bool | int | float) or value is None:
            safe[key_text] = value
        elif isinstance(value, str):
            safe[key_text] = _public_text(value)
        else:
            safe[f"{key_text}Digest"] = _digest_json(value)
    return safe


def _copy_public_text(
    target: dict[str, object],
    key: str,
    value: object,
    *,
    limit: int = _PUBLIC_TEXT_LIMIT,
) -> None:
    if isinstance(value, str) and value.strip():
        target[key] = _bounded_public_text(value, limit=limit)


def _copy_finite_number(target: dict[str, object], key: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return
    if math.isfinite(float(value)):
        target[key] = value


def _preview_payload(
    key: str,
    value: object,
    *,
    parse_string_containers: bool = False,
) -> dict[str, object]:
    structured_preview = False
    if isinstance(value, str):
        parsed = _parse_json_container(value) if parse_string_containers else None
        if parsed is not None:
            preview_source = _public_preview(parsed)
            structured_preview = True
        elif parse_string_containers and _mentions_private_metadata_key(value):
            preview_source = _public_preview({"digest": _digest_json(value)})
            structured_preview = True
        else:
            preview_source = value
    else:
        preview_source = _public_preview(value)
        structured_preview = True
    return {
        key: (
            _public_structured_text(preview_source)
            if structured_preview
            else _public_text(preview_source)
        ),
        f"{key.removesuffix('Preview')}Digest": _digest_json(value),
    }


def _sanitize_usage(value: object) -> dict[str, int]:
    usage = value if isinstance(value, Mapping) else {}
    return {
        "inputTokens": _non_negative_int(usage.get("inputTokens")),
        "outputTokens": _non_negative_int(usage.get("outputTokens")),
    }


def _safe_public_ref(value: str, *, prefix: str) -> str:
    if _is_safe_public_ref(value):
        return value
    return f"{prefix}:{hashlib.sha1(value.encode('utf-8')).hexdigest()[:16]}"


def _is_safe_public_ref(value: str) -> bool:
    if not value.strip() or len(value) > 180:
        return False
    normalized = _normalize_key(value)
    if _is_sensitive_key_shape(normalized):
        return False
    return all(char.isalnum() or char in "._:-" for char in value)


def _is_safe_public_authority_ref(value: str) -> bool:
    if not _is_safe_public_ref(value):
        return False
    normalized = _normalize_public_ref_body(value)
    return not any(fragment in normalized for fragment in _PRIVATE_PUBLIC_REF_FRAGMENTS)


def _normalize_public_ref_body(value: str) -> str:
    body = value.split(":", 1)[1] if ":" in value else value
    return _normalize_key(body)


def _public_entry_ref(value: str, *, prefix: str) -> str:
    return _safe_public_ref(_public_text(value), prefix=prefix)


def _is_private_metadata_key(normalized_key: str) -> bool:
    return any(fragment in normalized_key for fragment in _PRIVATE_METADATA_KEY_FRAGMENTS)


def _is_sensitive_key_shape(normalized_key: str) -> bool:
    return any(fragment in normalized_key for fragment in _SENSITIVE_REF_FRAGMENTS)


def _mentions_private_metadata_key(value: str) -> bool:
    return _is_private_metadata_key(_normalize_key(value))


def _public_string_preview(value: str) -> str:
    parsed = _parse_json_container(value)
    if parsed is not None:
        return _public_preview(parsed)
    if _mentions_private_metadata_key(value):
        return _public_preview({"digest": _digest_json(value)})
    return value


def _parse_json_container(value: str) -> object | None:
    stripped = value.strip()
    if not (stripped.startswith("{") or stripped.startswith("[")):
        return None
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, Mapping | list) else None


def _normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _public_text(value: str) -> str:
    if _has_private_text_marker(value):
        return "[redacted-private]"
    return _PRIVATE_PATH_RE.sub("[redacted-path]", sanitize_tool_preview(value))


def _public_structured_text(value: str) -> str:
    return _PRIVATE_PATH_RE.sub("[redacted-path]", sanitize_tool_preview(value))


def _public_key_text(value: str) -> str:
    return _PRIVATE_PATH_RE.sub("[redacted-path]", sanitize_tool_preview(value))


def _has_private_text_marker(value: str) -> bool:
    phrase_text = re.sub(r"[_-]+", " ", value)
    if _PRIVATE_TEXT_RE.search(phrase_text):
        return True
    tokens = re.findall(r"[A-Za-z0-9]+", value)
    return any(
        fragment in token.lower()
        for token in tokens
        for fragment in _PRIVATE_TEXT_MARKER_FRAGMENTS
    )
