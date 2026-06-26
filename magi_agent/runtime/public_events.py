from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
import math
import re
from typing import Literal


PublicEvent = dict[str, object]
PublicMetadata = Mapping[str, object]
PublicTask = Mapping[str, object]

TurnPhase = Literal[
    "pending",
    "planning",
    "executing",
    "verifying",
    "committing",
    "committed",
    "aborted",
]
RuntimeTracePhase = Literal[
    "verifier_blocked",
    "retry_scheduled",
    "retry_aborted",
    "terminal_abort",
]
RuntimeTraceSeverity = Literal["info", "warning", "error"]
RuleVerdict = Literal["pending", "ok", "violation"]
SourceKind = Literal[
    "web_search",
    "web_fetch",
    "browser",
    "kb",
    "file",
    "external_repo",
    "external_doc",
    "subagent_result",
]
TrustTier = Literal["primary", "official", "secondary", "unknown"]
TaskStatus = Literal["pending", "in_progress", "completed", "cancelled"]

_TEXT_LIMIT = 240
_DETAIL_LIMIT = 400
_ID_LIMIT = 120
_URI_LIMIT = 1_000
_SOURCE_SNIPPET_LIMIT = 5
_TASK_LIMIT = 25
_TASK_DEPENDENCY_LIMIT = 25
_REF_LIMIT = 25
_TOOL_INPUT_PREVIEW_VALUE_LIMIT = 160
_TOOL_INPUT_PREVIEW_KEYS = (
    "query",
    "q",
    "url",
    "path",
    "target",
    "title",
    "pattern",
    "glob",
    "file",
    "filename",
    "workspacePath",
    "workspace_path",
)
_RULE_CHECK_AUTHORITY_FIELD = "_openmagiRuleCheckAuthority"
_RULE_CHECK_AUTHORITY_TOKEN = object()
_DIGEST_RE = re.compile(r"^(?:receipt:)?sha256:[a-fA-F0-9]{64}$")
_RESULT_REF_RE = re.compile(r"^result:sha256:[a-fA-F0-9]{64}$")
_PUBLIC_REF_RE = re.compile(r"^ref:[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}$")
_PRODUCTION_PATH_RE = re.compile(
    r"(?:/data/bots|/workspace|/var/lib/kubelet|/Users|/home|/private|/mnt|/root)"
    r"(?:/[^\s\"',}]+)*",
    re.IGNORECASE,
)
_PRIVATE_REF_RE = re.compile(
    r"(?<![A-Za-z0-9_.:@/-])"
    r"(?:"
    r"(?:memory|session|sessions|transcript|transcripts|child/transcripts|children/transcripts)"
    r"/[A-Za-z0-9._@+:/=-]+"
    r"|(?:memory|session|transcript):[A-Za-z0-9._@+:/=-]+"
    r")",
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
_ROUTE_PATH_TOKEN_RE = re.compile(
    r"(?:https?://[^\s\"'<>)]*|[A-Za-z0-9._/-]*(?:/|%(?:25)*(?:2f|5c)|[?#]|%(?:25)*(?:3f|23))[^\s\"'<>)]*)",
    re.IGNORECASE,
)
_ENCODED_PATH_SEPARATOR_RE = re.compile(r"%(?:25)*(?:2f|5c)", re.IGNORECASE)
_ENCODED_QUERY_SEPARATOR_RE = re.compile(r"%(?:25)*3f", re.IGNORECASE)
_ENCODED_HASH_SEPARATOR_RE = re.compile(r"%(?:25)*23", re.IGNORECASE)
_ENCODED_SEGMENT_SEPARATOR_RE = re.compile(r"%(?:25)*(?:2d|2e|5f)", re.IGNORECASE)
_PERCENT_ENCODED_BYTE_RE = re.compile(r"%(?:25)*([0-9a-fA-F]{2})", re.IGNORECASE)
_BEARER_RE = re.compile(r"\b(Bearer\s+)[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
_BASIC_RE = re.compile(r"\b(Basic\s+)[A-Za-z0-9+/=:_-]+", re.IGNORECASE)
_AUTH_HEADER_RE = re.compile(r"\b(Authorization\s*:\s*)(?:Bearer|Basic)?\s*[^\r\n,}]+", re.IGNORECASE)
_COOKIE_HEADER_RE = re.compile(r"\b((?:Set-)?Cookie\s*:\s*)[^\r\n]+", re.IGNORECASE)
_GITHUB_TOKEN_RE = re.compile(r"\bgh[pousr]_[A-Za-z0-9_]+\b")
_GITHUB_PAT_RE = re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b")
_OPENAI_TOKEN_RE = re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]+\b")
_STRIPE_TOKEN_RE = re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9_]+\b")
_SLACK_TOKEN_RE = re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b")
_AWS_ACCESS_KEY_RE = re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")
_JWT_RE = re.compile(
    r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"
)
_TELEGRAM_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:bot)?\d{6,12}:[A-Za-z0-9_-]{20,}\b",
    re.IGNORECASE,
)
_KEY_VALUE_SECRET_RE = re.compile(
    r"(\b(?:[A-Z0-9_]*(?:API[_-]?KEY|SERVICE[_-]?ROLE[_-]?KEY|"
    r"PRIVATE[_-]?KEY|TOKEN|SECRET|PASSWORD)[A-Z0-9_]*|api[_-]?key|"
    r"token|secret|password|session)[\"'\s:=]+)"
    r"(?:\"[^\"]*\"|'[^']*'|[^\"'\s,}]+)",
    re.IGNORECASE,
)
_PRIVATE_TEXT_RE = re.compile(
    r"\b(?:"
    r"hidden\s+reasoning|"
    r"chain\s+of\s+thought|"
    r"raw\s+(?:(?:[a-z0-9_-]+\s+){0,3}(?:payload|response|output|"
    r"result|body|transcript|event)|prompt|tool\s+(?:args?|arguments?|inputs?|"
    r"outputs?|results?|responses?|logs?))|"
    r"(?:tool|tool\s+call|tool\s+use|function|function\s+call)\s+"
    r"(?:args?|arguments?|inputs?|outputs?|results?|responses?|logs?)"
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
    "rawadkevent",
    "rawprompt",
    "rawtoolargs",
    "rawtoolarguments",
    "rawtoolinput",
    "rawtooloutput",
    "rawtoolresult",
    "rawtoolresponse",
    "rawtoollog",
    "rawtoollogs",
    "toolargs",
    "toolarguments",
    "toolinput",
    "tooloutput",
    "toolresult",
    "toolresponse",
    "toollog",
    "toollogs",
    "toolcallargs",
    "toolcallarguments",
    "toolcallinput",
    "toolcalloutput",
    "toolcallresult",
    "toolcallresponse",
    "toolcalllog",
    "toolcalllogs",
    "tooluseargs",
    "toolusearguments",
    "tooluseinput",
    "tooluseoutput",
    "tooluseresult",
    "tooluseresponse",
    "tooluselog",
    "tooluselogs",
    "functionargs",
    "functionarguments",
    "functioninput",
    "functionoutput",
    "functionresult",
    "functionresponse",
    "functionlog",
    "functionlogs",
    "functioncallargs",
    "functioncallarguments",
    "functioncallinput",
    "functioncalloutput",
    "functioncallresult",
    "functioncallresponse",
    "functioncalllog",
    "functioncalllogs",
)


