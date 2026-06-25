"""F-EXEC-AUDIT — Subprocess runner foundation for operator-defined shell hooks.

This module ships the typed ``ShellPayload`` schema + the async
``run_shell_payload`` runner + ``build_scoped_env`` + ``truncate_output`` +
``validate_shell_payload`` helpers. **No consumer is wired** — F-EXEC1 (action
kind) and F-EXEC2 (condition kind) PRs will call into this module from their
projection sites. Until then, importing this module is a pure no-op at runtime,
matching today's audit-confirmed behavior.

Design notes (mirrors F-MUT-AUDIT replace_payloads.py pattern):

* ``ShellPayload`` is a frozen pydantic model with ``extra="forbid"``; the
  wizard's authoring kinds can reference its stable shape names without
  producing dead UI bindings.
* The runner template is the async :class:`AsyncShellRunner` (gates) +
  :class:`CommandHookExecutor` (hooks). Both use
  ``asyncio.create_subprocess_exec`` with ``start_new_session=True`` so a
  best-effort ``os.killpg`` on timeout targets the entire process group.
  Reap is bounded at ``_REAP_TIMEOUT_S = 1.0s`` — child processes stuck
  in uninterruptible disk wait (FUSE mounts, rare on a healthy host)
  may slip past the kill and continue running while the runner returns.
  Operator-authored scripts rarely hit this edge.
* Honest-degrade on non-POSIX: returns ``ShellRunResult(exit_code=-2,
  reason="unsupported_platform")`` on ``win32``. The wizard adds an inline
  warning when an operator visits the picker on a Windows runtime.
* Fail-open: any unexpected exception in the runner returns
  ``ShellRunResult(exit_code=-1, ..., reason="internal_error")`` so the
  runtime path can decide whether to block, audit, or continue.
* Env scoping is whitelist-only. The default whitelist
  (``_ENV_WHITELIST``) carries only the variables a typical operator
  script needs to function (PATH/HOME/LANG/LC_ALL/USER/TZ). Operators
  declare extra env names per-rule via ``ShellPayload.env_vars``; only
  declared names are forwarded from ``source_env`` (default ``os.environ``).
  Secrets such as ``OPENAI_API_KEY`` are *never* forwarded unless the
  operator explicitly declares them.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants / whitelists
# ---------------------------------------------------------------------------

# Maximum inline script length (chars). Larger scripts should use source="file"
# so the file path is auditable.
_INLINE_MAX_LEN = 4000

# Timeout bounds (seconds). Range [1, 600] keeps any single shell hook
# bounded; F-EXEC1 wires a budget controller on top.
_TIMEOUT_MIN_S = 1
_TIMEOUT_MAX_S = 600

# Stdout/stderr capture cap (bytes). Truncation marker is appended on overflow.
_OUTPUT_CAP_BYTES = 4096

# Bounded wait for the child to die after a SIGKILL to its process group.
_REAP_TIMEOUT_S = 1.0

# Allowed shell binaries. Operator-trusted; runner does not validate the
# script body (that is the operator's responsibility per env scoping doc).
_SHELL_WHITELIST: tuple[str, ...] = ("bash", "sh")

# Whitelist of env names always forwarded to the subprocess. Anything outside
# this set must be explicitly declared via ShellPayload.env_vars.
_ENV_WHITELIST: tuple[str, ...] = (
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "USER",
    "TZ",
)


# ---------------------------------------------------------------------------
# Typed payload schema
# ---------------------------------------------------------------------------


class ShellPayload(BaseModel):
    """Operator-authored shell payload (mirrors F-MUT-AUDIT replace shapes).

    ``source="inline"`` requires ``inline`` (string, <= _INLINE_MAX_LEN chars).
    ``source="file"`` requires ``path`` (non-empty string; resolution happens
    at run time, not at validation time, so a missing file is a runtime
    ``internal_error`` rather than a validation failure).

    ``env_vars`` lists operator-declared extra env names to forward beyond the
    default whitelist. The runner never forwards anything outside the union of
    the whitelist and this list, so secrets (e.g. ``OPENAI_API_KEY``) are
    excluded by default.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: Literal["inline", "file"]
    # PR-F-EXEC-AUDIT review pass: enforce the size cap at the model
    # boundary, not just in :func:`validate_shell_payload`. Consumers
    # that construct ShellPayload directly (e.g. via model_validate on
    # persisted JSON or direct kwargs) get the cap for free; the
    # validator becomes a thin cross-field check on top of pydantic.
    inline: str | None = Field(default=None, max_length=_INLINE_MAX_LEN)
    path: str | None = None
    timeout_seconds: int = Field(
        default=30, ge=_TIMEOUT_MIN_S, le=_TIMEOUT_MAX_S
    )
    env_vars: list[str] = []
    shell: Literal["bash", "sh"] = "bash"


