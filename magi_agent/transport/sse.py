from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
from collections.abc import Mapping
from urllib.parse import SplitResult, urlsplit

from magi_agent.composio.redaction import redact_composio_text
from magi_agent.ops.health import _truthy_env
from magi_agent.runtime.public_events import rule_check_event_has_authority
from magi_agent.shared import tool_preview as _tool_preview


_PRODUCTION_PATH_RE = re.compile(
    r"(?:/data/bots|/workspace|/var/lib/kubelet|/Users|/home|/private|/mnt)"
    r"(?:/[^\s\"',}]+)*",
    re.IGNORECASE,
)
_MAX_DOCUMENT_DRAFT_PREVIEW = 6_000
_MAX_PATCH_PREVIEW_FILES = 100
_MAX_TASK_BOARD_TASKS = 50
_MAX_BROWSER_FRAME_BASE64 = 1_000_000
_MAX_BROWSER_FRAME_ACTION = 64
_BROWSER_FRAME_BASE64_RE = re.compile(r"^[A-Za-z0-9+/]+={0,2}$")
_SENSITIVE_URL_PATH_RE = re.compile(
    r"/(?:auth|callback|cookie|oauth|session|sessions)(?:[/?#]|$)",
    re.IGNORECASE,
)
_SENSITIVE_ROUTE_PATH_RE = re.compile(
    r"(^|[^A-Za-z0-9._/-])"
    r"((?:https?://[^/\s\"'<>)]*)?(?:/?[A-Za-z0-9._-]+/)*/?"
    r"(?:auth|callback|cookie|oauth|sessions|session)(?:[/?#][^\s\"'<>)]*|$))",
    re.IGNORECASE,
)
_SENSITIVE_QUERY_FRAGMENT_RE = re.compile(
    r"(?:[?#]|%(?:25)*(?:3f|23))[^\s\"'<>)]*(?:auth|callback|code|cookie|session|state|token)[^\s\"'<>)]*",
    re.IGNORECASE,
)
_QUERY_FRAGMENT_TOKEN_RE = re.compile(
    r"(?:[?#]|%(?:25)*(?:3f|23))[^\s\"'<>)]*",
    re.IGNORECASE,
)
_SENSITIVE_ROUTE_SEGMENT_RE = re.compile(
    r"(?:^|[/?#._-])(?:auth|callback|cookie|oauth|sessions|session)(?:[/?#._-]|$)",
    re.IGNORECASE,
)
_SESSION_ASSIGNMENT_RE = re.compile(
    r"\b((?:code|session|state)\s*=\s*)[^\s,}]+",
    re.IGNORECASE,
)
_ROUTE_PATH_TOKEN_RE = re.compile(
    r"(?:https?://[^\s\"'<>)]*|[A-Za-z0-9._/-]*(?:/|%(?:25)*(?:2f|5c)|[?#]|%(?:25)*(?:3f|23))[^\s\"'<>)]*)",
    re.IGNORECASE,
)
_ENCODED_PATH_SEPARATOR_RE = re.compile(r"%(?:25)*(?:2f|5c)", re.IGNORECASE)
_ENCODED_QUERY_SEPARATOR_RE = re.compile(r"%(?:25)*3f", re.IGNORECASE)
_ENCODED_HASH_SEPARATOR_RE = re.compile(r"%(?:25)*23", re.IGNORECASE)
_ENCODED_SEGMENT_SEPARATOR_RE = re.compile(r"%(?:25)*(?:2d|2e|5f)", re.IGNORECASE)
_PERCENT_ENCODED_BYTE_RE = re.compile(r"%(?:25)*([0-9a-fA-F]{2})", re.IGNORECASE)
_GITHUB_FINE_GRAINED_PAT_RE = re.compile(r"\bgithub_pat_[A-Za-z0-9_]{12,}\b")
_RECIPE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}(?:\.[a-z0-9][a-z0-9_-]{0,63})+$")
_RECIPE_VERSION_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
_RECIPE_DIGEST_RE = re.compile(r"^sha256:[a-fA-F0-9]{64}$")
_RUNTIME_TYPED_DIGEST_RE = re.compile(r"^sha256:(?:activity|heartbeat):[a-fA-F0-9]{64}$")
_RECEIPT_REF_RE = re.compile(r"^(?:receipt:)?sha256:[a-fA-F0-9]{64}$")
_PUBLIC_EVIDENCE_REF_RE = re.compile(
    r"^(?:(?:receipt:)?sha256:[a-fA-F0-9]{64}|(?:evidence|source|file|result|tool-result):"
    r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,160})$"
)
_PUBLIC_REASON_CODE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,119}$")
_TURN_USAGE_MAX_TOKENS = 10_000_000
_TURN_USAGE_MAX_COST_USD = 10_000
_PUBLIC_MAX_COUNT = 1_000_000
_PUBLIC_MAX_ITER = 100_000
_PUBLIC_MAX_DURATION_MS = 86_400_000
_PUBLIC_MAX_TIMESTAMP_MS = 4_102_444_800_000
_PUBLIC_MAX_PROGRESS = 100
_PUBLIC_MAX_ATTEMPT = 100
_PUBLIC_MAX_COLLECTION_SIZE = 10_000
_PUBLIC_MAX_RATIO = 1
_PRIVATE_TEXT_RE = re.compile(
    r"\b(?:"
    r"hidden\s+reasoning|"
    r"chain\s+of\s+thought|"
    r"raw\s+(?:(?:[a-z0-9_-]+\s+){0,3}(?:payload|response|output|"
    r"result|body|transcript)|prompt|adk\s+event)|"
    r"(?:raw\s+)?tool\s+(?:args?|arguments?|inputs?|outputs?|results?|responses?|logs?)|"
    r"private\s+tool\s+logs?|"
    r"private\s+(?:mission|goal|task)\s+payload|"
    r"(?:raw\s+)?source\s+snapshot|"
    r"(?:raw\s+)?(?:system\s+|developer\s+|user\s+)?prompt|"
    r"private\s+(?:active\s+snapshot|prompt|payload|context|memory|transcript|source)|"
    r"memory\s+context\s+hidden"
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
    "functioncall",
    "functioncalls",
    "functioncallargs",
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
_PATCH_OPERATIONS = frozenset({"create", "update", "delete"})
_TASK_BOARD_STATUSES = frozenset({"pending", "in_progress", "completed", "cancelled"})
_BROWSER_FRAME_CONTENT_TYPES = frozenset({"image/png", "image/jpeg"})
_RECIPE_SELECTION_STATUSES = frozenset({"applied", "blocked", "omitted"})
_RECIPE_SELECTION_SOURCES = frozenset({"explicit", "automatic", "default", "mixed"})
_UNSAFE_RECIPE_REF_TEXT = frozenset((
    "secret",
    "token",
    "credential",
    "password",
    "bearer",
    "authorization",
    "auth",
    "cookie",
    "sk-proj",
    "ghp_",
))
_UNSAFE_RECIPE_REF_PREFIX_RE = re.compile(
    r"^(?:sk[-_][a-z0-9]|github_pat_|gh[pousr]_|xox[baprs]-)",
    re.IGNORECASE,
)
_RECIPE_OMISSION_REASONS = frozenset((
    "malformed_explicit_recipe_selection",
    "explicit_recipe_missing",
    "explicit_recipe_disabled",
    "explicit_recipe_unauthorized",
    "version_mismatch",
    "digest_mismatch",
    "incompatible_runtime_contract",
    "dependency_unavailable",
    "dependency_unauthorized",
    "dependency_incompatible_runtime_contract",
    "dependency_forbidden_tool_ref",
    "forbidden_tool_ref",
    "forbidden_projection_policy",
    "hard_invariant_downgrade",
))
_PLUGIN_PROJECTED_EVENT_TYPE_RE = re.compile(r"^[a-z][a-z0-9_:-]{0,80}$")
_PLUGIN_PROJECTED_EVENT_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_:-]{0,80}$")
_PRIVATE_PROJECTED_REF_RE = re.compile(
    r"(?<![A-Za-z0-9_.:@/-])"
    r"(?:"
    r"(?:memory|session|sessions|transcript|transcripts|child/transcripts|children/transcripts)"
    r"/[A-Za-z0-9._@+:/=-]+"
    r"|(?:memory|session|transcript):[A-Za-z0-9._@+:/=-]+"
    r")",
    re.IGNORECASE,
)
_SLASH_PRIVATE_PROJECTED_REF_RE = re.compile(
    r"(?<![A-Za-z0-9_.:@-])"
    r"/(?:memory|session|sessions|transcript|transcripts|child/transcripts|children/transcripts)"
    r"/[A-Za-z0-9._@+:/=-]+",
    re.IGNORECASE,
)
_SENSITIVE_PUBLIC_REF_FRAGMENTS = frozenset(
    (
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
)
_PLUGIN_PROJECTED_MAX_DEPTH = 5
_PLUGIN_PROJECTED_MAX_LIST_ITEMS = 50
_PLUGIN_PROJECTED_MAX_MAPPING_ITEMS = 50
_PLUGIN_PROJECTED_MAX_STRING = 4_000
_UNSAFE_PROJECTED_EVENT_KEY_TERMS = frozenset(
    (
        "auth",
        "authorization",
        "cookie",
        "credential",
        "header",
        "hidden",
        "memory",
        "password",
        "private",
        "prompt",
        "raw",
        "ref",
        "refs",
        "secret",
        "session",
        "token",
        "transcript",
        "toolargs",
        "toollogs",
    )
)
class InMemorySseWriter:
    def __init__(self) -> None:
        self._chunks: list[str] = []

    @property
    def body(self) -> str:
        return "".join(self._chunks)

    def start(self) -> None:
        self._chunks.append(":ok\n\n")

    def agent(self, event: dict[str, object]) -> None:
        safe_event = _sanitize_agent_event(event)
        if safe_event is None:
            return
        self._chunks.append(f"event: agent\ndata: {_json(safe_event)}\n\n")

    def projected_agent(
        self,
        projection_result: Mapping[str, object] | object,
    ) -> None:
        safe_event = _sanitize_projected_agent_event(projection_result)
        if safe_event is None:
            return
        self._chunks.append(f"event: agent\ndata: {_json(safe_event)}\n\n")

    def legacy_delta(self, content: str) -> None:
        payload = {
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "role": "assistant",
                        "content": _redact_unbounded_public_text(content),
                    },
                }
            ]
        }
        self._chunks.append(f"data: {_json(payload)}\n\n")

    def legacy_finish(self) -> None:
        payload = {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
        self._chunks.append(f"data: {_json(payload)}\n\n")
        self._chunks.append("data: [DONE]\n\n")


def _json(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


_DEFAULT_PUBLIC_EVENT_TYPES = frozenset(
    {
        "turn_start",
        "turn_phase",
        "turn_end",
        "text_delta",
        "response_clear",
        "control_replay_complete",
        "tool_start",
        "tool_progress",
        "tool_end",
        "context_end",
        "task_board",
        "rule_check",
        "reasoning_promoted",
    }
)

_DEFAULT_PUBLIC_STRING_FIELDS: dict[str, tuple[str, ...]] = {
    "turn_start": ("eventId", "turnId", "declaredRoute"),
    "turn_phase": (
        "eventId",
        "turnId",
        "phase",
        "status",
        "label",
        "message",
        "detail",
    ),
    "turn_end": ("eventId", "turnId", "status", "stopReason", "reason"),
    "text_delta": ("delta",),
    "response_clear": ("eventId", "turnId", "reason"),
    "control_replay_complete": ("eventId", "turnId", "status"),
    "tool_start": ("eventId", "id", "name", "input_preview"),
    "tool_progress": (
        "eventId",
        "id",
        "label",
        "status",
        "message",
        "detail",
    ),
    "tool_end": (
        "eventId",
        "id",
        "status",
        "output_preview",
        "error",
    ),
    "context_end": (),
    "rule_check": (
        "eventId",
        "turnId",
        "ruleId",
        "status",
        "message",
        "detail",
        "requiredAction",
    ),
    "reasoning_promoted": ("turnId", "severity", "reason", "contentDigest"),
}
_PUBLIC_RESPONSE_CLEAR_REASONS = frozenset(
    {
        "stream_fallback_model_switch",
        "stream_withholding_recovery",
    }
)

_DEFAULT_PUBLIC_NUMERIC_FIELDS: dict[str, tuple[str, ...]] = {
    "turn_phase": ("sequence", "createdAt"),
    "turn_end": ("durationMs", "createdAt"),
    "control_replay_complete": ("lastSeq", "createdAt"),
    "tool_progress": ("progress", "createdAt"),
    "tool_end": ("durationMs", "createdAt"),
    "context_end": ("createdAt",),
    "rule_check": ("createdAt",),
}
_PUBLIC_NUMERIC_BOUNDS_BY_KEY = {
    "addedLines": _PUBLIC_MAX_COUNT,
    "afterTokenCount": _TURN_USAGE_MAX_TOKENS,
    "attempt": _PUBLIC_MAX_ATTEMPT,
    "beforeTokenCount": _TURN_USAGE_MAX_TOKENS,
    "capturedAt": _PUBLIC_MAX_TIMESTAMP_MS,
    "confidence": _PUBLIC_MAX_RATIO,
    "contentLength": _TURN_USAGE_MAX_TOKENS,
    "count": _PUBLIC_MAX_COUNT,
    "createdAt": _PUBLIC_MAX_TIMESTAMP_MS,
    "durationMs": _PUBLIC_MAX_DURATION_MS,
    "elapsedMs": _PUBLIC_MAX_DURATION_MS,
    "errorCount": _PUBLIC_MAX_COLLECTION_SIZE,
    "expiresAt": _PUBLIC_MAX_TIMESTAMP_MS,
    "hunks": _PUBLIC_MAX_COUNT,
    "inspectedAt": _PUBLIC_MAX_TIMESTAMP_MS,
    "iter": _PUBLIC_MAX_ITER,
    "iteration": _PUBLIC_MAX_ITER,
    "lastEventAt": _PUBLIC_MAX_TIMESTAMP_MS,
    "lastSeq": _PUBLIC_MAX_COUNT,
    "maxAttempts": _PUBLIC_MAX_ATTEMPT,
    "progress": _PUBLIC_MAX_PROGRESS,
    "removedLines": _PUBLIC_MAX_COUNT,
    "score": _PUBLIC_MAX_RATIO,
    "seq": _PUBLIC_MAX_COUNT,
    "sequence": _PUBLIC_MAX_COUNT,
    "toolCount": _PUBLIC_MAX_COLLECTION_SIZE,
    "variantIndex": _PUBLIC_MAX_COLLECTION_SIZE,
    "winnerIndex": _PUBLIC_MAX_COLLECTION_SIZE,
}
_PUBLIC_INTEGER_NUMERIC_KEYS = frozenset(
    {
        "addedLines",
        "afterTokenCount",
        "attempt",
        "beforeTokenCount",
        "capturedAt",
        "contentLength",
        "count",
        "createdAt",
        "durationMs",
        "elapsedMs",
        "errorCount",
        "expiresAt",
        "hunks",
        "inspectedAt",
        "iter",
        "iteration",
        "lastEventAt",
        "lastSeq",
        "maxAttempts",
        "removedLines",
        "seq",
        "sequence",
        "toolCount",
        "variantIndex",
        "winnerIndex",
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
    "runtime_heartbeat_status": frozenset({"heartbeat_recorded"}),
    "runtime_stale_status": frozenset(
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
    "runtime_watchdog_status": frozenset(
        {
            "silent_healthy",
            "alert_output",
            "alert_failure",
            "alert_timeout",
            "blocked_recursive_scheduler",
        }
    ),
}
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
_RUNTIME_WATCHDOG_ALERT_KINDS = frozenset(
    {"none", "output", "failure", "timeout", "recursive_scheduler_denied"}
)


def _sanitize_agent_event(event: dict[str, object]) -> dict[str, object] | None:
    event_type = event.get("type")
    aliased_event_type = _PUBLIC_EVENT_TYPE_ALIASES.get(event_type)
    if aliased_event_type is not None:
        event = {**event, "type": aliased_event_type}
        event_type = aliased_event_type
    if event_type == "thinking_delta":
        # I-1: routed through the typed flag registry.
        from magi_agent.config.flags import flag_profile_bool  #  # noqa: PLC0415

        if not flag_profile_bool("MAGI_STREAM_THINKING"):
            return None
        value = _get_public_string_value(event, "delta")
        if value is None:
            value = _get_public_string_value(event, "text")
        if value is None:
            return {"type": "thinking_delta"}
        # _has_private_text_marker is applied first as belt-and-suspenders for
        # structured data; _redact_unbounded_public_text is prose-oriented and
        # handles token-level secrets in free-form thinking content.
        redacted = (
            "[redacted-private]"
            if _has_private_text_marker(value)
            else _redact_unbounded_public_text(value)
        )
        return {"type": "thinking_delta", "delta": redacted}
    if event_type == "browser_frame":
        return _sanitize_browser_frame_event(event)
    if event_type == "source_inspected":
        return _sanitize_source_inspected_event(event)
    if event_type == "document_draft":
        return _sanitize_document_draft_event(event)
    if event_type == "rule_check":
        return _sanitize_rule_check_event(event)
    if event_type == "recipe_selection":
        return _sanitize_recipe_selection_event(event)
    if event_type == "llm_progress":
        return _sanitize_llm_progress_event(event)
    if event_type == "patch_preview":
        return _sanitize_patch_preview_event(event)
    if event_type in {
        "child_started",
        "child_progress",
        "child_completed",
        "child_cancelled",
        "child_failed",
    }:
        return _sanitize_child_event(event)
    if event_type == "control_event":
        return _sanitize_control_event(event)
    if event_type == "control_request":
        return _sanitize_control_request_event(event)
    if event_type in _RUNTIME_STATUS_EVENT_TYPES:
        return _sanitize_runtime_status_event(event_type, event)
    if event_type == "runtime_trace":
        return _sanitize_runtime_trace_event(event)
    if event_type == "error":
        return _sanitize_error_event(event)
    if event_type == "model_fallback":
        return _sanitize_model_fallback_event(event)
    if event_type == "deterministic_fallback":
        return _sanitize_deterministic_fallback_event(event)
    if event_type == "compaction_boundary":
        return _sanitize_compaction_boundary_event(event)
    if event_type == "coding_final_projection":
        return _sanitize_coding_final_projection_event(event)
    if event_type == "research_artifact_delta":
        return _sanitize_research_artifact_delta_event(event)
    if event_type in _PUBLIC_UNION_EVENT_TYPES:
        return _sanitize_public_union_event(event_type, event)
    if event_type not in _DEFAULT_PUBLIC_EVENT_TYPES:
        return None

    return _sanitize_default_public_event(event_type, event)


def _sanitize_default_public_event(
    event_type: str,
    event: Mapping[str, object],
) -> dict[str, object]:
    if event_type == "turn_start":
        return _sanitize_turn_start_event(event)

    if event_type == "turn_phase":
        return _sanitize_turn_phase_event(event)

    if event_type == "turn_end":
        return _sanitize_turn_end_event(event)

    if event_type == "response_clear":
        sanitized = {"type": "response_clear"}
        reason = event.get("reason")
        if reason in _PUBLIC_RESPONSE_CLEAR_REASONS:
            sanitized["reason"] = reason
        return sanitized

    if event_type == "context_end":
        return {"type": "context_end"}

    if event_type == "task_board":
        return {
            "type": "task_board",
            "tasks": _sanitize_task_board_tasks(event.get("tasks")),
        }

    sanitized: dict[str, object] = {"type": event_type}

    for key in _DEFAULT_PUBLIC_STRING_FIELDS.get(event_type, ()):
        value = _get_public_string_value(event, key)
        if value is not None:
            if key == "eventId":
                event_id = _sanitize_public_event_id(value)
                if event_id is not None:
                    sanitized[key] = event_id
            elif event_type == "text_delta" and key == "delta":
                sanitized[key] = (
                    "[redacted-private]"
                    if _has_private_text_marker(value)
                    else _redact_unbounded_public_text(value)
                )
            else:
                sanitized[key] = _sanitize_public_text(value)

    for key in _DEFAULT_PUBLIC_NUMERIC_FIELDS.get(event_type, ()):
        value = _bounded_public_number(key, event.get(key))
        if value is not None:
            sanitized[key] = value

    transcript_refs = event.get("transcriptRefs")
    if isinstance(transcript_refs, list):
        safe_refs = [
            ref
            for item in transcript_refs
            if (ref := _sanitize_public_terminal_ref(item)) is not None
        ]
        if safe_refs:
            sanitized["transcriptRefs"] = safe_refs

    if event_type == "tool_start":
        input_digest = _sanitize_public_digest_ref(event.get("inputDigest"))
        if input_digest is not None:
            sanitized["inputDigest"] = input_digest
    elif event_type == "tool_end":
        receipt_ref = _sanitize_public_receipt_ref(event.get("receiptRef"))
        if receipt_ref is not None:
            sanitized["receiptRef"] = receipt_ref
        output_digest = _sanitize_public_digest_ref(event.get("outputDigest"))
        if output_digest is not None:
            sanitized["outputDigest"] = output_digest
    elif event_type == "tool_progress":
        receipt_ref = _sanitize_public_receipt_ref(event.get("receiptRef"))
        if receipt_ref is not None:
            sanitized["receiptRef"] = receipt_ref

    return sanitized


def _sanitize_turn_start_event(event: Mapping[str, object]) -> dict[str, object]:
    declared_route = event.get("declaredRoute")
    return {
        "type": "turn_start",
        "turnId": _public_turn_id(event),
        "declaredRoute": (
            declared_route
            if declared_route in {"direct", "subagent", "pipeline"}
            else "direct"
        ),
    }


def _sanitize_turn_phase_event(event: Mapping[str, object]) -> dict[str, object]:
    phase = event.get("phase")
    sanitized: dict[str, object] = {
        "type": "turn_phase",
        "turnId": _public_turn_id(event),
        "phase": phase if phase in _TURN_PHASES else "pending",
    }
    event_id = _sanitize_public_event_id(event.get("eventId"))
    if event_id is not None:
        sanitized["eventId"] = event_id
        for key in ("status", "label", "message", "detail"):
            value = _sanitize_optional_public_string(event.get(key))
            if value is not None:
                sanitized[key] = value
        for key in ("sequence", "createdAt"):
            value = _finite_number(event.get(key))
            if value is not None:
                sanitized[key] = value
    return sanitized


def _sanitize_turn_end_event(event: Mapping[str, object]) -> dict[str, object]:
    status = event.get("status")
    receipt_ref = event.get("receiptRef")
    safe_receipt_ref = (
        receipt_ref
        if isinstance(receipt_ref, str) and _RECEIPT_REF_RE.fullmatch(receipt_ref)
        else None
    )
    # Local-serve invariant: when the upstream projection layer attests
    # ``expectReceipt=False`` (the local OSS runner has no runtime-receipt
    # infrastructure), do NOT re-apply the strict-receipt downgrade. Without
    # this carve-out a fully successful local turn (text + tools) ends up as
    # ``aborted/missing_runtime_receipt`` in the persisted record even though
    # the model produced a complete reply. The dashboard then renders "no
    # final answer" on Kevin's 0.1.86 Gemini 3.5 Flash + Tesla 10-K shape.
    # The hosted path leaves the marker absent so the strict-receipt safety
    # net is preserved byte-identically. The marker itself is internal-only
    # and is never propagated to the public output (this function rebuilds
    # ``sanitized`` from scratch).
    receipt_not_expected = event.get("expectReceipt") is False
    if status == "committed" and safe_receipt_ref is not None:
        safe_status = "committed"
    elif status == "committed" and receipt_not_expected:
        safe_status = "committed"
    else:
        safe_status = "aborted"
    sanitized: dict[str, object] = {
        "type": "turn_end",
        "turnId": _public_turn_id(event),
        "status": safe_status,
    }
    if safe_status == "committed" and safe_receipt_ref is not None:
        sanitized["receiptRef"] = safe_receipt_ref
    missing_receipt = (
        status == "committed"
        and safe_receipt_ref is None
        and not receipt_not_expected
    )
    reason = (
        "missing_runtime_receipt"
        if missing_receipt
        else _sanitize_optional_public_string(event.get("reason"))
    )
    if reason is not None:
        sanitized["reason"] = reason
    usage = _sanitize_turn_usage(event.get("usage"))
    if safe_status == "committed" and usage is not None:
        sanitized["usage"] = usage
    return sanitized


def _sanitize_turn_usage(value: object) -> dict[str, int | float] | None:
    if not isinstance(value, Mapping):
        return None
    input_tokens = _bounded_usage_field(
        value,
        "inputTokens",
        maximum=_TURN_USAGE_MAX_TOKENS,
    )
    output_tokens = _bounded_usage_field(
        value,
        "outputTokens",
        maximum=_TURN_USAGE_MAX_TOKENS,
    )
    cost_usd = _bounded_usage_field(
        value,
        "costUsd",
        maximum=_TURN_USAGE_MAX_COST_USD,
    )
    if input_tokens is None or output_tokens is None or cost_usd is None:
        return None
    usage = {
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
        "costUsd": cost_usd,
    }
    if usage == {"inputTokens": 0, "outputTokens": 0, "costUsd": 0}:
        return None
    return usage


def _public_turn_id(event: Mapping[str, object]) -> str:
    turn_id = event.get("turnId")
    return _sanitize_public_text(turn_id if isinstance(turn_id, str) else "turn")


def _sanitize_document_draft_event(event: Mapping[str, object]) -> dict[str, object] | None:
    draft_id = _sanitize_optional_public_string(event.get("id"), limit=120)
    raw_preview = event.get("contentPreview")
    raw_preview_length = len(raw_preview) if isinstance(raw_preview, str) else None
    content_preview = _sanitize_document_draft_public_string(
        raw_preview,
        limit=_MAX_DOCUMENT_DRAFT_PREVIEW,
    )
    if draft_id is None or content_preview is None:
        return None

    sanitized: dict[str, object] = {
        "type": "document_draft",
        "id": draft_id,
    }
    raw_filename = event.get("filename")
    filename = _sanitize_document_draft_public_string(
        "[redacted-path]"
        if isinstance(raw_filename, str) and _SENSITIVE_QUERY_FRAGMENT_RE.search(raw_filename)
        else raw_filename,
        limit=500,
    )
    if filename is not None:
        sanitized["filename"] = filename
    format_value = event.get("format")
    sanitized["format"] = format_value if format_value in {"md", "txt", "html"} else "md"
    sanitized["contentPreview"] = content_preview
    content_length = _bounded_public_int("contentLength", event.get("contentLength"))
    sanitized["contentLength"] = (
        content_length if content_length is not None else len(content_preview)
    )
    truncated = event.get("truncated")
    sanitized["truncated"] = (
        truncated
        if isinstance(truncated, bool)
        else raw_preview_length is not None
        and (raw_preview_length > len(content_preview) or raw_preview_length > _MAX_DOCUMENT_DRAFT_PREVIEW)
    )
    return sanitized


def _sanitize_llm_progress_event(event: Mapping[str, object]) -> dict[str, object]:
    sanitized: dict[str, object] = {"type": "llm_progress"}
    for key in ("turnId", "label"):
        value = _sanitize_optional_public_string(event.get(key))
        if value is not None:
            sanitized[key] = value
    stage = event.get("stage")
    if isinstance(stage, str):
        sanitized["stage"] = (
            stage if stage in {"started", "waiting", "completed"} else "waiting"
        )
    detail = _sanitize_optional_public_string(event.get("detail"))
    if detail is not None:
        sanitized["detail"] = detail
    for key in ("iter", "elapsedMs"):
        value = _bounded_public_int(key, event.get(key))
        if value is not None:
            sanitized[key] = value
    return sanitized


def _sanitize_patch_preview_event(event: Mapping[str, object]) -> dict[str, object] | None:
    patch_preview = _sanitize_patch_preview_record(event)
    if patch_preview is None:
        return None
    sanitized: dict[str, object] = {"type": "patch_preview"}
    tool_use_id = _sanitize_optional_public_string(event.get("toolUseId"), limit=120)
    if tool_use_id is not None:
        sanitized["toolUseId"] = tool_use_id
    sanitized.update(patch_preview)
    return sanitized


def _sanitize_patch_preview_record(value: Mapping[str, object]) -> dict[str, object] | None:
    files = _sanitize_patch_preview_files(value.get("files"))
    changed_files = _sanitize_public_string_list(
        value.get("changedFiles"),
        limit=_MAX_PATCH_PREVIEW_FILES,
    )
    if not changed_files:
        changed_files = [
            file["path"]
            for file in files
            if isinstance(file.get("path"), str)
        ][: _MAX_PATCH_PREVIEW_FILES]
    if not changed_files and not files:
        return None
    return {
        "dryRun": value.get("dryRun") is True,
        "changedFiles": changed_files,
        "createdFiles": _sanitize_public_string_list(
            value.get("createdFiles"),
            limit=_MAX_PATCH_PREVIEW_FILES,
        ),
        "deletedFiles": _sanitize_public_string_list(
            value.get("deletedFiles"),
            limit=_MAX_PATCH_PREVIEW_FILES,
        ),
        "files": files,
    }


def _sanitize_recipe_selection_event(
    event: Mapping[str, object],
) -> dict[str, object] | None:
    status = event.get("status")
    if event.get("admissionBlocked") is True:
        status = "blocked"
    elif status not in _RECIPE_SELECTION_STATUSES:
        status = "blocked" if event.get("admissionBlocked") is True else "applied"
    selection_source = event.get("selectionSource")
    sanitized: dict[str, object] = {
        "type": "recipe_selection",
        "status": status,
        "selectionSource": (
            selection_source
            if selection_source in _RECIPE_SELECTION_SOURCES
            else "automatic"
        ),
    }
    for source_key, target_key in (
        ("requestedRecipeRefs", "requestedRecipeRefs"),
        ("appliedRecipeRefs", "appliedRecipeRefs"),
        ("omittedRecipeRefs", "omittedRecipeRefs"),
    ):
        refs = _sanitize_recipe_refs(event.get(source_key))
        if refs:
            sanitized[target_key] = refs
    omission_reasons = _sanitize_recipe_omission_reasons(
        event.get("omissionReasons"),
        sanitized,
    )
    if omission_reasons:
        sanitized["omissionReasons"] = omission_reasons
    policy_digest = _sanitize_recipe_digest(event.get("policySnapshotDigest"))
    if policy_digest is not None:
        sanitized["policySnapshotDigest"] = policy_digest
    return sanitized


def _sanitize_recipe_refs(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list | tuple):
        return []
    refs: list[dict[str, str]] = []
    for item in value[:100]:
        if not isinstance(item, Mapping):
            continue
        recipe_id = _sanitize_recipe_id(item.get("recipeId"))
        if recipe_id is None:
            continue
        ref = {"recipeId": recipe_id}
        version = _sanitize_recipe_version(item.get("version"))
        if version is not None:
            ref["version"] = version
        digest = _sanitize_recipe_digest(item.get("digest"))
        if digest is not None:
            ref["digest"] = digest
        refs.append(ref)
    return refs


def _sanitize_recipe_omission_reasons(
    value: object,
    sanitized_event: Mapping[str, object],
) -> dict[str, list[str]]:
    if not isinstance(value, Mapping):
        return {}
    allowed_recipe_ids: set[str] = set()
    for key in ("requestedRecipeRefs", "appliedRecipeRefs", "omittedRecipeRefs"):
        refs = sanitized_event.get(key)
        if not isinstance(refs, list):
            continue
        for ref in refs:
            if isinstance(ref, Mapping) and isinstance(ref.get("recipeId"), str):
                allowed_recipe_ids.add(ref["recipeId"])
    reasons: dict[str, list[str]] = {}
    for recipe_id, raw_reasons in sorted(value.items(), key=lambda item: str(item[0])):
        safe_recipe_id = _sanitize_recipe_id(recipe_id)
        if safe_recipe_id is None or safe_recipe_id not in allowed_recipe_ids:
            continue
        safe_reasons = [
            reason
            for reason in (
                _sanitize_recipe_omission_reason(item)
                for item in _recipe_reason_items(raw_reasons)
            )
            if reason is not None
        ]
        if safe_reasons:
            reasons[safe_recipe_id] = safe_reasons[:20]
    return reasons


def _recipe_reason_items(value: object) -> list[object]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        return [value]
    return []


def _sanitize_recipe_id(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not _RECIPE_ID_RE.fullmatch(candidate):
        return None
    return _sanitize_public_text(candidate)


def _sanitize_recipe_version(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not _RECIPE_VERSION_RE.fullmatch(candidate) or _unsafe_recipe_ref_text(candidate):
        return None
    return candidate


def _sanitize_recipe_omission_reason(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if candidate not in _RECIPE_OMISSION_REASONS:
        return None
    return candidate


def _sanitize_recipe_digest(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not _RECIPE_DIGEST_RE.fullmatch(candidate):
        return None
    return candidate


def _unsafe_recipe_ref_text(value: str) -> bool:
    normalized = value.strip().lower()
    return _UNSAFE_RECIPE_REF_PREFIX_RE.search(normalized) is not None or any(
        fragment in normalized for fragment in _UNSAFE_RECIPE_REF_TEXT
    )


def _sanitize_patch_preview_files(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    files: list[dict[str, object]] = []
    for item in value[:_MAX_PATCH_PREVIEW_FILES]:
        if not isinstance(item, Mapping):
            continue
        path = _sanitize_optional_public_string(item.get("path"), limit=500)
        if path is None:
            continue
        operation = item.get("operation")
        file: dict[str, object] = {
            "path": path,
            "operation": operation if operation in _PATCH_OPERATIONS else "update",
            "hunks": _bounded_public_int("hunks", item.get("hunks")) or 0,
            "addedLines": _bounded_public_int("addedLines", item.get("addedLines"))
            or 0,
            "removedLines": _bounded_public_int(
                "removedLines",
                item.get("removedLines"),
            )
            or 0,
        }
        for key in ("oldSha256", "newSha256"):
            value_for_key = _sanitize_optional_public_string(item.get(key), limit=96)
            if value_for_key is not None:
                file[key] = value_for_key
        files.append(file)
    return files


def _sanitize_task_board_tasks(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    tasks: list[dict[str, object]] = []
    for item in value[:_MAX_TASK_BOARD_TASKS]:
        if not isinstance(item, Mapping):
            continue
        task_id = _sanitize_optional_public_string(item.get("id"), limit=96)
        title = _sanitize_optional_public_string(item.get("title"))
        if task_id is None or title is None:
            continue
        description_value = item.get("description")
        description = (
            _sanitize_public_text(description_value)
            if isinstance(description_value, str) and description_value.strip()
            else ""
        )
        task: dict[str, object] = {
            "id": task_id,
            "title": title,
            "description": description,
        }
        status = item.get("status")
        task["status"] = status if status in _TASK_BOARD_STATUSES else "pending"
        parallel_group = _sanitize_optional_public_string(
            item.get("parallelGroup"),
            limit=96,
        )
        if parallel_group is not None:
            task["parallelGroup"] = parallel_group
        depends_on = _sanitize_public_string_list(item.get("dependsOn"), limit=50)
        if depends_on:
            task["dependsOn"] = depends_on
        tasks.append(task)
    return tasks


def _get_public_string_value(event: Mapping[str, object], key: str) -> str | None:
    value = event.get(key)
    if isinstance(value, str):
        return value
    alias = _PUBLIC_EVENT_ALIASES.get(key)
    if alias is None:
        return None
    alias_value = event.get(alias)
    if isinstance(alias_value, str):
        return alias_value
    return None


def _sanitize_optional_public_string(
    value: object,
    *,
    limit: int | None = None,
) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return _sanitize_public_text(value, limit=limit)


def _sanitize_public_route_string(
    value: object,
    *,
    limit: int,
) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    redacted = _redact_relative_private_refs(value)
    redacted = _redact_sensitive_route_paths(redacted)
    return _sanitize_public_text(redacted, limit=limit)


def _sanitize_document_draft_public_string(
    value: object,
    *,
    limit: int,
) -> str | None:
    return _sanitize_public_route_string(value, limit=limit)


def _redact_sensitive_route_paths(value: str) -> str:
    redacted = _ROUTE_PATH_TOKEN_RE.sub(_redact_sensitive_route_token, value)
    redacted = _SENSITIVE_ROUTE_PATH_RE.sub(r"\1[redacted-path]", redacted)
    return _QUERY_FRAGMENT_TOKEN_RE.sub(_redact_sensitive_query_fragment, redacted)


def _redact_sensitive_route_token(match: re.Match[str]) -> str:
    token = match.group(0)
    return "[redacted-path]" if _has_sensitive_route_path(token) else token


def _redact_sensitive_query_fragment(match: re.Match[str]) -> str:
    fragment = match.group(0)
    normalized = _decode_percent_encoded_route_token(_normalize_route_separators(fragment))
    if _SENSITIVE_QUERY_FRAGMENT_RE.search(fragment) or _SENSITIVE_QUERY_FRAGMENT_RE.search(
        normalized
    ):
        return "[redacted-query]"
    return fragment


def _normalize_route_separators(value: str) -> str:
    normalized = _ENCODED_PATH_SEPARATOR_RE.sub("/", value)
    normalized = _ENCODED_QUERY_SEPARATOR_RE.sub("?", normalized)
    normalized = _ENCODED_HASH_SEPARATOR_RE.sub("#", normalized)
    return _ENCODED_SEGMENT_SEPARATOR_RE.sub("-", normalized)


def _decode_percent_encoded_route_token(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        codepoint = int(match.group(1), 16)
        if 32 <= codepoint <= 126:
            return chr(codepoint)
        return match.group(0)

    return _PERCENT_ENCODED_BYTE_RE.sub(replace, value)


def _has_sensitive_url_path(value: str) -> bool:
    return bool(
        _SENSITIVE_URL_PATH_RE.search(value)
        or _SENSITIVE_URL_PATH_RE.search(_normalize_route_separators(value))
    )


def _has_sensitive_route_path(value: str) -> bool:
    normalized = _normalize_route_separators(value)
    return bool(
        _SENSITIVE_ROUTE_PATH_RE.search(value)
        or _SENSITIVE_ROUTE_PATH_RE.search(normalized)
        or _SENSITIVE_ROUTE_SEGMENT_RE.search(normalized)
    )


def _sanitize_public_evidence_ref(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = value.strip()
    if _PUBLIC_EVIDENCE_REF_RE.fullmatch(candidate) is None:
        return None
    return candidate if _is_safe_public_ref(candidate) else None


def _sanitize_public_receipt_ref(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = value.strip()
    if _RECEIPT_REF_RE.fullmatch(candidate) is None:
        return None
    return candidate if _is_safe_public_ref(candidate) else None


def _sanitize_public_digest_ref(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = value.strip()
    if _RECIPE_DIGEST_RE.fullmatch(candidate) is None:
        return None
    return candidate if _is_safe_public_ref(candidate) else None


def _sanitize_public_event_id(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = value.strip()
    if _is_safe_public_ref(candidate):
        return candidate
    return _hashed_public_event_id(candidate)


def _sanitize_public_reason_code(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = value.strip()
    if _PUBLIC_EVIDENCE_REF_RE.fullmatch(candidate) is not None:
        return candidate if _is_safe_public_ref(candidate) else None
    if _PUBLIC_REASON_CODE_RE.fullmatch(candidate) is None:
        return None
    return _sanitize_public_text(candidate, limit=120) if _is_safe_public_ref(candidate) else None


def _hashed_public_event_id(value: str) -> str:
    return f"event:{hashlib.sha1(value.encode('utf-8')).hexdigest()[:16]}"


def _sanitize_public_terminal_ref(value: object) -> str | None:
    return (
        _sanitize_public_receipt_ref(value)
        or _sanitize_public_digest_ref(value)
        or _sanitize_public_evidence_ref(value)
    )


def _is_safe_public_ref(value: str) -> bool:
    if not value.strip() or len(value) > 180:
        return False
    if _PRIVATE_PROJECTED_REF_RE.search(value):
        return False
    if not all(char.isalnum() or char in "._:-" for char in value):
        return False
    normalized_value = _normalize_public_ref_body(value)
    if any(fragment in normalized_value for fragment in _SENSITIVE_PUBLIC_REF_FRAGMENTS):
        return False
    return not any(
        fragment in normalized_value for fragment in _PRIVATE_TEXT_MARKER_FRAGMENTS
    )


def _normalize_public_ref_body(value: str) -> str:
    body = value.split(":", 1)[1] if ":" in value else value
    return re.sub(r"[^a-z0-9]", "", body.lower())


def _non_negative_int(value: object) -> int | None:
    number = _finite_number(value)
    if number is None:
        return None
    return max(0, int(number))


def _finite_number(value: object) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        finite_value = float(value)
    except OverflowError:
        return None
    if not math.isfinite(finite_value):
        return None
    return value


def _bounded_number(value: object, *, maximum: int | float) -> int | float | None:
    number = _finite_number(value)
    if number is None or number < 0 or number > maximum:
        return None
    return number


def _bounded_public_number(key: str, value: object) -> int | float | None:
    maximum = _PUBLIC_NUMERIC_BOUNDS_BY_KEY.get(key, _PUBLIC_MAX_COUNT)
    number = _bounded_number(value, maximum=maximum)
    if number is None:
        return None
    if key in _PUBLIC_INTEGER_NUMERIC_KEYS:
        return int(number)
    return number


def _bounded_public_int(key: str, value: object) -> int | None:
    number = _bounded_public_number(key, value)
    if number is None:
        return None
    return int(number)


def _bounded_usage_field(
    usage: Mapping[str, object],
    key: str,
    *,
    maximum: int | float,
) -> int | float | None:
    if key not in usage:
        return 0
    return _bounded_number(usage.get(key), maximum=maximum)


def _finite_int(value: object) -> int | None:
    number = _finite_number(value)
    if number is None:
        return None
    return int(number)


_PUBLIC_EVENT_ALIASES = {
    "input_preview": "inputPreview",
    "output_preview": "outputPreview",
}

_PUBLIC_EVENT_TYPE_ALIASES = {
    "citation_gate": "rule_check",
    "inject": "injection_queued",
    "interrupt": "turn_interrupted",
}

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

_PUBLIC_UNION_EVENT_TYPES = frozenset(
    {
        "retry",
        "spawn_worktree_conflict",
        "structured_output",
        "turn_interrupted",
        "spawn_started",
        "spawn_result",
        "background_task",
        "child_tool_request",
        "child_permission_decision",
        "child_llm_start",
        "child_llm_end",
        "child_tool_batch_start",
        "child_tool_batch_end",
        "child_abort",
        "tournament_result",
        "ask_user",
        "plan_ready",
        "plan_lifecycle",
        "session_stop",
        "context_activated",
        "compaction_impossible",
        "injection_queued",
        "injection_drained",
        "heartbeat",
    }
)

def _sanitize_projected_agent_event(
    projection_result: Mapping[str, object] | object,
) -> dict[str, object] | None:
    if isinstance(projection_result, Mapping):
        return None
    from magi_agent.missions.events import (
        MissionPublicEventProjectionResult,
        sanitize_projected_agent_event,
    )

    if type(projection_result) is not MissionPublicEventProjectionResult:
        return None
    safe_event = sanitize_projected_agent_event(projection_result)
    if safe_event is not None:
        return _sanitize_projected_public_agent_event(safe_event)
    return None


def _sanitize_projected_public_agent_event(
    event: Mapping[str, object],
) -> dict[str, object] | None:
    event_type = event.get("type")
    if not isinstance(event_type, str):
        return None
    if _PLUGIN_PROJECTED_EVENT_TYPE_RE.fullmatch(event_type) is None:
        return None

    sanitized: dict[str, object] = {"type": event_type}
    for key, value in tuple(event.items())[:_PLUGIN_PROJECTED_MAX_MAPPING_ITEMS]:
        if key == "type":
            continue
        if not _is_safe_plugin_projected_key(key):
            continue
        safe_value = _sanitize_plugin_projected_value(value, depth=0)
        if safe_value is not None:
            sanitized[key] = safe_value
    return sanitized


def _is_safe_plugin_projected_key(key: object) -> bool:
    if not isinstance(key, str):
        return False
    if _PLUGIN_PROJECTED_EVENT_KEY_RE.fullmatch(key) is None:
        return False
    normalized = re.sub(r"[^a-z0-9]", "", key.lower())
    if normalized in {"path", "filepath", "workspacepath", "filesystempath"}:
        return False
    return not any(term in normalized for term in _UNSAFE_PROJECTED_EVENT_KEY_TERMS)


def _sanitize_plugin_projected_value(value: object, *, depth: int) -> object | None:
    if depth >= _PLUGIN_PROJECTED_MAX_DEPTH:
        return None
    if isinstance(value, str):
        return _PRIVATE_PROJECTED_REF_RE.sub(
            "[redacted-ref]",
            _sanitize_public_text(value, limit=_PLUGIN_PROJECTED_MAX_STRING),
        )
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float) and math.isfinite(value):
        return value
    if isinstance(value, list | tuple):
        safe_items: list[object] = []
        for item in value[:_PLUGIN_PROJECTED_MAX_LIST_ITEMS]:
            safe_item = _sanitize_plugin_projected_value(item, depth=depth + 1)
            if safe_item is not None:
                safe_items.append(safe_item)
        return safe_items
    if isinstance(value, Mapping):
        safe_mapping: dict[str, object] = {}
        for key, nested_value in tuple(value.items())[:_PLUGIN_PROJECTED_MAX_MAPPING_ITEMS]:
            if not _is_safe_plugin_projected_key(key):
                continue
            safe_value = _sanitize_plugin_projected_value(
                nested_value,
                depth=depth + 1,
            )
            if safe_value is not None:
                safe_mapping[key] = safe_value
        return safe_mapping
    return None


def _sanitize_public_union_event(
    event_type: str,
    event: Mapping[str, object],
) -> dict[str, object] | None:
    if event_type in {"child_tool_request", "child_permission_decision"}:
        return _sanitize_control_event_payload(event)
    if event_type == "retry":
        return _sanitize_string_number_bool_event(
            event_type,
            event,
            string_fields=("reason", "toolUseId", "toolName"),
            numeric_fields=("retryNo",),
        )
    if event_type == "spawn_worktree_conflict":
        sanitized = _sanitize_string_number_bool_event(
            event_type,
            event,
            string_fields=(
                "action",
                "spawnDir",
                "conflictKind",
                "mergeStrategy",
                "adoptedCommit",
                "summary",
            ),
        )
        for key in ("conflictedFiles", "changedFiles", "suggestedActions"):
            values = _sanitize_public_string_list(event.get(key))
            if values:
                sanitized[key] = values
        return sanitized
    if event_type == "structured_output":
        return _sanitize_string_number_bool_event(
            event_type,
            event,
            string_fields=("status", "schemaName", "reason"),
        )
    if event_type == "turn_interrupted":
        return _sanitize_string_number_bool_event(
            event_type,
            event,
            string_fields=("turnId", "source"),
            bool_fields=("handoffRequested",),
        )
    if event_type == "spawn_started":
        return _sanitize_string_number_bool_event(
            event_type,
            event,
            string_fields=("taskId", "persona", "deliver", "detail"),
        )
    if event_type == "spawn_result":
        return _sanitize_string_number_bool_event(
            event_type,
            event,
            string_fields=("taskId", "status", "errorMessage"),
            numeric_fields=("toolCallCount",),
        )
    if event_type == "background_task":
        return _sanitize_string_number_bool_event(
            event_type,
            event,
            string_fields=("taskId", "persona", "status", "detail"),
        )
    if event_type in {
        "child_llm_start",
        "child_llm_end",
        "child_tool_batch_start",
        "child_tool_batch_end",
        "child_abort",
    }:
        return _sanitize_child_telemetry_union_event(event_type, event)
    if event_type == "tournament_result":
        sanitized = _sanitize_string_number_bool_event(
            event_type,
            event,
            numeric_fields=("winnerIndex",),
        )
        variants = _sanitize_tournament_variants(event.get("variants"))
        if variants:
            sanitized["variants"] = variants
        return sanitized
    if event_type == "ask_user":
        sanitized = _sanitize_string_number_bool_event(
            event_type,
            event,
            string_fields=("questionId", "question"),
            bool_fields=("allowFreeText",),
        )
        choices = _sanitize_ask_user_choices(event.get("choices"))
        if choices:
            sanitized["choices"] = choices
        return sanitized
    if event_type == "plan_ready":
        return _sanitize_string_number_bool_event(
            event_type,
            event,
            string_fields=("planId", "requestId", "state", "plan"),
        )
    if event_type == "plan_lifecycle":
        return _sanitize_string_number_bool_event(
            event_type,
            event,
            string_fields=("state", "previousMode", "planId", "requestId"),
        )
    if event_type == "session_stop":
        return _sanitize_string_number_bool_event(
            event_type,
            event,
            string_fields=("taskId", "reason"),
            numeric_fields=("round", "lastScore"),
        )
    if event_type == "context_activated":
        return _sanitize_string_number_bool_event(
            event_type,
            event,
            string_fields=("contextId", "title"),
        )
    if event_type == "compaction_impossible":
        return _sanitize_string_number_bool_event(
            event_type,
            event,
            string_fields=("model",),
            numeric_fields=(
                "contextWindow",
                "effectiveReserveTokens",
                "effectiveBudgetTokens",
                "minViableBudgetTokens",
            ),
        )
    if event_type == "injection_queued":
        return _sanitize_string_number_bool_event(
            event_type,
            event,
            string_fields=("injectionId",),
            numeric_fields=("queuedCount",),
        )
    if event_type == "injection_drained":
        return _sanitize_string_number_bool_event(
            event_type,
            event,
            numeric_fields=("count", "iteration"),
        )
    if event_type == "heartbeat":
        return _sanitize_string_number_bool_event(
            event_type,
            event,
            string_fields=("eventId", "turnId"),
            numeric_fields=("iter", "elapsedMs", "lastEventAt"),
        )
    return None


def _sanitize_string_number_bool_event(
    event_type: str,
    event: Mapping[str, object],
    *,
    string_fields: tuple[str, ...] = (),
    numeric_fields: tuple[str, ...] = (),
    bool_fields: tuple[str, ...] = (),
) -> dict[str, object]:
    sanitized: dict[str, object] = {"type": event_type}
    for key in string_fields:
        value = event.get(key)
        if isinstance(value, str):
            if key == "eventId":
                event_id = _sanitize_public_event_id(value)
                if event_id is not None:
                    sanitized[key] = event_id
            else:
                sanitized[key] = _sanitize_public_text(value)
    for key in numeric_fields:
        value = _bounded_public_number(key, event.get(key))
        if value is not None:
            sanitized[key] = value
    for key in bool_fields:
        value = event.get(key)
        if isinstance(value, bool):
            sanitized[key] = value
    return sanitized


def _sanitize_public_string_list(value: object, *, limit: int = 100) -> list[str]:
    if not isinstance(value, list):
        return []
    return [
        _sanitize_public_text(item)
        for item in value[:limit]
        if isinstance(item, str) and item.strip()
    ]


def _sanitize_research_artifact_delta_event(
    event: Mapping[str, object],
) -> dict[str, object] | None:
    sanitized: dict[str, object] = {"type": "research_artifact_delta"}
    has_payload = False
    claims = _sanitize_research_claims(event.get("claims"))
    if claims:
        sanitized["claims"] = claims
        has_payload = True
    links = _sanitize_claim_source_links(event.get("claimSourceLinks"))
    if links:
        sanitized["claimSourceLinks"] = links
        has_payload = True
    contradictions = _sanitize_research_contradictions(event.get("contradictions"))
    if contradictions:
        sanitized["contradictions"] = contradictions
        has_payload = True
    return sanitized if has_payload else None


def _sanitize_research_claims(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    claims: list[dict[str, object]] = []
    for item in value[:24]:
        if not isinstance(item, Mapping):
            continue
        claim_id = item.get("claimId")
        text = item.get("text")
        if not isinstance(claim_id, str) or not isinstance(text, str):
            continue
        claim: dict[str, object] = {
            "claimId": _sanitize_public_text(claim_id),
            "text": _sanitize_public_text(text),
        }
        for key in ("claimType", "supportStatus"):
            field_value = item.get(key)
            if isinstance(field_value, str):
                claim[key] = _sanitize_public_text(field_value)
        source_ids = _sanitize_public_string_list(item.get("sourceIds"), limit=20)
        if source_ids:
            claim["sourceIds"] = source_ids
        confidence = _bounded_public_number("confidence", item.get("confidence"))
        if confidence is not None:
            claim["confidence"] = max(0, min(1, confidence))
        reasoning = _sanitize_research_reasoning(item.get("reasoning"))
        if reasoning:
            claim["reasoning"] = reasoning
        claims.append(claim)
    return claims


def _sanitize_research_reasoning(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    reasoning: dict[str, object] = {}
    premise_source_ids = _sanitize_public_string_list(value.get("premiseSourceIds"), limit=20)
    if premise_source_ids:
        reasoning["premiseSourceIds"] = premise_source_ids
    inference = value.get("inference")
    if isinstance(inference, str):
        reasoning["inference"] = _sanitize_public_text(inference)
    assumptions = _sanitize_public_string_list(value.get("assumptions"), limit=12)
    if assumptions:
        reasoning["assumptions"] = assumptions
    status = value.get("status")
    if isinstance(status, str):
        reasoning["status"] = _sanitize_public_text(status)
    return reasoning or None


def _sanitize_claim_source_links(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    links: list[dict[str, object]] = []
    for item in value[:100]:
        if not isinstance(item, Mapping):
            continue
        claim_id = item.get("claimId")
        source_id = item.get("sourceId")
        if not isinstance(claim_id, str) or not isinstance(source_id, str):
            continue
        link: dict[str, object] = {
            "claimId": _sanitize_public_text(claim_id),
            "sourceId": _sanitize_public_text(source_id),
        }
        support = item.get("support")
        if isinstance(support, str):
            link["support"] = _sanitize_public_text(support)
        links.append(link)
    return links


def _sanitize_research_contradictions(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    contradictions: list[dict[str, object]] = []
    for item in value[:24]:
        if not isinstance(item, Mapping):
            continue
        contradiction_id = item.get("contradictionId")
        if not isinstance(contradiction_id, str):
            continue
        contradiction: dict[str, object] = {
            "contradictionId": _sanitize_public_text(contradiction_id),
        }
        claim_ids = _sanitize_public_string_list(item.get("claimIds"), limit=20)
        source_ids = _sanitize_public_string_list(item.get("sourceIds"), limit=20)
        if claim_ids:
            contradiction["claimIds"] = claim_ids
        if source_ids:
            contradiction["sourceIds"] = source_ids
        for key in ("resolution", "status"):
            value_for_key = item.get(key)
            if isinstance(value_for_key, str):
                contradiction[key] = _sanitize_public_text(value_for_key)
        contradictions.append(contradiction)
    return contradictions


def _sanitize_child_telemetry_union_event(
    event_type: str,
    event: Mapping[str, object],
) -> dict[str, object]:
    sanitized = _sanitize_string_number_bool_event(
        event_type,
        event,
        string_fields=("taskId", "parentTurnId", "childTurnId", "traceId"),
    )
    if event_type in {"child_llm_start", "child_llm_end"}:
        for key in ("model", "stopReason"):
            value = event.get(key)
            if isinstance(value, str):
                sanitized[key] = _sanitize_public_text(value)
        for key in ("iter", "durationMs"):
            value = _bounded_public_number(key, event.get(key))
            if value is not None:
                sanitized[key] = value
    elif event_type == "child_tool_batch_start":
        for key in ("iter", "toolCount"):
            value = _bounded_public_number(key, event.get(key))
            if value is not None:
                sanitized[key] = value
        tool_names = _sanitize_public_string_list(event.get("toolNames"))
        if tool_names:
            sanitized["toolNames"] = tool_names
    elif event_type == "child_tool_batch_end":
        for key in ("status", "errorName", "errorMessage"):
            value = event.get(key)
            if isinstance(value, str):
                sanitized[key] = _sanitize_public_text(value)
        for key in ("iter", "toolCount", "errorCount", "durationMs"):
            value = _bounded_public_number(key, event.get(key))
            if value is not None:
                sanitized[key] = value
    elif event_type == "child_abort":
        source = event.get("source")
        if isinstance(source, str):
            sanitized["source"] = _sanitize_public_text(source)
    return sanitized


def _sanitize_tournament_variants(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    variants: list[dict[str, object]] = []
    for item in value[:20]:
        if not isinstance(item, Mapping):
            continue
        variant: dict[str, object] = {}
        for key in ("variantIndex", "score"):
            item_value = _bounded_public_number(key, item.get(key))
            if item_value is not None:
                variant[key] = item_value
        if variant:
            variants.append(variant)
    return variants


def _sanitize_ask_user_choices(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    choices: list[dict[str, object]] = []
    for item in value[:12]:
        if not isinstance(item, Mapping):
            continue
        choice_id = item.get("id")
        label = item.get("label")
        if not isinstance(choice_id, str) or not isinstance(label, str):
            continue
        choice: dict[str, object] = {
            "id": _sanitize_public_text(choice_id),
            "label": _sanitize_public_text(label),
        }
        description = item.get("description")
        if isinstance(description, str):
            choice["description"] = _sanitize_public_text(description)
        choices.append(choice)
    return choices


def _sanitize_browser_frame_event(event: Mapping[str, object]) -> dict[str, object] | None:
    image_base64 = _sanitize_browser_frame_image(event.get("imageBase64"))
    if image_base64 is None:
        return None

    sanitized: dict[str, object] = {
        "type": "browser_frame",
        "action": _browser_frame_text(
            event.get("action"),
            fallback="browser",
            limit=_MAX_BROWSER_FRAME_ACTION,
        ),
        "imageBase64": image_base64,
        "contentType": (
            event.get("contentType")
            if event.get("contentType") in _BROWSER_FRAME_CONTENT_TYPES
            else "image/png"
        ),
    }
    captured_at = _browser_frame_number(event.get("capturedAt"))
    if captured_at is None and "capturedAt" not in event:
        captured_at = int(time.time() * 1000)
    if captured_at is not None:
        sanitized["capturedAt"] = captured_at
    url = event.get("url")
    if isinstance(url, str):
        public_url = _sanitize_browser_frame_url(url)
        if public_url is not None:
            sanitized["url"] = public_url
    return sanitized


def _sanitize_browser_frame_url(value: str) -> str | None:
    trimmed = value.strip()
    if not trimmed:
        return None

    try:
        parsed = urlsplit(trimmed)
    except ValueError:
        parsed = urlsplit("")
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        origin = _public_url_origin(parsed)
        if origin is None:
            return None
        if _has_sensitive_url_path(parsed.path):
            return origin
        public_url = _sanitize_public_text(f"{origin}{parsed.path}", limit=500)
        return None if "[redacted" in public_url else public_url
    if parsed.scheme:
        return None

    if _has_sensitive_url_path(trimmed) or _has_sensitive_route_path(trimmed):
        return None
    if trimmed.startswith("/"):
        public_path = re.split(r"[?#]", trimmed, maxsplit=1)[0]
        return _sanitize_public_text(public_path, limit=500) if public_path else None
    public_text = re.split(r"[?#]", trimmed, maxsplit=1)[0]
    if public_text != trimmed:
        return _sanitize_public_text(public_text, limit=500) if public_text else None
    return _sanitize_public_text(trimmed, limit=500)


def _public_url_origin(parsed: SplitResult) -> str | None:
    hostname = parsed.hostname
    if not hostname:
        return None
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    try:
        port = f":{parsed.port}" if parsed.port is not None else ""
    except ValueError:
        return None
    return f"{parsed.scheme}://{hostname}{port}"


def _browser_frame_text(value: object, *, fallback: str, limit: int) -> str:
    if not isinstance(value, str):
        return fallback
    trimmed = value.strip()
    if not trimmed:
        return fallback
    redacted = _redact_public_text(trimmed)
    if len(redacted) > limit:
        return f"{redacted[:limit - 3]}..."
    return redacted


def _browser_frame_number(value: object) -> int | float | None:
    return _bounded_public_number("capturedAt", value)


def _sanitize_browser_frame_image(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    if len(value) > _MAX_BROWSER_FRAME_BASE64:
        return None
    if _BROWSER_FRAME_BASE64_RE.fullmatch(value) is None:
        return None
    return value


def _sanitize_source_inspected_event(event: Mapping[str, object]) -> dict[str, object] | None:
    source = event.get("source")
    if not isinstance(source, Mapping):
        return None
    source_id = _sanitize_optional_public_string(source.get("sourceId"), limit=120)
    uri = _sanitize_public_route_string(source.get("uri"), limit=4_000)
    if source_id is None or uri is None:
        return None
    kind = source.get("kind")
    trust_tier = source.get("trustTier")
    safe_source: dict[str, object] = {
        "sourceId": source_id,
        "kind": (
            kind
            if kind
            in {
                "web_search",
                "web_fetch",
                "browser",
                "kb",
                "file",
                "external_repo",
                "external_doc",
                "subagent_result",
            }
            else "web_fetch"
        ),
        "uri": uri,
    }
    for key in (
        "turnId",
        "toolName",
        "toolUseId",
        "title",
        "contentHash",
        "contentType",
        "trustTier",
    ):
        value = source.get(key)
        if isinstance(value, str):
            safe_source[key] = _sanitize_public_text(value)
    evidence_ref = _sanitize_public_evidence_ref(
        source.get("contentHash", source.get("evidenceRef")),
    )
    if evidence_ref is None:
        return _blocked_public_projection_event(
            event,
            detail="source_inspected omitted: missing public evidence receipt",
            turn_id=source.get("turnId"),
        )
    safe_source["contentHash"] = evidence_ref
    safe_source["trustTier"] = (
        trust_tier
        if trust_tier in {"primary", "official", "secondary", "unknown"}
        else "unknown"
    )
    inspected_at = _bounded_public_number("inspectedAt", source.get("inspectedAt"))
    safe_source["inspectedAt"] = inspected_at if inspected_at is not None else 0
    snippets = source.get("snippets")
    if isinstance(snippets, list):
        safe_source["snippets"] = [
            _sanitize_public_text(item)
            for item in snippets[:5]
            if isinstance(item, str) and item.strip()
        ]
    sanitized: dict[str, object] = {"type": "source_inspected", "source": safe_source}
    event_id = _sanitize_public_event_id(event.get("eventId"))
    if event_id is not None:
        sanitized["eventId"] = event_id
    return sanitized


def _sanitize_child_event(event: Mapping[str, object]) -> dict[str, object] | None:
    event_type = str(event.get("type"))
    task_id = _sanitize_optional_public_string(event.get("taskId"), limit=120)
    if task_id is None:
        return None
    child_receipt_ref = _sanitize_public_receipt_ref(
        event.get("childReceiptRef"),
    )
    if child_receipt_ref is None:
        return _blocked_public_projection_event(
            event,
            detail=f"{event_type} omitted: missing public child receipt",
            turn_id=event.get("turnId", event.get("parentTurnId")),
        )
    sanitized: dict[str, object] = {"type": event_type, "taskId": task_id}
    event_id = _sanitize_public_event_id(event.get("eventId"))
    if event_id is not None:
        sanitized["eventId"] = event_id
    sanitized["childReceiptRef"] = child_receipt_ref
    if event_type == "child_started":
        parent_turn_id = _sanitize_optional_public_string(event.get("parentTurnId"), limit=120)
        if parent_turn_id is not None:
            sanitized["parentTurnId"] = parent_turn_id
        detail = event.get("detail")
        if isinstance(detail, str):
            sanitized["detail"] = _sanitize_public_text(detail)
        # The AGENTS chip relies on these fields for a meaningful label /
        # model badge / task hint.  Without passing them through this
        # sanitizer the streaming chat route silently drops them and the
        # chip falls back to the index-based "Halley" placeholder.
        agent_name = _sanitize_optional_public_string(event.get("agentName"), limit=64)
        if agent_name is not None:
            sanitized["agentName"] = agent_name
        model = _sanitize_optional_public_string(event.get("model"), limit=96)
        if model is not None:
            sanitized["model"] = model
        task_title = _sanitize_optional_public_string(event.get("taskTitle"), limit=64)
        if task_title is not None:
            sanitized["taskTitle"] = task_title
    elif event_type == "child_progress":
        detail = _sanitize_optional_public_string(event.get("detail"))
        if detail is None:
            return None
        sanitized["detail"] = detail
    elif event_type == "child_completed":
        # Forward the truncated + redacted child summary so the chip detail
        # can hint at what the agent CAME BACK WITH instead of resetting to
        # a placeholder on completion.
        summary = _sanitize_optional_public_string(event.get("summary"))
        if summary is not None:
            sanitized["summary"] = summary
    elif event_type == "child_cancelled":
        reason = _sanitize_optional_public_string(event.get("reason"))
        if reason is None:
            return None
        sanitized["reason"] = reason
        summary = _sanitize_optional_public_string(event.get("summary"))
        if summary is not None:
            sanitized["summary"] = summary
    if event_type == "child_failed":
        error_message = event.get("errorMessage", event.get("error"))
        if not isinstance(error_message, str):
            return None
        sanitized["errorMessage"] = _sanitize_public_text(error_message)
        summary = _sanitize_optional_public_string(event.get("summary"))
        if summary is not None:
            sanitized["summary"] = summary
    return sanitized


def _sanitize_control_event(event: Mapping[str, object]) -> dict[str, object]:
    sanitized: dict[str, object] = {"type": "control_event"}
    seq = _bounded_public_int("seq", event.get("seq"))
    if seq is not None:
        sanitized["seq"] = seq
    raw_control_event = event.get("event")
    if isinstance(raw_control_event, Mapping):
        safe_payload = _sanitize_control_event_payload(raw_control_event)
        if safe_payload is None:
            return None
        sanitized["event"] = safe_payload
    if "event" not in sanitized:
        return None
    return sanitized


def _sanitize_control_request_event(event: Mapping[str, object]) -> dict[str, object]:
    """Sanitize a ``control_request`` event for public SSE emission.

    This event type carries a tool-permission approval request that the browser
    must render as a modal dialog.  The ``request_id`` and ``tool_name`` fields
    must survive sanitization (they are needed for correlation and rendering).
    ``arguments`` is redacted via the same :func:`~magi_agent.transport.tool_preview.sanitize_tool_preview`
    path used for ``tool_start`` events; ``reason`` passes through
    :func:`_sanitize_public_text` — a reason containing a private-text-marker
    is included as ``"[redacted-private]"``; ``None``/empty reason is omitted.
    """
    sanitized: dict[str, object] = {"type": "control_request"}

    # request_id: sanitized for safety but must normally survive for correlation.
    request_id = event.get("request_id")
    safe_request_id = _sanitize_optional_public_string(request_id, limit=120)
    if safe_request_id is not None:
        sanitized["request_id"] = safe_request_id

    # tool_name: sanitized public text — needed to render the modal title.
    tool_name = event.get("tool_name")
    if isinstance(tool_name, str) and tool_name.strip():
        sanitized["tool_name"] = _sanitize_public_text(tool_name)

    # reason: sanitize; a private-text-marker reason becomes "[redacted-private]"
    # (included); None/empty reason is omitted entirely.
    reason = event.get("reason")
    if isinstance(reason, str) and reason.strip():
        safe_reason = _sanitize_optional_public_string(reason)
        if safe_reason is not None:
            sanitized["reason"] = safe_reason

    # arguments: redact using the tool-preview sanitizer (credentials / tokens)
    # plus the production-path redactor (filesystem paths).  This matches the
    # combined redaction applied to tool_start/tool_end visible text.
    # arguments is always emitted as a string (or absent), never a dict.
    arguments = event.get("arguments")
    if isinstance(arguments, Mapping):
        try:
            raw_preview = json.dumps(
                {str(k): v for k, v in arguments.items()},
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
                default=str,
            )
        except (ValueError, TypeError):
            raw_preview = "[redacted-args]"
        redacted_preview = _tool_preview.sanitize_tool_preview(raw_preview)
        redacted_preview = _PRODUCTION_PATH_RE.sub("[redacted-path]", redacted_preview)
        sanitized["arguments"] = redacted_preview
    elif arguments is not None:
        sanitized["arguments"] = "{}"

    return sanitized


def _sanitize_control_event_payload(event: Mapping[str, object]) -> dict[str, object] | None:
    event_type = event.get("type")
    if event_type == "control_request_created":
        request = event.get("request")
        safe_request: dict[str, object] = {}
        if isinstance(request, Mapping):
            for key in (
                "requestId",
                "kind",
                "state",
                "sessionKey",
                "source",
                "prompt",
            ):
                value = request.get(key)
                if isinstance(value, str):
                    safe_request[key] = _sanitize_public_text(value)
            for key in ("createdAt", "expiresAt"):
                value = _bounded_public_number(key, request.get(key))
                if value is not None:
                    safe_request[key] = value
            proposed_input = _sanitize_control_proposed_input(
                safe_request.get("kind"),
                request.get("proposedInput"),
            )
            if proposed_input is not None:
                safe_request["proposedInput"] = proposed_input
        return {
            "type": "control_request_created",
            "request": safe_request,
        }
    if event_type == "control_request_resolved":
        safe_event: dict[str, object] = {"type": "control_request_resolved"}
        for key in ("requestId", "decision", "feedback"):
            value = event.get(key)
            if isinstance(value, str):
                safe_event[key] = _sanitize_public_text(value)
        return safe_event
    if event_type in {"control_request_cancelled", "control_request_timed_out"}:
        request_id = event.get("requestId")
        if isinstance(request_id, str):
            safe_event: dict[str, object] = {
                "type": event_type,
                "requestId": _sanitize_public_text(request_id),
            }
            if event_type == "control_request_cancelled":
                reason = _sanitize_optional_public_string(event.get("reason"))
                if reason is not None:
                    safe_event["reason"] = reason
            return safe_event
    if event_type == "task_board_snapshot":
        safe_event: dict[str, object] = {"type": "task_board_snapshot"}
        turn_id = _sanitize_optional_public_string(event.get("turnId"), limit=120)
        if turn_id is not None:
            safe_event["turnId"] = turn_id
        return safe_event
    if event_type == "verification":
        safe_event = {"type": "verification"}
        for key in ("status", "reason"):
            value = event.get(key)
            if isinstance(value, str):
                safe_event[key] = _sanitize_public_text(value)
        return safe_event
    if event_type == "child_progress":
        task_id = _sanitize_optional_public_string(event.get("taskId"), limit=120)
        detail = _sanitize_optional_public_string(event.get("detail"))
        if task_id is None or detail is None:
            return None
        return {
            "type": "child_progress",
            "taskId": task_id,
            "detail": detail,
        }
    if event_type == "child_tool_request":
        task_id = _sanitize_optional_public_string(event.get("taskId"), limit=120)
        request_id = _sanitize_optional_public_string(event.get("requestId"), limit=120)
        tool_name = _sanitize_optional_public_string(event.get("toolName"), limit=120)
        if task_id is None or request_id is None or tool_name is None:
            return None
        return {
            "type": "child_tool_request",
            "taskId": task_id,
            "requestId": request_id,
            "toolName": tool_name,
        }
    if event_type == "child_permission_decision":
        task_id = _sanitize_optional_public_string(event.get("taskId"), limit=120)
        decision = event.get("decision")
        if task_id is None or decision not in {"allow", "deny", "ask"}:
            return None
        safe_event = {
            "type": "child_permission_decision",
            "taskId": task_id,
            "decision": decision,
        }
        reason = _sanitize_optional_public_string(event.get("reason"))
        if reason is not None:
            safe_event["reason"] = reason
        return safe_event
    if event_type == "child_cancelled":
        task_id = _sanitize_optional_public_string(event.get("taskId"), limit=120)
        reason = _sanitize_optional_public_string(event.get("reason"))
        if task_id is None or reason is None:
            return None
        return {
            "type": "child_cancelled",
            "taskId": task_id,
            "reason": reason,
        }
    return None


def _sanitize_control_proposed_input(
    kind: object,
    value: object,
) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    if kind == "tool_permission":
        return _sanitize_patch_apply_permission_input(value)
    if kind == "user_question":
        return {
            "choices": _sanitize_ask_user_choices(value.get("choices")),
            "allowFreeText": value.get("allowFreeText") is True,
        }
    if kind == "plan_approval":
        plan_id = _sanitize_optional_public_string(value.get("planId"), limit=120)
        plan = _sanitize_optional_public_string(value.get("plan"), limit=16_000)
        if plan_id is None or plan is None:
            return None
        return {"planId": plan_id, "plan": plan}
    return None


def _sanitize_patch_apply_permission_input(value: Mapping[str, object]) -> dict[str, object] | None:
    if value.get("toolName") != "PatchApply":
        return None
    safe: dict[str, object] = {"toolName": "PatchApply"}
    patch_preview_value = value.get("patchPreview")
    if isinstance(patch_preview_value, Mapping):
        patch_preview = _sanitize_patch_preview_record(patch_preview_value)
        if patch_preview is not None:
            safe["patchPreview"] = patch_preview
    preview_error = _sanitize_optional_public_string(value.get("previewError"), limit=120)
    if preview_error is not None:
        safe["previewError"] = preview_error
    return safe


def _sanitize_runtime_trace_event(event: Mapping[str, object]) -> dict[str, object]:
    sanitized: dict[str, object] = {"type": "runtime_trace"}
    event_id = _sanitize_public_event_id(event.get("eventId"))
    if event_id is not None:
        sanitized["eventId"] = event_id
    turn_id = event.get("turnId")
    if isinstance(turn_id, str):
        sanitized["turnId"] = _sanitize_public_text(turn_id)
    phase = event.get("phase")
    sanitized["phase"] = (
        phase
        if phase
        in {"verifier_blocked", "retry_scheduled", "retry_aborted", "terminal_abort"}
        else "verifier_blocked"
    )
    severity = event.get("severity")
    sanitized["severity"] = (
        severity if severity in {"info", "warning", "error"} else "info"
    )
    for key in ("title", "detail", "requiredAction"):
        value = event.get(key)
        if isinstance(value, str):
            sanitized[key] = _sanitize_public_text(value)
    for key in ("reasonCode", "ruleId"):
        code = _sanitize_public_reason_code(event.get(key))
        if code is not None:
            sanitized[key] = code
    for key in ("attempt", "maxAttempts"):
        value = _bounded_public_int(key, event.get(key))
        if value is not None:
            sanitized[key] = value
    retryable = event.get("retryable")
    if isinstance(retryable, bool):
        sanitized["retryable"] = retryable
    return sanitized


def _sanitize_runtime_status_event(
    event_type: str,
    event: Mapping[str, object],
) -> dict[str, object]:
    sanitized: dict[str, object] = {
        "type": event_type,
        "publicSafe": True,
        **{key: False for key in _RUNTIME_STATUS_FALSE_FIELDS},
    }
    event_id = _sanitize_public_event_id(event.get("eventId"))
    if event_id is not None:
        sanitized["eventId"] = event_id
    turn_id = _sanitize_optional_public_string(event.get("turnId"), limit=120)
    if turn_id is not None:
        sanitized["turnId"] = turn_id

    status = _sanitize_runtime_status_value(event_type, event.get("status"))
    if status is not None:
        sanitized["status"] = status
    decision = _sanitize_runtime_resume_decision(event.get("decision"))
    if decision is not None:
        sanitized["decision"] = decision
    alert_kind = _sanitize_runtime_watchdog_alert_kind(event.get("alertKind"))
    if alert_kind is not None:
        sanitized["alertKind"] = alert_kind

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
        digest = _sanitize_runtime_digest_ref(event.get(key))
        if digest is not None:
            sanitized[key] = digest

    for key in ("reasonCodeDigests", "pendingApprovalDigests"):
        digests = _sanitize_runtime_digest_list(event.get(key))
        if digests:
            sanitized[key] = digests

    for key in ("emittedAt", "lastActivityAt", "checkedAt", "decidedAt"):
        value = _sanitize_optional_public_string(event.get(key), limit=80)
        if value is not None:
            sanitized[key] = value

    for key in ("sequence", "exitCode", "durationMs"):
        value = _bounded_public_number(key, event.get(key))
        if value is not None:
            sanitized[key] = value

    for key in ("alertRequired", "timedOut", "recursiveSchedulerDenied"):
        value = event.get(key)
        if isinstance(value, bool):
            sanitized[key] = value
    return sanitized


def _sanitize_runtime_status_value(event_type: str, value: object) -> str | None:
    if not isinstance(value, str):
        return None
    allowed = _RUNTIME_STATUS_BY_TYPE.get(event_type)
    return value if allowed is not None and value in allowed else None


def _sanitize_runtime_resume_decision(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return value if value in _RUNTIME_RESUME_DECISIONS else None


def _sanitize_runtime_watchdog_alert_kind(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return value if value in _RUNTIME_WATCHDOG_ALERT_KINDS else None


def _sanitize_runtime_digest_ref(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = value.strip()
    if (
        _RECIPE_DIGEST_RE.fullmatch(candidate) is None
        and _RUNTIME_TYPED_DIGEST_RE.fullmatch(candidate) is None
    ):
        return None
    return candidate if _is_safe_public_ref(candidate) else None


def _sanitize_runtime_digest_list(value: object) -> list[str]:
    if not isinstance(value, list | tuple):
        return []
    digests: list[str] = []
    for item in value[:50]:
        digest = _sanitize_runtime_digest_ref(item)
        if digest is not None and digest not in digests:
            digests.append(digest)
    return digests


def _blocked_public_projection_event(
    event: Mapping[str, object],
    *,
    detail: str,
    turn_id: object | None = None,
) -> dict[str, object]:
    event_id = _sanitize_public_event_id(event.get("eventId"))
    if event_id is None:
        event_type = event.get("type")
        fallback = event_type if isinstance(event_type, str) else "event"
        event_id = _sanitize_public_event_id(fallback) or _hashed_public_event_id(fallback)
    safe_turn_id = _sanitize_optional_public_string(turn_id or event.get("turnId"), limit=120)
    return {
        "type": "runtime_trace",
        "eventId": f"{event_id}:blocked",
        "turnId": safe_turn_id or "turn",
        "phase": "verifier_blocked",
        "severity": "warning",
        "title": "Public event omitted",
        "detail": detail,
        "reasonCode": "public_projection_missing_receipt",
        "requiredAction": "retain_typescript_fallback",
    }


def _sanitize_rule_check_event(event: Mapping[str, object]) -> dict[str, object]:
    rule_id = _sanitize_public_reason_code(event.get("ruleId"))
    if rule_id is None:
        rule_id = "rule"
    verdict = event.get("verdict")
    safe_verdict = verdict if verdict in {"pending", "ok", "violation"} else "pending"
    detail = _sanitize_optional_public_string(event.get("detail"))
    evidence_ref = _sanitize_public_receipt_ref(
        event.get("evidenceRef")
    ) or _sanitize_public_digest_ref(event.get("evidenceRef"))
    if (
        safe_verdict != "pending"
        and (evidence_ref is None or not rule_check_event_has_authority(event))
    ):
        return _blocked_public_projection_event(
            event,
            detail="rule_check omitted: missing public evidence receipt",
        )
    sanitized: dict[str, object] = {
        "type": "rule_check",
        "ruleId": rule_id,
        "verdict": safe_verdict,
    }
    if detail is not None:
        sanitized["detail"] = detail
    event_id = _sanitize_public_event_id(event.get("eventId"))
    if event_id is not None:
        sanitized["eventId"] = event_id
    turn_id = _sanitize_optional_public_string(event.get("turnId"), limit=120)
    if turn_id is not None:
        sanitized["turnId"] = turn_id
    if evidence_ref is not None:
        sanitized["evidenceRef"] = evidence_ref
    checked_at = _finite_number(event.get("checkedAt"))
    sanitized["checkedAt"] = checked_at if checked_at is not None else 0
    return sanitized

def _sanitize_error_event(event: Mapping[str, object]) -> dict[str, object]:
    sanitized: dict[str, object] = {"type": "error"}
    for key in ("code", "message"):
        value = event.get(key)
        if isinstance(value, str):
            sanitized[key] = _sanitize_public_text(value)
    return sanitized


def _sanitize_model_fallback_event(event: Mapping[str, object]) -> dict[str, object]:
    sanitized: dict[str, object] = {"type": "model_fallback"}
    for key in ("turnId", "fromModel", "toModel", "reason"):
        value = event.get(key)
        if isinstance(value, str):
            sanitized[key] = _sanitize_public_text(value)
    return sanitized


def _sanitize_deterministic_fallback_event(
    event: Mapping[str, object],
) -> dict[str, object] | None:
    from_authority = event.get("fromAuthority")
    to_authority = event.get("toAuthority")
    reason_code = _sanitize_public_reason_code(event.get("reasonCode"))
    if (
        from_authority not in {"python", "typescript", "none"}
        or to_authority not in {"typescript", "none"}
        or reason_code is None
    ):
        return None
    sanitized: dict[str, object] = {
        "type": "deterministic_fallback",
        "fromAuthority": from_authority,
        "toAuthority": to_authority,
        "reasonCode": reason_code,
    }
    request_digest = event.get("requestDigest")
    if isinstance(request_digest, str) and _RECIPE_DIGEST_RE.fullmatch(request_digest):
        sanitized["requestDigest"] = request_digest
    return sanitized


def _sanitize_compaction_boundary_event(event: Mapping[str, object]) -> dict[str, object]:
    sanitized: dict[str, object] = {"type": "compaction_boundary"}
    for key in ("eventId", "turnId", "boundaryId", "summaryHash"):
        value = event.get(key)
        if isinstance(value, str):
            if key == "eventId":
                event_id = _sanitize_public_event_id(value)
                if event_id is not None:
                    sanitized[key] = event_id
            else:
                sanitized[key] = _sanitize_public_text(value)
    for key in ("beforeTokenCount", "afterTokenCount", "createdAt"):
        value = _bounded_public_number(key, event.get(key))
        if value is not None:
            sanitized[key] = value
    return sanitized


def _sanitize_public_text(value: str, *, limit: int | None = None) -> str:
    if _has_private_text_marker(value):
        return "[redacted-private]"
    if limit is not None:
        redacted = _redact_public_text(value)
        redacted = _redact_relative_private_refs(redacted)
        redacted = _redact_sensitive_route_paths(redacted)
        if len(redacted) > limit:
            return f"{redacted[:limit - 3]}..."
        return redacted
    redacted = _tool_preview.sanitize_tool_preview(value)
    redacted = redact_composio_text(redacted)
    redacted = _SESSION_ASSIGNMENT_RE.sub(r"\1[redacted]", redacted)
    redacted = _redact_relative_private_refs(redacted)
    redacted = _PRODUCTION_PATH_RE.sub("[redacted-path]", redacted)
    return _redact_sensitive_route_paths(redacted)


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


def _redact_unbounded_public_text(value: str) -> str:
    redacted = _redact_public_text(value)
    redacted = _redact_relative_private_refs(redacted)
    return _redact_sensitive_route_paths(redacted)


def _redact_public_text(value: str) -> str:
    redacted = _tool_preview._BEARER_TOKEN_RE.sub(r"\1[redacted]", value)
    redacted = _tool_preview._AUTHORIZATION_HEADER_RE.sub(r"\1[redacted]", redacted)
    redacted = _tool_preview._COOKIE_HEADER_RE.sub(r"\1[redacted]", redacted)
    redacted = _GITHUB_FINE_GRAINED_PAT_RE.sub("[redacted]", redacted)
    redacted = _tool_preview._GITHUB_TOKEN_RE.sub("[redacted]", redacted)
    redacted = _tool_preview._OPENAI_TOKEN_RE.sub("[redacted]", redacted)
    redacted = _tool_preview._STRIPE_TOKEN_RE.sub("[redacted]", redacted)
    redacted = _tool_preview._DOUBLE_QUOTED_PUBLIC_CREDENTIAL_KEY_VALUE_RE.sub(
        r'\1"[redacted]"',
        redacted,
    )
    redacted = _tool_preview._SINGLE_QUOTED_PUBLIC_CREDENTIAL_KEY_VALUE_RE.sub(
        r"\1'[redacted]'",
        redacted,
    )
    redacted = _tool_preview._UNQUOTED_PUBLIC_CREDENTIAL_KEY_VALUE_RE.sub(
        r"\1[redacted]",
        redacted,
    )
    redacted = _tool_preview._DOUBLE_QUOTED_KEY_VALUE_SECRET_RE.sub(
        r'\1"[redacted]"',
        redacted,
    )
    redacted = _tool_preview._SINGLE_QUOTED_KEY_VALUE_SECRET_RE.sub(
        r"\1'[redacted]'",
        redacted,
    )
    redacted = _tool_preview._UNQUOTED_KEY_VALUE_SECRET_RE.sub(
        r"\1[redacted]",
        redacted,
    )
    redacted = _SESSION_ASSIGNMENT_RE.sub(r"\1[redacted]", redacted)
    redacted = _PRODUCTION_PATH_RE.sub("[redacted-path]", redacted)
    return redact_composio_text(redacted)


def _redact_relative_private_refs(value: str) -> str:
    redacted = _PRIVATE_PROJECTED_REF_RE.sub("[redacted-ref]", value)
    return _SLASH_PRIVATE_PROJECTED_REF_RE.sub("[redacted-ref]", redacted)


def _sanitize_coding_final_projection_event(
    event: Mapping[str, object],
) -> dict[str, object] | None:
    """Sanitize a coding_final_projection event for public SSE emission.

    Strips any raw file paths, private content, or auth tokens.
    Only allows digest-safe, evidence-backed fields through.
    """
    sanitized: dict[str, object] = {"type": "coding_final_projection"}

    event_id = _sanitize_public_event_id(event.get("eventId"))
    if event_id is not None:
        sanitized["eventId"] = event_id

    status = event.get("status")
    if isinstance(status, str) and status in {"complete", "incomplete"}:
        sanitized["status"] = status

    for count_key in (
        "changedFileCount",
        "testRunCount",
        "evidenceGapCount",
    ):
        count_val = event.get(count_key)
        if isinstance(count_val, int) and 0 <= count_val <= _PUBLIC_MAX_COLLECTION_SIZE:
            sanitized[count_key] = count_val

    rollback_verified = event.get("rollbackVerified")
    if isinstance(rollback_verified, bool):
        sanitized["rollbackVerified"] = rollback_verified

    next_action = event.get("nextAction")
    if isinstance(next_action, str):
        clean_action = _sanitize_public_text(next_action, limit=500)
        if _has_private_text_marker(clean_action):
            sanitized["nextAction"] = "[redacted-private]"
        else:
            sanitized["nextAction"] = clean_action

    default_off = event.get("defaultOff")
    if default_off is True:
        sanitized["defaultOff"] = True

    prod_mutation = event.get("productionWorkspaceMutationAllowed")
    if prod_mutation is False:
        sanitized["productionWorkspaceMutationAllowed"] = False

    # Sanitize evidence refs lists — only allow valid digest/ref patterns
    changed_files = event.get("changedFiles")
    if isinstance(changed_files, list):
        safe_files: list[dict[str, object]] = []
        for item in changed_files[:_MAX_PATCH_PREVIEW_FILES]:
            if not isinstance(item, Mapping):
                continue
            safe_item: dict[str, object] = {}
            digest = item.get("fileDigest")
            if isinstance(digest, str) and _RECIPE_DIGEST_RE.fullmatch(digest):
                safe_item["fileDigest"] = digest
            op = item.get("operation")
            if isinstance(op, str) and op in {"created", "modified", "deleted"}:
                safe_item["operation"] = op
            diff_ref = _sanitize_public_evidence_ref(item.get("diffEvidenceRef"))
            if diff_ref is not None:
                safe_item["diffEvidenceRef"] = diff_ref
            if safe_item:
                safe_files.append(safe_item)
        sanitized["changedFiles"] = safe_files

    tests_run = event.get("testsRun")
    if isinstance(tests_run, list):
        safe_tests: list[dict[str, object]] = []
        for item in tests_run[:_MAX_PATCH_PREVIEW_FILES]:
            if not isinstance(item, Mapping):
                continue
            safe_item_t: dict[str, object] = {}
            suite_ref = _sanitize_public_evidence_ref(item.get("testSuiteRef"))
            if suite_ref is not None:
                safe_item_t["testSuiteRef"] = suite_ref
            t_status = item.get("status")
            if isinstance(t_status, str) and t_status in {"pass", "failed"}:
                safe_item_t["status"] = t_status
            ev_ref = _sanitize_public_evidence_ref(item.get("evidenceRef"))
            if ev_ref is not None:
                safe_item_t["evidenceRef"] = ev_ref
            if safe_item_t:
                safe_tests.append(safe_item_t)
        sanitized["testsRun"] = safe_tests

    evidence_gaps = event.get("evidenceGaps")
    if isinstance(evidence_gaps, list):
        safe_gaps: list[dict[str, object]] = []
        for item in evidence_gaps[:_MAX_PATCH_PREVIEW_FILES]:
            if not isinstance(item, Mapping):
                continue
            safe_gap: dict[str, object] = {}
            gap_type = item.get("gapType")
            if isinstance(gap_type, str) and len(gap_type) <= 120:
                safe_gap["gapType"] = gap_type
            desc = item.get("description")
            if isinstance(desc, str):
                clean_desc = _sanitize_public_text(desc, limit=500)
                safe_gap["description"] = clean_desc
            if safe_gap:
                safe_gaps.append(safe_gap)
        sanitized["evidenceGaps"] = safe_gaps

    return sanitized
