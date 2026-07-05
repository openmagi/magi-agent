"""Wave 2 Piece B: research_fact gains an injectable id-assigner so its brief
lines carry session-scoped src_N ids, composing with the existing guidance.
"""
from __future__ import annotations


def _search_fn(_query):
    return {
        "web": {
            "results": [
                {"url": "https://a.com", "description": "da"},
                {"url": "https://b.com", "description": "db"},
            ]
        }
    }


def _fetch_fn(url):
    return {"data": {"markdown": f"content-{url[-5:]}"}}


def test_research_fact_default_noop_keeps_bracket_index(monkeypatch) -> None:
    """No assign_id (harness off): brief lines keep the numeric [i] form,
    byte-identical to the pre-Wave-2 baseline."""
    monkeypatch.delenv("MAGI_RESEARCH_FACT_GUIDANCE_ENABLED", raising=False)
    from magi_agent.tools.web_search_tools import research_fact

    brief = research_fact("q", search_fn=_search_fn, fetch_fn=_fetch_fn, n=2)
    assert brief == (
        "[1] https://a.com\ncontent-a.com\n\n---\n\n"
        "[2] https://b.com\ncontent-b.com"
    )


def test_research_fact_assigner_rewrites_to_src_ids(monkeypatch) -> None:
    """A real assigner (harness on) rewrites the per-source line index to the
    registry src_N and prepends the one-line citation instruction."""
    monkeypatch.delenv("MAGI_RESEARCH_FACT_GUIDANCE_ENABLED", raising=False)
    from magi_agent.tools.web_search_tools import research_fact

    ids = {"https://a.com": "src_5", "https://b.com": "src_6"}

    def assign_id(kind, url, title):
        assert kind == "web_fetch"
        return ids.get(url)

    brief = research_fact(
        "q", search_fn=_search_fn, fetch_fn=_fetch_fn, n=2, assign_id=assign_id
    )
    assert brief == (
        "Cite these ids inline when you use a figure, e.g. [src_12].\n\n"
        "[src_5] https://a.com\ncontent-a.com\n\n---\n\n"
        "[src_6] https://b.com\ncontent-b.com"
    )


def test_research_fact_assigner_composes_with_guidance(monkeypatch) -> None:
    """With MAGI_RESEARCH_FACT_GUIDANCE_ENABLED on AND a real assigner, the
    citation line composes with (does not replace) the guidance header/footer."""
    monkeypatch.setenv("MAGI_RESEARCH_FACT_GUIDANCE_ENABLED", "1")
    from magi_agent.tools.web_search_tools import research_fact

    def assign_id(kind, url, title):
        return {"https://a.com": "src_5", "https://b.com": "src_6"}.get(url)

    brief = research_fact(
        "q", search_fn=_search_fn, fetch_fn=_fetch_fn, n=2, assign_id=assign_id
    )
    assert "Cite these ids inline when you use a figure, e.g. [src_12]." in brief
    assert "[src_5] https://a.com" in brief
    assert "Cross-check: compare the specific values" in brief  # guidance footer
    assert "research_fact brief" in brief  # guidance header


def test_research_fact_assigner_returning_none_falls_back(monkeypatch) -> None:
    """An assigner that returns None for a url falls back to [i] for that source
    (mixed assignment is safe)."""
    monkeypatch.delenv("MAGI_RESEARCH_FACT_GUIDANCE_ENABLED", raising=False)
    from magi_agent.tools.web_search_tools import research_fact

    def assign_id(kind, url, title):
        return "src_5" if url == "https://a.com" else None

    brief = research_fact(
        "q", search_fn=_search_fn, fetch_fn=_fetch_fn, n=2, assign_id=assign_id
    )
    assert "[src_5] https://a.com" in brief
    assert "[2] https://b.com" in brief
