"""Tests for .xls (BIFF) spreadsheet read support.

Covers:
- Extension dispatch: .xls routes to xls_read; .xlsx still goes to openpyxl
- Not-installed xlrd returns a clean blocked_result (monkeypatched)
- Row-projection helper produces the same shape for .xls as .xlsx
- file_markdown.convert_file_to_markdown accepts .xls
- QA_SUPPORTED_EXTENSIONS includes .xls
- Regression: .xlsx behavior unchanged
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

from magi_agent.tools.context import ToolContext
from magi_agent.tools.spreadsheet_tools import xlsx_read
from magi_agent.tools.file_markdown import (
    QA_SUPPORTED_EXTENSIONS,
    MarkdownConversion,
    convert_file_to_markdown,
)


def _ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(
        botId="test-bot-xls",
        sessionId="test-session-xls",
        turnId="test-turn-xls",
        workspaceRoot=str(tmp_path),
    )


# ---------------------------------------------------------------------------
# RED tests: xls_read exists as an importable symbol
# ---------------------------------------------------------------------------


def test_xls_read_is_importable() -> None:
    from magi_agent.tools.spreadsheet_tools import xls_read  # noqa: PLC0415

    assert callable(xls_read)


# ---------------------------------------------------------------------------
# Extension dispatch: .xls routes to xls path; .xlsx unchanged
# ---------------------------------------------------------------------------


def test_xlsx_read_still_blocks_non_xlsx_extensions(tmp_path: Path) -> None:
    (tmp_path / "data.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    result = xlsx_read({"path": "data.csv"}, _ctx(tmp_path))
    assert result.status == "blocked"
    assert result.error_code == "xlsx_extension_required"


def test_xls_read_blocks_non_xls_extension(tmp_path: Path) -> None:
    from magi_agent.tools.spreadsheet_tools import xls_read  # noqa: PLC0415

    (tmp_path / "data.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    result = xls_read({"path": "data.csv"}, _ctx(tmp_path))
    assert result.status == "blocked"
    assert result.error_code == "xls_extension_required"


def test_xls_read_requires_path_argument(tmp_path: Path) -> None:
    from magi_agent.tools.spreadsheet_tools import xls_read  # noqa: PLC0415

    result = xls_read({}, _ctx(tmp_path))
    assert result.status == "blocked"
    assert result.error_code == "path_required"


def test_xls_read_blocks_missing_file(tmp_path: Path) -> None:
    from magi_agent.tools.spreadsheet_tools import xls_read  # noqa: PLC0415

    result = xls_read({"path": "missing.xls"}, _ctx(tmp_path))
    # either error or blocked -- must not crash
    assert result.status in ("error", "blocked")


# ---------------------------------------------------------------------------
# Not-installed xlrd graceful path (monkeypatched)
# ---------------------------------------------------------------------------


def test_xls_read_returns_blocked_when_xlrd_not_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from magi_agent.tools.spreadsheet_tools import xls_read  # noqa: PLC0415

    (tmp_path / "book.xls").write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")  # OLE magic

    # Simulate xlrd absent by hiding it from imports
    original_xlrd = sys.modules.get("xlrd")
    sys.modules["xlrd"] = None  # type: ignore[assignment]
    try:
        result = xls_read({"path": "book.xls"}, _ctx(tmp_path))
    finally:
        if original_xlrd is None:
            sys.modules.pop("xlrd", None)
        else:
            sys.modules["xlrd"] = original_xlrd

    assert result.status == "blocked"
    assert result.error_code == "xls_dependency_not_installed"


# ---------------------------------------------------------------------------
# Real xlrd integration (skipped if xlrd not installed)
# ---------------------------------------------------------------------------


def test_xls_read_real_file_returns_same_shape_as_xlsx_read(tmp_path: Path) -> None:
    xlrd = pytest.importorskip("xlrd")
    xlwt = pytest.importorskip("xlwt")

    from magi_agent.tools.spreadsheet_tools import xls_read  # noqa: PLC0415

    # Build a real .xls file
    wb_out = xlwt.Workbook()
    ws_out = wb_out.add_sheet("Sheet1")
    ws_out.write(0, 0, "name")
    ws_out.write(0, 1, "score")
    ws_out.write(1, 0, "Ada")
    ws_out.write(1, 1, 99)
    wb_out.save(str(tmp_path / "book.xls"))

    result = xls_read({"path": "book.xls"}, _ctx(tmp_path))

    assert result.status == "ok"
    output = result.output
    assert isinstance(output, dict)
    # Same keys as xlsx_read
    assert "rows" in output
    assert "rowCount" in output
    assert "columnCount" in output
    assert "truncated" in output
    assert "contentDigest" in output
    assert "byteCount" in output
    rows = output["rows"]
    assert isinstance(rows, list)
    assert rows[0] == ["name", "score"]
    assert rows[1] == ["Ada", "99"]
    assert output["rowCount"] == 2
    assert output["columnCount"] == 2


# ---------------------------------------------------------------------------
# Row projection via fake xlrd (no real xlrd needed)
# ---------------------------------------------------------------------------


class _FakeCell:
    def __init__(self, ctype: int, value: object) -> None:
        self.ctype = ctype  # 0=empty, 1=text, 2=number, 3=date, 4=bool, 5=error, 6=blank
        self.value = value


class _FakeSheet:
    def __init__(self, name: str, rows: list[list[_FakeCell]]) -> None:
        self.name = name
        self.nrows = len(rows)
        self.ncols = max((len(r) for r in rows), default=0)
        self._rows = rows

    def row(self, idx: int) -> list[_FakeCell]:
        return self._rows[idx]


class _FakeBook:
    def __init__(self, sheets: list[_FakeSheet]) -> None:
        self.sheets_list = sheets
        self.nsheets = len(sheets)

    def sheets(self) -> list[_FakeSheet]:
        return self.sheets_list

    def sheet_by_index(self, idx: int) -> _FakeSheet:
        return self.sheets_list[idx]

    def sheet_by_name(self, name: str) -> _FakeSheet:
        for s in self.sheets_list:
            if s.name == name:
                return s
        raise KeyError(name)

    def release_resources(self) -> None:
        pass


def _make_fake_xlrd_module(book: _FakeBook) -> types.ModuleType:
    fake = types.ModuleType("xlrd")

    def open_workbook(path: str, **_kwargs: object) -> _FakeBook:  # noqa: ARG001
        return book

    fake.open_workbook = open_workbook
    # xlrd cell type constants
    fake.XL_CELL_EMPTY = 0
    fake.XL_CELL_TEXT = 1
    fake.XL_CELL_NUMBER = 2
    fake.XL_CELL_DATE = 3
    fake.XL_CELL_BOOLEAN = 4
    fake.XL_CELL_ERROR = 5
    fake.XL_CELL_BLANK = 6
    return fake


def test_xls_read_projection_with_fake_xlrd_produces_correct_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from magi_agent.tools.spreadsheet_tools import xls_read  # noqa: PLC0415

    (tmp_path / "book.xls").write_bytes(b"fake-xls-content")

    sheet = _FakeSheet(
        "Sheet1",
        [
            [_FakeCell(1, "name"), _FakeCell(1, "score")],
            [_FakeCell(1, "Ada"), _FakeCell(2, 99.0)],
            [_FakeCell(1, "Grace"), _FakeCell(2, 42.0)],
        ],
    )
    book = _FakeBook([sheet])
    fake_xlrd = _make_fake_xlrd_module(book)

    monkeypatch.setitem(sys.modules, "xlrd", fake_xlrd)

    result = xls_read({"path": "book.xls"}, _ctx(tmp_path))

    assert result.status == "ok"
    output = result.output
    assert isinstance(output, dict)
    # Must have the same keys as xlsx_read
    for key in ("rows", "rowCount", "columnCount", "truncated", "contentDigest", "byteCount"):
        assert key in output, f"Missing key: {key}"

    rows = output["rows"]
    assert rows[0] == ["name", "score"]
    assert rows[1][0] == "Ada"
    # Whole-number floats strip trailing .0 for clean display
    assert rows[1][1] == "99"
    assert output["rowCount"] == 3
    assert output["columnCount"] == 2
    assert output["truncated"] is False


def test_xls_read_projection_numeric_integer_no_trailing_dot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Integers stored as float (xlrd style) render without trailing .0."""
    from magi_agent.tools.spreadsheet_tools import xls_read  # noqa: PLC0415

    (tmp_path / "nums.xls").write_bytes(b"fake")

    sheet = _FakeSheet(
        "Sheet1",
        [
            [_FakeCell(2, 42.0), _FakeCell(2, 3.14)],
        ],
    )
    book = _FakeBook([sheet])
    fake_xlrd = _make_fake_xlrd_module(book)
    monkeypatch.setitem(sys.modules, "xlrd", fake_xlrd)

    result = xls_read({"path": "nums.xls"}, _ctx(tmp_path))
    assert result.status == "ok"
    rows = result.output["rows"]  # type: ignore[index]
    # 42.0 (whole) -> "42", 3.14 -> "3.14"
    assert rows[0][0] == "42"
    assert rows[0][1] == "3.14"


