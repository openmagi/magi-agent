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

0.1.84 repro with the flag ON still produced ZERO log lines: the
helpers were using ``_logger.warning(...)`` but ``magi-serve`` doesn't
call ``logging.basicConfig`` / ``dictConfig`` so the root logger had
no handler attached. Emit goes through ``sys.stderr`` now (same stream
uvicorn/pydantic warnings use); these tests pin that channel via
``capsys`` instead of ``caplog``.
"""

from __future__ import annotations

from magi_agent.runtime.child_runner_live import (
    CHILD_RUNNER_EMPTY_DEBUG_ENV,
    _maybe_log_governed_collect_result,
    _maybe_log_legacy_collect_result,
)


def test_governed_log_silent_when_flag_off(capsys) -> None:
    _maybe_log_governed_collect_result(
        {},
        provider="anthropic",
        model="claude-opus-4-8",
        summary="",
        evidence_refs=(),
        status="completed",
    )
    captured = capsys.readouterr()
    assert captured.err == ""


def test_governed_log_fires_when_flag_on(capsys) -> None:
    _maybe_log_governed_collect_result(
        {CHILD_RUNNER_EMPTY_DEBUG_ENV: "1"},
        provider="anthropic",
        model="claude-opus-4-8",
        summary="  ",  # whitespace, would bypass the `not summary` guard if any.
        evidence_refs=("evidence:abc", "evidence:def"),
        status="completed",
    )
    err = capsys.readouterr().err
    assert err.count("\n") == 1
    assert "governed_branch" in err
    assert "anthropic" in err
    assert "claude-opus-4-8" in err
    # summary_len reflects RAW length; summary_stripped_len reflects what
    # the guard's `not summary.strip()` would see, both matter for the
    # bypass diagnosis.
    assert "summary_len=2" in err
    assert "summary_stripped_len=0" in err
    assert "evidence_refs_count=2" in err
    assert "first_ref='evidence:abc'" in err
    assert "status=completed" in err


def test_legacy_log_silent_when_flag_off(capsys) -> None:
    _maybe_log_legacy_collect_result(
        {},
        provider="fireworks",
        model="kimi-k2p6",
        text_chunks=0,
        text_total_len=0,
        evidence_refs=(),
    )
    assert capsys.readouterr().err == ""


def test_legacy_log_fires_when_flag_on(capsys) -> None:
    _maybe_log_legacy_collect_result(
        {CHILD_RUNNER_EMPTY_DEBUG_ENV: "true"},
        provider="fireworks",
        model="kimi-k2p6",
        text_chunks=3,
        text_total_len=0,
        evidence_refs=(),
    )
    err = capsys.readouterr().err
    assert err.count("\n") == 1
    assert "legacy_branch" in err
    assert "fireworks" in err
    assert "kimi-k2p6" in err
    # 3 chunks but zero total length: exactly the silent-whitespace
    # bypass shape the operator needs to spot.
    assert "text_chunks=3" in err
    assert "text_total_len=0" in err
    assert "evidence_refs_count=0" in err


def test_log_never_raises_on_exotic_inputs(capsys) -> None:
    """Logging must never crash the turn even on weird inputs (provider/
    model as None, evidence_refs that aren't a tuple, etc.)."""
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
    err = capsys.readouterr().err
    # Two lines emitted, no exception.
    assert err.count("\n") == 2
