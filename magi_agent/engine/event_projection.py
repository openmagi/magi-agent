"""G-2 — single source of truth for CLI surface event classification + token
text extraction.

Three modules used to redo the same projection over the same
``RuntimeEvent`` shape: ``cli/engine.py`` (the producer), ``cli/headless.py``
(the headless surface), and ``cli/tui/app.py`` (the TUI surface). The two
``_token_text`` copies were byte-identical (REVIEW-A ``review/cli-tui.md``
M9) and the event-type-to-kind mapping was re-derived in three places —
adding a payload key forced edits in every copy and risked silent drift.

This module owns:

- :data:`TOKEN_EVENT_TYPES` / :data:`TOOL_EVENT_TYPES` /
  :data:`CONTROL_EVENT_TYPES` / :data:`ARTIFACT_EVENT_TYPES` /
  :data:`ERROR_EVENT_TYPES` — the canonical event-type sets.
- :func:`token_text` — the single ``("delta", "text")`` reader the two
  surfaces used to duplicate.
- :func:`classify_event` — the single event-type-to-``EventKind`` mapper
  the engine used to own privately as ``_map_event_kind``.

Import-clean: zero ADK / textual / pydantic imports so both surfaces and
``cli/engine.py`` can pull it without disturbing their cold-start discipline.
"""

from __future__ import annotations

from typing import Literal


EventKind = Literal["token", "tool", "control", "artifact", "error", "status"]


#: Public-event ``type`` strings the engine emits as streaming-text deltas.
TOKEN_EVENT_TYPES: frozenset[str] = frozenset({"text_delta"})

#: Tool-lifecycle events (start / progress / end).
TOOL_EVENT_TYPES: frozenset[str] = frozenset(
    {"tool_start", "tool_progress", "tool_end"}
)

#: Control-plane events the producer threads through the public surface.
CONTROL_EVENT_TYPES: frozenset[str] = frozenset(
    {"control_event", "control_request", "control_replay_complete"}
)

#: Artifact / source / patch-preview events.
ARTIFACT_EVENT_TYPES: frozenset[str] = frozenset(
    {"source_inspected", "document_draft", "research_artifact_delta", "patch_preview"}
)

#: Fatal / recoverable error events.
ERROR_EVENT_TYPES: frozenset[str] = frozenset({"error"})


def token_text(payload: object) -> str:
    """Extract assistant text from a token-shaped payload.

    The real ADK engine emits ``text_delta`` events whose text lives under
    the ``delta`` key, while the A1 stub used ``text``. Read both so every
    driver works. Anything else (``None``, missing keys, non-``str`` values)
    yields ``""`` — the empty extraction is the well-defined no-op.
    """

    if not isinstance(payload, dict):
        return ""
    for key in ("delta", "text"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
    return ""


def classify_event(event_type: object) -> EventKind:
    """Map a projected public-event ``type`` string to its ``EventKind``.

    Anything not listed in the canonical sets defaults to ``"status"`` —
    same fall-through the engine's private ``_map_event_kind`` carried.
    """

    if event_type in TOKEN_EVENT_TYPES:
        return "token"
    if event_type in TOOL_EVENT_TYPES:
        return "tool"
    if event_type in CONTROL_EVENT_TYPES:
        return "control"
    if event_type in ARTIFACT_EVENT_TYPES:
        return "artifact"
    if event_type in ERROR_EVENT_TYPES:
        return "error"
    return "status"


__all__ = [
    "ARTIFACT_EVENT_TYPES",
    "CONTROL_EVENT_TYPES",
    "ERROR_EVENT_TYPES",
    "EventKind",
    "TOKEN_EVENT_TYPES",
    "TOOL_EVENT_TYPES",
    "classify_event",
    "token_text",
]
