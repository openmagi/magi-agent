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
# Shared, broader token/secret redactor — single source of truth, also used by
# the read-side projection (``memory/prompt_projection.py``).  ``tool_preview``
# imports only ``re`` (no transport/network at module load), so importing it at
# module top does not trip the memory import-boundary tests.  This keeps the
# write-side redactor at least as strong as the read-side one (C2 / PR-D).
from magi_agent.transport.tool_preview import MAX_TOOL_PREVIEW, redact_secret_tokens
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

# ---------------------------------------------------------------------------
# C2 — gap patterns that BOTH the existing ``_SECRET_RE`` and the shared
# ``redact_secret_tokens`` miss.  These bring the write-side redactor to parity
# with (and slightly beyond) the read-side projection redactor.
# ---------------------------------------------------------------------------

# PEM private-key blocks (RSA/EC/OPENSSH/PGP/etc).  DOTALL so the base64 body
# between the BEGIN/END markers is consumed.
#
# C1 (ReDoS): this regex ALSO runs on the FULL body (not windowed), so it must
# be super-linear-free.  Two hardening changes vs the earlier form:
#   1. The type label is bounded ``[A-Z0-9 ]{0,20}`` (real labels — RSA, EC,
#      OPENSSH, ENCRYPTED, … — are short) instead of unbounded ``[A-Z0-9 ]*``.
#   2. The body is a TEMPERED, BOUNDED dot ``(?:(?!-----BEGIN)[\s\S]){0,8192}?``.
#      The bare ``.*?`` form restarts its scan-to-EOF at EVERY ``-----BEGIN``
#      marker, so ``"-----BEGIN PRIVATE KEY-----" * 8000`` (no END) cost ~45s
#      (O(n^2)).  The ``(?!-----BEGIN)`` temper forbids the body from crossing
#      into a second BEGIN marker (killing the quadratic restart) and the
#      ``{0,8192}`` ceiling caps a single key body (real keys are ~1-4KB), so the
#      same adversarial 200KB input now redacts in ~0.001s.  A genuine block —
#      including one buried inside an adversarial run — is still matched.
_PEM_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [A-Z0-9 ]{0,20}PRIVATE KEY-----"
    r"(?:(?!-----BEGIN)[\s\S]){0,8192}?"
    r"-----END [A-Z0-9 ]{0,20}PRIVATE KEY-----",
    re.DOTALL,
)
# JSON Web Tokens: three base64url segments separated by dots.  The first
# segment starts with ``eyJ`` (``{"`` base64url-encoded), which keeps this from
# matching ordinary dotted prose.
#
# C1 audit (runs on FULL body): ReDoS-safe.  Each segment is a single bounded
# char class ``[A-Za-z0-9_-]{6,}`` and the dots between them are LITERAL — there
# is no nested/overlapping quantifier and ``.`` is not in the segment class, so a
# segment can never absorb a delimiter.  On a 200KB adversarial input (a long
# ``eyJA....`` run that never completes a valid 3-segment token) the engine fails
# linearly: measured ~0.007s.
_JWT_RE = re.compile(
    r"\beyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\b"
)
# Slack/Discord/etc incoming-webhook URLs carrying a secret path component.
#
# C1 audit (runs on FULL body): ReDoS-safe.  Each alternative is a fixed literal
# prefix followed by a SINGLE bounded char class (no nested quantifier, no
# overlap between the class and any following literal), so a long matching tail
# is consumed in one linear greedy pass.  On a 200KB adversarial slack tail it
# measured ~0.006-0.09s.
_WEBHOOK_URL_RE = re.compile(
    r"https://hooks\.slack\.com/services/[A-Za-z0-9/_+-]+|"
    r"https://(?:ptb\.|canary\.)?discord(?:app)?\.com/api/webhooks/[0-9]+/[A-Za-z0-9_-]+",
    re.IGNORECASE,
)
# Connection DSNs with inline credentials: ``scheme://user:pass@host``.  Only
# the ``user:pass`` credential portion is redacted, preserving the rest of the
# URL for context.  Restricted to a plausible scheme + non-empty password to
# avoid mangling ordinary ``http://user@host`` or ``a://b`` prose.
#
# C1 (ReDoS): the scheme class is BOUNDED to ``{0,30}``.  An UNbounded
# ``[a-z0-9+.-]*`` (which contains ``.``) backtracks catastrophically on a long
# dotted run with no ``@`` — ``"QUJjRGVm." * 8000`` (~64KB) took ~16.7s, and the
# compaction-tree I2 path feeds UNBOUNDED text through here (NOT windowed —
# this regex runs on the full body), so the cost is super-linear in body size.
# Real URI schemes are short (RFC 3986 examples top out well under 30 chars), so
# the ``{0,30}`` ceiling cannot be reached by a long dotted blob and the engine
# fails the match in linear time instead of backtracking.  Post-fix the same
# 64KB vector redacts in ~0.04s.
_DSN_CREDENTIALS_RE = re.compile(
    r"\b([a-z][a-z0-9+.-]{0,30}://)[^\s:/@]+:[^\s:/@]+(@)",
    re.IGNORECASE,
)

