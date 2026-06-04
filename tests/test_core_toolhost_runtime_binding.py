import asyncio

from magi_agent.tools import ToolDispatcher, ToolRegistry, register_core_tool_manifests
from magi_agent.tools.context import ToolContext
from magi_agent.tools.core_toolhost import bind_core_toolhost_handlers


def _context(workspace_root, *, selected: bool = False) -> ToolContext:
    scope = None
    if selected:
        scope = {
            "mode": "selected_full_toolhost",
            "source": "selected_full_toolhost",
        }
    return ToolContext(
        bot_id="bot-test",
        turn_id="turn-test",
        workspace_root=str(workspace_root),
        permission_scope=scope,
    )


def test_core_toolhost_binds_default_file_and_shell_handlers(tmp_path) -> None:
    registry = ToolRegistry()
    register_core_tool_manifests(registry)

    bound = bind_core_toolhost_handlers(registry)

    assert {"FileRead", "FileWrite", "FileEdit", "PatchApply", "Bash"}.issubset(bound)
    assert registry.resolve_registration("FileRead").handler is not None
    assert registry.resolve_registration("PatchApply").handler is not None


def test_core_toolhost_read_executes_without_approval(tmp_path) -> None:
    (tmp_path / "README.md").write_text("hello runtime\n", encoding="utf-8")
    registry = ToolRegistry()
    register_core_tool_manifests(registry)
    bind_core_toolhost_handlers(registry)

    result = asyncio.run(
        ToolDispatcher(registry).dispatch(
            "FileRead",
            {"path": "README.md"},
            _context(tmp_path),
            mode="plan",
        )
    )

    assert result.status == "ok"
    assert result.output is not None
    assert result.output["content"] == "hello runtime\n"
    assert result.metadata["toolName"] == "FileRead"
    assert result.metadata["gate5bFullToolhostReceipt"]["toolName"] == "FileRead"


def test_core_toolhost_write_still_requires_scope_or_approval(tmp_path) -> None:
    registry = ToolRegistry()
    register_core_tool_manifests(registry)
    bind_core_toolhost_handlers(registry)

    result = asyncio.run(
        ToolDispatcher(registry).dispatch(
            "FileWrite",
            {"path": "notes/out.txt", "content": "should not write"},
            _context(tmp_path),
            mode="act",
        )
    )

    assert result.status == "needs_approval"
    assert result.metadata["reason"] == "workspace mutation requires approval"
    assert not (tmp_path / "notes" / "out.txt").exists()


def test_core_toolhost_selected_scope_runs_write_patch_and_bash(tmp_path) -> None:
    registry = ToolRegistry()
    register_core_tool_manifests(registry)
    bind_core_toolhost_handlers(registry)
    dispatcher = ToolDispatcher(registry)
    context = _context(tmp_path, selected=True)

    write = asyncio.run(
        dispatcher.dispatch(
            "FileWrite",
            {"path": "notes/out.txt", "content": "hello\n"},
            context,
            mode="act",
        )
    )
    patch = asyncio.run(
        dispatcher.dispatch(
            "FileRead",
            {"path": "notes/out.txt"},
            context,
            mode="act",
        )
    )
    patch = asyncio.run(
        dispatcher.dispatch(
            "PatchApply",
            {"path": "notes/out.txt", "content": "patched\n"},
            context,
            mode="act",
        )
    )
    bash = asyncio.run(
        dispatcher.dispatch(
            "Bash",
            {"command": "printf ok"},
            context,
            mode="act",
        )
    )

    assert write.status == "ok"
    assert patch.status == "ok"
    assert bash.status == "ok"
    assert (tmp_path / "notes" / "out.txt").read_text(encoding="utf-8") == "patched\n"
    assert bash.output["stdout"] == "ok"
    assert patch.metadata["gate5bFullToolhostReceipt"]["toolName"] == "PatchApply"
    assert patch.coding_mutation_receipt is not None


def test_core_toolhost_selected_scope_preserves_hard_path_denials(tmp_path) -> None:
    registry = ToolRegistry()
    register_core_tool_manifests(registry)
    bind_core_toolhost_handlers(registry)

    result = asyncio.run(
        ToolDispatcher(registry).dispatch(
            "FileWrite",
            {"path": "../escape.txt", "content": "nope"},
            _context(tmp_path, selected=True),
            mode="act",
        )
    )

    assert result.status == "blocked"
    assert result.metadata["reason"] in {
        "path escapes workspace",
        "path_policy_denied",
    }
    assert not (tmp_path.parent / "escape.txt").exists()
