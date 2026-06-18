"""Async, off-loop shell execution for the Gate5B Bash/TestRun path (B-2).

The Gate5B full toolhost runs ``Bash``/``TestRun`` commands from an ``async``
``dispatch``. The historical implementation called the blocking
``subprocess.Popen(...).wait(timeout=...)`` on the calling thread — the async
event-loop thread for the HTTP/SSE serving path — stalling heartbeats, SSE
emission, cancel handling, and unrelated requests for up to the command timeout
(300s for TestRun).

This module provides :class:`AsyncShellRunner`, a native-async runner built on
``asyncio.create_subprocess_shell`` + ``asyncio.wait_for`` that:

* runs entirely off the event loop (the loop stays live during the command),
* preserves the process-group kill on timeout / cancellation
  (``start_new_session=True`` + ``os.killpg(SIGKILL)`` on posix),
* drains stdout/stderr concurrently into the gate5b
  :class:`_BoundedPipeCapture` so the cap + digest semantics are identical,
* bounds the number of simultaneous commands with a small per-host semaphore
  (``max_concurrent_shell_commands``).

The returned :class:`ShellRunResult` projects the exact dict shape the sync
``_run_shell_command`` returned, so behaviour is byte-identical — only the
scheduling changes.
"""
from __future__ import annotations

import asyncio
import os
import signal
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from magi_agent.gates._bounded_pipe import BoundedPipeCapture

# Bounded wait for the child to die after a SIGKILL to its process group. The
# group is already SIGKILL'd; this only reaps the zombie / awaits the transport.
_REAP_TIMEOUT_S = 1.0

# Default number of shell/TestRun commands allowed to run simultaneously per
# host. Keeps create_subprocess from exhausting file descriptors under
# aggressive tool use while still allowing real concurrency.
DEFAULT_MAX_CONCURRENT_SHELL_COMMANDS = 2


@dataclass(frozen=True)
class ShellRunResult:
    """Outcome of one shell command, projectable to the gate5b result dict."""

    exit_code: int | None
    timed_out: bool
    stdout: str
    stderr: str
    stdout_digest: str
    stderr_digest: str


async def _read_into(stream: asyncio.StreamReader | None, capture: BoundedPipeCapture) -> None:
    if stream is None:
        return
    try:
        while True:
            chunk = await stream.read(65536)
            if not chunk:
                break
            capture.feed(chunk)
    except (OSError, ValueError):
        return


async def _terminate_group(proc: asyncio.subprocess.Process) -> None:
    """Kill the child's process group (posix) or the child (other), then reap.

    Mirrors the sync ``_kill_process_group``/``_force_stop_process`` guarantees:
    SIGKILL the whole group so descendants of ``shell=True`` die too, guard
    ``ProcessLookupError`` for an already-exited child, and bound the reap.
    """
    if proc.returncode is not None:
        return
    if os.name == "posix":
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            return
        except OSError:
            try:
                proc.kill()
            except ProcessLookupError:
                return
    else:
        try:
            proc.kill()
        except ProcessLookupError:
            return
    try:
        await asyncio.wait_for(proc.wait(), _REAP_TIMEOUT_S)
    except (asyncio.TimeoutError, ProcessLookupError):
        return


class AsyncShellRunner:
    """Runs shell commands off the event loop with bounded concurrency."""

    def __init__(self, *, max_concurrent: int = DEFAULT_MAX_CONCURRENT_SHELL_COMMANDS) -> None:
        self._semaphore = asyncio.Semaphore(max(1, int(max_concurrent)))

    async def run(
        self,
        command: str,
        *,
        cwd: Path,
        timeout_s: float,
        env: Mapping[str, str],
        cap: int,
    ) -> ShellRunResult:
        """Execute ``command`` and return a :class:`ShellRunResult`.

        Raises :class:`asyncio.CancelledError` after tearing down the process
        group when the awaiting task is cancelled (client abort / interrupt).
        """

        async with self._semaphore:
            return await self._run_once(
                command, cwd=cwd, timeout_s=timeout_s, env=env, cap=cap
            )

    async def _run_once(
        self,
        command: str,
        *,
        cwd: Path,
        timeout_s: float,
        env: Mapping[str, str],
        cap: int,
    ) -> ShellRunResult:
        stdout_capture = BoundedPipeCapture(cap)
        stderr_capture = BoundedPipeCapture(cap)
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=str(cwd),
            env=dict(env),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=(os.name == "posix"),
        )
        drain = asyncio.gather(
            _read_into(proc.stdout, stdout_capture),
            _read_into(proc.stderr, stderr_capture),
        )
        try:
            try:
                await asyncio.wait_for(self._wait_complete(proc, drain), timeout=timeout_s)
            except asyncio.TimeoutError:
                await _terminate_group(proc)
                await self._finish_drain(drain)
                return ShellRunResult(
                    exit_code=None,
                    timed_out=True,
                    stdout=stdout_capture.text(),
                    stderr=stderr_capture.text(),
                    stdout_digest=stdout_capture.digest(),
                    stderr_digest=stderr_capture.digest(),
                )
        except asyncio.CancelledError:
            await _terminate_group(proc)
            await self._finish_drain(drain)
            raise
        return ShellRunResult(
            exit_code=proc.returncode,
            timed_out=False,
            stdout=stdout_capture.text(),
            stderr=stderr_capture.text(),
            stdout_digest=stdout_capture.digest(),
            stderr_digest=stderr_capture.digest(),
        )

    @staticmethod
    async def _wait_complete(
        proc: asyncio.subprocess.Process,
        drain: asyncio.Future[object],
    ) -> None:
        await proc.wait()
        await drain

    @staticmethod
    async def _finish_drain(drain: asyncio.Future[object]) -> None:
        try:
            await asyncio.wait_for(asyncio.shield(drain), _REAP_TIMEOUT_S)
        except (asyncio.TimeoutError, asyncio.CancelledError, OSError):
            drain.cancel()
        try:
            await drain
        except (asyncio.CancelledError, OSError):
            return