def test_xls_read_empty_cells_become_empty_string(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from magi_agent.tools.spreadsheet_tools import xls_read  # noqa: PLC0415

    (tmp_path / "empty.xls").write_bytes(b"fake")

    sheet = _FakeSheet(
        "Sheet1",
        [
            [_FakeCell(0, ""), _FakeCell(1, "val")],
        ],
    )
    book = _FakeBook([sheet])
    fake_xlrd = _make_fake_xlrd_module(book)
    monkeypatch.setitem(sys.modules, "xlrd", fake_xlrd)

    result = xls_read({"path": "empty.xls"}, _ctx(tmp_path))
    assert result.status == "ok"
    rows = result.output["rows"]  # type: ignore[index]
    assert rows[0][0] == ""
    assert rows[0][1] == "val"


# ---------------------------------------------------------------------------
# file_markdown routing: .xls dispatches to xls_read
# ---------------------------------------------------------------------------


def test_file_markdown_routes_xls_to_xls_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "data.xls").write_bytes(b"fake-xls")

    sheet = _FakeSheet(
        "Sheet1",
        [
            [_FakeCell(1, "item"), _FakeCell(1, "qty")],
            [_FakeCell(1, "widget"), _FakeCell(2, 5.0)],
        ],
    )
    book = _FakeBook([sheet])
    fake_xlrd = _make_fake_xlrd_module(book)
    monkeypatch.setitem(sys.modules, "xlrd", fake_xlrd)

    conversion = convert_file_to_markdown("data.xls", _ctx(tmp_path))

    assert isinstance(conversion, MarkdownConversion)
    assert conversion.status == "ok"
    assert conversion.source_tool == "xls_read"
    assert "item" in conversion.markdown
    assert "widget" in conversion.markdown


