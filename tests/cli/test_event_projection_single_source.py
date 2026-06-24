"""G-2 — locked single source of truth for CLI event projection.

Three modules used to redo the same projection:
* ``cli/engine.py::_map_event_kind`` + 5 event-type sets (the producer).
* ``cli/headless.py::_token_text`` (8-line byte-identical with the TUI copy).
* ``cli/tui/app.py::_token_text`` (8-line byte-identical with headless).

This module locks the post-G-2 contract:

1. :func:`token_text` reads ``delta`` then ``text`` from a payload, returning
   ``""`` for any other shape — same behaviour the deleted copies pinned.
2. :func:`classify_event` maps every event-type string to the correct kind
   (parametrized over every known type).
3. The two former ``_token_text`` private copies are GONE from
   ``cli/headless.py`` and ``cli/tui/app.py`` (or, if kept, they delegate
   to :func:`token_text`).
4. The five event-type set constants live in exactly one module
   (``cli/event_projection``); ``cli/engine.py`` imports them rather than
   re-declaring frozensets with the same names.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import magi_agent
from magi_agent.cli.event_projection import (
    ARTIFACT_EVENT_TYPES,
    CONTROL_EVENT_TYPES,
    ERROR_EVENT_TYPES,
    TOKEN_EVENT_TYPES,
    TOOL_EVENT_TYPES,
    classify_event,
    token_text,
)


# ---------------------------------------------------------------------------
# token_text contract
# ---------------------------------------------------------------------------


def test_token_text_reads_delta_first() -> None:
    assert token_text({"delta": "hello", "text": "ignored"}) == "hello"


def test_token_text_falls_back_to_text() -> None:
    assert token_text({"text": "stub"}) == "stub"


def test_token_text_returns_empty_on_missing() -> None:
    assert token_text({}) == ""


def test_token_text_returns_empty_on_non_dict() -> None:
    """A non-mapping payload (a tuple from a malformed driver, ``None``,
    etc.) must NOT crash — the empty extraction is the no-op."""

    assert token_text(None) == ""
    assert token_text("plain string") == ""
    assert token_text(42) == ""


def test_token_text_returns_empty_on_non_string_values() -> None:
    """A numeric or list value under ``delta`` / ``text`` is not a token
    text — fall through and return ``""``."""

    assert token_text({"delta": 5}) == ""
    assert token_text({"delta": ["a", "b"]}) == ""


# ---------------------------------------------------------------------------
# classify_event contract — every known type maps; unknown → status
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "event_type,expected",
    [
        ("text_delta", "token"),
        ("tool_start", "tool"),
        ("tool_progress", "tool"),
        ("tool_end", "tool"),
        ("control_event", "control"),
        ("control_request", "control"),
        ("control_replay_complete", "control"),
        ("source_inspected", "artifact"),
        ("document_draft", "artifact"),
        ("research_artifact_delta", "artifact"),
        ("patch_preview", "artifact"),
        ("error", "error"),
        # Unknown / explicitly-status types fall through.
        ("turn_end", "status"),
        ("unknown_type", "status"),
        ("", "status"),
        (None, "status"),
    ],
)
def test_classify_event_matches_canonical_table(
    event_type: object, expected: str
) -> None:
    assert classify_event(event_type) == expected


# ---------------------------------------------------------------------------
# Single-source meta-tests: forbid re-introduction of the deleted copies.
# ---------------------------------------------------------------------------


_PACKAGE_ROOT = Path(magi_agent.__file__).parent


def _read(rel: str) -> str:
    return (_PACKAGE_ROOT / rel).read_text(encoding="utf-8")


def test_headless_does_not_redeclare_token_text() -> None:
    """``cli/headless.py`` must not define a private ``_token_text``
    function any more — it imports from ``event_projection``."""

    src = _read("cli/headless.py")
    # A ``def _token_text(`` line would be a regression.
    assert not re.search(r"^def\s+_token_text\s*\(", src, re.MULTILINE), (
        "cli/headless.py re-declared _token_text; route through "
        "cli.event_projection.token_text instead"
    )


def test_tui_does_not_redeclare_token_text() -> None:
    src = _read("cli/tui/app.py")
    assert not re.search(r"^def\s+_token_text\s*\(", src, re.MULTILINE), (
        "cli/tui/app.py re-declared _token_text; route through "
        "cli.event_projection.token_text instead"
    )


def test_engine_uses_canonical_event_type_sets() -> None:
    """``cli/engine.py`` must not own a parallel ``_TOKEN_EVENT_TYPES`` /
    ``_TOOL_EVENT_TYPES`` / etc. — the canonical sets live in
    ``cli/event_projection``. A regression that re-declares them would
    silently fork the vocabulary."""

    src = _read("cli/engine.py")
    forbidden = (
        "_TOKEN_EVENT_TYPES = frozenset",
        "_TOOL_EVENT_TYPES = frozenset",
        "_CONTROL_EVENT_TYPES = frozenset",
        "_ARTIFACT_EVENT_TYPES = frozenset",
        "_ERROR_EVENT_TYPES = frozenset",
    )
    offenders = [needle for needle in forbidden if needle in src]
    assert offenders == [], (
        "cli/engine.py re-declared one or more canonical event-type sets. "
        "Import them from cli.event_projection instead. "
        f"Offenders: {offenders}"
    )


# ---------------------------------------------------------------------------
# The canonical sets are non-empty and disjoint — sanity invariant.
# ---------------------------------------------------------------------------


def test_canonical_sets_are_disjoint_and_non_empty() -> None:
    all_sets = (
        TOKEN_EVENT_TYPES,
        TOOL_EVENT_TYPES,
        CONTROL_EVENT_TYPES,
        ARTIFACT_EVENT_TYPES,
        ERROR_EVENT_TYPES,
    )
    union: set[str] = set()
    for typed_set in all_sets:
        assert typed_set, "every canonical set must have at least one member"
        assert union.isdisjoint(typed_set), (
            "canonical event-type sets must be disjoint — a type cannot "
            "belong to two kinds at once"
        )
        union |= typed_set
