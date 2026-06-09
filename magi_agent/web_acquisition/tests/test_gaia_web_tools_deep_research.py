"""Tests for GAIA web_tools deep-research wiring — PR4.

All tests are hermetic: no real network, no real provider keys required.
"""

from __future__ import annotations

import pytest


def test_build_web_tools_returns_empty_without_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without platform credentials, build_web_tools returns []."""
    monkeypatch.delenv("MAGI_PLATFORM_BASE_URL", raising=False)
    monkeypatch.delenv("MAGI_PLATFORM_API_KEY", raising=False)
    from magi_agent.benchmarks.gaia.web_tools import build_web_tools

    result = build_web_tools(env={})
    assert result == []


def test_adapter_deep_research_orchestrator_none_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Adapter.deep_research_orchestrator is None unless MAGI_DEEP_WEB_RESEARCH_ENABLED=1."""
    from magi_agent.benchmarks.gaia.web_tools import _WebResearchBoundaryAdapter

    adapter = _WebResearchBoundaryAdapter(boundary=object())
    assert adapter.deep_research_orchestrator is None


def test_adapter_stores_orchestrator_when_provided() -> None:
    from magi_agent.benchmarks.gaia.web_tools import _WebResearchBoundaryAdapter
    from magi_agent.web_acquisition.deep_research import DeepWebResearchOrchestrator
    from magi_agent.web_acquisition.deep_research_config import DeepResearchConfig
    from magi_agent.web_acquisition.research_tools import LocalWebResearchToolBoundary

    boundary = LocalWebResearchToolBoundary()
    config = DeepResearchConfig(enabled=False)
    orch = DeepWebResearchOrchestrator(boundary=boundary, config=config)

    adapter = _WebResearchBoundaryAdapter(boundary=boundary, deep_research_orchestrator=orch)
    assert adapter.deep_research_orchestrator is orch


def test_deep_research_config_default_off_in_gaia_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """deep_research_config_from_env() returns enabled=False without the env var."""
    monkeypatch.delenv("MAGI_DEEP_WEB_RESEARCH_ENABLED", raising=False)
    from magi_agent.web_acquisition.deep_research_config import deep_research_config_from_env

    config = deep_research_config_from_env()
    assert config.enabled is False


def test_deep_research_config_enabled_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_DEEP_WEB_RESEARCH_ENABLED", "1")
    from magi_agent.web_acquisition.deep_research_config import deep_research_config_from_env

    config = deep_research_config_from_env()
    assert config.enabled is True
