"""Tests for deferred tool ADK integration — PR 3 of deferred tool loading."""

from __future__ import annotations

import asyncio
import os
from unittest.mock import MagicMock

import pytest

from magi_agent.tools.context import ToolContext
from magi_agent.tools.manifest import Budget, ToolManifest, ToolSource
from magi_agent.tools.result import ToolResult
from magi_agent.tools.registry import ToolRegistry
from magi_agent.tools.deferred import DeferredToolRegistry
from magi_agent.tools.tool_search import ToolSearchTool
from magi_agent.adk_bridge.tool_adapter import (
    DeferredToolManager,
    build_adk_function_tools_for_registry,
    build_deferred_adk_tools,
)


def _source() -> ToolSource:
    return ToolSource(kind="builtin", package="test")


def _manifest(
    name: str,
    description: str = "A test tool.",
    *,
    permission: str = "read",
    dangerous: bool = False,
    mutates_workspace: bool = False,
    should_defer: bool = True,
    tags: tuple[str, ...] = (),
) -> ToolManifest:
    return ToolManifest(
        name=name,
        description=description,
        kind="core",
        source=_source(),
        permission=permission,
        input_schema={"type": "object"},
        timeout_ms=30_000,
        budget=Budget(max_calls_per_turn=10, max_parallel=1),
        dangerous=dangerous,
        mutates_workspace=mutates_workspace,
        should_defer=should_defer,
        tags=tags,
    )


def _big_registry(n: int = 35, *, non_deferrable: int = 5) -> ToolRegistry:
    registry = ToolRegistry()
    for i in range(non_deferrable):
        registry.register(_manifest(f"CoreTool{i}", should_defer=False))
    for i in range(n - non_deferrable):
        registry.register(_manifest(f"DeferrableTool{i}", should_defer=True))
    return registry


def _small_registry() -> ToolRegistry:
    registry = ToolRegistry()
    for i in range(10):
        registry.register(_manifest(f"Tool{i}", should_defer=True))
    return registry


