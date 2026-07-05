"""Tests for the unified file→markdown conversion entry point.

``convert_file_to_markdown`` is delegation-only: it routes by extension to the
existing ``document_read`` / ``xlsx_read`` / ``archive_extract`` handlers and
normalizes their ``ToolResult``s into a ``MarkdownConversion``.  All existing
workspace-path policy, byte gates, and sanitization are inherited from the
delegates — these tests prove the routing and the local head+tail truncation
helper, not the underlying parsers.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from magi_agent.tools.context import ToolContext
from magi_agent.tools.file_markdown import (
    QA_SUPPORTED_EXTENSIONS,
    MarkdownConversion,
    convert_file_to_markdown,
    truncate_head_tail,
)


def _ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(
        botId="test-bot",
        sessionId="test-session",
        turnId="test-turn",
        workspaceRoot=str(tmp_path),
    )


# ---------------------------------------------------------------------------
# Text-family routing (document_read delegate)
# ---------------------------------------------------------------------------


class TestTextRouting:
    @pytest.mark.parametrize("name", ["notes.txt", "readme.md", "table.csv"])
    def test_text_extensions_route_through_document_read(
        self, tmp_path: Path, name: str
    ) -> None:
        content = "alpha beta gamma\nline two with figures 42 and 7\n"
        (tmp_path / name).write_text(content, encoding="utf-8")

        conversion = convert_file_to_markdown(name, _ctx(tmp_path))

        assert isinstance(conversion, MarkdownConversion)
        assert conversion.status == "ok"
        assert conversion.source_tool == "document_read"
        assert "alpha beta gamma" in conversion.markdown
        assert "figures 42 and 7" in conversion.markdown
        assert conversion.error_code is None
        assert conversion.content_digest is not None
        assert conversion.content_digest.startswith("sha256:")

    def test_text_conversion_reports_delegate_truncation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Pin head+tail truncation OFF: the elision marker is additive and would
        # push len(markdown) above the max_chars budget.  This test verifies
        # delegate routing and truncated-flag propagation, not HEADTAIL mechanics.
        monkeypatch.setenv("MAGI_HEADTAIL_TRUNCATION_ENABLED", "0")
        (tmp_path / "big.txt").write_text("x" * 5_000, encoding="utf-8")

        conversion = convert_file_to_markdown("big.txt", _ctx(tmp_path), max_chars=100)

        assert conversion.status == "ok"
        assert conversion.truncated is True
        assert len(conversion.markdown) <= 100


# ---------------------------------------------------------------------------
# XLSX routing (xlsx_read delegate)
# ---------------------------------------------------------------------------


class TestXlsxRouting:
    def test_xlsx_routes_to_xlsx_read_and_renders_markdown_table(
        self, tmp_path: Path
    ) -> None:
        openpyxl = pytest.importorskip("openpyxl")

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["name", "amount"])
        ws.append(["widgets", 12])
        ws.append(["gadgets", 34])
        wb.save(tmp_path / "book.xlsx")

        conversion = convert_file_to_markdown("book.xlsx", _ctx(tmp_path))

        assert conversion.status == "ok"
        assert conversion.source_tool == "xlsx_read"
        assert "| name | amount |" in conversion.markdown
        assert "| widgets | 12 |" in conversion.markdown
        assert "| gadgets | 34 |" in conversion.markdown


# ---------------------------------------------------------------------------
# ZIP routing (archive_extract delegate) — entry listing only, never content
# ---------------------------------------------------------------------------


class TestZipRouting:
    def test_zip_returns_entry_listing_never_inner_content(
        self, tmp_path: Path
    ) -> None:
        inner_sentinel = "INNER-CONTENT-MUST-NOT-LEAK"
        with zipfile.ZipFile(tmp_path / "bundle.zip", "w") as zf:
            zf.writestr("inner.txt", inner_sentinel)
            zf.writestr("data/table.csv", "a,b\n1,2\n")

        conversion = convert_file_to_markdown("bundle.zip", _ctx(tmp_path))

        assert conversion.status == "ok"
        assert conversion.source_tool == "archive_extract"
        assert "inner.txt" in conversion.markdown
        assert "data/table.csv" in conversion.markdown
        assert inner_sentinel not in conversion.markdown


# ---------------------------------------------------------------------------
# Unsupported extension + policy inheritance
# ---------------------------------------------------------------------------


class TestRoutingPolicy:
    def test_unknown_extension_is_blocked(self, tmp_path: Path) -> None:
        (tmp_path / "blob.qqq").write_text("data", encoding="utf-8")

        conversion = convert_file_to_markdown("blob.qqq", _ctx(tmp_path))

        assert conversion.status == "blocked"
        assert conversion.error_code == "document_extension_not_supported"
        assert conversion.markdown == ""

    def test_workspace_escape_is_blocked_by_delegate_policy(
        self, tmp_path: Path
    ) -> None:
        conversion = convert_file_to_markdown("../../etc/secrets.txt", _ctx(tmp_path))

        assert conversion.status == "blocked"
        assert conversion.error_code == "path_escapes_workspace"
        assert conversion.markdown == ""

    def test_supported_extensions_cover_the_qa_set(self) -> None:
        assert QA_SUPPORTED_EXTENSIONS == frozenset(
            {
                ".pdf",
                ".docx",
                ".pptx",
                ".xml",
                ".csv",
                ".txt",
                ".md",
                ".rst",
                ".xlsx",
                ".zip",
            }
        )


# ---------------------------------------------------------------------------
# truncate_head_tail
# ---------------------------------------------------------------------------


class TestTruncateHeadTail:
    def test_short_input_is_a_no_op(self) -> None:
        text, truncated = truncate_head_tail("short text", 100)
        assert text == "short text"
        assert truncated is False

    def test_keeps_head_and_tail_with_middle_marker(self) -> None:
        source = "HEAD-" + ("m" * 10_000) + "-TAIL"
        text, truncated = truncate_head_tail(source, 500)

        assert truncated is True
        assert len(text) <= 500
        assert text.startswith("HEAD-")
        assert text.endswith("-TAIL")
        assert "chars truncated (middle)" in text

    def test_head_gets_the_larger_share_of_the_budget(self) -> None:
        source = ("h" * 5_000) + ("t" * 5_000)
        text, truncated = truncate_head_tail(source, 1_000)

        assert truncated is True
        head_chars = text.split("\n\n", 1)[0]
        tail_chars = text.rsplit("\n\n", 1)[-1]
        assert len(head_chars) > len(tail_chars)

    def test_exact_budget_is_not_truncated(self) -> None:
        source = "x" * 200
        text, truncated = truncate_head_tail(source, 200)
        assert text == source
        assert truncated is False
