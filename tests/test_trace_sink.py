"""File-backed trace sink (PR-G).

PR #994 routed every dispatch-path / boundary trace stamp through
``print(line, file=sys.stderr, flush=True)`` because ``magi-serve`` does
not call ``logging.basicConfig`` and a ``_logger.warning(...)`` would
never reach the operator. Kevin's 0.1.86 long-running session then showed
the uvicorn stderr FD wedging mid-session (log mtime froze at 21:59:24
while SQLite kept getting writes until 22:04:34). The trace stamps fired
into the wedged FD and were lost.

This module pins the file-backed sink: a dedicated FD distinct from the
uvicorn stdout / stderr handles so a wedged uvicorn FD no longer freezes
the diagnostic channel.

Coverage:

* Default path resolves under ``$HOME``.
* ``MAGI_TRACE_LOG_PATH`` override honours ``~`` and ``$VAR`` expansion.
* Append semantics: three emits leave three lines.
* Parent directory created on first write.
* Open failures swallowed (no exception, no traceback).
* Write failures swallowed.
* FD reused across calls (open invoked once per process).
* Failure does NOT fall back to stderr (capsys.err stays empty).
"""

from __future__ import annotations

import builtins
import sys
from pathlib import Path

import pytest

from magi_agent.runtime import trace_sink
from magi_agent.runtime.trace_sink import (
    MAGI_TRACE_LOG_PATH_ENV,
    _emit_trace,
    reset_trace_fd_for_tests,
    resolve_trace_path,
)


@pytest.fixture(autouse=True)
def _reset_sink(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Reset the module-level FD before AND after each test.

    Also points the default path under ``tmp_path`` (via ``HOME``) so a
    test that never sets ``MAGI_TRACE_LOG_PATH`` does not write into the
    operator's real ``~/.openmagi`` directory.
    """
    reset_trace_fd_for_tests()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv(MAGI_TRACE_LOG_PATH_ENV, raising=False)
    yield
    reset_trace_fd_for_tests()


# --------------------------------------------------------------------------- #
# Path resolution                                                             #
# --------------------------------------------------------------------------- #


def test_default_path_resolves_to_home_openmagi(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Default path is ``$HOME/.openmagi/trace.log``."""
    fake_home = tmp_path / "fake"
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.delenv(MAGI_TRACE_LOG_PATH_ENV, raising=False)
    resolved = resolve_trace_path({"HOME": str(fake_home)})
    assert resolved == fake_home / ".openmagi" / "trace.log"


def test_env_override_resolves_with_tilde_and_vars(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``MAGI_TRACE_LOG_PATH`` honours ``~`` AND ``$VAR`` expansion."""
    fake_home = tmp_path / "home2"
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("MAGI_TRACE_LOG_PATH", "~/custom/path.log")
    resolved = resolve_trace_path()
    assert resolved == fake_home / "custom" / "path.log"

    monkeypatch.setenv("LOG_DIR", str(tmp_path / "vardir"))
    monkeypatch.setenv("MAGI_TRACE_LOG_PATH", "$LOG_DIR/inside.log")
    resolved2 = resolve_trace_path()
    assert resolved2 == tmp_path / "vardir" / "inside.log"


# --------------------------------------------------------------------------- #
# Append semantics                                                            #
# --------------------------------------------------------------------------- #


def test_emit_appends_line_to_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "trace.log"
    monkeypatch.setenv("MAGI_TRACE_LOG_PATH", str(path))

    _emit_trace("alpha")
    _emit_trace("beta")
    _emit_trace("gamma")

    contents = path.read_text(encoding="utf-8")
    lines = [line for line in contents.splitlines() if line]
    assert lines == ["alpha", "beta", "gamma"]


def test_emit_creates_parent_directory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """First write creates a missing parent directory chain."""
    path = tmp_path / "a" / "b" / "c" / "trace.log"
    monkeypatch.setenv("MAGI_TRACE_LOG_PATH", str(path))

    _emit_trace("hello")

    assert path.is_file()
    assert path.read_text(encoding="utf-8") == "hello\n"


# --------------------------------------------------------------------------- #
# Fail-soft on open / write                                                   #
# --------------------------------------------------------------------------- #


def test_emit_swallows_open_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``PermissionError`` on open does not raise, does not traceback."""
    path = tmp_path / "trace.log"
    monkeypatch.setenv("MAGI_TRACE_LOG_PATH", str(path))

    real_open = builtins.open

    def _raise_open(*args: object, **kwargs: object):
        # Only intercept the trace sink's open call (text + append).
        if args and str(args[0]) == str(path):
            raise PermissionError("simulated open failure")
        return real_open(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "open", _raise_open)

    # Must not raise.
    _emit_trace("dropped")
    # File never created.
    assert not path.exists()


def test_emit_swallows_write_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A write failure on the cached FD does not raise."""
    path = tmp_path / "trace.log"
    monkeypatch.setenv("MAGI_TRACE_LOG_PATH", str(path))

    # First emit opens the FD normally.
    _emit_trace("first")
    assert path.exists()

    # Replace the cached FD with one whose write raises.
    class _BrokenFD:
        def write(self, _: str) -> int:
            raise OSError("simulated write failure")

        def close(self) -> None:  # for reset_trace_fd_for_tests
            return None

    trace_sink._TRACE_FD = _BrokenFD()  # type: ignore[assignment]

    # Must not raise.
    _emit_trace("second")


# --------------------------------------------------------------------------- #
# FD lifecycle                                                                #
# --------------------------------------------------------------------------- #


def test_fd_reused_across_calls(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """100 emits open the file exactly once."""
    path = tmp_path / "trace.log"
    monkeypatch.setenv("MAGI_TRACE_LOG_PATH", str(path))

    real_open = builtins.open
    open_calls = {"count": 0}

    def _counting_open(*args: object, **kwargs: object):
        if args and str(args[0]) == str(path):
            open_calls["count"] += 1
        return real_open(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "open", _counting_open)

    for i in range(100):
        _emit_trace(f"line-{i}")

    assert open_calls["count"] == 1
    written = path.read_text(encoding="utf-8").splitlines()
    assert len(written) == 100


# --------------------------------------------------------------------------- #
# No stderr leak                                                              #
# --------------------------------------------------------------------------- #


def test_no_fallback_to_stderr_on_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """When the file cannot be opened, the line is dropped, not echoed to stderr.

    The whole point of the file-backed sink is to bypass the wedged uvicorn
    stderr FD. A silent fallback to stderr on failure would defeat the
    purpose, so the sink must NOT print anywhere on failure.
    """
    path = tmp_path / "nowhere" / "trace.log"
    monkeypatch.setenv("MAGI_TRACE_LOG_PATH", str(path))

    real_open = builtins.open

    def _raise_open(*args: object, **kwargs: object):
        if args and str(args[0]) == str(path):
            raise PermissionError("simulated")
        return real_open(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "open", _raise_open)

    _emit_trace("must-not-leak-to-stderr")
    sys.stderr.flush()

    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == ""
