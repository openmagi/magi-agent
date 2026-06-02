from __future__ import annotations

from openmagi_core_agent.runtime.error_recovery.strategies import (
    CollapseDrainStrategy,
    MediaRemovalStrategy,
    OutputEscalationStrategy,
    RateLimitStrategy,
    ReactiveCompactStrategy,
    RecoveryMessageStrategy,
)
from openmagi_core_agent.runtime.error_recovery.types import (
    ErrorRecoveryConfig,
    MessageDict,
    RecoverableError,
    RecoveryAttemptState,
    RecoveryContext,
    RecoveryResult,
    RecoveryStrategy,
)

__all__ = ["DEFAULT_STRATEGIES", "RecoveryEngine"]

_DEFAULT_CONFIG = ErrorRecoveryConfig(recovery_enabled=True)

DEFAULT_STRATEGIES: tuple[RecoveryStrategy, ...] = (
    RateLimitStrategy(_DEFAULT_CONFIG),
    OutputEscalationStrategy(_DEFAULT_CONFIG),
    CollapseDrainStrategy(_DEFAULT_CONFIG),
    ReactiveCompactStrategy(_DEFAULT_CONFIG),
    MediaRemovalStrategy(_DEFAULT_CONFIG),
    RecoveryMessageStrategy(_DEFAULT_CONFIG),
)


class RecoveryEngine:
    """Orchestrates recovery attempts using classified errors and strategies.

    Strategy priority order:
    1. RateLimitStrategy (for rate_limit errors)
    2. OutputEscalationStrategy (for max_output_tokens, try first)
    3. CollapseDrainStrategy (for prompt_too_long, cheap)
    4. ReactiveCompactStrategy (for prompt_too_long, expensive LLM-based)
    5. MediaRemovalStrategy (for media_size)
    6. RecoveryMessageStrategy (for max_output_tokens, after escalation)
    """

    def __init__(
        self,
        config: ErrorRecoveryConfig,
        strategies: tuple[RecoveryStrategy, ...] | None = None,
    ) -> None:
        self._config = config
        if strategies is not None:
            self._strategies = strategies
        else:
            self._strategies = (
                RateLimitStrategy(config),
                OutputEscalationStrategy(config),
                CollapseDrainStrategy(config),
                ReactiveCompactStrategy(config),
                MediaRemovalStrategy(config),
                RecoveryMessageStrategy(config),
            )

    async def attempt_recovery(
        self,
        error: RecoverableError,
        messages: list[MessageDict],
        session_key: str,
        turn_id: str,
        state: RecoveryAttemptState | None = None,
    ) -> tuple[RecoveryResult, RecoveryAttemptState]:
        """Try each applicable strategy in order until one succeeds.

        Returns ``(result, updated_state)``.
        """
        current_state = state or RecoveryAttemptState()
        attempt_number = current_state.attempt_number + 1
        strategies_tried = list(current_state.strategies_tried)
        total_tokens_freed = current_state.total_tokens_freed

        for strategy in self._strategies:
            if not strategy.applies_to(error):
                continue

            context = RecoveryContext(
                error=error,
                messages=messages,
                attempt=attempt_number - 1,
                max_attempts=self._config.max_recovery_attempts,
                previous_strategies=tuple(strategies_tried),
                session_key=session_key,
                turn_id=turn_id,
            )

            result = await strategy.recover(context, current_state)
            strategies_tried.append(strategy.name)
            total_tokens_freed += result.tokens_freed

            if result.success:
                new_state = current_state.model_copy(
                    update={
                        "attempt_number": attempt_number,
                        "strategies_tried": tuple(strategies_tried),
                        "total_tokens_freed": total_tokens_freed,
                        "collapse_attempted": current_state.collapse_attempted or strategy.name == "collapse_drain",
                        "compact_attempted": current_state.compact_attempted or strategy.name == "reactive_compact",
                        "escalation_attempted": current_state.escalation_attempted or strategy.name == "output_escalation",
                        "recovery_messages_sent": (
                            current_state.recovery_messages_sent + (1 if strategy.name == "recovery_message" else 0)
                        ),
                    }
                )
                return result, new_state

        # All strategies exhausted — return failure
        final_state = current_state.model_copy(
            update={
                "attempt_number": attempt_number,
                "strategies_tried": tuple(strategies_tried),
                "total_tokens_freed": total_tokens_freed,
            }
        )
        return (
            RecoveryResult(
                success=False,
                strategy_name="none",
            ),
            final_state,
        )
