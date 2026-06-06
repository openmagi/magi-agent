from __future__ import annotations

import pytest

from magi_agent.benchmarks.gaia.web_tools import build_web_tools


def test_disabled_returns_empty() -> None:
    assert build_web_tools(env={}) == []


def test_enabled_without_key_returns_empty() -> None:
    assert build_web_tools(env={"MAGI_COMPOSIO_ENABLED": "1"}) == []


def test_factory_raises_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """If resolve_composio_config raises for any reason, build_web_tools returns []."""
    import magi_agent.benchmarks.gaia.web_tools as _mod

    def _boom(env: object) -> None:
        raise RuntimeError("simulated config failure")

    monkeypatch.setattr(_mod, "_resolve_composio_config", _boom)
    result = build_web_tools(env={"MAGI_COMPOSIO_ENABLED": "1"})
    assert result == []
