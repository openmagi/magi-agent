from __future__ import annotations

import pytest

from magi_agent.runtime.session_identity import MemoryMode
from magi_agent.tools.context import ToolContext
from magi_agent.tools.local_readonly import LocalReadOnlyToolHost


def _context(workspace_root, *, memory_mode: MemoryMode) -> ToolContext:
    return ToolContext(
        bot_id="bot-test",
        turn_id="turn-test",
        workspace_root=str(workspace_root),
        memory_mode=memory_mode,
    )


def test_file_read_protected_blocked_under_incognito(tmp_path) -> None:
    (tmp_path / "MEMORY.md").write_text("secret\n", encoding="utf-8")
    host = LocalReadOnlyToolHost()
    result = host.execute_tool(
        tool_name="FileRead",
        arguments={"path": "MEMORY.md"},
        context=_context(tmp_path, memory_mode=MemoryMode.INCOGNITO),
    )
    assert result.status == "blocked"
    assert result.error_code == "memory_mode_incognito"
    assert "memory mode blocks access to MEMORY.md" in result.error_message


def test_file_read_protected_blocked_under_read_only(tmp_path) -> None:
    (tmp_path / "MEMORY.md").write_text("secret\n", encoding="utf-8")
    host = LocalReadOnlyToolHost()
    result = host.execute_tool(
        tool_name="FileRead",
        arguments={"path": "MEMORY.md"},
        context=_context(tmp_path, memory_mode=MemoryMode.READ_ONLY),
    )
    assert result.status == "blocked"
    assert result.error_code == "memory_mode_blocked"
    assert "memory mode blocks access to MEMORY.md" in result.error_message


def test_file_read_protected_allowed_under_normal(tmp_path) -> None:
    (tmp_path / "MEMORY.md").write_text("hello\n", encoding="utf-8")
    host = LocalReadOnlyToolHost()
    result = host.execute_tool(
        tool_name="FileRead",
        arguments={"path": "MEMORY.md"},
        context=_context(tmp_path, memory_mode=MemoryMode.NORMAL),
    )
    assert result.status == "ok"
    assert "hello" in result.output["content"]


def test_grep_protected_path_blocked_under_incognito(tmp_path) -> None:
    (tmp_path / "memory").mkdir()
    (tmp_path / "memory" / "notes.md").write_text("token here\n", encoding="utf-8")
    host = LocalReadOnlyToolHost()
    result = host.execute_tool(
        tool_name="Grep",
        arguments={"pattern": "token", "path": "memory/notes.md"},
        context=_context(tmp_path, memory_mode=MemoryMode.INCOGNITO),
    )
    assert result.status == "blocked"
    assert result.error_code == "memory_mode_incognito"


@pytest.mark.parametrize("memory_mode", [MemoryMode.INCOGNITO, MemoryMode.READ_ONLY])
def test_broad_grep_does_not_return_protected_memory_under_non_normal_mode(
    tmp_path, memory_mode: MemoryMode
) -> None:
    (tmp_path / "MEMORY.md").write_text("needle protected top\n", encoding="utf-8")
    (tmp_path / "memory").mkdir()
    (tmp_path / "memory" / "daily.md").write_text(
        "needle protected dir\n", encoding="utf-8"
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "note.txt").write_text("needle public\n", encoding="utf-8")
    host = LocalReadOnlyToolHost()

    result = host.execute_tool(
        tool_name="Grep",
        arguments={"pattern": "needle"},
        context=_context(tmp_path, memory_mode=memory_mode),
    )

    assert result.status == "ok"
    paths = {match["path"] for match in result.output["matches"]}
    snippets = "\n".join(match["snippet"] for match in result.output["matches"])
    assert "src/note.txt" in paths
    assert "MEMORY.md" not in paths
    assert "memory/daily.md" not in paths
    assert "protected top" not in snippets
    assert "protected dir" not in snippets


@pytest.mark.parametrize("memory_mode", [MemoryMode.INCOGNITO, MemoryMode.READ_ONLY])
def test_broad_glob_does_not_return_protected_memory_under_non_normal_mode(
    tmp_path, memory_mode: MemoryMode
) -> None:
    (tmp_path / "MEMORY.md").write_text("secret\n", encoding="utf-8")
    (tmp_path / "memory").mkdir()
    (tmp_path / "memory" / "daily.md").write_text("secret\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "note.txt").write_text("public\n", encoding="utf-8")
    host = LocalReadOnlyToolHost()

    result = host.execute_tool(
        tool_name="Glob",
        arguments={"pattern": "**/*"},
        context=_context(tmp_path, memory_mode=memory_mode),
    )

    assert result.status == "ok"
    paths = {match["path"] for match in result.output["matches"]}
    assert "src/note.txt" in paths
    assert "MEMORY.md" not in paths
    assert "memory/daily.md" not in paths


def test_non_protected_file_read_allowed_under_incognito(tmp_path) -> None:
    (tmp_path / "app.py").write_text("print('ok')\n", encoding="utf-8")
    host = LocalReadOnlyToolHost()
    result = host.execute_tool(
        tool_name="FileRead",
        arguments={"path": "app.py"},
        context=_context(tmp_path, memory_mode=MemoryMode.INCOGNITO),
    )
    assert result.status == "ok"
