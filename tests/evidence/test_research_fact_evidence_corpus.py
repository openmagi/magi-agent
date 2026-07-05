"""Fix 2: research_fact sources land in the SAME producer_control corpus as
web_fetch, not just the SessionSourceRegistry.

research_fact registers its brief's sources through the SessionSourceRegistry via
an injectable id-assigner. The ambient wrap-point classifier returns [] for
research_fact (its output is a synthesized brief, not a per-source result shape),
so without an explicit evidence emission a research_fact src_N would live in
``registry.snapshot()`` but be ABSENT from the ``_records`` corpus the pre-final
gate reads. This locks: after a research_fact call the sources appear in BOTH the
registry snapshot AND the collector per-session records with
origin=producer_control, producing_rule_id="source_citation.capture".
"""
from __future__ import annotations

import asyncio


def _fake_research_fact(question: str, *, assign_id=None) -> str:
    assert assign_id is not None, "handler must thread the registry id-assigner"
    a = assign_id("web_fetch", "https://example.com/a", "Example A")
    b = assign_id("web_fetch", "https://example.com/b", "Example B")
    return f"[{a}] fact one\n[{b}] fact two"


def test_research_fact_sources_in_registry_and_producer_control_corpus(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_SOURCE_CITATION_ENABLED", "1")

    from magi_agent.evidence.local_tool_collector import LocalToolEvidenceCollector
    from magi_agent.evidence.types import EvidenceRecord
    from magi_agent.cli.tool_runtime import _citation_evidence_sink_for_session
    from magi_agent.tools.context import ToolContext
    from magi_agent.plugins.native import web as web_plugin

    monkeypatch.setattr(
        "magi_agent.tools.web_search_tools.research_fact", _fake_research_fact
    )

    session_id = "sid-rf"
    collector = LocalToolEvidenceCollector()
    registry = collector.source_registry_for(session_id)
    assert registry is not None
    sink = _citation_evidence_sink_for_session(collector, session_id)

    context = ToolContext(
        bot_id="b",
        session_id=session_id,
        turn_id="turn-rf",
        citation_registry=registry,
        citation_evidence_sink=sink,
    )

    result = asyncio.run(
        web_plugin.handle_research_fact({"question": "what is x"}, context)
    )
    assert result.status == "ok"
    assert "src_1" in result.output["brief"]

    # (1) Registry snapshot carries both sources with stable ids.
    snapshot = registry.snapshot()
    assert len(snapshot) == 2
    assert {r.source_id for r in snapshot} == {"src_1", "src_2"}

    # (2) The SAME sources are in the collector's producer_control corpus.
    corpus = collector.collect_for_session(session_id)
    citation_records = [
        r
        for r in corpus
        if isinstance(r, EvidenceRecord)
        and r.origin == "producer_control"
        and r.producing_rule_id == "source_citation.capture"
    ]
    assert len(citation_records) == 2
    cited_ids = {r.fields.get("sourceId") for r in citation_records}
    assert cited_ids == {"src_1", "src_2"}


def test_research_fact_no_sink_still_registers_but_no_corpus(monkeypatch) -> None:
    # Sink absent (belt-and-suspenders): registration still works, no crash, and
    # no producer_control citation record is emitted (the exact gap Fix 2 closes).
    monkeypatch.setenv("MAGI_SOURCE_CITATION_ENABLED", "1")

    from magi_agent.evidence.local_tool_collector import LocalToolEvidenceCollector
    from magi_agent.evidence.types import EvidenceRecord
    from magi_agent.tools.context import ToolContext
    from magi_agent.plugins.native import web as web_plugin

    monkeypatch.setattr(
        "magi_agent.tools.web_search_tools.research_fact", _fake_research_fact
    )

    session_id = "sid-rf2"
    collector = LocalToolEvidenceCollector()
    registry = collector.source_registry_for(session_id)
    context = ToolContext(
        bot_id="b",
        session_id=session_id,
        turn_id="turn-rf2",
        citation_registry=registry,
        citation_evidence_sink=None,
    )

    result = asyncio.run(
        web_plugin.handle_research_fact({"question": "q"}, context)
    )
    assert result.status == "ok"
    assert len(registry.snapshot()) == 2
    corpus = collector.collect_for_session(session_id)
    citation_records = [
        r
        for r in corpus
        if isinstance(r, EvidenceRecord)
        and r.producing_rule_id == "source_citation.capture"
    ]
    assert citation_records == []