# ReDoS guard for the per-line token redactors (``_SECRET_RE`` and
# ``redact_secret_tokens``).  Both exhibit superlinear backtracking on long
# uniform lines, and the compaction-tree path feeds *unbounded* tier text
# through ``_redact_for_write`` (not just the byte-bounded remember() path).
# Mirrors the read-side ReDoS guard in ``prompt_projection``
# (``_TOKEN_REDACT_LINE_LIMIT = MAX_TOOL_PREVIEW + 200``).
#
# Unlike the read side (which truncates the tail away) the write side must NOT
# lose persisted memory.  Earlier versions redacted only ``line[:600]`` and
# re-attached the rest *verbatim* — that leaked any token-format secret that
# appeared past column 600 (PR-D review).  We instead redact the ENTIRE line in
# fixed-size **overlapping windows**: every window of ``_TOKEN_REDACT_WINDOW``
# original chars is run through the backtracking-prone redactors (bounded input
# → bounded time), and consecutive windows overlap by ``_TOKEN_REDACT_OVERLAP``
# so any secret straddling a window boundary is fully contained in at least one
# window.  Windows are stitched back without duplicating the overlap, so the
# non-secret remainder is preserved with no truncation.
_TOKEN_REDACT_LINE_LIMIT = MAX_TOOL_PREVIEW + 200
# Number of *committed* original chars per window.  Bounded so the redactors
# never see an unbounded string in one shot.
_TOKEN_REDACT_WINDOW = _TOKEN_REDACT_LINE_LIMIT
# Trailing margin (in original chars) redacted together with each committed
# window.  Must be ≥ the longest realistic single token-format secret so a
# secret straddling a window boundary is fully contained in the window that
# commits the chars before the boundary.  Vendor tokens / Bearer tokens / long
# AIza/JWT-ish blobs are well under 200 chars.
_TOKEN_REDACT_OVERLAP = 200

