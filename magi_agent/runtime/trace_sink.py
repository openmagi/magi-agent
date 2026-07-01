"""File-backed diagnostic trace sink.

PR-G of the silent child-runner hunt. Centralises the ``_emit_trace`` helper
that every dispatch-path / boundary / model-tiers trace stamp funnels through,
and writes lines to a dedicated file instead of ``sys.stderr``.

Why a file instead of stderr
----------------------------
PR #994 routed all ten dispatch-path trace helpers through
``print(line, file=sys.stderr, flush=True)`` because ``magi-serve`` does not
call ``logging.basicConfig`` / ``dictConfig`` so a ``_logger.warning(...)``
would never reach the operator's serve log.

Kevin's 0.1.86 long-running SOTA-spawn repro then surfaced a second failure
mode: a 21 minute session with 39 tool calls and two spawned child agents
(each emitting ~1900 ``text_delta`` chunks) saw ``magi-serve.log`` mtime
FREEZE at 21:59:24 while SQLite kept getting writes until 22:04:34. The
uvicorn stderr / stdout file descriptor got wedged mid-session (child
subprocess holding the FD or buffer-fill back-pressure on the parent FD).
Every trace stamp PR #1010 (PR-1) and PR #1072 (PR-H) fire during the frozen
window is lost. The operator cannot capture the diagnostic data the
follow-up root-cause PR needs.

The fix is to give the trace channel its OWN file descriptor: a single
process-wide append-only file handle distinct from the uvicorn FDs. A wedged
stdout / stderr no longer freezes diagnostics.

Path resolution
---------------
* Default: ``~/.openmagi/trace.log`` (parent directory created on demand).
* Override: ``MAGI_TRACE_LOG_PATH`` environment variable. ``~`` and ``$VAR``
  expansion is applied so an operator can set the override exactly the way
  they would on the shell.

The sink opens the file lazily on the first ``_emit_trace`` call, in append
mode, line-buffered, and keeps the file object on a module-level ``_TRACE_FD``
so the FD is reused for every subsequent call. There is no per-call open
cost.

Fail-soft
---------
``_emit_trace`` MUST NEVER raise. Logging is not allowed to break a turn
under any condition: any ``OSError`` / ``IOError`` on open OR write is
swallowed and the line is dropped silently. Crucially the sink does NOT
fall back to ``sys.stderr`` on failure: that fallback is exactly the FD
path this PR exists to bypass. A best-effort drop is the correct behaviour:
the operator can flip ``MAGI_TRACE_LOG_PATH`` to a writable path and the
next call will reopen.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import IO

#: Environment variable name an operator sets to override the default trace
#: path. Accepts an absolute path with ``~`` and ``$VAR`` expansion.
MAGI_TRACE_LOG_PATH_ENV = "MAGI_TRACE_LOG_PATH"

#: Default trace path relative to ``$HOME``. The sink creates the parent
#: directory on first write so the operator does not have to pre-create
#: ``~/.openmagi``.
_DEFAULT_TRACE_PATH_RELATIVE = ".openmagi/trace.log"

#: Module-level append-mode file handle reused across every ``_emit_trace``
#: call in this process. Lazy: ``None`` until the first call. The sink does
#: not close it; the OS reaps it at process exit.
_TRACE_FD: IO[str] | None = None


def resolve_trace_path(env: "os._Environ[str] | dict[str, str] | None" = None) -> Path:
    """Return the resolved trace-log path for the current process.

    * Honours ``MAGI_TRACE_LOG_PATH`` when set in ``env`` (defaults to
      ``os.environ``).
    * Applies ``~`` and ``$VAR`` expansion to the override AND to the
      default (the default depends on ``$HOME`` which can be reassigned in
      tests).
    * Returns an absolute :class:`Path`. Does NOT touch the filesystem.
    """
    source = os.environ if env is None else env
    raw = source.get(MAGI_TRACE_LOG_PATH_ENV, "").strip()
    if raw:
        expanded = os.path.expandvars(os.path.expanduser(raw))
        return Path(expanded)
    home = Path(os.path.expanduser(source.get("HOME", "~"))) if env is not None else Path.home()
    return home / _DEFAULT_TRACE_PATH_RELATIVE


def reset_trace_fd_for_tests() -> None:
    """Close (best-effort) and clear the module-level ``_TRACE_FD``.

    Test-only helper. Production code MUST NOT call this: the sink is
    designed to keep a single FD open for the lifetime of the process.
    """
    global _TRACE_FD
    fd = _TRACE_FD
    _TRACE_FD = None
    if fd is None:
        return
    try:
        fd.close()
    except Exception:  # noqa: BLE001 - test helper must never raise.
        return


def _open_trace_fd() -> IO[str] | None:
    """Open the trace file lazily. Returns ``None`` if the open fails.

    Creates the parent directory if it does not exist. Append mode and a
    line-buffered text stream so the operator can ``tail -f`` the file and
    see every emit immediately without an explicit ``flush``.
    """
    path = resolve_trace_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # ``buffering=1`` => line-buffered text mode; every newline-terminated
        # write hits the OS without us calling ``flush()`` ourselves.
        return open(path, "a", buffering=1, encoding="utf-8")  # noqa: SIM115
    except (OSError, ValueError):
        # ``ValueError`` covers pathological cases like an empty path string.
        # Anything else (PermissionError, FileNotFoundError on a deleted
        # parent race, etc.) is just an ``OSError`` subclass.
        return None


def _emit_trace(line: str) -> None:
    """Append ``line`` (plus a newline) to the trace file.

    Lazy: opens the file on the first call and caches the FD on a
    module-level slot reused thereafter. Fail-soft: any open / write
    failure is swallowed and the line is dropped: the sink does NOT fall
    back to ``sys.stderr`` because that is the wedged FD this PR exists to
    bypass.
    """
    global _TRACE_FD
    fd = _TRACE_FD
    if fd is None:
        fd = _open_trace_fd()
        if fd is None:
            return
        _TRACE_FD = fd
    try:
        fd.write(line if line.endswith("\n") else line + "\n")
    except (OSError, ValueError):
        return
