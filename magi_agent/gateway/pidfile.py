"""Gateway daemon pidfile + liveness probe (Track F honest-defaults).

``magi gateway start`` (the real supervising daemon mode) records its pid here so
that ``magi gateway status`` can report whether a daemon is *actually running*,
not merely whether the env gate ``MAGI_GATEWAY_DAEMON_ENABLED`` is set.

Design
------
- Pure / import-clean: only ``os`` + ``pathlib`` at module top level (no network,
  no process spawn).  Liveness is probed with ``os.kill(pid, 0)`` (signal 0 is a
  permission/existence check that does not actually signal the process).
- A *stale* pidfile (the recorded pid is no longer running) reports
  ``running=False`` — never a false positive.
- A corrupt / non-integer pidfile reads as absent (status never crashes).
- Default state dir follows the existing ``MAGI_STATE_DIR`` convention
  (default ``~/.magi``), matching ``gateway.watchers``.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_GATEWAY_PIDFILE_RELATIVE = ("gateway", "daemon.pid")


def _state_dir() -> Path:
    return Path(os.environ.get("MAGI_STATE_DIR", "~/.magi")).expanduser()


def gateway_pidfile_path() -> Path:
    """Return the path of the gateway daemon pidfile (``<state>/gateway/daemon.pid``)."""
    return _state_dir().joinpath(*_GATEWAY_PIDFILE_RELATIVE)


def write_pidfile(*, pid: int) -> Path:
    """Write ``pid`` to the gateway pidfile (creating parent dirs); return its path."""
    path = gateway_pidfile_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{int(pid)}\n", encoding="utf-8")
    return path


def read_pidfile() -> int | None:
    """Return the recorded pid, or ``None`` if absent / unreadable / corrupt."""
    path = gateway_pidfile_path()
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, OSError):
        return None
    try:
        return int(raw)
    except (ValueError, TypeError):
        return None


def remove_pidfile() -> bool:
    """Remove the pidfile if present.  Returns True if a file was removed."""
    path = gateway_pidfile_path()
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


def _pid_is_alive(pid: int) -> bool:
    """Return True iff a process with ``pid`` exists (probe with signal 0)."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # The process exists but is owned by another user — still "alive".
        return True
    except OSError:
        return False
    return True


@dataclass(frozen=True)
class DaemonLiveness:
    """Result of a pidfile-based liveness probe."""

    running: bool
    pid: int | None


def daemon_liveness() -> DaemonLiveness:
    """Probe the pidfile and report whether the recorded daemon is alive.

    No pidfile          → ``DaemonLiveness(running=False, pid=None)``.
    Stale (dead) pid    → ``DaemonLiveness(running=False, pid=<pid>)``.
    Live pid            → ``DaemonLiveness(running=True,  pid=<pid>)``.
    """
    pid = read_pidfile()
    if pid is None:
        return DaemonLiveness(running=False, pid=None)
    return DaemonLiveness(running=_pid_is_alive(pid), pid=pid)


__all__ = [
    "DaemonLiveness",
    "daemon_liveness",
    "gateway_pidfile_path",
    "read_pidfile",
    "remove_pidfile",
    "write_pidfile",
]
