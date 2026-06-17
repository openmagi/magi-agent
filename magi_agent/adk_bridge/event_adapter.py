from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from typing import Literal

from google.adk.events import Event

from magi_agent.runtime.events import (
    NormalizedEvent,
    metadata_digest,
    public_refs,
    public_terminal_refs,
)
from magi_agent.ops.health import _truthy_env
from magi_agent.runtime.transcript import (
    AssistantTextEntry,
    TranscriptEntry,
    ToolCallEntry,
    ToolResultEntry,
    TurnAbortedEntry,
)
from magi_agent.transport import tool_preview as _tool_preview
from magi_agent.adk_bridge.wire_profile import WireProfile


_PRODUCTION_PATH_RE = re.compile(
    r"(?:/data/bots|/workspace|/var/lib/kubelet|/Users|/home|/private|/mnt|/root)"
    r"(?:/[^\s\"',}]+)*",
    re.IGNORECASE,
)
_PUBLIC_EVENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}$")
_PRIVATE_REF_RE = re.compile(
    r"(?<![A-Za-z0-9_.:@/-])"
    r"(?:"
    r"(?:memory|session|sessions|transcript|transcripts|child/transcripts|children/transcripts)"
    r"/[A-Za-z0-9._@+:/=-]+"
    r"|(?:memory|session|transcript):[A-Za-z0-9._@+:/=-]+"
    r")",
    re.IGNORECASE,
)
_GITHUB_PAT_RE = re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b")
_SLACK_TOKEN_RE = re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b")
_AWS_ACCESS_KEY_RE = re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")
_JWT_RE = re.compile(
    r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"
)
_GOOGLE_API_KEY_RE = re.compile(r"\bAIza[A-Za-z0-9_-]{20,}\b")
_TELEGRAM_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:bot)?\d{6,12}:[A-Za-z0-9_-]{20,}\b",
    re.IGNORECASE,
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
    "raweventpayload",
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
    "rawtoollog",
    "rawtoollogs",
    "toolcall",
    "toolcalls",
    "tooluse",
    "tooluses",
    "tooluseargs",
    "toolusearguments",
    "tooluseinput",
    "tooluseoutput",
    "tooluseresult",
    "tooluseresponse",
    "tooluselogs",
    "toolcallargs",
    "toolcallarguments",
    "toolcallinput",
    "toolcalloutput",
    "toolcallresult",
    "toolcallresponse",
    "toolcalllogs",
    "functioncallargs",
    "functioncall",
    "functioncalls",
    "functioncallarguments",
    "functioncallinput",
    "functioncalloutput",
    "functioncallresult",
    "functioncallresponse",
    "functionresponse",
    "functionresult",
    "functionoutput",
    "functionlog",
    "functionlogs",
    "rawfunctionlog",
    "rawfunctionlogs",
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
_PRIVATE_REF_MARKER_FRAGMENTS = (
    "hiddenreasoning",
    "chainofthought",
    "rawpayload",
    "raweventpayload",
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
    "toollog",
    "toollogs",
    "rawtoollog",
    "rawtoollogs",
    "tooluselogs",
    "toolcalllogs",
    "functionlog",
    "functionlogs",
    "rawfunctionlog",
    "rawfunctionlogs",
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
_RECEIPT_REF_RE = re.compile(r"^(?:receipt:)?sha256:[a-fA-F0-9]{64}$")
_HEARTBEAT_MAX_ITER = 100_000
_HEARTBEAT_MAX_ELAPSED_MS = 86_400_000
_HEARTBEAT_MAX_EVENT_AT = 4_102_444_800_000
_RUNNER_MAX_ATTEMPT = 10
_USAGE_MAX_TOKENS = 10_000_000
_USAGE_MAX_COST_USD = 10_000
_RUNNER_PHASE_ALIASES = {
    "prepare": "planning",
    "preparing": "planning",
    "model_prepare": "planning",
    "model_preparing": "planning",
    "model_start": "executing",
    "model_started": "executing",
    "model_run": "executing",
    "model_running": "executing",
    "model_final": "committing",
    "model_finalize": "committing",
    "model_finalizing": "committing",
    "finalize": "committing",
    "finalizing": "committing",
}
_MODEL_FALLBACK_REASON_CODES = frozenset(
    {
        "provider_fallback",
        "provider_unavailable",
        "provider_rate_limited",
        "provider_timeout",
        "provider_error",
        "model_unavailable",
        "model_context_window_exceeded",
        "routing_fallback",
        "empty_response",
        "safety_block",
        "fallback_disabled",
        "python_phase_route_invalid_model_route",
        "python_phase_route_budget_too_low",
        "python_phase_route_unsupported_model_capability",
    }
)
_RETRY_REASON_CODES = frozenset(
    {
        "retry_scheduled",
        "provider_transient_error",
        "provider_rate_limited",
        "provider_timeout",
        "stream_interrupted",
        "empty_response",
        "verifier_retry",
        "tool_retry",
    }
)
_LLM_PROGRESS_STAGES = frozenset({"started", "waiting", "completed"})
_TURN_STOP_REASONS = frozenset(
    {
        "end_turn",
        "tool_use",
        "max_tokens",
        "stop",
        "cancelled",
        "aborted",
        "error",
        "safety",
        "content_filter",
        "missing_runtime_receipt",
    }
)


@dataclass(frozen=True)
class EventProjection:
    agent_events: list[dict[str, object]] = field(default_factory=list)
    legacy_deltas: list[str] = field(default_factory=list)
    transcript_entries: list[TranscriptEntry] = field(default_factory=list)
    normalized_events: list[NormalizedEvent] = field(default_factory=list)


class OpenMagiEventBridge:
    def __init__(self, *, live_compatible: bool = False, wire_profile: WireProfile | None = None) -> None:
        self.live_compatible = live_compatible
        self._wire_profile = wire_profile   # None = CLI path, existing code UNCHANGED
        # True once this turn has streamed partial `text_delta` events whose
        # non-partial aggregate has not yet been seen — used to drop the duplicate
        # aggregate that arrives alongside a trailing tool call. See
        # ``_project_content_parts``.
        self._streamed_partial_text = False
        self._streamed_partial_public_text = ""

    def project_runner_start_event(
        self,
        *,
        turn_id: str,
        declared_route: str = "direct",
    ) -> EventProjection:
        return project_runner_start_event(
            turn_id=turn_id,
            declared_route=declared_route,
        )

    def project_runner_phase_event(
        self,
        *,
        turn_id: str,
        phase: str,
        status: str | None = None,
        label: str | None = None,
        message: str | None = None,
        detail: str | None = None,
        sequence: int | float | None = None,
        created_at: int | float | None = None,
    ) -> EventProjection:
        return project_runner_phase_event(
            turn_id=turn_id,
            phase=phase,
            status=status,
            label=label,
            message=message,
            detail=detail,
            sequence=sequence,
            created_at=created_at,
        )

    def project_runner_heartbeat_event(
        self,
        *,
        turn_id: str,
        iter: int | float | None = None,
        elapsed_ms: int | float | None = None,
        last_event_at: int | float | None = None,
    ) -> EventProjection:
        return project_runner_heartbeat_event(
            turn_id=turn_id,
            iter=iter,
            elapsed_ms=elapsed_ms,
            last_event_at=last_event_at,
        )

    def project_runner_model_fallback_event(
        self,
        *,
        turn_id: str,
        from_model: str,
        to_model: str,
        reason: str,
        attempt: int | float | None = None,
    ) -> EventProjection:
        return project_runner_model_fallback_event(
            turn_id=turn_id,
            from_model=from_model,
            to_model=to_model,
            reason=reason,
            attempt=attempt,
        )

    def project_runner_retry_event(
        self,
        *,
        turn_id: str,
        reason: str,
        retry_no: int | float | None = None,
        tool_use_id: str | None = None,
        tool_name: str | None = None,
    ) -> EventProjection:
        return project_runner_retry_event(
            turn_id=turn_id,
            reason=reason,
            retry_no=retry_no,
            tool_use_id=tool_use_id,
            tool_name=tool_name,
        )

    def project_runner_llm_progress_event(
        self,
        *,
        turn_id: str,
        stage: str = "waiting",
        label: str | None = None,
        detail: str | None = None,
        iter: int | float | None = None,
        elapsed_ms: int | float | None = None,
    ) -> EventProjection:
        return project_runner_llm_progress_event(
            turn_id=turn_id,
            stage=stage,
            label=label,
            detail=detail,
            iter=iter,
            elapsed_ms=elapsed_ms,
        )

    def project_runner_end_event(
        self,
        *,
        turn_id: str,
        status: str = "committed",
        stop_reason: str | None = None,
        reason: str | None = None,
        usage: dict[str, object] | None = None,
        receipt_ref: str | None = None,
    ) -> EventProjection:
        return project_runner_end_event(
            turn_id=turn_id,
            status=status,
            stop_reason=stop_reason,
            reason=reason,
            usage=usage,
            receipt_ref=receipt_ref,
        )

    def project_adk_event(self, event: Event, *, turn_id: str) -> EventProjection:
        if (event.error_code or event.error_message) and not _all_error_fields_benign(
            event.error_code, event.error_message
        ):
            message = event.error_message or event.error_code or "adk_error"
            public_message = _public_preview(message)
            agent_events: list[dict[str, object]] = [
                {
                    "type": "runtime_trace",
                    "turnId": _public_ref(turn_id, prefix="turn"),
                    "phase": "terminal_abort",
                    "severity": "error",
                    "title": "ADK event error",
                    "detail": public_message,
                },
                {
                    "type": "error",
                    "code": _public_text(event.error_code or "adk_error"),
                    "message": public_message,
                },
            ]
            if self.live_compatible:
                agent_events.append(
                    {
                        "type": "turn_end",
                        "turnId": _public_ref(turn_id, prefix="turn"),
                        "status": "aborted",
                        "reason": public_message,
                    }
                )
            normalized_event = NormalizedEvent(
                type="turn.failed",
                eventId=_normalized_event_id(event, suffix="turn-failed"),
                ts=_event_ts(event),
                turnId=turn_id,
                source="adk",
                payload={"reasonPreview": public_message},
                metadata={
                    "errorDigest": metadata_digest(
                        {"code": event.error_code, "message": message}
                    ),
                },
            )
            return EventProjection(
                agent_events=agent_events,
                transcript_entries=[
                    TurnAbortedEntry(ts=_event_ts(event), turn_id=turn_id, reason=message)
                ],
                normalized_events=[normalized_event],
            )

        prefix_events = _project_response_clear_events(event, turn_id=turn_id)
        if prefix_events:
            # A response_clear restarts the visible text; any in-flight partial-text
            # run is void, so the next non-partial aggregate is not a duplicate.
            self._streamed_partial_text = False
            self._streamed_partial_public_text = ""
        partial_run = [self._streamed_partial_text]
        partial_public_text = [self._streamed_partial_public_text]
        content_projection = _project_content_parts(
            event,
            turn_id=turn_id,
            live_compatible=self.live_compatible,
            partial_run=partial_run,
            partial_public_text=partial_public_text,
            wire_profile=self._wire_profile,
        )
        self._streamed_partial_text = partial_run[0]
        self._streamed_partial_public_text = partial_public_text[0]
        if (
            content_projection.agent_events
            or content_projection.legacy_deltas
            or content_projection.transcript_entries
        ):
            return _prepend_agent_events(content_projection, prefix_events)
        return EventProjection(agent_events=prefix_events)


def project_runner_start_event(
    *,
    turn_id: str,
    declared_route: str = "direct",
) -> EventProjection:
    route = declared_route if declared_route in {"direct", "subagent", "pipeline"} else "direct"
    return EventProjection(
        agent_events=[
            {
                "type": "turn_start",
                "turnId": _public_ref(turn_id, prefix="turn"),
                "declaredRoute": route,
            }
        ]
    )


def project_runner_phase_event(
    *,
    turn_id: str,
    phase: str,
    status: str | None = None,
    label: str | None = None,
    message: str | None = None,
    detail: str | None = None,
    sequence: int | float | None = None,
    created_at: int | float | None = None,
) -> EventProjection:
    _ = (status, label, message, detail, sequence, created_at)
    safe_phase = _turn_phase(phase)
    return EventProjection(
        agent_events=[
            {
                "type": "turn_phase",
                "turnId": _public_ref(turn_id, prefix="turn"),
                "phase": safe_phase,
            }
        ]
    )


def project_runner_heartbeat_event(
    *,
    turn_id: str,
    iter: int | float | None = None,
    elapsed_ms: int | float | None = None,
    last_event_at: int | float | None = None,
) -> EventProjection:
    event: dict[str, object] = {
        "type": "heartbeat",
        "turnId": _public_ref(turn_id, prefix="turn"),
    }
    _put_bounded_number(event, "iter", iter, minimum=0, maximum=_HEARTBEAT_MAX_ITER)
    _put_bounded_number(
        event,
        "elapsedMs",
        elapsed_ms,
        minimum=0,
        maximum=_HEARTBEAT_MAX_ELAPSED_MS,
    )
    _put_bounded_number(
        event,
        "lastEventAt",
        last_event_at,
        minimum=0,
        maximum=_HEARTBEAT_MAX_EVENT_AT,
    )
    return EventProjection(agent_events=[event])


def project_runner_model_fallback_event(
    *,
    turn_id: str,
    from_model: str,
    to_model: str,
    reason: str,
    attempt: int | float | None = None,
) -> EventProjection:
    safe_reason = _safe_reason_code(
        reason,
        allowed=_MODEL_FALLBACK_REASON_CODES,
        default="provider_fallback",
    )
    event: dict[str, object] = {
        "type": "runtime_trace",
        "turnId": _public_ref(turn_id, prefix="turn"),
        "phase": "retry_scheduled",
        "severity": "warning",
        "title": "Model fallback selected",
        "reasonCode": safe_reason,
        "detail": _public_bounded_text(f"{from_model} -> {to_model}"),
    }
    _put_bounded_number(
        event,
        "attempt",
        attempt,
        minimum=1,
        maximum=_RUNNER_MAX_ATTEMPT,
    )
    return EventProjection(agent_events=[event])


def project_runner_retry_event(
    *,
    turn_id: str,
    reason: str,
    retry_no: int | float | None = None,
    tool_use_id: str | None = None,
    tool_name: str | None = None,
) -> EventProjection:
    _ = turn_id
    event: dict[str, object] = {
        "type": "retry",
        "reason": _safe_reason_code(
            reason,
            allowed=_RETRY_REASON_CODES,
            default="retry_scheduled",
        ),
    }
    _put_bounded_number(
        event,
        "retryNo",
        retry_no,
        minimum=1,
        maximum=_RUNNER_MAX_ATTEMPT,
    )
    if tool_use_id is not None:
        event["toolUseId"] = _public_ref(tool_use_id, prefix="tool")
    if tool_name is not None:
        event["toolName"] = _public_bounded_text(tool_name)
    return EventProjection(agent_events=[event])


def project_runner_llm_progress_event(
    *,
    turn_id: str,
    stage: str = "waiting",
    label: str | None = None,
    detail: str | None = None,
    iter: int | float | None = None,
    elapsed_ms: int | float | None = None,
) -> EventProjection:
    stage_candidate = _reason_code_candidate(stage)
    safe_stage = stage_candidate if stage_candidate in _LLM_PROGRESS_STAGES else "waiting"
    event: dict[str, object] = {
        "type": "llm_progress",
        "turnId": _public_ref(turn_id, prefix="turn"),
        "stage": safe_stage,
    }
    if label is not None:
        event["label"] = _public_bounded_text(label)
    if detail is not None:
        event["detail"] = _public_bounded_text(detail)
    _put_bounded_number(event, "iter", iter, minimum=0, maximum=_HEARTBEAT_MAX_ITER)
    _put_bounded_number(
        event,
        "elapsedMs",
        elapsed_ms,
        minimum=0,
        maximum=_HEARTBEAT_MAX_ELAPSED_MS,
    )
    return EventProjection(agent_events=[event])


def project_runner_end_event(
    *,
    turn_id: str,
    status: str = "committed",
    stop_reason: str | None = None,
    reason: str | None = None,
    usage: dict[str, object] | None = None,
    receipt_ref: str | None = None,
) -> EventProjection:
    safe_receipt_ref = _safe_receipt_ref(receipt_ref)
    safe_status: Literal["committed", "aborted"] = "aborted"
    if status == "committed" and safe_receipt_ref is not None:
        safe_status = "committed"
    event: dict[str, object] = {
        "type": "turn_end",
        "turnId": _public_ref(turn_id, prefix="turn"),
        "status": safe_status,
    }
    if safe_status == "committed":
        event["stopReason"] = _safe_stop_reason(stop_reason, default="end_turn")
        event["receiptRef"] = safe_receipt_ref
        safe_usage = _public_usage(usage)
        if safe_usage:
            event["usage"] = safe_usage
    else:
        event["reason"] = _safe_stop_reason(reason or stop_reason, default="aborted")
        if status == "committed" and safe_receipt_ref is None:
            event["reason"] = "missing_runtime_receipt"
    return EventProjection(agent_events=[event])


def _project_content_parts(
    event: Event,
    *,
    turn_id: str,
    live_compatible: bool,
    partial_run: list[bool],
    partial_public_text: list[str],
    wire_profile: WireProfile | None = None,
) -> EventProjection:
    agent_events: list[dict[str, object]] = []
    legacy_deltas: list[str] = []
    transcript_entries: list[TranscriptEntry] = []
    normalized_events: list[NormalizedEvent] = []
    final_text_chunks: list[str] = []
    is_final_response = _event_is_final_response(event)
    saw_tool_part = False

    def flush_final_text(*, public_if_non_final: bool = False) -> None:
        if not final_text_chunks:
            return
        text = "".join(final_text_chunks)
        final_text_chunks.clear()
        public_text = _public_stream_text(text)
        if is_final_response:
            token_text = _unstreamed_final_text(public_text, partial_public_text[0])
            if token_text:
                agent_events.append({"type": "text_delta", "delta": token_text})
            # The final reply's aggregate has now been reconciled with any
            # partials streamed earlier in the turn; close the partial run.
            partial_run[0] = False
            partial_public_text[0] = ""
            transcript_entries.append(
                AssistantTextEntry(ts=_event_ts(event), turn_id=turn_id, text=public_text)
            )
            normalized_events.append(
                NormalizedEvent(
                    type="model.message.completed",
                    eventId=_normalized_event_id(
                        event,
                        suffix=f"model-completed-{len(normalized_events)}",
                    ),
                    ts=_event_ts(event),
                    turnId=turn_id,
                    source="adk",
                    payload={"textPreview": public_text},
                    metadata={"contentDigest": metadata_digest(text)},
                )
            )
            return
        if not public_if_non_final:
            return
        if partial_run[0]:
            # Streaming already delivered this segment token-by-token as partial
            # `text_delta` events; the aggregated NON-partial event that carries a
            # trailing tool call repeats the whole text, which would duplicate it in
            # the client transcript (e.g. "…subagent.…subagent." right before a tool
            # call). Drop the aggregate. When the text was NOT streamed first — a
            # single mixed text+tool event with no preceding partials — ``partial_run``
            # is False and the text is emitted normally.
            partial_run[0] = False
            partial_public_text[0] = ""
            return
        agent_events.append({"type": "text_delta", "delta": public_text})
        normalized_events.append(
            NormalizedEvent(
                type="model.message.delta",
                eventId=_normalized_event_id(
                    event,
                    suffix=f"model-delta-{len(normalized_events)}",
                ),
                ts=_event_ts(event),
                turnId=turn_id,
                source="adk",
                payload={"textPreview": public_text},
                metadata={"contentDigest": metadata_digest(text)},
            )
        )
        if not live_compatible:
            legacy_deltas.append(public_text)

    for index, part in enumerate(_event_parts(event)):
        if getattr(part, "thought", False):
            # Model reasoning (ADK marks it thought=True; covers Anthropic
            # thinking blocks and LiteLLM reasoning_content e.g. Kimi/Gemini).
            # Surface streaming thought on the thinking_delta channel so the
            # hosted UI renders it in the collapsible thinking block instead of
            # dropping it. sse.py gates this behind MAGI_STREAM_THINKING.
            # Gated at the producer (defense in depth): when MAGI_STREAM_THINKING
            # is off the projection layer stays a hard privacy boundary and emits
            # nothing for thought parts. When on, surface streaming thought as
            # thinking_delta; sse.py redacts/forwards it for the public path.
            thought_text = getattr(part, "text", None)
            if thought_text and event.partial and _truthy_env("MAGI_STREAM_THINKING"):
                agent_events.append(
                    {"type": "thinking_delta", "delta": _public_stream_text(thought_text)}
                )
            continue
        text = getattr(part, "text", None)
        if text:
            if event.partial:
                public_text = _public_stream_text(text)
                agent_events.append({"type": "text_delta", "delta": public_text})
                partial_run[0] = True
                partial_public_text[0] += public_text
                normalized_events.append(
                    NormalizedEvent(
                        type="model.message.delta",
                        eventId=_normalized_event_id(
                            event,
                            suffix=f"model-delta-{len(normalized_events)}",
                        ),
                        ts=_event_ts(event),
                        turnId=turn_id,
                        source="adk",
                        payload={"textPreview": public_text},
                        metadata={"contentDigest": metadata_digest(text)},
                    )
                )
                if not live_compatible:
                    legacy_deltas.append(public_text)
            else:
                final_text_chunks.append(text)

        function_call = getattr(part, "function_call", None)
        if function_call:
            flush_final_text(public_if_non_final=True)
            saw_tool_part = True
            tool_projection = _project_function_call_part(
                event,
                turn_id=turn_id,
                function_call=function_call,
                index=index,
                live_compatible=live_compatible,
                wire_profile=wire_profile,
            )
            agent_events.extend(tool_projection.agent_events)
            transcript_entries.extend(tool_projection.transcript_entries)
            normalized_events.extend(tool_projection.normalized_events)
            continue

        function_response = getattr(part, "function_response", None)
        if function_response:
            flush_final_text(public_if_non_final=True)
            saw_tool_part = True
            tool_projection = _project_function_response_part(
                event,
                turn_id=turn_id,
                function_response=function_response,
                index=index,
                live_compatible=live_compatible,
                wire_profile=wire_profile,
            )
            agent_events.extend(tool_projection.agent_events)
            transcript_entries.extend(tool_projection.transcript_entries)
            normalized_events.extend(tool_projection.normalized_events)

    flush_final_text(public_if_non_final=saw_tool_part)
    if is_final_response and live_compatible:
        end_projection = project_runner_end_event(
            turn_id=turn_id,
            status="committed",
            stop_reason=_final_stop_reason(event),
        )
        agent_events.extend(end_projection.agent_events)

    return EventProjection(
        agent_events=agent_events,
        legacy_deltas=legacy_deltas,
        transcript_entries=transcript_entries,
        normalized_events=normalized_events,
    )


def _project_function_call_part(
    event: Event,
    *,
    turn_id: str,
    function_call: object,
    index: int,
    live_compatible: bool,
    wire_profile: WireProfile | None = None,
) -> EventProjection:
    name = getattr(function_call, "name", None) or "unknown_tool"
    args = getattr(function_call, "args", None) or {}
    adk_id = getattr(function_call, "id", None)
    public_name = _public_tool_name(name)
    input_digest = metadata_digest(args)

    if wire_profile is not None:
        tool_use_id = wire_profile.tool_id(name, args, adk_id, index)
        agent_event = wire_profile.build_tool_start(tool_use_id, public_name, _public_preview(args))
    else:
        # EXISTING code, byte-for-byte unchanged
        tool_use_id = _tool_use_id(
            event,
            turn_id=turn_id,
            name=name,
            index=index,
            adk_id=adk_id,
            kind="call",
        )
        agent_event = {
            "type": "tool_start",
            "id": tool_use_id,
            "name": public_name,
            "input_preview": _public_preview(args),
        }
        if live_compatible:
            agent_event["eventId"] = _public_event_id(
                event,
                suffix=f"tool-start-{index}",
            )
            agent_event["inputDigest"] = input_digest

    return EventProjection(
        agent_events=[agent_event],
        transcript_entries=[
            ToolCallEntry(
                ts=_event_ts(event),
                turn_id=turn_id,
                tool_use_id=tool_use_id,
                name=public_name,
                input=args,
            )
        ],
        normalized_events=[
            NormalizedEvent(
                type="tool.call.started",
                eventId=_normalized_event_id(event, suffix=f"tool-start-{index}"),
                ts=_event_ts(event),
                turnId=turn_id,
                callId=tool_use_id,
                source="adk",
                toolName=public_name,
                payload={"inputPreview": _public_preview(args)},
                metadata={"inputDigest": input_digest},
            )
        ],
    )


def _project_function_response_part(
    event: Event,
    *,
    turn_id: str,
    function_response: object,
    index: int,
    live_compatible: bool,
    wire_profile: WireProfile | None = None,
) -> EventProjection:
    name = getattr(function_response, "name", None) or "unknown_tool"
    adk_id = getattr(function_response, "id", None)
    response = getattr(function_response, "response", None) or {}
    is_error = _is_error_response(response)
    status = "error" if is_error else "ok"
    output = _preview(response)
    normalized_type = "tool.call.failed" if is_error else "tool.call.completed"
    public_name = _public_tool_name(name)
    normalized_metadata: dict[str, object] = {
        "outputDigest": metadata_digest(response),
    }
    tool_result_refs = _tool_result_refs(response)
    if tool_result_refs:
        normalized_metadata["toolResultRefs"] = list(tool_result_refs)
    source_refs = _source_refs(response)
    if source_refs:
        normalized_metadata["sourceRefs"] = list(source_refs)

    if wire_profile is not None:
        # Response side: we need args to compute tool_id, but FunctionResponse has none.
        # Use empty dict for args (response-side id, consistent with call-side convention).
        tool_use_id = wire_profile.tool_id(name, {}, adk_id, index)
        agent_event: dict[str, object] = wire_profile.build_tool_end(
            tool_use_id, status, _public_preview(response)
        )
    else:
        # EXISTING code, byte-for-byte unchanged
        tool_use_id = _tool_use_id(
            event,
            turn_id=turn_id,
            name=name,
            index=index,
            adk_id=adk_id,
            kind="response",
        )
        agent_event = {
            "type": "tool_end",
            "id": tool_use_id,
            "status": status,
            "output_preview": _public_preview(response),
            "durationMs": 0,
        }
        if live_compatible:
            agent_event["eventId"] = _public_event_id(
                event,
                suffix=f"tool-end-{index}",
            )
            agent_event["outputDigest"] = normalized_metadata["outputDigest"]
            transcript_refs = public_terminal_refs([*tool_result_refs, *source_refs])
            if transcript_refs:
                agent_event["transcriptRefs"] = list(transcript_refs)

    return EventProjection(
        agent_events=[agent_event],
        transcript_entries=[
            ToolResultEntry(
                ts=_event_ts(event),
                turn_id=turn_id,
                tool_use_id=tool_use_id,
                status=status,
                output=output,
                is_error=is_error,
            )
        ],
        normalized_events=[
            NormalizedEvent(
                type=normalized_type,
                eventId=_normalized_event_id(event, suffix=f"tool-end-{index}"),
                ts=_event_ts(event),
                turnId=turn_id,
                callId=tool_use_id,
                source="adk",
                toolName=public_name,
                payload={"outputPreview": _public_preview(response), "status": status},
                metadata=normalized_metadata,
            )
        ],
    )


def _prepend_agent_events(
    projection: EventProjection,
    prefix_events: list[dict[str, object]],
) -> EventProjection:
    if not prefix_events:
        return projection
    return EventProjection(
        agent_events=[*prefix_events, *projection.agent_events],
        legacy_deltas=projection.legacy_deltas,
        transcript_entries=projection.transcript_entries,
        normalized_events=projection.normalized_events,
    )


def _project_response_clear_events(
    event: Event,
    *,
    turn_id: str,
) -> list[dict[str, object]]:
    reason = _response_clear_reason(event)
    if reason is None:
        return []
    return [
        {
            "type": "response_clear",
            "turnId": _public_ref(turn_id, prefix="turn"),
            "reason": reason,
        }
    ]


def _response_clear_reason(event: Event) -> str | None:
    actions = getattr(event, "actions", None)
    if getattr(actions, "rewind_before_invocation_id", None):
        return "adk_rewind"

    metadata = getattr(event, "custom_metadata", None)
    if not isinstance(metadata, dict):
        return None
    if metadata.get("response_clear") is True or metadata.get("responseClear") is True:
        reason = metadata.get("response_clear_reason", metadata.get("responseClearReason"))
        if isinstance(reason, str) and reason.strip():
            return _public_preview(reason)
        return "adk_response_clear"
    return None


def _event_parts(event: Event) -> list[object]:
    return list(event.content.parts if event.content and event.content.parts else [])


def _event_ts(event: Event) -> int | float:
    return event.timestamp if getattr(event, "timestamp", None) else 0


def _event_is_final_response(event: Event) -> bool:
    is_final_response = getattr(event, "is_final_response", None)
    turn_complete = bool(getattr(event, "turn_complete", False))
    if callable(is_final_response):
        return bool(is_final_response()) or turn_complete
    return turn_complete


# A normal finish status (e.g. Gemini surfacing "completed"/"STOP") can arrive
# in an ADK event's error_code/error_message fields. That is NOT a turn failure,
# so it must not project a terminal_abort trace / error / aborted turn_end —
# doing so renders a spurious "응답 생성이 중단되었습니다: completed" banner
# downstream even though the answer completed normally.
_BENIGN_FINISH_SIGNAL_RE = re.compile(
    r"(?:complete[d]?|committed|done|finished|success(?:ful)?|ok|stop|"
    r"stop_sequence|end_turn|normal)",
    re.IGNORECASE,
)


def _is_benign_finish_signal(value: object) -> bool:
    return isinstance(value, str) and bool(
        _BENIGN_FINISH_SIGNAL_RE.fullmatch(value.strip())
    )


def _all_error_fields_benign(error_code: object, error_message: object) -> bool:
    populated = [field for field in (error_code, error_message) if field]
    return bool(populated) and all(_is_benign_finish_signal(f) for f in populated)


def _final_stop_reason(event: Event) -> str:
    finish_reason = getattr(event, "finish_reason", None)
    if finish_reason is None:
        return "end_turn"
    value = getattr(finish_reason, "value", None) or getattr(finish_reason, "name", None)
    if value is None:
        value = str(finish_reason)
    return _public_text(str(value)) or "end_turn"


def _turn_phase(value: str) -> str:
    candidate = _reason_code_candidate(value)
    if candidate in _RUNNER_PHASE_ALIASES:
        return _RUNNER_PHASE_ALIASES[candidate]
    if candidate in {
        "pending",
        "planning",
        "executing",
        "verifying",
        "committing",
        "committed",
        "aborted",
    }:
        return candidate
    return "pending"


def _safe_reason_code(
    value: str,
    *,
    allowed: frozenset[str],
    default: str,
) -> str:
    candidate = _reason_code_candidate(value)
    if candidate in allowed:
        return candidate
    return default


def _reason_code_candidate(value: str) -> str:
    return re.sub(r"[^a-z0-9_:-]+", "_", value.strip().lower().replace("-", "_")).strip(
        "_:"
    )[:80]


def _public_bounded_text(value: str, *, limit: int = 240) -> str:
    if _has_private_text_marker(value):
        return "[redacted-private]"
    redacted = _public_text(value)
    if len(redacted) > limit:
        return f"{redacted[: limit - 3]}..."
    return redacted


def _safe_stop_reason(value: str | None, *, default: str) -> str:
    if value is None:
        return default
    return _safe_reason_code(value, allowed=_TURN_STOP_REASONS, default=default)


def _safe_receipt_ref(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if _RECEIPT_REF_RE.fullmatch(candidate):
        return candidate
    return None


def _public_stream_text(value: str) -> str:
    if _has_private_text_marker(value):
        return "[redacted-private]"
    return _public_text(value)


def _unstreamed_final_text(final_text: str, streamed_text: str) -> str:
    if not streamed_text:
        return final_text
    if final_text.startswith(streamed_text):
        return final_text[len(streamed_text) :]
    if streamed_text.endswith(final_text):
        return ""
    max_overlap = min(len(final_text), len(streamed_text))
    for size in range(max_overlap, 0, -1):
        if streamed_text.endswith(final_text[:size]):
            return final_text[size:]
    return final_text


def _has_private_text_marker(value: str) -> bool:
    if _PRIVATE_TEXT_RE.search(value):
        return True
    normalized = re.sub(r"[^a-z0-9]", "", value.lower())
    return any(fragment in normalized for fragment in _PRIVATE_TEXT_MARKER_FRAGMENTS)


def _has_private_ref_marker(value: str) -> bool:
    if _PRIVATE_TEXT_RE.search(value):
        return True
    normalized = re.sub(r"[^a-z0-9]", "", value.lower())
    return any(fragment in normalized for fragment in _PRIVATE_REF_MARKER_FRAGMENTS)


def _put_bounded_number(
    event: dict[str, object],
    key: str,
    value: int | float | None,
    *,
    minimum: int | float,
    maximum: int | float,
) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return
    try:
        numeric_value = float(value)
    except OverflowError:
        return
    if math.isfinite(numeric_value) and minimum <= numeric_value <= maximum:
        event[key] = value


def _public_usage(value: dict[str, object] | None) -> dict[str, int | float] | None:
    if not isinstance(value, dict):
        return None
    usage: dict[str, int | float] = {}
    for source_key, target_key in (
        ("inputTokens", "inputTokens"),
        ("outputTokens", "outputTokens"),
        ("costUsd", "costUsd"),
    ):
        item = value.get(source_key)
        if isinstance(item, bool) or not isinstance(item, int | float):
            continue
        max_value = _USAGE_MAX_COST_USD if target_key == "costUsd" else _USAGE_MAX_TOKENS
        try:
            numeric_item = float(item)
        except OverflowError:
            return None
        if not math.isfinite(numeric_item) or not 0 <= item <= max_value:
            return None
        usage[target_key] = item
    return usage or None


def _tool_use_id(
    event: Event,
    *,
    turn_id: str,
    name: str,
    index: int,
    adk_id: str | None,
    kind: str,
) -> str:
    if isinstance(adk_id, str) and adk_id.strip():
        return _public_ref(adk_id, prefix=f"adk-tool-{kind}")
    fallback_source = json.dumps(
        {
            "kind": kind,
            "eventId": getattr(event, "id", None),
            "fingerprint": _event_fingerprint(event),
            "invocationId": event.invocation_id or turn_id,
            "name": name,
            "index": index,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha1(fallback_source.encode("utf-8")).hexdigest()[:12]
    return f"adk-tool-{kind}-{digest}"


def _normalized_event_id(event: Event, *, suffix: str) -> str:
    event_id = getattr(event, "id", None)
    if _is_safe_normalized_event_id(event_id):
        return f"{event_id}:{suffix}"
    fallback_source = (
        f"{event_id or event.invocation_id or 'turn'}:{suffix}:{_event_fingerprint(event)}"
    )
    digest = hashlib.sha1(fallback_source.encode("utf-8")).hexdigest()[:12]
    return f"adk-event-{digest}"


def _is_safe_normalized_event_id(value: object) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    if _PUBLIC_EVENT_ID_RE.fullmatch(value) is None:
        return False
    if _has_private_ref_marker(value):
        return False
    return _public_text(value) == value


def _public_event_id(event: Event, *, suffix: str) -> str:
    event_id = _normalized_event_id(event, suffix=suffix)
    return _public_ref(event_id, prefix="event")


def _event_fingerprint(event: Event) -> str:
    material = {
        "author": getattr(event, "author", None),
        "invocationId": getattr(event, "invocation_id", None),
        "parts": [_part_fingerprint(part) for part in _event_parts(event)],
        "errorCode": getattr(event, "error_code", None),
        "errorMessageDigest": metadata_digest(getattr(event, "error_message", None)),
    }
    return hashlib.sha1(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]


def _part_fingerprint(part: object) -> dict[str, object]:
    text = getattr(part, "text", None)
    function_call = getattr(part, "function_call", None)
    function_response = getattr(part, "function_response", None)
    if text:
        return {"textDigest": metadata_digest(text)}
    if function_call:
        return {
            "functionCall": {
                "id": getattr(function_call, "id", None),
                "name": getattr(function_call, "name", None),
                "argsDigest": metadata_digest(getattr(function_call, "args", None) or {}),
            }
        }
    if function_response:
        return {
            "functionResponse": {
                "id": getattr(function_response, "id", None),
                "name": getattr(function_response, "name", None),
                "responseDigest": metadata_digest(
                    getattr(function_response, "response", None) or {}
                ),
            }
        }
    return {"part": "unknown"}


def _tool_result_refs(response: object) -> tuple[str, ...]:
    if not isinstance(response, dict):
        return ()
    candidates: list[object] = []
    for key in ("resultRef", "resultRefs", "digest", "digests", "artifactRefs", "fileRefs"):
        value = response.get(key)
        if isinstance(value, list | tuple):
            candidates.extend(value)
        elif value is not None:
            candidates.append(value)
    return public_terminal_refs(candidates)


def _source_refs(response: object) -> tuple[str, ...]:
    if not isinstance(response, dict):
        return ()
    candidates: list[object] = []
    for key in ("sourceRef", "sourceRefs", "sources", "fileRefs"):
        value = response.get(key)
        if isinstance(value, list | tuple):
            candidates.extend(value)
        elif value is not None:
            candidates.append(value)
    refs: list[str] = []
    for candidate in candidates:
        if isinstance(candidate, dict):
            for key in ("sourceRef", "sourceId", "ref", "id"):
                nested = candidate.get(key)
                if isinstance(nested, str):
                    refs.append(nested)
        elif isinstance(candidate, str):
            refs.append(candidate)
    return public_refs(refs, prefix="source")


def _preview(value: object) -> str:
    return json.dumps(
        _json_safe_preview_value(value),
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
    )


def _json_safe_preview_value(value: object) -> object:
    if isinstance(value, bool):
        return value
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {
            str(key): _json_safe_preview_value(item_value)
            for key, item_value in value.items()
        }
    if isinstance(value, list | tuple):
        return [_json_safe_preview_value(item) for item in value]
    return value


def _public_preview(value: object) -> str:
    public_value = _public_json_safe_preview_value(value)
    preview = (
        public_value
        if isinstance(public_value, str)
        else json.dumps(
            public_value,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        )
    )
    redacted = _public_text(preview)
    if len(redacted) > _tool_preview.MAX_TOOL_PREVIEW:
        return f"{redacted[: _tool_preview.MAX_TOOL_PREVIEW - 3]}..."
    return redacted


def _public_json_safe_preview_value(value: object) -> object:
    if isinstance(value, bool):
        return value
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        result: dict[str, object] = {}
        for key, item_value in value.items():
            key_text = str(key)
            if _is_private_preview_key(key_text):
                result[f"{_public_text(key_text)}Digest"] = metadata_digest(item_value)
                continue
            result[key_text] = _public_json_safe_preview_value(item_value)
        return result
    if isinstance(value, list | tuple):
        return [_public_json_safe_preview_value(item) for item in value]
    if isinstance(value, str):
        parsed = _parse_json_container(value)
        if parsed is not None:
            return _public_json_safe_preview_value(parsed)
        if _mentions_private_preview_key(value):
            return {"digest": metadata_digest(value)}
    return value


def _public_text(value: str) -> str:
    redacted = _tool_preview.sanitize_tool_preview(value)
    redacted = _GITHUB_PAT_RE.sub("[redacted]", redacted)
    redacted = _SLACK_TOKEN_RE.sub("[redacted]", redacted)
    redacted = _AWS_ACCESS_KEY_RE.sub("[redacted]", redacted)
    redacted = _JWT_RE.sub("[redacted]", redacted)
    redacted = _GOOGLE_API_KEY_RE.sub("[redacted]", redacted)
    redacted = _TELEGRAM_TOKEN_RE.sub("[redacted]", redacted)
    redacted = _PRIVATE_TEXT_RE.sub("[redacted-private]", redacted)
    redacted = _PRIVATE_REF_RE.sub("[redacted-ref]", redacted)
    redacted = _PRODUCTION_PATH_RE.sub("[redacted-path]", redacted)
    return redacted


def _public_tool_name(value: str) -> str:
    if _has_private_text_marker(value):
        return "[redacted-private]"
    return _public_text(value)


def _public_ref(value: str, *, prefix: str) -> str:
    if _has_private_ref_marker(value):
        return f"{prefix}:{hashlib.sha1(value.encode('utf-8')).hexdigest()[:16]}"
    public = _public_text(value)
    refs = public_refs([public], prefix=prefix)
    if refs and "[redacted" not in public:
        return refs[0]
    return f"{prefix}:{hashlib.sha1(value.encode('utf-8')).hexdigest()[:16]}"


def _is_private_preview_key(value: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", value.lower())
    private_fragments = (
        "childoutput",
        "childprompt",
        "childtranscript",
        "hiddenreasoning",
        "memorypayload",
        "privatecontext",
        "privatememory",
        "prompt",
        "rawpayload",
        "raweventpayload",
        "rawproviderpayload",
        "rawmodelpayload",
        "rawproviderresponse",
        "rawmodelresponse",
        "rawchildoutput",
        "rawchildtranscript",
        "rawargs",
        "rawarguments",
        "rawinput",
        "rawoutput",
        "rawresponse",
        "rawresult",
        "rawtoolargs",
        "rawtoolarguments",
        "rawtoolinput",
        "rawtoolresponse",
        "rawtoolresult",
        "rawtooloutput",
        "toolargs",
        "toolarguments",
        "toolinput",
        "tooloutput",
        "toolresponse",
        "toollog",
        "toolresult",
        "toollogs",
        "rawtoollog",
        "rawtoollogs",
        "toolcall",
        "toolcalls",
        "tooluse",
        "tooluses",
        "rawtooluseargs",
        "rawtoolusearguments",
        "rawtooluseinput",
        "rawtooluseoutput",
        "rawtooluseresult",
        "rawtooluseresponse",
        "rawtooluselogs",
        "tooluseargs",
        "toolusearguments",
        "tooluseinput",
        "tooluseoutput",
        "tooluseresult",
        "tooluseresponse",
        "tooluselogs",
        "rawtoolcallargs",
        "rawtoolcallarguments",
        "rawtoolcallinput",
        "rawtoolcalloutput",
        "rawtoolcallresult",
        "rawtoolcallresponse",
        "rawtoolcalllogs",
        "toolcallargs",
        "toolcallarguments",
        "toolcallinput",
        "toolcalloutput",
        "toolcallresult",
        "toolcallresponse",
        "toolcalllogs",
        "functioncallargs",
        "functioncall",
        "functioncalls",
        "functioncallarguments",
        "functioncallinput",
        "functioncalloutput",
        "functioncallresult",
        "functioncallresponse",
        "functionresponse",
        "functionresult",
        "functionoutput",
        "functionlog",
        "functionlogs",
        "rawfunctionlog",
        "rawfunctionlogs",
        "sourcesnapshot",
        "rawsourcesnapshot",
    )
    return any(fragment in normalized for fragment in private_fragments)


def _mentions_private_preview_key(value: str) -> bool:
    return _is_private_preview_key(value)


def _parse_json_container(value: str) -> object | None:
    stripped = value.strip()
    if not (stripped.startswith("{") or stripped.startswith("[")):
        return None
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict | list) else None


def _is_error_response(response: object) -> bool:
    if not isinstance(response, dict):
        return False
    status = response.get("status")
    if isinstance(status, str) and status.lower() in {
        "blocked",
        "error",
        "failed",
        "needs_approval",
    }:
        return True
    return bool(
        response.get("error")
        or response.get("errorCode")
        or response.get("errorMessage")
        or response.get("isError")
        or response.get("is_error")
    )