# Linear, NON-backtracking vendor/bearer token shapes.  These can appear in a
# long uniform tail with NO surrounding ``key=value`` context, so they are
# redacted on the FULL line in one shot (no windowing needed — each alternative
# is anchored on a literal prefix and uses a single bounded char class, so the
# regex is linear).  This is the only way a token past column 600 with no
# delimiter is caught.
# I-1 (write<read parity): the ``sk-`` alternative is aligned to the read-side
# ``_OPENAI_TOKEN_RE`` (``\bsk-[A-Za-z0-9._-]+\b`` in transport.tool_preview):
# it now ALLOWS ``.`` in the body and drops the ``{8,}`` floor.  The earlier
# form ``sk-(?:proj-|live|test)?[-_A-Za-z0-9]{8,}`` excluded ``.`` and required
# ≥8 chars, so a delimiter-free dotted key like ``sk-abc.def.ghi.jkl.mnopqr``
# (which the read side redacts) passed through the write side VERBATIM — a
# write<read leak.  The ``\b`` prefix (mirroring read-side) keeps the literal
# ``sk-`` from firing inside ordinary hyphenated prose (``task-list``,
# ``disk-usage``: no word boundary before ``sk``).  The class is a single
# bounded char class with a literal prefix, so it stays LINEAR / non-backtracking
# (measured ~0.004s on a 200KB ``sk-`` dotted run).  github/stripe read-side
# patterns do not permit ``.`` so their write-side alternatives are already at
# parity and are left unchanged.
_VENDOR_TOKEN_RE = re.compile(
    r"Bearer\s+[A-Za-z0-9._~+/=-]{8,}"
    r"|gh[opusr]_[A-Za-z0-9_]{8,}"
    r"|github_pat_[A-Za-z0-9_]{8,}"
    r"|\bsk-[A-Za-z0-9._-]+"
    r"|[rs]k_(?:live|test)_[A-Za-z0-9_]{8,}"
    r"|AKIA[A-Z0-9]{8,}"
    r"|glpat-[A-Za-z0-9_-]{8,}"
    r"|xox[baprs]-[A-Za-z0-9-]{8,}"
    r"|AIza[0-9A-Za-z_-]{20,}",
    re.IGNORECASE,
)
# A ``NAME=value`` / ``key: value`` / ``Bearer x`` style secret can only form
# around one of these delimiter characters.  The backtracking-prone key=value
# redactors are therefore run ONLY on windows that contain a delimiter; uniform
# delimiter-free tails skip them entirely (so a multi-KB uniform run costs O(n),
# not O(n^2)).
_TOKEN_DELIMITER_RE = re.compile(r"[:=]")

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

        # Redact secrets and sanitize. Memory entries are line-delimited, so
        # body text must be a single logical line before it is persisted.
        safe_body = _single_line_memory_text(_redact_for_write(body))

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

        if len(entry_bytes) > self.config.max_file_bytes:
            raise ValueError(
                f"remember: entry exceeds max_file_bytes "
                f"({len(entry_bytes)} > {self.config.max_file_bytes})"
            )

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
            current_size = self._compact_file(target_path, len(entry_bytes))

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
        """Determine whether the gated append-compactor is enabled (default OFF).

        I-2 PR A: delegates to the canonical truthy leaf so the truthy set
        lives in one place.
        """
        from magi_agent.config._truthy import env_bool  # noqa: PLC0415

        return env_bool(os.environ, MAGI_MEMORY_COMPACTION_ENABLED_ENV, default=False)

    def _compact_file(self, target_path: Path, incoming_entry_size: int) -> int:
        """Archive then consolidate ``target_path`` in place.

        1. Read the current content and archive it verbatim under
           ``memory/archive/<NAME>.<content-hash>.md`` (deterministic suffix —
           no clock; the hash makes identical content map to one archive).
        2. Consolidate (dedup + bounded) via the pure compactor, reserving
           ``incoming_entry_size`` bytes of headroom so the subsequent append
           of the triggering entry cannot breach ``max_file_bytes``.
        3. Atomically replace the file with the consolidated text via a
           write-to-tmp-then-rename pattern (os.replace is atomic on POSIX).
           The tmp file lives in the same directory so the rename is guaranteed
           to be on the same filesystem.

        Reuses the same workspace-containment guard as every other write path;
        never touches a path outside the workspace root. Returns the new file
        size in bytes so the caller can re-run its max_file_bytes guard.

        Args:
            target_path: The memory file to compact in place.
            incoming_entry_size: UTF-8 byte length of the about-to-be-appended
                entry. Subtracted from ``max_file_bytes`` so the compacted file
                leaves guaranteed room for the new entry.
        """
        original = target_path.read_text(encoding="utf-8")

        # Archive first (no-delete safety): deterministic content-hash suffix.
        # The archive is written BEFORE the live file is touched — if anything
        # fails after this point, the original content is preserved in both the
        # live file (unchanged until os.replace succeeds) and the archive.
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

        # Reserve headroom for the incoming entry so that after consolidation
        # the subsequent append of that entry cannot breach max_file_bytes.
        # Without this, on an all-unique near-cap file, consolidate() could fill
        # all of max_file_bytes and the new fact would still be rejected.
        headroom_max = max(self.config.max_file_bytes - incoming_entry_size, 0)
        result = consolidate(original, max_bytes=headroom_max)

        # Atomic overwrite: write consolidated text to a sibling tmp file first,
        # then rename into place. os.replace is atomic on POSIX — the live file
        # is never left partially-written. The tmp file is in the same directory
        # as the target to guarantee a same-filesystem rename.
        _atomic_write_text(target_path, result.text)
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


