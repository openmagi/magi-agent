from __future__ import annotations

import re

from magi_agent.tools.output_budget import budget_tool_result
from magi_agent.tools.result import ToolResult


_MARKER_RE = re.compile(r"\n<(\d+) chars elided>\n")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _preview(*, output: str, limit: int) -> tuple[str, bool]:
    budgeted = budget_tool_result(
        ToolResult(status="ok", output=output),
        llm_preview_chars=limit,
    )
    preview = budgeted.llm_preview
    assert isinstance(preview, str)
    return preview, budgeted.truncation.llm_preview_truncated


# ---------------------------------------------------------------------------
# Within-limit behavior is unchanged
# ---------------------------------------------------------------------------

def test_preview_within_limit_unchanged_no_marker() -> None:
    preview, truncated = _preview(output="short output", limit=4000)
    assert preview == "short output"
    assert truncated is False
    assert _MARKER_RE.search(preview) is None


def test_preview_exact_boundary_length_unchanged() -> None:
    text = "b" * 64
    preview, truncated = _preview(output=text, limit=64)
    assert preview == text
    assert truncated is False


# ---------------------------------------------------------------------------
# Over-limit → head + tail + elision marker
# ---------------------------------------------------------------------------

def test_preview_over_limit_keeps_head_and_tail_with_marker() -> None:
    text = "HEAD" + ("m" * 500) + "TAIL"
    preview, truncated = _preview(output=text, limit=100)

    assert truncated is True
    assert preview.startswith("HEAD")
    assert preview.endswith("TAIL")
    match = _MARKER_RE.search(preview)
    assert match is not None


def test_preview_marker_reports_actual_elided_char_count() -> None:
    text = "x" * 1000
    preview, truncated = _preview(output=text, limit=100)

    assert truncated is True
    match = _MARKER_RE.search(preview)
    assert match is not None
    elided = int(match.group(1))
    kept = len(preview) - len(match.group(0))
    assert elided == len(text) - kept


def test_preview_total_length_never_exceeds_limit() -> None:
    for limit in (30, 50, 100, 1200, 4000):
        for size in (limit + 1, limit * 2, 50_000):
            preview, truncated = _preview(output="y" * size, limit=limit)
            assert truncated is True
            assert len(preview) <= limit, (limit, size, len(preview))


def test_preview_head_and_tail_are_contiguous_slices_of_source() -> None:
    text = "".join(chr(ord("a") + (i % 26)) for i in range(2000))
    preview, truncated = _preview(output=text, limit=200)

    assert truncated is True
    match = _MARKER_RE.search(preview)
    assert match is not None
    head = preview[: match.start()]
    tail = preview[match.end() :]
    assert text.startswith(head)
    assert text.endswith(tail)
    assert head
    assert tail


# ---------------------------------------------------------------------------
# Tiny limits degrade gracefully to head-only clamp
# ---------------------------------------------------------------------------

def test_preview_limit_smaller_than_marker_degrades_to_head_only() -> None:
    text = "z" * 500
    preview, truncated = _preview(output=text, limit=10)

    assert truncated is True
    assert preview == "z" * 10
    assert _MARKER_RE.search(preview) is None


# ---------------------------------------------------------------------------
# Transcript preview path shares the behavior
# ---------------------------------------------------------------------------

def test_transcript_preview_uses_head_tail_truncation() -> None:
    budgeted = budget_tool_result(
        ToolResult(
            status="ok",
            output="ignored",
            llmOutput="llm fits",
            transcriptOutput="T" * 2000,
        ),
        llm_preview_chars=4000,
        transcript_preview_chars=120,
    )
    preview = budgeted.transcript_preview
    assert isinstance(preview, str)
    assert budgeted.truncation.transcript_preview_truncated is True
    assert len(preview) <= 120
    match = _MARKER_RE.search(preview)
    assert match is not None
    assert preview.startswith("T")
    assert preview.endswith("T")


# ---------------------------------------------------------------------------
# Unicode safety — str slicing operates on code points
# ---------------------------------------------------------------------------

def test_preview_unicode_text_truncates_on_code_points() -> None:
    text = "한글텍스트" * 200 + "🤖" * 50
    preview, truncated = _preview(output=text, limit=80)

    assert truncated is True
    assert len(preview) <= 80
    # Round-trips through UTF-8 without surrogate errors.
    assert preview.encode("utf-8").decode("utf-8") == preview
    match = _MARKER_RE.search(preview)
    assert match is not None
    tail = preview[match.end() :]
    assert text.endswith(tail)
