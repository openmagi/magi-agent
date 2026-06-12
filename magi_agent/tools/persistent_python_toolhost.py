"""Additive first-party toolhost binder for the ``PersistentPython`` tool.

This is the EXECUTION half of the ``openmagi.tools-persistent-python`` pack. The
pack manifest (``firstparty/packs/tools_persistent_python``) is pure declaration;
the handler-binding seam is still the first-party toolhost layer today (a
pack-authored handler is a future authoring-ABI gap — see the Step B design doc
§Risks), so this binder lives alongside ``bind_core_toolhost_handlers`` and is
invoked from the same runtime build paths. It is ADDITIVE and removable: if the
manifest is not registered (pack disabled), ``bind_persistent_python_handler``
binds nothing and returns ``()``.

CodeAct persistence: the handler keeps a ``dict[key, _Interpreter]`` keyed by
``(workspace_root, turn_id or session_id or "local")`` — the same keying idea as
``CoreToolhostHandlerSet._host_for``. Each interpreter persists a ``globals``
dict across calls so variables carry across steps within a turn; a different
turn/session gets a fresh namespace (no cross-question leak).

Security: OSS-local full-trust scope. This is a guarded ``exec`` in-process — the
real control is the existing execute/dangerous/requires-approval machinery that
governs ``Bash``/``PythonExec``. The hosted opinionated runtime gates this pack
off (``config.toml [packs] disable``).
"""
from __future__ import annotations

import ast
import contextlib
import io
import threading
from collections.abc import Mapping
from typing import Any

from .context import ToolContext
from .registry import ToolRegistry
from .result import ToolResult

PERSISTENT_PYTHON_TOOL_NAME = "PersistentPython"

# Per-call wall-clock cap and per-stream output byte cap. Conservative defaults;
# the manifest timeout is the model-facing contract, these are the hard guards.
_DEFAULT_TIMEOUT_S = 30.0
_DEFAULT_MAX_OUTPUT_BYTES = 16_384


class _Interpreter:
    """A long-lived guarded-``exec`` namespace for one context key.

    ``globals`` persists across calls so the model can load data once and keep
    filtering/aggregating it across steps (CodeAct). Execution runs the code as a
    module body, then — if the final statement is a bare expression — evaluates it
    so its value can be reported (Jupyter-style last-expression echo).
    """

    def __init__(self) -> None:
        self._globals: dict[str, Any] = {"__name__": "__persistent_python__"}

    def run(self, code: str, *, timeout_s: float) -> tuple[bool, str, str | None, str | None]:
        """Execute ``code``; return ``(ok, stdout, value_repr, error)``.

        Never raises: a syntax/runtime error or a timeout is reported as
        ``ok=False`` with a short ``error`` string. ``value_repr`` is the repr of
        the final bare expression when present, else ``None``.
        """
        stdout_buf = io.StringIO()
        outcome: dict[str, Any] = {"ok": False, "value": None, "error": None}

        def _target() -> None:
            try:
                body, last_expr = _split_last_expression(code)
                with contextlib.redirect_stdout(stdout_buf):
                    if body is not None:
                        exec(body, self._globals)  # noqa: S102 - full-trust local
                    if last_expr is not None:
                        value = eval(last_expr, self._globals)  # noqa: S307
                        outcome["value"] = None if value is None else repr(value)
                outcome["ok"] = True
            except BaseException as exc:  # noqa: BLE001 - fail-soft, report not raise
                outcome["error"] = f"{type(exc).__name__}: {exc}"

        worker = threading.Thread(target=_target, daemon=True)
        worker.start()
        worker.join(timeout=max(0.1, timeout_s))
        if worker.is_alive():
            # The daemon thread keeps running but we stop waiting; report a
            # timeout rather than blocking the agent loop. The namespace may be
            # left mid-mutation, which is acceptable for a full-trust local tool.
            return (
                False,
                stdout_buf.getvalue(),
                None,
                f"TimeoutError: code exceeded {timeout_s:.0f}s wall-clock limit",
            )
        return (
            bool(outcome["ok"]),
            stdout_buf.getvalue(),
            outcome["value"],
            outcome["error"],
        )


def _split_last_expression(code: str) -> tuple[Any | None, Any | None]:
    """Split ``code`` into a (body, last-expression) pair of compiled code objects.

    If the module's final statement is a bare expression it is compiled
    separately in ``eval`` mode so its value can be echoed. A parse error is
    re-raised by the caller's guarded ``exec`` path (compile happens inside the
    worker so SyntaxError is reported fail-soft).
    """
    parsed = ast.parse(code, mode="exec")
    if parsed.body and isinstance(parsed.body[-1], ast.Expr):
        last = parsed.body.pop()
        body_code = compile(parsed, "<persistent_python>", "exec") if parsed.body else None
        expr_module = ast.Expression(body=last.value)
        ast.copy_location(expr_module, last)
        expr_code = compile(expr_module, "<persistent_python>", "eval")
        return body_code, expr_code
    return compile(parsed, "<persistent_python>", "exec"), None


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
    """Per-context-key persistent interpreters for the ``PersistentPython`` tool.

    A single instance is created once per registry (one per CLI/runtime session),
    so the ``_interpreters`` map survives across calls. Keyed by
    ``(workspace_root, turn_id or session_id or "local")`` — the same keying idea
    as ``CoreToolhostHandlerSet._host_for``.
    """

    def __init__(
        self,
        *,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
        max_output_bytes: int = _DEFAULT_MAX_OUTPUT_BYTES,
    ) -> None:
        self._timeout_s = timeout_s
        self._max_output_bytes = max_output_bytes
        self._interpreters: dict[tuple[str, str], _Interpreter] = {}

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

    def _interpreter_for(self, context: ToolContext) -> _Interpreter:
        workspace_root = context.workspace_root or "local"
        scope = context.turn_id or context.session_id or "local"
        key = (str(workspace_root), str(scope))
        interpreter = self._interpreters.get(key)
        if interpreter is None:
            interpreter = _Interpreter()
            self._interpreters[key] = interpreter
        return interpreter

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
            interpreter = self._interpreter_for(context)
            ok, stdout, value, error = interpreter.run(code, timeout_s=self._timeout_s)
            capped_stdout = _bounded_head_tail(stdout, self._max_output_bytes)
            if ok:
                return ToolResult(
                    status="ok",
                    output={"stdout": capped_stdout, "value": value},
                    metadata={"toolName": PERSISTENT_PYTHON_TOOL_NAME},
                )
            return ToolResult(
                status="error",
                error_code="persistent_python_error",
                error_message=error or "execution failed",
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


def register_persistent_python_manifest(registry: ToolRegistry) -> None:
    """Register the ``PersistentPython`` manifest by running the PACK provider.

    Sources the manifest from the bundled ``tools_persistent_python`` pack's
    ``provide_persistent_python`` (the SAME typed ``ToolProvideContext`` the
    loader would use) rather than hardcoding it here — the pack stays the single
    source of truth (§1 no privilege). No-op if already registered.
    """
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
