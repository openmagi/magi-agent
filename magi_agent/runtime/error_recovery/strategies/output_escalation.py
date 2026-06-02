from __future__ import annotations

from magi_agent.runtime.error_recovery.types import (
    ErrorKind,
    ErrorRecoveryConfig,
    RecoverableError,
    RecoveryAttemptState,
    RecoveryContext,
    RecoveryResult,
)

__all__ = ["OutputEscalationStrategy"]


class OutputEscalationStrategy:
    """Increase max_tokens when output was truncated."""

    def __init__(self, config: ErrorRecoveryConfig) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "output_escalation"

    def applies_to(self, error: RecoverableError) -> bool:
        return error.kind == ErrorKind.MAX_OUTPUT_TOKENS

    async def recover(
        self,
        context: RecoveryContext,
        state: RecoveryAttemptState | None = None,
    ) -> RecoveryResult:
        if state is not None and state.escalation_attempted:
            return RecoveryResult(
                success=False,
                strategy_name=self.name,
            )

        return RecoveryResult(
            success=True,
            strategy_name=self.name,
            modified_messages=list(context.messages),
            retry_with_config={"max_tokens": self._config.max_output_tokens_escalation},
        )
