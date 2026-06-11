"""B2 + A1-measurement wiring.

(1) The fast direct web tools (web_search / web_fetch / research_fact) existed
but had ZERO consumers — never registered into the CLI agent, so a fresh
install with BRAVE+FIRECRAWL keys still had no live web capability ("built but
unused", again). They now auto-activate on key presence via the existing
key-gated builder (keyless installs byte-identical: builder returns []).

(2) A1 is now a measurement task: the full local profile defaults research
governance to AUDIT (observe-only, zero behavior change) and audit reports
persist to the durable evidence dir, so default-ON enforce can be justified
with real false-positive data instead of assertion.
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


def test_full_profile_defaults_research_governance_to_audit():
    from magi_agent.runtime.local_defaults import LOCAL_FULL_RUNTIME_ENV_DEFAULTS

    assert LOCAL_FULL_RUNTIME_ENV_DEFAULTS.get("MAGI_RESEARCH_GOVERNANCE_MODE") == "audit"


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
