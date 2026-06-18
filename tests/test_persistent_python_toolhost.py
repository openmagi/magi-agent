"""Regression tests for the killable-subprocess persistent-python toolhost (B-3).

The persistent-python pack used to run user code on a daemon ``threading.Thread``
and wait with ``Thread.join(timeout=...)``. On timeout the daemon thread kept
running — Python cannot kill a thread — so a ``while True`` cell pinned a CPU
core for the life of the process and leaked one thread per timeout. These tests
prove that the toolhost now executes in a killable subprocess whose runaway code
is actually terminated on timeout, while CodeAct persistence / isolation /
last-expression echo / output capping are preserved.

Hermetic, no network.
"""
from __future__ import annotations

import time
from pathlib import Path

from magi_agent.tools.context import ToolContext
from magi_agent.tools.persistent_python_toolhost import PersistentPythonHandlerSet


def _ctx(turn_id: str, *, session_id: str | None = None, workspace: str | None = None) -> ToolContext:
    return ToolContext(
        botId="test-bot",
        sessionId=session_id,
        turnId=turn_id,
        workspaceRoot=workspace or "/tmp",
    )


def _stdout(output: object) -> str:
    if isinstance(output, dict):
        parts = [str(output.get("stdout") or ""), str(output.get("value") or "")]
        return "\n".join(p for p in parts if p) or str(output)
    return str(output)


def test_runaway_loop_is_actually_killed_on_timeout(tmp_path: Path) -> None:
    """A runaway ``while True`` cell must be KILLED on timeout, not abandoned.

    RED on the thread-timeout model: the daemon thread keeps appending to the
    sentinel file after the handler returns, so the file keeps growing. With the
    killable subprocess the process group is SIGKILLed and the file stops growing.
    """
    handler_set = PersistentPythonHandlerSet(timeout_s=0.3)
    sentinel = tmp_path / "runaway.log"
    code = (
        "f = open({path!r}, 'w')\n"
        "i = 0\n"
        "while True:\n"
        "    i += 1\n"
        "    f.write('x')\n"
        "    f.flush()\n"
    ).format(path=str(sentinel))

    result = handler_set._handle({"code": code}, _ctx("turn-runaway"))

    assert result.status == "error"
    assert "TimeoutError" in str(result.error_message)

    # Give any (incorrectly) surviving worker time to keep writing.
    assert sentinel.exists()
    size_after_timeout = sentinel.stat().st_size
    time.sleep(0.8)
    size_later = sentinel.stat().st_size

    # The runaway compute must have stopped: the file is no longer growing.
    assert size_later == size_after_timeout, (
        "runaway subprocess kept writing after timeout — it was not killed "
        f"(size {size_after_timeout} -> {size_later})"
    )


def test_timeout_resets_namespace_for_next_call() -> None:
    handler_set = PersistentPythonHandlerSet(timeout_s=0.3)
    ctx = _ctx("turn-timeout")

    seeded = handler_set._handle({"code": "x = 41"}, ctx)
    assert seeded.status == "ok"

    timed_out = handler_set._handle({"code": "while True:\n    pass"}, ctx)
    assert timed_out.status == "error"
    assert "TimeoutError" in str(timed_out.error_message)

    # A fresh, fast call after the timeout must work AND see a reset namespace.
    fresh = handler_set._handle({"code": "print(x)"}, ctx)
    assert fresh.status == "error"
    assert "NameError" in str(fresh.error_message) + str(fresh.output or "")


def test_codeact_state_persists_within_same_key() -> None:
    handler_set = PersistentPythonHandlerSet()
    ctx = _ctx("turn-shared")
    first = handler_set._handle({"code": "x = 41"}, ctx)
    assert first.status == "ok"
    second = handler_set._handle({"code": "x + 1"}, ctx)
    assert second.status == "ok"
    assert "42" in _stdout(second.output)


def test_state_isolated_across_different_keys() -> None:
    handler_set = PersistentPythonHandlerSet()
    seeded = handler_set._handle({"code": "secret = 1234"}, _ctx("turn-a"))
    assert seeded.status == "ok"
    leaked = handler_set._handle({"code": "print(secret)"}, _ctx("turn-b"))
    assert leaked.status == "error"
    assert "1234" not in str(leaked.output or "")


def test_last_expression_echo_and_stdout() -> None:
    handler_set = PersistentPythonHandlerSet()
    result = handler_set._handle({"code": "print('hi')\n40 + 2"}, _ctx("turn-echo"))
    assert result.status == "ok"
    assert "hi" in _stdout(result.output)
    assert "42" in _stdout(result.output)


def test_arbitrary_imports_allowed_full_trust() -> None:
    """Persistent-python is full-trust local: arbitrary imports must still work."""
    handler_set = PersistentPythonHandlerSet()
    result = handler_set._handle(
        {"code": "import os\nprint(os.getpid() > 0)"}, _ctx("turn-imports")
    )
    assert result.status == "ok"
    assert "True" in _stdout(result.output)


def test_large_output_is_head_tail_capped() -> None:
    handler_set = PersistentPythonHandlerSet()
    result = handler_set._handle(
        {"code": "print('A' * 1_000_000)"}, _ctx("turn-big")
    )
    assert result.status == "ok"
    stdout = _stdout(result.output)
    assert len(stdout) < 200_000
    assert "elided" in stdout or "truncated" in stdout


def test_missing_code_is_error() -> None:
    handler_set = PersistentPythonHandlerSet()
    result = handler_set._handle({}, _ctx("turn-missing"))
    assert result.status == "error"
    assert result.error_code == "missing_code"
