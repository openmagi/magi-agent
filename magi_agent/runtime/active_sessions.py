"""In-process active-session transcript buffer for serve session-end extraction.

The local ``serve`` chat path builds a fresh ``WorkspaceSessionService`` per
runner invocation and never holds it on the app/runtime, so at process shutdown
there is no handle to enumerate active ADK sessions. There is also no per-
conversation "end" event and no real ``/reset`` boundary.

So serve gets its session-end extraction from a small in-process buffer instead:
the chat turn seam appends each completed turn's ``{user, assistant}`` pair here
(keyed by session id), and the app lifespan ``finally`` drains every buffered
session once on shutdown, handing each transcript to the session-end extractor.

GOVERNANCE
----------
* **Zero overhead when OFF** — :func:`note_turn` no-ops (no buffering) unless
  ``MAGI_MEMORY_SESSION_EXTRACT_ENABLED`` is set, so the default install never
  grows this buffer.
* **Incognito / read-only suppressed** — those memory modes never buffer.
* **Bounded** — each session keeps at most the most-recent ``_MAX_TURNS`` turns.
* **Fail-soft** — draining swallows per-session errors; shutdown never breaks.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

#: Most-recent turns retained per session (each turn = user + assistant message).
_MAX_TURNS = 50

#: Memory modes that must never be buffered / extracted.
_SUPPRESSED_MODES = {"incognito", "read_only"}

_LOCK = threading.Lock()


@dataclass
class _SessionBuffer:
    workspace_root: str
    messages: list[dict] = field(default_factory=list)


_BUFFERS: dict[str, _SessionBuffer] = {}


def note_turn(
    *,
    session_id: str,
    workspace_root: str,
    user_text: str,
    assistant_text: str,
    memory_mode: str = "normal",
) -> None:
    """Buffer one completed turn for later session-end extraction (gated)."""
    from magi_agent.runtime.session_extract_runtime import (  # noqa: PLC0415
        session_extract_enabled,
    )

    if not session_extract_enabled():
        return
    if (memory_mode or "normal").strip().lower() in _SUPPRESSED_MODES:
        return
    if not (user_text.strip() or assistant_text.strip()):
        return

    key = session_id or "local-serve"
    with _LOCK:
        buf = _BUFFERS.get(key)
        if buf is None:
            buf = _SessionBuffer(workspace_root=str(workspace_root))
            _BUFFERS[key] = buf
        buf.messages.append({"role": "user", "content": user_text})
        buf.messages.append({"role": "assistant", "content": assistant_text})
        overflow = len(buf.messages) - _MAX_TURNS * 2
        if overflow > 0:
            del buf.messages[:overflow]


async def drain_and_extract() -> int:
    """Flush every buffered session through the session-end extractor.

    Returns the number of sessions drained. Fail-soft: a single session's
    failure is logged and skipped; this never raises into the shutdown path.
    """
    from magi_agent.runtime.session_extract_runtime import (  # noqa: PLC0415
        run_session_extract,
    )

    with _LOCK:
        items = list(_BUFFERS.items())
        _BUFFERS.clear()

    drained = 0
    for _session_id, buf in items:
        try:
            await run_session_extract(buf.messages, workspace_root=buf.workspace_root)
            drained += 1
        except Exception:  # noqa: BLE001 — never break shutdown
            logger.debug("session-extract drain failed for one session", exc_info=True)
    return drained


def _reset_for_test() -> None:
    """Clear all buffers (test helper)."""
    with _LOCK:
        _BUFFERS.clear()


def _buffered_session_count() -> int:
    """Number of currently-buffered sessions (test helper)."""
    with _LOCK:
        return len(_BUFFERS)


__all__ = ["drain_and_extract", "note_turn"]
