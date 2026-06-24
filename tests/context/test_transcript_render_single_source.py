"""D-13 — locked single source of truth for transcript rendering.

Two renderers used to emit ``[role]: content`` lines from per-message
pieces: ``context/auto_compact.AutoCompactionEngine._format_conversation``
(dict-message shape, dormant pre-ADK path) and
``adk_bridge/context_compaction._render_dropped_transcript`` (ADK
``types.Content`` shape, live compaction stack). The dropped-transcript
renderer's docstring even cited ``_format_conversation`` as its
model. REVIEW-A engine L1 (D-13) flagged the silent-drift hazard.

This module locks the post-D-13 contract:

1. ``render_transcript`` produces ``"\\n\\n"``-joined ``[role]: <pieces
   joined by ' '>`` lines — the common skeleton.
2. ``total_cap`` drops segments whose cumulative length would exceed
   the cap and appends ``truncation_marker``.
3. ``auto_compact._format_conversation`` produces byte-identical output
   to the documented pre-D-13 contract for the edge cases that exercise
   the role-bracket / empty-content / multi-message paths.
4. ``_render_dropped_transcript`` is a thin adapter on top of
   ``render_transcript`` (does not re-implement the ``[role]: ...``
   line construction or the ``"\\n\\n"`` joining locally).
5. Both call sites import ``NormalizedSegment`` + ``render_transcript``
   from the shared module — a meta-test forbids re-introducing a
   private ``"[role]: " + ' '.join`` construction at either call site.
"""

from __future__ import annotations

import re
from pathlib import Path

import magi_agent
from magi_agent.context.auto_compact import AutoCompactionEngine
from magi_agent.context.transcript_render import (
    NormalizedSegment,
    render_transcript,
)


# ---------------------------------------------------------------------------
# render_transcript primitive contract
# ---------------------------------------------------------------------------


def test_render_transcript_joins_with_double_newline() -> None:
    out = render_transcript(
        [
            NormalizedSegment(role="user", pieces=("hello",)),
            NormalizedSegment(role="assistant", pieces=("world",)),
        ]
    )
    assert out == "[user]: hello\n\n[assistant]: world"


def test_render_transcript_empty_pieces_emit_role_marker() -> None:
    """Auto-compact's pre-D-13 behaviour: an empty message still emits
    the ``[role]: `` marker. The renderer renders every segment passed
    in; adapters that want suppression filter BEFORE constructing."""

    out = render_transcript([NormalizedSegment(role="user", pieces=())])
    assert out == "[user]: "


def test_render_transcript_total_cap_drops_and_marks() -> None:
    seg_a = NormalizedSegment(role="u", pieces=("a" * 30,))
    seg_b = NormalizedSegment(role="u", pieces=("b" * 30,))
    out = render_transcript(
        [seg_a, seg_b], total_cap=40, truncation_marker="\n…[truncated]"
    )
    # seg_a fits (37 chars: "[u]: " + 30 a's = 35), seg_b would exceed.
    assert out.startswith("[u]: aaaa")
    assert out.endswith("…[truncated]")
    assert "bbbb" not in out


def test_render_transcript_total_cap_unbounded_when_none() -> None:
    pieces = tuple("a" * 5000 for _ in range(3))
    out = render_transcript(
        [NormalizedSegment(role="u", pieces=pieces)],
        total_cap=None,
        truncation_marker="\n…[truncated]",
    )
    assert "…[truncated]" not in out


def test_render_transcript_pieces_joined_with_space() -> None:
    out = render_transcript(
        [NormalizedSegment(role="u", pieces=("text", "[tool_call x]", "[tool_result y]"))]
    )
    assert out == "[u]: text [tool_call x] [tool_result y]"


# ---------------------------------------------------------------------------
# auto_compact byte-identity vs pre-D-13 contract
# ---------------------------------------------------------------------------


