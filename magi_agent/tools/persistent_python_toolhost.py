"""Additive first-party toolhost binder for the ``PersistentPython`` tool.

This is the EXECUTION half of the bundled persistent-python pack. The pack
manifest (``firstparty/packs/tools_persistent_python``) is pure declaration; the
handler-binding seam is still the first-party toolhost layer today (a
pack-authored handler is a future authoring-ABI gap — see the Step B design doc
§Risks), so this binder lives alongside ``bind_core_toolhost_handlers`` and is
invoked from the same runtime build paths. It is ADDITIVE and removable: if the
manifest is not registered (pack disabled), ``bind_persistent_python_handler``
binds nothing and returns ``()``.

CodeAct persistence: the handler keeps a ``dict[key, PythonExecWorker]`` keyed by
``(workspace_root, turn_id or session_id or "local")`` — the same keying idea as
``CoreToolhostHandlerSet._host_for``. Each worker is a long-lived ``python3 -I``
subprocess whose module namespace persists across calls so variables carry across
steps within a turn; a different turn/session gets a fresh subprocess namespace
(no cross-question leak).

Killable timeout (B-3): execution previously ran on a daemon ``threading.Thread``
with ``Thread.join(timeout=...)``. Python cannot kill a thread, so a runaway
``while True`` cell kept pinning a CPU core after the "timeout" returned and
leaked one thread per timeout. This binder now reuses the SAME killable
subprocess machinery that backs ``PythonExec`` (``python_exec_worker``): on
timeout the worker process *group* is SIGKILLed (``start_new_session=True`` +
``os.killpg``) and a fresh worker is spawned for that key on the next call. No
second worker implementation is forked.

Security: OSS-local full-trust scope. The subprocess runs with a PATH-only env
(no inherited secrets) and the import allowlist set to the wildcard ``"*"`` so the
full-trust ``exec`` semantics are preserved (arbitrary imports allowed) — the real
control is the existing execute/dangerous/requires-approval machinery that governs
``Bash``/``PythonExec``. The hosted opinionated runtime gates this pack off
(``config.toml [packs] disable``).
"""
from __future__ import annotations

from collections.abc import Mapping

from .context import ToolContext
from .python_exec import PythonExecConfig
from .python_exec_worker import PythonExecWorker, WorkerDead, WorkerTimeout
from .registry import ToolRegistry
from .result import ToolResult

PERSISTENT_PYTHON_TOOL_NAME = "PersistentPython"
_PERSISTENT_PYTHON_PACK_ID = "open" "magi.tools-persistent-python"

# Per-call wall-clock cap and per-stream output byte cap. Conservative defaults;
# the manifest timeout is the model-facing contract, these are the hard guards.
_DEFAULT_TIMEOUT_S = 30.0
_DEFAULT_MAX_OUTPUT_BYTES = 16_384

# Full-trust local scope: allow ALL imports inside the killable subprocess. The
# shared worker driver treats ``"*"`` as "no import allowlist", preserving the
# in-process ``exec`` semantics this pack had before the subprocess migration.
_FULL_TRUST_ALLOWLIST: tuple[str, ...] = ("*",)


