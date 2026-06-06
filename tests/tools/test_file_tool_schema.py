"""File tools must expose explicit, informative parameter schemas.

Root cause of the SWE-bench edit failures: FileRead/FileWrite/FileEdit were
registered with the permissive shared schema, so the model received no inner
parameter names and guessed (Claude -> start_line/end_line, Kimi -> filePath),
mismatching the gate5b handler contract (path/old_text/new_text).
"""
from __future__ import annotations

from magi_agent.tools.catalog import core_tool_manifests


def _schema(name: str) -> dict:
    mans = {m.name: m for m in core_tool_manifests()}
    return mans[name].input_schema


def test_file_edit_schema_declares_path_old_new():
    props = _schema("FileEdit").get("properties", {})
    assert set(props) >= {"path", "old_text", "new_text"}
    assert _schema("FileEdit").get("required") == ["path", "old_text", "new_text"]
    # descriptions present so the model knows what each field is
    assert props["old_text"].get("description")


def test_file_write_schema_declares_path_content():
    props = _schema("FileWrite").get("properties", {})
    assert set(props) >= {"path", "content"}
    assert "path" in _schema("FileWrite").get("required", [])


def test_file_read_schema_declares_path():
    props = _schema("FileRead").get("properties", {})
    assert "path" in props
    assert "path" in _schema("FileRead").get("required", [])
