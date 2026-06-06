"""Integration tests for LocalWebResearchToolBoundary wired through ProviderRouter (PR-C).

All tests are hermetic — no live network. Providers are in-process fakes.
"""

from __future__ import annotations

import asyncio


# ---------------------------------------------------------------------------
# Helpers: fake providers, configs, router
# ---------------------------------------------------------------------------


class _OkSearchProvider:
    openmagi_live_provider = True

    def search(self, request: object) -> dict[str, object]:
        return {
            "results": [
                {
                    "url": "https://docs.example.com/search-result",
                    "title": "Search Result",
                    "snippet": "OK search snippet.",
                }
            ]
        }

    def fetch(self, request: object) -> dict[str, object]:
        return {
            "url": "https://docs.example.com/fetch-result",
            "title": "Fetched Page",
            "content": "OK fetched content.",
        }

    def reader(self, request: object) -> dict[str, object]:
        return {
            "url": "https://docs.example.com/reader-result",
            "title": "Reader Page",
            "content": "OK reader content.",
        }


class _TimeoutProvider:
    openmagi_live_provider = True

    def search(self, request: object) -> dict[str, object]:
        return {"status": "timeout"}

    def fetch(self, request: object) -> dict[str, object]:
        return {"status": "timeout"}

    def reader(self, request: object) -> dict[str, object]:
        return {"status": "timeout"}


def _build_boundary_with_router(
    primary: object,
    fallback: object | None = None,
    *,
    env: dict[str, str] | None = None,
) -> object:
    """Build a LocalWebResearchToolBoundary wired through a ProviderRouter."""
    from magi_agent.web_acquisition.live_provider_pack import (
        LiveWebAcquisitionPackConfig,
        LiveWebAcquisitionProviderPack,
    )
    from magi_agent.web_acquisition.provider_router import (
        ProviderRouterConfig,
        WebAcquisitionProviderRouter,
    )
    from magi_agent.web_acquisition.research_tools import LocalWebResearchToolBoundary

    provider_names: list[str] = ["primary"]
    providers: dict[str, object] = {"primary": primary}
    if fallback is not None:
        provider_names.append("fallback")
        providers["fallback"] = fallback

    pack_config = LiveWebAcquisitionPackConfig(
        enabled=True,
        liveNetworkEnabled=True,
        providerAllowlist=tuple(provider_names),
    )
    router_config = ProviderRouterConfig(
        enabled=True,
        providers=tuple(provider_names),
        base_retry_delay_ms=0,
        max_retry_delay_ms=0,
    )
    pack = LiveWebAcquisitionProviderPack(pack_config)
    router = WebAcquisitionProviderRouter(pack=pack, config=router_config, providers=providers)

    live_env = {
        "CORE_AGENT_PYTHON_LIVE_WEB_ACQUISITION_ENABLED": "1",
        **(env or {}),
    }
    return LocalWebResearchToolBoundary(
        live_pack=pack,
        provider_router=router,
        env=live_env,
    )


def _run(coro: object) -> object:
    return asyncio.get_event_loop().run_until_complete(coro)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Integration: router wired through boundary → WebSearch ok
# ---------------------------------------------------------------------------


def test_boundary_with_router_websearch_returns_ok() -> None:
    boundary = _build_boundary_with_router(_OkSearchProvider())
    result = _run(boundary.execute_tool("WebSearch", {"query": "test query"}))

    assert result.status == "ok"
    assert result.output is not None
    output = result.output
    assert isinstance(output, dict)
    assert "sources" in output
    assert len(output["sources"]) > 0


def test_boundary_with_router_webfetch_returns_ok() -> None:
    boundary = _build_boundary_with_router(_OkSearchProvider())
    result = _run(
        boundary.execute_tool("WebFetch", {"url": "https://docs.example.com/page"})
    )

    assert result.status == "ok"


def test_boundary_with_router_webreader_returns_ok() -> None:
    """WebReader is a live-only tool; it must route through the live path."""
    boundary = _build_boundary_with_router(_OkSearchProvider())
    result = _run(
        boundary.execute_tool("WebReader", {"url": "https://docs.example.com/page"})
    )

    assert result.status == "ok"


# ---------------------------------------------------------------------------
# Integration: platform fails → router falls to fallback → ok
# ---------------------------------------------------------------------------


def test_boundary_router_falls_to_fallback_on_primary_timeout() -> None:
    boundary = _build_boundary_with_router(_TimeoutProvider(), _OkSearchProvider())
    result = _run(boundary.execute_tool("WebSearch", {"query": "fallback test"}))

    assert result.status == "ok"


# ---------------------------------------------------------------------------
# Integration: both fail → tool returns error
# ---------------------------------------------------------------------------


def test_boundary_router_both_fail_returns_error() -> None:
    boundary = _build_boundary_with_router(_TimeoutProvider(), _TimeoutProvider())
    result = _run(boundary.execute_tool("WebSearch", {"query": "both fail"}))

    assert result.status == "error"
    assert result.error_code == "all_providers_exhausted"