def test_auto_compact_format_conversation_empty_messages() -> None:
    assert AutoCompactionEngine._format_conversation([]) == ""


def test_auto_compact_format_conversation_empty_string_content() -> None:
    """Pre-D-13 emitted ``[user]: `` for empty string content; preserved."""

    assert (
        AutoCompactionEngine._format_conversation([{"role": "user", "content": ""}])
        == "[user]: "
    )


def test_auto_compact_format_conversation_empty_list_content() -> None:
    assert (
        AutoCompactionEngine._format_conversation([{"role": "user", "content": []}])
        == "[user]: "
    )


def test_auto_compact_format_conversation_text_messages() -> None:
    assert (
        AutoCompactionEngine._format_conversation(
            [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi"},
            ]
        )
        == "[user]: hello\n\n[assistant]: hi"
    )


def test_auto_compact_format_conversation_list_text_blocks() -> None:
    out = AutoCompactionEngine._format_conversation(
        [
            {
                "role": "user",
                "content": [{"type": "text", "text": "x"}, {"type": "text", "text": "y"}],
            }
        ]
    )
    assert out == "[user]: x\ny"


def test_auto_compact_format_conversation_per_message_cap_2000() -> None:
    big = "a" * 5000
    out = AutoCompactionEngine._format_conversation(
        [{"role": "user", "content": big}]
    )
    # Original capped to 2000 per message ([:2000])
    assert out == f"[user]: {'a' * 2000}"


def test_auto_compact_format_conversation_unknown_role_fallback() -> None:
    assert (
        AutoCompactionEngine._format_conversation([{"content": "x"}])
        == "[unknown]: x"
    )


# ---------------------------------------------------------------------------
# Meta-tests: neither call site re-implements the shared skeleton.
# ---------------------------------------------------------------------------


_PACKAGE_ROOT = Path(magi_agent.__file__).parent


def _read(rel: str) -> str:
    return (_PACKAGE_ROOT / rel).read_text(encoding="utf-8")


def test_auto_compact_routes_through_render_transcript() -> None:
    """``_format_conversation`` must import ``render_transcript`` so the
    shared renderer owns the role-bracket / line-join skeleton."""

    src = _read("context/auto_compact.py")
    assert "from magi_agent.context.transcript_render" in src or (
        "magi_agent.context.transcript_render" in src and "render_transcript" in src
    ), "auto_compact._format_conversation must route through the shared renderer"


def test_context_compaction_routes_through_render_transcript() -> None:
    src = _read("adk_bridge/context_compaction.py")
    assert "from magi_agent.context.transcript_render" in src
    assert "render_transcript" in src


def test_context_compaction_does_not_locally_build_role_line() -> None:
    """The live dropped-transcript renderer must not re-implement the
    ``[role]: ...`` line construction or the ``"\\n\\n"`` joining
    locally — those belong to ``render_transcript``. A regression that
    re-adds a local ``f"[{role}]: ..."`` + ``"\\n\\n".join(lines)``
    block trips this test."""

    src = _read("adk_bridge/context_compaction.py")
    # The single permitted f-string under D-13 is inside the docstring
    # of the public dropped-transcript renderer (mentioned in the
    # piece-formatting prose). The renderer's body must not contain a
    # ``"\n\n".join(lines)`` or ``f"[{role}]: "`` build any more.
    forbidden_join = re.search(r'"\\n\\n"\.join\(lines\)', src)
    forbidden_role = re.search(
        r'line\s*=\s*f"\[\{role\}\]:\s+\{', src
    )
    offenders = []
    if forbidden_join:
        offenders.append("\\n\\n.join(lines) regression in context_compaction.py")
    if forbidden_role:
        offenders.append('local f"[{role}]: " line build in context_compaction.py')
    assert offenders == [], (
        "context_compaction re-introduced a local transcript-render block; "
        "route through ``context.transcript_render.render_transcript`` "
        f"instead. Offenders: {offenders}"
    )
