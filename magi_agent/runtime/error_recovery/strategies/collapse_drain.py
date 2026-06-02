from __future__ import annotations

import math

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

__all__ = ["CollapseDrainStrategy"]


class CollapseDrainStrategy:
    """Drop oldest tool_result rounds to free context tokens.

    Partitions messages into API rounds (user + assistant + optional tool_results),
    then drops at least 1, up to max_collapse_fraction of total rounds (never the
    first or last round).
    """

    def __init__(self, config: ErrorRecoveryConfig) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "collapse_drain"

    def applies_to(self, error: RecoverableError) -> bool:
        return error.kind == ErrorKind.PROMPT_TOO_LONG

    async def recover(
        self,
        context: RecoveryContext,
        state: RecoveryAttemptState | None = None,
    ) -> RecoveryResult:
        if state is not None and state.collapse_attempted:
            return RecoveryResult(
                success=False,
                strategy_name=self.name,
            )

        messages = list(context.messages)
        if not messages:
            return RecoveryResult(success=False, strategy_name=self.name)

        rounds = _partition_into_rounds(messages)
        if len(rounds) <= 2:
            # Only first + last round — nothing safe to drop
            return RecoveryResult(success=False, strategy_name=self.name)

        # Droppable = everything except first and last round
        droppable = rounds[1:-1]
        n_to_drop = max(1, math.ceil(len(rounds) * self._config.max_collapse_fraction))
        n_to_drop = min(n_to_drop, len(droppable))

        dropped_msgs: list[MessageDict] = []
        for r in droppable[:n_to_drop]:
            dropped_msgs.extend(r)

        tokens_freed = _estimate_tokens(dropped_msgs)

        kept_rounds = [rounds[0]] + droppable[n_to_drop:] + [rounds[-1]]
        modified: list[MessageDict] = []
        for r in kept_rounds:
            modified.extend(r)

        return RecoveryResult(
            success=True,
            strategy_name=self.name,
            modified_messages=modified,
            tokens_freed=tokens_freed,
        )


def _partition_into_rounds(messages: list[MessageDict]) -> list[list[MessageDict]]:
    """Split messages into rounds starting at each ``user`` message."""
    rounds: list[list[MessageDict]] = []
    current: list[MessageDict] = []
    for msg in messages:
        role = msg.get("role")
        if role == "user" and current:
            rounds.append(current)
            current = []
        current.append(msg)
    if current:
        rounds.append(current)
    return rounds
