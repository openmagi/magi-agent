"""Live-provider wiring for the native web tools (PR-2 / C10).

Builds on the frozen not-configured contract (PR #381) and the live-delegation
seam (PR-1). This module verifies the dedicated ``build_native_web_boundary``
factory: it reuses ``build_live_research_boundary`` but RELAXES the platform
precondition so jina/insane alone make the boundary live. When zero providers
are configured it returns ``None`` and the plugin falls back to the merged
not-configured error.

All tests are hermetic — no real network. The jina path is exercised through an
injected ``httpx.MockTransport``; the insane path through a fake session-like
provider. Any fixture key is assembled at runtime (never a literal) to satisfy
GH013 push protection.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from magi_agent.plugins.native.web import (
    WEB_RESEARCH_NOT_CONFIGURED_ERROR_CODE,
    build_native_web_boundary,
    web_fetch,
    web_search,
)
from magi_agent.tools.context import ToolContext
from magi_agent.tools.result import ToolResult

_LIVE_WEB_FLAGS = (
    "CORE_AGENT_PYTHON_LIVE_WEB_ACQUISITION_ENABLED",
    "CORE_AGENT_PYTHON_LIVE_WEB_ACQUISITION_KILL_SWITCH",
    "CORE_AGENT_PYTHON_WEB_PROVIDER_ROUTER_ENABLED",
    "CORE_AGENT_PYTHON_JINA_READER_ENABLED",
    "CORE_AGENT_PYTHON_INSANE_FETCH_ENABLED",
    "MAGI_JINA_API_KEY",
    "MAGI_PLATFORM_BASE_URL",
    "MAGI_PLATFORM_API_KEY",
)


def _runtime_fixture_key() -> str:
    # Assemble at runtime so no secret-shaped literal lands in the source (GH013).
    return "test" + "-" + "jina" + "-" + "key"


@pytest.fixture
def fresh_env() -> dict[str, str]:
    """An env mapping with every live-web flag/key unset (fresh install)."""
    return {}


@pytest.fixture
def jina_live_env() -> dict[str, str]:
    """Minimal live config that reaches a provider via jina alone (no platform)."""
    return {
        "CORE_AGENT_PYTHON_LIVE_WEB_ACQUISITION_ENABLED": "1",
        "CORE_AGENT_PYTHON_WEB_PROVIDER_ROUTER_ENABLED": "1",
        "CORE_AGENT_PYTHON_JINA_READER_ENABLED": "1",
        "MAGI_JINA_API_KEY": _runtime_fixture_key(),
    }


@pytest.fixture
def insane_live_env() -> dict[str, str]:
    """Minimal live config that reaches a provider via insane.fetch alone."""
    return {
        "CORE_AGENT_PYTHON_LIVE_WEB_ACQUISITION_ENABLED": "1",
        "CORE_AGENT_PYTHON_WEB_PROVIDER_ROUTER_ENABLED": "1",
        "CORE_AGENT_PYTHON_INSANE_FETCH_ENABLED": "1",
    }


def _context() -> ToolContext:
    return ToolContext(botId="test-bot", turnId="turn-1")


# ---------------------------------------------------------------------------
# build_native_web_boundary: relaxed platform precondition + None-on-empty
# ---------------------------------------------------------------------------


def test_native_boundary_none_on_fresh_install(fresh_env: dict[str, str]) -> None:
    """No live flags → no providers → None (plugin falls to not-configured error)."""
    assert build_native_web_boundary(fresh_env) is None


def test_native_boundary_none_when_master_gate_off(jina_live_env: dict[str, str]) -> None:
    env = dict(jina_live_env)
    env.pop("CORE_AGENT_PYTHON_LIVE_WEB_ACQUISITION_ENABLED")
    assert build_native_web_boundary(env) is None


def test_native_boundary_none_when_kill_switch_set(jina_live_env: dict[str, str]) -> None:
    env = dict(jina_live_env)
    env["CORE_AGENT_PYTHON_LIVE_WEB_ACQUISITION_KILL_SWITCH"] = "1"
    assert build_native_web_boundary(env) is None


def test_native_boundary_live_with_jina_alone_no_platform(jina_live_env: dict[str, str]) -> None:
    """RELAXED precondition: jina alone (no MAGI_PLATFORM_*) yields a live boundary."""
    boundary = build_native_web_boundary(jina_live_env)
    assert boundary is not None
    assert boundary._provider_router is not None
    assert boundary._provider_router.config.enabled is True
    assert "jina.reader" in boundary._provider_router.config.providers
    # Platform precondition relaxed: no platform providers present.
    assert "platform.fetch" not in boundary._provider_router.config.providers


def test_native_boundary_live_with_insane_alone(insane_live_env: dict[str, str]) -> None:
    boundary = build_native_web_boundary(insane_live_env)
    assert boundary is not None
    assert boundary._provider_router is not None
    assert "insane.fetch" in boundary._provider_router.config.providers


# ---------------------------------------------------------------------------
# Live path reached: jina via injected httpx.MockTransport (no network)
# ---------------------------------------------------------------------------


def _jina_mock_client(body: str) -> httpx.Client:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body)

    return httpx.Client(transport=httpx.MockTransport(_handler))


def test_web_fetch_reaches_live_jina_via_mock_transport(
    jina_live_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """web_fetch hits the live path; jina egress is served by a MockTransport."""
    from magi_agent.web_acquisition.providers.jina_reader import JinaReaderProvider

    # Inject a jina provider whose egress is a hermetic MockTransport. Expose a
    # fetch() alias so the 'fetch' operation reaches the same (mocked) reader.
    class _JinaFetchProvider(JinaReaderProvider):
        openmagi_live_provider = True

        def fetch(self, request: object) -> object:
            return self.reader(request)

    jina = _JinaFetchProvider(client=_jina_mock_client("mocked jina content"))

    real_build = build_native_web_boundary

    def _patched_build(env: object | None = None) -> object | None:
        boundary = real_build(env)
        if boundary is None:
            return None
        # Swap the configured jina.reader for our mock-backed instance.
        boundary._provider_router._providers["jina.reader"] = jina  # type: ignore[attr-defined]
        return boundary

    monkeypatch.setattr(
        "magi_agent.plugins.native.web.build_native_web_boundary", _patched_build
    )
    monkeypatch.setattr(
        "magi_agent.plugins.native.web.os.environ", jina_live_env, raising=False
    )

    result = asyncio.run(web_fetch({"url": "https://example.com/"}, _context()))

    assert isinstance(result, ToolResult)
    # Live path reached (not the not-configured error).
    assert result.error_code != WEB_RESEARCH_NOT_CONFIGURED_ERROR_CODE


# ---------------------------------------------------------------------------
# insane.fetch path: fake session-like provider, curl_cffi never imported
# ---------------------------------------------------------------------------


def test_web_fetch_reaches_live_insane_via_fake_provider(
    insane_live_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    class _InsaneFake:
        openmagi_live_provider = True

        def __init__(self) -> None:
            self.calls: list[str] = []

        def fetch(self, request: object) -> dict[str, object]:
            self.calls.append(getattr(request, "url", ""))
            return {
                "url": "https://docs.example.com/page",
                "title": "via insane.fetch",
                "content": "ok fake insane content",
            }

    fake = _InsaneFake()
    real_build = build_native_web_boundary

    def _patched_build(env: object | None = None) -> object | None:
        boundary = real_build(env)
        if boundary is None:
            return None
        boundary._provider_router._providers["insane.fetch"] = fake  # type: ignore[attr-defined]
        return boundary

    monkeypatch.setattr(
        "magi_agent.plugins.native.web.build_native_web_boundary", _patched_build
    )
    monkeypatch.setattr(
        "magi_agent.plugins.native.web.os.environ", insane_live_env, raising=False
    )

    result = asyncio.run(web_fetch({"url": "https://docs.example.com/page"}, _context()))

    assert result.status == "ok"
    assert fake.calls  # provider actually invoked → live path reached


# ---------------------------------------------------------------------------
# Regression: live flags OFF preserves the frozen #381 not-configured error
# ---------------------------------------------------------------------------


def test_web_search_off_preserves_frozen_error(
    fresh_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "magi_agent.plugins.native.web.os.environ", fresh_env, raising=False
    )
    result = asyncio.run(web_search({"query": "openmagi"}, _context()))
    assert result.status == "error"
    assert result.error_code == WEB_RESEARCH_NOT_CONFIGURED_ERROR_CODE


def test_web_fetch_off_preserves_frozen_error(
    fresh_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "magi_agent.plugins.native.web.os.environ", fresh_env, raising=False
    )
    result = asyncio.run(web_fetch({"url": "https://example.com/"}, _context()))
    assert result.status == "error"
    assert result.error_code == WEB_RESEARCH_NOT_CONFIGURED_ERROR_CODE