def test_file_markdown_xls_without_xlrd_returns_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "data.xls").write_bytes(b"fake-xls")

    sys.modules["xlrd"] = None  # type: ignore[assignment]
    try:
        conversion = convert_file_to_markdown("data.xls", _ctx(tmp_path))
    finally:
        sys.modules.pop("xlrd", None)

    assert conversion.status == "blocked"
    assert conversion.error_code == "xls_dependency_not_installed"


def test_qa_supported_extensions_includes_xls() -> None:
    assert ".xls" in QA_SUPPORTED_EXTENSIONS


# ---------------------------------------------------------------------------
# Regression: .xlsx behavior unchanged
# ---------------------------------------------------------------------------


def test_xlsx_read_unchanged_for_xlsx_extension(tmp_path: Path) -> None:
    openpyxl = pytest.importorskip("openpyxl")

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["col1", "col2"])
    ws.append(["a", "b"])
    wb.save(tmp_path / "sheet.xlsx")

    result = xlsx_read({"path": "sheet.xlsx"}, _ctx(tmp_path))

    assert result.status == "ok"
    assert result.output["rows"] == [["col1", "col2"], ["a", "b"]]  # type: ignore[index]
    assert result.output["rowCount"] == 2  # type: ignore[index]


def test_file_markdown_xlsx_routing_unchanged(tmp_path: Path) -> None:
    openpyxl = pytest.importorskip("openpyxl")

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["x", "y"])
    ws.append([1, 2])
    wb.save(tmp_path / "sheet.xlsx")

    conversion = convert_file_to_markdown("sheet.xlsx", _ctx(tmp_path))

    assert conversion.status == "ok"
    assert conversion.source_tool == "xlsx_read"
    assert "| x | y |" in conversion.markdown
