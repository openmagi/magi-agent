"""Integration tests for jina.reader and insane.fetch wiring in build_live_research_boundary.

All tests are hermetic — no live network.  Provider instances are either fake
in-process objects or real provider instances with injected session/client seams
so that curl_cffi / httpx are never hit.
"""

from __future__ import annotations

import asyncio


# ---------------------------------------------------------------------------
# Helpers: fake providers
# ---------------------------------------------------------------------------


class _FetchTimeoutProvider:
    """Fake provider that always times out."""

    openmagi_live_provider = True

    def search(self, request: object) -> dict[str, object]:
        return {"status": "timeout"}

    def fetch(self, request: object) -> dict[str, object]:
        return {"status": "timeout"}

    def reader(self, request: object) -> dict[str, object]:
        return {"status": "timeout"}


def _run(coro: object) -> object:
    return asyncio.run(coro)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Wiring/registration: both flags on + platform keys → all providers registered
# ---------------------------------------------------------------------------


def test_build_live_research_boundary_registers_jina_and_insane_when_enabled() -> None:
    """When both enable flags are set, jina.reader and insane.fetch appear in the router."""
    from magi_agent.web_acquisition.research_tools import (
        INSANE_FETCH_ENABLED_ENV,
        INSANE_FETCH_PROVIDER_NAME,
        JINA_READER_ENABLED_ENV,
        JINA_READER_PROVIDER_NAME,
        PLATFORM_FETCH_PROVIDER_NAME,
        PLATFORM_SEARCH_PROVIDER_NAME,
        PROVIDER_ROUTER_ENABLED_ENV,
        build_live_research_boundary,
    )
    from magi_agent.web_acquisition.provider_router import WebAcquisitionProviderRouter

    env = {
        "CORE_AGENT_PYTHON_LIVE_WEB_ACQUISITION_ENABLED": "1",
        PROVIDER_ROUTER_ENABLED_ENV: "1",
        "MAGI_PLATFORM_BASE_URL": "https://platform.example.com",
        "MAGI_PLATFORM_API_KEY": "test-key",
        INSANE_FETCH_ENABLED_ENV: "1",
        JINA_READER_ENABLED_ENV: "1",
    }

    boundary = build_live_research_boundary(env=env)
    assert boundary._provider_router is not None
    assert isinstance(boundary._provider_router, WebAcquisitionProviderRouter)
    assert boundary._provider_router.config.enabled is True

    router_providers = boundary._provider_router.config.providers
    pack_allowlist = set(boundary._live_pack.config.provider_allowlist)  # type: ignore[attr-defined]

    # Both new providers appear in router providers.
    assert JINA_READER_PROVIDER_NAME in router_providers
    assert INSANE_FETCH_PROVIDER_NAME in router_providers

    # Both new providers appear in the pack allowlist.
    assert JINA_READER_PROVIDER_NAME in pack_allowlist
    assert INSANE_FETCH_PROVIDER_NAME in pack_allowlist

    # Platform providers still present.
    assert PLATFORM_SEARCH_PROVIDER_NAME in router_providers
    assert PLATFORM_FETCH_PROVIDER_NAME in router_providers
    assert PLATFORM_SEARCH_PROVIDER_NAME in pack_allowlist
    assert PLATFORM_FETCH_PROVIDER_NAME in pack_allowlist


def test_build_live_research_boundary_ordering_platform_then_insane_then_jina() -> None:
    """Provider order must be: platform names first, then insane.fetch, then jina.reader."""
    from magi_agent.web_acquisition.research_tools import (
        INSANE_FETCH_ENABLED_ENV,
        INSANE_FETCH_PROVIDER_NAME,
        JINA_READER_ENABLED_ENV,
        JINA_READER_PROVIDER_NAME,
        PLATFORM_FETCH_PROVIDER_NAME,
        PLATFORM_SEARCH_PROVIDER_NAME,
        PROVIDER_ROUTER_ENABLED_ENV,
        build_live_research_boundary,
    )

    env = {
        PROVIDER_ROUTER_ENABLED_ENV: "1",
        "MAGI_PLATFORM_BASE_URL": "https://platform.example.com",
        "MAGI_PLATFORM_API_KEY": "test-key",
        INSANE_FETCH_ENABLED_ENV: "1",
        JINA_READER_ENABLED_ENV: "1",
    }

    boundary = build_live_research_boundary(env=env)
    assert boundary._provider_router is not None

    providers = list(boundary._provider_router.config.providers)

    # Platform names must come first.
    platform_indices = [
        providers.index(PLATFORM_SEARCH_PROVIDER_NAME),
        providers.index(PLATFORM_FETCH_PROVIDER_NAME),
    ]
    insane_index = providers.index(INSANE_FETCH_PROVIDER_NAME)
    jina_index = providers.index(JINA_READER_PROVIDER_NAME)

    assert max(platform_indices) < insane_index, (
        f"insane.fetch ({insane_index}) must come after all platform providers ({platform_indices})"
    )
    assert insane_index < jina_index, (
        f"insane.fetch ({insane_index}) must come before jina.reader ({jina_index})"
    )


# ---------------------------------------------------------------------------
# Default-OFF: neither flag set → providers absent
# ---------------------------------------------------------------------------


