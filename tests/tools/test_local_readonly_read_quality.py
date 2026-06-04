from pathlib import Path

from magi_agent.tools.context import ToolContext
from magi_agent.tools.local_readonly import LocalReadOnlyToolHost


def _context(workspace_root: Path) -> ToolContext:
    return ToolContext(
        botId="bot-rq",
        userId="user-rq",
        sessionId="session-rq",
        sessionKey="ctx-rq",
        turnId="turn-rq",
        workspaceRoot=str(workspace_root),
    )


def _read(host, workspace_root, path, **args):
    return host.execute_tool(
        tool_name="FileRead",
        arguments={"path": path, **args},
        context=_context(workspace_root),
    )


def test_flag_on_numbers_lines(tmp_path):
    (tmp_path / "a.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
    host = LocalReadOnlyToolHost(read_quality_enabled=True)
    result = _read(host, tmp_path, "a.txt")
    assert result.status == "ok"
    assert result.output["content"].startswith("1: one")
    assert "lineNumberGuidance" in result.output


def test_flag_off_original_behavior(tmp_path):
    (tmp_path / "a.txt").write_text("one\ntwo\n", encoding="utf-8")
    host = LocalReadOnlyToolHost(read_quality_enabled=False)
    result = _read(host, tmp_path, "a.txt")
    assert result.status == "ok"
    assert result.output["content"] == "one\ntwo\n"
    assert "lineNumberGuidance" not in result.output


def test_binary_file_message(tmp_path):
    (tmp_path / "b.bin").write_bytes(b"\x00\x01\x02data\x00")
    host = LocalReadOnlyToolHost(read_quality_enabled=True)
    result = _read(host, tmp_path, "b.bin")
    assert result.status == "ok"
    assert result.output["binary"] is True
    assert "Cannot read binary file" in result.output["content"]


def test_missing_file_did_you_mean(tmp_path):
    (tmp_path / "readme.txt").write_text("hi\n", encoding="utf-8")
    host = LocalReadOnlyToolHost(read_quality_enabled=True)
    result = _read(host, tmp_path, "redme.txt")
    assert result.status == "blocked"
    assert result.error_code == "path_not_found"
    assert "readme.txt" in result.error_message
    assert "readme.txt" in result.metadata["suggestions"]


def test_missing_file_does_not_leak_secret(tmp_path):
    (tmp_path / ".env").write_text("SECRET=1\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("hi\n", encoding="utf-8")
    host = LocalReadOnlyToolHost(read_quality_enabled=True)
    result = _read(host, tmp_path, "note.txt")
    assert ".env" not in result.metadata.get("suggestions", [])


def test_redaction_before_numbering(tmp_path):
    (tmp_path / "c.txt").write_text(
        "first\ntoken=sk-supersecretkey1234567890\nlast\n", encoding="utf-8"
    )
    host = LocalReadOnlyToolHost(read_quality_enabled=True)
    result = _read(host, tmp_path, "c.txt")
    content = result.output["content"]
    assert "sk-supersecretkey1234567890" not in content
    assert "[redacted]" in content
    assert content.startswith("1: first")


def test_offset_paging(tmp_path):
    body = "\n".join(f"l{i}" for i in range(1, 11)) + "\n"
    (tmp_path / "d.txt").write_text(body, encoding="utf-8")
    host = LocalReadOnlyToolHost(read_quality_enabled=True)
    result = _read(host, tmp_path, "d.txt", offset=4)
    content = result.output["content"]
    assert "4: l4" in content
    assert "1: l1" not in content
