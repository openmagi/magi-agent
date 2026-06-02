from __future__ import annotations

import json
from pathlib import Path

import pytest

import openmagi_core_agent.tools.spreadsheet_tools as spreadsheet_module
from openmagi_core_agent.tools.context import ToolContext
from openmagi_core_agent.tools.spreadsheet_tools import (
    csv_read,
    csv_write,
    spreadsheet_preview,
)


def _context(workspace_root: Path) -> ToolContext:
    return ToolContext(
        botId="bot-spreadsheet-test",
        userId="user-spreadsheet-test",
        sessionId="session-spreadsheet-test",
        turnId="turn-spreadsheet-test",
        workspaceRoot=str(workspace_root),
        toolUseId="tool-use-spreadsheet-test",
    )


def _dump(result: object) -> str:
    if hasattr(result, "model_dump"):
        return json.dumps(result.model_dump(by_alias=True, mode="json"), sort_keys=True)
    return json.dumps(result, sort_keys=True)


def _fixture_value(*parts: str) -> str:
    return "".join(parts)


def test_csv_read_returns_rows_with_caps_and_digest_metadata(tmp_path: Path) -> None:
    csv_path = tmp_path / "reports" / "sales.csv"
    csv_path.parent.mkdir()
    csv_path.write_text("name,amount,region\nAda,10,west\nGrace,20,east\n", encoding="utf-8")

    result = csv_read({"path": "reports/sales.csv", "maxRows": 2, "maxCols": 2}, _context(tmp_path))

    assert result.status == "ok"
    assert result.output == {
        "rows": [["name", "amount"], ["Ada", "10"]],
        "rowCount": 2,
        "columnCount": 2,
        "truncated": True,
        "contentDigest": result.output["contentDigest"],  # type: ignore[index]
        "byteCount": csv_path.stat().st_size,
    }
    assert str(tmp_path) not in _dump(result)
    assert result.metadata["toolName"] == "csv_read"
    assert result.metadata["contentDigest"] == result.output["contentDigest"]  # type: ignore[index]


def test_csv_read_blocks_parent_and_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-spreadsheet.csv"
    outside.write_text("secret,value\n", encoding="utf-8")
    (tmp_path / "link.csv").symlink_to(outside)
    (tmp_path / "book.xlsx").write_bytes(b"not-a-real-xlsx")

    parent_result = csv_read({"path": "../outside-spreadsheet.csv"}, _context(tmp_path))
    symlink_result = csv_read({"path": "link.csv"}, _context(tmp_path))
    xlsx_result = csv_read({"path": "book.xlsx"}, _context(tmp_path))
    stripped_absolute_result = csv_read(
        {"path": f" {outside}"},
        _context(tmp_path),
    )

    assert parent_result.status == "blocked"
    assert parent_result.error_code == "path_escapes_workspace"
    assert symlink_result.status == "blocked"
    assert symlink_result.error_code == "path_symlink_denied"
    assert xlsx_result.status == "blocked"
    assert xlsx_result.error_code == "xlsx_unsupported_dependency_approval_required"
    assert stripped_absolute_result.status == "blocked"
    assert stripped_absolute_result.error_code == "path_escapes_workspace"
    assert str(outside) not in _dump(parent_result)
    assert str(outside) not in _dump(symlink_result)


