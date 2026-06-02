from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol

from openmagi_core_agent.runtime.error_recovery.classifier import ErrorClassifier
from openmagi_core_agent.runtime.error_recovery.engine import RecoveryEngine
from openmagi_core_agent.runtime.error_recovery.types import (
    ErrorRecoveryConfig,
    MessageDict,
    RecoverableError,
    RecoveryAttemptState,
    TerminalError,
)

__all__ = ["ResilientRunnerSessionBoundary"]


class _InnerBoundary(Protocol):
    """Protocol for the inner boundary that ResilientRunnerSessionBoundary wraps."""

    async def run_turn(self, **kwargs: object) -> dict[str, object]: ...


class ResilientRunnerSessionBoundary:
    """Wraps a RunnerSessionBoundary with error recovery capabilities.

    This is a WRAPPER, not a subclass. It delegates to the inner boundary
    and adds try/except recovery around the call.

    **Architectural note on ``config_overrides``:**
    Recovery strategies like ``OutputEscalationStrategy`` may return
    ``retry_with_config`` (e.g. ``{"max_tokens": 65536}``).  Because
    ``RunnerSessionBoundary.run_turn()`` does not accept LLM-level
    parameters directly (it takes ``TurnControllerInput`` /
    ``RunnerSessionBoundaryConfig``), this wrapper **cannot** apply the
    overrides itself.  Instead it accumulates them in
    ``config_overrides`` so the *caller* (whoever constructs the LLM
    request) can inspect and apply them before the next call.
    """

    def __init__(
        self,
        inner: _InnerBoundary,
        engine: RecoveryEngine,
        config: ErrorRecoveryConfig,
    ) -> None:
        self._inner = inner
        self._engine = engine
        self._config = config
        self._evidence: list[dict[str, object]] = []
        self._config_overrides: dict[str, object] = {}

    @property
    def evidence(self) -> list[dict[str, object]]:
        """Recovery attempt evidence records."""
        return list(self._evidence)

    @property
    def config_overrides(self) -> dict[str, object]:
        """Accumulated config overrides from recovery strategies.

        Callers should inspect this after ``run_turn()`` returns and
        apply the overrides (e.g. ``max_tokens``) before the next LLM
        request.  The boundary wrapper itself cannot modify the inner
        runner's LLM parameters between retries.
        """
        return dict(self._config_overrides)

    async def run_turn(
        self,
        messages: list[MessageDict],
        session_key: str,
        turn_id: str,
        **kwargs: object,
    ) -> dict[str, object]:
        """Run a turn with automatic error recovery.

        1. If recovery_enabled is False, delegate directly.
        2. Try inner.run_turn().
        3. On error: classify, attempt recovery, retry if recoverable.
        4. Record evidence for every recovery attempt.
        5. Bounded by max_recovery_attempts.
        """
        if not self._config.recovery_enabled:
            return await self._inner.run_turn(
                messages=messages,
                session_key=session_key,
                turn_id=turn_id,
                **kwargs,
            )

        current_messages = messages
        state: RecoveryAttemptState | None = None
        attempt = 0

        while True:
            result = await self._inner.run_turn(
                messages=current_messages,
                session_key=session_key,
                turn_id=turn_id,
                **kwargs,
            )

            # Check for error in result
            if not _is_error_result(result):
                return result

            # Classify the error
            error_text = str(result.get("error", ""))
            classified = ErrorClassifier.classify(error_text)

            # Terminal errors are not retried
            if isinstance(classified, TerminalError):
                return result

            # Check retry budget
            if attempt >= self._config.max_recovery_attempts:
                return result

            # Attempt recovery
            assert isinstance(classified, RecoverableError)
            recovery_result, new_state = await self._engine.attempt_recovery(
                error=classified,
                messages=current_messages,
                session_key=session_key,
                turn_id=turn_id,
                state=state,
            )

            attempt += 1

            # Accumulate config overrides from strategy
            config_overrides: dict[str, object] | None = None
            if recovery_result.retry_with_config:
                self._config_overrides.update(recovery_result.retry_with_config)
                config_overrides = dict(recovery_result.retry_with_config)

            # Record evidence
            self._evidence.append(
                _make_evidence(
                    error_kind=classified.kind.value,
                    strategy=recovery_result.strategy_name,
                    attempt=attempt,
                    success=recovery_result.success,
                    tokens_freed=recovery_result.tokens_freed,
                    config_overrides=config_overrides,
                )
            )

            if not recovery_result.success:
                return result

            # Update messages for retry
            if recovery_result.modified_messages is not None:
                current_messages = recovery_result.modified_messages

            state = new_state


def _is_error_result(result: dict[str, object]) -> bool:
    """Check if a run_turn result represents an error."""
    return result.get("status") == "error"


def _make_evidence(
    *,
    error_kind: str,
    strategy: str,
    attempt: int,
    success: bool,
    tokens_freed: int,
    config_overrides: dict[str, object] | None = None,
) -> dict[str, object]:
    """Create an evidence record for a recovery attempt."""
    record: dict[str, object] = {
        "type": "ErrorRecovery",
        "error_kind": error_kind,
        "strategy": strategy,
        "attempt": attempt,
        "success": success,
        "tokens_freed": tokens_freed,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if config_overrides:
        record["config_overrides"] = config_overrides
    return record
