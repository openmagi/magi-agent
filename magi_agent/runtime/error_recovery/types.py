from __future__ import annotations

import os
from enum import Enum
from typing import Literal, Protocol, TypeAlias, runtime_checkable

from pydantic import BaseModel, ConfigDict

from magi_agent.shared.types import TokenBudgetSnapshot

# TypeAlias for LLM message dicts — avoids raw `Any` throughout this module.
MessageDict: TypeAlias = dict[str, object]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)


class ErrorKind(str, Enum):
    PROMPT_TOO_LONG = "prompt_too_long"
    MAX_OUTPUT_TOKENS = "max_output_tokens"
    MEDIA_SIZE = "media_size"
    RATE_LIMIT = "rate_limit"
    UNRECOVERABLE = "unrecoverable"


class RecoverableError(BaseModel):
    """Classified error that has a recovery path."""

    model_config = _MODEL_CONFIG

    kind: ErrorKind
    original_error: str
    http_status: int | None = None
    tokens_over: int | None = None
    # OpenCode-style Retry-After honoring: when an upstream 429 carries a
    # Retry-After / retry-after-ms hint, the parsed delay (in seconds, float)
    # is threaded here so RateLimitStrategy waits the *server-requested* delay
    # instead of (or capped against) its blind exponential backoff. ``None``
    # means no hint was present -> fall back to exponential backoff.
    retry_after_seconds: float | None = None


class TerminalError(BaseModel):
    """Classified error with no recovery path."""

    model_config = _MODEL_CONFIG

    kind: Literal[ErrorKind.UNRECOVERABLE] = ErrorKind.UNRECOVERABLE
    original_error: str
    http_status: int | None = None


@runtime_checkable
class RecoveryStrategy(Protocol):
    """Protocol for pluggable recovery strategies.

    Note: ``isinstance()`` with ``@runtime_checkable`` only checks that the
    ``recover`` attribute exists — it does **not** verify that it is a
    coroutine function.  Implementations MUST define ``recover`` with
    ``async def``; a synchronous ``recover`` will pass the isinstance check
    but fail at call-site when awaited.
    """

    @property
    def name(self) -> str: ...

    async def recover(self, context: RecoveryContext, state: RecoveryAttemptState | None = None) -> RecoveryResult: ...

    def applies_to(self, error: RecoverableError) -> bool: ...


class RecoveryContext(BaseModel):
    """Immutable context passed to recovery strategies."""

    model_config = _MODEL_CONFIG

    error: RecoverableError
    messages: list[MessageDict]
    attempt: int = 0
    max_attempts: int = 3
    previous_strategies: tuple[str, ...] = ()
    session_key: str
    turn_id: str
    token_budget: TokenBudgetSnapshot | None = None


class RecoveryResult(BaseModel):
    """Result of a recovery attempt."""

    model_config = _MODEL_CONFIG

    success: bool
    strategy_name: str
    modified_messages: list[MessageDict] | None = None
    tokens_freed: int = 0
    retry_with_config: dict[str, object] | None = None


class RecoveryAttemptState(BaseModel):
    """Immutable state passed between recovery attempts."""

    model_config = _MODEL_CONFIG

    attempt_number: int = 0
    strategies_tried: tuple[str, ...] = ()
    total_tokens_freed: int = 0
    collapse_attempted: bool = False
    compact_attempted: bool = False
    escalation_attempted: bool = False
    recovery_messages_sent: int = 0


class ErrorRecoveryConfig(BaseModel):
    """Configuration for error recovery -- loaded from env vars."""

    model_config = _MODEL_CONFIG

    recovery_enabled: bool = False
    max_recovery_attempts: int = 3
    max_collapse_fraction: float = 0.2
    max_output_tokens_escalation: int = 65536
    rate_limit_max_retries: int = 3
    rate_limit_base_delay_seconds: float = 1.0

    @classmethod
    def from_env(cls) -> ErrorRecoveryConfig:
        """Construct config from environment variables with safe defaults."""
        enabled = os.environ.get("MAGI_ERROR_RECOVERY_ENABLED", "").lower() in ("1", "true")
        try:
            max_attempts = int(os.environ.get("MAGI_MAX_RECOVERY_ATTEMPTS", "3"))
        except ValueError:
            max_attempts = 3
        return cls(
            recovery_enabled=enabled,
            max_recovery_attempts=max_attempts,
        )


__all__ = [
    "ErrorKind",
    "ErrorRecoveryConfig",
    "MessageDict",
    "RecoverableError",
    "RecoveryAttemptState",
    "RecoveryContext",
    "RecoveryResult",
    "RecoveryStrategy",
    "TerminalError",
    "TokenBudgetSnapshot",
]
