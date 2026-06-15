"""Per-session JSONL transcript writer + process-global sink registry.

Full-fidelity, append-only debug transcript of what a bot did during a session:
messages, turn stages, tool calls (name+args+result), subagent spawns, workflow
steps, errors. One file per session under ``<base>/sessions/<session_id>.jsonl``.

Internal/ops surface only — fail-open everywhere so transcript logging can never
break a chat turn. The writer is generic: it appends whatever event dict it is
given (the caller decides what to emit; e.g. streaming ``text_delta`` is left out
upstream in favour of an assembled ``message`` record).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

TranscriptSink = Callable[[dict, "str | None", "str | None"], None]


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sanitize_session_id(session_id: str | None) -> str:
    s = session_id or "unknown"
    s = s.replace("\0", "").replace("/", "_").replace("\\", "_")
    if s in (".", ".."):
        s = "_"
    return s or "unknown"


class SessionTranscriptWriter:
    """Append-only per-session JSONL writer. Thread-safe, fail-open."""

    def __init__(self, base_dir: str | Path) -> None:
        self._base = Path(base_dir)
        self._seq: dict[str | None, int] = {}
        self._lock = threading.Lock()

    def _session_path(self, session_id: str | None) -> Path:
        return self._base / "sessions" / f"{_sanitize_session_id(session_id)}.jsonl"

    def record(
        self,
        event: dict,
        session_id: str | None = None,
        turn_id: str | None = None,
    ) -> None:
        try:
            if isinstance(event, dict) and event.get("type") == "text_delta":
                # Token-level streaming noise — the assembled `message` record
                # carries the final body. Skip before consuming a seq number.
                return
            path = self._session_path(session_id)
            payload = dict(event) if isinstance(event, dict) else {}
            with self._lock:
                seq = self._seq.get(session_id, 0) + 1
                self._seq[session_id] = seq
                line: dict = {
                    "ts": _now_iso(),
                    "seq": seq,
                    "session_id": session_id,
                    "turn_id": turn_id,
                }
                line.update(payload)
                path.parent.mkdir(parents=True, exist_ok=True)
                # Append under the lock so concurrent same-session writes (turns
                # run via asyncio.run on multiple worker threads) cannot interleave
                # bytes within a single JSONL line.
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(line, default=str) + "\n")
        except Exception:
            logger.debug("session transcript record failed", exc_info=True)

    def prune(self, *, retention_days: int, max_files: int) -> int:
        """Delete session files older than *retention_days* or beyond the newest
        *max_files*. Fail-open; never raises. Returns the count removed."""
        removed = 0
        try:
            sessions_dir = self._base / "sessions"
            files = sorted(
                sessions_dir.glob("*.jsonl"),
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
            logger.debug("session transcript prune failed", exc_info=True)
        return removed


_active_sink: "TranscriptSink | None" = None


def set_active_transcript_sink(sink: "TranscriptSink | None") -> None:
    """Register *sink* as the process-global transcript sink (clear with None)."""
    global _active_sink
    _active_sink = sink


def get_active_transcript_sink() -> "TranscriptSink | None":
    """Return the currently registered transcript sink, or None."""
    return _active_sink


def register_session_transcript(app: Any, runtime: Any) -> "SessionTranscriptWriter | None":
    """Install the session-transcript sink when ``MAGI_SESSION_TRANSCRIPT_ENABLED``
    is truthy. Default-OFF: returns None and registers nothing, leaving the app
    surface byte-identical. Files live under the shared observability home
    (``MAGI_OBS_HOME``) so the PVC path works on hosted pods (HOME=/, read-only
    root)."""
    if not _truthy(os.environ.get("MAGI_SESSION_TRANSCRIPT_ENABLED")):
        return None

    from magi_agent.observability.integration import resolve_observability_home

    writer = SessionTranscriptWriter(resolve_observability_home())
    set_active_transcript_sink(writer.record)

    try:
        app.state.session_transcript_writer = writer
    except Exception:
        logger.debug("could not stash transcript writer on app.state", exc_info=True)

    try:
        writer.prune(
            retention_days=_int_env("MAGI_SESSION_TRANSCRIPT_RETENTION_DAYS", 14),
            max_files=_int_env("MAGI_SESSION_TRANSCRIPT_MAX_FILES", 500),
        )
    except Exception:
        logger.debug("session transcript initial prune failed", exc_info=True)

    return writer
