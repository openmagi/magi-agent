"""Daemon liveness: `magi gateway start` records a pidfile while supervising and
`magi gateway status` reports whether a daemon process is actually alive — not
merely whether the env gate is set.

The pidfile mechanism is pure/import-clean: no network, no real process spawn in
these tests.  Liveness is probed with ``os.kill(pid, 0)`` so a stale pidfile
(pid no longer running) reports ``not running`` rather than a false positive.
"""
from __future__ import annotations

import os

import pytest

from magi_agent.gateway import pidfile


def test_pidfile_path_honours_state_dir(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("MAGI_STATE_DIR", str(tmp_path))
    path = pidfile.gateway_pidfile_path()
    assert path == tmp_path / "gateway" / "daemon.pid"


def test_write_then_read_roundtrip(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("MAGI_STATE_DIR", str(tmp_path))
    pidfile.write_pidfile(pid=os.getpid())
    assert pidfile.read_pidfile() == os.getpid()


def test_status_running_for_live_pid(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """A pidfile naming a live process (this test process) → running=True."""
    monkeypatch.setenv("MAGI_STATE_DIR", str(tmp_path))
    pidfile.write_pidfile(pid=os.getpid())
    status = pidfile.daemon_liveness()
    assert status.running is True
    assert status.pid == os.getpid()


def test_status_not_running_for_stale_pid(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """A pidfile naming a dead process → running=False (no false positive)."""
    monkeypatch.setenv("MAGI_STATE_DIR", str(tmp_path))
    # find a pid that is (almost certainly) not running
    dead_pid = 2_000_000_000
    pidfile.write_pidfile(pid=dead_pid)
    status = pidfile.daemon_liveness()
    assert status.running is False
    assert status.pid == dead_pid


def test_status_no_pidfile(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("MAGI_STATE_DIR", str(tmp_path))
    status = pidfile.daemon_liveness()
    assert status.running is False
    assert status.pid is None


def test_remove_pidfile_is_idempotent(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("MAGI_STATE_DIR", str(tmp_path))
    # removing a non-existent pidfile is a no-op
    assert pidfile.remove_pidfile() is False
    pidfile.write_pidfile(pid=os.getpid())
    assert pidfile.remove_pidfile() is True
    assert pidfile.read_pidfile() is None


def test_read_corrupt_pidfile_is_none(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """A non-integer pidfile must not crash status — it reads as absent."""
    monkeypatch.setenv("MAGI_STATE_DIR", str(tmp_path))
    path = pidfile.gateway_pidfile_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not-a-pid", encoding="utf-8")
    assert pidfile.read_pidfile() is None
    status = pidfile.daemon_liveness()
    assert status.running is False
    assert status.pid is None
