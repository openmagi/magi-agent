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
