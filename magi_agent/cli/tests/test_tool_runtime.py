from __future__ import annotations

import asyncio

from magi_agent.cli.tool_runtime import (
    build_cli_adk_tools,
    build_cli_instruction,
    build_cli_tool_runtime,
)
from magi_agent.adk_bridge.tool_adapter import build_adk_function_tools_for_registry
from magi_agent.evidence.local_tool_collector import LocalToolEvidenceCollector
from magi_agent.harness.general_automation.task_completion import (
    RequiredDeliverableEvidence,
    TaskCompletionVerifier,
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


def test_cli_adk_tools_record_local_tool_receipts_for_engine_collector(tmp_path) -> None:
    class _InvocationContext:
        invocation_id = "turn-local"
        function_call = {"id": "call-read", "name": "FileRead"}

    collector = LocalToolEvidenceCollector()
    (tmp_path / "note.txt").write_text("real content here\n", encoding="utf-8")
    tools = build_cli_adk_tools(
        workspace_root=str(tmp_path),
        session_id="sid-local",
        local_tool_evidence_collector=collector,
    )
    file_read = _find_tool(tools, "FileRead")

    result = asyncio.run(file_read.func({"path": "note.txt"}, _InvocationContext()))

    records = collector.collect_for_turn("turn-local")
    assert result["status"] == "ok"
    assert len(records) == 1
    assert records[0]["schemaVersion"] == "openmagi.localToolEvidenceReceipt.v1"
    assert records[0]["receipts"]["toolExecutionReceipt"]["toolName"] == "FileRead"


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
    runtime = build_cli_tool_runtime(workspace_root=str(tmp_path))
    context = runtime.tool_context_factory(object())
    assert context.workspace_root == str(tmp_path)


def test_cli_tool_runtime_records_ga_dispatch_receipt_for_completion_verifier(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("MAGI_GA_LIVE_ENABLED", "1")
    runtime = build_cli_tool_runtime(workspace_root=str(tmp_path), session_id="sid-ga")
    bash = _find_tool(
        build_adk_function_tools_for_registry(
            runtime.registry,
            runtime.dispatcher,
            mode="act",
            tool_context_factory=runtime.tool_context_factory,
            attach_enabled=True,
            exposed_tool_names=("Bash",),
        ),
        "Bash",
    )

    result = asyncio.run(
        bash.func({"command": f"rm -rf {tmp_path / 'data'}"}, object())
    )

    assert result["status"] == "blocked"
    assert result["metadata"]["generalAutomationReceipt"]["status"] == "blocked"
    assert set(
        result["metadata"]["generalAutomationReceipt"]["authorityFlags"].values()
    ) == {False}
    ledger = runtime.general_automation_receipts.ledger_for_turn(
        session_id="sid-ga",
        turn_id="cli",
    )
    assert ledger is not None
    assert len(ledger.entries) == 1
    assert ledger.entries[0].metadata["generalAutomationReceipt"]["status"] == "blocked"
    assert TaskCompletionVerifier().evaluate(
        ledger,
        RequiredDeliverableEvidence(),
    ).status == "pass"


def test_cli_tool_runtime_infers_write_operation_for_ga_workspace_file_write(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("MAGI_GA_LIVE_ENABLED", "1")
    tools = build_cli_adk_tools(workspace_root=str(tmp_path), session_id="sid-write")
    file_write = _find_tool(tools, "FileWrite")

    result = asyncio.run(
        file_write.func(
            {"path": "notes/out.txt", "content": "blocked until approved\n"},
            object(),
        )
    )

    assert result["status"] == "needs_approval"
    assert result["metadata"]["generalAutomationLiveGate"] is True
    assert result["metadata"]["reason"] == (
        "general_automation_workspace_write_requires_approval"
    )
    assert not (tmp_path / "notes" / "out.txt").exists()


def test_build_cli_instruction_is_real_system_prompt(tmp_path) -> None:
    instruction = build_cli_instruction(session_id="s1", model="claude-sonnet-4-6")
    assert instruction
    # Stable markers emitted by build_system_prompt.
    assert "<output-rules>" in instruction
    assert "<tool-preferences>" in instruction
    assert "<skills>" in instruction
    assert "SkillLoader" in instruction
    assert "superpowers-style workflows" in instruction
