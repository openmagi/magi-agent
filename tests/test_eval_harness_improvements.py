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
