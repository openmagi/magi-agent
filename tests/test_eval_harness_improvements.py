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


def test_eval_autonomy_parser_default_on():
    assert parse_eval_autonomy_enabled({}) is True
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
