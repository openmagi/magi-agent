"""Tests for PR5 — max-steps wrap-up brake.

TDD contract:
  (a) non-final iteration → no brake, tools not disabled, no wrap-up message appended
  (b) final iteration (iteration == max_iterations - 1) → wrap-up instruction injected
      + ``MaxStepsBrakeResult.tools_disabled`` is True
  (c) wrap-up message content includes the required sections (max-steps reached,
      accomplished/summary, remaining, recommendations/next steps)
  (d) edge cases: max_iterations <= 0 → no brake; max_iterations == 1 at iteration 0
      → brake fires; messages list is mutated in-place

All tests use real types from ``magi_agent.runtime.turn_policy``.
"""
from __future__ import annotations

from typing import Any

import pytest

from magi_agent.runtime.turn_policy import (
    MAX_STEPS_WRAP_UP_MESSAGE,
    MaxStepsBrakeResult,
    maybe_apply_max_steps_brake,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _messages(n: int = 0) -> list[dict[str, Any]]:
    """Return a fresh list of n simple user messages."""
    return [{"role": "user", "content": f"msg {i}"} for i in range(n)]


def _tools(n: int = 2) -> list[dict[str, Any]]:
    """Return a fresh list of n dummy tool descriptors."""
    return [{"name": f"tool_{i}", "description": "a tool"} for i in range(n)]


# ---------------------------------------------------------------------------
# (a) Non-final iteration — no brake, no injection, tools not disabled
# ---------------------------------------------------------------------------


def test_non_final_iteration_returns_no_brake_not_injected() -> None:
    """Iteration 0 of 5 is not final — brake must not fire."""
    msgs = _messages(1)
    original_msgs = list(msgs)
    result = maybe_apply_max_steps_brake(
        iteration=0,
        max_iterations=5,
        messages=msgs,
        tools=_tools(),
    )

    assert result.brake_applied is False
    assert result.tools_disabled is False
    assert msgs == original_msgs  # messages list unchanged


def test_non_final_iteration_penultimate_is_not_final() -> None:
    """Iteration 3 of 5 is penultimate but NOT final (final is 4)."""
    msgs = _messages()
    result = maybe_apply_max_steps_brake(
        iteration=3,
        max_iterations=5,
        messages=msgs,
        tools=_tools(),
    )

    assert result.brake_applied is False
    assert result.tools_disabled is False
    assert msgs == []


def test_non_final_iteration_zero_of_ten() -> None:
    msgs = _messages()
    result = maybe_apply_max_steps_brake(
        iteration=0,
        max_iterations=10,
        messages=msgs,
        tools=_tools(3),
    )
    assert result.brake_applied is False
    assert result.tools_disabled is False


# ---------------------------------------------------------------------------
# (b) Final iteration — brake fires, wrap-up injected, tools disabled
# ---------------------------------------------------------------------------


def test_final_iteration_brake_applies_and_tools_disabled() -> None:
    """iteration == max_iterations - 1 → brake fires."""
    msgs = _messages(2)
    result = maybe_apply_max_steps_brake(
        iteration=4,
        max_iterations=5,
        messages=msgs,
        tools=_tools(),
    )

    assert result.brake_applied is True
    assert result.tools_disabled is True


def test_final_iteration_injects_user_message_into_messages() -> None:
    """Wrap-up instruction is appended to messages as a user turn."""
    msgs = _messages(1)
    maybe_apply_max_steps_brake(
        iteration=9,
        max_iterations=10,
        messages=msgs,
        tools=_tools(),
    )

    # The last appended message must be a user turn with the wrap-up text
    assert len(msgs) == 2
    injected = msgs[-1]
    assert injected["role"] == "user"
    assert isinstance(injected["content"], str)
    assert len(injected["content"]) > 0


def test_final_step_of_1_iteration_budget_fires_at_iteration_zero() -> None:
    """max_iterations=1 means iteration 0 is the ONLY and final step."""
    msgs = _messages()
    result = maybe_apply_max_steps_brake(
        iteration=0,
        max_iterations=1,
        messages=msgs,
        tools=_tools(),
    )

    assert result.brake_applied is True
    assert result.tools_disabled is True
    assert len(msgs) == 1  # one message injected into empty list


def test_final_step_of_2_iteration_budget_fires_at_iteration_one() -> None:
    msgs = _messages()
    result = maybe_apply_max_steps_brake(
        iteration=1,
        max_iterations=2,
        messages=msgs,
        tools=_tools(),
    )
    assert result.brake_applied is True


def test_empty_tools_list_still_brakes() -> None:
    """Even with no tools declared, the brake fires and injects the message."""
    msgs = _messages()
    result = maybe_apply_max_steps_brake(
        iteration=4,
        max_iterations=5,
        messages=msgs,
        tools=[],
    )
    assert result.brake_applied is True
    assert result.tools_disabled is True
    assert len(msgs) == 1


# ---------------------------------------------------------------------------
# (c) Wrap-up message content — required sections present
# ---------------------------------------------------------------------------


def test_wrap_up_message_mentions_max_steps_reached() -> None:
    """The wrap-up text must state that the maximum number of steps has been reached."""
    content = MAX_STEPS_WRAP_UP_MESSAGE
    lower = content.lower()

    # "maximum" + some variant of "steps" or "iterations" reached
    assert "maximum" in lower or "max" in lower
    assert "step" in lower or "iteration" in lower


def test_wrap_up_message_mentions_accomplished_summary() -> None:
    """Wrap-up must prompt the model to summarize what was done / accomplished."""
    content = MAX_STEPS_WRAP_UP_MESSAGE.lower()
    # Any of: "accomplished", "done", "completed", "summary", "summarize", "what you did"
    accomplished_terms = ("accomplish", "done", "completed", "summary", "summarize", "what")
    assert any(term in content for term in accomplished_terms)


def test_wrap_up_message_mentions_remaining_tasks() -> None:
    """Wrap-up must prompt the model to list remaining / outstanding work."""
    content = MAX_STEPS_WRAP_UP_MESSAGE.lower()
    remaining_terms = ("remain", "outstanding", "pending", "unfinished", "incomplete", "left")
    assert any(term in content for term in remaining_terms)


def test_wrap_up_message_mentions_next_steps_or_recommendations() -> None:
    """Wrap-up must prompt the model to provide recommended next steps."""
    content = MAX_STEPS_WRAP_UP_MESSAGE.lower()
    next_terms = ("recommend", "next step", "suggest", "follow-up", "follow up", "continuation")
    assert any(term in content for term in next_terms)


def test_wrap_up_message_instructs_no_tool_calls() -> None:
    """Wrap-up must explicitly instruct the model NOT to call tools."""
    content = MAX_STEPS_WRAP_UP_MESSAGE.lower()
    no_tool_terms = ("do not", "don't", "no tool", "without tool", "text only", "text-only")
    assert any(term in content for term in no_tool_terms)


def test_injected_message_content_matches_constant() -> None:
    """The injected message's content must equal MAX_STEPS_WRAP_UP_MESSAGE."""
    msgs: list[dict[str, Any]] = []
    maybe_apply_max_steps_brake(
        iteration=4,
        max_iterations=5,
        messages=msgs,
        tools=_tools(),
    )
    assert msgs[-1]["content"] == MAX_STEPS_WRAP_UP_MESSAGE


# ---------------------------------------------------------------------------
# (d) Edge / guard cases
# ---------------------------------------------------------------------------


def test_max_iterations_zero_never_fires() -> None:
    """max_iterations <= 0 is degenerate — brake must not fire (defensive guard)."""
    for max_iter in (0, -1, -5):
        msgs = _messages()
        result = maybe_apply_max_steps_brake(
            iteration=0,
            max_iterations=max_iter,
            messages=msgs,
            tools=_tools(),
        )
        assert result.brake_applied is False, f"Expected no brake for max_iterations={max_iter}"
        assert msgs == []


def test_iteration_beyond_final_still_fires() -> None:
    """If the caller somehow passes iteration > max_iterations - 1 the brake fires."""
    msgs = _messages()
    result = maybe_apply_max_steps_brake(
        iteration=10,
        max_iterations=5,
        messages=msgs,
        tools=_tools(),
    )
    assert result.brake_applied is True


def test_messages_list_mutated_in_place_not_replaced() -> None:
    """The function appends to the *same* list object, not a new one."""
    msgs: list[dict[str, Any]] = []
    original_id = id(msgs)
    maybe_apply_max_steps_brake(
        iteration=0,
        max_iterations=1,
        messages=msgs,
        tools=_tools(),
    )
    assert id(msgs) == original_id
    assert len(msgs) == 1


def test_result_is_dataclass_with_expected_fields() -> None:
    """MaxStepsBrakeResult must have brake_applied and tools_disabled bool fields."""
    result = MaxStepsBrakeResult(brake_applied=True, tools_disabled=True)
    assert result.brake_applied is True
    assert result.tools_disabled is True

    result2 = MaxStepsBrakeResult(brake_applied=False, tools_disabled=False)
    assert result2.brake_applied is False
    assert result2.tools_disabled is False


def test_non_final_does_not_inject_any_messages() -> None:
    """Confirm zero messages are appended on non-final iterations."""
    for iteration in range(4):
        msgs: list[dict[str, Any]] = []
        maybe_apply_max_steps_brake(
            iteration=iteration,
            max_iterations=5,
            messages=msgs,
            tools=_tools(),
        )
        assert msgs == [], f"Unexpected injection at iteration={iteration}"
