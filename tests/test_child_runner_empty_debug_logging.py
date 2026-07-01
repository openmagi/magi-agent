"""Operator-opt-in debug logging for child-runner empty-response paths.

Kevin's 0.1.77 SOTA-spawn repro showed anthropic / fireworks children
ending with ``llmOutput={status:"ok"}`` + empty result even though PRs
#854 / #876 guards SHOULD have raised
``_ChildLlmTurnError(child_llm_empty_response)`` in both branches
(legacy + governed). The two possible bypass paths:

  1. ``summary`` or ``text_chunks`` came back non-empty (whitespace,
     unexpected text) -> guard ``not summary`` falsy -> no raise.
  2. ``evidence_refs`` came back non-empty (unexpected ref leakage
     from a reasoning chunk's metadata) -> guard ``not evidence_refs``
     falsy -> no raise.

0.1.84 repro with the flag ON still produced ZERO log lines: the
helpers were using ``_logger.warning(...)`` but ``magi-serve`` doesn't
call ``logging.basicConfig`` / ``dictConfig`` so the root logger had
no handler attached. PR #994 routed emit through ``sys.stderr``. PR-G
moved the channel to a dedicated file (``~/.openmagi/trace.log`` by
default, override with ``MAGI_TRACE_LOG_PATH``) because Kevin's 0.1.86
long-running session wedged the uvicorn stderr FD mid-session. These
tests read the file channel back.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from magi_agent.runtime import trace_sink
from magi_agent.runtime.child_runner_live import (
    CHILD_RUNNER_EMPTY_DEBUG_ENV,
    _maybe_log_governed_collect_result,
    _maybe_log_legacy_collect_result,
)


@pytest.fixture
def trace_log(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    path = tmp_path / "trace.log"
    monkeypatch.setenv(trace_sink.MAGI_TRACE_LOG_PATH_ENV, str(path))
    trace_sink.reset_trace_fd_for_tests()
    yield path
    trace_sink.reset_trace_fd_for_tests()


def _read_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_governed_log_silent_when_flag_off(trace_log: Path) -> None:
    _maybe_log_governed_collect_result(
        {},
        provider="anthropic",
        model="claude-opus-4-8",
        summary="",
        evidence_refs=(),
        status="completed",
    )
    assert _read_lines(trace_log) == []


def test_governed_log_fires_when_flag_on(trace_log: Path) -> None:
    _maybe_log_governed_collect_result(
        {CHILD_RUNNER_EMPTY_DEBUG_ENV: "1"},
        provider="anthropic",
        model="claude-opus-4-8",
        summary="  ",  # whitespace, would bypass the `not summary` guard if any.
        evidence_refs=("evidence:abc", "evidence:def"),
        status="completed",
    )
    lines = _read_lines(trace_log)
    assert len(lines) == 1
    line = lines[0]
    assert "governed_branch" in line
    assert "anthropic" in line
    assert "claude-opus-4-8" in line
    # summary_len reflects RAW length; summary_stripped_len reflects what
    # the guard's `not summary.strip()` would see, both matter for the
    # bypass diagnosis.
    assert "summary_len=2" in line
    assert "summary_stripped_len=0" in line
    assert "evidence_refs_count=2" in line
    assert "first_ref='evidence:abc'" in line
    assert "status=completed" in line


def test_legacy_log_silent_when_flag_off(trace_log: Path) -> None:
    _maybe_log_legacy_collect_result(
        {},
        provider="fireworks",
        model="kimi-k2p6",
        text_chunks=0,
        text_total_len=0,
        evidence_refs=(),
    )
    assert _read_lines(trace_log) == []


def test_legacy_log_fires_when_flag_on(trace_log: Path) -> None:
    _maybe_log_legacy_collect_result(
        {CHILD_RUNNER_EMPTY_DEBUG_ENV: "true"},
        provider="fireworks",
        model="kimi-k2p6",
        text_chunks=3,
        text_total_len=0,
        evidence_refs=(),
    )
    lines = _read_lines(trace_log)
    assert len(lines) == 1
    line = lines[0]
    assert "legacy_branch" in line
    assert "fireworks" in line
    assert "kimi-k2p6" in line
    # 3 chunks but zero total length: exactly the silent-whitespace
    # bypass shape the operator needs to spot.
    assert "text_chunks=3" in line
    assert "text_total_len=0" in line
    assert "evidence_refs_count=0" in line


def test_log_never_raises_on_exotic_inputs(trace_log: Path) -> None:
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
    lines = _read_lines(trace_log)
    # Two lines emitted, no exception.
    assert len(lines) == 2