def turn_phase_event(
    *,
    turn_id: str,
    phase: TurnPhase,
    status: str | None = None,
    label: str | None = None,
    message: str | None = None,
    detail: str | None = None,
    sequence: int | float | None = None,
    created_at: int | float | None = None,
    event_family: str = "turn_lifecycle_public_stream",
) -> PublicEvent:
    _require_event_family(event_family, {"turn_lifecycle_public_stream"})
    return {
        "type": "turn_phase",
        "turnId": _public_text(turn_id, limit=_ID_LIMIT),
        "phase": phase,
    }


def heartbeat_event(
    *,
    turn_id: str,
    iter: int | float | None = None,
    elapsed_ms: int | float | None = None,
    last_event_at: int | float | None = None,
    event_family: str = "heartbeat_progress",
) -> PublicEvent:
    _require_event_family(event_family, {"heartbeat_progress"})
    event: PublicEvent = {"type": "heartbeat", "turnId": _public_text(turn_id, limit=_ID_LIMIT)}
    _put_number(event, "iter", iter)
    _put_number(event, "elapsedMs", elapsed_ms)
    _put_number(event, "lastEventAt", last_event_at)
    return event


def runtime_trace_event(
    *,
    turn_id: str | None = None,
    phase: RuntimeTracePhase = "verifier_blocked",
    severity: RuntimeTraceSeverity = "info",
    title: str | None = None,
    detail: str | None = None,
    reason_code: str | None = None,
    rule_id: str | None = None,
    required_action: str | None = None,
    attempt: int | float | None = None,
    max_attempts: int | float | None = None,
    retryable: bool | None = None,
    metadata: PublicMetadata | None = None,
    event_family: str = "runtime_trace_and_error",
) -> PublicEvent:
    _require_event_family(event_family, {"runtime_trace_and_error"})
    event: PublicEvent = {"type": "runtime_trace", "phase": phase, "severity": severity}
    _put_text(event, "turnId", turn_id, limit=_ID_LIMIT)
    _put_text(event, "title", title)
    _put_text(event, "detail", detail, limit=_DETAIL_LIMIT)
    _put_text(event, "reasonCode", _first_ref(reason_code, metadata, "receiptRef", "evidenceRef"))
    _put_text(event, "ruleId", _first_ref(rule_id, metadata, "policyDigest", "ruleDigest"))
    _put_text(event, "requiredAction", required_action)
    _put_number(event, "attempt", attempt)
    _put_number(event, "maxAttempts", max_attempts)
    if isinstance(retryable, bool):
        event["retryable"] = retryable
    return event


