from __future__ import annotations

import asyncio

from magi_agent.cli.tool_runtime import (
    build_cli_adk_tools,
    build_cli_instruction,
    build_cli_tool_runtime,
    build_tool_advertisement_block,
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


def test_build_cli_adk_tools_registers_browser_task_by_default(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.delenv("MAGI_BROWSER_TOOL_ENABLED", raising=False)
    monkeypatch.delenv("MAGI_BROWSER_TOOL_KILL_SWITCH", raising=False)
    monkeypatch.delenv("MAGI_RUNTIME_PROFILE", raising=False)

    tools = build_cli_adk_tools(workspace_root=str(tmp_path))

    assert "BrowserTask" in {getattr(tool, "name", None) for tool in tools}


def test_build_cli_adk_tools_respects_browser_kill_switch(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.delenv("MAGI_BROWSER_TOOL_ENABLED", raising=False)
    monkeypatch.setenv("MAGI_BROWSER_TOOL_KILL_SWITCH", "1")

    tools = build_cli_adk_tools(workspace_root=str(tmp_path))

    assert "BrowserTask" not in {getattr(tool, "name", None) for tool in tools}


def test_tool_advertisement_lists_direct_web_tools_when_provider_keys_present(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("BRAVE_API_KEY", "brave-test")
    monkeypatch.setenv("FIRECRAWL_API_KEY", "firecrawl-test")

    block = build_tool_advertisement_block(workspace_root=str(tmp_path))

    assert "web_search [net]" in block
    assert "web_fetch [net]" in block
    assert "research_fact [net]" in block


def test_file_read_tool_performs_real_read(tmp_path, monkeypatch) -> None:
    # Non-mocked proof: the FileRead tool runs the REAL core toolhost and reads
    # an actual file written into the workspace. Pin read-quality OFF: the
    # raw-content contract is what this test proves (the env-wired binder
    # otherwise enables line numbering in the full profile).
    monkeypatch.setenv("MAGI_READ_QUALITY_ENABLED", "0")
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


def test_tool_context_factory_carries_workspace_root(tmp_path, monkeypatch) -> None:
    # Dispatch a tool and assert the magi ToolContext the toolhost saw carried
    # ``workspace_root == tmp_path``. We prove this through the resolved
    # workspace path embedded in the real toolhost receipt: a read of a file
    # only resolves when the workspace root is correct.
    monkeypatch.setenv("MAGI_READ_QUALITY_ENABLED", "0")
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
    assert "<coding-discipline>" in instruction
    assert "<skills>" in instruction
    assert "SkillLoader" in instruction
    assert "superpowers-style workflows" in instruction


# ---------------------------------------------------------------------------
# Output-format-adherence guidance block — default-OFF general capability.
# Gated by MAGI_FORMAT_ADHERENCE_ENABLED. When off, prompt assembly must NOT
# contain the <output_format_adherence> marker; when on, the block (units/scale,
# rounding precision, canonical name/format, no-unrequested-units clauses) is
# appended. This is a GENERAL capability — no GAIA-specific text lives here.
# ---------------------------------------------------------------------------


def test_output_format_adherence_block_disabled_by_default() -> None:
    from magi_agent.cli.tool_runtime import output_format_adherence_block

    assert output_format_adherence_block({}) == ""
    assert output_format_adherence_block({"MAGI_FORMAT_ADHERENCE_ENABLED": "0"}) == ""


def test_output_format_adherence_block_enabled_has_clauses() -> None:
    from magi_agent.cli.tool_runtime import output_format_adherence_block

    text = output_format_adherence_block({"MAGI_FORMAT_ADHERENCE_ENABLED": "1"})
    assert "<output_format_adherence>" in text
    lowered = text.lower()
    # units / scale clause
    assert "unit" in lowered and "scale" in lowered
    # rounding precision clause
    assert "round" in lowered
    # canonical name / format clause
    assert "name" in lowered and "format" in lowered
    # no-unrequested-units clause
    assert "do not add" in lowered


def test_build_cli_instruction_omits_format_block_when_flag_off() -> None:
    # Default env (flag unset) -> marker must be ABSENT. Asserting absence of the
    # substring marker (not full-string equality) because build_system_prompt
    # reads environment-dependent identity/memory snapshots.
    instruction = build_cli_instruction(session_id="fa-off", model="claude-sonnet-4-6")
    assert "<output_format_adherence>" not in instruction


def test_build_cli_instruction_includes_format_block_when_flag_on(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_FORMAT_ADHERENCE_ENABLED", "1")
    instruction = build_cli_instruction(session_id="fa-on", model="claude-sonnet-4-6")
    assert "<output_format_adherence>" in instruction
    lowered = instruction.lower()
    assert "unit" in lowered and "scale" in lowered
    assert "round" in lowered
    assert "do not add" in lowered


# ---------------------------------------------------------------------------
# Recipe-routing listing block — default-OFF (MAGI_RECIPE_ROUTING_LLM_ENABLED).
# When off, prompt assembly must NOT contain the listing header marker; when on,
# the cross-family recipe listing (with the select_recipe call) is appended.
# ---------------------------------------------------------------------------


def test_recipe_listing_absent_when_flag_off_present_when_on(monkeypatch) -> None:
    monkeypatch.delenv("MAGI_RECIPE_ROUTING_LLM_ENABLED", raising=False)
    off = build_cli_instruction(session_id="rr-off", model="claude-sonnet-4-6")
    assert "Available recipes (load on demand)" not in off
    assert "select_recipe" not in off

    monkeypatch.setenv("MAGI_RECIPE_ROUTING_LLM_ENABLED", "1")
    on = build_cli_instruction(session_id="rr-on", model="claude-sonnet-4-6")
    assert "Available recipes (load on demand)" in on
    assert "select_recipe" in on


# ---------------------------------------------------------------------------
# select_recipe tool registration at the CLI tool-runtime seam — default-OFF
# (MAGI_RECIPE_ROUTING_LLM_ENABLED). Flag OFF → the tool is NOT in the runtime
# registry (byte-identical advertised tool set); flag ON → it is registered,
# enabled, and dispatchable.
# ---------------------------------------------------------------------------


def test_cli_tool_runtime_omits_select_recipe_when_flag_off(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("MAGI_RECIPE_ROUTING_LLM_ENABLED", raising=False)
    runtime = build_cli_tool_runtime(workspace_root=str(tmp_path))
    assert runtime.registry.resolve_registration("select_recipe") is None
    assert runtime.registry.is_enabled("select_recipe") is False


def test_cli_tool_runtime_registers_select_recipe_when_flag_on(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MAGI_RECIPE_ROUTING_LLM_ENABLED", "1")
    runtime = build_cli_tool_runtime(workspace_root=str(tmp_path))
    registration = runtime.registry.resolve_registration("select_recipe")
    assert registration is not None
    assert registration.handler is not None
    assert runtime.registry.is_enabled("select_recipe") is True


def test_cli_select_recipe_tool_dispatches_to_handler(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MAGI_RECIPE_ROUTING_LLM_ENABLED", "1")
    tools = build_cli_adk_tools(workspace_root=str(tmp_path), session_id="sid-recipe")
    select = _find_tool(tools, "select_recipe")

    # openmagi.dev-coding is a first-party routable (non-hard) pack.
    result = asyncio.run(select.func({"pack_id": "openmagi.dev-coding"}, object()))

    assert result["status"] == "ok"
    assert result["metadata"]["toolName"] == "select_recipe"
