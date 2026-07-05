"""Byte-level tests for magi_agent/evidence/citation_injection.py (Wave 2).

The injection module rewrites the model-facing tool-result dict to carry the
assigned ``src_N`` ids: a rendered header per source, a structured ``sources``
mirror, and a ``citation`` metadata marker. It never truncates or reorders
provider content, and it fails quiet.
"""
from __future__ import annotations


def _entry(source_id, kind, uri, title=None, snippet=None):
    from magi_agent.evidence.citation_injection import InjectedSource

    return InjectedSource(
        source_id=source_id, kind=kind, uri=uri, title=title, snippet=snippet
    )


def test_web_search_injection_rendered_block_and_structured_mirror() -> None:
    """web_search injection builds a per-entry rendered block on llmOutput,
    adds a sourceId to each raw result item, adds a top-level sources list, and
    stamps citation metadata. Byte-exact."""
    from magi_agent.evidence.citation_injection import inject_citation_headers

    result = {
        "status": "ok",
        "output": {
            "results": [
                {"url": "https://alpha.com", "title": "Alpha", "description": "about alpha"},
                {"url": "https://beta.com", "title": "Beta", "description": "beta desc"},
            ]
        },
        "metadata": {"tool": "web_search"},
    }
    sources = [
        _entry("src_1", "web_search", "https://alpha.com", "Alpha", "about alpha"),
        _entry("src_2", "web_search", "https://beta.com", "Beta", "beta desc"),
    ]

    injected = inject_citation_headers("web_search", result, sources)

    assert injected["llmOutput"] == (
        "[src_1] Alpha\nhttps://alpha.com\nabout alpha\n\n"
        "[src_2] Beta\nhttps://beta.com\nbeta desc"
    )
    assert injected["output"]["results"][0]["sourceId"] == "src_1"
    assert injected["output"]["results"][1]["sourceId"] == "src_2"
    # Provider content preserved (url/title/description untouched).
    assert injected["output"]["results"][0]["url"] == "https://alpha.com"
    assert injected["output"]["results"][0]["title"] == "Alpha"
    assert injected["sources"] == [
        {"sourceId": "src_1", "url": "https://alpha.com", "title": "Alpha"},
        {"sourceId": "src_2", "url": "https://beta.com", "title": "Beta"},
    ]
    assert injected["metadata"]["citation"] == {
        "injected": True,
        "sourceIds": ["src_1", "src_2"],
    }


def test_web_search_injection_does_not_mutate_input() -> None:
    """Injection returns a new dict; the caller's dict is unchanged."""
    from magi_agent.evidence.citation_injection import inject_citation_headers

    result = {
        "status": "ok",
        "output": {"results": [{"url": "https://alpha.com", "title": "A", "description": "d"}]},
        "metadata": {"tool": "web_search"},
    }
    before = repr(result)
    sources = [_entry("src_1", "web_search", "https://alpha.com", "A", "d")]
    inject_citation_headers("web_search", result, sources)
    assert repr(result) == before, "input dict must not be mutated"


def test_web_fetch_injection_prepends_header_to_markdown() -> None:
    """web_fetch prepends one header line to output.markdown and mirrors the
    source, without truncating the provider markdown."""
    from magi_agent.evidence.citation_injection import inject_citation_headers

    result = {
        "status": "ok",
        "output": {"markdown": "# Real Page\n\nBody content here."},
        "metadata": {"tool": "web_fetch"},
    }
    sources = [_entry("src_9", "web_fetch", "https://example.com/a", "Example A")]

    injected = inject_citation_headers("web_fetch", result, sources)

    assert injected["output"]["markdown"] == (
        "[source: src_9] Example A - https://example.com/a\n\n"
        "# Real Page\n\nBody content here."
    )
    assert injected["sources"] == [
        {"sourceId": "src_9", "url": "https://example.com/a", "title": "Example A"}
    ]
    assert injected["metadata"]["citation"] == {
        "injected": True,
        "sourceIds": ["src_9"],
    }


def test_web_fetch_injection_no_title_uses_url_only_header() -> None:
    """A source with no title yields a header without the ' - ' separator."""
    from magi_agent.evidence.citation_injection import inject_citation_headers

    result = {
        "status": "ok",
        "output": {"markdown": "Body."},
        "metadata": {"tool": "web_fetch"},
    }
    sources = [_entry("src_3", "web_fetch", "https://example.com/x", None)]
    injected = inject_citation_headers("web_fetch", result, sources)
    assert injected["output"]["markdown"] == (
        "[source: src_3] https://example.com/x\n\nBody."
    )