def tool_progress_event(
    *,
    tool_id: str,
    label: str | None = None,
    status: str | None = None,
    message: str | None = None,
    detail: str | None = None,
    progress: int | float | None = None,
    created_at: int | float | None = None,
    event_family: str = "tool_progress",
) -> PublicEvent:
    _require_event_family(event_family, {"tool_progress"})
    event: PublicEvent = {"type": "tool_progress", "id": _public_text(tool_id, limit=_ID_LIMIT)}
    _put_text(event, "label", label)
    _put_text(event, "status", status)
    _put_text(event, "message", message)
    _put_text(event, "detail", detail)
    _put_number(event, "progress", progress)
    _put_number(event, "createdAt", created_at)
    return event


def tool_start_event(
    *,
    tool_id: str,
    name: str,
    input_preview: str | None = None,
    event_family: str = "tool_progress",
) -> PublicEvent:
    _require_event_family(event_family, {"tool_progress"})
    event: PublicEvent = {
        "type": "tool_start",
        "id": _public_text(tool_id, limit=_ID_LIMIT),
        "name": _public_text(name),
    }
    _put_text(event, "input_preview", input_preview)
    return event


def tool_input_preview(arguments: Mapping[str, object] | None) -> str | None:
    if arguments is None:
        return None
    preview: dict[str, str] = {}
    for key in _TOOL_INPUT_PREVIEW_KEYS:
        value = arguments.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        safe_value = _public_text(
            value.strip(),
            limit=_TOOL_INPUT_PREVIEW_VALUE_LIMIT,
        )
        if safe_value:
            preview[key] = safe_value
    if not preview:
        return None
    return json.dumps(preview, sort_keys=True, separators=(",", ":"))


def tool_end_event(
    *,
    tool_id: str,
    status: str,
    output_preview: str | None = None,
    error: str | None = None,
    receipt_refs: Sequence[object] = (),
    duration_ms: int | float | None = None,
    event_family: str = "tool_progress",
) -> PublicEvent:
    _require_event_family(event_family, {"tool_progress"})
    safe_status = status if status in {"ok", "error"} else "error"
    event: PublicEvent = {
        "type": "tool_end",
        "id": _public_text(tool_id, limit=_ID_LIMIT),
        "status": safe_status,
    }
    _put_text(event, "output_preview", output_preview)
    _put_text(event, "error", error)
    refs = _safe_refs(receipt_refs)
    if refs:
        event["transcriptRefs"] = refs
    _put_number(event, "durationMs", duration_ms)
    return event


