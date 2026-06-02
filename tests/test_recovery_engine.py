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
from magi_agent.runtime.error_recovery.resilient_boundary import (
    ResilientRunnerSessionBoundary,
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
# ResilientRunnerSessionBoundary tests
# ---------------------------------------------------------------------------


def _make_inner_boundary(
    *,
    success_result: object | None = None,
    error_result: object | None = None,
    side_effects: list[object] | None = None,
) -> MagicMock:
    """Mock of RunnerSessionBoundary."""
    boundary = MagicMock()
    if side_effects is not None:
        boundary.run_turn = AsyncMock(side_effect=side_effects)
    elif error_result is not None:
        boundary.run_turn = AsyncMock(return_value=error_result)
    else:
        boundary.run_turn = AsyncMock(
            return_value=success_result or {"status": "ok", "messages": []}
        )
    return boundary


class TestResilientRunnerSessionBoundary:
    """Integration tests for ResilientRunnerSessionBoundary."""

    async def test_recovery_disabled_passthrough(self) -> None:
        """When recovery_enabled=False, delegates directly."""
        config = ErrorRecoveryConfig(recovery_enabled=False)
        engine = RecoveryEngine(config=config)
        inner = _make_inner_boundary(success_result={"status": "ok"})
        boundary = ResilientRunnerSessionBoundary(inner=inner, engine=engine, config=config)

        result = await boundary.run_turn(
            messages=[{"role": "user", "content": "hi"}],
            session_key="s",
            turn_id="t",
        )

        assert result == {"status": "ok"}
        inner.run_turn.assert_awaited_once()
        assert boundary.evidence == []

    async def test_successful_turn_no_recovery(self) -> None:
        """Successful turn returns directly without recovery attempts."""
        config = _default_config()
        engine = RecoveryEngine(config=config)
        inner = _make_inner_boundary(success_result={"status": "ok"})
        boundary = ResilientRunnerSessionBoundary(inner=inner, engine=engine, config=config)

        result = await boundary.run_turn(
            messages=[{"role": "user", "content": "hello"}],
            session_key="s",
            turn_id="t",
        )

        assert result == {"status": "ok"}
        assert boundary.evidence == []

    async def test_error_turn_recovery_succeeds(self) -> None:
        """Error on first call, recovery modifies messages, retry succeeds."""
        config = _default_config()
        strategy = _make_strategy(
            "test_fix",
            success=True,
            modified_messages=[{"role": "user", "content": "fixed"}],
        )
        engine = RecoveryEngine(config=config, strategies=(strategy,))
        inner = _make_inner_boundary(
            side_effects=[
                {"status": "error", "error": "prompt is too long"},
                {"status": "ok", "messages": []},
            ]
        )
        boundary = ResilientRunnerSessionBoundary(inner=inner, engine=engine, config=config)

        result = await boundary.run_turn(
            messages=[{"role": "user", "content": "big prompt"}],
            session_key="s",
            turn_id="t",
        )

        assert result["status"] == "ok"
        assert len(boundary.evidence) == 1
        assert boundary.evidence[0]["success"] is True

    async def test_error_turn_recovery_fails(self) -> None:
        """Error on first call, recovery fails, returns original error."""
        config = _default_config()
        strategy = _make_strategy("bad_fix", success=False)
        engine = RecoveryEngine(config=config, strategies=(strategy,))
        error_result = {"status": "error", "error": "prompt is too long"}
        inner = _make_inner_boundary(error_result=error_result)
        boundary = ResilientRunnerSessionBoundary(inner=inner, engine=engine, config=config)

        result = await boundary.run_turn(
            messages=[{"role": "user", "content": "big"}],
            session_key="s",
            turn_id="t",
        )

        assert result["status"] == "error"
        assert len(boundary.evidence) == 1
        assert boundary.evidence[0]["success"] is False

    async def test_terminal_error_no_recovery(self) -> None:
        """Terminal errors are not retried."""
        config = _default_config()
        strategy = _make_strategy("should_not_run", success=True)
        engine = RecoveryEngine(config=config, strategies=(strategy,))
        error_result = {"status": "error", "error": "internal server error unknown"}
        inner = _make_inner_boundary(error_result=error_result)
        boundary = ResilientRunnerSessionBoundary(inner=inner, engine=engine, config=config)

        result = await boundary.run_turn(
            messages=[{"role": "user", "content": "x"}],
            session_key="s",
            turn_id="t",
        )

        assert result["status"] == "error"
        strategy.recover.assert_not_awaited()

    async def test_max_attempts_exhausted(self) -> None:
        """Recovery loop stops after max_recovery_attempts."""
        config = ErrorRecoveryConfig(recovery_enabled=True, max_recovery_attempts=2)
        # Strategy always "succeeds" but the inner boundary always errors
        strategy = _make_strategy(
            "always_fix",
            success=True,
            modified_messages=[{"role": "user", "content": "fixed"}],
        )
        engine = RecoveryEngine(config=config, strategies=(strategy,))
        error_result = {"status": "error", "error": "prompt is too long"}
        inner = _make_inner_boundary(
            side_effects=[error_result, error_result, error_result]
        )
        boundary = ResilientRunnerSessionBoundary(inner=inner, engine=engine, config=config)

        result = await boundary.run_turn(
            messages=[{"role": "user", "content": "big"}],
            session_key="s",
            turn_id="t",
        )

        assert result["status"] == "error"
        # 1 initial + 2 retries = 3 calls total
        assert inner.run_turn.await_count == 3
        assert len(boundary.evidence) == 2

    async def test_evidence_recorded_on_every_attempt(self) -> None:
        """Evidence is recorded for every recovery attempt."""
        config = ErrorRecoveryConfig(recovery_enabled=True, max_recovery_attempts=3)
        strategy = _make_strategy(
            "persist_fix",
            success=True,
            modified_messages=[{"role": "user", "content": "retry"}],
        )
        engine = RecoveryEngine(config=config, strategies=(strategy,))
        error_result = {"status": "error", "error": "prompt is too long"}
        inner = _make_inner_boundary(
            side_effects=[error_result, error_result, {"status": "ok"}]
        )
        boundary = ResilientRunnerSessionBoundary(inner=inner, engine=engine, config=config)

        result = await boundary.run_turn(
            messages=[{"role": "user", "content": "x"}],
            session_key="s",
            turn_id="t",
        )

        assert result["status"] == "ok"
        assert len(boundary.evidence) == 2
        assert boundary.evidence[0]["attempt"] == 1
        assert boundary.evidence[1]["attempt"] == 2
        for ev in boundary.evidence:
            assert ev["type"] == "ErrorRecovery"
            assert "timestamp" in ev
            assert "error_kind" in ev

    async def test_evidence_contains_required_fields(self) -> None:
        """Each evidence record has all required fields."""
        config = _default_config()
        strategy = _make_strategy("ev_check", success=False)
        engine = RecoveryEngine(config=config, strategies=(strategy,))
        inner = _make_inner_boundary(
            error_result={"status": "error", "error": "rate_limit"}
        )
        boundary = ResilientRunnerSessionBoundary(inner=inner, engine=engine, config=config)

        await boundary.run_turn(
            messages=[], session_key="s", turn_id="t",
        )

        assert len(boundary.evidence) >= 1
        ev = boundary.evidence[0]
        assert ev["type"] == "ErrorRecovery"
        assert "error_kind" in ev
        assert "strategy" in ev
        assert "attempt" in ev
        assert "success" in ev
        assert "tokens_freed" in ev
        assert "timestamp" in ev

    async def test_config_overrides_populated_on_escalation(self) -> None:
        """retry_with_config from strategy is surfaced as config_overrides."""
        config = _default_config()
        strategy = _make_strategy(
            "output_escalation",
            success=True,
            retry_with_config={"max_tokens": 65536},
            modified_messages=[{"role": "user", "content": "retry"}],
        )
        engine = RecoveryEngine(config=config, strategies=(strategy,))
        inner = _make_inner_boundary(
            side_effects=[
                {"status": "error", "error": "prompt is too long"},
                {"status": "ok", "messages": []},
            ]
        )
        boundary = ResilientRunnerSessionBoundary(inner=inner, engine=engine, config=config)

        result = await boundary.run_turn(
            messages=[{"role": "user", "content": "hello"}],
            session_key="s",
            turn_id="t",
        )

        assert result["status"] == "ok"
        assert boundary.config_overrides == {"max_tokens": 65536}
        # Evidence should also contain config_overrides
        assert len(boundary.evidence) == 1
        assert boundary.evidence[0]["config_overrides"] == {"max_tokens": 65536}

    async def test_config_overrides_empty_when_no_escalation(self) -> None:
        """config_overrides stays empty when strategy has no retry_with_config."""
        config = _default_config()
        strategy = _make_strategy(
            "plain_fix",
            success=True,
            modified_messages=[{"role": "user", "content": "fixed"}],
        )
        engine = RecoveryEngine(config=config, strategies=(strategy,))
        inner = _make_inner_boundary(
            side_effects=[
                {"status": "error", "error": "prompt is too long"},
                {"status": "ok", "messages": []},
            ]
        )
        boundary = ResilientRunnerSessionBoundary(inner=inner, engine=engine, config=config)

        await boundary.run_turn(
            messages=[{"role": "user", "content": "big"}],
            session_key="s",
            turn_id="t",
        )

        assert boundary.config_overrides == {}
        assert "config_overrides" not in boundary.evidence[0]

    async def test_config_overrides_accumulate_across_retries(self) -> None:
        """Multiple recovery attempts accumulate config overrides."""
        config = ErrorRecoveryConfig(recovery_enabled=True, max_recovery_attempts=3)
        s1 = _make_strategy(
            "escalate_first",
            success=True,
            retry_with_config={"max_tokens": 32768},
            modified_messages=[{"role": "user", "content": "r1"}],
        )
        s2 = _make_strategy(
            "escalate_second",
            success=True,
            retry_with_config={"max_tokens": 65536, "temperature": 0.5},
            modified_messages=[{"role": "user", "content": "r2"}],
        )

        # First attempt uses s1, second uses s2
        call_count = 0

        async def dynamic_recovery(error, messages, session_key, turn_id, state=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                result = await s1.recover(
                    RecoveryContext(
                        error=error, messages=messages,
                        session_key=session_key, turn_id=turn_id,
                    )
                )
                new_state = RecoveryAttemptState(
                    attempt_number=1, strategies_tried=("escalate_first",),
                )
                return result, new_state
            result = await s2.recover(
                RecoveryContext(
                    error=error, messages=messages,
                    session_key=session_key, turn_id=turn_id,
                )
            )
            new_state = RecoveryAttemptState(
                attempt_number=2,
                strategies_tried=("escalate_first", "escalate_second"),
            )
            return result, new_state

        engine = RecoveryEngine(config=config)
        engine.attempt_recovery = AsyncMock(side_effect=dynamic_recovery)  # type: ignore[method-assign]

        inner = _make_inner_boundary(
            side_effects=[
                {"status": "error", "error": "prompt is too long"},
                {"status": "error", "error": "prompt is too long"},
                {"status": "ok", "messages": []},
            ]
        )
        boundary = ResilientRunnerSessionBoundary(inner=inner, engine=engine, config=config)

        result = await boundary.run_turn(
            messages=[{"role": "user", "content": "hi"}],
            session_key="s",
            turn_id="t",
        )

        assert result["status"] == "ok"
        # Second override should have overwritten max_tokens, added temperature
        assert boundary.config_overrides == {"max_tokens": 65536, "temperature": 0.5}


# ---------------------------------------------------------------------------
# ErrorRecoveryConfig.from_env tests
# ---------------------------------------------------------------------------


class TestErrorRecoveryConfigFromEnv:
    def test_defaults_when_no_env(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            cfg = ErrorRecoveryConfig.from_env()
        assert cfg.recovery_enabled is False
        assert cfg.max_recovery_attempts == 3

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

    def test_invalid_attempts_falls_back(self) -> None:
        env = {
            "MAGI_MAX_RECOVERY_ATTEMPTS": "not_a_number",
        }
        with patch.dict(os.environ, env, clear=True):
            cfg = ErrorRecoveryConfig.from_env()
        assert cfg.max_recovery_attempts == 3

    def test_disabled_by_default(self) -> None:
        with patch.dict(os.environ, {"MAGI_ERROR_RECOVERY_ENABLED": "false"}, clear=True):
            cfg = ErrorRecoveryConfig.from_env()
        assert cfg.recovery_enabled is False
