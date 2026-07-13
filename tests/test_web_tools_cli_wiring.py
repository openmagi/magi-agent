"""B2 + A1-measurement wiring.

(1) The fast direct web tools (web_search / web_fetch / research_fact) existed
but had ZERO consumers — never registered into the CLI agent, so a fresh
install with BRAVE+FIRECRAWL keys still had no live web capability ("built but
unused", again). They now auto-activate on key presence via the existing
key-gated builder (keyless installs byte-identical: builder returns []).

(2) The full local profile defaults research governance to ENFORCE (the
deterministic cited-without-source class, one bounded re-prompt, ~0 FP).
Steps down to "audit"/"off" via the same env var if needed.
"""
from __future__ import annotations

import json


def test_cli_tools_include_web_tools_when_keys_present(tmp_path, monkeypatch):
    from magi_agent.cli.tool_runtime import build_cli_adk_tools

    monkeypatch.setenv("BRAVE_API_KEY", "k1")
    monkeypatch.setenv("FIRECRAWL_API_KEY", "k2")
    tools = build_cli_adk_tools(workspace_root=str(tmp_path))
    names = {getattr(t, "name", "") for t in tools}
    assert {"web_search", "web_fetch", "research_fact"}.issubset(names)


def test_cli_tools_exclude_web_tools_without_keys(tmp_path, monkeypatch):
    from magi_agent.cli.tool_runtime import build_cli_adk_tools

    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    tools = build_cli_adk_tools(workspace_root=str(tmp_path))
    names = {getattr(t, "name", "") for t in tools}
    assert not {"web_search", "web_fetch", "research_fact"} & names


def test_cli_web_tools_are_dispatcher_backed_not_bare_functiontools(tmp_path, monkeypatch):
    """A-2: web tools must NOT be the bare ``FunctionTool(web_search)`` shape.

    A dispatcher-backed tool wraps the registry/dispatch ladder; its underlying
    callable is the generic ``invoke_openmagi_tool`` (renamed to the tool name),
    never the raw ``web_search``/``web_fetch``/``research_fact`` function. RED
    today: the direct-append path attaches the bare functions.
    """
    from magi_agent.cli.tool_runtime import build_cli_adk_tools
    from magi_agent.tools import web_search_tools

    monkeypatch.setenv("BRAVE_API_KEY", "k1")
    monkeypatch.setenv("FIRECRAWL_API_KEY", "k2")
    tools = build_cli_adk_tools(workspace_root=str(tmp_path))
    by_name = {getattr(t, "name", ""): t for t in tools}

    bare_callables = {
        web_search_tools.web_search,
        web_search_tools.web_fetch,
        web_search_tools._research_fact_tool,
    }
    for name in ("web_search", "web_fetch", "research_fact"):
        tool = by_name.get(name)
        assert tool is not None, f"{name} missing from CLI tools"
        func = getattr(tool, "func", None)
        assert func not in bare_callables, (
            f"{name} is a bare FunctionTool that bypasses the dispatcher"
        )


def test_invoking_cli_web_fetch_routes_through_tool_dispatcher(tmp_path, monkeypatch):
    """A-2: invoking the ADK WebFetch tool crosses ToolDispatcher.dispatch."""
    import asyncio

    from magi_agent.cli import tool_runtime
    from magi_agent.tools.dispatcher import ToolDispatcher

    monkeypatch.setenv("BRAVE_API_KEY", "k1")
    monkeypatch.setenv("FIRECRAWL_API_KEY", "k2")

    dispatched: list[str] = []
    original_dispatch = ToolDispatcher.dispatch

    async def _spy_dispatch(self, name, arguments, context, **kwargs):
        dispatched.append(name)
        return await original_dispatch(self, name, arguments, context, **kwargs)

    monkeypatch.setattr(ToolDispatcher, "dispatch", _spy_dispatch)

    tools = tool_runtime.build_cli_adk_tools(workspace_root=str(tmp_path))
    web_fetch_tool = next(t for t in tools if getattr(t, "name", "") == "web_fetch")

    # Block the URL so no real network egress happens; the point is that the call
    # is routed through the dispatcher at all.
    asyncio.run(
        web_fetch_tool.run_async(
            args={"arguments": {"url": "http://169.254.169.254/"}},
            tool_context=object(),
        )
    )
    assert "web_fetch" in dispatched


