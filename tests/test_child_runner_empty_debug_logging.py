"""Operator-opt-in debug logging for child-runner empty-response paths.

Kevin's 0.1.77 SOTA-spawn repro showed anthropic / fireworks children
ending with ``llmOutput={status:"ok"}`` + empty result even though PRs
#854 / #876 guards SHOULD have raised
``_ChildLlmTurnError(child_llm_empty_response)`` in both branches
(legacy + governed). The two possible bypass paths:

  1. ``summary`` or ``text_chunks`` came back non-empty (whitespace,
     unexpected text) → guard ``not summary`` falsy → no raise.
  2. ``evidence_refs`` came back non-empty (unexpected ref leakage
     from a reasoning chunk's metadata) → guard ``not evidence_refs``
     falsy → no raise.

Without production observability we can't tell which. These tests pin
the new diagnostic surface so the operator's next repro logs the actual
values without needing a debug wheel build.
"""
from __future__ import annotations

import logging

from magi_agent.runtime.child_runner_live import (
    CHILD_RUNNER_EMPTY_DEBUG_ENV,
    _maybe_log_governed_collect_result,
    _maybe_log_legacy_collect_result,
)


def test_governed_log_silent_when_flag_off(caplog) -> None:
    caplog.set_level(logging.DEBUG)
    _maybe_log_governed_collect_result(
        {},
        provider="anthropic",
        model="claude-opus-4-8",
        summary="",
        evidence_refs=(),
        status="completed",
    )
    assert caplog.records == []


def test_governed_log_fires_when_flag_on(caplog) -> None:
    caplog.set_level(logging.WARNING)
    _maybe_log_governed_collect_result(
        {CHILD_RUNNER_EMPTY_DEBUG_ENV: "1"},
        provider="anthropic",
        model="claude-opus-4-8",
        summary="  ",  # whitespace — would bypass the `not summary` guard if any.
        evidence_refs=("evidence:abc", "evidence:def"),
        status="completed",
    )
    assert len(caplog.records) == 1
    msg = caplog.records[0].getMessage()
    assert "governed_branch" in msg
    assert "anthropic" in msg
    assert "claude-opus-4-8" in msg
    # summary_len reflects RAW length; summary_stripped_len reflects what
    # the guard's `not summary.strip()` would see — both matter for the
    # bypass diagnosis.
    assert "summary_len=2" in msg
    assert "summary_stripped_len=0" in msg
    assert "evidence_refs_count=2" in msg
    assert "first_ref='evidence:abc'" in msg
    assert "status=completed" in msg


def test_legacy_log_silent_when_flag_off(caplog) -> None:
    caplog.set_level(logging.DEBUG)
    _maybe_log_legacy_collect_result(
        {},
        provider="fireworks",
        model="kimi-k2p6",
        text_chunks=0,
        text_total_len=0,
        evidence_refs=(),
    )
    assert caplog.records == []


def test_legacy_log_fires_when_flag_on(caplog) -> None:
    caplog.set_level(logging.WARNING)
    _maybe_log_legacy_collect_result(
        {CHILD_RUNNER_EMPTY_DEBUG_ENV: "true"},
        provider="fireworks",
        model="kimi-k2p6",
        text_chunks=3,
        text_total_len=0,
        evidence_refs=(),
    )
    assert len(caplog.records) == 1
    msg = caplog.records[0].getMessage()
    assert "legacy_branch" in msg
    assert "fireworks" in msg
    assert "kimi-k2p6" in msg
    # 3 chunks but zero total length — exactly the silent-whitespace
    # bypass shape the operator needs to spot.
    assert "text_chunks=3" in msg
    assert "text_total_len=0" in msg
    assert "evidence_refs_count=0" in msg


def test_log_never_raises_on_exotic_inputs(caplog) -> None:
    """Logging must never crash the turn even on weird inputs (provider/
    model as None, evidence_refs that aren't a tuple, etc.)."""
    caplog.set_level(logging.WARNING)
    # Should NOT raise.
    _maybe_log_governed_collect_result(
        {CHILD_RUNNER_EMPTY_DEBUG_ENV: "1"},
        provider=None,
        model=None,
        summary="",
        evidence_refs=(),
        status="failed",
    )
    _maybe_log_legacy_collect_result(
        {CHILD_RUNNER_EMPTY_DEBUG_ENV: "1"},
        provider=None,
        model=None,
        text_chunks=0,
        text_total_len=0,
        evidence_refs=(),
    )
    # Two warning lines emitted, no exception.
    assert len(caplog.records) == 2
