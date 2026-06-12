"""Tests for file-tools v2 — page-addressable PDF + in-doc search, ArchiveExtract, spreadsheet
structure.

All tests are hermetic: tiny fixtures constructed at test time, no network calls.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from magi_agent.tools.context import ToolContext


# ---------------------------------------------------------------------------
# Shared helper
# ---------------------------------------------------------------------------


def _ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(
        botId="test-bot",
        sessionId="test-session",
        turnId="test-turn",
        workspaceRoot=str(tmp_path),
    )


def _ctx_no_workspace() -> ToolContext:
    return ToolContext(
        botId="test-bot",
        sessionId="test-session",
        turnId="test-turn",
    )


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _make_two_page_pdf(path: Path) -> None:
    """Write a minimal two-page PDF with distinct text on each page.

    Uses raw PDF byte construction so no external PDF-writing library beyond
    pypdf (for reading) is required.
    """
    page_texts = ["PageOneContent nuclear", "PageTwoContent footnote397"]
    path.write_bytes(_build_minimal_pdf(page_texts))


def _build_minimal_pdf(page_texts: list[str]) -> bytes:
    """Build a minimal valid PDF with one text line per page (no compression).

    The PDF is constructed entirely with stdlib — no fpdf/reportlab needed.
    ``pypdf`` can read text from these pages because the content streams are
    uncompressed and use standard Type1 fonts.
    """
    n = len(page_texts)
    objects: list[bytes] = []

    # Object 1 — Catalog
    objects.append(b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n")

    # Object 2 — Pages node
    kids = " ".join(f"{i + 3} 0 R" for i in range(n))
    objects.append(
        f"2 0 obj\n<< /Type /Pages /Kids [{kids}] /Count {n} >>\nendobj\n".encode()
    )

    # Objects 3..(2+n) — Page dictionaries
    for i, _ in enumerate(page_texts):
        obj_num = i + 3
        content_ref = obj_num + n  # content streams come after page dicts
        objects.append(
            (
                f"{obj_num} 0 obj\n"
                f"<< /Type /Page /Parent 2 0 R\n"
                f"   /MediaBox [0 0 612 792]\n"
                f"   /Contents {content_ref} 0 R\n"
                f"   /Resources << /Font << /F1 << /Type /Font "
                f"/Subtype /Type1 /BaseFont /Helvetica >> >> >>\n"
                f">>\nendobj\n"
            ).encode()
        )

    # Objects (3+n)..(2+2n) — Content streams
    for i, text in enumerate(page_texts):
        obj_num = i + 3 + n
        stream = f"BT /F1 12 Tf 72 700 Td ({text}) Tj ET\n".encode()
        objects.append(
            f"{obj_num} 0 obj\n<< /Length {len(stream)} >>\nstream\n".encode()
            + stream
            + b"\nendstream\nendobj\n"
        )

    # Assemble body and compute xref offsets
    header = b"%PDF-1.4\n"
    pos = len(header)
    offsets: list[int] = []
    for obj in objects:
        offsets.append(pos)
        pos += len(obj)

    xref_offset = pos
    xref = f"xref\n0 {len(objects) + 1}\n".encode()
    xref += b"0000000000 65535 f \r\n"
    for off in offsets:
        xref += f"{off:010d} 00000 n \r\n".encode()

    trailer = (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n"
    ).encode()

    return header + b"".join(objects) + xref + trailer


def _make_zip_with_inner_file(zip_path: Path, inner_name: str, inner_content: str) -> None:
    """Write a .zip containing a single text file."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(inner_name, inner_content)
    zip_path.write_bytes(buf.getvalue())


def _make_xlsx_two_sheets(path: Path) -> None:
    """Write a minimal .xlsx with two sheets: 'Alpha' and 'Beta'."""
    import openpyxl  # noqa: PLC0415

    wb = openpyxl.Workbook()
    ws1 = wb.active
    assert ws1 is not None
    ws1.title = "Alpha"
    ws1.append(["Col1", "Col2"])
    ws1.append(["A1", "B1"])
    ws1.append(["A2", "B2"])
    ws2 = wb.create_sheet("Beta")
    ws2.append(["X", "Y", "Z"])
    ws2.append(["10", "20", "30"])
    wb.save(str(path))


