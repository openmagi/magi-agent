from __future__ import annotations
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from magi_agent.context.types import WarningLevel
from magi_agent.context.protected_tools import is_compaction_protected_tool_result

MIN_RESULT_TOKENS_FOR_COMPACT = 2_000
SUMMARY_MAX_WORDS = 200

ClassifierCallable = Callable[[str], Awaitable[str]]


@dataclass(frozen=True)
class MicrocompactResult:
    messages_processed: int
    messages_compacted: int
    cache_hits: int
    tokens_freed: int


class MicrocompactEngine:
    """Tier 4: LLM-based per-tool-result compression using classifier model (e.g., Haiku).

    Caches summaries by tool_use_id to avoid re-summarizing.
    Only activates at WarningLevel >= HIGH.
    """

    SUMMARY_PROMPT = (
        "Summarize this tool output in under 200 words. "
        "Preserve key data, file paths, error messages, and numbers.\n\n{content}"
    )

    def __init__(self, classifier: ClassifierCallable, cache: dict[str, str] | None = None):
        self._classifier = classifier
        self._cache: dict[str, str] = cache if cache is not None else {}

    @property
    def cache(self) -> dict[str, str]:
        return self._cache

    def clear_cache(self) -> None:
        self._cache.clear()

    async def apply(
        self,
        messages: list[dict],
        warning_level: WarningLevel,
    ) -> tuple[list[dict], MicrocompactResult]:
        if warning_level not in (WarningLevel.HIGH, WarningLevel.CRITICAL):
            return messages, MicrocompactResult(
                messages_processed=0, messages_compacted=0, cache_hits=0, tokens_freed=0
            )

        result: list[dict] = []
        compacted = 0
        cache_hits = 0
        tokens_freed = 0
        processed = 0

        for msg in messages:
            if not self._is_tool_result(msg):
                result.append(msg)
                continue

            # Compaction-protected tool results (e.g. loaded GA playbook bodies)
            # are preserved verbatim, mirroring OpenCode PRUNE_PROTECTED_TOOLS.
            # No-op for any non-protected tool result.
            if is_compaction_protected_tool_result(msg):
                result.append(msg)
                continue

            processed += 1
            tool_use_id = self._extract_tool_use_id(msg)
            original_tokens = self._estimate_tokens(msg)

            # Check cache first
            if tool_use_id and tool_use_id in self._cache:
                new_msg = self._replace_content(msg, self._cache[tool_use_id])
                new_tokens = self._estimate_tokens(new_msg)
                tokens_freed += original_tokens - new_tokens
                cache_hits += 1
                compacted += 1
                result.append(new_msg)
                continue

            # Skip small results
            if original_tokens < MIN_RESULT_TOKENS_FOR_COMPACT:
                result.append(msg)
                continue

            # LLM summarize
            content_str = self._extract_content_str(msg)
            try:
                summary = await self._classifier(
                    self.SUMMARY_PROMPT.format(content=content_str)
                )
            except Exception:
                # Fail open — keep original on classifier failure
                result.append(msg)
                continue

            if tool_use_id:
                self._cache[tool_use_id] = summary

            new_msg = self._replace_content(msg, summary)
            new_tokens = self._estimate_tokens(new_msg)
            tokens_freed += original_tokens - new_tokens
            compacted += 1
            result.append(new_msg)

        return result, MicrocompactResult(
            messages_processed=processed,
            messages_compacted=compacted,
            cache_hits=cache_hits,
            tokens_freed=tokens_freed,
        )

    @staticmethod
    def _is_tool_result(msg: dict) -> bool:
        return msg.get("role") == "tool" or msg.get("type") == "tool_result"

    @staticmethod
    def _extract_tool_use_id(msg: dict) -> str | None:
        return msg.get("tool_use_id") or msg.get("tool_call_id")

    @staticmethod
    def _extract_content_str(msg: dict) -> str:
        content = msg.get("content", "")
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif isinstance(block, str):
                    parts.append(block)
            return "\n".join(parts)
        return str(content)

    @staticmethod
    def _replace_content(msg: dict, new_content: str) -> dict:
        result = dict(msg)
        result["content"] = new_content
        return result

    @staticmethod
    def _estimate_tokens(msg: dict) -> int:
        return len(json.dumps(msg, default=str)) // 4