# ---------------------------------------------------------------------------
# WebReader blocked when live gate is off (no live path wired)
# ---------------------------------------------------------------------------


def test_webreader_blocked_when_live_gate_off() -> None:
    from magi_agent.web_acquisition.research_tools import LocalWebResearchToolBoundary

    # No live_pack, no provider_router, live env gate off.
    boundary = LocalWebResearchToolBoundary(env={})
    result = _run(boundary.execute_tool("WebReader", {"url": "https://docs.example.com/page"}))

    assert result.status == "blocked"
    assert result.error_code in {
        "web_research_live_required_for_reader",
        "web_research_tool_not_supported",
    }


# ---------------------------------------------------------------------------
# Existing WebSearch/WebFetch still works without router (legacy path)
# ---------------------------------------------------------------------------


def test_legacy_live_path_websearch_still_works() -> None:
    """The direct live_provider path (without router) must continue to work.

    The providerName in the request is derived from OPERATION_TO_PROVIDER_NAME
    which maps "search" → "web.search".  The pack's allowlist must include that
    name for the request to pass the allowlist gate.
    """
    from magi_agent.web_acquisition.live_provider_pack import (
        OPERATION_TO_PROVIDER_NAME,
        LiveWebAcquisitionPackConfig,
        LiveWebAcquisitionProviderPack,
    )
    from magi_agent.web_acquisition.research_tools import LocalWebResearchToolBoundary

    # Use the actual provider name that _live_request_from_tool will mint.
    search_provider_name = OPERATION_TO_PROVIDER_NAME["search"]  # "web.search"

    pack = LiveWebAcquisitionProviderPack(
        LiveWebAcquisitionPackConfig(
            enabled=True,
            liveNetworkEnabled=True,
            providerAllowlist=(search_provider_name,),
        )
    )

    class _DirectProvider:
        openmagi_live_provider = True

        def search(self, request: object) -> dict[str, object]:
            return {
                "results": [
                    {
                        "url": "https://docs.example.com/direct",
                        "title": "Direct Result",
                        "snippet": "Direct provider snippet.",
                    }
                ]
            }

        def fetch(self, request: object) -> dict[str, object]:
            return {"url": "https://docs.example.com/direct", "content": "direct content"}

    boundary = LocalWebResearchToolBoundary(
        live_pack=pack,
        live_provider=_DirectProvider(),
        env={"CORE_AGENT_PYTHON_LIVE_WEB_ACQUISITION_ENABLED": "1"},
    )

    result = _run(boundary.execute_tool("WebSearch", {"query": "direct test"}))
    assert result.status == "ok"


# ---------------------------------------------------------------------------
# build_live_research_boundary factory
# ---------------------------------------------------------------------------


def test_build_live_research_boundary_no_env_returns_boundary() -> None:
    """Factory must return a boundary even when no platform vars are set."""
    from magi_agent.web_acquisition.research_tools import build_live_research_boundary

    boundary = build_live_research_boundary(env={})
    assert boundary is not None


def test_build_live_research_boundary_disabled_router_when_no_platform_keys() -> None:
    """Without MAGI_PLATFORM_BASE_URL / MAGI_PLATFORM_API_KEY, no router is built."""
    from magi_agent.web_acquisition.research_tools import build_live_research_boundary

    boundary = build_live_research_boundary(env={})
    # _provider_router should be None (not configured).
    assert boundary._provider_router is None


def test_build_live_research_boundary_with_platform_keys_creates_router() -> None:
    """With both platform env vars, the factory wires up a provider router."""
    import httpx
    from magi_agent.web_acquisition.research_tools import (
        PROVIDER_ROUTER_ENABLED_ENV,
        build_live_research_boundary,
    )
    from magi_agent.web_acquisition.provider_router import WebAcquisitionProviderRouter

    env = {
        "MAGI_PLATFORM_BASE_URL": "https://platform.example.com",
        "MAGI_PLATFORM_API_KEY": "test-api-key",
        PROVIDER_ROUTER_ENABLED_ENV: "1",
    }
    boundary = build_live_research_boundary(env=env)
    assert isinstance(boundary._provider_router, WebAcquisitionProviderRouter)
    assert boundary._provider_router.config.enabled is True


def test_build_live_research_boundary_router_disabled_without_flag() -> None:
    """Router requires CORE_AGENT_PYTHON_WEB_PROVIDER_ROUTER_ENABLED=1 to be active."""
    from magi_agent.web_acquisition.research_tools import build_live_research_boundary
    from magi_agent.web_acquisition.provider_router import WebAcquisitionProviderRouter

    env = {
        "MAGI_PLATFORM_BASE_URL": "https://platform.example.com",
        "MAGI_PLATFORM_API_KEY": "test-api-key",
        # PROVIDER_ROUTER_ENABLED_ENV intentionally omitted
    }
    boundary = build_live_research_boundary(env=env)
    # Router may be constructed but disabled.
    if boundary._provider_router is not None:
        assert boundary._provider_router.config.enabled is False
