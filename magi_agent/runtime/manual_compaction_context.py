"""Process-level cross-turn one-shot signal for manual ``/compact`` (G7).

The live ``/compact`` slash command is dispatched on a SEPARATE local-command
turn (no model call), while the forced compaction must happen on a LATER model
turn (the next user message). A :class:`contextvars.ContextVar` — the
``memory_mode_context`` precedent — would be WRONG here: ADK/CLI runs each
top-level task/turn in its own async context, so a contextvar set during the
``/compact`` command turn does not propagate to the next model turn.

Mechanism: a MODULE-LEVEL ``_requested`` boolean guarded by a ``threading.Lock``
with one-shot ``request()`` / ``consume()`` semantics. The ``/compact`` turn
calls :func:`request_manual_compaction` (sets the global True); the next model
turn's ``before_model_callback`` calls :func:`consume_manual_compaction` (reads
True, clears to False). Because the global lives at module scope it survives the
turn boundary for the lifetime of the process. The single-process asyncio
TUI/CLI means a plain bool under a Lock is thread-safe enough.

Zero overhead and fully inert unless :func:`request_manual_compaction` is called,
which only happens behind the default-OFF surface flag gate
(:func:`manual_compaction_enabled`). Every helper is fail-open: none of them may
raise into a live turn.
"""

from __future__ import annotations

import os
import threading

MAGI_COMPACTION_MANUAL_ENABLED_ENV: str = "MAGI_COMPACTION_MANUAL_ENABLED"


# Module-level one-shot holder. Guarded by ``_lock`` so the (event-loop-only)
# writes are safe even though the headless reader daemon thread never touches
# these helpers. A plain bool is sufficient for the single-process asyncio
# TUI/CLI — no ContextVar (it would not survive the turn boundary).
_lock = threading.Lock()
_requested = False


def manual_compaction_enabled() -> bool:
    """Return True when ``MAGI_COMPACTION_MANUAL_ENABLED`` is truthy (default off)."""
    # I-1: route through the typed flag registry. ``flag_bool`` returns
    # the registered default (``False``) for unset and delegates set
    # values to ``is_true`` (canonical ``TRUE_VALUES`` = ``{"1",
    # "true", "yes", "on"}``, identical to the local ``_TRUTHY_VALUES``).
    from magi_agent.config.flags import flag_bool  # noqa: PLC0415

    return flag_bool(MAGI_COMPACTION_MANUAL_ENABLED_ENV)


def request_manual_compaction() -> None:
    """Mark a manual compaction as pending (idempotent one-shot request).

    Multiple ``/compact`` presses before a single consume collapse to one pending
    request. Fail-open: never raises into the turn that set it.
    """
    global _requested
    with _lock:
        _requested = True


def consume_manual_compaction() -> bool:
    """Return the pending flag and clear it (one-shot).

    Returns ``True`` at most once per :func:`request_manual_compaction`, then
    ``False`` until re-requested. This is the cross-turn persistence: set on the
    ``/compact`` turn, read on a LATER ``before_model`` turn. Fail-open.
    """
    global _requested
    with _lock:
        prior = _requested
        _requested = False
        return prior


def reset_manual_compaction() -> None:
    """Clear any pending request (test-only seam; mirrors the contextvar reset)."""
    global _requested
    with _lock:
        _requested = False


__all__ = [
    "MAGI_COMPACTION_MANUAL_ENABLED_ENV",
    "consume_manual_compaction",
    "manual_compaction_enabled",
    "request_manual_compaction",
    "reset_manual_compaction",
]
