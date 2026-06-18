# tests/gates/test_gate5b_async_shell_liveness.py
"""Gate5B Bash/TestRun must run off the event loop (B-2).

The shell/test execution path historically called the blocking
``subprocess.Popen(...).wait(timeout=...)`` on the calling thread, which is the
async event-loop thread for the HTTP/SSE serving path. A long-running command
therefore stalled heartbeats, SSE emission, and cancel handling for up to the
command timeout (300s for TestRun).

These tests assert the loop stays live while a shell command runs, that
cancelling the dispatch task tears down the child process group, and that a
bounded ``max_concurrent_shell_commands`` cap is enforced.
"""
from __future__ import annotations

import asyncio
import os
import signal
from pathlib import Path

import pytest

from magi_agent.gates.gate5b_full_toolhost import (
    Gate5BFullToolHost,
    Gate5BFullToolHostConfig,
)


def _host(workspace: Path, *, tools: tuple[str, ...] = ("Bash", "TestRun")) -> Gate5BFullToolHost:
    config = Gate5BFullToolHostConfig.model_validate(
        {
            "enabled": True,
            "killSwitchEnabled": False,
            "maxToolCallsPerTurn": 32,
            # Long Bash timeout so the liveness window is governed by the sleep,
            # not by an early Bash timeout.
            "commandTimeoutMs": 60000,
            "maxPerToolOutputBytes": 4096,
        }
    )
    return Gate5BFullToolHost(
        config=config,
        workspace_root=workspace,
        exposed_tool_names=tools,
        now_ms=lambda: 0,
    )


@pytest.mark.asyncio
async def test_long_bash_does_not_block_event_loop(tmp_path):
    """A concurrent heartbeat coroutine must keep advancing while Bash sleeps."""
    host = _host(tmp_path)

    heartbeats = 0

    async def heartbeat() -> None:
        nonlocal heartbeats
        # Beat every 20ms; the shell command sleeps ~1.5s.
        while True:
            await asyncio.sleep(0.02)
            heartbeats += 1

    shell_task = asyncio.create_task(
        host._handle("Bash", {"command": "sleep 1.5"}, tool_call_id="live-bash")
    )
    beat_task = asyncio.create_task(heartbeat())

    out = await shell_task
    beat_task.cancel()
    try:
        await beat_task
    except asyncio.CancelledError:
        pass

    assert out["exitCode"] == 0
    # If the loop had been blocked by Popen.wait(), the heartbeat would not have
    # run at all. A live loop produces many beats during the ~1.5s sleep.
    assert heartbeats >= 10, f"event loop appears blocked (heartbeats={heartbeats})"


@pytest.mark.asyncio
async def test_long_testrun_does_not_block_event_loop(tmp_path):
    host = _host(tmp_path)

    heartbeats = 0

    async def heartbeat() -> None:
        nonlocal heartbeats
        while True:
            await asyncio.sleep(0.02)
            heartbeats += 1

    shell_task = asyncio.create_task(
        host._handle("TestRun", {"command": "sleep 1.0"}, tool_call_id="live-testrun")
    )
    beat_task = asyncio.create_task(heartbeat())

    out = await shell_task
    beat_task.cancel()
    try:
        await beat_task
    except asyncio.CancelledError:
        pass

    assert out["exitCode"] == 0
    assert heartbeats >= 8, f"event loop appears blocked (heartbeats={heartbeats})"


def _pgid_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="process-group kill is posix-only")
async def test_cancel_terminates_child_process_group(tmp_path):
    """Cancelling the dispatch task must kill the child process group."""
    host = _host(tmp_path)

    # Write the child's process-group id to a sentinel file so we can verify the
    # OS reclaimed it after cancellation.
    sentinel = tmp_path / "pgid.txt"
    command = f"echo $$ > {sentinel}; sleep 30"

    shell_task = asyncio.create_task(
        host._handle("Bash", {"command": command}, tool_call_id="cancel-bash")
    )

    # Wait for the child to write its pid.
    for _ in range(200):
        await asyncio.sleep(0.02)
        if sentinel.exists() and sentinel.read_text().strip():
            break
    pid = int(sentinel.read_text().strip())
    pgid = os.getpgid(pid)
    assert _pgid_alive(pgid)

    shell_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await shell_task

    # The process group must be gone shortly after cancellation.
    for _ in range(200):
        if not _pgid_alive(pgid):
            break
        await asyncio.sleep(0.02)
    assert not _pgid_alive(pgid), "child process group survived cancellation"


@pytest.mark.asyncio
async def test_concurrency_cap_bounds_simultaneous_shell_commands(tmp_path):
    """No more than ``max_concurrent_shell_commands`` commands run at once."""
    host = _host(tmp_path)
    cap = host.config.max_concurrent_shell_commands
    assert cap >= 1

    n = cap + 2
    sentinel_dir = tmp_path / "sentinels"
    sentinel_dir.mkdir()

    # Each command creates a unique marker, sleeps, then removes it. Peak marker
    # count == peak concurrency.
    async def one(i: int) -> dict:
        marker = sentinel_dir / f"run-{i}"
        command = f"touch {marker}; sleep 0.6; rm -f {marker}"
        return await host._handle("Bash", {"command": command}, tool_call_id=f"cap-{i}")

    peak = 0

    async def sampler() -> None:
        nonlocal peak
        while True:
            await asyncio.sleep(0.02)
            count = len(list(sentinel_dir.iterdir()))
            peak = max(peak, count)

    sampler_task = asyncio.create_task(sampler())
    await asyncio.gather(*(one(i) for i in range(n)))
    sampler_task.cancel()
    try:
        await sampler_task
    except asyncio.CancelledError:
        pass

    assert peak <= cap, f"observed peak concurrency {peak} exceeds cap {cap}"
    # Sanity: the cap actually constrained something (more tasks than the cap).
    assert n > cap


@pytest.mark.asyncio
async def test_handle_uses_async_shell_runner_not_blocking_wait(tmp_path):
    """Regression guard: the Bash/TestRun path must be a coroutine helper.

    Locks in that ``_handle`` routes through an async shell helper instead of the
    blocking sync ``_run_shell_command`` (which calls ``Popen.wait`` on the loop).
    """
    import inspect

    host = _host(tmp_path)
    runner = getattr(host, "_run_shell_command_async", None)
    assert runner is not None, "_run_shell_command_async must exist"
    assert inspect.iscoroutinefunction(runner), "_run_shell_command_async must be async"
