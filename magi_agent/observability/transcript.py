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
import threading
import time
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

TranscriptSink = Callable[[dict, "str | None", "str | None"], None]


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


def emit_transcript_record(
    event: Mapping[str, object] | dict,
    session_id: "str | None",
    turn_id: "str | None",
) -> None:
    """Emit one full-fidelity record to the process-global transcript sink.

    Shared chokepoint used by both the legacy gate5b4c3 boundary (via its
    ``_emit_record`` method) and the governed hosted serving path, so the two
    surfaces write byte-identical records without duplicating the sink-lookup +
    fail-open guard. No-op when no sink is registered (transcript logging OFF);
    never raises -- a transcript write must never alter or break a chat turn."""
    try:
        sink = get_active_transcript_sink()
        if sink is None:
            return
        sink(dict(event), session_id, turn_id)
    except Exception:
        logger.debug("transcript record emit failed", exc_info=True)


def public_tool_event_to_transcript_record(
    event: object,
) -> "dict[str, object] | None":
    """Translate an engine-native public tool event into a legacy transcript
    record TYPE (decision D4).

    The governed hosted driver emits sanitized public tool events (``tool_start``
    / ``tool_end``, see :mod:`magi_agent.runtime.public_events`), whereas the
    legacy boundary emitted dedicated full-fidelity ``tool_call`` / ``tool_result``
    records. D4 requires the legacy record TYPES be preserved for transcript
    consumers, so this shim maps the public event onto the legacy record type.

    Fidelity note (FLAGGED): the public event carries only a bounded input
    *preview* (``tool_start``) and an output *preview* / digest (``tool_end``),
    and the public ``tool_end`` event carries no tool name. Therefore the
    legacy full-fidelity ``args`` / ``output`` fields and the ``tool_result``
    ``tool_name`` CANNOT be reproduced from the governed public-event stream.
    Rather than silently approximating those fields under their legacy key names,
    the preview values are surfaced under distinct ``*_preview`` keys and a
    ``transcript_source`` marker records the provenance. Returns ``None`` for any
    non-tool event (those already ride the SSE / governance path unchanged)."""
    if not isinstance(event, Mapping):
        return None
    event_type = event.get("type")
    if event_type == "tool_start":
        return {
            "type": "tool_call",
            "tool_name": str(event.get("name", "") or ""),
            "call_id": str(event.get("id", "") or ""),
            "args_preview": event.get("input_preview"),
            "transcript_source": "governed_public_event",
        }
    if event_type == "tool_end":
        return {
            "type": "tool_result",
            "call_id": str(event.get("id", "") or ""),
            "status": event.get("status"),
            "output_preview": event.get("output_preview"),
            "error": event.get("error"),
            "transcript_source": "governed_public_event",
        }
    return None


def governed_transcript_event_sink(inner_sink: object) -> object:
    """Compose the process-global transcript sink into a driver ``event_sink``
    for the governed hosted path, mirroring the CLI wiring (``combine_sinks`` +
    ``get_active_transcript_sink``) but with the D4 tool-event translation shim.

    When no transcript sink is registered this returns ``inner_sink`` UNCHANGED,
    so the driver ``event_sink`` path is byte-identical to the pre-U8 wiring (no
    per-event translation overhead and no behavior change).

    When a transcript sink IS registered, the returned 3-arg sink translates each
    public tool event to its legacy transcript record TYPE (via
    :func:`public_tool_event_to_transcript_record`) and emits it, then forwards
    the ORIGINAL event to ``inner_sink`` exactly as ``combine_sinks`` would. The
    pre-existing hosted public-sink arity contract (``inner_sink`` is a 1-arg
    ``(event)`` callable) is unchanged: ``combine_sinks`` guards each member, so
    the 3-arg call the driver makes into ``inner_sink`` is caught fail-open, and
    the public / SSE path behaves exactly as it does today (this unit does NOT
    change the public-event path, only adds the transcript record family)."""
    if get_active_transcript_sink() is None:
        return inner_sink

    from magi_agent.observability.runtime_sink import combine_sinks

    def _translate(
        event: dict,
        session_id: "str | None" = None,
        turn_id: "str | None" = None,
    ) -> None:
        record = public_tool_event_to_transcript_record(event)
        if record is not None:
            emit_transcript_record(record, session_id, turn_id)

    return combine_sinks([inner_sink, _translate])


def register_session_transcript(app: Any, runtime: Any) -> "SessionTranscriptWriter | None":
    """Install the session-transcript sink when ``MAGI_SESSION_TRANSCRIPT_ENABLED``
    is truthy. Default-OFF: returns None and registers nothing, leaving the app
    surface byte-identical. Files live under the shared observability home
    (``MAGI_OBS_HOME``) so the PVC path works on hosted pods (HOME=/, read-only
    root)."""
    from magi_agent.config.flags import flag_bool, flag_int

    if not flag_bool("MAGI_SESSION_TRANSCRIPT_ENABLED"):
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
            retention_days=flag_int("MAGI_SESSION_TRANSCRIPT_RETENTION_DAYS") or 14,
            max_files=flag_int("MAGI_SESSION_TRANSCRIPT_MAX_FILES") or 500,
        )
    except Exception:
        logger.debug("session transcript initial prune failed", exc_info=True)

    return writer
