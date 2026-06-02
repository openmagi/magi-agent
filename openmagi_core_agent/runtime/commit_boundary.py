from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import re
from typing import Any, Literal

from openmagi_core_agent.runtime.turn_utilities import (
    normalize_user_visible_route_meta_tags,
)


CommitPlanStatus = Literal["committed", "blocked", "aborted"]
IntentTarget = Literal["transcript", "sse", "control", "hook", "local_runtime"]

_DISABLED_REASON = (
    "local commit boundary planner is disabled/default-off; "
    "intent records are descriptive only"
)
_DEFAULT_USAGE = {"inputTokens": 0, "outputTokens": 0, "costUsd": 0}
_REASON_CODE_RE = re.compile(r"\[(?:RETRY|RULE):([A-Z0-9_:-]+)\]")
_HOOK_THROW_RE = re.compile(r"^hook:[^\s]+ threw:", re.IGNORECASE)
_HOOK_TIMEOUT_RE = re.compile(
    r"^hook:[^\s]+ .*?(?:timeout|timed out)",
    re.IGNORECASE,
)
_BEARER_RE = re.compile(r"(Bearer\s+)[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
_BASIC_RE = re.compile(r"(Basic\s+)[A-Za-z0-9+/=:_-]+", re.IGNORECASE)
_GH_TOKEN_RE = re.compile(r"\bgh[pousr]_[A-Za-z0-9_]+\b")
_SK_TOKEN_RE = re.compile(r"\bsk-[A-Za-z0-9_-]+\b")
_TELEGRAM_BOT_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:bot)?\d{6,12}:[A-Za-z0-9_-]{20,}\b",
    re.IGNORECASE,
)
_COOKIE_HEADER_RE = re.compile(
    r"\b((?:Set-)?Cookie\s*:\s*)[^\r\n]+",
    re.IGNORECASE,
)
_PRIVATE_KEY_BLOCK_RE = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    re.IGNORECASE | re.DOTALL,
)
_PRIVATE_FIELD_RE = re.compile(
    r"\b(?:hiddenReasoning|privateToolPreview|privateReasoning|"
    r"private(?:Key|Token|Secret|Password)?|reasoningPrivate)"
    r"[\"'\s:=]+(?:\"[^\"]*\"|'[^']*'|[^\r\n,}]+)",
    re.IGNORECASE,
)
_ABSOLUTE_PRIVATE_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_.-])/(?:data/bots|workspace|var/lib/kubelet|Users|home|"
    r"root|tmp|private|etc|opt|srv|app)(?:/[^\s\"',}]+)*",
    re.IGNORECASE,
)
_PRIVATE_PRODUCTION_URL_RE = re.compile(
    r"https?://(?:clawy\.pro|(?:www\.)?openmagi\.ai|staging\.openmagi\.ai)"
    r"/(?:internal|api/internal|v1/internal|admin/internal)(?:[^\s\"',}]*)?",
    re.IGNORECASE,
)
_KEY_VALUE_SECRET_RE = re.compile(
    r"(\b(?:[A-Z0-9_]*(?:API[_-]?KEY|SERVICE[_-]?ROLE[_-]?KEY|"
    r"PRIVATE[_-]?KEY|TOKEN|SECRET|PASSWORD)[A-Z0-9_]*|api[_-]?key|"
    r"token|secret|password)[\"'\s:=]+)(?:\"[^\"]*\"|'[^']*'|[^\"'\s,}]+)",
    re.IGNORECASE,
)
_READ_TOOL_NAMES = frozenset({"FileRead", "Grep", "Glob"})


@dataclass(frozen=True)
class CommitIntent:
    target: IntentTarget
    operation: str
    payload: dict[str, Any] = field(default_factory=dict)
    executed: Literal[False] = False
    enabled: Literal[False] = False
    defaultOff: Literal[True] = True
    disabledReason: str = _DISABLED_REASON

    def __post_init__(self) -> None:
        _assert_disabled_default_off(
            type_name="CommitIntent",
            executed=self.executed,
            enabled=self.enabled,
            default_off=self.defaultOff,
        )


