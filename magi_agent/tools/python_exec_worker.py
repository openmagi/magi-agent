"""Worker process + session pool backing the persistent ``PythonExec`` tool.

Each session key owns one long-lived ``python3 -I`` subprocess running the
in-module ``_DRIVER_SOURCE`` REPL driver: a loop reading one JSON request line
from stdin and writing one JSON response line to stdout. The interpreter
namespace lives inside that subprocess, so variables/imports/loaded data
survive across calls for the same session — the smolagents-style code-action
lever — while the host process stays memory-bounded (driver-side raw capture
caps, host-side head+tail caps).

Process hygiene mirrors the stateless Bash path in
``gates/gate5b_full_toolhost.py``: minimal ``{"PATH": ...}`` env (no secrets
inherited), ``start_new_session=True`` for group-kill, and a force-stop that
``os.killpg``-s the worker group on timeout. Unlike Bash, the process is kept
alive between calls; on timeout/crash it is killed and the next call starts a
fresh namespace.
"""

from __future__ import annotations

import atexit
import json
import os
import signal
import subprocess
import sys
import threading
import time
from collections import OrderedDict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from magi_agent.tools.python_exec import PythonExecConfig

__all__ = [
    "PythonExecSessionPool",
    "PythonExecWorker",
    "WorkerDead",
    "WorkerTimeout",
]

_VALUE_REPR_CAP_CHARS = 4096
_TRACEBACK_TAIL_CHARS = 2048


class WorkerDead(Exception):
    """The worker process exited or its pipes broke mid-call."""


class WorkerTimeout(Exception):
    """The worker did not answer within the configured wall-clock budget."""


# The worker-side REPL driver. Runs via ``python3 -I -c _DRIVER_SOURCE`` inside
# the subprocess; stdlib only. Protocol: one JSON object per stdin line ->
# exactly one JSON object per stdout line. Exceptions never kill the loop.
_DRIVER_SOURCE = r"""
import ast
import builtins as _builtins
import json
import sys
import traceback

_REAL_STDOUT = sys.stdout
_NS = None
_ALLOWLIST = set()


def _guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    top = str(name).split(".")[0]
    if level == 0 and top not in _ALLOWLIST:
        raise ImportError("import_not_allowed: " + top)
    return _builtins.__import__(name, globals, locals, fromlist, level)


def _ensure_namespace():
    global _NS
    if _NS is None:
        guarded = dict(vars(_builtins))
        guarded["__import__"] = _guarded_import
        _NS = {"__name__": "__main__", "__builtins__": guarded}
    return _NS


def _blocked_import(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top not in _ALLOWLIST:
                    return top
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                return node.module or "<relative>"
            top = (node.module or "").split(".")[0]
            if top not in _ALLOWLIST:
                return top or "<relative>"
    return None


class _CappedWriter:
    # Bounded stdout/stderr capture keeping BOTH ends (60/40 head/tail split
    # with an elision marker), so unbounded prints cannot exhaust memory.

    def __init__(self, cap):
        self.cap = max(1, int(cap))
        self._head = []
        self._head_len = 0
        self._tail = []
        self._tail_len = 0
        self.total = 0

    @property
    def _head_budget(self):
        return max(1, (self.cap * 3) // 5)

    @property
    def _tail_budget(self):
        return max(0, self.cap - self._head_budget)

    def write(self, text):
        text = str(text)
        if not text:
            return 0
        self.total += len(text)
        remaining = self.cap - self._head_len
        if remaining > 0:
            chunk = text[:remaining]
            self._head.append(chunk)
            self._head_len += len(chunk)
        budget = self._tail_budget
        if budget > 0:
            self._tail.append(text)
            self._tail_len += len(text)
            while self._tail and (self._tail_len - len(self._tail[0])) >= budget:
                self._tail_len -= len(self._tail[0])
                self._tail.pop(0)
        return len(text)

    def flush(self):
        return None

    def text(self):
        joined = "".join(self._head)
        if self.total <= self.cap:
            return joined
        head = joined[: self._head_budget]
        budget = self._tail_budget
        tail = "".join(self._tail)[-budget:] if budget else ""
        elided = max(0, self.total - len(head) - len(tail))
        marker = (
            "\n[... " + str(elided) + " chars elided - output truncated ...]\n"
        )
        return head + marker + tail


def _respond(payload):
    _REAL_STDOUT.write(json.dumps(payload) + "\n")
    _REAL_STDOUT.flush()


def _main():
    global _ALLOWLIST
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            code = str(request.get("code") or "")
            _ALLOWLIST = set(request.get("allowlist") or [])
            raw_cap = int(request.get("raw_capture_chars") or 262144)
            value_cap = int(request.get("value_repr_chars") or 4096)
            tb_cap = int(request.get("traceback_tail_chars") or 2048)
        except Exception:
            _respond({"ok": False, "error_code": "protocol_error",
                      "error": "malformed request line"})
            continue

        try:
            tree = ast.parse(code, mode="exec")
        except SyntaxError as exc:
            _respond({"ok": False, "error_code": "python_syntax_error",
                      "error": str(exc)})
            continue

        blocked = _blocked_import(tree)
        if blocked is not None:
            _respond({
                "ok": False,
                "error_code": "import_not_allowed",
                "error": "import of " + repr(blocked)
                + " is not in the stdlib allowlist; the code was not executed",
            })
            continue

        namespace = _ensure_namespace()
        out = _CappedWriter(raw_cap)
        err = _CappedWriter(raw_cap)
        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = out, err
        value_repr = None
        error_text = None
        try:
            body = list(tree.body)
            final_expr = None
            if body and isinstance(body[-1], ast.Expr):
                final_expr = ast.fix_missing_locations(
                    ast.Expression(body[-1].value)
                )
                body = body[:-1]
            if body:
                module = ast.Module(body=body, type_ignores=[])
                exec(compile(module, "<python_exec>", "exec"), namespace)
            if final_expr is not None:
                value = eval(compile(final_expr, "<python_exec>", "eval"), namespace)
                if value is not None:
                    value_repr = repr(value)
                    if len(value_repr) > value_cap:
                        value_repr = value_repr[:value_cap] + "...[truncated]"
        except BaseException:
            text = traceback.format_exc()
            if len(text) > tb_cap:
                text = "...[traceback head elided]...\n" + text[-tb_cap:]
            error_text = text
        finally:
            sys.stdout, sys.stderr = old_out, old_err

        if error_text is not None:
            _respond({"ok": False, "error_code": "python_runtime_error",
                      "error": error_text,
                      "stdout": out.text(), "stderr": err.text()})
        else:
            _respond({"ok": True, "value": value_repr,
                      "stdout": out.text(), "stderr": err.text()})


_main()
"""