def _atomic_write_text(target: Path, text: str) -> None:
    """Write *text* to *target* atomically using a write-tmp-then-rename pattern.

    The tmp file (``<target>.compact.tmp``) is created in the same directory as
    *target* so the final ``os.replace`` call is guaranteed to be a same-
    filesystem rename — which is atomic on POSIX.  The fixed suffix
    ``".compact.tmp"`` is deterministic (no clock, no random) and makes the
    leftover tmp file easy to identify after a crash.

    If this function raises (e.g. ``os.replace`` fails), *target* is guaranteed
    to still contain its original content — it is never touched until the rename
    succeeds.
    """
    tmp_path = target.with_suffix(target.suffix + ".compact.tmp")
    # Write consolidated content to the sibling tmp file.
    tmp_path.write_text(text, encoding="utf-8")
    # Atomically replace the live file.  If this raises, tmp_path is left
    # behind for inspection but target is untouched.
    os.replace(tmp_path, target)


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
    """Scrub secrets from body before persisting to disk.

    C2 / PR-D: the write-side redactor must be *at least as strong* as the
    read-side projection redactor (``_redact_snapshot_content``).  We therefore
    layer the shared, broader ``redact_secret_tokens`` (the single source of
    truth, also used by projection) on top of the adapter's own structural
    patterns:

      1. Structural / multi-token shapes the shared redactor misses entirely:
         PEM private-key blocks, JWTs, Slack/Discord webhook URLs, DSNs with
         inline ``user:pass@`` credentials.  (ReDoS-safe regexes; run on full
         text.)
      2. The existing ``_SECRET_RE`` allowlist (vendor tokens + NAME=value) AND
         the shared ``redact_secret_tokens`` (Bearer/GitHub/OpenAI/Stripe/
         key=value/session) — applied per-line under a ReDoS guard so the
         result is scrubbed at least as well as the projection path.
      3. Header / sensitive-URL passes (Authorization, Cookie, DSN-by-scheme).
         (ReDoS-safe regexes; run on full text.)
    """
    # 1. Multi-token / structural secrets both per-line passes otherwise miss.
    safe = _PEM_PRIVATE_KEY_RE.sub("[redacted-private-key]", body)
    safe = _JWT_RE.sub("[redacted]", safe)
    safe = _WEBHOOK_URL_RE.sub("[redacted-url]", safe)
    safe = _DSN_CREDENTIALS_RE.sub(r"\1[redacted]\2", safe)
    # 2. Backtracking-prone token redactors, applied per-line under a ReDoS
    #    guard.  ``_SECRET_RE`` and ``redact_secret_tokens`` both blow up on
    #    long uniform lines, and the compaction-tree path feeds unbounded tier
    #    text here.  We redact the ENTIRE line in fixed-size overlapping windows
    #    (see ``_redact_token_line``): bounded input → bounded time, no verbatim
    #    tail leak past the window, and no truncation → no persisted-memory loss.
    #
    # M-1: ``str.splitlines()`` splits on the FULL Unicode line-boundary set
    # (``\r``, ``\n``, ``\r\n``, plus ``\v \f \x1c \x1d \x1e \x85    ``),
    # NOT only on ``\r\n``.  Each piece is therefore guaranteed boundary-free, so
    # every per-line redactor sees one logical line.  These boundary chars are
    # normalised to ``\n`` by the join — acceptable here because the result is
    # immediately collapsed to a single delimiter-safe line by
    # ``_single_line_memory_text`` before persisting; on the unbounded I2 path the
    # text is consolidated tier content where exact boundary preservation is not
    # required.  (If byte-exact ``\r?\n`` round-tripping ever matters, switch to an
    # explicit ``re.split(r"(\r?\n)", ...)`` that retains the separators.)
    safe = "\n".join(_redact_token_line(line) for line in safe.splitlines())
    # 3. Header / sensitive-URL passes (ReDoS-safe).
    safe = _AUTHORIZATION_HEADER_RE.sub(r"\1[redacted]", safe)
    safe = _COOKIE_HEADER_RE.sub(r"\1[redacted]", safe)
    safe = _SENSITIVE_URL_RE.sub("[redacted-url]", safe)
    return safe.strip()