@dataclass(frozen=True)
class CommitBoundaryPlan:
    status: CommitPlanStatus
    intents: tuple[CommitIntent, ...]
    finalText: str = ""
    reason: str | None = None
    retryable: bool | None = None
    retryKind: str | None = None
    stopReason: str | None = None
    reasonCode: str | None = None
    requiredAction: str | None = None
    executed: Literal[False] = False
    enabled: Literal[False] = False
    defaultOff: Literal[True] = True
    disabledReason: str = _DISABLED_REASON

    def __post_init__(self) -> None:
        _assert_disabled_default_off(
            type_name="CommitBoundaryPlan",
            executed=self.executed,
            enabled=self.enabled,
            default_off=self.defaultOff,
        )


def collect_final_assistant_text(blocks: Sequence[Mapping[str, Any]]) -> str:
    text = "".join(
        block.get("text", "")
        for block in blocks
        if block.get("type") == "text" and isinstance(block.get("text"), str)
    )
    return normalize_user_visible_route_meta_tags(text).lstrip()


def build_commit_plan(
    *,
    blocks: Sequence[Mapping[str, Any]],
    turn_id: str,
    user_message: str,
    usage: Mapping[str, Any] | None = None,
    started_at: int | float | None = None,
    ended_at: int | float | None = None,
    stop_reason: str = "end_turn",
) -> CommitBoundaryPlan:
    final_text = collect_final_assistant_text(blocks)
    usage_payload = dict(_DEFAULT_USAGE | dict(usage or {}))
    tool_names = _collect_tool_names(blocks)
    files_changed = collect_files_changed(blocks)
    intents: list[CommitIntent] = [
        _intent(
            "hook",
            "beforeCommit",
            _before_commit_payload(
                blocks=blocks,
                assistant_text=final_text,
                user_message=user_message,
            ),
        )
    ]

    if final_text:
        intents.append(
            _intent(
                "transcript",
                "assistant_text",
                {
                    "kind": "assistant_text",
                    "turnId": turn_id,
                    "text": final_text,
                },
            )
        )

    intents.extend(
        (
            _intent(
                "transcript",
                "turn_committed",
                {
                    "kind": "turn_committed",
                    "turnId": turn_id,
                    "inputTokens": usage_payload.get("inputTokens", 0),
                    "outputTokens": usage_payload.get("outputTokens", 0),
                },
            ),
            _intent(
                "control",
                "stop_reason",
                {
                    "type": "stop_reason",
                    "turnId": turn_id,
                    "reason": stop_reason,
                },
            ),
            _intent(
                "sse",
                "turn_end",
                {
                    "type": "turn_end",
                    "turnId": turn_id,
                    "status": "committed",
                    "stopReason": stop_reason,
                    "usage": usage_payload,
                },
            ),
            _intent("sse", "legacy_finish", {"type": "legacy_finish"}),
            _intent(
                "hook",
                "afterCommit",
                {"assistantText": final_text},
            ),
            _intent(
                "hook",
                "afterTurnEnd",
                {
                    "userMessage": user_message,
                    "assistantText": final_text,
                    "status": "committed",
                },
            ),
            _intent(
                "hook",
                "onTaskCheckpoint",
                _omit_none(
                    {
                        "userMessage": user_message,
                        "assistantText": final_text,
                        "toolCallCount": len(tool_names),
                        "toolNames": tool_names,
                        "filesChanged": files_changed,
                        "startedAt": started_at,
                        "endedAt": ended_at,
                    }
                ),
            ),
        )
    )
    return CommitBoundaryPlan(
        status="committed",
        finalText=final_text,
        stopReason=stop_reason,
        intents=tuple(intents),
    )