def test_kb_injection_list_shape() -> None:
    """KnowledgeSearch injects a per-source header block and per-item sourceId."""
    from magi_agent.evidence.citation_injection import inject_citation_headers

    result = {
        "status": "ok",
        "output": {
            "results": [
                {"id": "d1", "title": "Doc One", "path": "kb://c/d1"},
                {"id": "d2", "title": "Doc Two", "path": "kb://c/d2"},
            ]
        },
        "metadata": {"tool": "KnowledgeSearch"},
    }
    sources = [
        _entry("src_1", "kb", "kb://c/d1", "Doc One"),
        _entry("src_2", "kb", "kb://c/d2", "Doc Two"),
    ]
    injected = inject_citation_headers("KnowledgeSearch", result, sources)
    assert injected["llmOutput"] == (
        "[src_1] Doc One - kb://c/d1\n\n[src_2] Doc Two - kb://c/d2"
    )
    assert injected["sources"] == [
        {"sourceId": "src_1", "url": "kb://c/d1", "title": "Doc One"},
        {"sourceId": "src_2", "url": "kb://c/d2", "title": "Doc Two"},
    ]
    assert injected["metadata"]["citation"]["sourceIds"] == ["src_1", "src_2"]


def test_truncation_before_injection_header_not_cut() -> None:
    """cap_text truncation is a provider concern that runs BEFORE injection.
    This asserts the injected header survives regardless of body length: the
    header is prepended AFTER the (already-capped) body, so it is never cut."""
    from magi_agent.evidence.citation_injection import inject_citation_headers

    capped_body = "x" * 4000  # simulate an already-capped provider markdown
    result = {
        "status": "ok",
        "output": {"markdown": capped_body},
        "metadata": {"tool": "web_fetch"},
    }
    sources = [_entry("src_5", "web_fetch", "https://e.com", "E")]
    injected = inject_citation_headers("web_fetch", result, sources)
    md = injected["output"]["markdown"]
    assert md.startswith("[source: src_5] E - https://e.com\n\n")
    assert md.endswith(capped_body)


def test_web_fetch_cap_then_inject_integration(monkeypatch) -> None:
    """Cap-then-inject WIRING, end to end through the real web_fetch handler.

    The handler caps the provider markdown to _FIRECRAWL_MAX_CHARS BEFORE the
    wrap point injects the citation header, so the injected result carries BOTH a
    [source: src_N] header AND an already-capped body (the header is never the
    thing that gets truncated). test_truncation_before_injection_header_not_cut
    asserts the unit invariant on a pre-capped body; this asserts the real
    handler + collector wiring produces it.
    """
    import asyncio

    monkeypatch.setenv("MAGI_SOURCE_CITATION_ENABLED", "1")

    from magi_agent.evidence.local_tool_collector import LocalToolEvidenceCollector
    from magi_agent.plugins.native import web as web_plugin
    from magi_agent.tools.context import ToolContext
    from magi_agent.tools.web_search_tools import _FIRECRAWL_MAX_CHARS

    oversize = "x" * (_FIRECRAWL_MAX_CHARS + 5000)
    monkeypatch.setattr(
        "magi_agent.tools.web_search_tools.web_fetch_raw",
        lambda url: {"data": {"markdown": oversize}},
    )

    url = "https://example.com/big"
    result = asyncio.run(
        web_plugin.handle_web_fetch({"url": url}, ToolContext(bot_id="b"))
    )
    assert result.status == "ok"
    # The handler capped the provider bytes BEFORE injection (cap_text keeps a
    # head+tail around a "..." elision, so the capped length hugs the cap rather
    # than the oversize input).
    assert len(result.output["markdown"]) <= _FIRECRAWL_MAX_CHARS + 200
    assert len(result.output["markdown"]) < len(oversize)

    collector = LocalToolEvidenceCollector()
    injected, _records = collector.register_and_inject_sources(
        session_id="sid-fetch",
        turn_id="turn-fetch",
        tool_call_id="call-fetch",
        tool_name="web_fetch",
        result=result.model_dump(by_alias=True),
        arguments={"url": url},
    )
    body = injected["output"]["markdown"]
    header = f"[source: src_1] {url}\n\n"
    # Header present...
    assert body.startswith(header)
    # ...AND the body is capped: injection only prepends the header, so the total
    # hugs cap + header, never the pre-cap oversize length.
    assert len(body) <= _FIRECRAWL_MAX_CHARS + len(header) + 200
    assert len(body) < len(oversize)


def test_empty_sources_returns_result_untouched() -> None:
    """No registered sources -> no injection, no citation metadata, same dict."""
    from magi_agent.evidence.citation_injection import inject_citation_headers

    result = {
        "status": "ok",
        "output": {"results": []},
        "metadata": {"tool": "web_search"},
    }
    injected = inject_citation_headers("web_search", result, [])
    assert "citation" not in injected.get("metadata", {})
    assert "sources" not in injected


def test_injection_fail_quiet_on_bad_shape() -> None:
    """A result whose output shape does not match the tool still gets the
    structured sources mirror + metadata and never raises."""
    from magi_agent.evidence.citation_injection import inject_citation_headers

    result = {"status": "ok", "output": None, "metadata": {"tool": "web_fetch"}}
    sources = [_entry("src_1", "web_fetch", "https://e.com", "E")]
    injected = inject_citation_headers("web_fetch", result, sources)
    # Even without a text field to prepend to, the structured mirror + marker
    # must still be present so downstream consumers see the mapping.
    assert injected["sources"] == [
        {"sourceId": "src_1", "url": "https://e.com", "title": "E"}
    ]
    assert injected["metadata"]["citation"]["sourceIds"] == ["src_1"]
