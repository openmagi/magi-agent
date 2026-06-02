"""Tests for magi_agent.shared.token_estimation."""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from magi_agent.shared.token_estimation import (
    estimate_message_tokens,
    estimate_messages_tokens,
)


# ---------------------------------------------------------------------------
# 1. Single message → positive int
# ---------------------------------------------------------------------------

def test_single_message_positive_int():
    msg = {"role": "user", "content": "Hello, world!"}
    result = estimate_message_tokens(msg)
    assert isinstance(result, int)
    assert result > 0


# ---------------------------------------------------------------------------
# 2. Empty message {} → 0 or small number (not negative)
# ---------------------------------------------------------------------------

def test_empty_message_non_negative():
    result = estimate_message_tokens({})
    # json.dumps({}) == "{}" → len 2 → 2 // 4 == 0
    assert isinstance(result, int)
    assert result >= 0


# ---------------------------------------------------------------------------
# 3. Multiple messages → sum of individual estimates
# ---------------------------------------------------------------------------

def test_multiple_messages_sum():
    msgs = [
        {"role": "user", "content": "First message"},
        {"role": "assistant", "content": "Second message response"},
        {"role": "tool", "content": "Tool result data"},
    ]
    expected = sum(estimate_message_tokens(m) for m in msgs)
    result = estimate_messages_tokens(msgs)
    assert result == expected


# ---------------------------------------------------------------------------
# 4. Consistency: estimate_messages_tokens([m]) == estimate_message_tokens(m)
# ---------------------------------------------------------------------------

def test_single_item_list_consistency():
    msg = {"role": "user", "content": "consistency check"}
    single = estimate_message_tokens(msg)
    via_list = estimate_messages_tokens([msg])
    assert single == via_list


# ---------------------------------------------------------------------------
# 5. Large message → proportional to content size
# ---------------------------------------------------------------------------

def test_large_message_proportional():
    small_msg = {"role": "user", "content": "x" * 100}
    large_msg = {"role": "user", "content": "x" * 10_000}
    small_tokens = estimate_message_tokens(small_msg)
    large_tokens = estimate_message_tokens(large_msg)
    # Large should be roughly 100× the small
    assert large_tokens > small_tokens * 50


# ---------------------------------------------------------------------------
# 6. Non-serializable values (datetime) → handled via default=str
# ---------------------------------------------------------------------------

def test_non_serializable_datetime_handled():
    msg = {"role": "user", "content": "text", "timestamp": datetime(2026, 1, 1, 12, 0, 0)}
    # Should not raise; default=str converts datetime to string
    result = estimate_message_tokens(msg)
    assert isinstance(result, int)
    assert result > 0


# ---------------------------------------------------------------------------
# 7. Context path uses shared: TokenBudgetTracker.estimate_tokens delegates
# ---------------------------------------------------------------------------

def test_context_token_tracker_uses_shared():
    from magi_agent.context.token_tracker import TokenBudgetTracker

    msg = {"role": "user", "content": "hi"}
    tracker_result = TokenBudgetTracker.estimate_tokens(msg)
    shared_result = estimate_message_tokens(msg)
    assert tracker_result == shared_result


# ---------------------------------------------------------------------------
# 8. Error recovery path uses shared: _estimate_tokens delegates
# ---------------------------------------------------------------------------

def test_error_recovery_token_utils_uses_shared():
    from magi_agent.runtime.error_recovery.strategies._token_utils import (
        _estimate_tokens,
    )

    msgs = [{"role": "user", "content": "hi"}]
    utils_result = _estimate_tokens(msgs)
    shared_result = estimate_messages_tokens(msgs)
    assert utils_result == shared_result


# ---------------------------------------------------------------------------
# 9. Empty list → 0 tokens
# ---------------------------------------------------------------------------

def test_empty_list_zero_tokens():
    result = estimate_messages_tokens([])
    assert result == 0


# ---------------------------------------------------------------------------
# 10. Correctness: matches raw json.dumps // 4 formula
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("msg", [
    {"role": "user", "content": "hello"},
    {"key": "value", "nested": {"a": 1}},
    {"content": "x" * 500},
    {},
])
def test_matches_raw_formula(msg: dict):
    expected = len(json.dumps(msg, default=str)) // 4
    assert estimate_message_tokens(msg) == expected