def build_before_commit_block_plan(
    *,
    reason: str,
    blocks: Sequence[Mapping[str, Any]],
    turn_id: str,
    user_message: str = "",
    retry_count: int = 0,
) -> CommitBoundaryPlan:
    final_text = collect_final_assistant_text(blocks)
    retryable = is_before_commit_block_retryable(reason)
    reason_code = reason_code_from_reason(reason)
    required_action = required_action_from_reason(reason)
    public_reason = public_trace_detail(reason)
    trace = _runtime_trace_payload(
        turn_id=turn_id,
        phase="verifier_blocked",
        severity="warning" if retryable else "error",
        title="Runtime verifier blocked completion",
        detail=reason,
        reason_code=reason_code,
        retryable=retryable,
        required_action=required_action,
    )
    return CommitBoundaryPlan(
        status="blocked",
        finalText=final_text,
        reason=public_reason,
        retryable=retryable,
        retryKind="before_commit_blocked",
        reasonCode=reason_code,
        requiredAction=public_trace_detail(required_action) if required_action else None,
        intents=(
            _intent(
                "hook",
                "beforeCommit",
                _before_commit_payload(
                    blocks=blocks,
                    assistant_text=final_text,
                    user_message=user_message,
                    retry_count=retry_count,
                ),
            ),
            _intent("sse", "runtime_trace", trace),
            _intent("control", "runtime_trace", trace),
        ),
    )


def build_structured_output_block_plan(
    assessment: Mapping[str, Any],
    *,
    blocks: Sequence[Mapping[str, Any]],
    turn_id: str,
) -> CommitBoundaryPlan:
    raw_reason = _string_value(assessment.get("reason")) or "structured output invalid"
    reason = public_trace_detail(raw_reason)
    status = _string_value(assessment.get("status")) or "invalid"
    schema_name = _string_value(assessment.get("schemaName")) or _string_value(
        assessment.get("schema_name")
    )
    retryable = status != "retry_exhausted"
    stop_reason = (
        "structured_output_retry_exhausted"
        if status == "retry_exhausted"
        else None
    )
    trace = _runtime_trace_payload(
        turn_id=turn_id,
        phase="verifier_blocked",
        severity="warning" if retryable else "error",
        title="Structured output verifier blocked completion",
        detail=reason,
        retryable=retryable,
        required_action="Produce output that satisfies the required schema.",
    )
    structured_payload = _omit_none(
        {
            "type": "structured_output",
            "turnId": turn_id,
            "status": status,
            "schemaName": schema_name or None,
            "reason": reason,
        }
    )
    return CommitBoundaryPlan(
        status="blocked",
        finalText=collect_final_assistant_text(blocks),
        reason=reason,
        retryable=retryable,
        retryKind="structured_output_invalid",
        stopReason=stop_reason,
        intents=(
            _intent("sse", "structured_output", structured_payload),
            _intent("control", "structured_output", structured_payload),
            _intent("sse", "runtime_trace", trace),
            _intent("control", "runtime_trace", trace),
        ),
    )


def build_abort_plan(
    *,
    turn_id: str,
    user_message: str,
    reason: str,
    cached_assistant_text: str = "",
    stop_reason: str = "aborted",
) -> CommitBoundaryPlan:
    reason_code = reason_code_from_reason(reason)
    public_reason = public_trace_detail(reason)
    trace = _runtime_trace_payload(
        turn_id=turn_id,
        phase="terminal_abort",
        severity="error",
        title="Turn aborted before completion",
        detail=reason,
        reason_code=reason_code,
        retryable=False,
    )
    intents = (
        _intent(
            "local_runtime",
            "reject_pending_asks",
            {"reason": public_reason},
        ),
        _intent(
            "transcript",
            "turn_aborted",
            {
                "kind": "turn_aborted",
                "turnId": turn_id,
                "reason": public_reason,
            },
        ),
        _intent(
            "control",
            "stop_reason",
            {
                "type": "stop_reason",
                "turnId": turn_id,
                "reason": stop_reason,
            },
        ),
        _intent("sse", "runtime_trace", trace),
        _intent("control", "runtime_trace", trace),
        _intent(
            "sse",
            "turn_end",
            {
                "type": "turn_end",
                "turnId": turn_id,
                "status": "aborted",
                "stopReason": stop_reason,
                "reason": public_reason,
            },
        ),
        _intent("sse", "legacy_finish", {"type": "legacy_finish"}),
        _intent("hook", "onAbort", {"reason": public_reason}),
        _intent(
            "hook",
            "afterTurnEnd",
            {
                "userMessage": user_message,
                "assistantText": cached_assistant_text,
                "status": "aborted",
                "reason": public_reason,
            },
        ),
    )
    return CommitBoundaryPlan(
        status="aborted",
        reason=public_reason,
        stopReason=stop_reason,
        reasonCode=reason_code,
        intents=intents,
    )