def test_full_profile_defaults_research_governance_to_enforce():
    from magi_agent.runtime.local_defaults import LOCAL_FULL_RUNTIME_ENV_DEFAULTS

    assert LOCAL_FULL_RUNTIME_ENV_DEFAULTS.get("MAGI_RESEARCH_GOVERNANCE_MODE") == "enforce"


def test_eval_profile_does_not_enable_research_governance():
    from magi_agent.runtime.local_defaults import EVAL_RUNTIME_ENV_DEFAULTS

    assert "MAGI_RESEARCH_GOVERNANCE_MODE" not in EVAL_RUNTIME_ENV_DEFAULTS


def test_audit_report_persists_to_evidence_dir(tmp_path, monkeypatch):
    from magi_agent.research.live_audit import persist_audit_report

    monkeypatch.setenv("MAGI_EVIDENCE_LEDGER_DIR", str(tmp_path / "ev"))
    persist_audit_report({"verdict": "attention", "citedWithoutSource": ["https://x.test/a"]}, session_id="s1")
    persist_audit_report({"verdict": "pass", "citedWithoutSource": []}, session_id="s1")

    lines = (tmp_path / "ev" / "research_audit.jsonl").read_text().splitlines()
    entries = [json.loads(line) for line in lines]
    assert len(entries) == 2
    assert entries[0]["sessionId"] == "s1"
    assert entries[0]["report"]["verdict"] == "attention"


def test_audit_report_persist_off_and_fail_soft(tmp_path, monkeypatch):
    from magi_agent.research.live_audit import persist_audit_report

    monkeypatch.setenv("MAGI_EVIDENCE_LEDGER_DIR", "off")
    monkeypatch.chdir(tmp_path)
    persist_audit_report({"verdict": "pass"}, session_id="s1")
    assert list(tmp_path.iterdir()) == []

    blocker = tmp_path / "f"
    blocker.write_text("x")
    monkeypatch.setenv("MAGI_EVIDENCE_LEDGER_DIR", str(blocker))
    persist_audit_report({"verdict": "pass"}, session_id="s1")  # must not raise


# ---------------------------------------------------------------------------
# Item 07: <web_research> guidance block wiring in build_cli_instruction
# ---------------------------------------------------------------------------


def test_cli_instruction_default_off_has_no_web_research_block(monkeypatch):
    """Keys set, flag unset → prompt byte-surface has no <web_research> block.

    Default-OFF proof for the prompt surface.
    """
    from magi_agent.cli.tool_runtime import build_cli_instruction

    monkeypatch.setenv("BRAVE_API_KEY", "k1")
    monkeypatch.setenv("FIRECRAWL_API_KEY", "k2")
    monkeypatch.delenv("MAGI_RESEARCH_FACT_GUIDANCE_ENABLED", raising=False)

    instruction = build_cli_instruction(session_id="test-web-research-off")
    assert "<web_research>" not in instruction


def test_cli_instruction_flag_on_with_keys_has_web_research_block(monkeypatch):
    """Flag + both keys set → the <web_research> block appears exactly once."""
    from magi_agent.cli.tool_runtime import build_cli_instruction

    monkeypatch.setenv("BRAVE_API_KEY", "k1")
    monkeypatch.setenv("FIRECRAWL_API_KEY", "k2")
    monkeypatch.setenv("MAGI_RESEARCH_FACT_GUIDANCE_ENABLED", "1")

    instruction = build_cli_instruction(session_id="test-web-research-on")
    assert instruction.count("<web_research>") == 1
    assert instruction.count("</web_research>") == 1
    assert "research_fact" in instruction
