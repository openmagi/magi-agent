"""OperatorSoulWriter — operator-gated SOUL.md write path (D4).

SOUL.md is the agent's persistent identity/persona file.  The agent MUST NEVER
be able to write it:

  * The D1 allowlist (``_ALLOWED_WRITE_FILES``) does NOT include ``SOUL.md``.
  * The D2 ``MemoryWrite`` tool sanitizes ``target_file`` to ``MEMORY.md`` when
    an unknown target is provided — so ``SOUL.md`` can never reach the agent
    write path.
  * ``_attempt_real_write`` in ``harness/memory_write.py`` only accepts a
    ``LocalFileMemoryProvider`` instance; ``OperatorSoulWriter`` is not one.

This module provides a SEPARATE, OPERATOR-ONLY write path that:

  1. Requires an explicit operator authority flag: ``operator_enabled=True`` in
     config OR ``MAGI_SOUL_WRITE_ENABLED=1`` in the environment.
  2. Is gated independently from ``MAGI_MEMORY_WRITE_ENABLED`` — the agent
     write gate does NOT open the operator SOUL gate.
  3. Applies the same redaction pipeline as the agent write path.
  4. Enforces a byte cap.
  5. Is unreachable from the agent tool (``MemoryWriteToolHost``) or harness
     (``MemoryWriteHarness``): those only accept ``LocalFileMemoryProvider``.

Default: OFF.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from magi_agent.memory.adapters.hipocampus_readonly import (
    _validate_workspace_root,
    UnsafeMemoryPathError,
)
from magi_agent.memory.adapters.local_file_writable import _redact_for_write


# ---------------------------------------------------------------------------
# Public env-gate constant
# ---------------------------------------------------------------------------

MAGI_SOUL_WRITE_ENABLED_ENV: str = "MAGI_SOUL_WRITE_ENABLED"

# The target file this writer manages.
_SOUL_FILENAME = "SOUL.md"

# Default byte cap for SOUL writes (16 KiB — generous for persona content).
_DEFAULT_MAX_WRITE_BYTES = 16_384

_MODEL_CONFIG = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")


# ---------------------------------------------------------------------------
# Error types
# ---------------------------------------------------------------------------


class OperatorSoulWriteDisabledError(Exception):
    """Raised when a write is attempted but the operator gate is off."""

    def __init__(self, reason: str = "operator soul write gate is disabled") -> None:
        super().__init__(reason)
        self.reason = reason


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class OperatorSoulWriterConfig(BaseModel):
    """Configuration for OperatorSoulWriter.

    ``operator_enabled`` is the explicit Python flag.  When ``None`` (unset),
    falls back to the ``MAGI_SOUL_WRITE_ENABLED`` environment variable.

    This gate is COMPLETELY SEPARATE from ``MAGI_MEMORY_WRITE_ENABLED`` (the
    agent write gate).  Setting the agent gate does NOT open the operator gate.
    """

    model_config = _MODEL_CONFIG

    workspace_root: Path = Field(alias="workspaceRoot")
    operator_enabled: bool | None = Field(default=None, alias="operatorEnabled")
    max_write_bytes: int = Field(
        default=_DEFAULT_MAX_WRITE_BYTES,
        alias="maxWriteBytes",
        ge=1,
    )


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------


class OperatorSoulWriter:
    """Operator-only SOUL.md write surface (D4).

    Authority separation
    --------------------
    The agent tool (``MemoryWriteToolHost`` → ``MemoryWriteHarness``) only
    accepts ``LocalFileMemoryProvider`` instances as adapters — type-checking
    in ``_attempt_real_write`` rejects all other types.  ``OperatorSoulWriter``
    is a distinct class that does NOT inherit from ``LocalFileMemoryProvider``,
    so it is structurally unreachable from the agent write path.

    The operator gate (``operator_enabled`` / ``MAGI_SOUL_WRITE_ENABLED``)
    is read at call time — not at construction time — so the gate can be opened
    or closed by the operator without recreating the writer.
    """

    def __init__(self, config: OperatorSoulWriterConfig) -> None:
        self.config = config
        self.workspace_root = _validate_workspace_root(config.workspace_root)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def write_soul(self, content: str) -> None:
        """Write (append) content to SOUL.md.

        Raises ``OperatorSoulWriteDisabledError`` when the operator gate is
        off.  Raises ``ValueError`` when the content exceeds ``max_write_bytes``
        or would escape the workspace root.
        """
        if not self._operator_gate_open():
            raise OperatorSoulWriteDisabledError()

        if not isinstance(content, str):
            content = str(content)

        body = content.strip()
        body_bytes = len(body.encode("utf-8"))
        if body_bytes > self.config.max_write_bytes:
            raise ValueError(
                f"OperatorSoulWriter: content exceeds max_write_bytes "
                f"({body_bytes} > {self.config.max_write_bytes})"
            )

        safe_body = _redact_for_write(body)

        # Resolve path — must stay inside workspace root.
        target_path = self.workspace_root / _SOUL_FILENAME
        # Safety: confirm resolved path is within workspace (no symlink escapes).
        try:
            resolved = target_path.resolve()
            ws_resolved = self.workspace_root.resolve()
            resolved.relative_to(ws_resolved)
        except ValueError as exc:
            raise ValueError(
                f"OperatorSoulWriter: path safety violation for {_SOUL_FILENAME!r}"
            ) from exc

        entry = f"\n- [operator] {safe_body}\n"
        with target_path.open("a", encoding="utf-8") as fh:
            fh.write(entry)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _operator_gate_open(self) -> bool:
        """Return True if the operator gate is open."""
        if self.config.operator_enabled is not None:
            return self.config.operator_enabled
        env_val = os.environ.get(MAGI_SOUL_WRITE_ENABLED_ENV, "").strip().lower()
        return env_val in {"1", "true", "yes", "on"}


__all__ = [
    "OperatorSoulWriter",
    "OperatorSoulWriterConfig",
    "OperatorSoulWriteDisabledError",
    "MAGI_SOUL_WRITE_ENABLED_ENV",
]
