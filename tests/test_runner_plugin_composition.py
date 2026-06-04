"""Regression guard: independently-flagged ADK plugins must COMPOSE (union),
not silently drop one another, on the live local runner builder.

The edit-retry reflection, resilience (loop guard + recovery), and context
compaction plugins each attach to ``App(plugins=...)`` in
``build_local_adk_runner``. Each was added on an independent branch editing the
same ``runner_plugins`` construction; a naive merge could keep only one. This
test asserts that with all relevant flags ON, every plugin is attached, and that
each flag attaches exactly its own plugin.
"""

from __future__ import annotations

import pytest

from magi_agent.adk_bridge import local_runner as lr


def _plugins(monkeypatch: pytest.MonkeyPatch, **flags: str) -> list[object]:
    monkeypatch.setenv(lr.LOCAL_ADK_RUNNER_FLAG, "1")
    for key, value in flags.items():
        monkeypatch.setenv(key, value)
    bundle = lr.build_local_adk_runner()
    return list(bundle.runner.app.plugins)


def _names(plugins: list[object]) -> set[str]:
    return {type(p).__name__ for p in plugins}


def test_all_flags_on_attaches_all_three_plugins(monkeypatch: pytest.MonkeyPatch) -> None:
    plugins = _plugins(
        monkeypatch,
        MAGI_EDIT_RETRY_REFLECTION_ENABLED="1",
        MAGI_LOOP_GUARD_ENABLED="1",
        MAGI_ERROR_RECOVERY_ENABLED="1",
        MAGI_CONTEXT_COMPACTION_ENABLED="1",
    )
    names = _names(plugins)
    assert "MagiEditRetryReflectionPlugin" in names
    assert "MagiResiliencePlugin" in names
    assert "MagiContextCompactionPlugin" in names
    assert len(plugins) == 3


def test_only_compaction_on_attaches_only_compaction(monkeypatch: pytest.MonkeyPatch) -> None:
    plugins = _plugins(monkeypatch, MAGI_CONTEXT_COMPACTION_ENABLED="1")
    assert _names(plugins) == {"MagiContextCompactionPlugin"}


def test_only_loop_guard_on_attaches_only_resilience(monkeypatch: pytest.MonkeyPatch) -> None:
    plugins = _plugins(monkeypatch, MAGI_LOOP_GUARD_ENABLED="1")
    assert _names(plugins) == {"MagiResiliencePlugin"}


def test_all_flags_off_attaches_no_plugins(monkeypatch: pytest.MonkeyPatch) -> None:
    for flag in (
        "MAGI_EDIT_RETRY_REFLECTION_ENABLED",
        "MAGI_LOOP_GUARD_ENABLED",
        "MAGI_ERROR_RECOVERY_ENABLED",
        "MAGI_CONTEXT_COMPACTION_ENABLED",
    ):
        monkeypatch.delenv(flag, raising=False)
    assert _plugins(monkeypatch) == []
