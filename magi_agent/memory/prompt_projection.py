"""D3 — gated memory prompt projection.

Reads MEMORY.md / USER.md from the workspace root, applies the existing
redaction pipeline, and assembles a bounded ``<memory-context>`` block for
injection into the DYNAMIC/volatile section of the system prompt.

Gate: ``MAGI_MEMORY_PROJECTION_ENABLED`` (default off).
When off: ``project_memory_snapshot`` returns a disabled result —
byte-identical behaviour to before D3.

Incognito: even when the gate is on, ``memory_mode="incognito"`` blocks
projection entirely.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from magi_agent.memory.policy import (
    MAGI_MEMORY_PROJECTION_ENABLED_ENV,
    _projection_gate_open,
)
from magi_agent.memory.adapters.hipocampus_readonly import (
    _validate_workspace_root,
    _resolve_workspace_path,
    _safe_snippet,
)
from magi_agent.memory.projection import _sanitize_memory_snippet
from magi_agent.transport.tool_preview import MAX_TOOL_PREVIEW


# Re-export so tests can import from here directly.
__all__ = [
    "MAGI_MEMORY_PROJECTION_ENABLED_ENV",
    "MEMORY_CONTEXT_OPEN",
    "MEMORY_CONTEXT_CLOSE",
    "MemoryProjectionResult",
    "MemoryPromptProjector",
    "project_memory_snapshot",
]

MEMORY_CONTEXT_OPEN = '<memory-context hidden="true">'
MEMORY_CONTEXT_CLOSE = "</memory-context>"

# Default snapshot budget: 8 KiB — generous for MEMORY.md + USER.md combined,
# small enough to stay in the cheap volatile section without cache pressure.
_DEFAULT_MAX_BYTES = 8_192

# Files that contribute to the snapshot (in order).
_SNAPSHOT_FILES: tuple[str, ...] = ("MEMORY.md", "USER.md")

_MODEL_CONFIG = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")


class MemoryProjectionResult(BaseModel):
    """Outcome of a projection decision.

    ``enabled=True`` means the snapshot was produced and should be injected.
    ``enabled=False`` means the gate was off or the channel is incognito —
    ``snapshot_block`` is ``""`` and the caller must NOT inject anything.
    """

    model_config = _MODEL_CONFIG

    enabled: bool
    prompt_projection_allowed: bool = Field(alias="promptProjectionAllowed")
    snapshot_block: str = Field(default="", alias="snapshotBlock")
    snapshot_digest: str = Field(default="sha256:" + "0" * 64, alias="snapshotDigest")
    bytes_used: int = Field(default=0, alias="bytesUsed")
    bytes_budget: int = Field(default=_DEFAULT_MAX_BYTES, alias="bytesBudget")
    files_loaded: tuple[str, ...] = Field(default=(), alias="filesLoaded")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")


class MemoryPromptProjector:
    """Read-path snapshot projector (D3).

    ``enabled`` defaults to None → resolves from
    ``MAGI_MEMORY_PROJECTION_ENABLED`` env var each call.
    Pass ``enabled=True`` in unit tests that must not mutate ``os.environ``.
    """

    def __init__(
        self,
        workspace_root: Path,
        *,
        enabled: bool | None = None,
        max_bytes: int = _DEFAULT_MAX_BYTES,
    ) -> None:
        self.workspace_root = _validate_workspace_root(workspace_root)
        self._explicit_enabled = enabled
        self.max_bytes = max_bytes

    def project(
        self,
        *,
        memory_mode: str = "normal",
    ) -> MemoryProjectionResult:
        """Produce the memory snapshot block.

        ``memory_mode="incognito"`` blocks projection regardless of the gate.
        """
        gate_open = (
            self._explicit_enabled
            if self._explicit_enabled is not None
            else _projection_gate_open()
        )

        if not gate_open:
            return MemoryProjectionResult(
                enabled=False,
                promptProjectionAllowed=False,
                snapshotBlock="",
                reasonCodes=("projection_gate_off",),
            )

        if memory_mode == "incognito":
            return MemoryProjectionResult(
                enabled=False,
                promptProjectionAllowed=False,
                snapshotBlock="",
                reasonCodes=("incognito_blocks_projection",),
            )

        return _build_snapshot(self.workspace_root, max_bytes=self.max_bytes)


def project_memory_snapshot(
    *,
    workspace_root: Path,
    memory_mode: str = "normal",
    max_bytes: int = _DEFAULT_MAX_BYTES,
) -> MemoryProjectionResult:
    """Module-level convenience wrapper around :class:`MemoryPromptProjector`.

    The gate is read from the environment each call.
    """
    projector = MemoryPromptProjector(workspace_root=workspace_root, max_bytes=max_bytes)
    return projector.project(memory_mode=memory_mode)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_snapshot(
    workspace_root: Path,
    *,
    max_bytes: int,
) -> MemoryProjectionResult:
    """Read MEMORY.md / USER.md, redact, bound, and fence as <memory-context>."""
    parts: list[str] = []
    files_loaded: list[str] = []
    budget = max(max_bytes, 1)

    # Pre-compute content_budget so we can pre-truncate raw files before
    # passing them to the sanitizer (which contains regexes with catastrophic
    # backtracking on large inputs).
    headroom = len(MEMORY_CONTEXT_OPEN.encode()) + len(MEMORY_CONTEXT_CLOSE.encode()) + 4
    content_budget = max(budget - headroom, 0)

    for rel_path in _SNAPSHOT_FILES:
        path = _resolve_workspace_path(workspace_root, rel_path)
        if path is None or not path.is_file():
            continue
        raw = path.read_text(encoding="utf-8", errors="replace")
        # Bound BEFORE sanitizing: several regexes inside _sanitize_memory_snippet
        # (and the final sanitize_tool_preview call) exhibit catastrophic
        # backtracking on large inputs.  _sanitize_memory_snippet terminates with
        # sanitize_tool_preview, which itself caps output to MAX_TOOL_PREVIEW
        # (~400 chars).  Pre-truncating each file to MAX_TOOL_PREVIEW + 200
        # (matching the ReDoS guard in sanitize_tool_preview) is therefore
        # behaviour-preserving: no useful content beyond that limit survives the
        # sanitiser pipeline.  The final _slice_utf8(combined, content_budget)
        # below enforces the exact byte cap over all combined files.
        raw = _slice_utf8(raw, MAX_TOOL_PREVIEW + 200)
        redacted = _sanitize_memory_snippet(raw)
        if not redacted.strip():
            continue
        parts.append(f"<!-- {rel_path} -->\n{redacted}")
        files_loaded.append(rel_path)

    if not parts:
        # Nothing to inject; return a disabled-style result (no block).
        return MemoryProjectionResult(
            enabled=False,
            promptProjectionAllowed=False,
            snapshotBlock="",
            reasonCodes=("no_memory_files",),
        )

    combined = "\n\n".join(parts)

    # Apply byte cap (UTF-8 aware slice).
    truncated = _slice_utf8(combined, content_budget)

    # Build the fenced block.
    block = f"{MEMORY_CONTEXT_OPEN}\n{truncated}\n{MEMORY_CONTEXT_CLOSE}"
    block_bytes = len(block.encode("utf-8"))

    # Evidence digest (over the REDACTED+truncated content, not raw)
    digest = "sha256:" + hashlib.sha256(truncated.encode("utf-8")).hexdigest()

    return MemoryProjectionResult(
        enabled=True,
        promptProjectionAllowed=True,
        snapshotBlock=block,
        snapshotDigest=digest,
        bytesUsed=block_bytes,
        bytesBudget=budget,
        filesLoaded=tuple(files_loaded),
        reasonCodes=("projection_gate_on",),
    )


def _slice_utf8(value: str, max_bytes: int) -> str:
    """Truncate *value* to at most *max_bytes* UTF-8 bytes, respecting codepoint boundaries."""
    if max_bytes <= 0:
        return ""
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    end = max_bytes
    while end > 0 and (encoded[end] & 0xC0) == 0x80:
        end -= 1
    return encoded[:end].decode("utf-8", errors="ignore")