def _bounded_head_tail(text: str, max_bytes: int) -> str:
    """Cap ``text`` keeping BOTH ends (60/40 split + elision marker).

    Mirrors the gate5b / ``python_exec`` truncation shape so large program output
    never blows context or pollutes the final answer.
    """
    if len(text) <= max_bytes:
        return text
    head_budget = max(1, (max_bytes * 3) // 5)
    tail_budget = max(0, max_bytes - head_budget)
    head = text[:head_budget]
    tail = text[len(text) - tail_budget :] if tail_budget else ""
    elided = len(text) - head_budget - tail_budget
    marker = (
        f"\n[... {elided} bytes elided - output truncated; print less or "
        "slice the data to see the elided region ...]\n"
    )
    return head + marker + tail


class PersistentPythonHandlerSet:
    """Per-context-key persistent **subprocess** workers for ``PersistentPython``.

    A single instance is created once per registry (one per CLI/runtime session),
    so the ``_workers`` map survives across calls. Keyed by
    ``(workspace_root, turn_id or session_id or "local")`` — the same keying idea
    as ``CoreToolhostHandlerSet._host_for``.

    Each key owns one long-lived, killable ``PythonExecWorker`` subprocess (the
    same primitive that backs ``PythonExec``) instead of an in-process daemon
    thread. The subprocess module namespace persists across calls (CodeAct); a
    runaway cell is SIGKILLed on timeout and the worker for that key is dropped so
    the next call spawns a fresh namespace.
    """

    def __init__(
        self,
        *,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
        max_output_bytes: int = _DEFAULT_MAX_OUTPUT_BYTES,
    ) -> None:
        self._timeout_s = timeout_s
        self._max_output_bytes = max_output_bytes
        # Full-trust config for the shared worker: wildcard allowlist (arbitrary
        # imports), our wall-clock timeout, and a raw in-driver capture cap a bit
        # above the host head+tail cap so truncation still shows an elision marker.
        self._worker_config = PythonExecConfig(
            timeout_s=timeout_s,
            max_output_bytes=max_output_bytes,
            raw_capture_bytes=max(max_output_bytes * 8, 262_144),
            import_allowlist=_FULL_TRUST_ALLOWLIST,
        )
        self._workers: dict[tuple[str, str], PythonExecWorker] = {}

    def bind(self, registry: ToolRegistry) -> tuple[str, ...]:
        registration = registry.resolve_registration(PERSISTENT_PYTHON_TOOL_NAME)
        if registration is None or registration.handler is not None:
            return ()
        registry.bind_handler(
            PERSISTENT_PYTHON_TOOL_NAME,
            self._handle,
            enabled_by_registry_policy=True,
        )
        return (PERSISTENT_PYTHON_TOOL_NAME,)

    def _context_key(self, context: ToolContext) -> tuple[str, str]:
        workspace_root = context.workspace_root or "local"
        scope = context.turn_id or context.session_id or "local"
        return (str(workspace_root), str(scope))

    def _worker_for_key(self, key: tuple[str, str]) -> PythonExecWorker:
        worker = self._workers.get(key)
        if worker is not None and not worker.is_alive():
            self._drop_worker(key)
            worker = None
        if worker is None:
            workspace_root = key[0] if key[0] != "local" else None
            worker = PythonExecWorker(
                workspace_root=workspace_root,
                config=self._worker_config,
            )
            self._workers[key] = worker
        return worker

    def _drop_worker(self, key: tuple[str, str]) -> None:
        worker = self._workers.pop(key, None)
        if worker is not None:
            try:
                worker.close()
            except Exception:  # noqa: BLE001 - best-effort cleanup, never raise
                pass

    def close(self) -> None:
        """Kill all per-key worker subprocesses (session/registry teardown)."""
        for key in list(self._workers):
            self._drop_worker(key)

    def _handle(self, arguments: Mapping[str, object], context: ToolContext) -> ToolResult:
        try:
            code = arguments.get("code")
            if not isinstance(code, str) or not code.strip():
                return ToolResult(
                    status="error",
                    error_code="missing_code",
                    error_message="code is required",
                    metadata={"toolName": PERSISTENT_PYTHON_TOOL_NAME},
                )
            key = self._context_key(context)
            worker = self._worker_for_key(key)
            try:
                response = worker.execute(code)
            except WorkerTimeout:
                # SIGKILLed by the worker; drop it so the next call in this key
                # spawns a fresh namespace (the OS reclaimed the runaway).
                self._drop_worker(key)
                return ToolResult(
                    status="error",
                    error_code="persistent_python_error",
                    error_message=(
                        f"TimeoutError: code exceeded {self._timeout_s:.0f}s "
                        "wall-clock limit; interpreter killed"
                    ),
                    metadata={"toolName": PERSISTENT_PYTHON_TOOL_NAME},
                )
            except WorkerDead as exc:
                self._drop_worker(key)
                return ToolResult(
                    status="error",
                    error_code="persistent_python_unavailable",
                    error_message=str(exc),
                    metadata={"toolName": PERSISTENT_PYTHON_TOOL_NAME},
                )

            stdout = str(response.get("stdout") or "")
            capped_stdout = _bounded_head_tail(stdout, self._max_output_bytes)
            if response.get("ok"):
                return ToolResult(
                    status="ok",
                    output={"stdout": capped_stdout, "value": response.get("value")},
                    metadata={"toolName": PERSISTENT_PYTHON_TOOL_NAME},
                )
            error_text = str(response.get("error") or "execution failed")
            return ToolResult(
                status="error",
                error_code="persistent_python_error",
                error_message=error_text,
                output={"stdout": capped_stdout} if capped_stdout else None,
                metadata={"toolName": PERSISTENT_PYTHON_TOOL_NAME},
            )
        except Exception as exc:  # noqa: BLE001 - handlers must NEVER raise
            return ToolResult(
                status="error",
                error_code="persistent_python_unavailable",
                error_message=str(exc),
                metadata={"toolName": PERSISTENT_PYTHON_TOOL_NAME},
            )


def _persistent_python_pack_enabled() -> bool:
    """Return whether config.toml leaves the bundled pack enabled.

    The manifest loader already applies ``[packs] disable``. This binder can also
    be invoked directly by runtime construction paths, so it must mirror that
    removal contract instead of treating ``MAGI_PERSISTENT_PYTHON_ENABLED`` as a
    privileged override.
    """
    try:
        from magi_agent.packs.discovery import load_packs_config  # noqa: PLC0415

        return _PERSISTENT_PYTHON_PACK_ID not in set(load_packs_config().disable)
    except Exception:
        return True


def register_persistent_python_manifest(registry: ToolRegistry) -> None:
    """Register the ``PersistentPython`` manifest by running the PACK provider.

    Sources the manifest from the bundled ``tools_persistent_python`` pack's
    ``provide_persistent_python`` (the SAME typed ``ToolProvideContext`` the
    loader would use) rather than hardcoding it here — the pack stays the single
    source of truth (§1 no privilege). No-op if already registered.
    """
    if not _persistent_python_pack_enabled():
        return
    if registry.resolve_registration(PERSISTENT_PYTHON_TOOL_NAME) is not None:
        return
    from magi_agent.firstparty.packs.tools_persistent_python.impl import (  # noqa: PLC0415
        provide_persistent_python,
    )
    from magi_agent.packs.context import ToolProvideContext  # noqa: PLC0415

    provide_persistent_python(ToolProvideContext(register=registry.register))


def bind_persistent_python_handler(
    registry: ToolRegistry,
    *,
    handler_set: PersistentPythonHandlerSet | None = None,
) -> PersistentPythonHandlerSet | None:
    """Bind a :class:`PersistentPythonHandlerSet` to ``registry`` if the manifest
    is registered.

    Additive + removable: returns ``None`` when the ``PersistentPython`` manifest
    was never registered (the pack is disabled), so the runtime is byte-identical
    to before. ``handler_set`` is injectable for tests.
    """
    if registry.resolve_registration(PERSISTENT_PYTHON_TOOL_NAME) is None:
        return None
    resolved = handler_set if handler_set is not None else PersistentPythonHandlerSet()
    resolved.bind(registry)
    return resolved


__all__ = [
    "PERSISTENT_PYTHON_TOOL_NAME",
    "PersistentPythonHandlerSet",
    "bind_persistent_python_handler",
    "register_persistent_python_manifest",
]
