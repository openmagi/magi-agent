from __future__ import annotations

from collections.abc import MutableSequence, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
import re
from typing import Any, Literal, Protocol, TypeAlias


StopReasonCase: TypeAlias = Literal[
    "end_turn",
    "tool_use",
    "stop_sequence",
    "max_tokens",
    "refusal",
    "pause_turn",
    "unknown",
]
DecisionKind: TypeAlias = Literal["finalise", "run_tools", "recover"]

MAX_OUTPUT_TOKENS_RECOVERY_LIMIT = 3
_CANONICAL_STOP_REASONS: frozenset[str] = frozenset(
    (
        "end_turn",
        "tool_use",
        "stop_sequence",
        "max_tokens",
        "refusal",
        "pause_turn",
    ),
)
_CONTEXT_OVERFLOW_RE = re.compile(
    r"prompt is too long|max_tokens_exceeded|context_length_exceeded|"
    r"request entity too large|input.*too (?:long|large)|"
    r"exceeds.*context|maximum context length",
    re.IGNORECASE,
)


class StopReasonHandlerDeps(Protocol):
    def stage_audit_event(
        self,
        event: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        ...

    def log_unknown(self, raw: str | None, turn_id: str) -> None:
        ...


@dataclass
class StopReasonHandlerState:
    recovery_attempt: int = 0
    assistant_text_so_far_len: int = 0
    completion_repair_attempt: int = 0


class CompletionGate(Protocol):
    """Optional finalise-path gate consulted before ``end_turn`` finalises.

    Implemented by the General-Automation task-completion seam
    (``harness/general_automation/task_completion``). It is passed in as a plain
    callable so ``turn_policy`` stays import-pure (no evidence/ledger imports
    here). Returning ``True`` means the turn should re-enter the loop (recover):
    the gate is responsible for any synthetic message injection + bounded-attempt
    bookkeeping, mirroring the output-recovery mechanism below. Returning
    ``False`` means finalise should proceed unchanged.
    """

    def __call__(
        self,
        deps: StopReasonHandlerDeps,
        state: StopReasonHandlerState,
        *,
        blocks: Sequence[dict[str, Any]],
        iteration: int,
        messages: MutableSequence[dict[str, Any]],
    ) -> bool:
        ...


@dataclass(frozen=True)
class StopReasonDecision:
    kind: DecisionKind
    tool_uses: list[dict[str, Any]] = field(default_factory=list)


def classify_stop_reason(raw: str | None) -> StopReasonCase:
    if raw in _CANONICAL_STOP_REASONS:
        return raw  # type: ignore[return-value]
    return "unknown"


def handle_stop_reason(
    deps: StopReasonHandlerDeps,
    state: StopReasonHandlerState,
    *,
    stop_reason_raw: str | None,
    blocks: Sequence[dict[str, Any]],
    iteration: int,
    turn_id: str,
    messages: MutableSequence[dict[str, Any]],
    completion_gate: CompletionGate | None = None,
) -> StopReasonDecision:
    stop_case = classify_stop_reason(stop_reason_raw)

    if stop_case in ("end_turn", "stop_sequence"):
        if completion_gate is not None and completion_gate(
            deps,
            state,
            blocks=blocks,
            iteration=iteration,
            messages=messages,
        ):
            return StopReasonDecision(kind="recover")
        return StopReasonDecision(kind="finalise")

    if stop_case == "refusal":
        deps.stage_audit_event(
            "rule_check_violation",
            {
                "reason": "model_refusal",
                "stop_reason": stop_reason_raw,
                "iteration": iteration,
            },
        )
        return StopReasonDecision(kind="finalise")

    if stop_case == "unknown":
        deps.log_unknown(stop_reason_raw, turn_id)
        deps.stage_audit_event(
            "stop_reason_unknown",
            {"raw": stop_reason_raw, "iteration": iteration},
        )
        return StopReasonDecision(kind="finalise")

    if stop_case == "tool_use":
        tool_uses = [block for block in blocks if block.get("type") == "tool_use"]
        if not tool_uses:
            return StopReasonDecision(kind="finalise")
        return StopReasonDecision(kind="run_tools", tool_uses=tool_uses)

    if state.recovery_attempt >= MAX_OUTPUT_TOKENS_RECOVERY_LIMIT:
        deps.stage_audit_event(
            "output_recovery_exhausted",
            {
                "finalLength": state.assistant_text_so_far_len,
                "limit": MAX_OUTPUT_TOKENS_RECOVERY_LIMIT,
                "stop_reason": stop_reason_raw,
            },
        )
        return StopReasonDecision(kind="finalise")

    tool_use_count = sum(1 for block in blocks if block.get("type") == "tool_use")
    filtered_blocks = [
        deepcopy(block) for block in blocks if block.get("type") != "tool_use"
    ]
    if tool_use_count > 0:
        deps.stage_audit_event(
            "output_recovery_drop_unresolved_tool_use",
            {
                "dropped": tool_use_count,
                "iter": iteration,
                "recoveryAttempt": state.recovery_attempt,
            },
        )

    if filtered_blocks:
        messages.append({"role": "assistant", "content": filtered_blocks})
    messages.append({"role": "user", "content": "Continue."})
    state.recovery_attempt += 1
    deps.stage_audit_event(
        "output_recovery",
        {
            "iteration": iteration,
            "recoveryAttempt": state.recovery_attempt,
            "stop_reason": stop_reason_raw,
        },
    )
    return StopReasonDecision(kind="recover")


def is_context_overflow_error(code: str, message: str) -> bool:
    if code not in ("http_400", "http_413"):
        return False
    if code == "http_413":
        return True
    return bool(_CONTEXT_OVERFLOW_RE.search(message))


class ContextOverflowError(Exception):
    def __init__(self, http_code: str, upstream_message: str) -> None:
        super().__init__(f"Context overflow ({http_code}): {upstream_message}")
        self.name = "ContextOverflowError"
        self.http_code = http_code
        self.upstream_message = upstream_message


def sanitize_messages_for_llm(messages: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    if not messages:
        return []

    result = [_copy_message(message) for message in messages]
    _strip_orphaned_tool_use(result)
    _strip_orphaned_tool_result(result)

    filtered = [message for message in result if _has_content(message.get("content"))]
    while filtered and filtered[-1].get("role") == "assistant":
        filtered.pop()
    return filtered


def _copy_message(message: dict[str, Any]) -> dict[str, Any]:
    return deepcopy(message)


def _strip_orphaned_tool_use(messages: list[dict[str, Any]]) -> None:
    index = 0
    while index < len(messages):
        message = messages[index]
        content = message.get("content")
        if message.get("role") != "assistant" or not isinstance(content, list):
            index += 1
            continue

        tool_use_ids = {
            block.get("id")
            for block in content
            if isinstance(block, dict)
            and block.get("type") == "tool_use"
            and isinstance(block.get("id"), str)
        }
        if not tool_use_ids:
            index += 1
            continue

        matched_ids: set[str] = set()
        next_message = messages[index + 1] if index + 1 < len(messages) else None
        if next_message and next_message.get("role") == "user":
            next_content = next_message.get("content")
            if isinstance(next_content, list):
                for block in next_content:
                    if not isinstance(block, dict) or block.get("type") != "tool_result":
                        continue
                    tool_use_id = block.get("tool_use_id")
                    if isinstance(tool_use_id, str) and tool_use_id in tool_use_ids:
                        matched_ids.add(tool_use_id)

        if len(matched_ids) < len(tool_use_ids):
            message["content"] = [
                block
                for block in content
                if not (
                    isinstance(block, dict)
                    and block.get("type") == "tool_use"
                    and block.get("id") not in matched_ids
                )
            ]

        if not message["content"]:
            del messages[index]
            continue
        index += 1


def _strip_orphaned_tool_result(messages: list[dict[str, Any]]) -> None:
    index = 0
    while index < len(messages):
        message = messages[index]
        content = message.get("content")
        if message.get("role") != "user" or not isinstance(content, list):
            index += 1
            continue
        has_tool_result = any(
            isinstance(block, dict) and block.get("type") == "tool_result"
            for block in content
        )
        if not has_tool_result:
            index += 1
            continue

        prev_message = messages[index - 1] if index > 0 else None
        prev_tool_ids: set[str] = set()
        if prev_message and prev_message.get("role") == "assistant":
            prev_content = prev_message.get("content")
            if isinstance(prev_content, list):
                prev_tool_ids = {
                    block.get("id")
                    for block in prev_content
                    if isinstance(block, dict)
                    and block.get("type") == "tool_use"
                    and isinstance(block.get("id"), str)
                }

        seen_result_ids: set[str] = set()
        stripped_content: list[Any] = []
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                stripped_content.append(block)
                continue

            tool_use_id = block.get("tool_use_id")
            if not isinstance(tool_use_id, str) or tool_use_id not in prev_tool_ids:
                continue
            if tool_use_id in seen_result_ids:
                continue
            seen_result_ids.add(tool_use_id)
            stripped_content.append(block)

        message["content"] = stripped_content
        if not message["content"]:
            del messages[index]
            continue
        index += 1


def _has_content(content: object) -> bool:
    if isinstance(content, str):
        return len(content) > 0
    if isinstance(content, list):
        return len(content) > 0
    return content is not None