# ---------------------------------------------------------------------------
# Runner result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ShellRunResult:
    """Outcome of one shell payload execution.

    ``exit_code`` semantics:

    * ``>= 0`` — actual process exit code (0 means success).
    * ``-1`` — runner-internal failure (``reason="internal_error"``);
      ``stderr`` carries the exception text. Also used for ``timed_out=True``.
    * ``-2`` — non-POSIX host honest-degrade
      (``reason="unsupported_platform"``).

    ``duration_ms`` is wall time from the start of the subprocess spawn to its
    completion (or termination on timeout).
    """

    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool
    reason: str | None = None


# ---------------------------------------------------------------------------
# Validator (lightweight, returns list of error strings; empty = valid)
# ---------------------------------------------------------------------------


def validate_shell_payload(payload: dict, fires_at: str | None = None) -> list[str]:
    """Validate a raw operator payload dict.

    Returns a list of human-readable error strings; an empty list means the
    payload is valid (and would coerce cleanly into :class:`ShellPayload`).

    ``fires_at`` is reserved for F-EXEC1/F-EXEC2 cross-field checks
    (e.g. some hook points may forbid certain sources); accepted but unused
    here so the foundation signature does not change later.
    """

    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["shell payload must be an object"]

    # Try pydantic first to catch shape errors uniformly. If it succeeds we
    # still apply the cross-field rules below for richer per-field messages.
    try:
        model = ShellPayload.model_validate(payload)
    except ValidationError as exc:
        for err in exc.errors():
            loc = ".".join(str(p) for p in err.get("loc", ())) or "<root>"
            errors.append(f"{loc}: {err.get('msg', 'invalid')}")
        return errors

    if model.source == "inline":
        if model.inline is None or not model.inline.strip():
            errors.append("inline: required when source='inline'")
        elif len(model.inline) > _INLINE_MAX_LEN:
            errors.append(
                f"inline: too long ({len(model.inline)} > {_INLINE_MAX_LEN} chars)"
            )
    elif model.source == "file":
        if model.path is None or not model.path.strip():
            errors.append("path: required when source='file'")

    if not (_TIMEOUT_MIN_S <= model.timeout_seconds <= _TIMEOUT_MAX_S):
        errors.append(
            f"timeout_seconds: must be in [{_TIMEOUT_MIN_S}, {_TIMEOUT_MAX_S}]"
        )

    if model.shell not in _SHELL_WHITELIST:
        errors.append(
            f"shell: must be one of {sorted(_SHELL_WHITELIST)}"
        )

    for i, name in enumerate(model.env_vars):
        if not isinstance(name, str) or not name.strip():
            errors.append(f"env_vars[{i}]: must be a non-empty string")

    return errors


# ---------------------------------------------------------------------------
# Env scoping
# ---------------------------------------------------------------------------