def _build_worker_env() -> dict[str, str]:
    """PATH-only env: no secrets inherited (mirrors gate5b ``_build_bash_env``)."""
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}


class PythonExecWorker:
    """One long-lived driver subprocess; calls are serialized by ``lock``."""

    def __init__(
        self,
        *,
        workspace_root: str | None,
        config: "PythonExecConfig",
    ) -> None:
        self._config = config
        self.lock = threading.Lock()
        self.last_used = time.monotonic()
        interpreter = sys.executable or "python3"
        self._process: subprocess.Popen[bytes] = subprocess.Popen(  # noqa: S603
            [interpreter, "-I", "-c", _DRIVER_SOURCE],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd=workspace_root or None,
            env=_build_worker_env(),
            start_new_session=True,
        )

    def is_alive(self) -> bool:
        return self._process.poll() is None

    def execute(self, code: str) -> dict[str, object]:
        """Run one request; raises ``WorkerDead`` / ``WorkerTimeout``."""
        with self.lock:
            self.last_used = time.monotonic()
            if not self.is_alive():
                raise WorkerDead("worker process already exited")
            request = json.dumps(
                {
                    "code": code,
                    "allowlist": list(self._config.import_allowlist),
                    "raw_capture_chars": int(self._config.raw_capture_bytes),
                    "value_repr_chars": _VALUE_REPR_CAP_CHARS,
                    "traceback_tail_chars": _TRACEBACK_TAIL_CHARS,
                }
            )
            stdin = self._process.stdin
            if stdin is None:
                raise WorkerDead("worker stdin unavailable")
            try:
                stdin.write(request.encode("utf-8") + b"\n")
                stdin.flush()
            except (BrokenPipeError, OSError, ValueError) as exc:
                raise WorkerDead(f"worker stdin broke: {exc}") from exc

            line = self._read_response_line(self._config.timeout_s)
            try:
                response = json.loads(line.decode("utf-8", errors="replace"))
            except ValueError as exc:
                raise WorkerDead(f"worker spoke a malformed response: {exc}") from exc
            if not isinstance(response, dict):
                raise WorkerDead("worker response was not a JSON object")
            return response

    def _read_response_line(self, timeout_s: float) -> bytes:
        stdout = self._process.stdout
        if stdout is None:
            raise WorkerDead("worker stdout unavailable")
        captured: list[bytes] = []

        def _read() -> None:
            try:
                captured.append(stdout.readline())
            except (OSError, ValueError):
                captured.append(b"")

        reader = threading.Thread(target=_read, daemon=True)
        reader.start()
        reader.join(max(0.001, timeout_s))
        if reader.is_alive():
            self._force_stop()
            raise WorkerTimeout(
                f"execution exceeded {timeout_s:g}s; interpreter killed"
            )
        line = captured[0] if captured else b""
        if not line:
            raise WorkerDead("worker process exited mid-call")
        return line

    def close(self) -> None:
        self._force_stop()
        for pipe in (self._process.stdin, self._process.stdout):
            if pipe is None:
                continue
            try:
                pipe.close()
            except (OSError, ValueError):
                continue

    def _force_stop(self) -> None:
        """Group-kill the worker (the gate5b ``_force_stop_process`` pattern)."""
        if self._process.poll() is not None:
            return
        if os.name == "posix":
            try:
                os.killpg(self._process.pid, signal.SIGKILL)
            except ProcessLookupError:
                return
            except OSError:
                self._process.kill()
        else:  # pragma: no cover - non-posix fallback
            self._process.kill()
        try:
            self._process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:  # pragma: no cover - defensive
            self._process.kill()
            try:
                self._process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                return


