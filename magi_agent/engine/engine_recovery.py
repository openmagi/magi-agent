"""Live retry / continuation / empty-response recovery helpers, pure move out of
engine/driver.py (PR-G3).

Builder factories for the recovery, output-continuation and empty-response
recovery configs, plus the zero-edit reprompt predicate, the continuation-event
classifier and the cleanup-error suppression context manager. Bodies are moved
verbatim (the only delta is N-34: ``_suppress_cancel`` renamed to
``_suppress_cleanup_errors`` with an honest docstring and a debug log when a
non-cancel exception is swallowed; suppression scope is behavior-preserved).
The driver re-imports every name and keeps a ``_suppress_cancel`` back-compat
alias.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from magi_agent.engine.event_projection import (
    ARTIFACT_EVENT_TYPES as _ARTIFACT_EVENT_TYPES,
    TOKEN_EVENT_TYPES as _TOKEN_EVENT_TYPES,
    TOOL_EVENT_TYPES as _TOOL_EVENT_TYPES,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from magi_agent.runtime.empty_response_recovery import EmptyResponseRecoveryConfig
    from magi_agent.runtime.error_recovery import RecoveryEngine
    from magi_agent.runtime.output_continuation import OutputContinuationConfig


@dataclass(frozen=True)
class EngineRecoveryPolicy:
    """Live retry policy for the run invocation (PR12 genuine recovery seam).

    Holds the EXISTING :class:`RecoveryEngine` (activation, not reimpl) plus the
    per-turn attempt budget. Passed to :class:`MagiEngineDriver`; ``None`` (the
    default) disables the retry wrapper entirely so the OFF path is unchanged.
    """

    engine: "RecoveryEngine"
    max_attempts: int = 3


def build_engine_recovery_policy(env: object = None) -> "EngineRecoveryPolicy | None":
    """Build the recovery policy from env, or ``None`` when recovery is OFF.

    Reuses ``MAGI_ERROR_RECOVERY_ENABLED`` / ``MAGI_MAX_RECOVERY_ATTEMPTS`` (the
    single source of truth in ``config.env``) and the existing default
    ``RecoveryEngine``. Imports are deferred so ``import cli.engine`` stays
    cold-clean (no error_recovery import at module top is required, but these
    are pure-python anyway).
    """

    import os

    from magi_agent.config.env import parse_error_recovery_env
    from magi_agent.runtime.error_recovery import ErrorRecoveryConfig, RecoveryEngine

    mapping = env if isinstance(env, dict) else os.environ
    parsed = parse_error_recovery_env(mapping)
    if not parsed.enabled:
        return None
    config = ErrorRecoveryConfig(
        recovery_enabled=True,
        max_recovery_attempts=parsed.max_recovery_attempts,
    )
    return EngineRecoveryPolicy(
        engine=RecoveryEngine(config),
        max_attempts=parsed.max_recovery_attempts,
    )


def build_output_continuation_config(
    env: object = None,
) -> "OutputContinuationConfig | None":
    """Build the output-continuation config from env, or ``None`` when OFF.

    Reuses ``MAGI_OUTPUT_CONTINUATION_ENABLED`` / ``MAGI_MAX_OUTPUT_CONTINUATIONS``
    (single source of truth in ``config.env``). ``None`` leaves streaming
    byte-for-byte identical to the pre-continuation path.
    """

    import os

    from magi_agent.config.env import parse_output_continuation_env
    from magi_agent.runtime.output_continuation import OutputContinuationConfig

    mapping = env if isinstance(env, dict) else os.environ
    parsed = parse_output_continuation_env(mapping)
    if not parsed.enabled:
        return None
    return OutputContinuationConfig(
        enabled=True,
        max_continuations=parsed.max_continuations,
    )


def build_empty_response_recovery_config(
    env: object = None,
) -> "EmptyResponseRecoveryConfig | None":
    """Build the empty-response recovery config from env, or ``None`` when OFF.

    Reuses ``MAGI_EMPTY_RESPONSE_RECOVERY_ENABLED`` /
    ``MAGI_EMPTY_RESPONSE_MAX_RECOVERIES`` (single source of truth in
    ``config.env``; strict truthy opt-in, default OFF). ``None`` leaves
    streaming byte-for-byte identical to the pre-recovery path.
    """

    import os

    from magi_agent.config.env import parse_empty_response_recovery_env
    from magi_agent.runtime.empty_response_recovery import (
        EmptyResponseRecoveryConfig,
    )

    mapping = env if isinstance(env, dict) else os.environ
    parsed = parse_empty_response_recovery_env(mapping)
    if not parsed.enabled:
        return None
    # PR5b: thread ``escalate`` explicitly. When escalation is OFF the resulting
    # config is byte-identical to the pre-PR5b dataclass (escalate defaults
    # False, grace_event_allowance keeps its dataclass default).
    return EmptyResponseRecoveryConfig(
        enabled=True,
        max_recoveries=parsed.max_recoveries,
        escalate=parsed.escalate,
    )


def should_reprompt_for_zero_edits(
    *, file_edits: int, already_reprompted: bool, enabled: bool
) -> bool:
    """Return True iff the zero-edit guard should fire a re-invocation.

    Pure helper — no side effects, fully unit-testable without driving the
    engine. The engine calls this after the main run loop concludes and before
    yielding the terminal EngineResult.

    Args:
        file_edits: number of file-mutating tool calls observed this turn.
        already_reprompted: True if we already fired the guard once this turn
            (prevents infinite re-invocation).
        enabled: value of ``parse_eval_zero_edit_guard_enabled(os.environ)``.
    """
    return bool(enabled and not already_reprompted and file_edits == 0)


def _is_continuation_output_event(event: Mapping[str, object]) -> bool:
    event_type = event.get("type")
    if event_type in _TOKEN_EVENT_TYPES:
        return bool(event.get("delta"))
    if event_type in _TOOL_EVENT_TYPES:
        return True
    if event_type in _ARTIFACT_EVENT_TYPES:
        return True
    return False


class _suppress_cleanup_errors:
    """Context manager that swallows ``asyncio.CancelledError`` AND any
    ``Exception`` raised while draining an already-cancelled or abandoned ADK
    iterator during cleanup. ``BaseException`` (``KeyboardInterrupt`` /
    ``SystemExit``) still propagates. When a non-``CancelledError`` exception is
    suppressed a single debug line records it so the swallow is observable.
    """

    def __enter__(self) -> "_suppress_cleanup_errors":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is None:
            return False
        if not issubclass(exc_type, (asyncio.CancelledError, Exception)):
            return False
        if not issubclass(exc_type, asyncio.CancelledError):
            logger.debug("suppressed cleanup exception: %r", exc)
        return True
