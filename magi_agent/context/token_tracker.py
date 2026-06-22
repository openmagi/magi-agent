from __future__ import annotations

from magi_agent.context._token_window_table import _KNOWN_TOKEN_LIMITS
from magi_agent.context.types import (
    ContextManagementConfig,
    TokenBudgetSnapshot,
    TrackedMessage,
    WarningLevel,
)
from magi_agent.shared.token_estimation import estimate_message_tokens

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