def test_build_live_research_boundary_jina_absent_when_flag_off() -> None:
    """jina.reader must NOT appear in router/allowlist when its enable flag is unset."""
    from magi_agent.web_acquisition.research_tools import (
        JINA_READER_PROVIDER_NAME,
        PROVIDER_ROUTER_ENABLED_ENV,
        build_live_research_boundary,
    )

    env = {
        PROVIDER_ROUTER_ENABLED_ENV: "1",
        "MAGI_PLATFORM_BASE_URL": "https://platform.example.com",
        "MAGI_PLATFORM_API_KEY": "test-key",
        # JINA_READER_ENABLED_ENV intentionally absent
    }

    boundary = build_live_research_boundary(env=env)
    assert boundary._provider_router is not None
    assert JINA_READER_PROVIDER_NAME not in boundary._provider_router.config.providers
    if boundary._live_pack is not None:
        assert JINA_READER_PROVIDER_NAME not in boundary._live_pack.config.provider_allowlist  # type: ignore[attr-defined]


def test_build_live_research_boundary_insane_absent_when_flag_off() -> None:
    """insane.fetch must NOT appear in router/allowlist when its enable flag is unset."""
    from magi_agent.web_acquisition.research_tools import (
        INSANE_FETCH_PROVIDER_NAME,
        PROVIDER_ROUTER_ENABLED_ENV,
        build_live_research_boundary,
    )

    env = {
        PROVIDER_ROUTER_ENABLED_ENV: "1",
        "MAGI_PLATFORM_BASE_URL": "https://platform.example.com",
        "MAGI_PLATFORM_API_KEY": "test-key",
        # INSANE_FETCH_ENABLED_ENV intentionally absent
    }

    boundary = build_live_research_boundary(env=env)
    assert boundary._provider_router is not None
    assert INSANE_FETCH_PROVIDER_NAME not in boundary._provider_router.config.providers
    if boundary._live_pack is not None:
        assert INSANE_FETCH_PROVIDER_NAME not in boundary._live_pack.config.provider_allowlist  # type: ignore[attr-defined]


def test_build_live_research_boundary_both_absent_when_no_flags() -> None:
    """Neither new provider must appear when their flags are unset (default-OFF guarantee)."""
    from magi_agent.web_acquisition.research_tools import (
        INSANE_FETCH_PROVIDER_NAME,
        JINA_READER_PROVIDER_NAME,
        PROVIDER_ROUTER_ENABLED_ENV,
        build_live_research_boundary,
    )

    env = {
        PROVIDER_ROUTER_ENABLED_ENV: "1",
        "MAGI_PLATFORM_BASE_URL": "https://platform.example.com",
        "MAGI_PLATFORM_API_KEY": "test-key",
    }

    boundary = build_live_research_boundary(env=env)
    assert boundary._provider_router is not None
    router_providers = boundary._provider_router.config.providers
    assert JINA_READER_PROVIDER_NAME not in router_providers
    assert INSANE_FETCH_PROVIDER_NAME not in router_providers
    if boundary._live_pack is not None:
        allowlist = boundary._live_pack.config.provider_allowlist  # type: ignore[attr-defined]
        assert JINA_READER_PROVIDER_NAME not in allowlist
        assert INSANE_FETCH_PROVIDER_NAME not in allowlist


# ---------------------------------------------------------------------------
# Fallback-chain integration: fake platform times out → insane.fetch succeeds
# ---------------------------------------------------------------------------


def _build_boundary_with_providers(
    providers: dict[str, object],
    provider_names: list[str],
    *,
    env: dict[str, str] | None = None,
) -> object:
    """Build a LocalWebResearchToolBoundary with the given ordered providers dict."""
    from magi_agent.web_acquisition.live_provider_pack import (
        LiveWebAcquisitionPackConfig,
        LiveWebAcquisitionProviderPack,
    )
    from magi_agent.web_acquisition.provider_router import (
        ProviderRouterConfig,
        WebAcquisitionProviderRouter,
    )
    from magi_agent.web_acquisition.research_tools import LocalWebResearchToolBoundary

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


def test_fallback_from_platform_to_insane_fetch() -> None:
    """When the primary fetch provider times out, the router falls back to insane.fetch.

    Uses a pure fake provider in the 'insane.fetch' slot — consistent with the
    existing router test pattern (test_web_provider_router.py) which avoids
    hitting live DNS/network by design.  The real InsaneFetchProvider is tested
    for its network / DNS behaviour in test_web_insane_fetch_provider.py.
    """

    class _InsaneFetchFake:
        """Fake that represents a successful insane.fetch response."""

        openmagi_live_provider = True

        def fetch(self, request: object) -> dict[str, object]:
            return {
                "url": "https://docs.example.com/page",
                "title": "Fallback via insane.fetch",
                "content": "ok insane fallback content",
            }

    provider_names = ["platform.fetch", "insane.fetch"]
    providers: dict[str, object] = {
        "platform.fetch": _FetchTimeoutProvider(),
        "insane.fetch": _InsaneFetchFake(),
    }

    boundary = _build_boundary_with_providers(providers, provider_names)
    result = _run(boundary.execute_tool("WebFetch", {"url": "https://docs.example.com/page"}))  # type: ignore[arg-type]

    assert result.status == "ok"


# ---------------------------------------------------------------------------
# Constants are exported in __all__
# ---------------------------------------------------------------------------


def test_new_constants_in_all() -> None:
    """New provider constants must be exported via __all__."""
    import magi_agent.web_acquisition.research_tools as rt

    for name in (
        "JINA_READER_PROVIDER_NAME",
        "JINA_READER_ENABLED_ENV",
        "MAGI_JINA_API_KEY_ENV",
        "INSANE_FETCH_PROVIDER_NAME",
        "INSANE_FETCH_ENABLED_ENV",
    ):
        assert name in rt.__all__, f"{name!r} missing from research_tools.__all__"
        assert hasattr(rt, name), f"{name!r} not defined in research_tools"
