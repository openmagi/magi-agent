from __future__ import annotations

import asyncio

from magi_agent.runtime.session_identity import MemoryMode
from magi_agent.tools import ToolDispatcher, ToolRegistry, register_core_tool_manifests
from magi_agent.tools.context import ToolContext
from magi_agent.tools.core_toolhost import bind_core_toolhost_handlers


def _context(
    workspace_root,
    *,
    memory_mode: MemoryMode = MemoryMode.NORMAL,
) -> ToolContext:
    return ToolContext(
        bot_id="bot-test",
        turn_id="turn-test",
        workspace_root=str(workspace_root),
        memory_mode=memory_mode,
        permission_scope={
            "mode": "selected_full_toolhost",
            "source": "selected_full_toolhost",
        },
    )


def _dispatch(registry, tool_name, arguments, context):
    return asyncio.run(
        ToolDispatcher(registry).dispatch(tool_name, arguments, context, mode="act")
    )


def _registry():
    registry = ToolRegistry()
    register_core_tool_manifests(registry)
    bind_core_toolhost_handlers(registry)
    return registry


def test_filewrite_protected_blocked_under_read_only(tmp_path) -> None:
    registry = _registry()
    result = _dispatch(
        registry,
        "FileWrite",
        {"path": "MEMORY.md", "content": "nope"},
        _context(tmp_path, memory_mode=MemoryMode.READ_ONLY),
    )
    assert result.status == "blocked"
    assert result.error_code == "memory_mode_blocked"
    assert result.metadata["reason"] == "memory_mode_blocked"
    assert "memory mode blocks access to MEMORY.md" in result.error_message
    assert not (tmp_path / "MEMORY.md").exists()


def test_filewrite_memory_dir_blocked_under_incognito(tmp_path) -> None:
    # The pre-existing dispatcher seal already blocks ``memory/`` writes with its
    # own ``protected memory path`` reason BEFORE the handler-level memory-mode
    # guard runs, so here we only assert the call is blocked (the guard remains
    # the responsible layer for the top-level protected files the seal misses —
    # see test_filewrite_top_level_protected_blocked).
    registry = _registry()
    result = _dispatch(
        registry,
        "FileWrite",
        {"path": "memory/note.md", "content": "nope"},
        _context(tmp_path, memory_mode=MemoryMode.INCOGNITO),
    )
    assert result.status == "blocked"
    assert not (tmp_path / "memory" / "note.md").exists()


def test_filewrite_top_level_protected_blocked(tmp_path) -> None:
    # Top-level MEMORY.md/WORKING.md/etc are NOT pre-sealed by the dispatcher, so
    # the handler-level memory-mode guard is the responsible blocking layer.
    registry = _registry()
    for protected in ("MEMORY.md", "SCRATCHPAD.md", "WORKING.md", "TASK-QUEUE.md"):
        result = _dispatch(
            registry,
            "FileWrite",
            {"path": protected, "content": "nope"},
            _context(tmp_path, memory_mode=MemoryMode.INCOGNITO),
        )
        assert result.status == "blocked", protected
        assert result.error_code == "memory_mode_blocked", protected
        assert not (tmp_path / protected).exists()


def test_fileread_top_level_protected_blocked_under_incognito(tmp_path) -> None:
    (tmp_path / "MEMORY.md").write_text("secret recall\n", encoding="utf-8")
    registry = _registry()
    result = _dispatch(
        registry,
        "FileRead",
        {"path": "MEMORY.md"},
        _context(tmp_path, memory_mode=MemoryMode.INCOGNITO),
    )
    assert result.status == "blocked"
    assert result.error_code == "memory_mode_blocked"
    assert "memory mode blocks access to MEMORY.md" in result.error_message


def test_fileread_top_level_protected_blocked_under_read_only(tmp_path) -> None:
    (tmp_path / "MEMORY.md").write_text("secret recall\n", encoding="utf-8")
    registry = _registry()
    result = _dispatch(
        registry,
        "FileRead",
        {"path": "MEMORY.md"},
        _context(tmp_path, memory_mode=MemoryMode.READ_ONLY),
    )
    assert result.status == "blocked"
    assert result.error_code == "memory_mode_blocked"
    assert "memory mode blocks access to MEMORY.md" in result.error_message


