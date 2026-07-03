from __future__ import annotations

import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from magi_agent.runtime.error_recovery.types import (
    ErrorKind,
    ErrorRecoveryConfig,
    MessageDict,
    RecoverableError,
    RecoveryAttemptState,
    RecoveryContext,
    RecoveryResult,
    RecoveryStrategy,
    TerminalError,
)
from magi_agent.runtime.error_recovery.engine import (
    DEFAULT_STRATEGIES,
    RecoveryEngine,
)

pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_error(kind: ErrorKind, tokens_over: int | None = None) -> RecoverableError:
    return RecoverableError(
        kind=kind,
        original_error="test error",
        tokens_over=tokens_over,
    )


def _default_config() -> ErrorRecoveryConfig:
    return ErrorRecoveryConfig(recovery_enabled=True)


def _make_strategy(
    name: str,
    applies: bool = True,
    success: bool = True,
    tokens_freed: int = 0,
    modified_messages: list[MessageDict] | None = None,
    retry_with_config: dict[str, object] | None = None,
) -> MagicMock:
    """Create a mock strategy implementing the RecoveryStrategy protocol."""
    strategy = MagicMock()
    strategy.name = name
    strategy.applies_to = MagicMock(return_value=applies)
    result = RecoveryResult(
        success=success,
        strategy_name=name,
        modified_messages=modified_messages,
        tokens_freed=tokens_freed,
        retry_with_config=retry_with_config,
    )
    strategy.recover = AsyncMock(return_value=result)
    return strategy


# ---------------------------------------------------------------------------
# RecoveryEngine unit tests
# ---------------------------------------------------------------------------


class TestRecoveryEngine:
    """RecoveryEngine orchestration tests."""

    async def test_single_strategy_success(self) -> None:
        strategy = _make_strategy("test_strategy", success=True, tokens_freed=100)
        engine = RecoveryEngine(config=_default_config(), strategies=(strategy,))
        error = _make_error(ErrorKind.PROMPT_TOO_LONG)

        result, state = await engine.attempt_recovery(
            error=error,
            messages=[{"role": "user", "content": "hello"}],
            session_key="sess-1",
            turn_id="turn-1",
        )

        assert result.success is True
        assert result.strategy_name == "test_strategy"
        assert state.strategies_tried == ("test_strategy",)
        assert state.attempt_number == 1

    async def test_single_strategy_failure(self) -> None:
        strategy = _make_strategy("failing", success=False)
        engine = RecoveryEngine(config=_default_config(), strategies=(strategy,))
        error = _make_error(ErrorKind.PROMPT_TOO_LONG)

        result, state = await engine.attempt_recovery(
            error=error,
            messages=[],
            session_key="s",
            turn_id="t",
        )

        assert result.success is False
        assert state.strategies_tried == ("failing",)

    async def test_strategy_priority_ordering(self) -> None:
        """First applicable successful strategy wins."""
        s1 = _make_strategy("first", success=True, tokens_freed=10)
        s2 = _make_strategy("second", success=True, tokens_freed=20)
        engine = RecoveryEngine(config=_default_config(), strategies=(s1, s2))
        error = _make_error(ErrorKind.PROMPT_TOO_LONG)

        result, state = await engine.attempt_recovery(
            error=error, messages=[], session_key="s", turn_id="t",
        )

        assert result.strategy_name == "first"
        # second strategy should not be called
        s2.recover.assert_not_awaited()

    async def test_all_strategies_fail_returns_failure(self) -> None:
        s1 = _make_strategy("a", success=False)
        s2 = _make_strategy("b", success=False)
        engine = RecoveryEngine(config=_default_config(), strategies=(s1, s2))
        error = _make_error(ErrorKind.RATE_LIMIT)

        result, state = await engine.attempt_recovery(
            error=error, messages=[], session_key="s", turn_id="t",
        )

        assert result.success is False
        assert state.strategies_tried == ("a", "b")

    async def test_state_correctly_updated_between_attempts(self) -> None:
        """Passing existing state accumulates strategies_tried."""
        s1 = _make_strategy("retry_strat", success=True, tokens_freed=50)
        engine = RecoveryEngine(config=_default_config(), strategies=(s1,))
        error = _make_error(ErrorKind.RATE_LIMIT)

        prev_state = RecoveryAttemptState(
            attempt_number=1,
            strategies_tried=("old_strat",),
            total_tokens_freed=200,
        )

        result, state = await engine.attempt_recovery(
            error=error, messages=[], session_key="s", turn_id="t",
            state=prev_state,
        )

        assert result.success is True
        assert state.attempt_number == 2
        assert state.strategies_tried == ("old_strat", "retry_strat")
        assert state.total_tokens_freed == 250

    async def test_strategy_not_applicable_is_skipped(self) -> None:
        s_no = _make_strategy("no_apply", applies=False)
        s_yes = _make_strategy("yes_apply", applies=True, success=True)
        engine = RecoveryEngine(config=_default_config(), strategies=(s_no, s_yes))
        error = _make_error(ErrorKind.MEDIA_SIZE)

        result, _ = await engine.attempt_recovery(
            error=error, messages=[], session_key="s", turn_id="t",
        )

        assert result.strategy_name == "yes_apply"
        s_no.recover.assert_not_awaited()

    async def test_empty_strategies_list(self) -> None:
        engine = RecoveryEngine(config=_default_config(), strategies=())
        error = _make_error(ErrorKind.PROMPT_TOO_LONG)

        result, state = await engine.attempt_recovery(
            error=error, messages=[], session_key="s", turn_id="t",
        )

        assert result.success is False
        assert state.strategies_tried == ()

    async def test_no_applicable_strategy(self) -> None:
        s1 = _make_strategy("wrong", applies=False)
        s2 = _make_strategy("also_wrong", applies=False)
        engine = RecoveryEngine(config=_default_config(), strategies=(s1, s2))
        error = _make_error(ErrorKind.PROMPT_TOO_LONG)

        result, state = await engine.attempt_recovery(
            error=error, messages=[], session_key="s", turn_id="t",
        )

        assert result.success is False

    async def test_default_strategies_created_when_none_provided(self) -> None:
        engine = RecoveryEngine(config=_default_config())
        assert len(engine._strategies) == 6

    async def test_tokens_freed_accumulated(self) -> None:
        s1 = _make_strategy("s1", success=False, tokens_freed=0)
        s2 = _make_strategy("s2", success=True, tokens_freed=300)
        engine = RecoveryEngine(config=_default_config(), strategies=(s1, s2))
        error = _make_error(ErrorKind.PROMPT_TOO_LONG)

        result, state = await engine.attempt_recovery(
            error=error, messages=[], session_key="s", turn_id="t",
        )

        assert state.total_tokens_freed == 300

    async def test_first_failure_tries_next(self) -> None:
        """If first applicable strategy fails, engine tries the next one."""
        s1 = _make_strategy("fail_first", success=False)
        s2 = _make_strategy("succeed_second", success=True, tokens_freed=42)
        engine = RecoveryEngine(config=_default_config(), strategies=(s1, s2))
        error = _make_error(ErrorKind.MAX_OUTPUT_TOKENS)

        result, state = await engine.attempt_recovery(
            error=error, messages=[], session_key="s", turn_id="t",
        )

        assert result.success is True
        assert result.strategy_name == "succeed_second"
        assert state.strategies_tried == ("fail_first", "succeed_second")

    async def test_recovery_context_passed_correctly(self) -> None:
        strategy = _make_strategy("ctx_check", success=True)
        engine = RecoveryEngine(config=_default_config(), strategies=(strategy,))
        error = _make_error(ErrorKind.RATE_LIMIT)
        msgs: list[MessageDict] = [{"role": "user", "content": "test"}]

        await engine.attempt_recovery(
            error=error, messages=msgs, session_key="my-sess", turn_id="my-turn",
        )

        call_args = strategy.recover.call_args
        ctx: RecoveryContext = call_args[0][0]
        assert ctx.session_key == "my-sess"
        assert ctx.turn_id == "my-turn"
        assert ctx.messages == msgs