def is_before_commit_block_retryable(reason: str) -> bool:
    normalized = reason.strip()
    if normalized.startswith("[RULE:SEALED_FILES]"):
        return False
    if normalized.startswith("[RULE:MEMORY_MUTATION_TOOL_REQUIRED]"):
        return False
    if normalized.startswith("[RULE:CLAIM_CITATION_REQUIRED]"):
        return False
    if normalized.startswith("[RULE:CLAIM_CITATION_GATE_ERROR]"):
        return False
    if _HOOK_THROW_RE.search(normalized):
        return False
    if _HOOK_TIMEOUT_RE.search(normalized):
        return False
    return True


def reason_code_from_reason(reason: str) -> str | None:
    match = _REASON_CODE_RE.search(reason)
    if not match:
        return None
    return match.group(1)


def required_action_from_reason(reason: str) -> str | None:
    code = reason_code_from_reason(reason)
    if not code:
        return None
    if "GOAL_PROGRESS_EXECUTE_NEXT" in code:
        return "Call the required tool or runtime action before answering."
    if "INTERACTIVE_TOOL_REQUIRED" in code:
        return "Use Browser or SocialBrowser before answering."
    if "CLAIM_CITATION" in code or "RESEARCH_PROOF" in code:
        return "Cite inspected sources or remove unsupported claims."
    if "ARTIFACT" in code or "DELIVERY" in code or "FILE" in code:
        return "Deliver the requested artifact before claiming completion."
    return None


def collect_files_changed(blocks: Sequence[Mapping[str, Any]]) -> list[str]:
    changed: list[str] = []
    for block in blocks:
        if block.get("type") != "tool_use":
            continue
        name = block.get("name")
        tool_input = block.get("input")
        if name in {"FileWrite", "FileEdit"}:
            path = _path_from_input(tool_input)
            if path:
                changed.append(path)
            continue
        if name == "PatchApply":
            changed.extend(_patch_apply_changed_paths(tool_input))
            continue
        if name == "SpawnWorktreeApply":
            path = _spawn_worktree_apply_changed_path(tool_input)
            if path:
                changed.append(path)
    return _dedupe_preserve_order(
        [
            display_path
            for path in changed
            if (display_path := _safe_workspace_display_path(path)) is not None
        ]
    )


def public_trace_detail(value: str) -> str:
    redacted = _BEARER_RE.sub(r"\1[redacted]", value)
    redacted = _BASIC_RE.sub(r"\1[redacted]", redacted)
    redacted = _COOKIE_HEADER_RE.sub(r"\1[redacted]", redacted)
    redacted = _PRIVATE_KEY_BLOCK_RE.sub("[redacted-private-key]", redacted)
    redacted = _PRIVATE_FIELD_RE.sub("[redacted-private-field]", redacted)
    redacted = _GH_TOKEN_RE.sub("[redacted]", redacted)
    redacted = _SK_TOKEN_RE.sub("[redacted]", redacted)
    redacted = _TELEGRAM_BOT_TOKEN_RE.sub("[redacted]", redacted)
    redacted = _KEY_VALUE_SECRET_RE.sub(r"\1[redacted]", redacted)
    redacted = _ABSOLUTE_PRIVATE_PATH_RE.sub("[redacted-path]", redacted)
    redacted = _PRIVATE_PRODUCTION_URL_RE.sub("[redacted-url]", redacted)
    redacted = re.sub(r"\s+", " ", redacted).strip()
    if len(redacted) > 500:
        return f"{redacted[:497]}..."
    return redacted


def _intent(
    target: IntentTarget,
    operation: str,
    payload: Mapping[str, Any] | None = None,
) -> CommitIntent:
    return CommitIntent(
        target=target,
        operation=operation,
        payload=dict(payload or {}),
    )