# ===========================================================================
# Part 1 — Page-addressable PDF + in-doc search
# ===========================================================================


class TestDocumentSearch:
    """document_search: in-document term search returning page/snippet."""

    def test_find_term_returns_ok(self, tmp_path: Path) -> None:
        _make_two_page_pdf(tmp_path / "doc.pdf")
        from magi_agent.tools.document_tools import document_search

        result = document_search({"path": "doc.pdf", "query": "nuclear"}, _ctx(tmp_path))
        assert result.status == "ok", f"{result.status}: {result.error_code}"

    def test_find_term_returns_matches(self, tmp_path: Path) -> None:
        _make_two_page_pdf(tmp_path / "doc.pdf")
        from magi_agent.tools.document_tools import document_search

        result = document_search({"path": "doc.pdf", "query": "nuclear"}, _ctx(tmp_path))
        assert result.status == "ok"
        matches = result.output["matches"]  # type: ignore[index]
        assert isinstance(matches, list)
        assert len(matches) >= 1

    def test_find_term_includes_page_number(self, tmp_path: Path) -> None:
        _make_two_page_pdf(tmp_path / "doc.pdf")
        from magi_agent.tools.document_tools import document_search

        result = document_search({"path": "doc.pdf", "query": "nuclear"}, _ctx(tmp_path))
        assert result.status == "ok"
        matches = result.output["matches"]  # type: ignore[index]
        assert len(matches) >= 1
        first = matches[0]
        assert "page" in first, f"Expected 'page' key in match: {first!r}"
        assert isinstance(first["page"], int)

    def test_find_term_includes_snippet(self, tmp_path: Path) -> None:
        _make_two_page_pdf(tmp_path / "doc.pdf")
        from magi_agent.tools.document_tools import document_search

        result = document_search({"path": "doc.pdf", "query": "nuclear"}, _ctx(tmp_path))
        assert result.status == "ok"
        matches = result.output["matches"]  # type: ignore[index]
        first = matches[0]
        assert "snippet" in first, f"Expected 'snippet' key in match: {first!r}"
        assert "nuclear" in first["snippet"].lower()

    def test_find_footnote_term(self, tmp_path: Path) -> None:
        _make_two_page_pdf(tmp_path / "doc.pdf")
        from magi_agent.tools.document_tools import document_search

        result = document_search({"path": "doc.pdf", "query": "footnote397"}, _ctx(tmp_path))
        assert result.status == "ok"
        matches = result.output["matches"]  # type: ignore[index]
        assert len(matches) >= 1
        assert matches[0]["page"] == 2  # second page

    def test_no_matches_returns_empty_list(self, tmp_path: Path) -> None:
        _make_two_page_pdf(tmp_path / "doc.pdf")
        from magi_agent.tools.document_tools import document_search

        result = document_search({"path": "doc.pdf", "query": "xyzzy_not_present"}, _ctx(tmp_path))
        assert result.status == "ok"
        matches = result.output["matches"]  # type: ignore[index]
        assert matches == []

    def test_match_count_in_output(self, tmp_path: Path) -> None:
        _make_two_page_pdf(tmp_path / "doc.pdf")
        from magi_agent.tools.document_tools import document_search

        result = document_search({"path": "doc.pdf", "query": "nuclear"}, _ctx(tmp_path))
        assert result.status == "ok"
        assert "matchCount" in result.output  # type: ignore[operator]

    def test_missing_path_returns_blocked(self, tmp_path: Path) -> None:
        from magi_agent.tools.document_tools import document_search

        result = document_search({"query": "nuclear"}, _ctx(tmp_path))
        assert result.status == "blocked"
        assert result.error_code == "path_required"

    def test_missing_query_returns_blocked(self, tmp_path: Path) -> None:
        _make_two_page_pdf(tmp_path / "doc.pdf")
        from magi_agent.tools.document_tools import document_search

        result = document_search({"path": "doc.pdf"}, _ctx(tmp_path))
        assert result.status == "blocked"
        assert result.error_code == "query_required"

    def test_path_escape_rejected(self, tmp_path: Path) -> None:
        from magi_agent.tools.document_tools import document_search

        result = document_search({"path": "../outside.pdf", "query": "x"}, _ctx(tmp_path))
        assert result.status == "blocked"
        assert result.error_code == "path_escapes_workspace"

    def test_workspace_root_required(self) -> None:
        from magi_agent.tools.document_tools import document_search

        result = document_search({"path": "doc.pdf", "query": "x"}, _ctx_no_workspace())
        assert result.status == "blocked"
        assert result.error_code == "workspace_root_required"

    def test_unsupported_extension_blocked(self, tmp_path: Path) -> None:
        (tmp_path / "file.docx").write_bytes(b"\x00" * 10)
        from magi_agent.tools.document_tools import document_search

        result = document_search({"path": "file.docx", "query": "x"}, _ctx(tmp_path))
        assert result.status == "blocked"
        assert result.error_code == "document_search_not_supported_for_format"

    def test_no_pypdf_returns_blocked(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_two_page_pdf(tmp_path / "doc.pdf")
        import sys

        monkeypatch.setitem(sys.modules, "pypdf", None)  # type: ignore[arg-type]
        from magi_agent.tools.document_tools import document_search

        result = document_search({"path": "doc.pdf", "query": "nuclear"}, _ctx(tmp_path))
        assert result.status == "blocked"
        assert result.error_code == "document_dependency_not_installed"

    def test_document_search_registered_in_manifest(self) -> None:
        from magi_agent.tools.file_tool_manifests import file_tool_manifests

        names = {m.name for m in file_tool_manifests()}
        assert "DocumentSearch" in names, f"DocumentSearch not found; names={names}"

    def test_document_search_appears_in_build_cli_adk_tools(self, tmp_path: Path) -> None:
        import os
        from unittest.mock import patch

        with patch.dict(os.environ, {"MAGI_FILE_TOOLS_ENABLED": "true"}):
            from magi_agent.cli.tool_runtime import build_cli_adk_tools

            tools = build_cli_adk_tools(workspace_root=str(tmp_path))
        tool_names = {getattr(t, "name", None) for t in tools}
        assert "DocumentSearch" in tool_names, f"DocumentSearch not in tools: {tool_names}"


# ===========================================================================
# Part 2 — ArchiveExtract (unzip)
# ===========================================================================


class TestArchiveExtract:
    """ArchiveExtract: extract a .zip in the workspace + read an inner file."""

    def test_list_contents_returns_ok(self, tmp_path: Path) -> None:
        _make_zip_with_inner_file(tmp_path / "data.zip", "readme.txt", "Hello inner file.")
        from magi_agent.tools.archive_tools import archive_extract

        result = archive_extract({"path": "data.zip"}, _ctx(tmp_path))
        assert result.status == "ok", f"{result.status}: {result.error_code}"

    def test_list_contents_includes_inner_file(self, tmp_path: Path) -> None:
        _make_zip_with_inner_file(tmp_path / "data.zip", "readme.txt", "Hello inner file.")
        from magi_agent.tools.archive_tools import archive_extract

        result = archive_extract({"path": "data.zip"}, _ctx(tmp_path))
        assert result.status == "ok"
        entries = result.output["entries"]  # type: ignore[index]
        assert isinstance(entries, list)
        names = [e["name"] for e in entries]
        assert "readme.txt" in names

    def test_read_inner_file_returns_content(self, tmp_path: Path) -> None:
        _make_zip_with_inner_file(tmp_path / "data.zip", "inner.txt", "XmlCategoryValue")
        from magi_agent.tools.archive_tools import archive_extract

        result = archive_extract({"path": "data.zip", "readEntry": "inner.txt"}, _ctx(tmp_path))
        assert result.status == "ok"
        content = result.output["entryContent"]  # type: ignore[index]
        assert "XmlCategoryValue" in content

    def test_read_xml_inner_file(self, tmp_path: Path) -> None:
        xml_content = "<categories><item>ALPHA</item><item>BETA</item></categories>"
        _make_zip_with_inner_file(tmp_path / "archive.zip", "data.xml", xml_content)
        from magi_agent.tools.archive_tools import archive_extract

        result = archive_extract({"path": "archive.zip", "readEntry": "data.xml"}, _ctx(tmp_path))
        assert result.status == "ok"
        content = result.output["entryContent"]  # type: ignore[index]
        assert "ALPHA" in content

    def test_path_traversal_in_read_entry_rejected(self, tmp_path: Path) -> None:
        _make_zip_with_inner_file(tmp_path / "data.zip", "safe.txt", "safe")
        from magi_agent.tools.archive_tools import archive_extract

        result = archive_extract(
            {"path": "data.zip", "readEntry": "../../../etc/passwd"}, _ctx(tmp_path)
        )
        assert result.status == "blocked"
        assert result.error_code == "archive_entry_traversal_denied"

    def test_missing_entry_returns_error(self, tmp_path: Path) -> None:
        _make_zip_with_inner_file(tmp_path / "data.zip", "safe.txt", "safe")
        from magi_agent.tools.archive_tools import archive_extract

        result = archive_extract(
            {"path": "data.zip", "readEntry": "nonexistent.txt"}, _ctx(tmp_path)
        )
        assert result.status in ("error", "blocked")
        assert result.error_code == "archive_entry_not_found"

    def test_missing_path_returns_blocked(self, tmp_path: Path) -> None:
        from magi_agent.tools.archive_tools import archive_extract

        result = archive_extract({}, _ctx(tmp_path))
        assert result.status == "blocked"
        assert result.error_code == "path_required"

    def test_path_escape_rejected(self, tmp_path: Path) -> None:
        from magi_agent.tools.archive_tools import archive_extract

        result = archive_extract({"path": "../outside.zip"}, _ctx(tmp_path))
        assert result.status == "blocked"
        assert result.error_code == "path_escapes_workspace"

    def test_non_zip_extension_rejected(self, tmp_path: Path) -> None:
        (tmp_path / "file.tar.gz").write_bytes(b"\x00" * 10)
        from magi_agent.tools.archive_tools import archive_extract

        result = archive_extract({"path": "file.tar.gz"}, _ctx(tmp_path))
        assert result.status == "blocked"
        assert result.error_code == "archive_extension_not_supported"

    def test_workspace_root_required(self) -> None:
        from magi_agent.tools.archive_tools import archive_extract

        result = archive_extract({"path": "data.zip"}, _ctx_no_workspace())
        assert result.status == "blocked"
        assert result.error_code == "workspace_root_required"

    def test_archive_extract_registered_in_manifest(self) -> None:
        from magi_agent.tools.file_tool_manifests import file_tool_manifests

        names = {m.name for m in file_tool_manifests()}
        assert "ArchiveExtract" in names, f"ArchiveExtract not found; names={names}"

    def test_archive_extract_appears_in_build_cli_adk_tools(self, tmp_path: Path) -> None:
        import os
        from unittest.mock import patch

        with patch.dict(os.environ, {"MAGI_FILE_TOOLS_ENABLED": "true"}):
            from magi_agent.cli.tool_runtime import build_cli_adk_tools

            tools = build_cli_adk_tools(workspace_root=str(tmp_path))
        tool_names = {getattr(t, "name", None) for t in tools}
        assert "ArchiveExtract" in tool_names, f"ArchiveExtract not in tools: {tool_names}"

    def test_entry_size_capped(self, tmp_path: Path) -> None:
        """Reading a very large inner entry is capped at a reasonable limit."""
        large_content = "X" * 300_000
        _make_zip_with_inner_file(tmp_path / "big.zip", "large.txt", large_content)
        from magi_agent.tools.archive_tools import archive_extract

        result = archive_extract({"path": "big.zip", "readEntry": "large.txt"}, _ctx(tmp_path))
        assert result.status == "ok"
        content = result.output["entryContent"]  # type: ignore[index]
        # Should be truncated or capped; either way must be shorter than original
        assert len(content) < len(large_content) or result.output.get("truncated") is True  # type: ignore[index]

    def test_multi_entry_zip(self, tmp_path: Path) -> None:
        """Zip with multiple entries lists all of them."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("a.txt", "content_a")
            zf.writestr("subdir/b.txt", "content_b")
        (tmp_path / "multi.zip").write_bytes(buf.getvalue())
        from magi_agent.tools.archive_tools import archive_extract

        result = archive_extract({"path": "multi.zip"}, _ctx(tmp_path))
        assert result.status == "ok"
        names = {e["name"] for e in result.output["entries"]}  # type: ignore[index]
        assert "a.txt" in names
        assert "subdir/b.txt" in names


# ===========================================================================
# Part 3 — Spreadsheet structure (XLSXInfo + XLSXRead range query)
# ===========================================================================


class TestXlsxInfo:
    """xlsx_info: list sheets, return structure metadata."""

    def test_list_sheets_returns_ok(self, tmp_path: Path) -> None:
        _make_xlsx_two_sheets(tmp_path / "wb.xlsx")
        from magi_agent.tools.spreadsheet_tools import xlsx_info

        result = xlsx_info({"path": "wb.xlsx"}, _ctx(tmp_path))
        assert result.status == "ok", f"{result.status}: {result.error_code}"

    def test_list_sheets_returns_sheet_names(self, tmp_path: Path) -> None:
        _make_xlsx_two_sheets(tmp_path / "wb.xlsx")
        from magi_agent.tools.spreadsheet_tools import xlsx_info

        result = xlsx_info({"path": "wb.xlsx"}, _ctx(tmp_path))
        assert result.status == "ok"
        sheets = result.output["sheets"]  # type: ignore[index]
        assert isinstance(sheets, list)
        sheet_names = [s["name"] for s in sheets]
        assert "Alpha" in sheet_names
        assert "Beta" in sheet_names

    def test_sheet_row_count_in_info(self, tmp_path: Path) -> None:
        _make_xlsx_two_sheets(tmp_path / "wb.xlsx")
        from magi_agent.tools.spreadsheet_tools import xlsx_info

        result = xlsx_info({"path": "wb.xlsx"}, _ctx(tmp_path))
        assert result.status == "ok"
        sheets = {s["name"]: s for s in result.output["sheets"]}  # type: ignore[index]
        assert "rowCount" in sheets["Alpha"]
        assert sheets["Alpha"]["rowCount"] == 3  # header + 2 data rows

    def test_sheet_col_count_in_info(self, tmp_path: Path) -> None:
        _make_xlsx_two_sheets(tmp_path / "wb.xlsx")
        from magi_agent.tools.spreadsheet_tools import xlsx_info

        result = xlsx_info({"path": "wb.xlsx"}, _ctx(tmp_path))
        assert result.status == "ok"
        sheets = {s["name"]: s for s in result.output["sheets"]}  # type: ignore[index]
        assert "columnCount" in sheets["Beta"]
        assert sheets["Beta"]["columnCount"] == 3  # X, Y, Z

    def test_first_row_header_preview_in_info(self, tmp_path: Path) -> None:
        _make_xlsx_two_sheets(tmp_path / "wb.xlsx")
        from magi_agent.tools.spreadsheet_tools import xlsx_info

        result = xlsx_info({"path": "wb.xlsx"}, _ctx(tmp_path))
        assert result.status == "ok"
        sheets = {s["name"]: s for s in result.output["sheets"]}  # type: ignore[index]
        # Alpha sheet has Col1, Col2 as first row
        alpha = sheets["Alpha"]
        header_preview = alpha.get("headerPreview", [])
        assert "Col1" in header_preview

    def test_missing_path_returns_blocked(self, tmp_path: Path) -> None:
        from magi_agent.tools.spreadsheet_tools import xlsx_info

        result = xlsx_info({}, _ctx(tmp_path))
        assert result.status == "blocked"
        assert result.error_code == "path_required"

    def test_path_escape_rejected(self, tmp_path: Path) -> None:
        from magi_agent.tools.spreadsheet_tools import xlsx_info

        result = xlsx_info({"path": "../outside.xlsx"}, _ctx(tmp_path))
        assert result.status == "blocked"
        assert result.error_code == "path_escapes_workspace"

    def test_wrong_extension_rejected(self, tmp_path: Path) -> None:
        (tmp_path / "data.csv").write_text("a,b\n1,2\n", encoding="utf-8")
        from magi_agent.tools.spreadsheet_tools import xlsx_info

        result = xlsx_info({"path": "data.csv"}, _ctx(tmp_path))
        assert result.status == "blocked"
        assert result.error_code == "xlsx_extension_required"

    def test_no_openpyxl_returns_blocked(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_xlsx_two_sheets(tmp_path / "wb.xlsx")
        import sys

        monkeypatch.setitem(sys.modules, "openpyxl", None)  # type: ignore[arg-type]
        from magi_agent.tools.spreadsheet_tools import xlsx_info

        result = xlsx_info({"path": "wb.xlsx"}, _ctx(tmp_path))
        assert result.status == "blocked"
        assert result.error_code == "xlsx_dependency_not_installed"

    def test_workspace_root_required(self) -> None:
        from magi_agent.tools.spreadsheet_tools import xlsx_info

        result = xlsx_info({"path": "wb.xlsx"}, _ctx_no_workspace())
        assert result.status == "blocked"
        assert result.error_code == "workspace_root_required"

    def test_xlsx_info_registered_in_manifest(self) -> None:
        from magi_agent.tools.file_tool_manifests import file_tool_manifests

        names = {m.name for m in file_tool_manifests()}
        assert "XLSXInfo" in names, f"XLSXInfo not found; names={names}"

    def test_xlsx_info_appears_in_build_cli_adk_tools(self, tmp_path: Path) -> None:
        import os
        from unittest.mock import patch

        with patch.dict(os.environ, {"MAGI_FILE_TOOLS_ENABLED": "true"}):
            from magi_agent.cli.tool_runtime import build_cli_adk_tools

            tools = build_cli_adk_tools(workspace_root=str(tmp_path))
        tool_names = {getattr(t, "name", None) for t in tools}
        assert "XLSXInfo" in tool_names, f"XLSXInfo not in tools: {tool_names}"


class TestXlsxReadRange:
    """xlsx_read gains cellRange parameter for sub-range queries."""

    def test_read_specific_sheet_by_name(self, tmp_path: Path) -> None:
        _make_xlsx_two_sheets(tmp_path / "wb.xlsx")
        from magi_agent.tools.spreadsheet_tools import xlsx_read

        result = xlsx_read({"path": "wb.xlsx", "sheetName": "Beta"}, _ctx(tmp_path))
        assert result.status == "ok"
        rows = result.output["rows"]  # type: ignore[index]
        assert rows[0][0] == "X"

    def test_read_cell_range_a1_b2(self, tmp_path: Path) -> None:
        _make_xlsx_two_sheets(tmp_path / "wb.xlsx")
        from magi_agent.tools.spreadsheet_tools import xlsx_read

        result = xlsx_read(
            {"path": "wb.xlsx", "sheetName": "Alpha", "cellRange": "A1:B2"},
            _ctx(tmp_path),
        )
        assert result.status == "ok"
        rows = result.output["rows"]  # type: ignore[index]
        # A1:B2 on Alpha = [["Col1","Col2"],["A1","B1"]]
        assert rows[0] == ["Col1", "Col2"]
        assert rows[1] == ["A1", "B1"]
        assert len(rows) == 2

    def test_read_cell_range_single_column(self, tmp_path: Path) -> None:
        _make_xlsx_two_sheets(tmp_path / "wb.xlsx")
        from magi_agent.tools.spreadsheet_tools import xlsx_read

        result = xlsx_read(
            {"path": "wb.xlsx", "sheetName": "Alpha", "cellRange": "A1:A3"},
            _ctx(tmp_path),
        )
        assert result.status == "ok"
        rows = result.output["rows"]  # type: ignore[index]
        col_values = [r[0] for r in rows]
        assert "Col1" in col_values

    def test_invalid_cell_range_returns_blocked(self, tmp_path: Path) -> None:
        _make_xlsx_two_sheets(tmp_path / "wb.xlsx")
        from magi_agent.tools.spreadsheet_tools import xlsx_read

        result = xlsx_read(
            {"path": "wb.xlsx", "sheetName": "Alpha", "cellRange": "INVALID"},
            _ctx(tmp_path),
        )
        assert result.status == "blocked"
        assert result.error_code == "xlsx_invalid_cell_range"
