from __future__ import annotations

import asyncio

from magi_agent.runtime.error_recovery.types import (
    ErrorKind,
    ErrorRecoveryConfig,
    RecoverableError,
    RecoveryAttemptState,
    RecoveryContext,
    RecoveryResult,
)

__all__ = ["RateLimitStrategy"]


class RateLimitStrategy:
    """Wait with exponential backoff on rate limit errors."""

    def __init__(self, config: ErrorRecoveryConfig) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "rate_limit"

    def applies_to(self, error: RecoverableError) -> bool:
        return error.kind == ErrorKind.RATE_LIMIT

    async def recover(
        self,
        context: RecoveryContext,
        state: RecoveryAttemptState | None = None,
    ) -> RecoveryResult:
        if context.attempt >= self._config.rate_limit_max_retries:
            return RecoveryResult(
                success=False,
                strategy_name=self.name,
            )

        delay = self._config.rate_limit_base_delay_seconds * (2 ** context.attempt)
        delay = min(delay, 60.0)
        await asyncio.sleep(delay)

        return RecoveryResult(
            success=True,
            strategy_name=self.name,
            modified_messages=list(context.messages),
        )