def tool_blocked_event(
    *,
    tool_id: str,
    reason: str,
    receipt_refs: Sequence[object] = (),
    duration_ms: int | float | None = None,
    event_family: str = "tool_blocked_or_permission_denied",
) -> PublicEvent:
    _require_event_family(event_family, {"tool_blocked_or_permission_denied"})
    event: PublicEvent = {
        "type": "tool_end",
        "id": _public_text(tool_id, limit=_ID_LIMIT),
        "status": "error",
        "error": _public_text(reason),
    }
    event["output_preview"] = _public_text(reason)
    refs = _safe_refs(receipt_refs)
    if refs:
        event["transcriptRefs"] = refs
    _put_number(event, "durationMs", duration_ms)
    return event


def source_inspected_event(
    *,
    source_id: str,
    kind: SourceKind,
    uri: str,
    title: str | None = None,
    content_hash: str | None = None,
    evidence_ref: str | None = None,
    content_type: str | None = None,
    trust_tier: TrustTier = "unknown",
    turn_id: str | None = None,
    tool_name: str | None = None,
    tool_use_id: str | None = None,
    inspected_at: int | float | None = None,
    snippets: Sequence[object] = (),
    event_family: str = "source_inspected",
) -> PublicEvent:
    _require_event_family(event_family, {"source_inspected"})
    safe_evidence_ref = _safe_ref(content_hash) or _safe_ref(evidence_ref)
    if safe_evidence_ref is None:
        raise ValueError("source inspection requires safe evidence ref")
    source: dict[str, object] = {
        "sourceId": _public_text(source_id, limit=_ID_LIMIT),
        "kind": kind,
        "uri": _public_uri(uri, limit=_URI_LIMIT),
        "trustTier": trust_tier,
        "contentHash": safe_evidence_ref,
    }
    _put_text(source, "title", title)
    _put_text(source, "contentType", content_type)
    _put_text(source, "turnId", turn_id, limit=_ID_LIMIT)
    _put_text(source, "toolName", tool_name)
    _put_text(source, "toolUseId", tool_use_id, limit=_ID_LIMIT)
    _put_number(source, "inspectedAt", inspected_at)
    safe_snippets = _public_text_list(snippets, limit=_SOURCE_SNIPPET_LIMIT)
    if safe_snippets:
        source["snippets"] = safe_snippets
    return {"type": "source_inspected", "source": source}


def rule_check_event(
    *,
    rule_id: str,
    verdict: RuleVerdict = "pending",
    detail: str | None = None,
    event_family: str = "rule_check",
) -> PublicEvent:
    _require_event_family(event_family, {"rule_check", "citation_gate_alias"})
    event: PublicEvent = {
        "type": "rule_check",
        "ruleId": _public_text(rule_id, limit=_ID_LIMIT),
        "verdict": verdict,
    }
    _put_text(event, "detail", detail)
    return event


def authorize_rule_check_event(event: PublicEvent) -> PublicEvent:
    event[_RULE_CHECK_AUTHORITY_FIELD] = _RULE_CHECK_AUTHORITY_TOKEN
    return event


def authorize_rule_check_metadata(metadata: dict[str, object]) -> dict[str, object]:
    metadata[_RULE_CHECK_AUTHORITY_FIELD] = _RULE_CHECK_AUTHORITY_TOKEN
    return metadata


def rule_check_event_has_authority(event: Mapping[str, object]) -> bool:
    return event.get(_RULE_CHECK_AUTHORITY_FIELD) is _RULE_CHECK_AUTHORITY_TOKEN


def copy_rule_check_authority(
    source: Mapping[str, object],
    target: dict[str, object],
) -> None:
    if rule_check_event_has_authority(source):
        target[_RULE_CHECK_AUTHORITY_FIELD] = _RULE_CHECK_AUTHORITY_TOKEN