def _redact_keyvalue_run(text: str) -> str:
    """Apply the backtracking-prone ``NAME=value`` / token redactors to a slice.

    ``_SECRET_RE`` and ``redact_secret_tokens`` exhibit superlinear backtracking,
    so callers MUST only ever pass a bounded ``text`` (≤ one window + overlap).
    This is the only place the unbounded-input-prone regexes run.
    """
    redacted = _SECRET_RE.sub("[redacted]", text)
    return redact_secret_tokens(redacted)


def _redact_token_line(line: str) -> str:
    """Redact token-format secrets across an ENTIRE line, ReDoS-safe & lossless.

    Two-tier strategy so the whole line is covered without ever feeding an
    unbounded string to a backtracking regex:

      1. **Vendor/bearer tokens** (``ghp_``, ``sk-…``, ``AKIA…``, ``Bearer x``,
         …) are matched by ``_VENDOR_TOKEN_RE``, a *linear* (non-backtracking)
         regex, on the FULL line in one shot.  These can hide in a long uniform
         tail with no delimiter, so they must be caught anywhere — including past
         column 600 — and the linear regex makes that O(n).

      2. **``NAME=value`` / ``key: value`` secrets** require a ``:`` or ``=``
         delimiter, and the patterns that catch them backtrack badly.  They are
         therefore run (via ``_redact_keyvalue_run``) only on fixed-size
         **overlapping windows** that actually contain a delimiter.  Each window
         of ``_TOKEN_REDACT_WINDOW`` committed chars is redacted together with a
         trailing ``_TOKEN_REDACT_OVERLAP`` margin, so a ``key=value`` secret
         straddling a window boundary is fully contained in (and redacted by) the
         window that commits the chars before the boundary.  Windows are stitched
         back without duplicating the overlap.  Delimiter-free windows skip this
         tier entirely, so a multi-KB uniform run costs O(n), not O(n^2).

    Properties:
      * **No leak** — every char is covered: vendor tokens by tier 1 over the
        full line, ``key=value`` secrets by tier 2 over every delimiter-bearing
        window (boundary-straddling secrets fall inside an overlap).
      * **ReDoS-safe** — the backtracking redactors only ever see a bounded
        ``window + overlap`` slice; the full-line pass uses only linear regexes.
      * **No data loss** — the non-secret remainder is preserved verbatim.
    """
    # Tier 1: linear vendor/bearer token shapes over the full line.
    line = _VENDOR_TOKEN_RE.sub("[redacted]", line)

    # Tier 2: key=value secrets, only where a delimiter exists.
    if not _TOKEN_DELIMITER_RE.search(line):
        return line

    # Short lines fit in a single window — redact in one shot.
    if len(line) <= _TOKEN_REDACT_WINDOW + _TOKEN_REDACT_OVERLAP:
        return _redact_keyvalue_run(line)

    window = _TOKEN_REDACT_WINDOW
    overlap = _TOKEN_REDACT_OVERLAP
    out: list[str] = []
    i = 0
    n = len(line)
    while i < n:
        commit_end = i + window
        if commit_end >= n:
            # Last window: redact the whole remaining slice and commit it all.
            out.append(_redact_keyvalue_run(line[i:]))
            break

        margin_end = min(commit_end + overlap, n)
        slice_ = line[i:margin_end]
        # Only the backtracking key=value patterns need running, and only when a
        # delimiter is present.  A delimiter-free window cannot hold a key=value
        # secret (tier 1 already removed vendor tokens), so commit it verbatim.
        if not _TOKEN_DELIMITER_RE.search(slice_):
            out.append(line[i:commit_end])
            i = commit_end
            continue

        red_window = _redact_keyvalue_run(slice_)
        red_overlap = _redact_keyvalue_run(line[commit_end:margin_end])

        if red_overlap and red_window.endswith(red_overlap):
            # No secret straddles the commit boundary: the trailing overlap was
            # redacted identically standalone, so drop it (it is committed by the
            # next window) to avoid duplication.
            out.append(red_window[: len(red_window) - len(red_overlap)])
            i = commit_end
        else:
            # A secret spans the commit boundary (or the overlap merged into a
            # neighbouring match): the redacted window differs from the standalone
            # overlap, so we cannot safely split it.  Commit the full redacted
            # window and advance past the consumed overlap.
            out.append(red_window)
            i = margin_end
    return "".join(out)


def _single_line_memory_text(text: str) -> str:
    """Collapse persisted memory text to one delimiter-safe logical line."""
    return " ".join(text.split())


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
