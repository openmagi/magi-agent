"""Tests for the updated build_web_tools() in the GAIA harness (PR-D).

All tests are hermetic — no live network, no Composio.
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Path A: platform endpoint router
# ---------------------------------------------------------------------------


def test_build_web_tools_returns_adapter_when_platform_keys_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When MAGI_PLATFORM_BASE_URL + MAGI_PLATFORM_API_KEY are set, return adapter."""
    import magi_agent.benchmarks.gaia.web_tools as _mod

    # Stub build_live_research_boundary so no real network is involved.
    class _FakeBoundary:
        pass

    def _fake_build(env: object) -> _FakeBoundary:
        return _FakeBoundary()

    monkeypatch.setattr(
        "magi_agent.web_acquisition.research_tools.build_live_research_boundary",
        _fake_build,
    )

    env = {
        "MAGI_PLATFORM_BASE_URL": "https://platform.example.com",
        "MAGI_PLATFORM_API_KEY": "test-key",
    }
    result = _mod.build_web_tools(env=env)
    assert len(result) == 1
    assert hasattr(result[0], "boundary")


def test_build_web_tools_returns_empty_without_platform_keys() -> None:
    """When no platform keys are set and Composio is disabled, return []."""
    from magi_agent.benchmarks.gaia.web_tools import build_web_tools

    result = build_web_tools(env={})
    assert result == []


def test_build_web_tools_platform_boundary_exception_falls_back_to_composio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the platform path raises, fall through to the Composio path gracefully."""
    import magi_agent.benchmarks.gaia.web_tools as _mod

    def _raises(env: object) -> None:
        raise RuntimeError("simulated platform boundary failure")

    monkeypatch.setattr(
        "magi_agent.web_acquisition.research_tools.build_live_research_boundary",
        _raises,
    )
    # Composio is disabled → result should be [].
    env = {
        "MAGI_PLATFORM_BASE_URL": "https://platform.example.com",
        "MAGI_PLATFORM_API_KEY": "test-key",
    }
    result = _mod.build_web_tools(env=env)
    assert result == []


# ---------------------------------------------------------------------------
# Path B: legacy Composio fallback (existing tests must still pass)
# ---------------------------------------------------------------------------


def test_disabled_returns_empty() -> None:
    from magi_agent.benchmarks.gaia.web_tools import build_web_tools

    assert build_web_tools(env={}) == []


def test_enabled_without_key_returns_empty() -> None:
    from magi_agent.benchmarks.gaia.web_tools import build_web_tools

    assert build_web_tools(env={"MAGI_COMPOSIO_ENABLED": "1"}) == []


def test_factory_raises_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """If resolve_composio_config raises, build_web_tools returns []."""
    import magi_agent.benchmarks.gaia.web_tools as _mod

    def _boom(env: object) -> None:
        raise RuntimeError("simulated config failure")

    monkeypatch.setattr(_mod, "_resolve_composio_config", _boom)
    result = _mod.build_web_tools(env={"MAGI_COMPOSIO_ENABLED": "1"})
    assert result == []


# ---------------------------------------------------------------------------
# Adapter shape
# ---------------------------------------------------------------------------


def test_web_research_boundary_adapter_has_boundary_attr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import magi_agent.benchmarks.gaia.web_tools as _mod

    class _FakeBoundary:
        pass

    monkeypatch.setattr(
        "magi_agent.web_acquisition.research_tools.build_live_research_boundary",
        lambda env: _FakeBoundary(),
    )

    env = {
        "MAGI_PLATFORM_BASE_URL": "https://platform.example.com",
        "MAGI_PLATFORM_API_KEY": "test-key",
    }
    result = _mod.build_web_tools(env=env)
    assert len(result) == 1
    adapter = result[0]
    assert isinstance(adapter.boundary, _FakeBoundary)
