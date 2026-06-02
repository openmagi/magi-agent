"""Tests for ToolSearchTool — PR 1 of deferred tool loading."""

from __future__ import annotations

import pytest

from openmagi_core_agent.tools.manifest import Budget, ToolManifest, ToolSource
from openmagi_core_agent.tools.registry import ToolRegistry
from openmagi_core_agent.tools.tool_search import ToolSearchTool


def _source() -> ToolSource:
    return ToolSource(kind="builtin", package="test")


def _manifest(name: str, description: str, **kwargs: object) -> ToolManifest:
    return ToolManifest(
        name=name,
        description=description,
        kind="core",
        source=_source(),
        permission=kwargs.pop("permission", "read"),
        input_schema={"type": "object"},
        timeout_ms=30_000,
        budget=Budget(max_calls_per_turn=10, max_parallel=1),
        tags=kwargs.pop("tags", ()),
        **kwargs,
    )


def _registry_with_tools() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(_manifest("FileRead", "Read workspace file contents."))
    registry.register(_manifest("FileWrite", "Write workspace file contents.", permission="write", mutates_workspace=True))
    registry.register(_manifest("FileEdit", "Edit existing workspace file contents.", permission="write", mutates_workspace=True))
    registry.register(_manifest("Glob", "List workspace paths matching a glob pattern."))
    registry.register(_manifest("Grep", "Search workspace text with a pattern."))
    registry.register(_manifest("Bash", "Run a shell command in the workspace.", permission="execute", dangerous=True, mutates_workspace=True))
    registry.register(_manifest("Clock", "Read current time metadata.", permission="meta"))
    registry.register(_manifest("AskUserQuestion", "Request user input through the OpenMagi control surface.", permission="meta"))
    return registry


class TestToolSearchSelectMode:
    def test_select_single_tool_by_exact_name(self) -> None:
        searcher = ToolSearchTool(_registry_with_tools())
        results = searcher.search("select:FileRead")
        assert len(results) == 1
        assert results[0]["name"] == "FileRead"
        assert "description" in results[0]

    def test_select_multiple_tools(self) -> None:
        searcher = ToolSearchTool(_registry_with_tools())
        results = searcher.search("select:FileRead,Grep,Glob")
        names = [r["name"] for r in results]
        assert sorted(names) == ["FileRead", "Glob", "Grep"]

    def test_select_nonexistent_tool_returns_empty(self) -> None:
        searcher = ToolSearchTool(_registry_with_tools())
        results = searcher.search("select:NoSuchTool")
        assert results == []

    def test_select_partial_match_returns_found_only(self) -> None:
        searcher = ToolSearchTool(_registry_with_tools())
        results = searcher.search("select:FileRead,NoSuchTool,Grep")
        names = [r["name"] for r in results]
        assert sorted(names) == ["FileRead", "Grep"]

    def test_select_returns_full_schema(self) -> None:
        searcher = ToolSearchTool(_registry_with_tools())
        results = searcher.search("select:Bash")
        assert len(results) == 1
        schema = results[0]
        assert schema["name"] == "Bash"
        assert "description" in schema
        assert "input_schema" in schema or "inputSchema" in schema


class TestToolSearchKeywordMode:
    def test_keyword_matches_name(self) -> None:
        searcher = ToolSearchTool(_registry_with_tools())
        results = searcher.search("File")
        names = [r["name"] for r in results]
        assert "FileRead" in names
        assert "FileWrite" in names
        assert "FileEdit" in names

    def test_keyword_matches_description(self) -> None:
        searcher = ToolSearchTool(_registry_with_tools())
        results = searcher.search("workspace")
        names = [r["name"] for r in results]
        assert len(names) >= 3

    def test_keyword_no_match_returns_empty(self) -> None:
        searcher = ToolSearchTool(_registry_with_tools())
        results = searcher.search("zzzznonexistentkeyword")
        assert results == []

    def test_keyword_case_insensitive(self) -> None:
        searcher = ToolSearchTool(_registry_with_tools())
        results_lower = searcher.search("file")
        results_upper = searcher.search("FILE")
        names_lower = sorted(r["name"] for r in results_lower)
        names_upper = sorted(r["name"] for r in results_upper)
        assert names_lower == names_upper

    def test_exact_name_scores_higher_than_description(self) -> None:
        searcher = ToolSearchTool(_registry_with_tools())
        results = searcher.search("Glob")
        assert results[0]["name"] == "Glob"


class TestToolSearchMaxResults:
    def test_max_results_caps_output(self) -> None:
        searcher = ToolSearchTool(_registry_with_tools())
        results = searcher.search("workspace", max_results=2)
        assert len(results) <= 2

    def test_default_max_results_is_5(self) -> None:
        registry = ToolRegistry()
        for i in range(10):
            registry.register(_manifest(f"Tool{i}", f"Description for tool {i}."))
        searcher = ToolSearchTool(registry)
        results = searcher.search("Description")
        assert len(results) == 5

    def test_max_results_zero_returns_empty(self) -> None:
        searcher = ToolSearchTool(_registry_with_tools())
        results = searcher.search("File", max_results=0)
        assert results == []


class TestToolSearchManifestOutput:
    def test_result_contains_required_fields(self) -> None:
        searcher = ToolSearchTool(_registry_with_tools())
        results = searcher.search("select:FileRead")
        assert len(results) == 1
        schema = results[0]
        assert "name" in schema
        assert "description" in schema
        assert "input_schema" in schema or "inputSchema" in schema
        assert "permission" in schema