def test_broad_grep_blocked_under_non_normal_memory_mode(tmp_path) -> None:
    (tmp_path / "MEMORY.md").write_text("needle protected\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "note.txt").write_text("needle public\n", encoding="utf-8")
    for mode in (MemoryMode.INCOGNITO, MemoryMode.READ_ONLY):
        registry = _registry()
        result = _dispatch(
            registry,
            "Grep",
            {"pattern": "needle"},
            _context(tmp_path, memory_mode=mode),
        )
        assert result.status == "blocked", mode
        assert result.error_code == "memory_mode_blocked", mode


def test_broad_glob_does_not_return_protected_memory_under_non_normal_mode(
    tmp_path,
) -> None:
    (tmp_path / "MEMORY.md").write_text("secret\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "note.txt").write_text("public\n", encoding="utf-8")
    for mode in (MemoryMode.INCOGNITO, MemoryMode.READ_ONLY):
        registry = _registry()
        result = _dispatch(
            registry,
            "Glob",
            {"pattern": "**/*"},
            _context(tmp_path, memory_mode=mode),
        )
        assert result.status == "ok", mode
        assert result.output is not None
        paths = set(result.output["matches"])
        assert "src/note.txt" in paths
        assert "MEMORY.md" not in paths


def test_fileedit_protected_blocked_under_read_only(tmp_path) -> None:
    registry = _registry()
    result = _dispatch(
        registry,
        "FileEdit",
        {"path": "SCRATCHPAD.md", "old_text": "a", "new_text": "b"},
        _context(tmp_path, memory_mode=MemoryMode.READ_ONLY),
    )
    assert result.status == "blocked"
    assert result.error_code == "memory_mode_blocked"


def test_patchapply_protected_blocked_under_read_only(tmp_path) -> None:
    registry = _registry()
    result = _dispatch(
        registry,
        "PatchApply",
        {"path": "WORKING.md", "content": "nope"},
        _context(tmp_path, memory_mode=MemoryMode.READ_ONLY),
    )
    assert result.status == "blocked"
    assert result.error_code == "memory_mode_blocked"


def test_bash_write_to_protected_blocked_under_read_only(tmp_path) -> None:
    # A mutating binary against a top-level protected file reaches the handler
    # guard (the redirection form ``>> MEMORY.md`` is intercepted earlier by the
    # pre-existing complex-shell-approval seal).
    registry = _registry()
    result = _dispatch(
        registry,
        "Bash",
        {"command": "rm MEMORY.md"},
        _context(tmp_path, memory_mode=MemoryMode.READ_ONLY),
    )
    assert result.status == "blocked"
    assert result.error_code == "memory_mode_blocked"


def test_bash_read_of_protected_blocked_under_incognito(tmp_path) -> None:
    registry = _registry()
    result = _dispatch(
        registry,
        "Bash",
        {"command": "cat MEMORY.md"},
        _context(tmp_path, memory_mode=MemoryMode.INCOGNITO),
    )
    assert result.status == "blocked"
    assert result.error_code == "memory_mode_blocked"


def test_bash_read_of_protected_allowed_under_read_only(tmp_path) -> None:
    # read_only blocks writes only — a plain read command is NOT a write and is
    # not blocked by the memory-mode guard (it reaches the host).
    (tmp_path / "MEMORY.md").write_text("hi\n", encoding="utf-8")
    registry = _registry()
    result = _dispatch(
        registry,
        "Bash",
        {"command": "cat MEMORY.md"},
        _context(tmp_path, memory_mode=MemoryMode.READ_ONLY),
    )
    assert result.error_code != "memory_mode_blocked"


def test_normal_mode_reaches_host_and_writes_protected(tmp_path) -> None:
    registry = _registry()
    result = _dispatch(
        registry,
        "FileWrite",
        {"path": "MEMORY.md", "content": "ok\n"},
        _context(tmp_path, memory_mode=MemoryMode.NORMAL),
    )
    assert result.status == "ok"
    assert (tmp_path / "MEMORY.md").read_text(encoding="utf-8") == "ok\n"


def test_non_protected_write_allowed_under_read_only(tmp_path) -> None:
    registry = _registry()
    result = _dispatch(
        registry,
        "FileWrite",
        {"path": "notes/out.txt", "content": "ok\n"},
        _context(tmp_path, memory_mode=MemoryMode.READ_ONLY),
    )
    assert result.status == "ok"
    assert (tmp_path / "notes" / "out.txt").read_text(encoding="utf-8") == "ok\n"
