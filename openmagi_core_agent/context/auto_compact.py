from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from openmagi_core_agent.context.types import WarningLevel

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
        tokens_after = self._estimate_tokens(summary_msg)

        turns_summarized = self._count_turns(old_messages)

        return [summary_msg] + recent_messages, AutoCompactResult(
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
        parts: list[str] = []
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
            parts.append(f"[{role}]: {content[:2000]}")  # Cap per-message for summary prompt
        return "\n\n".join(parts)

    @staticmethod
    def _count_turns(messages: list[dict]) -> int:
        return sum(1 for m in messages if m.get("role") == "user")

    @staticmethod
    def _estimate_tokens(msg: dict) -> int:
        return len(json.dumps(msg, default=str)) // 4
