"""Wave 2 wiring tests: register_and_inject_sources single-registration + the
record_tool_result precomputed path (no double registration).
"""
from __future__ import annotations


def _web_search_result_dict():
    # Model-facing dict shape (ToolResult.model_dump(by_alias=True)).
    return {
        "status": "ok",
        "output": {
            "results": [
                {"url": "https://alpha.com", "title": "Alpha", "description": "about alpha"},
                {"url": "https://beta.com", "title": "Beta", "description": "beta desc"},
            ]
        },
        "metadata": {"tool": "web_search"},
    }


def _registry_count(collector, session_id):
    registry = collector._session_source_registries.get(session_id)
    if registry is None:
        return 0
    return len(registry.snapshot())


def test_register_and_inject_registers_once_and_injects(monkeypatch) -> None:
    """With citation ON, register_and_inject_sources registers each source once,
    returns the injected dict carrying src ids, and returns producer_control
    records."""
    monkeypatch.setenv("MAGI_SOURCE_CITATION_ENABLED", "1")
    from magi_agent.evidence.local_tool_collector import LocalToolEvidenceCollector

    collector = LocalToolEvidenceCollector()
    injected, records = collector.register_and_inject_sources(
        session_id="s1",
        turn_id="t1",
        tool_call_id="call-1",
        tool_name="web_search",
        result=_web_search_result_dict(),
        arguments={"query": "alpha"},
    )
    assert _registry_count(collector, "s1") == 2
    assert injected["metadata"]["citation"]["sourceIds"] == ["src_1", "src_2"]
    assert injected["output"]["results"][0]["sourceId"] == "src_1"
    assert "[src_1] Alpha" in injected["llmOutput"]
    assert any(
        getattr(r, "producing_rule_id", None) == "source_citation.capture" for r in records
    )


def test_record_tool_result_with_precomputed_does_not_reregister(monkeypatch) -> None:
    """The wrap-point order: register_and_inject_sources (registers) THEN
    record_tool_result with the already-injected result + precomputed records.
    record_tool_result must NOT register a second time (single-registration)."""
    monkeypatch.setenv("MAGI_SOURCE_CITATION_ENABLED", "1")
    from magi_agent.evidence.local_tool_collector import LocalToolEvidenceCollector

    collector = LocalToolEvidenceCollector()
    injected, records = collector.register_and_inject_sources(
        session_id="s1",
        turn_id="t1",
        tool_call_id="call-1",
        tool_name="web_search",
        result=_web_search_result_dict(),
        arguments={"query": "alpha"},
    )
    assert _registry_count(collector, "s1") == 2

    collector.record_tool_result(
        session_id="s1",
        turn_id="t1",
        tool_call_id="call-1",
        tool_name="web_search",
        result=injected,
        arguments={"query": "alpha"},
        precomputed_citation_records=records,
    )
    # Still exactly 2 sources: record_tool_result consumed the precomputed
    # records instead of re-registering.
    assert _registry_count(collector, "s1") == 2
    # The precomputed citation records landed in the turn corpus.
    corpus = collector.collect_for_turn("t1")
    capture_records = [
        r for r in corpus
        if getattr(r, "producing_rule_id", None) == "source_citation.capture"
    ]
    assert len(capture_records) == 2


def test_injected_flag_guards_double_register_even_without_precomputed(monkeypatch) -> None:
    """Defense in depth: if record_tool_result is called with an
    already-injected result but no precomputed records, it must still NOT
    re-register (the metadata.citation.injected marker is the guard)."""
    monkeypatch.setenv("MAGI_SOURCE_CITATION_ENABLED", "1")
    from magi_agent.evidence.local_tool_collector import LocalToolEvidenceCollector

    collector = LocalToolEvidenceCollector()
    injected, _records = collector.register_and_inject_sources(
        session_id="s1",
        turn_id="t1",
        tool_call_id="call-1",
        tool_name="web_search",
        result=_web_search_result_dict(),
        arguments={"query": "alpha"},
    )
    assert _registry_count(collector, "s1") == 2
    collector.record_tool_result(
        session_id="s1",
        turn_id="t1",
        tool_call_id="call-1",
        tool_name="web_search",
        result=injected,
        arguments={"query": "alpha"},
    )
    assert _registry_count(collector, "s1") == 2


def test_flag_off_register_and_inject_is_byte_identical(monkeypatch) -> None:
    """Safe/eval profile: register_and_inject_sources performs zero injection and
    returns the result unchanged (byte-identical)."""
    monkeypatch.setenv("MAGI_SOURCE_CITATION_ENABLED", "0")
    from magi_agent.evidence.local_tool_collector import LocalToolEvidenceCollector

    collector = LocalToolEvidenceCollector()
    original = _web_search_result_dict()
    before = repr(original)
    injected, records = collector.register_and_inject_sources(
        session_id="s1",
        turn_id="t1",
        tool_call_id="call-1",
        tool_name="web_search",
        result=original,
        arguments={"query": "alpha"},
    )
    assert injected == original
    assert repr(original) == before
    assert records == []
    assert _registry_count(collector, "s1") == 0


def test_wave1_direct_record_tool_result_still_captures(monkeypatch) -> None:
    """Wave 1 preserved: a direct record_tool_result call (no register_and_inject
    first, no precomputed) still classifies + registers + emits records."""
    monkeypatch.setenv("MAGI_SOURCE_CITATION_ENABLED", "1")
    from magi_agent.evidence.local_tool_collector import LocalToolEvidenceCollector

    collector = LocalToolEvidenceCollector()
    collector.record_tool_result(
        session_id="s1",
        turn_id="t1",
        tool_call_id="call-1",
        tool_name="web_search",
        result=_web_search_result_dict(),
        arguments={"query": "alpha"},
    )
    assert _registry_count(collector, "s1") == 2