def is_rule_check_authority_field(key: object) -> bool:
    return key == _RULE_CHECK_AUTHORITY_FIELD


def child_started_event(
    *,
    task_id: str,
    parent_turn_id: str,
    child_receipt_ref: str,
    agent_name: str | None = None,
    model: str | None = None,
    task_title: str | None = None,
    detail: str = "Delegated child started",
    event_family: str = "child_spawn_background_supported_core",
) -> PublicEvent:
    """Build a sanitized ``child_started`` event.

    The optional ``agent_name`` / ``model`` / ``task_title`` fields drive the
    local-dashboard AGENTS chip label, model badge, and per-agent task hint
    respectively.  ``task_title`` is a public-safe short brief the LLM provides
    via SpawnAgent args — it is NOT the prompt body (privacy contract).
    """
    _require_event_family(event_family, {"child_spawn_background_supported_core"})
    event: PublicEvent = {
        "type": "child_started",
        "taskId": _public_text(task_id, limit=_ID_LIMIT),
        "parentTurnId": _public_text(parent_turn_id, limit=_ID_LIMIT),
        "childReceiptRef": _public_text(child_receipt_ref, limit=_ID_LIMIT),
        "detail": _public_text(detail),
    }
    _put_text(event, "agentName", agent_name)
    _put_text(event, "model", model)
    _put_text(event, "taskTitle", task_title)
    return event


def child_progress_event(
    *,
    task_id: str,
    detail: str,
    event_family: str = "child_spawn_background_supported_core",
) -> PublicEvent:
    _require_event_family(event_family, {"child_spawn_background_supported_core"})
    return {
        "type": "child_progress",
        "taskId": _public_text(task_id, limit=_ID_LIMIT),
        "detail": _public_text(detail),
    }


def child_completed_event(
    *,
    task_id: str,
    child_receipt_ref: str,
    summary: str | None = None,
    event_family: str = "child_spawn_background_supported_core",
) -> PublicEvent:
    """Build a sanitized ``child_completed`` event.

    ``summary`` is the truncated + redacted preview of the child's final
    answer.  This is the SAME string the parent LLM consumes via the tool
    result (``envelope.summary``); surfacing a preview here lets the UI hint
    what the child actually came back with — instead of resetting the chip
    detail to nothing once the child finishes.
    """
    _require_event_family(event_family, {"child_spawn_background_supported_core"})
    event: PublicEvent = {
        "type": "child_completed",
        "taskId": _public_text(task_id, limit=_ID_LIMIT),
        "childReceiptRef": _public_text(child_receipt_ref, limit=_ID_LIMIT),
    }
    _put_text(event, "summary", summary)
    return event


def child_failed_event(
    *,
    task_id: str,
    child_receipt_ref: str,
    error_message: str,
    summary: str | None = None,
    event_family: str = "child_spawn_background_supported_core",
) -> PublicEvent:
    _require_event_family(event_family, {"child_spawn_background_supported_core"})
    event: PublicEvent = {
        "type": "child_failed",
        "taskId": _public_text(task_id, limit=_ID_LIMIT),
        "childReceiptRef": _public_text(child_receipt_ref, limit=_ID_LIMIT),
        "errorMessage": _public_text(error_message),
    }
    _put_text(event, "summary", summary)
    return event


def child_cancelled_event(
    *,
    task_id: str,
    child_receipt_ref: str,
    reason: str,
    summary: str | None = None,
    event_family: str = "child_spawn_background_supported_core",
) -> PublicEvent:
    _require_event_family(event_family, {"child_spawn_background_supported_core"})
    event: PublicEvent = {
        "type": "child_cancelled",
        "taskId": _public_text(task_id, limit=_ID_LIMIT),
        "childReceiptRef": _public_text(child_receipt_ref, limit=_ID_LIMIT),
        "reason": _public_text(reason),
    }
    _put_text(event, "summary", summary)
    return event


