"""PR2: TokenBudgetSnapshot exposed on RecoveryContext (tracker bridge).

Tests verify:
1. Backward compatibility — token_budget defaults to None.
2. RecoveryContext accepts a TokenBudgetSnapshot.
3. TokenBudgetSnapshot is frozen (immutable).
4. warning_level is accessible via RecoveryContext.token_budget.
5. utilization is accessible via RecoveryContext.token_budget.
6. Full round-trip: TokenBudgetTracker.snapshot() → RecoveryContext → read back.
7. Existing code (no token_budget kwarg) is unaffected.
8. model_dump() serialization works with snapshot present.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from magi_agent.context.token_tracker import TokenBudgetTracker
from magi_agent.context.types import TokenBudgetSnapshot, WarningLevel
from magi_agent.runtime.error_recovery.types import (
    ErrorKind,
    RecoverableError,
    RecoveryContext,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ERROR = RecoverableError(
    kind=ErrorKind.PROMPT_TOO_LONG,
    original_error="context length exceeded",
)

_MESSAGES: list[dict] = [{"role": "user", "content": "hello"}]


def _make_ctx(**kwargs) -> RecoveryContext:
    """Return a minimal RecoveryContext with optional overrides."""
    return RecoveryContext(
        error=_ERROR,
        messages=_MESSAGES,
        session_key="sess-abc",
        turn_id="turn-001",
        **kwargs,
    )


def _make_snapshot(
    *,
    context_window: int = 150_000,
    total_tokens: int = 10_000,
    warning_level: WarningLevel = WarningLevel.NORMAL,
    message_count: int = 1,
) -> TokenBudgetSnapshot:
    utilization = total_tokens / context_window if context_window > 0 else 0.0
    return TokenBudgetSnapshot(
        context_window=context_window,
        total_tokens=total_tokens,
        utilization=utilization,
        warning_level=warning_level,
        message_count=message_count,
    )


# ---------------------------------------------------------------------------
# 1. Backward compatibility — token_budget defaults to None
# ---------------------------------------------------------------------------


def test_recovery_context_token_budget_defaults_to_none() -> None:
    ctx = _make_ctx()
    assert ctx.token_budget is None


# ---------------------------------------------------------------------------
# 2. RecoveryContext accepts a TokenBudgetSnapshot
# ---------------------------------------------------------------------------


def test_recovery_context_accepts_token_budget_snapshot() -> None:
    snapshot = _make_snapshot(total_tokens=50_000)
    ctx = _make_ctx(token_budget=snapshot)
    assert ctx.token_budget is not None
    assert ctx.token_budget.total_tokens == 50_000


# ---------------------------------------------------------------------------
# 3. TokenBudgetSnapshot is frozen (immutable)
# ---------------------------------------------------------------------------


def test_token_budget_snapshot_is_frozen() -> None:
    snapshot = _make_snapshot()
    with pytest.raises((TypeError, ValidationError)):
        snapshot.total_tokens = 999  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 4. warning_level accessible via RecoveryContext.token_budget
# ---------------------------------------------------------------------------


def test_recovery_context_token_budget_warning_level_accessible() -> None:
    snapshot = _make_snapshot(
        total_tokens=140_000,
        warning_level=WarningLevel.CRITICAL,
    )
    ctx = _make_ctx(token_budget=snapshot)
    assert ctx.token_budget is not None
    assert ctx.token_budget.warning_level == WarningLevel.CRITICAL


# ---------------------------------------------------------------------------
# 5. utilization accessible via RecoveryContext.token_budget
# ---------------------------------------------------------------------------


def test_recovery_context_token_budget_utilization_accessible() -> None:
    snapshot = _make_snapshot(context_window=100_000, total_tokens=75_000)
    ctx = _make_ctx(token_budget=snapshot)
    assert ctx.token_budget is not None
    assert abs(ctx.token_budget.utilization - 0.75) < 1e-9


# ---------------------------------------------------------------------------
# 6. Round-trip: TokenBudgetTracker.snapshot() → RecoveryContext → read back
# ---------------------------------------------------------------------------


def test_tracker_snapshot_round_trip_via_recovery_context() -> None:
    tracker = TokenBudgetTracker(model="claude-sonnet-4-6")
    msg = {"role": "user", "content": "This is a test message for token tracking."}
    tracker.add_message(msg, role="user", kind="user_message")

    snapshot = tracker.snapshot()
    assert snapshot.message_count == 1
    assert snapshot.total_tokens > 0

    ctx = _make_ctx(token_budget=snapshot)
    assert ctx.token_budget is not None
    assert ctx.token_budget.message_count == snapshot.message_count
    assert ctx.token_budget.total_tokens == snapshot.total_tokens
    assert ctx.token_budget.context_window == snapshot.context_window
    assert ctx.token_budget.warning_level == snapshot.warning_level


# ---------------------------------------------------------------------------
# 7. Existing code (no token_budget kwarg) is unaffected
# ---------------------------------------------------------------------------


def test_existing_code_without_token_budget_unaffected() -> None:
    """RecoveryContext constructed without token_budget must work identically."""
    ctx = RecoveryContext(
        error=_ERROR,
        messages=_MESSAGES,
        attempt=1,
        max_attempts=5,
        previous_strategies=("truncate",),
        session_key="sess-xyz",
        turn_id="turn-007",
    )
    assert ctx.token_budget is None
    assert ctx.attempt == 1
    assert ctx.previous_strategies == ("truncate",)


# ---------------------------------------------------------------------------
# 8. model_dump() serialization works with snapshot present
# ---------------------------------------------------------------------------


def test_recovery_context_model_dump_with_snapshot() -> None:
    snapshot = _make_snapshot(
        context_window=150_000,
        total_tokens=90_000,
        warning_level=WarningLevel.HIGH,
        message_count=3,
    )
    ctx = _make_ctx(token_budget=snapshot)
    data = ctx.model_dump()

    assert "token_budget" in data
    tb = data["token_budget"]
    assert tb is not None
    assert tb["total_tokens"] == 90_000
    assert tb["context_window"] == 150_000
    assert tb["message_count"] == 3
    assert tb["warning_level"] == WarningLevel.HIGH


# ---------------------------------------------------------------------------
# 9. model_dump() with no token_budget serializes as None
# ---------------------------------------------------------------------------


def test_recovery_context_model_dump_no_snapshot() -> None:
    ctx = _make_ctx()
    data = ctx.model_dump()
    assert data["token_budget"] is None
