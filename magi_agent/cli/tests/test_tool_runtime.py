from __future__ import annotations

import asyncio

from magi_agent.cli.tool_runtime import (
    build_cli_adk_tools,
    build_cli_instruction,
)


def _find_tool(tools: list[object], name: str) -> object:
    for tool in tools:
        if getattr(tool, "name", None) == name:
            return tool
    raise AssertionError(f"tool not found: {name}")


def test_build_cli_adk_tools_exposes_real_core_tools(tmp_path) -> None:
    tools = build_cli_adk_tools(workspace_root=str(tmp_path))
    names = {getattr(tool, "name", None) for tool in tools}
    # The deliberately-ungated core_toolhost path yields the 9 real tools.
    assert {"FileRead", "FileWrite", "FileEdit", "Glob", "Grep", "Bash"}.issubset(names)


def test_file_read_tool_performs_real_read(tmp_path) -> None:
    # Non-mocked proof: the FileRead tool runs the REAL core toolhost and reads
    # an actual file written into the workspace.
    (tmp_path / "note.txt").write_text("real content here\n", encoding="utf-8")
    tools = build_cli_adk_tools(workspace_root=str(tmp_path))
    file_read = _find_tool(tools, "FileRead")

    # Invoke the underlying ADK FunctionTool callable exactly as ADK does:
    # ``func(arguments, tool_context)``. The adapter returns
    # ``ToolResult.model_dump(by_alias=True)``.
    result = asyncio.run(file_read.func({"path": "note.txt"}, object()))

    assert result["status"] == "ok"
    assert result["output"]["content"] == "real content here\n"
    assert result["metadata"]["toolName"] == "FileRead"


def test_tool_context_factory_carries_workspace_root(tmp_path) -> None:
    # Dispatch a tool and assert the magi ToolContext the toolhost saw carried
    # ``workspace_root == tmp_path``. We prove this through the resolved
    # workspace path embedded in the real toolhost receipt: a read of a file
    # only resolves when the workspace root is correct.
    (tmp_path / "probe.txt").write_text("probe\n", encoding="utf-8")
    tools = build_cli_adk_tools(workspace_root=str(tmp_path))
    file_read = _find_tool(tools, "FileRead")

    result = asyncio.run(file_read.func({"path": "probe.txt"}, object()))

    assert result["status"] == "ok"
    assert result["output"]["content"] == "probe\n"


def test_tool_error_is_structured_not_raised(tmp_path) -> None:
    # A failing tool call (missing file) must return a structured non-ok result
    # through the ADK adapter, not raise — so the model sees an actionable error
    # rather than the turn crashing.
    tools = build_cli_adk_tools(workspace_root=str(tmp_path))
    file_read = _find_tool(tools, "FileRead")

    result = asyncio.run(file_read.func({"path": "does-not-exist.txt"}, object()))

    assert isinstance(result, dict)
    assert result["status"] != "ok"


def test_tool_context_factory_returns_workspace_root_directly(tmp_path) -> None:
    from magi_agent.cli.tool_runtime import build_cli_tool_runtime

    runtime = build_cli_tool_runtime(workspace_root=str(tmp_path))
    context = runtime.tool_context_factory(object())
    assert context.workspace_root == str(tmp_path)


def test_build_cli_instruction_is_real_system_prompt(tmp_path) -> None:
    instruction = build_cli_instruction(session_id="s1", model="claude-sonnet-4-6")
    assert instruction
    # Stable markers emitted by build_system_prompt.
    assert "<output-rules>" in instruction
    assert "<tool-preferences>" in instruction
    assert "<skills>" in instruction
    assert "SkillLoader" in instruction
    assert "superpowers-style workflows" in instruction
