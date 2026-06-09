"""Tests for DeepResearchConfig — PR1 (TDD: these were written first)."""

from __future__ import annotations

import pytest

from magi_agent.web_acquisition.deep_research_config import (
    DeepResearchConfig,
    deep_research_config_from_env,
)


def test_deep_research_config_defaults_off() -> None:
    config = DeepResearchConfig()
    assert config.enabled is False


def test_deep_research_config_defaults_reasonable() -> None:
    config = DeepResearchConfig()
    assert config.max_queries == 3
    assert config.max_fetch_per_query == 3
    assert config.max_iterations == 2
    assert config.min_sources_for_cross_verify == 2
    assert config.fetch_timeout_s == 30.0
    assert config.cross_verify_required is True
    assert config.navigate_sections is True


def test_deep_research_config_enabled_explicit() -> None:
    config = DeepResearchConfig(enabled=True)
    assert config.enabled is True


def test_deep_research_config_is_frozen() -> None:
    config = DeepResearchConfig()
    with pytest.raises(Exception):
        config.enabled = True  # type: ignore[misc]


def test_deep_research_config_rejects_extra_fields() -> None:
    with pytest.raises(Exception):
        DeepResearchConfig(enabled=True, unknown_field="bad")  # type: ignore[call-arg]


def test_deep_research_config_max_queries_bounds() -> None:
    # ge=1
    with pytest.raises(Exception):
        DeepResearchConfig(max_queries=0)
    # le=8
    with pytest.raises(Exception):
        DeepResearchConfig(max_queries=9)
    # valid bounds
    assert DeepResearchConfig(max_queries=1).max_queries == 1
    assert DeepResearchConfig(max_queries=8).max_queries == 8


def test_deep_research_config_from_env_defaults_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAGI_DEEP_WEB_RESEARCH_ENABLED", raising=False)
    config = deep_research_config_from_env()
    assert config.enabled is False


def test_deep_research_config_from_env_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_DEEP_WEB_RESEARCH_ENABLED", "1")
    config = deep_research_config_from_env()
    assert config.enabled is True


def test_deep_research_config_from_env_max_queries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_DEEP_WEB_RESEARCH_MAX_QUERIES", "5")
    config = deep_research_config_from_env()
    assert config.max_queries == 5


def test_deep_research_config_from_env_max_queries_clamped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_DEEP_WEB_RESEARCH_MAX_QUERIES", "99")
    config = deep_research_config_from_env()
    assert config.max_queries == 8  # clamped to max


def test_deep_research_config_from_env_cross_verify_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_DEEP_WEB_RESEARCH_CROSS_VERIFY", "0")
    config = deep_research_config_from_env()
    assert config.cross_verify_required is False
