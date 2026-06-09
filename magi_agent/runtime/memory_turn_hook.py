"""Turn-end memory hook: transcript→daily flush + compaction trigger (PR-B).

This is the WIRING half that PR-A's :class:`~magi_agent.memory.compaction_tree.CompactionTree`
deliberately left out.  PR-A built the engine (daily→weekly→monthly→ROOT synthesis)
but nothing ever fed it raw input or triggered a build.  This module is called at
the turn-finalization point of the live local chat path
(:func:`magi_agent.transport.chat._local_adk_chat_sse`) and:

1. **Flushes** a concise turn entry to ``memory/daily/YYYY-MM-DD.md`` (the tree's
   raw input layer) via :func:`~magi_agent.memory.compaction_tree.append_daily_entry`
   — gated on ``write_enabled`` and skipped for incognito / read-only modes and
   trivial turns (no tool use AND a short assistant reply).
2. **Triggers** a compaction build (``CompactionTree(...).run(today=..., force=False)``)
   once per session, gated on ``compaction_enabled``.  The engine's own 24h
   cooldown throttles real work; re-running is cheap and inert under cooldown.

GOVERNANCE INVARIANT
--------------------
The flag gates *activation*, never *capability*.  When ``write_enabled`` /
``compaction_enabled`` resolve True this hook ACTUALLY writes the daily file and
builds the tree; when the master default is OFF (PR-B keeps it OFF) every path
below short-circuits to an inert no-op — no daily file, no ROOT, no IO.

Fail-soft
---------
Memory bookkeeping must NEVER break a user's turn.  Every public entrypoint is
wrapped so any error (config resolution, file IO, summarizer) is logged and
swallowed — the turn always completes.

Determinism
-----------
Real dates are injected at the call site (``today`` defaults to
``date.today()`` only when the caller omits it), and the
:class:`~magi_agent.memory.compaction_tree.CompactionTree` summarizer stays
injectable, so tests are date-stable and model-free.
"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

from magi_agent.memory.compaction_tree import (
    CompactionTree,
    Summarizer,
    append_daily_entry,
)
from magi_agent.memory.config import MemoryRuntimeConfig, resolve_memory_config

logger = logging.getLogger(__name__)

#: Memory modes that suppress writes regardless of the gate.  ``incognito`` and
#: ``read_only`` are the two channel modes that must never persist a turn.
_NON_WRITING_MODES = frozenset({"incognito", "read_only", "read-only"})

#: A turn with no tool use is "trivial" (and skipped) when its assistant text is
#: at or under this many characters — chit-chat that is not worth a daily entry.
_TRIVIAL_ASSISTANT_CHARS = 80

#: Sessions whose first-turn compaction trigger has already fired this process.
#: Mirrors the "once per session" intent; the engine's own cooldown is the real
#: throttle, this just avoids re-running on every turn within a session.
#: Intentionally process-lifetime state (never auto-expired); only reset in tests
#: via :func:`reset_session_compaction_state`.
_compacted_sessions: set[str] = set()


def reset_session_compaction_state() -> None:
    """Clear the once-per-session compaction guard (test hook)."""
    _compacted_sessions.clear()


def _is_trivial_turn(assistant_text: str, *, used_tool: bool) -> bool:
    """A turn is trivial when no tool ran AND the assistant reply is short."""
    if used_tool:
        return False
    return len(assistant_text.strip()) <= _TRIVIAL_ASSISTANT_CHARS


def _build_turn_entry(
    *,
    user_text: str,
    assistant_text: str,
    used_tool: bool,
    turn_id: str,
) -> str:
    """Render a compact one-block daily entry for a completed turn.

    Redaction is applied here BEFORE truncation (and again, defense-in-depth,
    by ``append_daily_entry`` downstream).  Truncating first is order-fragile:
    a secret split by the ~200/~400-char cap could leave a fragment the
    redactor no longer matches.  This mirrors ROOT's redact-before-cap order
    (see ``compaction_tree._synthesize_root``).  Truncation then keeps the raw
    daily log bounded per turn (the tree's summarizer handles cross-turn
    growth).
    """
    user = _one_line(_redact(user_text), limit=200)
    assistant = _one_line(_redact(assistant_text), limit=400)
    tool_note = " [tools used]" if used_tool else ""
    parts = [f"- [turn {turn_id}]{tool_note}"]
    if user:
        parts.append(f"  - user: {user}")
    if assistant:
        parts.append(f"  - assistant: {assistant}")
    return "\n".join(parts)


def _redact(text: str) -> str:
    """Scrub secrets using the same redactor the write path uses.

    Lazy-imported (consistent with this hook's fail-soft, late-bound style) and
    fail-soft: if the redactor is unavailable for any reason we fall back to the
    raw text rather than break the turn — ``append_daily_entry`` redacts again
    downstream as defense-in-depth.
    """
    if not text:
        return text
    try:
        from magi_agent.memory.adapters.local_file_writable import (  # noqa: PLC0415
            _redact_for_write,
        )

        return _redact_for_write(text)
    except Exception:  # pragma: no cover - defensive; downstream redacts again
        logger.exception("turn-entry redaction failed; deferring to downstream redact")
        return text


def _one_line(text: str, *, limit: int) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: max(0, limit - 1)].rstrip() + "…"


def record_turn(
    *,
    workspace_root: Path | str,
    session_id: str,
    turn_id: str,
    user_text: str,
    assistant_text: str,
    used_tool: bool,
    config: MemoryRuntimeConfig | None = None,
    memory_mode: str = "normal",
    today: date | None = None,
    summarizer: Summarizer | None = None,
) -> None:
    """Flush this turn to the daily log and (once per session) build the tree.

    Fail-soft: any error is logged and swallowed — never raised into the turn.

    The file IO here is synchronous on the local-chat turn-finalization path: a
    deliberate, bounded (truncated entry + once-per-session cooldown-throttled
    build), fail-soft, default-OFF trade-off — simpler than a background queue
    and inert unless the operator opts in.

    Args:
        workspace_root: The workspace dir; ``memory/`` lives under it.
        session_id: Session key; used for the once-per-session compaction guard.
        turn_id: Turn identifier (for the daily entry header).
        user_text: The user's prompt text for this turn (may be empty).
        assistant_text: The assistant's reply text for this turn (may be empty).
        used_tool: Whether any tool ran this turn (drives trivial-turn skip).
        config: Resolved memory config; resolved from env when omitted.
        memory_mode: Channel memory mode; ``incognito``/``read_only`` suppress writes.
        today: Date for the daily file + compaction window (injected for tests).
        summarizer: Optional injectable summarizer for the compaction build.
    """
    try:
        cfg = config if config is not None else resolve_memory_config()
        memory_dir = Path(workspace_root) / "memory"
        the_date = today if today is not None else date.today()

        _maybe_flush_daily(
            cfg,
            memory_dir=memory_dir,
            turn_id=turn_id,
            user_text=user_text,
            assistant_text=assistant_text,
            used_tool=used_tool,
            memory_mode=memory_mode,
            today=the_date,
        )
        _maybe_run_compaction(
            cfg,
            memory_dir=memory_dir,
            session_id=session_id,
            today=the_date,
            summarizer=summarizer,
        )
    except Exception:  # pragma: no cover - defensive; per-step also guarded
        logger.exception("record_turn memory hook failed; continuing turn")


def _maybe_flush_daily(
    cfg: MemoryRuntimeConfig,
    *,
    memory_dir: Path,
    turn_id: str,
    user_text: str,
    assistant_text: str,
    used_tool: bool,
    memory_mode: str,
    today: date,
) -> None:
    """Append a daily entry when write is active, mode allows it, and turn is non-trivial."""
    if not cfg.write_enabled:
        return
    if memory_mode.strip().lower() in _NON_WRITING_MODES:
        return
    if _is_trivial_turn(assistant_text, used_tool=used_tool):
        return
    entry = _build_turn_entry(
        user_text=user_text,
        assistant_text=assistant_text,
        used_tool=used_tool,
        turn_id=turn_id,
    )
    try:
        append_daily_entry(memory_dir, entry, today=today)
    except Exception:
        logger.exception("daily flush failed; continuing turn")


def _maybe_run_compaction(
    cfg: MemoryRuntimeConfig,
    *,
    memory_dir: Path,
    session_id: str,
    today: date,
    summarizer: Summarizer | None,
) -> None:
    """Run the compaction tree once per session when compaction is enabled.

    The engine's own 24h cooldown is the real throttle; the per-session guard
    just avoids re-instantiating + re-running on every turn within a session.
    """
    if not cfg.compaction_enabled:
        return
    if session_id in _compacted_sessions:
        return
    _compacted_sessions.add(session_id)
    try:
        CompactionTree(memory_dir, cfg, summarizer=summarizer).run(
            today=today, force=False
        )
    except Exception:
        # CompactionTree.run is already fail-soft, but guard the construction /
        # any unexpected error too so the turn never breaks.
        logger.exception("compaction trigger failed; continuing turn")


__all__ = [
    "record_turn",
    "reset_session_compaction_state",
]
