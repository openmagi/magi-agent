"""Tests for magi_agent/evidence/citation_capture.py (Wave 1)."""
from __future__ import annotations

import pytest


def test_classify_web_search_extracts_urls_from_result() -> None:
    """web_search result with URL list extracts sources."""
    from magi_agent.evidence.citation_capture import classify_tool_result_for_citation

    result = {
        "results": [
            {"url": "https://alpha.com", "title": "Alpha", "snippet": "about alpha"},
            {"url": "https://beta.com", "title": "Beta"},
        ]
    }
    specs = classify_tool_result_for_citation("web_search", result, {})
    assert len(specs) == 2
    urls = {s.uri for s in specs}
    assert "https://alpha.com" in urls
    assert "https://beta.com" in urls
    assert all(s.kind == "web_search" for s in specs)


def test_classify_web_fetch_extracts_url() -> None:
    """web_fetch with URL in args produces a web_fetch source."""
    from magi_agent.evidence.citation_capture import classify_tool_result_for_citation

    result = {"output": {"title": "Example Page", "content": "..."}}
    specs = classify_tool_result_for_citation(
        "web_fetch", result, {"url": "https://example.com/article"}
    )
    assert len(specs) == 1
    assert specs[0].kind == "web_fetch"
    assert specs[0].uri == "https://example.com/article"


def test_classify_fileread_extracts_path() -> None:
    """FileRead with path in args produces a file source."""
    from magi_agent.evidence.citation_capture import classify_tool_result_for_citation

    result = {"output": "file contents here"}
    specs = classify_tool_result_for_citation(
        "FileRead", result, {"path": "/workspace/README.md"}
    )
    assert len(specs) == 1
    assert specs[0].kind == "file"
    assert "README.md" in specs[0].uri


def test_classify_memory_tool_returns_empty() -> None:
    """Memory tool names must never register sources (return empty list)."""
    from magi_agent.evidence.citation_capture import classify_tool_result_for_citation

    result = {"output": "some memory content"}
    for name in ("MemoryRead", "MemoryWrite", "MemorySearch", "memory_read"):
        specs = classify_tool_result_for_citation(name, result, {})
        assert specs == [], f"memory tool {name!r} must return empty list"


def test_classify_unknown_tool_returns_empty() -> None:
    """Unknown tool name returns empty list (fail-quiet)."""
    from magi_agent.evidence.citation_capture import classify_tool_result_for_citation

    specs = classify_tool_result_for_citation("SomeObscureUnknownTool", {"x": 1}, {})
    assert specs == []


def test_classify_fileread_authored_path_excluded() -> None:
    """FileRead of an authored (agent-written) path is excluded."""
    from magi_agent.evidence.citation_capture import classify_tool_result_for_citation

    authored = frozenset({"/workspace/output.md"})
    specs = classify_tool_result_for_citation(
        "FileRead",
        {"output": "content"},
        {"path": "/workspace/output.md"},
        authored_paths=authored,
    )
    assert specs == [], "authored file must not register as a citation source"
