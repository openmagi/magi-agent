from __future__ import annotations

from openmagi_core_agent.runtime.error_recovery.types import (
    ErrorKind,
    ErrorRecoveryConfig,
    MessageDict,
    RecoverableError,
    RecoveryAttemptState,
    RecoveryContext,
    RecoveryResult,
)

__all__ = ["RecoveryMessageStrategy"]

_RECOVERY_PROMPT = (
    "Your previous response was truncated. "
    "Resume directly from where you stopped. "
    "Do not repeat any content."
)


class RecoveryMessageStrategy:
    """Inject a resume prompt after output truncation."""

    def __init__(self, config: ErrorRecoveryConfig) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "recovery_message"

    def applies_to(self, error: RecoverableError) -> bool:
        return error.kind == ErrorKind.MAX_OUTPUT_TOKENS

    async def recover(
        self,
        context: RecoveryContext,
        state: RecoveryAttemptState | None = None,
    ) -> RecoveryResult:
        if state is not None and state.recovery_messages_sent >= 3:
            return RecoveryResult(
                success=False,
                strategy_name=self.name,
            )

        recovery_msg: MessageDict = {"role": "user", "content": _RECOVERY_PROMPT}
        modified = list(context.messages) + [recovery_msg]

        return RecoveryResult(
            success=True,
            strategy_name=self.name,
            modified_messages=modified,
        )