def _runtime_trace_payload(
    *,
    turn_id: str,
    phase: str,
    severity: str,
    title: str,
    detail: str | None = None,
    reason_code: str | None = None,
    retryable: bool | None = None,
    required_action: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": "runtime_trace",
        "turnId": turn_id,
        "phase": phase,
        "severity": severity,
        "title": public_trace_detail(title),
    }
    if detail:
        payload["detail"] = public_trace_detail(detail)
    if reason_code:
        payload["reasonCode"] = reason_code
    if retryable is not None:
        payload["retryable"] = retryable
    if required_action:
        payload["requiredAction"] = public_trace_detail(required_action)
    return payload


def _assert_disabled_default_off(
    *,
    type_name: str,
    executed: object,
    enabled: object,
    default_off: object,
) -> None:
    if executed is not False or enabled is not False or default_off is not True:
        raise ValueError(
            f"{type_name} is descriptive only: executed=False, "
            "enabled=False, defaultOff=True are required"
        )


def _before_commit_payload(
    *,
    blocks: Sequence[Mapping[str, Any]],
    assistant_text: str,
    user_message: str,
    retry_count: int = 0,
) -> dict[str, Any]:
    tool_names = _collect_tool_names(blocks)
    return {
        "assistantText": assistant_text,
        "toolCallCount": len(tool_names),
        "toolReadHappened": _tool_read_happened(blocks),
        "userMessage": user_message,
        "retryCount": retry_count,
        "toolNames": tool_names,
        "filesChanged": collect_files_changed(blocks),
    }


def _tool_read_happened(blocks: Sequence[Mapping[str, Any]]) -> bool:
    return any(
        block.get("type") == "tool_use" and block.get("name") in _READ_TOOL_NAMES
        for block in blocks
    )


def _omit_none(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if value is not None}


def _collect_tool_names(blocks: Sequence[Mapping[str, Any]]) -> list[str]:
    names: list[str] = []
    for block in blocks:
        if block.get("type") != "tool_use":
            continue
        name = block.get("name")
        if isinstance(name, str):
            names.append(name)
    return names


def _path_from_input(tool_input: object) -> str | None:
    if not isinstance(tool_input, Mapping):
        return None
    path = tool_input.get("path")
    if isinstance(path, str) and path:
        return path
    return None


def _safe_workspace_display_path(path: str) -> str | None:
    normalized = path.replace("\\", "/").strip()
    if not normalized or "\x00" in normalized:
        return None
    if (
        _GH_TOKEN_RE.search(normalized)
        or _SK_TOKEN_RE.search(normalized)
        or _TELEGRAM_BOT_TOKEN_RE.search(normalized)
    ):
        return None
    if re.match(r"^[A-Za-z]:/", normalized):
        return None
    if "/workspace/" in normalized:
        candidate = normalized.rsplit("/workspace/", 1)[1]
    elif normalized.startswith("/workspace/"):
        candidate = normalized[len("/workspace/") :]
    elif normalized.startswith("/"):
        return None
    else:
        candidate = normalized
    while candidate.startswith("./"):
        candidate = candidate[2:]
    parts = [part for part in candidate.split("/") if part and part != "."]
    if not parts or any(part == ".." for part in parts):
        return None
    return "/".join(parts)


def _patch_apply_changed_paths(tool_input: object) -> list[str]:
    if not isinstance(tool_input, Mapping):
        return []
    if tool_input.get("dry_run") is True:
        return []
    patch = tool_input.get("patch")
    if not isinstance(patch, str):
        return []
    paths: list[str] = []
    for line in patch.splitlines():
        if not line.startswith("--- ") and not line.startswith("+++ "):
            continue
        raw = line[4:].strip().split()
        if not raw:
            continue
        path = raw[0]
        if path == "/dev/null":
            continue
        paths.append(re.sub(r"^[ab]/", "", path))
    return _dedupe_preserve_order(paths)


def _spawn_worktree_apply_changed_path(tool_input: object) -> str | None:
    if not isinstance(tool_input, Mapping):
        return None
    action = tool_input.get("action")
    if action not in {"apply", "cherry_pick"}:
        return None
    spawn_dir = tool_input.get("spawnDir")
    if isinstance(spawn_dir, str) and spawn_dir:
        return spawn_dir
    return "SpawnWorktreeApply"


def _dedupe_preserve_order(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def _string_value(value: object) -> str:
    if isinstance(value, str):
        return value
    return ""