def build_scoped_env(
    operator_env_vars: list[str],
    source_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build the subprocess env using the whitelist + operator-declared names.

    Variables present in ``source_env`` (defaults to ``os.environ``) are
    forwarded if and only if their name is in the union of the runner
    whitelist and ``operator_env_vars``. Anything else is dropped — this is
    how secrets like ``OPENAI_API_KEY`` are kept out of operator-authored
    scripts unless the operator explicitly declares them.
    """

    src: Mapping[str, str] = source_env if source_env is not None else os.environ
    allowed: set[str] = set(_ENV_WHITELIST) | {
        n for n in (operator_env_vars or []) if isinstance(n, str) and n.strip()
    }
    return {name: src[name] for name in allowed if name in src}


# ---------------------------------------------------------------------------
# Output truncation
# ---------------------------------------------------------------------------

_TRUNCATION_MARKER = "... [truncated]"


def truncate_output(s: str, max_bytes: int = _OUTPUT_CAP_BYTES) -> str:
    """UTF-8-safe truncation: keep <= max_bytes, append marker on overflow.

    The marker itself is not counted against ``max_bytes`` (so the returned
    string may be longer than ``max_bytes`` by the marker length). This
    matches the gate5b convention where the digest carries the precise byte
    accounting and the text is a human-readable preview.
    """

    if max_bytes <= 0:
        return ""
    encoded = s.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return s
    # Cut on a UTF-8 boundary by decoding with errors='ignore' from the
    # truncated bytes — drops any partial multi-byte sequence at the end.
    truncated = encoded[:max_bytes].decode("utf-8", errors="ignore")
    return truncated + _TRUNCATION_MARKER


# ---------------------------------------------------------------------------
# Process-group kill (posix-aware; mirrors AsyncShellRunner._terminate_group)
# ---------------------------------------------------------------------------


async def _terminate_group(proc: asyncio.subprocess.Process) -> None:
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
    else:  # pragma: no cover - Windows honest-degrade short-circuits before here
        try:
            proc.kill()
        except ProcessLookupError:
            return
    try:
        await asyncio.wait_for(proc.wait(), _REAP_TIMEOUT_S)
    except (asyncio.TimeoutError, ProcessLookupError):
        return


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


async def run_shell_payload(
    payload: ShellPayload,
    *,
    stdin_json: dict | None = None,
    source_env: Mapping[str, str] | None = None,
) -> ShellRunResult:
    """Execute a validated :class:`ShellPayload` and return a result.

    * Non-POSIX honest-degrade: returns ``exit_code=-2`` immediately on
      ``win32`` without spawning anything.
    * The subprocess is started with ``start_new_session=True`` so a SIGKILL
      on timeout reaches the whole process group (the script + any children).
    * Stdout / stderr are captured up to :data:`_OUTPUT_CAP_BYTES` each and
      truncated with a visible marker.
    * Any internal exception (file resolution failure, spawn error, etc.) is
      caught and returned as ``exit_code=-1`` with ``reason="internal_error"``
      and the exception string in ``stderr`` (fail-open).
    """

    if sys.platform.startswith("win"):
        return ShellRunResult(
            exit_code=-2,
            stdout="",
            stderr="",
            duration_ms=0,
            timed_out=False,
            reason="unsupported_platform",
        )

    tmp_path: str | None = None
    proc: asyncio.subprocess.Process | None = None
    start = time.monotonic()
    try:
        # 1. Resolve script path.
        if payload.source == "inline":
            if payload.inline is None:
                raise RuntimeError("inline source missing 'inline' body")
            # Write inline body to a tmp file so the shell can execute it
            # via argv-style invocation (no shell=True string interpolation).
            tmp = tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".sh",
                delete=False,
                encoding="utf-8",
            )
            tmp.write(payload.inline)
            tmp.flush()
            tmp.close()
            tmp_path = tmp.name
            script_path = tmp_path
        else:  # source == "file"
            if payload.path is None:
                raise RuntimeError("file source missing 'path'")
            script_path = os.path.abspath(payload.path)
            if not os.path.isfile(script_path) or not os.access(script_path, os.R_OK):
                raise RuntimeError(f"script not readable: {script_path}")

        # 2. Build scoped env.
        env = build_scoped_env(list(payload.env_vars), source_env=source_env)

        # 3. Spawn under whitelisted shell.
        proc = await asyncio.create_subprocess_exec(
            payload.shell,
            script_path,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            start_new_session=(os.name == "posix"),
        )

        # 4. Pipe stdin_json if provided.
        stdin_bytes: bytes | None = None
        if stdin_json is not None:
            stdin_bytes = json.dumps(stdin_json, ensure_ascii=False).encode("utf-8")

        # 5. Wait for completion with timeout.
        timed_out = False
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(stdin_bytes),
                timeout=float(payload.timeout_seconds),
            )
        except asyncio.TimeoutError:
            await _terminate_group(proc)
            stdout_bytes, stderr_bytes = b"", b"timed out"
            timed_out = True
            logger.warning(
                "shell_runner: payload timed out after %ds",
                payload.timeout_seconds,
            )

        duration_ms = int((time.monotonic() - start) * 1000)
        stdout_text = truncate_output(stdout_bytes.decode("utf-8", errors="replace"))
        stderr_text = truncate_output(stderr_bytes.decode("utf-8", errors="replace"))

        if timed_out:
            return ShellRunResult(
                exit_code=-1,
                stdout=stdout_text,
                stderr=stderr_text,
                duration_ms=duration_ms,
                timed_out=True,
                reason="timed_out",
            )

        # proc.returncode is set after communicate() returns; default to -1
        # in the impossible case it is still None.
        return ShellRunResult(
            exit_code=proc.returncode if proc.returncode is not None else -1,
            stdout=stdout_text,
            stderr=stderr_text,
            duration_ms=duration_ms,
            timed_out=False,
            reason=None,
        )

    except Exception as exc:  # noqa: BLE001 - fail-open contract
        logger.exception("shell_runner: internal error")
        duration_ms = int((time.monotonic() - start) * 1000)
        return ShellRunResult(
            exit_code=-1,
            stdout="",
            stderr=str(exc),
            duration_ms=duration_ms,
            timed_out=False,
            reason="internal_error",
        )
    finally:
        if proc is not None and proc.returncode is None:
            await _terminate_group(proc)
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


__all__ = [
    "ShellPayload",
    "ShellRunResult",
    "build_scoped_env",
    "run_shell_payload",
    "truncate_output",
    "validate_shell_payload",
]
