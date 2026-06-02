from __future__ import annotations
import json
from dataclasses import dataclass
from magi_agent.context.types import WarningLevel

MAX_RESULT_TOKENS = 100_000  # Per tool result budget
HEAD_RATIO = 0.25
TAIL_RATIO = 0.25


@dataclass(frozen=True)
class SnipResult:
    messages_processed: int
    messages_snipped: int
    tokens_freed: int


class ContentReplacer:
    """Tier 2-3: Content replacement and snip compaction for oversized tool results."""

    def apply(
        self,
        messages: list[dict],
        warning_level: WarningLevel,
    ) -> tuple[list[dict], SnipResult]:
        """Apply content replacement and snip. Only activates at MODERATE or above.

        Returns (modified_messages, result_stats).
        Pure function — no side effects, no LLM calls.
        """
        if warning_level == WarningLevel.NORMAL:
            return messages, SnipResult(messages_processed=0, messages_snipped=0, tokens_freed=0)

        result: list[dict] = []
        snipped = 0
        tokens_freed = 0
        processed = 0

        for msg in messages:
            if self._is_tool_result(msg):
                processed += 1
                original_tokens = self._estimate_tokens(msg)
                if original_tokens > MAX_RESULT_TOKENS:
                    snipped_msg = self._snip(msg)
                    new_tokens = self._estimate_tokens(snipped_msg)
                    freed = original_tokens - new_tokens
                    if freed > 0:
                        tokens_freed += freed
                        snipped += 1
                        msg = snipped_msg
            result.append(msg)

        return result, SnipResult(
            messages_processed=processed,
            messages_snipped=snipped,
            tokens_freed=tokens_freed,
        )

    @staticmethod
    def _is_tool_result(msg: dict) -> bool:
        return msg.get("role") == "tool" or msg.get("type") == "tool_result"

    @staticmethod
    def _estimate_tokens(msg: dict) -> int:
        return len(json.dumps(msg, default=str)) // 4

    @staticmethod
    def _snip(msg: dict) -> dict:
        content = msg.get("content", "")
        if isinstance(content, list):
            # Handle structured content (list of content blocks)
            # Guard: if ANY block is a dict with type != "text", return unchanged
            # (don't snip mixed-type structured content to prevent losing image/other blocks)
            for block in content:
                if isinstance(block, dict) and block.get("type") is not None:
                    if block.get("type") != "text":
                        return msg

            text_parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
                elif isinstance(block, str):
                    text_parts.append(block)
            content_str = "\n".join(text_parts)
        elif isinstance(content, str):
            content_str = content
        else:
            content_str = str(content)

        lines = content_str.split("\n")
        total = len(lines)
        if total <= 10:
            return msg  # Too short to snip

        keep_head = max(1, int(total * HEAD_RATIO))
        keep_tail = max(1, int(total * TAIL_RATIO))
        snipped_count = total - keep_head - keep_tail

        if snipped_count <= 0:
            return msg

        snipped_lines = (
            lines[:keep_head]
            + [f"[... {snipped_count} lines snipped ...]"]
            + lines[-keep_tail:]
        )
        new_content = "\n".join(snipped_lines)

        result = dict(msg)
        result["content"] = new_content
        return result
