"""Research governance must exist in the LIVE loop — audit-first.

Until now the research governance machinery (claim graph / citation audit /
final projection gate) ran only inside the fixture-sealed harness; the live
CLI loop imported nothing from ``magi_agent.research``. This wires a
deterministic, observe-only citation audit into the live headless turn:
default OFF, ``MAGI_RESEARCH_GOVERNANCE_MODE=audit`` enables observation, and
NOTHING ever blocks (enforce is a future, measured step — GAIA showed blind
enforce over-corrects).
"""
from __future__ import annotations

import asyncio
import io
import json

from magi_agent.cli.contracts import EngineResult, Terminal
from magi_agent.runtime.events import RuntimeEvent


# ---------------------------------------------------------------------------
# Mode parser
# ---------------------------------------------------------------------------


def test_research_governance_mode_default_off():
    from magi_agent.research.live_audit import research_governance_mode

    assert research_governance_mode({}) == "off"
    assert research_governance_mode({"MAGI_RESEARCH_GOVERNANCE_MODE": ""}) == "off"
    # enforce is NOT accepted yet — audit-first; unknown values fall to off.
    assert research_governance_mode({"MAGI_RESEARCH_GOVERNANCE_MODE": "enforce"}) == "off"
    assert research_governance_mode({"MAGI_RESEARCH_GOVERNANCE_MODE": "banana"}) == "off"


def test_research_governance_mode_audit():
    from magi_agent.research.live_audit import research_governance_mode

    assert research_governance_mode({"MAGI_RESEARCH_GOVERNANCE_MODE": "audit"}) == "audit"
    assert research_governance_mode({"MAGI_RESEARCH_GOVERNANCE_MODE": " AUDIT "}) == "audit"


# ---------------------------------------------------------------------------
# Deterministic audit core
# ---------------------------------------------------------------------------


def _tool_start(tool_id: str, name: str) -> dict:
    return {"type": "tool_start", "id": tool_id, "name": name}


def _tool_end(tool_id: str, preview: str) -> dict:
    return {"type": "tool_end", "id": tool_id, "status": "ok", "output_preview": preview}


def test_audit_flags_cited_url_without_source():
    from magi_agent.research.live_audit import ResearchLiveAudit

    audit = ResearchLiveAudit()
    audit.observe_event("tool", _tool_start("t1", "web_fetch"))
    audit.observe_event("tool", _tool_end("t1", "content from https://example.com/a page"))

    report = audit.report("Per https://example.com/a and https://other.org/b, X is true.")

    assert report["type"] == "research_governance_audit"
    assert report["mode"] == "audit"
    assert "https://other.org/b" in report["citedWithoutSource"]
    assert "https://example.com/a" not in report["citedWithoutSource"]
    assert report["verdict"] == "attention"


def test_audit_passes_when_citations_covered():
    from magi_agent.research.live_audit import ResearchLiveAudit

    audit = ResearchLiveAudit()
    audit.observe_event("tool", _tool_start("t1", "research_fact"))
    audit.observe_event(
        "tool", _tool_end("t1", "[1] https://example.com/a\nsnippet\n[2] https://example.com/b\nsnippet")
    )

    report = audit.report("Answer cites https://example.com/a only.")

    assert report["citedWithoutSource"] == []
    assert "https://example.com/b" in report["sourcesUncited"]
    assert report["verdict"] == "pass"


def test_audit_ignores_non_web_tools_and_no_citations():
    from magi_agent.research.live_audit import ResearchLiveAudit

    audit = ResearchLiveAudit()
    audit.observe_event("tool", _tool_start("t1", "FileRead"))
    audit.observe_event("tool", _tool_end("t1", "see https://not-a-source.example/x"))

    report = audit.report("No URLs cited here.")

    assert report["sourceUrlCount"] == 0
    assert report["citedWithoutSource"] == []
    assert report["verdict"] == "pass"


# ---------------------------------------------------------------------------
# Live wiring (headless, observe-only)
# ---------------------------------------------------------------------------


class _WebTurnDriver:
    """Fake engine driver: one web_fetch tool round + an answer citing URLs."""

    def run_turn_stream(self, _session, _turn_input, *, cancel=None, gate=None):
        async def _gen():
            yield RuntimeEvent(
                type="tool", payload=_tool_start("t1", "web_fetch"), turn_id="t"
            )
            yield RuntimeEvent(
                type="tool",
                payload=_tool_end("t1", "fetched https://example.com/a fine"),
                turn_id="t",
            )
            yield RuntimeEvent(
                type="token",
                payload={"type": "text_delta", "delta": "Cited: https://uncovered.org/z"},
                turn_id="t",
            )
            yield EngineResult(
                terminal=Terminal.completed, usage={}, cost_usd=0.0, error=None
            )

        return _gen()


def _run(output: str, monkeypatch, mode: str | None) -> str:
    from magi_agent.cli.headless import run_headless

    monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
    if mode is None:
        monkeypatch.delenv("MAGI_RESEARCH_GOVERNANCE_MODE", raising=False)
    else:
        monkeypatch.setenv("MAGI_RESEARCH_GOVERNANCE_MODE", mode)
    out = io.StringIO()
    code = asyncio.run(
        run_headless(
            "what is X?",
            output=output,  # type: ignore[arg-type]
            driver=_WebTurnDriver(),
            stream=out,
        )
    )
    assert code == 0
    return out.getvalue()

def test_stream_json_emits_audit_frame_when_enabled(monkeypatch):
    raw = _run("stream-json", monkeypatch, "audit")
    frames = [json.loads(line) for line in raw.splitlines() if line.strip()]
    audit_frames = [f for f in frames if "research_governance_audit" in json.dumps(f)]
    assert audit_frames, f"no audit frame in: {raw}"
    payload = json.dumps(audit_frames)
    assert "uncovered.org/z" in payload


def test_stream_json_no_audit_frame_by_default(monkeypatch):
    raw = _run("stream-json", monkeypatch, None)
    assert "research_governance_audit" not in raw


def test_text_mode_output_unchanged_and_audit_logged(monkeypatch):
    from magi_agent.cli import headless as headless_mod

    logged: list[str] = []
    monkeypatch.setattr(headless_mod, "_log", logged.append)
    raw = _run("text", monkeypatch, "audit")
    # stdout contract unchanged (single text body, no audit JSON on stdout)
    assert "research_governance_audit" not in raw
    assert any("research_governance_audit" in line for line in logged)
