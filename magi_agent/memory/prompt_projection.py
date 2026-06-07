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
from pathlib import Path

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
from magi_agent.memory.projection import (
    _SENSITIVE_REF_RE,
    _COOKIE_HEADER_RE,
    _SECRET_TEXT_RE,
    _CHILD_PROMPT_RE,
    _RAW_TOOL_LOG_RE,
    _HIDDEN_REASONING_RE,
    _PRIVATE_PATH_RE,
    _PRIVATE_PATH_ALIAS_RE,
    _drop_private_projection_lines,
    _redact_private_path,
)
from magi_agent.transport.tool_preview import MAX_TOOL_PREVIEW, redact_secret_tokens

# ReDoS guard for per-line token redaction: some patterns in redact_secret_tokens
# have superlinear backtracking; cap each line to a safe length before running
# them.  Secrets are never multi-line, so per-line processing is semantically
# correct.  The cap matches sanitize_tool_preview's own input ceiling.
_TOKEN_REDACT_LINE_LIMIT = MAX_TOOL_PREVIEW + 200


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


def _redact_snapshot_content(raw: str) -> str:
    """Apply secret/private redaction to snapshot content WITHOUT a 400-char cap.

    Redaction is a SUPERSET of ``_sanitize_memory_snippet`` in
    ``memory/projection.py``:

      Layer 1 — the 9 projection regexes (same as ``_sanitize_memory_snippet``):
        _SENSITIVE_REF_RE, _COOKIE_HEADER_RE, _SECRET_TEXT_RE,
        _CHILD_PROMPT_RE, _RAW_TOOL_LOG_RE, _HIDDEN_REASONING_RE,
        _drop_private_projection_lines, _PRIVATE_PATH_RE,
        _PRIVATE_PATH_ALIAS_RE

      Layer 2 — token/secret patterns from ``transport/tool_preview.redact_secret_tokens``:
        Bearer tokens, Authorization headers, Cookie headers,
        GitHub tokens (ghp_/gho_/ghs_/ghu_/ghr_), OpenAI keys (sk-proj-/sk-),
        Stripe keys (sk_live_/rk_test_/…), quoted/unquoted key=value pairs for
        api_key/secret/token/client_secret/session_key/…, session assignments.

    The 400-char length cap from ``sanitize_tool_preview`` is intentionally
    NOT applied here; the caller enforces the byte bound via ``content_budget``
    / ``_slice_utf8``.

    ReDoS guards:
      - The caller pre-truncates ``raw`` to ``content_budget`` bytes (layer 1
        regexes run on bounded input).
      - Layer 2 applies ``redact_secret_tokens`` per-line, capping each line to
        ``_TOKEN_REDACT_LINE_LIMIT`` chars, because some token patterns have
        superlinear backtracking on long uniform strings (secrets are never
        multi-line so per-line processing is semantically correct).

    # keep in sync: snapshot redaction = 9 projection regexes (layer 1)
    #   + redact_secret_tokens() token/secret patterns (layer 2, per-line),
    #   minus ONLY the 400-char length cap.
    """
    sanitized = _SENSITIVE_REF_RE.sub("[redacted-ref]", raw)
    sanitized = _COOKIE_HEADER_RE.sub("[redacted-cookie]", sanitized)
    sanitized = _SECRET_TEXT_RE.sub("[redacted]", sanitized)
    sanitized = _CHILD_PROMPT_RE.sub("[redacted child prompt]", sanitized)
    sanitized = _RAW_TOOL_LOG_RE.sub("[redacted tool log]", sanitized)
    sanitized = _HIDDEN_REASONING_RE.sub("[redacted hidden reasoning]", sanitized)
    sanitized = "\n".join(_drop_private_projection_lines(sanitized.splitlines()))
    sanitized = _PRIVATE_PATH_RE.sub(_redact_private_path, sanitized)
    sanitized = _PRIVATE_PATH_ALIAS_RE.sub("[private_path]", sanitized)
    # Layer 2: token/secret patterns (Bearer/GitHub/OpenAI/Stripe/key-value/session)
    # — single source of truth in transport/tool_preview.redact_secret_tokens.
    # ReDoS guard: apply per-line, capping each line to _TOKEN_REDACT_LINE_LIMIT
    # before running the patterns (secrets are never multi-line).
    sanitized = "\n".join(
        redact_secret_tokens(line[:_TOKEN_REDACT_LINE_LIMIT])
        for line in sanitized.splitlines()
    )
    return sanitized


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
    # passing them to the sanitizer.
    # ReDoS guard: we pre-truncate each file to content_budget before running
    # regexes, so regex input is bounded by a few KiB, never by unbounded file
    # size.  The final _slice_utf8(combined, content_budget) enforces the exact
    # byte cap over all combined files.
    headroom = len(MEMORY_CONTEXT_OPEN.encode()) + len(MEMORY_CONTEXT_CLOSE.encode()) + 4
    content_budget = max(budget - headroom, 0)

    for rel_path in _SNAPSHOT_FILES:
        path = _resolve_workspace_path(workspace_root, rel_path)
        if path is None or not path.is_file():
            continue
        raw = path.read_text(encoding="utf-8", errors="replace")
        # ReDoS guard: pre-truncate to content_budget BEFORE running regexes,
        # so regex input is bounded by the budget (a few KiB), never unbounded.
        raw = _slice_utf8(raw, content_budget)
        redacted = _redact_snapshot_content(raw)
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