def task_board_event(
    *,
    tasks: Sequence[PublicTask],
    event_family: str = "task_board",
) -> PublicEvent:
    _require_event_family(event_family, {"task_board"})
    safe_tasks: list[dict[str, object]] = []
    for task in tasks[:_TASK_LIMIT]:
        task_id = _string_value(task.get("id"))
        title = _string_value(task.get("title"))
        if task_id is None or title is None:
            continue
        status = _task_status(task.get("status"))
        if status == "completed":
            status = "in_progress"
        safe_task: dict[str, object] = {
            "id": _public_text(task_id, limit=_ID_LIMIT),
            "title": _public_text(title),
            "description": _public_text(_string_value(task.get("description")) or ""),
            "status": status,
        }
        parallel_group = _string_value(task.get("parallelGroup"))
        if parallel_group is not None:
            safe_task["parallelGroup"] = _public_text(parallel_group, limit=_ID_LIMIT)
        depends_on = _public_text_list(
            _sequence_value(task.get("dependsOn")),
            limit=_TASK_DEPENDENCY_LIMIT,
            text_limit=_ID_LIMIT,
        )
        if depends_on:
            safe_task["dependsOn"] = depends_on
        safe_tasks.append(safe_task)
    return {"type": "task_board", "tasks": safe_tasks}


# ---------------------------------------------------------------------------
# Tool-event id — shared by gate5b4c3 and future wire-profile consumers
# ---------------------------------------------------------------------------

__all__ = [
    "result_digest",
    "tool_event_id",
    "tool_input_preview",
    "tool_start_event",
    "tool_progress_event",
    "tool_end_event",
    "tool_blocked_event",
    "turn_phase_event",
    "heartbeat_event",
    "runtime_trace_event",
    "source_inspected_event",
    "rule_check_event",
    "authorize_rule_check_event",
    "authorize_rule_check_metadata",
    "rule_check_event_has_authority",
    "copy_rule_check_authority",
    "is_rule_check_authority_field",
    "child_progress_event",
    "child_started_event",
    "child_completed_event",
    "child_failed_event",
    "child_cancelled_event",
    "task_board_event",
    "PublicEvent",
    "PublicMetadata",
    "PublicTask",
    "TurnPhase",
    "RuntimeTracePhase",
    "RuntimeTraceSeverity",
    "RuleVerdict",
    "SourceKind",
    "TrustTier",
    "TaskStatus",
]


def result_digest(value: object) -> str:
    """Return ``sha256:<hex>`` digest of *value* — thin public wrapper around ``_te_digest``.

    Callers (T3 bridge, gate5b4c3 parity checks) import this public name
    instead of reaching into the private ``_te_digest`` helper.  Byte-identical
    to ``gate5b4c3_live_runner_boundary._digest``.
    """
    return _te_digest(value)


def _te_json_dumps(value: object) -> str:
    """Canonical JSON serialisation for tool-event-id hashing.

    Must stay byte-identical to gate5b4c3_live_runner_boundary._json_dumps.
    """
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=repr,
    )


def _te_digest(value: object) -> str:
    """sha256 digest string — byte-identical to gate5b4c3._digest."""
    return "sha256:" + hashlib.sha256(_te_json_dumps(value).encode("utf-8")).hexdigest()


def _te_bounded_json_value(value: object, *, max_bytes: int) -> object:
    """Truncate value to max_bytes — byte-identical to gate5b4c3._bounded_json_value."""
    encoded = _te_json_dumps(value).encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    return {"truncated": True, "digest": _te_digest(value)}


def tool_event_id(
    *,
    name: str,
    args: Mapping[str, object],
    call_id: object,
    index: int,
) -> str:
    """Return the ``tu_<12-hex>`` tool-event id for a function call.

    Byte-identical to ``gate5b4c3_live_runner_boundary._manual_tool_event_id``.
    Both gate5b4c3 and the hosted wire-profile engine call this shared fn so
    the id scheme is computed exactly once.
    """
    return "tu_" + _te_digest(
        {
            "name": name,
            "args": _te_bounded_json_value(args, max_bytes=512),
            "id": str(call_id or ""),
            "index": index,
        }
    )[7:19]


def _require_event_family(event_family: str, allowed: set[str]) -> None:
    if event_family not in allowed:
        raise ValueError(f"unsupported event family: {event_family}")


