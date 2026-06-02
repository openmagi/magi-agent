"""Tests for DeferredToolRegistry — PR 2 of deferred tool loading."""

from __future__ import annotations

import pytest

from magi_agent.tools.manifest import Budget, ToolManifest, ToolSource
from magi_agent.tools.registry import ToolRegistry
from magi_agent.tools.deferred import DeferredToolRegistry


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


class TestThresholdBehavior:
    def test_below_threshold_returns_all_tools(self) -> None:
        registry = ToolRegistry()
        for i in range(10):
            registry.register(_manifest(f"Tool{i}"))
        deferred = DeferredToolRegistry(registry)
        initial = deferred.get_initial_tools(threshold=30)
        assert len(initial.active_manifests) == 10
        assert initial.deferred_names == ()

    def test_above_threshold_defers_deferrable_tools(self) -> None:
        registry = _big_registry(35, non_deferrable=5)
        deferred = DeferredToolRegistry(registry)
        initial = deferred.get_initial_tools(threshold=30)
        assert len(initial.active_manifests) == 5
        assert len(initial.deferred_names) == 30

    def test_exact_threshold_does_not_defer(self) -> None:
        registry = ToolRegistry()
        for i in range(30):
            registry.register(_manifest(f"Tool{i}"))
        deferred = DeferredToolRegistry(registry)
        initial = deferred.get_initial_tools(threshold=30)
        assert initial.deferred_names == ()


class TestNonDeferrable:
    def test_dangerous_tools_never_deferred(self) -> None:
        registry = ToolRegistry()
        for i in range(29):
            registry.register(_manifest(f"SafeTool{i}"))
        registry.register(
            _manifest("DangerousTool", dangerous=True, mutates_workspace=True, permission="execute", should_defer=True)
        )
        registry.register(_manifest("ExtraTool", should_defer=True))
        deferred = DeferredToolRegistry(registry)
        initial = deferred.get_initial_tools(threshold=10)
        active_names = {m.name for m in initial.active_manifests}
        assert "DangerousTool" in active_names

    def test_non_deferrable_flag_respected(self) -> None:
        registry = _big_registry(35, non_deferrable=5)
        deferred = DeferredToolRegistry(registry)
        initial = deferred.get_initial_tools(threshold=10)
        active_names = {m.name for m in initial.active_manifests}
        for i in range(5):
            assert f"CoreTool{i}" in active_names


class TestLoadDeferred:
    def test_load_deferred_returns_manifests(self) -> None:
        registry = _big_registry(35, non_deferrable=5)
        deferred = DeferredToolRegistry(registry)
        deferred.get_initial_tools(threshold=10)
        loaded = deferred.load_deferred(["DeferrableTool0", "DeferrableTool1"])
        assert len(loaded) == 2
        names = {m.name for m in loaded}
        assert names == {"DeferrableTool0", "DeferrableTool1"}

    def test_load_deferred_nonexistent_skipped(self) -> None:
        registry = _big_registry(35, non_deferrable=5)
        deferred = DeferredToolRegistry(registry)
        deferred.get_initial_tools(threshold=10)
        loaded = deferred.load_deferred(["DeferrableTool0", "NoSuchTool"])
        assert len(loaded) == 1
        assert loaded[0].name == "DeferrableTool0"

    def test_load_deferred_already_active_returns_it(self) -> None:
        registry = _big_registry(35, non_deferrable=5)
        deferred = DeferredToolRegistry(registry)
        deferred.get_initial_tools(threshold=10)
        loaded = deferred.load_deferred(["CoreTool0"])
        assert len(loaded) == 1
        assert loaded[0].name == "CoreTool0"


class TestSystemPromptHint:
    def test_deferred_names_in_initial_result(self) -> None:
        registry = _big_registry(35, non_deferrable=5)
        deferred = DeferredToolRegistry(registry)
        initial = deferred.get_initial_tools(threshold=10)
        assert len(initial.deferred_names) == 30
        assert all(isinstance(n, str) for n in initial.deferred_names)

    def test_hint_text_lists_deferred_names(self) -> None:
        registry = _big_registry(35, non_deferrable=5)
        deferred = DeferredToolRegistry(registry)
        initial = deferred.get_initial_tools(threshold=10)
        assert initial.hint_text is not None
        assert "ToolSearch" in initial.hint_text
        assert "DeferrableTool0" in initial.hint_text
