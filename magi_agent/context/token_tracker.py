from __future__ import annotations

from magi_agent.context.types import (
    ContextManagementConfig,
    TokenBudgetSnapshot,
    TrackedMessage,
    WarningLevel,
)
from magi_agent.shared.token_estimation import estimate_message_tokens

# Mirrors message_builder.py _KNOWN_TOKEN_LIMITS exactly
_KNOWN_TOKEN_LIMITS: dict[str, int] = {
    "claude-opus-4-6": 150_000,
    "claude-sonnet-4-6": 150_000,
    "claude-haiku-4-5-20251001": 150_000,
    "claude-haiku-4-5": 150_000,
    "anthropic/claude-opus-4-6": 150_000,
    "anthropic/claude-sonnet-4-6": 150_000,
    "anthropic/claude-haiku-4-5": 150_000,
    "openai/gpt-5.4-nano": 96_000,
    "gpt-5.4-nano": 96_000,
    "gpt-5-nano": 300_000,
    "gpt-5-mini": 300_000,
    "gpt-5.1": 300_000,
    "gpt-5.4": 300_000,
    "openai/gpt-5.4-mini": 96_000,
    "gpt-5.4-mini": 96_000,
    "openai/gpt-5.5": 750_000,
    "gpt-5.5": 750_000,
    "magi-smart-router/auto": 750_000,
    "big-dic-router/auto": 196_608,
    "openai/gpt-5.5-pro": 787_500,
    "openai-codex/gpt-5.5": 750_000,
    "fireworks/kimi-k2p6": 196_608,
    "kimi-k2p6": 192_000,
    "fireworks/minimax-m2p7": 147_456,
    "minimax-m2p7": 192_000,
    "google/gemini-3.5-flash": 786_432,
    "gemini-3.5-flash": 786_432,
    "google/gemini-3.1-flash-lite-preview": 786_432,
    "gemini-3.1-flash-lite-preview": 750_000,
    "google/gemini-3.1-pro-preview": 786_432,
    "gemini-3.1-pro-preview": 750_000,
    "local/gemma-fast": 98_304,
    "local/gemma-max": 98_304,
    "local/qwen-uncensored": 98_304,
}
_DEFAULT_CONTEXT_WINDOW = 150_000


class TokenBudgetTracker:
    """Tracks token usage across session messages and computes warning levels."""

    def __init__(
        self,
        model: str,
        config: ContextManagementConfig | None = None,
    ) -> None:
        self._model = model
        self._config = config or ContextManagementConfig()
        self._context_window = _KNOWN_TOKEN_LIMITS.get(model, _DEFAULT_CONTEXT_WINDOW)
        self._messages: list[TrackedMessage] = []

    @property
    def context_window(self) -> int:
        return self._context_window

    def add_message(
        self,
        message: dict,
        *,
        role: str = "",
        kind: str = "",
        tool_use_id: str | None = None,
    ) -> TrackedMessage:
        tokens = self.estimate_tokens(message)
        tracked = TrackedMessage(
            tokens=tokens,
            tool_use_id=tool_use_id,
            role=role,
            kind=kind,
        )
        self._messages.append(tracked)
        return tracked

    def snapshot(self) -> TokenBudgetSnapshot:
        total = sum(m.tokens for m in self._messages)
        util = total / self._context_window if self._context_window > 0 else 0.0
        return TokenBudgetSnapshot(
            context_window=self._context_window,
            total_tokens=total,
            utilization=util,
            warning_level=self._compute_warning_level(util),
            message_count=len(self._messages),
        )

    def reset(self) -> None:
        self._messages.clear()

    @staticmethod
    def estimate_tokens(message: dict) -> int:
        return estimate_message_tokens(message)

    def _compute_warning_level(self, utilization: float) -> WarningLevel:
        if utilization >= self._config.critical_threshold:
            return WarningLevel.CRITICAL
        if utilization >= self._config.high_threshold:
            return WarningLevel.HIGH
        if utilization >= self._config.moderate_threshold:
            return WarningLevel.MODERATE
        return WarningLevel.NORMAL
