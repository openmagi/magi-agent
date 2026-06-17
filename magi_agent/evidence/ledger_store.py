"""Reader + retention for the durable evidence-ledger files.

The CLI collector (``local_tool_collector._maybe_persist_records``) ALREADY
persists per-turn tool evidence to ``<dir>/<session>.jsonl`` by default (the
``MAGI_EVIDENCE_LEDGER_DIR`` knob; unset → ``<cwd>/.magi/evidence``). That sink
is write-only and unbounded.

This module adds the two missing pieces over those existing files, WITHOUT a
second writer:

- a shared path resolver (:func:`resolve_evidence_ledger_dir` /
  :func:`evidence_ledger_path`) so the writer and reader agree on one location;
- :class:`EvidenceLedgerReader` — a control-plane read surface (read a
  session's entries) plus :meth:`~EvidenceLedgerReader.prune` for retention.

Pure: no flag-system dependency (only the documented ``MAGI_EVIDENCE_LEDGER_DIR``
env semantics, shared with the writer). Fail-open: reading/pruning never raise.
"""
from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Mapping
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = [
    "EVIDENCE_LEDGER_DIR_ENV",
    "resolve_evidence_ledger_dir",
    "evidence_ledger_path",
    "evidence_ledger_filename",
    "write_evidence_records",
    "serve_evidence_ledger_dir",
    "EvidenceLedgerReader",
]

EVIDENCE_LEDGER_DIR_ENV = "MAGI_EVIDENCE_LEDGER_DIR"

# Values that disable the durable sink entirely (mirrors the writer).
_DISABLE_TOKENS = frozenset({"off", "0", "false", "none", "disable", "disabled"})


def evidence_ledger_filename(session_id: str) -> str:
    """Filesystem-safe ``<session>.jsonl`` stem used by writer AND reader.

    Identical to the writer's sanitization so the reader can find what the
    writer wrote: keep alnum / ``-_.``, collapse everything else to ``_``.
    """
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in session_id) or "session"
    return f"{safe}.jsonl"


def resolve_evidence_ledger_dir(env: Mapping[str, str] | None = None) -> Path | None:
    """Resolve the durable evidence directory, or ``None`` when disabled.

    Default-ON (mirrors the writer): unset → ``<cwd>/.magi/evidence``; a path
    relocates; a disable token (``off``/``0``/...) returns ``None``.
    """
    source = env if env is not None else os.environ
    raw = (source.get(EVIDENCE_LEDGER_DIR_ENV) or "").strip()
    if raw.lower() in _DISABLE_TOKENS:
        return None
    return Path(raw) if raw else Path.cwd() / ".magi" / "evidence"


def evidence_ledger_path(
    session_id: str, *, env: Mapping[str, str] | None = None
) -> Path | None:
    """Full path to a session's durable evidence file, or ``None`` if disabled."""
    base = resolve_evidence_ledger_dir(env)
    if base is None:
        return None
    return base / evidence_ledger_filename(session_id)


def write_evidence_records(
    base_dir: str | os.PathLike[str],
    *,
    session_id: str,
    turn_id: str,
    records: list[dict],
) -> None:
    """Append one JSONL line per record to ``<base_dir>/<session>.jsonl``.

    Each line has the shape ``{sessionId, turnId, toolCallId, toolName, status,
    record}`` derived from the input record dict (keys missing from the dict are
    omitted). Owner-only permissions: directory 0o700, file 0o600. Fail-open:
    never raises; any I/O or serialization error is silently swallowed.

    This is the shared writer extracted from
    ``local_tool_collector._maybe_persist_records`` so the CLI path and the
    hosted gate5b4c3 path can both call it without duplicating the
    byte-writing loop.
    """
    if not records:
        return
    try:
        target_dir = Path(base_dir)
        target_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            target_dir.chmod(0o700)
        except OSError:
            pass
        path = target_dir / evidence_ledger_filename(session_id)
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        fd = os.open(path, flags, 0o600)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        with os.fdopen(fd, "a", encoding="utf-8") as handle:
            for rec in records:
                entry: dict[str, object] = {
                    "sessionId": session_id,
                    "turnId": turn_id,
                }
                for key in ("toolCallId", "toolName", "status", "record"):
                    if key in rec:
                        entry[key] = rec[key]
                handle.write(json.dumps(entry, sort_keys=True, default=str) + "\n")
    except OSError:
        return
    except Exception:
        logger.debug("write_evidence_records failed", exc_info=True)


def serve_evidence_ledger_dir(
    *,
    default_dir: Path,
    env: Mapping[str, str] | None = None,
) -> Path | None:
    """Resolve the durable evidence directory for a *serve* context.

    Like :func:`resolve_evidence_ledger_dir` but uses *default_dir* (a
    ``Path`` supplied by the caller) instead of ``<cwd>/.magi/evidence`` when
    ``MAGI_EVIDENCE_LEDGER_DIR`` is unset. The caller supplies the home path so
    this module stays free of any ``observability`` import.

    Returns ``None`` on a disable token (``off``/``0``/``false``/``none``/
    ``disable``/``disabled``). An explicit ``MAGI_EVIDENCE_LEDGER_DIR`` path
    overrides *default_dir*. When the env var is unset or empty, *default_dir*
    is returned as-is. Fail-open: never raises.
    """
    try:
        source: Mapping[str, str] = env if env is not None else os.environ
        raw = (source.get(EVIDENCE_LEDGER_DIR_ENV) or "").strip()
        if raw.lower() in _DISABLE_TOKENS:
            return None
        return Path(raw) if raw else default_dir
    except Exception:
        logger.debug("serve_evidence_ledger_dir failed", exc_info=True)
        return default_dir


class EvidenceLedgerReader:
    """Read + prune the durable evidence files in one directory. Fail-open."""

    def __init__(self, store_dir: str | os.PathLike[str]) -> None:
        self._dir = Path(store_dir)

    @property
    def store_dir(self) -> Path:
        return self._dir

    def read(self, session_id: str) -> list[dict]:
        """Return a session's persisted evidence lines in append order.

        Best-effort: a missing file yields ``[]``; blank/unparseable lines are
        skipped. Never raises.
        """
        path = self._dir / evidence_ledger_filename(session_id)
        rows: list[dict] = []
        try:
            if not path.exists():
                return []
            text = path.read_text(encoding="utf-8")
        except OSError:
            return []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                rows.append(obj)
        return rows

    def prune(self, *, retention_days: int, max_files: int) -> int:
        """Delete files older than *retention_days* or beyond the newest
        *max_files*. Fail-open; never raises. Returns the count removed."""
        removed = 0
        try:
            files = sorted(self._dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
            if retention_days > 0:
                cutoff = time.time() - retention_days * 86400
                for path in list(files):
                    if path.stat().st_mtime < cutoff:
                        path.unlink(missing_ok=True)
                        files.remove(path)
                        removed += 1
            if max_files > 0 and len(files) > max_files:
                for path in files[: len(files) - max_files]:
                    path.unlink(missing_ok=True)
                    removed += 1
        except FileNotFoundError:
            return removed
        except Exception:
            logger.debug("evidence ledger prune failed", exc_info=True)
        return removed