def test_csv_read_redacts_sensitive_cells_and_blocks_sensitive_paths(tmp_path: Path) -> None:
    token = _fixture_value("Author", "ization: Bearer ", "local-fixture-token")
    cookie_pair = _fixture_value("cook", "ie=", "abc123")
    auth_pair = _fixture_value("auth", "=", "abc123")
    authorization_pair = _fixture_value("author", "ization=", "abc123")
    password_value = _fixture_value("hunter", "2")
    api_key_header = _fixture_value("api", "_key")
    api_key_value = _fixture_value("plain", "table", "secret")
    csv_path = tmp_path / "reports" / "sales.csv"
    csv_path.parent.mkdir()
    csv_path.write_text(
        "\n".join(
            [
                f"name,value,{api_key_header}",
                f"header,{token}",
                f"cookie,{cookie_pair}",
                f"auth,{auth_pair}",
                f"authorization,{authorization_pair}",
                f"password,{password_value}",
                f"Ada,10,{api_key_value}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    private_path = tmp_path / "private" / "ledger.csv"
    private_path.parent.mkdir()
    private_path.write_text("name,value\nsecret,1\n", encoding="utf-8")

    result = csv_read({"path": "reports/sales.csv"}, _context(tmp_path))
    private_result = csv_read({"path": "private/ledger.csv"}, _context(tmp_path))

    assert result.status == "ok"
    assert result.output["rows"][1][1] == "[redacted]"  # type: ignore[index]
    assert result.output["rows"][2][1] == "[redacted]"  # type: ignore[index]
    assert result.output["rows"][3][1] == "[redacted]"  # type: ignore[index]
    assert result.output["rows"][4][1] == "[redacted]"  # type: ignore[index]
    assert result.output["rows"][5][1] == "[redacted]"  # type: ignore[index]
    assert result.output["rows"][6][2] == "[redacted]"  # type: ignore[index]
    assert result.metadata["redactionStatus"] == "redacted"
    assert token not in _dump(result)
    assert cookie_pair not in _dump(result)
    assert auth_pair not in _dump(result)
    assert authorization_pair not in _dump(result)
    assert password_value not in _dump(result)
    assert api_key_value not in _dump(result)
    assert private_result.status == "blocked"
    assert private_result.error_code == "secret_path_denied"


def test_csv_read_rejects_malformed_input_and_cell_count_overflow(tmp_path: Path) -> None:
    invalid_utf8 = tmp_path / "invalid.csv"
    invalid_utf8.write_bytes(b"name,value\nbad,\xff\n")
    malformed = tmp_path / "malformed.csv"
    malformed.write_text('name,value\n"unterminated,value\n', encoding="utf-8")
    many_cells = tmp_path / "many.csv"
    many_cells.write_text(
        "\n".join([",".join(["x"] * 200) for _ in range(251)]),
        encoding="utf-8",
    )

    invalid_result = csv_read({"path": "invalid.csv"}, _context(tmp_path))
    malformed_result = csv_read({"path": "malformed.csv"}, _context(tmp_path))
    many_cells_result = csv_read(
        {"path": "many.csv", "maxRows": 10000, "maxCols": 200},
        _context(tmp_path),
    )

    assert invalid_result.status == "error"
    assert invalid_result.error_code == "csv_decode_error"
    assert malformed_result.status == "error"
    assert malformed_result.error_code == "csv_parse_error"
    assert many_cells_result.status == "error"
    assert many_cells_result.error_code == "csv_input_too_large"


def test_csv_read_returns_error_when_file_read_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    csv_path = tmp_path / "report.csv"
    csv_path.write_text("name,value\nAda,10\n", encoding="utf-8")

    def fail_read(_path: Path) -> bytes:
        raise PermissionError("fixture denied")

    monkeypatch.setattr(spreadsheet_module, "_read_bounded_bytes", fail_read)

    result = csv_read({"path": "report.csv"}, _context(tmp_path))

    assert result.status == "error"
    assert result.error_code == "csv_read_failed"
    assert "fixture denied" not in _dump(result)


def test_csv_write_writes_csv_and_returns_digest_only_receipt(tmp_path: Path) -> None:
    result = csv_write(
        {
            "path": "outputs/report.csv",
            "rows": [["name", "amount"], ["Ada", 10], ["Grace", 20]],
        },
        _context(tmp_path),
    )

    written = (tmp_path / "outputs" / "report.csv").read_bytes()
    assert written == b"name,amount\r\nAda,10\r\nGrace,20\r\n"
    assert result.status == "ok"
    assert result.output == {
        "artifactRef": result.output["artifactRef"],  # type: ignore[index]
        "contentDigest": result.output["contentDigest"],  # type: ignore[index]
        "outputDigest": result.output["outputDigest"],  # type: ignore[index]
        "byteCount": len(written),
        "rowCount": 3,
        "columnCount": 2,
    }
    assert result.artifact_refs == (result.output["artifactRef"],)  # type: ignore[index]
    assert result.file_refs == ()
    dumped = _dump(result)
    assert str(tmp_path) not in dumped
    assert "outputs/report.csv" not in dumped
    assert "authorization" not in dumped.lower()
    assert "cookie" not in dumped.lower()
    receipt = result.metadata["localArtifactReceipt"]
    assert receipt == {
        "kind": "local_csv_artifact",
        "artifactRef": result.output["artifactRef"],  # type: ignore[index]
        "contentDigest": result.output["contentDigest"],  # type: ignore[index]
        "outputDigest": result.output["outputDigest"],  # type: ignore[index]
        "byteCount": len(written),
        "rowCount": 3,
        "columnCount": 2,
        "localOnly": True,
        "deliveryClaimed": False,
        "liveAttachmentEnabled": False,
        "redactionStatus": "no_redaction_needed",
    }


def test_csv_write_blocks_non_csv_xlsx_and_invalid_or_oversized_rows(tmp_path: Path) -> None:
    context = _context(tmp_path)
    outside = tmp_path.parent / "outside-write.csv"
    outside.write_text("existing,value\n", encoding="utf-8")
    (tmp_path / "escape-dir").symlink_to(tmp_path.parent)

    txt_result = csv_write({"path": "outputs/report.txt", "rows": [["a"]]}, context)
    xlsx_result = csv_write({"path": "outputs/report.xlsx", "rows": [["a"]]}, context)
    parent_result = csv_write({"path": "../outside-write.csv", "rows": [["a"]]}, context)
    stripped_absolute_result = csv_write({"path": f" {outside}", "rows": [["a"]]}, context)
    nul_path_result = csv_write({"path": "outputs/\x00/report.csv", "rows": [["a"]]}, context)
    drive_path_result = csv_write({"path": "C:\\temp\\report.csv", "rows": [["a"]]}, context)
    symlink_result = csv_write({"path": "escape-dir/report.csv", "rows": [["a"]]}, context)
    invalid_result = csv_write({"path": "outputs/report.csv", "rows": ["not-a-row"]}, context)
    oversized_result = csv_write(
        {"path": "outputs/report.csv", "rows": [["x" * (1024 * 1024 + 1)]]},
        context,
    )

    assert txt_result.status == "blocked"
    assert txt_result.error_code == "csv_extension_required"
    assert xlsx_result.status == "blocked"
    assert xlsx_result.error_code == "xlsx_unsupported_dependency_approval_required"
    assert "dependency approval" in (xlsx_result.error_message or "")
    assert parent_result.status == "blocked"
    assert parent_result.error_code == "path_escapes_workspace"
    assert stripped_absolute_result.status == "blocked"
    assert stripped_absolute_result.error_code == "path_escapes_workspace"
    assert nul_path_result.status == "blocked"
    assert nul_path_result.error_code == "path_invalid"
    assert drive_path_result.status == "blocked"
    assert drive_path_result.error_code == "path_escapes_workspace"
    assert symlink_result.status == "blocked"
    assert symlink_result.error_code == "path_symlink_denied"
    assert invalid_result.status == "error"
    assert invalid_result.error_code == "invalid_rows_shape"
    assert oversized_result.status == "error"
    assert oversized_result.error_code == "csv_input_too_large"
    assert not (tmp_path / "outputs" / "report.csv").exists()
    assert outside.read_text(encoding="utf-8") == "existing,value\n"


def test_csv_write_rejects_oversized_rows_before_rendering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_render(_rows: list[list[str]]) -> bytes:
        raise AssertionError("render should not be called for oversized rows")

    monkeypatch.setattr(spreadsheet_module, "_render_csv", fail_render)

    result = csv_write(
        {"path": "outputs/report.csv", "rows": [["x" * (1024 * 1024 + 1)]]},
        _context(tmp_path),
    )

    assert result.status == "error"
    assert result.error_code == "csv_input_too_large"


def test_csv_write_returns_error_for_parent_file_and_target_directory(tmp_path: Path) -> None:
    parent_file = tmp_path / "parent.csv"
    parent_file.write_text("already,a,file\n", encoding="utf-8")
    target_directory = tmp_path / "outputs" / "report.csv"
    target_directory.mkdir(parents=True)

    parent_result = csv_write(
        {"path": "parent.csv/child.csv", "rows": [["a"]]},
        _context(tmp_path),
    )
    directory_result = csv_write(
        {"path": "outputs/report.csv", "rows": [["a"]]},
        _context(tmp_path),
    )

    assert parent_result.status == "error"
    assert parent_result.error_code == "csv_write_failed"
    assert directory_result.status == "error"
    assert directory_result.error_code == "csv_write_failed"


def test_csv_write_blocks_sensitive_paths_and_keeps_sensitive_cells_out_of_receipt(
    tmp_path: Path,
) -> None:
    token = _fixture_value("s", "k", "-spreadsheet-", "fixture")
    context = _context(tmp_path)

    private_path_result = csv_write(
        {"path": "private/report.csv", "rows": [["name", "value"], ["secret", token]]},
        context,
    )
    output_result = csv_write(
        {"path": "outputs/report.csv", "rows": [["name", "value"], ["secret", token]]},
        context,
    )

    assert private_path_result.status == "blocked"
    assert private_path_result.error_code == "secret_path_denied"
    assert output_result.status == "ok"
    dumped = _dump(output_result)
    assert token not in dumped
    assert "outputs/report.csv" not in dumped
    assert "secret" not in output_result.metadata["localArtifactReceipt"]


def test_spreadsheet_preview_accepts_rows_or_csv_read_result_and_caps_cells(tmp_path: Path) -> None:
    rows_preview = spreadsheet_preview(
        {
            "rows": [
                ["name", "amount", "region"],
                ["Ada", "10", "west"],
                ["Grace", "20", "east"],
            ],
            "maxRows": 2,
            "maxCols": 2,
        },
        _context(tmp_path),
    )
    csv_path = tmp_path / "table.csv"
    csv_path.write_text("name,amount,region\nAda,10,west\nGrace,20,east\n", encoding="utf-8")
    read_result = csv_read({"path": "table.csv"}, _context(tmp_path))
    read_preview = spreadsheet_preview(
        {"csvReadResult": read_result.model_dump(by_alias=True)},
        _context(tmp_path),
    )

    assert rows_preview.status == "ok"
    assert rows_preview.output == {
        "markdown": "| name | amount |\n| --- | --- |\n| Ada | 10 |",
        "rowCount": 2,
        "columnCount": 2,
        "truncated": True,
    }
    assert read_preview.status == "ok"
    assert "| Grace | 20 | east |" in read_preview.output["markdown"]  # type: ignore[index]
    assert str(tmp_path) not in _dump(read_preview)


def test_spreadsheet_preview_redacts_sensitive_cells(tmp_path: Path) -> None:
    token = _fixture_value("github", "_pat_", "spreadsheetfixture")
    cookie_pair = _fixture_value("cook", "ie=", "abc123")
    auth_pair = _fixture_value("auth", "=", "abc123")
    authorization_pair = _fixture_value("author", "ization=", "abc123")
    password_value = _fixture_value("hunter", "2")
    api_key_header = _fixture_value("api", "_key")
    api_key_value = _fixture_value("plain", "table", "secret")

    result = spreadsheet_preview(
        {
            "rows": [
                ["name", "value", api_key_header],
                ["secret", token],
                ["cookie", cookie_pair],
                ["auth", auth_pair],
                ["authorization", authorization_pair],
                ["password", password_value],
                ["Ada", "10", api_key_value],
            ]
        },
        _context(tmp_path),
    )

    assert result.status == "ok"
    assert "[redacted]" in result.output["markdown"]  # type: ignore[index]
    assert token not in _dump(result)
    assert cookie_pair not in _dump(result)
    assert auth_pair not in _dump(result)
    assert authorization_pair not in _dump(result)
    assert password_value not in _dump(result)
    assert api_key_value not in _dump(result)


def test_csv_read_redacts_values_under_sensitive_single_column_header(tmp_path: Path) -> None:
    header = _fixture_value("api", "_key")
    value = _fixture_value("plain", "header", "secret")
    csv_path = tmp_path / "reports" / "public.csv"
    csv_path.parent.mkdir()
    csv_path.write_text(f"{header}\n{value}\n", encoding="utf-8")

    result = csv_read({"path": "reports/public.csv"}, _context(tmp_path))

    assert result.status == "ok"
    assert result.output["rows"][1][0] == "[redacted]"  # type: ignore[index]
    assert value not in _dump(result)


def test_spreadsheet_preview_caps_cell_width_and_markdown_bytes(tmp_path: Path) -> None:
    wide_cell = "|" * 5000

    result = spreadsheet_preview(
        {"rows": [["name", "value"], ["large", wide_cell]], "maxRows": 2, "maxCols": 2},
        _context(tmp_path),
    )

    assert result.status == "ok"
    assert result.output["truncated"] is True  # type: ignore[index]
    assert result.metadata["previewTruncated"] is True
    assert len(result.output["markdown"].encode("utf-8")) <= 4096  # type: ignore[index]


def test_spreadsheet_preview_redaction_metadata_considers_hidden_cells(tmp_path: Path) -> None:
    token = _fixture_value("s", "k", "-hidden-", "fixture")

    result = spreadsheet_preview(
        {"rows": [["name"], ["visible"], [token]], "maxRows": 2, "maxCols": 1},
        _context(tmp_path),
    )

    assert result.status == "ok"
    assert token not in _dump(result)
    assert result.metadata["redactionStatus"] == "redacted"