class PythonExecSessionPool:
    """LRU pool of per-session workers; thread-safe; closed at interpreter exit."""

    def __init__(self, config: "PythonExecConfig") -> None:
        self._config = config
        self._lock = threading.Lock()
        self._workers: OrderedDict[str, PythonExecWorker] = OrderedDict()
        atexit.register(self.close_all)

    def execute(
        self,
        session_key: str,
        code: str,
        *,
        workspace_root: str | None,
        reset: bool = False,
    ) -> dict[str, object]:
        """Run ``code`` on the session's worker; returns the driver response dict
        augmented with ``namespace_reset`` and ``duration_ms``. Spawn failures
        propagate (the handler maps them to ``python_exec_unavailable``).
        """
        started = time.monotonic()
        with self._lock:
            self._reap_idle_locked()
            if reset:
                self._evict_locked(session_key)
            worker = self._workers.get(session_key)
            fresh = False
            if worker is not None and not worker.is_alive():
                self._evict_locked(session_key)
                worker = None
            if worker is None:
                worker = PythonExecWorker(
                    workspace_root=workspace_root,
                    config=self._config,
                )
                fresh = True
                self._workers[session_key] = worker
                while len(self._workers) > max(1, self._config.max_sessions):
                    oldest = next(iter(self._workers))
                    if oldest == session_key:
                        break
                    self._evict_locked(oldest)
            else:
                self._workers.move_to_end(session_key)

        def duration_ms() -> int:
            return max(0, int((time.monotonic() - started) * 1000))

        try:
            response = worker.execute(code)
        except WorkerTimeout as exc:
            self.evict(session_key)
            return {
                "ok": False,
                "error_code": "python_exec_timeout",
                "error": f"{exc}; the session namespace was reset",
                "namespace_reset": True,
                "duration_ms": duration_ms(),
            }
        except WorkerDead as exc:
            self.evict(session_key)
            return {
                "ok": False,
                "error_code": "python_exec_worker_died",
                "error": f"{exc}; the session namespace was reset",
                "namespace_reset": True,
                "duration_ms": duration_ms(),
            }
        response["namespace_reset"] = fresh
        response["duration_ms"] = duration_ms()
        return response

    def evict(self, session_key: str) -> None:
        with self._lock:
            self._evict_locked(session_key)

    def close_all(self) -> None:
        with self._lock:
            for key in list(self._workers):
                self._evict_locked(key)

    def _evict_locked(self, session_key: str) -> None:
        worker = self._workers.pop(session_key, None)
        if worker is not None:
            worker.close()

    def _reap_idle_locked(self) -> None:
        ttl = float(self._config.idle_ttl_s)
        if ttl <= 0:
            return
        now = time.monotonic()
        for key in [
            key
            for key, worker in self._workers.items()
            if (now - worker.last_used) > ttl
        ]:
            self._evict_locked(key)
