from __future__ import annotations

import pytest

from magi_agent.tools.output_budget import BudgetedToolResult, budget_tool_result
from magi_agent.tools.result import ToolResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_result(*, output: str) -> ToolResult:
    return ToolResult(status="ok", output=output)


def _budgeted(*, output: str, llm_preview_chars: int = 4000) -> BudgetedToolResult:
    return budget_tool_result(
        _make_result(output=output),
        llm_preview_chars=llm_preview_chars,
    )


# ---------------------------------------------------------------------------
# 1a-i  flag off → no delegationHint key, projection unchanged
# ---------------------------------------------------------------------------

def test_delegation_hint_flag_off_no_key_when_truncated() -> None:
    """When delegation_available=False (default), no delegationHint even if truncated."""
    btr = _budgeted(output="x" * 10_000, llm_preview_chars=100)
    assert btr.truncation.llm_preview_truncated is True

    proj = btr.public_projection()  # default: delegation_available=False
    assert "delegationHint" not in proj


def test_delegation_hint_flag_off_explicit_false_no_key() -> None:
    """Explicit delegation_available=False produces identical output to default."""
    btr = _budgeted(output="x" * 10_000, llm_preview_chars=100)

    proj_default = btr.public_projection()
    proj_explicit = btr.public_projection(delegation_available=False)

    assert proj_default == proj_explicit
    assert "delegationHint" not in proj_explicit


def test_delegation_hint_flag_off_not_truncated_no_key() -> None:
    """No delegationHint when flag is off and output fits within budget."""
    btr = _budgeted(output="short", llm_preview_chars=4000)
    assert btr.truncation.llm_preview_truncated is False

    proj = btr.public_projection()
    assert "delegationHint" not in proj


# ---------------------------------------------------------------------------
# 1a-ii  flag on + truncated → hint present, references resultRef
# ---------------------------------------------------------------------------

def test_delegation_hint_flag_on_truncated_hint_present() -> None:
    """When delegation_available=True and output was truncated, hint is injected."""
    btr = _budgeted(output="y" * 10_000, llm_preview_chars=100)
    assert btr.truncation.llm_preview_truncated is True

    proj = btr.public_projection(delegation_available=True)

    assert "delegationHint" in proj
    hint = proj["delegationHint"]
    assert isinstance(hint, str)
    assert btr.result_ref in hint


def test_delegation_hint_references_result_ref_value() -> None:
    """The hint string contains the exact result_ref value."""
    btr = _budgeted(output="z" * 5_000, llm_preview_chars=50)
    proj = btr.public_projection(delegation_available=True)

    assert btr.result_ref.startswith("result:sha256:")
    assert btr.result_ref in proj["delegationHint"]


def test_delegation_hint_advisory_mentions_delegation() -> None:
    """The hint string advises delegation, not inlining."""
    btr = _budgeted(output="a" * 5_000, llm_preview_chars=50)
    proj = btr.public_projection(delegation_available=True)

    hint = proj["delegationHint"]
    assert "delegate" in hint.lower() or "sub-agent" in hint.lower() or "subagent" in hint.lower()


# ---------------------------------------------------------------------------
# 1a-iii  flag on + NOT truncated → no hint
# ---------------------------------------------------------------------------

def test_delegation_hint_flag_on_not_truncated_no_hint() -> None:
    """When delegation_available=True but output was NOT truncated, no hint."""
    btr = _budgeted(output="small output", llm_preview_chars=4000)
    assert btr.truncation.llm_preview_truncated is False

    proj = btr.public_projection(delegation_available=True)
    assert "delegationHint" not in proj


def test_delegation_hint_flag_on_not_truncated_projection_identical_to_flag_off() -> None:
    """Not-truncated + flag-on projection equals not-truncated + flag-off projection."""
    btr = _budgeted(output="tiny", llm_preview_chars=4000)

    proj_off = btr.public_projection(delegation_available=False)
    proj_on = btr.public_projection(delegation_available=True)

    assert proj_off == proj_on


# ---------------------------------------------------------------------------
# Projection structure is otherwise unchanged
# ---------------------------------------------------------------------------

def test_delegation_hint_does_not_change_other_keys() -> None:
    """Adding the hint key does not alter any other projection key."""
    btr = _budgeted(output="b" * 5_000, llm_preview_chars=50)

    proj_off = btr.public_projection(delegation_available=False)
    proj_on = btr.public_projection(delegation_available=True)

    # hint is the only extra key
    extra_keys = set(proj_on) - set(proj_off)
    assert extra_keys == {"delegationHint"}

    # all other keys are byte-for-byte identical
    for key in proj_off:
        assert proj_off[key] == proj_on[key], f"key {key!r} changed unexpectedly"