class TestBuildDeferredAdkTools:
    def test_disabled_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAGI_DEFERRED_TOOLS_ENABLED", "0")
        registry = _big_registry(35, non_deferrable=5)
        result = build_deferred_adk_tools(registry, threshold=30)
        assert result is None

    def test_above_threshold_returns_manager(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAGI_DEFERRED_TOOLS_ENABLED", "1")
        registry = _big_registry(35, non_deferrable=5)
        result = build_deferred_adk_tools(registry, threshold=30)
        assert result is not None
        assert isinstance(result, DeferredToolManager)
        assert result.hint_text is not None
        assert "ToolSearch" in result.hint_text
        assert len(result.deferred_names) == 30

    def test_below_threshold_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAGI_DEFERRED_TOOLS_ENABLED", "1")
        registry = _small_registry()
        result = build_deferred_adk_tools(registry, threshold=30)
        assert result is None

    def test_env_disabled_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAGI_DEFERRED_TOOLS_ENABLED", "0")
        registry = _big_registry(35, non_deferrable=5)
        result = build_deferred_adk_tools(registry, threshold=30)
        assert result is None

    def test_env_enabled_works(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAGI_DEFERRED_TOOLS_ENABLED", "1")
        registry = _big_registry(35, non_deferrable=5)
        result = build_deferred_adk_tools(registry, threshold=30)
        assert result is not None


class TestExcludeNames:
    def test_exclude_names_tracks_deferred(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAGI_DEFERRED_TOOLS_ENABLED", "1")
        registry = _big_registry(35, non_deferrable=5)
        manager = build_deferred_adk_tools(registry, threshold=30)
        assert manager is not None
        assert "DeferrableTool0" in manager.exclude_names
        assert len(manager.exclude_names) == 30

    def test_build_adk_tools_with_exclude_names(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAGI_DEFERRED_TOOLS_ENABLED", "1")
        registry = _big_registry(35, non_deferrable=5)
        for name in [f"CoreTool{i}" for i in range(5)] + [f"DeferrableTool{i}" for i in range(30)]:
            registry.enable(name)
        manager = build_deferred_adk_tools(registry, threshold=30)
        assert manager is not None
        mock_dispatcher = MagicMock()
        mock_factory = MagicMock()
        tools = build_adk_function_tools_for_registry(
            registry,
            mock_dispatcher,
            mode="act",
            tool_context_factory=mock_factory,
            attach_enabled=True,
            exclude_names=manager.exclude_names,
        )
        tool_names = {t.func.__name__ for t in tools}
        for i in range(5):
            assert f"CoreTool{i}" in tool_names
        for i in range(30):
            assert f"DeferrableTool{i}" not in tool_names


class TestMaterializeTools:
    def test_materialize_appends_to_adk_tools_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAGI_DEFERRED_TOOLS_ENABLED", "1")
        registry = _big_registry(35, non_deferrable=5)
        manager = build_deferred_adk_tools(registry, threshold=30)
        assert manager is not None

        mock_dispatcher = MagicMock()
        mock_factory = MagicMock()
        adk_tools: list[object] = []

        new_tools = manager.materialize_tools(
            ["DeferrableTool0", "DeferrableTool1"],
            mock_dispatcher,
            mode="act",
            tool_context_factory=mock_factory,
            adk_tools_list=adk_tools,
        )

        assert len(new_tools) == 2
        assert len(adk_tools) == 2
        tool_names = {t.func.__name__ for t in adk_tools}
        assert tool_names == {"DeferrableTool0", "DeferrableTool1"}

    def test_materialize_idempotent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAGI_DEFERRED_TOOLS_ENABLED", "1")
        registry = _big_registry(35, non_deferrable=5)
        manager = build_deferred_adk_tools(registry, threshold=30)
        assert manager is not None

        mock_dispatcher = MagicMock()
        mock_factory = MagicMock()
        adk_tools: list[object] = []

        manager.materialize_tools(
            ["DeferrableTool0"],
            mock_dispatcher,
            mode="act",
            tool_context_factory=mock_factory,
            adk_tools_list=adk_tools,
        )
        manager.materialize_tools(
            ["DeferrableTool0"],
            mock_dispatcher,
            mode="act",
            tool_context_factory=mock_factory,
            adk_tools_list=adk_tools,
        )

        assert len(adk_tools) == 1

    def test_materialize_removes_from_exclude_names(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAGI_DEFERRED_TOOLS_ENABLED", "1")
        registry = _big_registry(35, non_deferrable=5)
        manager = build_deferred_adk_tools(registry, threshold=30)
        assert manager is not None
        assert "DeferrableTool0" in manager.exclude_names

        mock_dispatcher = MagicMock()
        mock_factory = MagicMock()
        manager.materialize_tools(
            ["DeferrableTool0"],
            mock_dispatcher,
            mode="act",
            tool_context_factory=mock_factory,
            adk_tools_list=[],
        )

        assert "DeferrableTool0" not in manager.exclude_names

    def test_materialize_nonexistent_tool_skipped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAGI_DEFERRED_TOOLS_ENABLED", "1")
        registry = _big_registry(35, non_deferrable=5)
        manager = build_deferred_adk_tools(registry, threshold=30)
        assert manager is not None

        mock_dispatcher = MagicMock()
        mock_factory = MagicMock()
        adk_tools: list[object] = []

        new_tools = manager.materialize_tools(
            ["NoSuchTool"],
            mock_dispatcher,
            mode="act",
            tool_context_factory=mock_factory,
            adk_tools_list=adk_tools,
        )

        assert len(new_tools) == 0
        assert len(adk_tools) == 0

    def test_materialize_rejects_tools_outside_exposed_boundary(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("MAGI_DEFERRED_TOOLS_ENABLED", "1")
        registry = _big_registry(35, non_deferrable=5)
        manager = build_deferred_adk_tools(
            registry,
            threshold=30,
            exposed_tool_names=("CoreTool0", "DeferrableTool1"),
        )
        assert manager is not None

        new_tools = manager.materialize_tools(
            ["DeferrableTool0", "DeferrableTool1"],
            MagicMock(),
            mode="act",
            tool_context_factory=MagicMock(),
            adk_tools_list=[],
        )

        assert [tool.func.__name__ for tool in new_tools] == ["DeferrableTool1"]

    def test_materialized_tool_preserves_exposed_boundary_on_dispatch(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("MAGI_DEFERRED_TOOLS_ENABLED", "1")
        registry = _big_registry(35, non_deferrable=5)
        manager = build_deferred_adk_tools(
            registry,
            threshold=30,
            exposed_tool_names=("CoreTool0", "DeferrableTool0"),
        )
        assert manager is not None

        class RecordingDispatcher:
            exposed_tool_names: tuple[str, ...] | None = None

            async def dispatch(
                self,
                name: str,
                arguments: dict[str, object],
                context: ToolContext,
                *,
                mode: str,
                exposed_tool_names: tuple[str, ...] | None = None,
            ) -> ToolResult:
                self.exposed_tool_names = exposed_tool_names
                return ToolResult(status="ok", output={"name": name})

        dispatcher = RecordingDispatcher()
        new_tools = manager.materialize_tools(
            ["DeferrableTool0"],
            dispatcher,  # type: ignore[arg-type]
            mode="act",
            tool_context_factory=lambda _tool_context: MagicMock(),
            adk_tools_list=[],
        )
        assert len(new_tools) == 1

        result = asyncio.run(new_tools[0].func({}, object()))

        assert result["status"] == "ok"
        assert dispatcher.exposed_tool_names == ("CoreTool0", "DeferrableTool0")


class TestDeferredToolThresholdConfig:
    def test_custom_threshold_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAGI_DEFERRED_TOOLS_ENABLED", "1")
        monkeypatch.setenv("MAGI_DEFERRED_TOOL_THRESHOLD", "15")
        registry = ToolRegistry()
        for i in range(20):
            registry.register(_manifest(f"Tool{i}"))
        result = build_deferred_adk_tools(registry)
        assert result is not None
        assert len(result.deferred_names) > 0


class TestToolSearchIntegration:
    def test_tool_search_finds_deferred_tool(self) -> None:
        registry = _big_registry(35, non_deferrable=5)
        deferred_reg = DeferredToolRegistry(registry)
        deferred_reg.get_initial_tools(threshold=10)
        searcher = ToolSearchTool(registry)
        results = searcher.search("select:DeferrableTool0")
        assert len(results) == 1
        assert results[0]["name"] == "DeferrableTool0"

    def test_tool_search_keyword_finds_deferred(self) -> None:
        registry = _big_registry(35, non_deferrable=5)
        DeferredToolRegistry(registry).get_initial_tools(threshold=10)
        searcher = ToolSearchTool(registry)
        results = searcher.search("DeferrableTool")
        assert len(results) == 5
        names = {r["name"] for r in results}
        assert all(n.startswith("DeferrableTool") for n in names)

    def test_loaded_deferred_available_in_registry(self) -> None:
        registry = _big_registry(35, non_deferrable=5)
        deferred_reg = DeferredToolRegistry(registry)
        deferred_reg.get_initial_tools(threshold=10)
        loaded = deferred_reg.load_deferred(["DeferrableTool0"])
        assert len(loaded) == 1
        resolved = registry.resolve("DeferrableTool0")
        assert resolved is not None
        assert resolved.name == "DeferrableTool0"

    def test_end_to_end_search_then_materialize(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Full flow: ToolSearch finds deferred tool → materialize adds to agent.tools."""
        monkeypatch.setenv("MAGI_DEFERRED_TOOLS_ENABLED", "1")
        registry = _big_registry(35, non_deferrable=5)
        manager = build_deferred_adk_tools(registry, threshold=30)
        assert manager is not None

        searcher = ToolSearchTool(registry)
        results = searcher.search("select:DeferrableTool5")
        assert len(results) == 1
        assert results[0]["name"] == "DeferrableTool5"

        mock_dispatcher = MagicMock()
        mock_factory = MagicMock()
        agent_tools: list[object] = []

        new_tools = manager.materialize_tools(
            ["DeferrableTool5"],
            mock_dispatcher,
            mode="act",
            tool_context_factory=mock_factory,
            adk_tools_list=agent_tools,
        )

        assert len(new_tools) == 1
        assert len(agent_tools) == 1
        assert agent_tools[0].func.__name__ == "DeferrableTool5"
        assert "DeferrableTool5" not in manager.exclude_names