def _put_text(
    event: dict[str, object],
    key: str,
    value: str | None,
    *,
    limit: int = _TEXT_LIMIT,
) -> None:
    if value is not None and value.strip():
        event[key] = _public_text(value, limit=limit)


def _put_number(event: dict[str, object], key: str, value: int | float | None) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return
    if math.isfinite(float(value)):
        event[key] = value


def _public_text(value: str, *, limit: int = _TEXT_LIMIT) -> str:
    if "[redacted-private]" in value or _has_private_text_marker(value):
        return "[redacted-private]"
    redacted = _AUTH_HEADER_RE.sub(r"\1[redacted]", value)
    redacted = _BEARER_RE.sub(r"\1[redacted]", redacted)
    redacted = _BASIC_RE.sub(r"\1[redacted]", redacted)
    redacted = _COOKIE_HEADER_RE.sub(r"\1[redacted]", redacted)
    redacted = _GITHUB_TOKEN_RE.sub("[redacted]", redacted)
    redacted = _GITHUB_PAT_RE.sub("[redacted]", redacted)
    redacted = _OPENAI_TOKEN_RE.sub("[redacted]", redacted)
    redacted = _STRIPE_TOKEN_RE.sub("[redacted]", redacted)
    redacted = _SLACK_TOKEN_RE.sub("[redacted]", redacted)
    redacted = _AWS_ACCESS_KEY_RE.sub("[redacted]", redacted)
    redacted = _JWT_RE.sub("[redacted]", redacted)
    redacted = _TELEGRAM_TOKEN_RE.sub("[redacted]", redacted)
    redacted = _KEY_VALUE_SECRET_RE.sub(r"\1[redacted]", redacted)
    redacted = _PRODUCTION_PATH_RE.sub("[redacted-path]", redacted)
    redacted = _PRIVATE_REF_RE.sub("[redacted-ref]", redacted)
    redacted = _redact_sensitive_route_paths(redacted)
    if len(redacted) > limit:
        return f"{redacted[:limit - 3]}..."
    return redacted


def _public_uri(value: str, *, limit: int = _URI_LIMIT) -> str:
    return _public_text(_redact_sensitive_route_paths(value), limit=limit)


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


def _has_sensitive_route_path(value: str) -> bool:
    normalized = _normalize_route_separators(value)
    return bool(
        _SENSITIVE_ROUTE_PATH_RE.search(value)
        or _SENSITIVE_ROUTE_PATH_RE.search(normalized)
        or _SENSITIVE_ROUTE_SEGMENT_RE.search(normalized)
    )


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


def _safe_ref(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if (
        _DIGEST_RE.fullmatch(candidate)
        or _RESULT_REF_RE.fullmatch(candidate)
        or _PUBLIC_REF_RE.fullmatch(candidate)
    ):
        return candidate
    return None


def _safe_refs(values: Sequence[object]) -> list[str]:
    refs: list[str] = []
    for value in values[:_REF_LIMIT]:
        ref = _safe_ref(value)
        if ref is not None:
            refs.append(ref)
    return refs


def _first_ref(
    explicit_value: str | None,
    metadata: PublicMetadata | None,
    *metadata_keys: str,
) -> str | None:
    explicit_ref = _safe_ref(explicit_value)
    if explicit_ref is not None:
        return explicit_ref
    if metadata is None:
        return None
    for key in metadata_keys:
        metadata_ref = _safe_ref(metadata.get(key))
        if metadata_ref is not None:
            return metadata_ref
    return None


def _public_text_list(
    values: Sequence[object],
    *,
    limit: int,
    text_limit: int = _TEXT_LIMIT,
) -> list[str]:
    safe_values: list[str] = []
    for value in values[:limit]:
        if isinstance(value, str) and value.strip():
            safe_values.append(_public_text(value, limit=text_limit))
    return safe_values


def _string_value(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    return None


def _sequence_value(value: object) -> Sequence[object]:
    if isinstance(value, list | tuple):
        return value
    return ()


def _task_status(value: object) -> TaskStatus:
    if value in {"pending", "in_progress", "completed", "cancelled"}:
        return value
    return "pending"
