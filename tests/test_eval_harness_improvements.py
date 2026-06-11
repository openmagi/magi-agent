"""Tests for eval harness improvements P1 + P2 + P3.

Run with:
    MAGI_CONFIG=$(mktemp) uv run pytest tests/test_eval_harness_improvements.py -v
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Task 1 — P1: loop-guard thresholds in eval defaults
# ---------------------------------------------------------------------------

from magi_agent.runtime.local_defaults import apply_local_eval_runtime_defaults


def test_eval_relaxes_loop_guard_thresholds():
    env = {"MAGI_RUNTIME_PROFILE": "eval"}
    apply_local_eval_runtime_defaults(env)
    # loop-guard stays ON but thresholds are raised so test-iteration is not blocked
    assert env["MAGI_LOOP_GUARD_ENABLED"] == "1"
    assert int(env["MAGI_LOOP_GUARD_HARD_THRESHOLD"]) >= 40
    assert int(env["MAGI_LOOP_GUARD_SOFT_THRESHOLD"]) >= 20
    assert int(env["MAGI_LOOP_GUARD_FREQUENCY_HARD_THRESHOLD"]) >= 150
    assert int(env["MAGI_LOOP_GUARD_FREQUENCY_SOFT_THRESHOLD"]) >= 60


def test_eval_loop_guard_thresholds_respect_explicit_override():
    env = {"MAGI_RUNTIME_PROFILE": "eval", "MAGI_LOOP_GUARD_HARD_THRESHOLD": "7"}
    apply_local_eval_runtime_defaults(env)
    assert env["MAGI_LOOP_GUARD_HARD_THRESHOLD"] == "7"  # setdefault must not override


# ---------------------------------------------------------------------------
# Task 2 — P2: eval autonomy + self-verification prompt directive
# ---------------------------------------------------------------------------

import os
from magi_agent.config.env import parse_eval_autonomy_enabled


def test_eval_autonomy_parser_default_off():
    assert parse_eval_autonomy_enabled({}) is False
    assert parse_eval_autonomy_enabled({"MAGI_EVAL_AUTONOMY_ENABLED": "0"}) is False


def test_eval_autonomy_parser_explicit_on():
    assert parse_eval_autonomy_enabled({"MAGI_EVAL_AUTONOMY_ENABLED": "1"}) is True
    assert parse_eval_autonomy_enabled({"MAGI_EVAL_AUTONOMY_ENABLED": "true"}) is True


def test_eval_defaults_set_autonomy_flag():
    env = {"MAGI_RUNTIME_PROFILE": "eval"}
    apply_local_eval_runtime_defaults(env)
    assert env["MAGI_EVAL_AUTONOMY_ENABLED"] == "1"


def test_eval_autonomy_block_enabled():
    from magi_agent.cli.tool_runtime import eval_autonomy_block
    text = eval_autonomy_block({"MAGI_EVAL_AUTONOMY_ENABLED": "1"})
    assert "run the project's existing tests" in text.lower() or "test-verified" in text.lower()
    assert "never ask for confirmation" in text.lower()


def test_eval_autonomy_block_disabled():
    from magi_agent.cli.tool_runtime import eval_autonomy_block
    text = eval_autonomy_block({"MAGI_EVAL_AUTONOMY_ENABLED": "0"})
    assert text == ""


def test_eval_autonomy_block_carries_workflow_recipe():
    """The measured prompt ingredients: reproduce-first workflow, root-cause
    framing, thoroughness, do-not-modify-tests boundary, and diff hygiene."""
    from magi_agent.cli.tool_runtime import eval_autonomy_block

    text = eval_autonomy_block({"MAGI_EVAL_AUTONOMY_ENABLED": "1"}).lower()
    assert "reproduc" in text  # write a reproduction script FIRST
    assert "root cause" in text
    assert "do not modify" in text and "test" in text  # test-file boundary
    assert "git diff" in text  # diff hygiene: only intended source files
    assert "as many tool calls as" in text or "do not stop early" in text


# ---------------------------------------------------------------------------
# Task 3 — P3: engine zero-edit guard
# ---------------------------------------------------------------------------

from magi_agent.config.env import parse_eval_zero_edit_guard_enabled
from magi_agent.cli.engine import should_reprompt_for_zero_edits


def test_zero_edit_guard_parser_default_off():
    assert parse_eval_zero_edit_guard_enabled({}) is False
    assert parse_eval_zero_edit_guard_enabled({"MAGI_EVAL_ZERO_EDIT_GUARD_ENABLED": "0"}) is False


def test_zero_edit_guard_parser_explicit_on():
    assert parse_eval_zero_edit_guard_enabled({"MAGI_EVAL_ZERO_EDIT_GUARD_ENABLED": "1"}) is True


def test_eval_defaults_set_zero_edit_guard_flag():
    env = {"MAGI_RUNTIME_PROFILE": "eval"}
    apply_local_eval_runtime_defaults(env)
    assert env["MAGI_EVAL_ZERO_EDIT_GUARD_ENABLED"] == "1"


def test_reprompt_when_no_edits_and_not_yet_retried():
    assert should_reprompt_for_zero_edits(
        file_edits=0, already_reprompted=False, enabled=True
    ) is True


def test_no_reprompt_when_edits_present():
    assert should_reprompt_for_zero_edits(
        file_edits=2, already_reprompted=False, enabled=True
    ) is False


def test_no_reprompt_when_already_retried_or_disabled():
    assert should_reprompt_for_zero_edits(file_edits=0, already_reprompted=True, enabled=True) is False
    assert should_reprompt_for_zero_edits(file_edits=0, already_reprompted=False, enabled=False) is False


# ---------------------------------------------------------------------------
# New task — verification-discipline bullets in autonomy block
# ---------------------------------------------------------------------------


def test_autonomy_block_includes_existing_test_discovery(monkeypatch):
    monkeypatch.setenv("MAGI_EVAL_AUTONOMY_ENABLED", "1")
    from magi_agent.cli.tool_runtime import eval_autonomy_block
    text = eval_autonomy_block()
    assert "parametrized variants" in text
    assert "cold-interpreter import" in text
    assert "Before deleting any file" in text
    assert "behavior-based assertions" in text


# ---------------------------------------------------------------------------
# New task — deadline-awareness nudge (default-OFF, env-driven)
# ---------------------------------------------------------------------------

from magi_agent.runtime import deadline as deadline_mod


def test_deadline_inert_when_unset():
    deadline_mod.reset_for_tests()
    assert deadline_mod.deadline_note({}, now=0.0) is None
    assert deadline_mod.deadline_note({}, now=10_000.0) is None


def test_deadline_fires_once_per_threshold():
    deadline_mod.reset_for_tests()
    env = {"MAGI_EVAL_DEADLINE_SECONDS": "1000"}
    assert deadline_mod.deadline_note(env, now=0.0) is None        # anchor
    assert deadline_mod.deadline_note(env, now=100.0) is None      # 10%
    note60 = deadline_mod.deadline_note(env, now=650.0)            # 65%
    assert note60 is not None and "60%" in note60
    assert deadline_mod.deadline_note(env, now=700.0) is None      # no repeat
    note85 = deadline_mod.deadline_note(env, now=900.0)            # 90%
    assert note85 is not None and "85%" in note85
    assert deadline_mod.deadline_note(env, now=950.0) is None


def test_deadline_invalid_value_inert():
    deadline_mod.reset_for_tests()
    assert deadline_mod.deadline_note({"MAGI_EVAL_DEADLINE_SECONDS": "abc"}, now=0.0) is None
    assert deadline_mod.deadline_note({"MAGI_EVAL_DEADLINE_SECONDS": "-5"}, now=0.0) is None
