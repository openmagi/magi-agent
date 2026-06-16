"""Durable append-only store for evidence-ledger entries (PR1).

Persists :class:`~magi_agent.evidence.ledger.EvidenceLedgerEntry` objects — the
structured evidence the in-memory ``EvidenceLedger`` accumulates per turn — to
one append-only JSONL file per session under ``<base>/evidence/<session>.jsonl``.

This is the canonical, machine-readable evidence store that a control-plane
``read``er can query; it complements the human-facing session transcript
(``observability/transcript.py``), which carries only an ``evidence_ref`` +
one-line summary so the full payload is not serialized twice.

Pure + decoupled: this module has NO flag reads and NO callers. Whoever
constructs an ``EvidenceLedgerStore`` (a later wiring PR, behind a flag) decides
when it runs. Entries are already sanitized at construction by the ledger model,
so the store only serializes.

Fail-open everywhere: an ``append`` failure must never raise into a live turn.
Reading and pruning are best-effort and likewise never raise.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = ["EvidenceLedgerStore"]


def _sanitize_session_id(session_id: str | None) -> str:
    """Make a session id safe as a filename (mirrors observability transcript)."""
    s = session_id or "unknown"
    s = s.replace("\0", "").replace("/", "_").replace("\\", "_")
    if s in (".", ".."):
        s = "_"
    return s or "unknown"


class EvidenceLedgerStore:
    """File-backed, append-only, per-session evidence store. Thread-safe, fail-open."""

    def __init__(self, base_dir: str | Path) -> None:
        self._base = Path(base_dir)
        self._lock = threading.RLock()

    @property
    def store_dir(self) -> Path:
        return self._base / "evidence"

    def _session_path(self, session_id: str | None) -> Path:
        return self.store_dir / f"{_sanitize_session_id(session_id)}.jsonl"

    def append(self, entry: object) -> None:
        """Append one ledger entry as a JSON line. Never raises (fail-open).

        ``entry`` is expected to be an ``EvidenceLedgerEntry`` (self-describing:
        it carries its own ``session_id``). A non-entry object is silently
        ignored so a malformed producer can never break the turn.
        """
        try:
            session_id = getattr(entry, "session_id", None)
            payload = entry.model_dump(mode="json", by_alias=True)  # type: ignore[attr-defined]
            path = self._session_path(session_id)
            line = json.dumps(payload, ensure_ascii=False, default=str) + "\n"
            with self._lock:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(line)
        except Exception:
            logger.debug("evidence ledger append failed", exc_info=True)

    def read(self, session_id: str) -> list[dict]:
        """Return the persisted entries for ``session_id`` in append order.

        Best-effort: a missing file yields ``[]``; blank/unparseable lines are
        skipped. Never raises.
        """
        path = self._session_path(session_id)
        rows: list[dict] = []
        try:
            with self._lock:
                if not path.exists():
                    return []
                text = path.read_text(encoding="utf-8")
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
        except Exception:
            logger.debug("evidence ledger read failed", exc_info=True)
        return rows

    def prune(self, *, retention_days: int, max_files: int) -> int:
        """Delete files older than *retention_days* or beyond the newest
        *max_files*. Fail-open; never raises. Returns the count removed."""
        removed = 0
        try:
            with self._lock:
                files = sorted(
                    self.store_dir.glob("*.jsonl"),
                    key=lambda p: p.stat().st_mtime,
                )
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