# ---------------------------------------------------------------------------
# DEFAULT_STRATEGIES
# ---------------------------------------------------------------------------


class TestDefaultStrategies:
    def test_default_strategies_has_six_entries(self) -> None:
        assert len(DEFAULT_STRATEGIES) == 6

    def test_default_strategies_order(self) -> None:
        names = [s.name for s in DEFAULT_STRATEGIES]
        assert names == [
            "rate_limit",
            "output_escalation",
            "collapse_drain",
            "reactive_compact",
            "media_removal",
            "recovery_message",
        ]


# ---------------------------------------------------------------------------
# ErrorRecoveryConfig.from_env tests
# ---------------------------------------------------------------------------


class TestErrorRecoveryConfigFromEnv:
    def test_defaults_when_no_env(self) -> None:
        # C3 (N-37): from_env now delegates to the canonical profile-bool
        # reader. Unset flag + unset profile resolves to the full profile,
        # so recovery is ON by default.
        with patch.dict(os.environ, {}, clear=True):
            cfg = ErrorRecoveryConfig.from_env()
        assert cfg.recovery_enabled is True
        assert cfg.max_recovery_attempts == 3

    def test_disabled_under_safe_profile(self) -> None:
        with patch.dict(os.environ, {"MAGI_RUNTIME_PROFILE": "safe"}, clear=True):
            cfg = ErrorRecoveryConfig.from_env()
        assert cfg.recovery_enabled is False

    def test_values_from_env(self) -> None:
        env = {
            "MAGI_ERROR_RECOVERY_ENABLED": "true",
            "MAGI_MAX_RECOVERY_ATTEMPTS": "5",
        }
        with patch.dict(os.environ, env, clear=True):
            cfg = ErrorRecoveryConfig.from_env()
        assert cfg.recovery_enabled is True
        assert cfg.max_recovery_attempts == 5

    def test_enabled_with_1(self) -> None:
        with patch.dict(os.environ, {"MAGI_ERROR_RECOVERY_ENABLED": "1"}, clear=True):
            cfg = ErrorRecoveryConfig.from_env()
        assert cfg.recovery_enabled is True

    def test_invalid_attempts_raises(self) -> None:
        from magi_agent.config.env import RuntimeEnvError

        env = {"MAGI_MAX_RECOVERY_ATTEMPTS": "not_a_number"}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(RuntimeEnvError):
                ErrorRecoveryConfig.from_env()

    def test_zero_attempts_raises(self) -> None:
        from magi_agent.config.env import RuntimeEnvError

        env = {"MAGI_MAX_RECOVERY_ATTEMPTS": "0"}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(RuntimeEnvError):
                ErrorRecoveryConfig.from_env()

    def test_disabled_by_default(self) -> None:
        with patch.dict(os.environ, {"MAGI_ERROR_RECOVERY_ENABLED": "false"}, clear=True):
            cfg = ErrorRecoveryConfig.from_env()
        assert cfg.recovery_enabled is False
