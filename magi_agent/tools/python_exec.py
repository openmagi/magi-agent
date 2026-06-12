"""``PythonExec``: persistent per-session Python execution tool (code-action seam).

Strict opt-in behind ``MAGI_CODE_ACTION_ENABLED`` (default OFF). When the flag
is unset this module is never imported and the runtime is byte-identical to
before. When ON, the CLI tool runtime registers the manifest and binds the
handler through the same gated registration seam as ``BrowserTask``
(``register_*_manifest`` + ``bind_*_handler``, lazy imports, call-time
degradation to an error result instead of import-time failure).

Why: a stateless ``Bash`` call per step forces "load once, query repeatedly"
data work (large CSV aggregation, log analysis) to re-pay the parse cost every
step. A persistent interpreter namespace lets the model load data once and keep
filtering/aggregating it across subsequent calls in the same session — the
structural lever of smolagents-style code-action agents.

The import allowlist reduces accident surface; it is NOT a security boundary
(the manifest description says so honestly). The real control is the existing
execute/dangerous/requires-approval machinery that already governs ``Bash``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from magi_agent.tools.catalog import CORE_TOOL_SOURCE
from magi_agent.tools.context import ToolContext
from magi_agent.tools.manifest import ToolManifest
from magi_agent.tools.result import ToolResult

if TYPE_CHECKING:
    from magi_agent.tools.python_exec_worker import PythonExecSessionPool
    from magi_agent.tools.registry import ToolRegistry

PYTHON_EXEC_TOOL_NAME = "PythonExec"

MAGI_CODE_ACTION_TIMEOUT_MS_ENV = "MAGI_CODE_ACTION_TIMEOUT_MS"
MAGI_CODE_ACTION_MAX_OUTPUT_BYTES_ENV = "MAGI_CODE_ACTION_MAX_OUTPUT_BYTES"

# Deliberately absent: os, sys, subprocess, socket, urllib, http, pathlib,
# shutil, ctypes, multiprocessing, importlib, builtins — the allowlist is a
# best-effort accident guard, not an isolation boundary (see module docstring).
DEFAULT_IMPORT_ALLOWLIST: tuple[str, ...] = (
    "math", "statistics", "json", "re", "csv", "datetime", "time", "calendar",
    "itertools", "collections", "functools", "operator", "decimal", "fractions",
    "random", "string", "textwrap", "unicodedata", "heapq", "bisect", "array",
    "copy", "enum", "dataclasses", "typing", "io", "base64", "hashlib", "uuid",
    "zoneinfo", "pprint",
)

_DESCRIPTION = (
    "Run Python code in a persistent per-session interpreter: variables, "
    "imports and loaded data survive across calls in the same session. "
    "Use print() for output; the value of a final bare expression is also "
    "returned. Output is head+tail capped. Imports are restricted to a "
    "stdlib allowlist (no os/sys/subprocess/socket/network modules). "
    "HONEST SANDBOX LIMITS: the allowlist is best-effort, not a security "
    "boundary — the code runs as a local subprocess with the same OS user, "
    "filesystem access via builtins, and no network restriction beyond "
    "platform egress policy. Treated with the same approval surface as Bash. "
    "On timeout the interpreter is killed and the session namespace resets."
)

_INPUT_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "code": {
            "type": "string",
            "description": "Python source to execute in the persistent session namespace.",
        },
        "reset": {
            "type": "boolean",
            "description": "Optional: discard the session namespace before executing.",
        },
    },
    "required": ["code"],
}


@dataclass(frozen=True)
class PythonExecConfig:
    """Tunables for the PythonExec worker pool."""

    timeout_s: float = 30.0  # wall-clock per call
    max_output_bytes: int = 8192  # final head+tail cap (per stream)
    raw_capture_bytes: int = 262_144  # in-driver stdout/stderr cap
    max_sessions: int = 4  # LRU pool size
    idle_ttl_s: float = 900.0  # worker reaped after idle
    import_allowlist: tuple[str, ...] = field(default=DEFAULT_IMPORT_ALLOWLIST)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "PythonExecConfig":
        from magi_agent.config.flags import flag_int  # noqa: PLC0415

        timeout_raw = flag_int(MAGI_CODE_ACTION_TIMEOUT_MS_ENV, env=env)
        timeout_ms = max(1_000, min(120_000, int(timeout_raw if timeout_raw is not None else 30_000)))
        output_raw = flag_int(MAGI_CODE_ACTION_MAX_OUTPUT_BYTES_ENV, env=env)
        max_output = max(1_024, min(65_536, int(output_raw if output_raw is not None else 8_192)))
        return cls(timeout_s=timeout_ms / 1000.0, max_output_bytes=max_output)


def build_python_exec_manifest() -> ToolManifest:
    return ToolManifest(
        name=PYTHON_EXEC_TOOL_NAME,
        description=_DESCRIPTION,
        kind="core",
        source=CORE_TOOL_SOURCE,
        permission="execute",
        dangerous=True,
        mutates_workspace=True,  # builtins open() can write
        available_in_modes=("act",),
        tags=("workspace", "code", "execute", "requires-approval"),
        parallel_safety="unsafe",  # never offloaded to a worker thread
        timeout_ms=120_000,
        input_schema=_INPUT_SCHEMA,
    )


def register_python_exec_manifest(registry: "ToolRegistry") -> None:
    """Register the PythonExec manifest (no handler bound yet)."""
    registry.register(build_python_exec_manifest())


def bind_python_exec_handler(
    registry: "ToolRegistry",
    *,
    config: PythonExecConfig | None = None,
    pool: "PythonExecSessionPool | None" = None,
) -> tuple[str, ...]:
    """Bind the PythonExec handler if its manifest is registered.

    Returns the bound tool names, or an empty tuple when the manifest was never
    registered (so callers can gate registration upstream). ``config``/``pool``
    are injectable for tests.
    """
    if registry.resolve_registration(PYTHON_EXEC_TOOL_NAME) is None:
        return ()

    resolved_config = config if config is not None else PythonExecConfig.from_env()
    if pool is None:
        from magi_agent.tools.python_exec_worker import (  # noqa: PLC0415
            PythonExecSessionPool,
        )

        pool = PythonExecSessionPool(resolved_config)

    def _bound_handler(
        arguments: Mapping[str, object], context: ToolContext
    ) -> ToolResult:
        return _python_exec_handler(
            arguments, context, config=resolved_config, pool=pool
        )

    registry.bind_handler(
        PYTHON_EXEC_TOOL_NAME,
        _bound_handler,
        enabled_by_registry_policy=True,
    )
    return (PYTHON_EXEC_TOOL_NAME,)


def _python_exec_handler(
    arguments: Mapping[str, object],
    context: ToolContext,
    *,
    config: PythonExecConfig,
    pool: "PythonExecSessionPool",
) -> ToolResult:
    """Sync handler: dispatcher contract — return results, never raise."""
    try:
        code = str(arguments.get("code") or "")
        if not code.strip():
            return ToolResult(
                status="error",
                error_code="missing_code",
                error_message="code is required",
            )
        reset = bool(arguments.get("reset") or False)
        session_key = (
            context.session_key or context.session_id or context.bot_id
        )
        try:
            response = pool.execute(
                str(session_key),
                code,
                workspace_root=context.workspace_root,
                reset=reset,
            )
        except Exception as exc:
            # Worker spawn failure (no interpreter, fork failure, bad cwd).
            return ToolResult(
                status="error",
                error_code="python_exec_unavailable",
                error_message=f"could not start the python execution worker: {exc}",
            )
        return _result_from_response(response, config=config)
    except Exception as exc:  # noqa: BLE001 - handlers must not raise
        return ToolResult(
            status="error",
            error_code="python_exec_unavailable",
            error_message=str(exc),
        )


def _result_from_response(
    response: Mapping[str, object],
    *,
    config: PythonExecConfig,
) -> ToolResult:
    cap = max(1, int(config.max_output_bytes))
    stdout = _bounded_head_tail(str(response.get("stdout") or ""), cap)
    stderr = _bounded_head_tail(str(response.get("stderr") or ""), cap)
    namespace_reset = bool(response.get("namespace_reset"))
    duration = int(response.get("duration_ms") or 0)

    if response.get("ok"):
        value = response.get("value")
        return ToolResult(
            status="ok",
            output={
                "value": None if value is None else str(value),
                "stdout": stdout,
                "stderr": stderr,
                "durationMs": duration,
                "namespaceReset": namespace_reset,
            },
            duration_ms=duration,
            metadata={"namespaceReset": namespace_reset},
        )

    error_code = str(response.get("error_code") or "python_exec_error")
    error_message = str(response.get("error") or "") or None
    output: dict[str, object] | None = None
    if stdout or stderr:
        output = {"stdout": stdout, "stderr": stderr}
    return ToolResult(
        status="error",
        error_code=error_code,
        error_message=error_message,
        output=output,
        duration_ms=duration,
        metadata={"namespaceReset": namespace_reset},
    )


def _bounded_head_tail(text: str, max_bytes: int) -> str:
    """Cap ``text`` keeping BOTH ends (60/40 split + elision marker).

    Local re-implementation of the gate5b ``_bounded_head_tail`` shape; if a
    shared truncation helper lands, dedup onto it as a follow-up.
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


__all__ = [
    "DEFAULT_IMPORT_ALLOWLIST",
    "PYTHON_EXEC_TOOL_NAME",
    "PythonExecConfig",
    "bind_python_exec_handler",
    "build_python_exec_manifest",
    "register_python_exec_manifest",
]
