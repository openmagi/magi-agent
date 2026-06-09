"""LocalFileMemoryProvider — gated writable local-file memory adapter (D1).

Default: read-only.  Writes are opt-in behind an explicit authority:
  - ``LocalFileMemoryConfig(write_enabled=True)``  — explicit Python flag
  - ``MAGI_MEMORY_WRITE_ENABLED=1``               — env gate (runtime override)

When neither gate is set the provider behaves identically to the read-only
hipocampus adapter: recall() works, remember() raises UnsupportedMemoryOperationError.

Files written / read:
  <workspace_root>/MEMORY.md  — declarative memory (preferences, facts, decisions)
  <workspace_root>/USER.md    — user profile notes

Writes are append-style (archive/no-delete) and bounded by ``max_write_bytes``.
All content is redacted through the existing secret-scanner before persisting.
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any, Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field

from magi_agent.memory.compactor import consolidate
from magi_agent.memory.contracts import (
    MemoryProviderCapabilities,
    MemoryRecord,
    RecallRequest,
    RecallResult,
    UnsupportedMemoryOperationError,
)
from magi_agent.memory.policy import MemoryPolicy, evaluate_memory_policy
from magi_agent.memory.adapters.hipocampus_readonly import (
    UnsafeMemoryPathError,
    _PRODUCTION_PATH_RE,
    _DROP_SNIPPET_LINE_RE,
    _REDACT_SNIPPET_RE,
    _MARKDOWN_HEADING_RE,
    _resolve_workspace_path,
    _apply_output_budget,
    _cap,
    _digest_ref,
    _matches_query,
    _safe_snippet,
    _validate_workspace_root,
)


# ---------------------------------------------------------------------------
# Public env-gate constant — import this in tests to avoid hard-coding
# ---------------------------------------------------------------------------
MAGI_MEMORY_WRITE_ENABLED_ENV: str = "MAGI_MEMORY_WRITE_ENABLED"

# Optional, default-OFF gate for the deterministic append-compactor (B2).
# When unset the provider behaves exactly as before: append-only, bounded by
# ``max_file_bytes``, no consolidation.
MAGI_MEMORY_COMPACTION_ENABLED_ENV: str = "MAGI_MEMORY_COMPACTION_ENABLED"

_PROVIDER_ID_READONLY = "local-file-memory-readonly"
_PROVIDER_ID_WRITABLE = "local-file-memory-writable"

# Subdirectory (relative to workspace root) where pre-compaction snapshots are
# archived before consolidation. Stays inside the workspace containment check.
_ARCHIVE_SUBDIR = "memory/archive"

# Allowed target files for gated writes.  No other paths may be written.
_ALLOWED_WRITE_FILES: frozenset[str] = frozenset({"MEMORY.md", "USER.md"})
_DEFAULT_WRITE_TARGET = "MEMORY.md"

# Byte cap defaults
_DEFAULT_MAX_WRITE_BYTES = 65_536  # 64 KiB per-append
_DEFAULT_MAX_RESULT_BYTES = 32_768

# Redaction patterns reused from the hipocampus adapter + extra secret patterns.
_SECRET_RE = re.compile(
    r"(?:Bearer\s+[A-Za-z0-9._~+/=-]{8,}|gh[opusr]_[A-Za-z0-9_]{8,}|"
    r"github_pat_[A-Za-z0-9_]{8,}|"
    r"sk-(?:live|test)?[-_A-Za-z0-9]{8,}|AKIA[A-Z0-9]{8,}|"
    r"glpat-[A-Za-z0-9_-]{8,}|xox[baprs]-[A-Za-z0-9-]{8,}|"
    r"AIza[0-9A-Za-z_-]{20,}|"
    r"[A-Z0-9_]*(?:SECRET|TOKEN|KEY|PASSWORD|ACCESS_KEY)[A-Z0-9_]*\s*[:=]\s*"
    r"[^,\s}{\n]{4,})",
    re.IGNORECASE,
)
_AUTHORIZATION_HEADER_RE = re.compile(
    r"\b((?:Proxy-)?Authorization\s*:\s*[A-Za-z][A-Za-z0-9+.-]*\s+)([^\s,;]+)",
    re.IGNORECASE,
)
_COOKIE_HEADER_RE = re.compile(r"\b((?:Set-)?Cookie\s*:\s*)[^\n\r]+", re.IGNORECASE)
_SENSITIVE_URL_RE = re.compile(
    r"(?:s3|gs|gcs|supabase|postgres|postgresql|mysql|redis|mongodb|file|vault|"
    r"secret|secrets)://[^\s\"'<>]+|"
    r"https?://api\.telegram\.org/bot[0-9]+:[^/\s\"'<>]+[^\s\"'<>]*",
    re.IGNORECASE,
)

_MODEL_CONFIG = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class LocalFileMemoryConfig(BaseModel):
    """Configuration for LocalFileMemoryProvider.

    ``write_enabled`` defaults to False.  When None (unset), the provider
    falls back to the ``MAGI_MEMORY_WRITE_ENABLED`` environment variable.
    Setting ``write_enabled=True`` explicitly bypasses the env check — useful
    for tests that need deterministic write behaviour without mutating os.environ.
    """

    model_config = _MODEL_CONFIG

    workspace_root: Path = Field(alias="workspaceRoot")
    enabled: bool = False
    write_enabled: bool | None = Field(default=None, alias="writeEnabled")
    max_write_bytes: int = Field(
        default=_DEFAULT_MAX_WRITE_BYTES,
        alias="maxWriteBytes",
        ge=1,
    )
    max_result_bytes: int = Field(
        default=_DEFAULT_MAX_RESULT_BYTES,
        alias="maxResultBytes",
        ge=1,
    )
    max_file_bytes: int = Field(
        default=4_194_304,
        alias="maxFileBytes",
        ge=1,
    )
    # Compaction threshold (B2). When the gated compactor is enabled and the
    # post-append file size would reach/exceed this many bytes, the file is
    # consolidated. ``None`` (default) derives 0.9 * max_file_bytes so we leave
    # headroom below the hard cap. The env gate must ALSO be open for any
    # compaction to occur — this field is inert unless gated on.
    compaction_threshold_bytes: int | None = Field(
        default=None,
        alias="compactionThresholdBytes",
        ge=1,
    )
    max_records: int = Field(default=5, alias="maxRecords", ge=1, le=20)

    def resolved_compaction_threshold_bytes(self) -> int:
        """Effective compaction threshold (defaults to 0.9 * max_file_bytes)."""
        if self.compaction_threshold_bytes is not None:
            return self.compaction_threshold_bytes
        return max(1, int(self.max_file_bytes * 9 // 10))


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class LocalFileMemoryProvider:
    """Local-file memory provider with a gated writable tier (D1).

    *Read path* is always active when ``enabled=True`` — it loads ``MEMORY.md``
    and ``USER.md`` from the workspace root and returns them as ``MemoryRecord``
    objects, passing through the existing recall / redaction pipeline.

    *Write path* is gated:
      - ``write_enabled=True`` in config, OR
      - ``MAGI_MEMORY_WRITE_ENABLED=1`` in the environment.
    When neither gate is open ``remember()`` raises ``UnsupportedMemoryOperationError``
    and the provider is fully read-only at runtime.
    """

    prompt_projection_enabled: Literal[False] = False

    def __init__(self, config: LocalFileMemoryConfig) -> None:
        self.config = config
        self.workspace_root = _validate_workspace_root(config.workspace_root)
        self._write_active = self._resolve_write_enabled(config)
        self._compaction_active = self._resolve_compaction_enabled()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def capabilities(self) -> MemoryProviderCapabilities:
        if self._write_active:
            return MemoryProviderCapabilities(
                provider_id=_PROVIDER_ID_WRITABLE,
                storage_model="file",
                supports_write=True,
                supports_search=True,
                supports_export=True,
                consistency="snapshot",
                max_result_bytes=self.config.max_result_bytes,
                max_write_bytes=self.config.max_write_bytes,
                policy_required=("memory_mode", "source_authority", "redaction"),
                write_tier="gated_write",
            )
        return MemoryProviderCapabilities(
            provider_id=_PROVIDER_ID_READONLY,
            storage_model="file",
            supports_search=True,
            supports_export=True,
            consistency="snapshot",
            max_result_bytes=self.config.max_result_bytes,
            policy_required=("memory_mode", "source_authority", "redaction"),
        )

    async def recall(
        self,
        request: RecallRequest,
        *,
        policy: MemoryPolicy,
    ) -> RecallResult:
        return self._query_memory(request, policy=policy)

    async def search(
        self,
        request: RecallRequest,
        *,
        policy: MemoryPolicy,
    ) -> RecallResult:
        return self._query_memory(request, policy=policy)

    async def remember(self, payload: Any) -> None:  # noqa: ANN401
        """Append a declarative memory entry to MEMORY.md or USER.md.

        The write gate must be open (``write_enabled=True`` or env), otherwise
        raises ``UnsupportedMemoryOperationError``.

        Payload keys (all optional except ``body``):
          ``body``        — the content to persist (str)
          ``kind``        — memory kind label (str, default "note")
          ``target_file`` — "MEMORY.md" or "USER.md" (default "MEMORY.md")
        """
        provider_id = _PROVIDER_ID_WRITABLE if self._write_active else _PROVIDER_ID_READONLY
        if not self._write_active:
            raise UnsupportedMemoryOperationError("remember", provider_id=provider_id)

        body = _extract_body(payload)
        target_file = _extract_target_file(payload)

        # Bound check BEFORE redaction (avoid leaking length of secret material)
        body_bytes = len(body.encode("utf-8"))
        if body_bytes > self.config.max_write_bytes:
            raise ValueError(
                f"remember payload exceeds max_write_bytes "
                f"({body_bytes} > {self.config.max_write_bytes})"
            )

        # Redact secrets and sanitize
        safe_body = _redact_for_write(body)

        # Resolve target path (must stay within workspace)
        target_path = _resolve_workspace_path(self.workspace_root, target_file)
        if target_path is None:
            raise UnsupportedMemoryOperationError(
                f"remember: '{target_file}' is not a safe write target",
                provider_id=provider_id,
            )

        # Cumulative file-size cap: check BEFORE writing
        kind_raw = _extract_kind(payload)
        kind = _SECRET_RE.sub("[redacted]", kind_raw)
        entry = f"\n- [{kind}] {safe_body}\n"
        entry_bytes = entry.encode("utf-8")
        current_size = target_path.stat().st_size if target_path.exists() else 0

        # Gated compaction (B2): when enabled and the post-append size would
        # reach/exceed the compaction threshold, archive the current file and
        # write back a consolidated (deduped + bounded) version. This shrinks
        # the file so the subsequent max_file_bytes guard passes naturally.
        # Default-OFF: when the gate is closed this branch is skipped entirely
        # and behavior is byte-identical to the legacy append-only path.
        if (
            self._compaction_active
            and target_path.exists()
            and current_size + len(entry_bytes)
            >= self.config.resolved_compaction_threshold_bytes()
        ):
            current_size = self._compact_file(target_path)

        if current_size + len(entry_bytes) > self.config.max_file_bytes:
            raise ValueError(
                f"remember: appending would exceed max_file_bytes "
                f"({current_size} + {len(entry_bytes)} > {self.config.max_file_bytes})"
            )

        # USER.md profile deduplication: skip only if this exact entry line already exists.
        # Compare the fully-formatted entry (not a raw-body substring) so that
        # short facts like "vim" are not swallowed by longer lines that merely
        # contain the word (e.g. "User uses vim-like keybindings").
        if target_file == "USER.md" and target_path.exists():
            existing = target_path.read_text(encoding="utf-8")
            if entry in existing:
                return

        # Append entry
        with target_path.open("a", encoding="utf-8") as fh:
            fh.write(entry)

    async def delete(self, _record_id: str) -> None:
        provider_id = _PROVIDER_ID_WRITABLE if self._write_active else _PROVIDER_ID_READONLY
        raise UnsupportedMemoryOperationError("delete", provider_id=provider_id)

    async def redact(self, _record_id: str) -> None:
        provider_id = _PROVIDER_ID_WRITABLE if self._write_active else _PROVIDER_ID_READONLY
        raise UnsupportedMemoryOperationError("redact", provider_id=provider_id)

    async def compact(self, _record_ids: Sequence[str]) -> None:
        provider_id = _PROVIDER_ID_WRITABLE if self._write_active else _PROVIDER_ID_READONLY
        raise UnsupportedMemoryOperationError("compact", provider_id=provider_id)

    async def erase(self, _record_id: str) -> None:
        provider_id = _PROVIDER_ID_WRITABLE if self._write_active else _PROVIDER_ID_READONLY
        raise UnsupportedMemoryOperationError("erase", provider_id=provider_id)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_write_enabled(config: LocalFileMemoryConfig) -> bool:
        """Determine whether the write gate is open.

        An explicit ``config.write_enabled`` flag always takes precedence (used
        by tests for deterministic behaviour).  Otherwise the gate is resolved
        through the single ``resolve_memory_config`` source of truth — which
        reads the same ``MAGI_MEMORY_WRITE_ENABLED`` env override and also honours
        the new ``MAGI_MEMORY_ENABLED`` master switch (default OFF in PR1, so the
        effective default is unchanged from the pre-resolver env read).
        """
        if config.write_enabled is not None:
            # Explicit config flag takes precedence
            return config.write_enabled
        # Single source of truth: resolver reads MAGI_MEMORY_WRITE_ENABLED (same
        # env contract) plus the MAGI_MEMORY_ENABLED master (default OFF in PR1).
        from magi_agent.memory.config import resolve_memory_config

        return resolve_memory_config().write_enabled

    @staticmethod
    def _resolve_compaction_enabled() -> bool:
        """Determine whether the gated append-compactor is enabled (default OFF)."""
        env_val = os.environ.get(MAGI_MEMORY_COMPACTION_ENABLED_ENV, "").strip().lower()
        return env_val in {"1", "true", "yes", "on"}

    def _compact_file(self, target_path: Path) -> int:
        """Archive then consolidate ``target_path`` in place.

        1. Read the current content and archive it verbatim under
           ``memory/archive/<NAME>.<content-hash>.md`` (deterministic suffix —
           no clock; the hash makes identical content map to one archive).
        2. Consolidate (dedup + bound to max_file_bytes) via the pure compactor.
        3. Atomically replace the file with the consolidated text.

        Reuses the same workspace-containment guard as every other write path;
        never touches a path outside the workspace root. Returns the new file
        size in bytes so the caller can re-run its max_file_bytes guard.
        """
        original = target_path.read_text(encoding="utf-8")

        # Archive first (no-delete safety): deterministic content-hash suffix.
        digest = hashlib.sha256(original.encode("utf-8")).hexdigest()[:16]
        archive_rel = f"{_ARCHIVE_SUBDIR}/{target_path.name}.{digest}.md"
        archive_path = _resolve_workspace_path(self.workspace_root, archive_rel)
        if archive_path is None:
            # Containment guard rejected the archive path — refuse to compact
            # rather than risk writing outside the workspace.
            raise UnsupportedMemoryOperationError(
                "remember: archive path failed workspace containment",
                provider_id=_PROVIDER_ID_WRITABLE,
            )
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        archive_path.write_text(original, encoding="utf-8")

        # Consolidate deterministically and write back (bounded by max_file_bytes).
        result = consolidate(original, max_bytes=self.config.max_file_bytes)
        target_path.write_text(result.text, encoding="utf-8")
        return len(result.text.encode("utf-8"))

    def _query_memory(
        self,
        request: RecallRequest,
        *,
        policy: MemoryPolicy,
    ) -> RecallResult:
        provider_id = _PROVIDER_ID_WRITABLE if self._write_active else _PROVIDER_ID_READONLY
        decision = evaluate_memory_policy(request, policy)
        if not self.config.enabled:
            return _empty_result(provider_id, decision, extra_codes=("adapter_disabled",))
        if not decision.recall_allowed:
            return _empty_result(provider_id, decision)

        records: list[MemoryRecord] = []
        for rel_path in ("MEMORY.md", "USER.md"):
            rec = self._load_file_record(rel_path, request)
            if rec is not None:
                records.append(rec)

        limited = _apply_output_budget(
            records[: min(request.limit, self.config.max_records)],
            max_bytes=request.max_bytes,
        )
        return RecallResult(
            provider_id=provider_id,
            records=limited,
            recall_allowed=decision.recall_allowed,
            write_allowed=decision.write_allowed,
            prompt_projection_allowed=False,
            public_projection_allowed=decision.public_projection_allowed,
            reason_codes=decision.reason_codes,
        )

    def _load_file_record(
        self,
        rel_path: str,
        request: RecallRequest,
    ) -> MemoryRecord | None:
        path = _resolve_workspace_path(self.workspace_root, rel_path)
        if path is None or not path.is_file():
            return None
        content = path.read_text(encoding="utf-8")
        if not _matches_query(content, request.query):
            return None
        return MemoryRecord(
            id=_digest_ref("local_file_memory", rel_path),
            scope="user",
            kind="note",
            body=_safe_snippet(content, request.max_bytes),
            source_ref=_digest_ref("local_file_memory.source", rel_path),
            provider_id=_PROVIDER_ID_WRITABLE if self._write_active else _PROVIDER_ID_READONLY,
            confidence="observed",
            visibility="public-safe",
            score=0.9,
            custom_metadata={
                "sourceKind": "local_file_memory",
                "sourceFile": rel_path,
                "sourceDigest": _digest_ref("local_file_memory.source", rel_path),
            },
        )


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _empty_result(
    provider_id: str,
    decision: object,
    *,
    extra_codes: tuple[str, ...] = (),
) -> RecallResult:
    base_codes = getattr(decision, "reason_codes", ())
    return RecallResult(
        provider_id=provider_id,
        records=(),
        recall_allowed=getattr(decision, "recall_allowed"),
        write_allowed=getattr(decision, "write_allowed"),
        prompt_projection_allowed=False,
        public_projection_allowed=getattr(decision, "public_projection_allowed"),
        reason_codes=tuple(dict.fromkeys((*base_codes, *extra_codes))),
    )


def _redact_for_write(body: str) -> str:
    """Scrub secrets from body before persisting to disk."""
    safe = _SECRET_RE.sub("[redacted]", body)
    safe = _AUTHORIZATION_HEADER_RE.sub(r"\1[redacted]", safe)
    safe = _COOKIE_HEADER_RE.sub(r"\1[redacted]", safe)
    safe = _SENSITIVE_URL_RE.sub("[redacted-url]", safe)
    return safe.strip()


def _extract_body(payload: Any) -> str:  # noqa: ANN401
    """Extract the body string from a payload dict or object."""
    if isinstance(payload, dict):
        body = payload.get("body", "")
    else:
        body = getattr(payload, "body", "")
    if not isinstance(body, str):
        body = str(body)
    return body


def _extract_target_file(payload: Any) -> str:  # noqa: ANN401
    """Extract and validate the target file from payload."""
    if isinstance(payload, dict):
        raw = payload.get("target_file", _DEFAULT_WRITE_TARGET)
    else:
        raw = getattr(payload, "target_file", _DEFAULT_WRITE_TARGET)
    if not isinstance(raw, str):
        raw = _DEFAULT_WRITE_TARGET
    name = Path(raw).name
    if name not in _ALLOWED_WRITE_FILES:
        raise ValueError(
            f"unknown write target: {name!r} (allowed: MEMORY.md, USER.md)"
        )
    return name


def _extract_kind(payload: Any) -> str:  # noqa: ANN401
    if isinstance(payload, dict):
        kind = payload.get("kind", "note")
    else:
        kind = getattr(payload, "kind", "note")
    if not isinstance(kind, str) or not kind.strip():
        return "note"
    return re.sub(r"[^a-z0-9_-]", "_", kind.strip().lower())[:32]
