"""Tests for openmagi_core_agent.context.token_tracker.TokenBudgetTracker."""
from __future__ import annotations

import json

import pytest

from openmagi_core_agent.context.token_tracker import (
    _DEFAULT_CONTEXT_WINDOW,
    TokenBudgetTracker,
)
from openmagi_core_agent.context.types import (
    ContextManagementConfig,
    WarningLevel,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_message(text: str, role: str = "user") -> dict:
    return {"role": role, "content": text}


def _tokens_for(message: dict) -> int:
    return len(json.dumps(message, default=str)) // 4


# ---------------------------------------------------------------------------
# 1. Empty tracker → NORMAL, 0 tokens, 0 messages
# ---------------------------------------------------------------------------

def test_empty_tracker_is_normal():
    tracker = TokenBudgetTracker("claude-opus-4-6")
    snap = tracker.snapshot()
    assert snap.warning_level == WarningLevel.NORMAL
    assert snap.total_tokens == 0
    assert snap.message_count == 0
    assert snap.utilization == 0.0


# ---------------------------------------------------------------------------
# 2. Single user message → correct token count
# ---------------------------------------------------------------------------

def test_single_user_message_token_count():
    tracker = TokenBudgetTracker("claude-opus-4-6")
    msg = _make_message("Hello, world!")
    tracked = tracker.add_message(msg, role="user", kind="user_message")
    expected_tokens = _tokens_for(msg)
    assert tracked.tokens == expected_tokens
    assert tracked.tokens > 0
    snap = tracker.snapshot()
    assert snap.total_tokens == expected_tokens
    assert snap.message_count == 1


# ---------------------------------------------------------------------------
# 3. Single tool_result message → correct token count + tool_use_id preserved
# ---------------------------------------------------------------------------

def test_tool_result_preserves_tool_use_id():
    tracker = TokenBudgetTracker("claude-sonnet-4-6")
    msg = {"role": "tool", "content": "result data", "tool_use_id": "toolu_abc123"}
    tool_use_id = "toolu_abc123"
    tracked = tracker.add_message(msg, role="tool", kind="tool_result", tool_use_id=tool_use_id)
    assert tracked.tool_use_id == tool_use_id
    assert tracked.kind == "tool_result"
    assert tracked.tokens == _tokens_for(msg)


# ---------------------------------------------------------------------------
# 4. Multi-turn (user + assistant + tool) → cumulative tokens
# ---------------------------------------------------------------------------

def test_multi_turn_cumulative_tokens():
    tracker = TokenBudgetTracker("claude-opus-4-6")
    msgs = [
        _make_message("user question", role="user"),
        _make_message("assistant answer", role="assistant"),
        {"role": "tool", "content": "tool output"},
    ]
    for msg in msgs:
        tracker.add_message(msg, role=msg["role"], kind="message")

    expected_total = sum(_tokens_for(m) for m in msgs)
    snap = tracker.snapshot()
    assert snap.total_tokens == expected_total
    assert snap.message_count == 3


# ---------------------------------------------------------------------------
# 5. Known model (claude-opus-4-6) → context_window = 150_000
# ---------------------------------------------------------------------------

def test_known_model_context_window():
    tracker = TokenBudgetTracker("claude-opus-4-6")
    assert tracker.context_window == 150_000


# ---------------------------------------------------------------------------
# 6. Unknown model → context_window = 150_000 (default)
# ---------------------------------------------------------------------------

def test_unknown_model_default_context_window():
    tracker = TokenBudgetTracker("unknown-model-xyz")
    assert tracker.context_window == _DEFAULT_CONTEXT_WINDOW
    assert tracker.context_window == 150_000


# ---------------------------------------------------------------------------
# 7. Threshold transition: MODERATE (>60%)
# ---------------------------------------------------------------------------

def test_threshold_moderate():
    context_window = 150_000
    tracker = TokenBudgetTracker("claude-opus-4-6")
    # We need total > 60% of 150_000 = > 90_000 tokens
    # estimate_tokens = len(json.dumps(msg)) // 4
    # Craft a message large enough to push over 60% in one shot
    # 90_001 tokens → need a string of ~360_004 chars
    large_text = "x" * (90_001 * 4)
    msg = {"role": "user", "content": large_text}
    tracker.add_message(msg)
    snap = tracker.snapshot()
    assert snap.utilization > 0.60
    assert snap.warning_level == WarningLevel.MODERATE


# ---------------------------------------------------------------------------
# 8. Threshold transition: HIGH (>75%)
# ---------------------------------------------------------------------------

def test_threshold_high():
    tracker = TokenBudgetTracker("claude-opus-4-6")
    # Need > 75% of 150_000 = > 112_500 tokens
    large_text = "x" * (112_501 * 4)
    msg = {"role": "user", "content": large_text}
    tracker.add_message(msg)
    snap = tracker.snapshot()
    assert snap.utilization > 0.75
    assert snap.warning_level == WarningLevel.HIGH


# ---------------------------------------------------------------------------
# 9. Threshold transition: CRITICAL (>90%)
# ---------------------------------------------------------------------------

def test_threshold_critical():
    tracker = TokenBudgetTracker("claude-opus-4-6")
    # Need > 90% of 150_000 = > 135_000 tokens
    large_text = "x" * (135_001 * 4)
    msg = {"role": "user", "content": large_text}
    tracker.add_message(msg)
    snap = tracker.snapshot()
    assert snap.utilization > 0.90
    assert snap.warning_level == WarningLevel.CRITICAL


# ---------------------------------------------------------------------------
# 10. Custom config thresholds work
# ---------------------------------------------------------------------------

def test_custom_config_thresholds():
    # Set very low thresholds: moderate at 1% of 150_000 = 1_500 tokens
    config = ContextManagementConfig(
        moderate_threshold=0.01,
        high_threshold=0.50,
        critical_threshold=0.80,
    )
    tracker = TokenBudgetTracker("claude-opus-4-6", config=config)
    # Need > 1% of 150_000 = 1_500 tokens → string of ~6_000 chars
    # message JSON: ~28 overhead + 6004 content + 2 close = ~6034 chars → 1508 tokens
    # utilization = 1508 / 150_000 ≈ 0.01005 → MODERATE (>0.01, <0.50)
    large_text = "x" * (1_501 * 4)
    msg = _make_message(large_text)
    tracker.add_message(msg)
    snap = tracker.snapshot()
    assert snap.warning_level == WarningLevel.MODERATE


def test_custom_config_high_threshold():
    config = ContextManagementConfig(
        moderate_threshold=0.01,
        high_threshold=0.02,
        critical_threshold=0.90,
    )
    tracker = TokenBudgetTracker("claude-opus-4-6", config=config)
    # Push past 2% of 150_000 = 3_000 tokens
    # message JSON: ~28 overhead + 12004 content + 2 close = ~12034 chars → 3008 tokens
    # utilization = 3008 / 150_000 ≈ 0.02005 → HIGH (>0.02, <0.90)
    large_text = "x" * (3_001 * 4)
    msg = {"role": "user", "content": large_text}
    tracker.add_message(msg)
    snap = tracker.snapshot()
    assert snap.warning_level == WarningLevel.HIGH


# ---------------------------------------------------------------------------
# 11. reset() clears messages
# ---------------------------------------------------------------------------

def test_reset_clears_messages():
    tracker = TokenBudgetTracker("claude-opus-4-6")
    tracker.add_message(_make_message("hello"))
    tracker.add_message(_make_message("world"))
    assert tracker.snapshot().message_count == 2

    tracker.reset()
    snap = tracker.snapshot()
    assert snap.message_count == 0
    assert snap.total_tokens == 0
    assert snap.warning_level == WarningLevel.NORMAL


# ---------------------------------------------------------------------------
# 12. estimate_tokens returns positive int for any dict
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("msg", [
    {},
    {"key": "value"},
    {"role": "user", "content": ""},
    {"nested": {"a": 1, "b": [1, 2, 3]}},
    {"content": "x" * 1000},
])
def test_estimate_tokens_positive_int(msg: dict):
    result = TokenBudgetTracker.estimate_tokens(msg)
    assert isinstance(result, int)
    # Even empty dict "{}" has 2 chars → 2 // 4 = 0, so allow >= 0
    assert result >= 0


def test_estimate_tokens_non_empty_dict_is_positive():
    msg = {"role": "user", "content": "hello world"}
    result = TokenBudgetTracker.estimate_tokens(msg)
    assert result > 0


# ---------------------------------------------------------------------------
# Bonus: snapshot reflects correct context_window from known model table
# ---------------------------------------------------------------------------

def test_snapshot_context_window_matches_tracker():
    tracker = TokenBudgetTracker("gpt-5.5")
    snap = tracker.snapshot()
    assert snap.context_window == 750_000
    assert tracker.context_window == 750_000


def test_snapshot_context_window_big_dic_router():
    tracker = TokenBudgetTracker("big-dic-router/auto")
    assert tracker.context_window == 196_608


# ---------------------------------------------------------------------------
# Threshold ordering validation
# ---------------------------------------------------------------------------

def test_inverted_thresholds_raise_value_error():
    """ContextManagementConfig must reject thresholds that are not ordered."""
    import pytest as _pytest
    with _pytest.raises(ValueError, match="thresholds must satisfy"):
        ContextManagementConfig(
            moderate_threshold=0.90,
            high_threshold=0.50,
            critical_threshold=0.20,
        )


def test_moderate_greater_than_high_raises_value_error():
    import pytest as _pytest
    with _pytest.raises(ValueError, match="thresholds must satisfy"):
        ContextManagementConfig(
            moderate_threshold=0.80,
            high_threshold=0.60,
            critical_threshold=0.90,
        )


def test_threshold_above_one_raises_value_error():
    import pytest as _pytest
    with _pytest.raises(ValueError, match="thresholds must satisfy"):
        ContextManagementConfig(
            moderate_threshold=0.60,
            high_threshold=0.75,
            critical_threshold=1.10,
        )


def test_equal_thresholds_are_valid():
    """Equal adjacent thresholds (e.g. moderate == high) are allowed."""
    config = ContextManagementConfig(
        moderate_threshold=0.75,
        high_threshold=0.75,
        critical_threshold=0.90,
    )
    assert config.moderate_threshold == config.high_threshold
