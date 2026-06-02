"""Command hook executor — spawns an external process via ``bash -c <command>``.

Protocol
--------
- JSON payload written to stdin
- Process stdout parsed as JSON → ``HookResult``
- Exit codes follow Claude Code's shell hook convention:
    0  success  — parse stdout as JSON HookResult
    2  block    — use stderr as the blocking reason
    other  warn-only — log stderr, return ``continue``
- On timeout: kill the process, then return ``continue`` (fail-open) or
  ``block`` (fail-closed) based on ``manifest.fail_open``.
- On any other exception: same fail-open / fail-closed policy as timeout.

Sanitisation
------------
``_build_sanitized_hook_input`` never forwards:
- Raw filesystem paths (redacted to ``<redacted_path>``)
- Thinking blocks / internal scratchpad content
- Auth tokens / API key strings

Security note
-------------
``manifest.command`` is operator-supplied and is passed directly to ``bash -c``.
This is intentional — operators are trusted to configure their own hook commands.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from typing import Any

from openmagi_core_agent.hooks.context import HookContext
from openmagi_core_agent.hooks.executors import _REGISTRY, HookExecutor
from openmagi_core_agent.hooks.executors.sanitize import (
    _build_sanitized_hook_input,
    _sanitize_any,
    _sanitize_any_typed,
    _sanitize_value,
)
from openmagi_core_agent.hooks.manifest import HookManifest
from openmagi_core_agent.hooks.result import HookResult

logger = logging.getLogger(__name__)

# Re-export sanitization helpers so existing tests that import them from this
# module continue to work without modification.
__all__ = [
    "CommandHookExecutor",
    "_build_sanitized_hook_input",
    "_sanitize_value",
    "_sanitize_any",
    "_sanitize_any_typed",
]

# ---------------------------------------------------------------------------
# Safe environment variable allowlist — never forward secrets to hook processes
# ---------------------------------------------------------------------------

_SAFE_ENV_KEYS: frozenset[str] = frozenset({
    "PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TMPDIR",
    "TERM", "SHELL", "USER", "LOGNAME",
})


def _build_env(context: HookContext, manifest: HookManifest) -> dict[str, str]:
    """Build the environment for the hook subprocess.

    Starts from a safe allowlist of inherited env vars (never forwards secrets
    such as ANTHROPIC_API_KEY, SUPABASE_*, etc.), then injects MAGI_* context.
    """
    env: dict[str, str] = {k: v for k, v in os.environ.items() if k in _SAFE_ENV_KEYS}
    env["MAGI_HOOK_EVENT"] = manifest.point.value
    env["MAGI_BOT_ID"] = context.bot_id
    if context.session_id is not None:
        env["MAGI_SESSION_ID"] = context.session_id
    else:
        env["MAGI_SESSION_ID"] = ""
    if context.turn_id is not None:
        env["MAGI_TURN_ID"] = context.turn_id
    else:
        env["MAGI_TURN_ID"] = ""
    # MAGI_TOOL_NAME — relevant for tool-use hook points; empty otherwise.
    # The caller can populate it via context if a tool_name field exists;
    # for now we default to empty (HookContext has no tool_name field yet).
    env["MAGI_TOOL_NAME"] = ""
    return env


def _parse_hook_output(stdout: str) -> HookResult:
    """Parse the JSON stdout from a hook process into a ``HookResult``.

    Supported fields (matching Claude Code's schema):
    - ``continue``           → action="continue" (boolean or "continue" string)
    - ``stopReason``         → action="block", reason=<str>
    - ``updatedInput``       → action="replace", value=<obj>
    - ``additionalContext``  → attached as metadata["additionalContext"]
    - ``permissionDecision`` → action="permission_decision", decision=<str>

    Returns ``HookResult(action="continue")`` on any parsing error.
    """
    stripped = stdout.strip()
    if not stripped:
        return HookResult(action="continue")

    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        logger.warning("command hook returned non-JSON stdout: %.200s", stripped)
        return HookResult(action="continue")

    if not isinstance(data, dict):
        logger.warning("command hook stdout is not a JSON object")
        return HookResult(action="continue")

    metadata: dict[str, object] = {}

    # permissionDecision takes precedence
    if "permissionDecision" in data:
        decision = data["permissionDecision"]
        if decision in ("approve", "deny", "ask"):
            return HookResult(
                action="permission_decision",
                decision=decision,  # type: ignore[arg-type]
                reason=data.get("reason"),
                metadata=metadata,
            )
        logger.warning("command hook returned unknown permissionDecision: %s", decision)

    # stopReason → block
    if "stopReason" in data:
        return HookResult(
            action="block",
            reason=str(data["stopReason"]),
            metadata=metadata,
        )

    def _safe_additional_context(raw: object) -> object | None:
        """Cap additionalContext to 8 KiB (JSON-serialised) and sanitize strings."""
        if raw is None:
            return None
        if isinstance(raw, str):
            raw = _sanitize_value(raw)
        try:
            serialized = json.dumps(raw)
        except (TypeError, ValueError):
            logger.warning("command hook additionalContext is not JSON-serialisable; discarding")
            return None
        if len(serialized) > 8192:
            logger.warning(
                "command hook additionalContext exceeds 8 KiB (%d bytes); discarding",
                len(serialized),
            )
            return None
        return raw

    # updatedInput → replace
    if "updatedInput" in data:
        if "additionalContext" in data:
            safe_ctx = _safe_additional_context(data["additionalContext"])
            if safe_ctx is not None:
                metadata["additionalContext"] = safe_ctx
        return HookResult(
            action="replace",
            value=data["updatedInput"],
            metadata=metadata,
        )

    # additionalContext only → continue with metadata
    if "additionalContext" in data:
        safe_ctx = _safe_additional_context(data["additionalContext"])
        if safe_ctx is not None:
            metadata["additionalContext"] = safe_ctx
        return HookResult(action="continue", metadata=metadata)

    # Explicit continue field
    if "continue" in data:
        cont = data["continue"]
        if cont is False or cont == "block":
            reason = data.get("reason")
            logger.warning(
                "command hook used deprecated 'continue: false' to block; "
                "prefer 'stopReason' for explicit blocking behaviour"
            )
            return HookResult(action="block", reason=reason)

    return HookResult(action="continue", metadata=metadata)


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------

class CommandHookExecutor:
    """Executes hooks by spawning ``bash -c <command>`` as a subprocess.

    Implements the ``HookExecutor`` protocol.

    Note: ``manifest.command`` is operator-trusted and passed directly to
    ``bash -c``. Shell injection from untrusted user input is the operator's
    responsibility to guard against.
    """

    async def execute(self, context: HookContext, manifest: HookManifest) -> HookResult:
        assert manifest.command is not None, "CommandHookExecutor requires manifest.command"

        payload = _build_sanitized_hook_input(context, manifest)
        stdin_bytes = json.dumps(payload, ensure_ascii=False).encode()
        env = _build_env(context, manifest)
        timeout_s = manifest.timeout_ms / 1000.0

        proc: asyncio.subprocess.Process | None = None
        try:
            proc = await asyncio.create_subprocess_exec(
                "bash",
                "-c",
                manifest.command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                start_new_session=True,
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(stdin_bytes),
                    timeout=timeout_s,
                )
            except asyncio.TimeoutError:
                _kill_proc(proc)
                logger.warning(
                    "command hook '%s' timed out after %.1fs",
                    manifest.name,
                    timeout_s,
                )
                if manifest.fail_open:
                    return HookResult(action="continue")
                return HookResult(
                    action="block",
                    reason=f"Hook '{manifest.name}' timed out after {manifest.timeout_ms}ms",
                )

            returncode = proc.returncode
            stdout_text = stdout_bytes.decode(errors="replace")
            stderr_text = stderr_bytes.decode(errors="replace").strip()

            if returncode == 0:
                return _parse_hook_output(stdout_text)

            if returncode == 2:
                reason = stderr_text or f"Hook '{manifest.name}' blocked (exit 2)"
                return HookResult(action="block", reason=reason)

            # Any other exit code: warn, continue
            logger.warning(
                "command hook '%s' exited with code %d; stderr: %.500s",
                manifest.name,
                returncode,
                stderr_text,
            )
            return HookResult(action="continue")

        except Exception:
            logger.exception("command hook '%s' raised an unexpected exception", manifest.name)
            if manifest.fail_open:
                return HookResult(action="continue")
            return HookResult(
                action="block",
                reason=f"Hook '{manifest.name}' encountered an unexpected error",
            )
        finally:
            _kill_proc(proc)


def _kill_proc(proc: asyncio.subprocess.Process | None) -> None:
    """Best-effort process group kill + reap; never raises.

    Sends SIGKILL to the entire process group (created via ``start_new_session=True``)
    so that any child processes spawned by the hook command are also terminated.
    Falls back to ``proc.kill()`` if the process group cannot be signalled.
    """
    if proc is None:
        return
    try:
        if proc.returncode is None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()
            asyncio.ensure_future(proc.wait())
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Self-register into the executor registry
# ---------------------------------------------------------------------------

_REGISTRY["command"] = CommandHookExecutor()
