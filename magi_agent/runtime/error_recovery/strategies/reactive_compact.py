from __future__ import annotations

import json
from typing import Protocol, runtime_checkable

from magi_agent.runtime.error_recovery.types import (
    ErrorKind,
    ErrorRecoveryConfig,
    MessageDict,
    RecoverableError,
    RecoveryAttemptState,
    RecoveryContext,
    RecoveryResult,
)

from ._token_utils import _estimate_tokens

__all__ = ["LLMCompactCaller", "ReactiveCompactStrategy", "StubLLMCompactCaller"]

_COMPACTION_PROMPT = (
    "Summarize the following conversation for context continuity. "
    "Preserve: key decisions, file paths mentioned, code changes made, "
    "tool results that affect current state, and any pending tasks. "
    "Be concise but complete. Output only the summary."
)


@runtime_checkable
class LLMCompactCaller(Protocol):
    """Protocol for LLM calls used in compaction. Injectable for testing."""

    async def compact(self, messages_text: str, prompt: str) -> str:
        """Call an LLM with the compaction prompt and return the summary text."""
        ...


class StubLLMCompactCaller:
    """Default stub that returns a placeholder summary. Used when no real LLM is available."""

    async def compact(self, messages_text: str, prompt: str) -> str:
        return f"[Compacted summary of {len(messages_text)} chars of conversation]"


class ReactiveCompactStrategy:
    """LLM-based conversation compaction for prompt_too_long errors.

    Uses a classifier-tier model (Haiku) to summarize the conversation,
    replacing older messages with a compact summary. This is expensive
    (requires an LLM call) so it's used only after cheaper strategies fail.
    """

    def __init__(
        self,
        config: ErrorRecoveryConfig,
        llm_caller: LLMCompactCaller | None = None,
    ) -> None:
        self._config = config
        self._llm_caller: LLMCompactCaller = llm_caller or StubLLMCompactCaller()

    @property
    def name(self) -> str:
        return "reactive_compact"

    def applies_to(self, error: RecoverableError) -> bool:
        return error.kind == ErrorKind.PROMPT_TOO_LONG

    async def recover(
        self,
        context: RecoveryContext,
        state: RecoveryAttemptState | None = None,
    ) -> RecoveryResult:
        # Guard: max 1 compact per turn
        if state is not None and state.compact_attempted:
            return RecoveryResult(success=False, strategy_name=self.name)

        messages = list(context.messages)

        # Need at least 2 messages to compact (something to summarize + current request)
        if len(messages) < 2:
            return RecoveryResult(success=False, strategy_name=self.name)

        # Split: messages to summarize vs last message (current request)
        to_summarize = messages[:-1]
        last_message = messages[-1]

        original_tokens = _estimate_tokens(to_summarize)

        # Build text representation for the LLM
        messages_text = json.dumps(to_summarize, default=str)

        try:
            summary = await self._llm_caller.compact(messages_text, _COMPACTION_PROMPT)
        except Exception:
            return RecoveryResult(success=False, strategy_name=self.name)

        # Build compacted messages
        summary_message: MessageDict = {
            "role": "user",
            "content": f"[Conversation Summary]\n{summary}",
        }
        modified_messages: list[MessageDict] = [summary_message, last_message]

        compacted_tokens = _estimate_tokens([summary_message])
        tokens_freed = max(0, original_tokens - compacted_tokens)

        return RecoveryResult(
            success=True,
            strategy_name=self.name,
            modified_messages=modified_messages,
            tokens_freed=tokens_freed,
        )
