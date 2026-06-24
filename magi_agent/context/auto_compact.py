from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from magi_agent.context.protected_tools import is_compaction_protected_tool_result
from magi_agent.context.types import WarningLevel


def _is_tool_result(msg: dict) -> bool:
    """Return True only for genuine tool-result messages.

    Mirrors MicrocompactEngine._is_tool_result so that the protection filter
    in AutoCompactionEngine applies the same role-gate: only messages with
    role='tool' (ADK / OpenAI) or type='tool_result' (Anthropic) can be
    recognized as protected tool results.  This prevents an arbitrary message
    that merely carries a matching ``name`` field from being preserved verbatim.
    """
    return msg.get("role") == "tool" or msg.get("type") == "tool_result"

KEEP_RECENT_TURNS = 3
ClassifierCallable = Callable[[str], Awaitable[str]]


@dataclass(frozen=True)
class AutoCompactResult:
    activated: bool
    turns_summarized: int
    boundary_id: str | None
    tokens_before: int
    tokens_after: int


class AutoCompactionEngine:
    """Tier 5: Full LLM-based session summary.

    Summarizes all messages before a boundary using the classifier model.
    Only activates at WarningLevel == CRITICAL.
    """

    SUMMARY_PROMPT = (
        "Summarize this conversation history concisely. "
        "Preserve: key decisions, important data, file paths, "
        "error messages, and user requests. "
        "Omit: repetitive tool outputs, intermediate steps, "
        "and debugging details.\n\n{conversation}"
    )

    # G5: anchored/incremental refinement variant (adapted from OpenCode's
    # incremental-summary principle, NOT copied). When a prior anchored summary
    # exists it is fed back so the model UPDATES/MERGES it instead of
    # re-summarizing from scratch — bounding cost and preventing drift.
    ANCHORED_SUMMARY_PROMPT = (
        "You are maintaining a running anchored summary of a long conversation. "
        "Update the anchored summary below using the new conversation history. "
        "Preserve still-true details, remove stale details, and merge in new "
        "facts. Keep it concise. Preserve: key decisions, important data, file "
        "paths, error messages, and user requests. Omit: repetitive tool outputs, "
        "intermediate steps, debugging details.\n\n"
        "<previous_summary>\n{anchor}\n</previous_summary>\n\n"
        "<new_history>\n{conversation}\n</new_history>"
    )

    def __init__(
        self,
        classifier: ClassifierCallable,
        keep_recent_turns: int = KEEP_RECENT_TURNS,
    ) -> None:
        self._classifier = classifier
        self._keep_recent_turns = keep_recent_turns

    async def apply(
        self,
        messages: list[dict],
        warning_level: WarningLevel,
    ) -> tuple[list[dict], AutoCompactResult]:
        if warning_level != WarningLevel.CRITICAL:
            return messages, AutoCompactResult(
                activated=False,
                turns_summarized=0,
                boundary_id=None,
                tokens_before=0,
                tokens_after=0,
            )

        boundary_idx = self._find_boundary(messages)
        if boundary_idx <= 0:
            # Nothing to compact (all messages are "recent")
            return messages, AutoCompactResult(
                activated=False,
                turns_summarized=0,
                boundary_id=None,
                tokens_before=0,
                tokens_after=0,
            )

        old_messages = messages[:boundary_idx]
        recent_messages = messages[boundary_idx:]

        # Compaction-protected tool results in the OLD region are preserved
        # verbatim instead of being summarized away (mirrors OpenCode
        # PRUNE_PROTECTED_TOOLS). No-op when none are present.
        # The role-gate (_is_tool_result) ensures that only genuine tool-result
        # messages (role=tool / type=tool_result) can be recognized as protected,
        # consistent with MicrocompactEngine and as a defense-in-depth measure
        # against arbitrary messages that merely carry a matching name field.
        protected_messages = [
            m for m in old_messages
            if _is_tool_result(m) and is_compaction_protected_tool_result(m)
        ]

        tokens_before = sum(self._estimate_tokens(m) for m in old_messages)

        # Summarize old messages
        conversation_text = self._format_conversation(old_messages)
        try:
            summary = await self._classifier(
                self.SUMMARY_PROMPT.format(conversation=conversation_text)
            )
        except Exception:
            # Fail open
            return messages, AutoCompactResult(
                activated=False,
                turns_summarized=0,
                boundary_id=None,
                tokens_before=0,
                tokens_after=0,
            )

        boundary_id = uuid.uuid4().hex
        summary_msg = {
            "role": "user",
            "content": f"[Previous conversation summary]\n\n{summary}",
        }
        tokens_after = self._estimate_tokens(summary_msg) + sum(
            self._estimate_tokens(m) for m in protected_messages
        )

        turns_summarized = self._count_turns(old_messages)

        # NOTE: re-attached protected messages (role=tool) no longer follow their
        # original assistant+tool_use pairing in the message list.  Before the
        # runner is wired to actually emit LoadGaPlaybook, the message-list
        # assembly must ensure protocol validity (e.g. wrap or re-pair the
        # tool result so the conversation remains well-formed for the LLM).
        return [summary_msg] + protected_messages + recent_messages, AutoCompactResult(
            activated=True,
            turns_summarized=turns_summarized,
            boundary_id=boundary_id,
            tokens_before=tokens_before,
            tokens_after=tokens_after,
        )

    def _find_boundary(self, messages: list[dict]) -> int:
        """Find the index that separates old messages from recent turns to keep.

        A "turn" starts with a user message. We keep the last N turns.
        """
        # Find user message indices (turn boundaries)
        turn_starts: list[int] = []
        for i, msg in enumerate(messages):
            if msg.get("role") == "user":
                turn_starts.append(i)

        if len(turn_starts) <= self._keep_recent_turns:
            return 0  # Not enough turns to compact

        # Keep the last N turns
        boundary = turn_starts[-self._keep_recent_turns]
        return boundary

    @staticmethod
    def _format_conversation(messages: list[dict]) -> str:
        # D-13: build a normalized segment per dict-message and route
        # through the shared transcript renderer so the live ADK path
        # (``adk_bridge/context_compaction._render_dropped_transcript``)
        # and this dormant dict-message path share the role-bracket/line-
        # join skeleton. Per-message 2000-char cap stays here (the
        # auto_compact contract); no total cap / truncation marker so
        # output is byte-identical to the pre-D-13 renderer. An empty
        # content still emits ``[role]: `` because we pack the empty
        # string as the only piece (the renderer joins pieces with
        # spaces and prefixes with the role bracket unconditionally).
        from magi_agent.context.transcript_render import (  # noqa: PLC0415
            NormalizedSegment,
            render_transcript,
        )

        segments: list[NormalizedSegment] = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if isinstance(content, list):
                text_parts = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                    elif isinstance(block, str):
                        text_parts.append(block)
                content = "\n".join(text_parts)
            elif not isinstance(content, str):
                content = str(content)
            segments.append(
                NormalizedSegment(role=role, pieces=(content[:2000],))
            )
        return render_transcript(segments)

    @staticmethod
    def _count_turns(messages: list[dict]) -> int:
        return sum(1 for m in messages if m.get("role") == "user")

    @staticmethod
    def _estimate_tokens(msg: dict) -> int:
        return len(json.dumps(msg, default=str)) // 4
