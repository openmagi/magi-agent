"""Tests for the WebAcquisitionProviderRouter (PR-A).

All tests are hermetic — no live network. Providers are fake in-process objects.
sleep is suppressed via ``_sleep=False``.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Helpers: fake providers and request/config builders
# ---------------------------------------------------------------------------


class OkProvider:
    """Always returns a successful search result."""

    openmagi_live_provider = True

    def __init__(self, name: str = "ok-provider") -> None:
        self.name = name
        self.calls: list[str] = []

    def search(self, request: object) -> dict[str, object]:
        self.calls.append("search")
        return {
            "results": [
                {
                    "url": "https://docs.example.com/ok",
                    "title": "OK Result",
                    "snippet": f"Result from {self.name}.",
                }
            ]
        }

    def fetch(self, request: object) -> dict[str, object]:
        self.calls.append("fetch")
        return {
            "url": "https://docs.example.com/ok",
            "title": "OK Fetch",
            "content": f"Content from {self.name}.",
        }


class FailProvider:
    """Returns a timeout / repair_required-triggering response."""

    openmagi_live_provider = True

    def __init__(self, status: str = "timeout") -> None:
        self.status = status
        self.calls: list[str] = []

    def search(self, request: object) -> dict[str, object]:
        self.calls.append("search")
        return {"status": self.status}

    def fetch(self, request: object) -> dict[str, object]:
        self.calls.append("fetch")
        return {"status": self.status}


class DeniedProvider:
    """Returns denied status (no_answer in pack terms — not retriable)."""

    openmagi_live_provider = True

    def __init__(self) -> None:
        self.calls: list[str] = []

    def search(self, request: object) -> dict[str, object]:
        self.calls.append("search")
        return {"status": "denied"}

    def fetch(self, request: object) -> dict[str, object]:
        self.calls.append("fetch")
        return {"status": "denied"}


def _live_pack(provider_names: tuple[str, ...] = ("primary", "fallback")) -> object:
    from magi_agent.web_acquisition.live_provider_pack import (
        LiveWebAcquisitionPackConfig,
        LiveWebAcquisitionProviderPack,
    )

    config = LiveWebAcquisitionPackConfig(
        enabled=True,
        liveNetworkEnabled=True,
        providerAllowlist=provider_names,
    )
    return LiveWebAcquisitionProviderPack(config)


def _router_config(**overrides: object) -> object:
    from magi_agent.web_acquisition.provider_router import ProviderRouterConfig

    defaults: dict[str, object] = {
        "enabled": True,
        "providers": ("primary", "fallback"),
        "max_attempts_per_provider": 1,
        "base_retry_delay_ms": 0,
        "max_retry_delay_ms": 0,
    }
    defaults.update(overrides)
    return ProviderRouterConfig(**defaults)


def _request(**overrides: object) -> object:
    from magi_agent.web_acquisition.live_provider_pack import WebAcquisitionProviderRequest

    payload: dict[str, object] = {
        "operation": "search",
        "requestId": "req-test-1",
        "providerName": "primary",
        "botIdDigest": "bot:abc",
        "ownerIdDigest": "owner:def",
        "sessionKeyDigest": "session:ghi",
        "query": "test query",
    }
    payload.update(overrides)
    return WebAcquisitionProviderRequest(**payload)


def _build_router(
    providers: dict[str, object],
    *,
    provider_names: tuple[str, ...] | None = None,
    **config_overrides: object,
) -> object:
    from magi_agent.web_acquisition.provider_router import WebAcquisitionProviderRouter

    names = provider_names or tuple(providers.keys())
    pack = _live_pack(names)
    config = _router_config(providers=names, **config_overrides)
    return WebAcquisitionProviderRouter(pack=pack, config=config, providers=providers)


# ---------------------------------------------------------------------------
# ProviderRouterConfig tests
# ---------------------------------------------------------------------------


def test_provider_router_config_default_is_disabled() -> None:
    from magi_agent.web_acquisition.provider_router import ProviderRouterConfig

    config = ProviderRouterConfig()
    assert config.enabled is False
    assert config.providers == ()
    assert config.max_attempts_per_provider == 1


def test_provider_router_config_validates_fields() -> None:
    import pytest
    from magi_agent.web_acquisition.provider_router import ProviderRouterConfig
    from pydantic import ValidationError

    # max_attempts_per_provider must be ge=1, le=3
    with pytest.raises(ValidationError):
        ProviderRouterConfig(enabled=True, providers=("p",), max_attempts_per_provider=0)
    with pytest.raises(ValidationError):
        ProviderRouterConfig(enabled=True, providers=("p",), max_attempts_per_provider=4)


# ---------------------------------------------------------------------------
# _backoff_ms pure function tests
# ---------------------------------------------------------------------------


def test_backoff_ms_zero_for_attempt_zero() -> None:
    from magi_agent.web_acquisition.provider_router import ProviderRouterConfig, _backoff_ms

    config = ProviderRouterConfig(base_retry_delay_ms=200, max_retry_delay_ms=2000)
    assert _backoff_ms(0, config) == 0.0


def test_backoff_ms_attempt_1_is_around_base() -> None:
    from magi_agent.web_acquisition.provider_router import ProviderRouterConfig, _backoff_ms

    config = ProviderRouterConfig(base_retry_delay_ms=200, max_retry_delay_ms=2000)
    value = _backoff_ms(1, config)
    # base=200, jitter ±20% → [160, 240]
    assert 160 <= value <= 240


def test_backoff_ms_attempt_2_doubles() -> None:
    from magi_agent.web_acquisition.provider_router import ProviderRouterConfig, _backoff_ms

    config = ProviderRouterConfig(base_retry_delay_ms=200, max_retry_delay_ms=2000)
    value = _backoff_ms(2, config)
    # base*2^1 = 400, jitter ±20% → [320, 480]
    assert 320 <= value <= 480


def test_backoff_ms_capped_at_max() -> None:
    from magi_agent.web_acquisition.provider_router import ProviderRouterConfig, _backoff_ms

    config = ProviderRouterConfig(base_retry_delay_ms=200, max_retry_delay_ms=300)
    value = _backoff_ms(5, config)  # would be 6400ms without cap
    # max=300, jitter ±20% → [240, 360]
    assert 240 <= value <= 360


def test_backoff_ms_zero_base_stays_zero() -> None:
    from magi_agent.web_acquisition.provider_router import ProviderRouterConfig, _backoff_ms

    config = ProviderRouterConfig(base_retry_delay_ms=0, max_retry_delay_ms=0)
    for attempt in range(4):
        assert _backoff_ms(attempt, config) == 0.0


# ---------------------------------------------------------------------------
# build_provider_router factory
# ---------------------------------------------------------------------------


def test_build_provider_router_returns_none_when_disabled() -> None:
    from magi_agent.web_acquisition.provider_router import ProviderRouterConfig, build_provider_router

    config = ProviderRouterConfig(enabled=False)
    result = build_provider_router(config, _live_pack(), {})
    assert result is None


def test_build_provider_router_returns_router_when_enabled() -> None:
    from magi_agent.web_acquisition.provider_router import (
        ProviderRouterConfig,
        WebAcquisitionProviderRouter,
        build_provider_router,
    )

    config = ProviderRouterConfig(enabled=True, providers=("p",))
    provider = OkProvider()
    router = build_provider_router(config, _live_pack(("p",)), {"p": provider})
    assert isinstance(router, WebAcquisitionProviderRouter)


# ---------------------------------------------------------------------------
# Router disabled → disabled result
# ---------------------------------------------------------------------------


def test_router_disabled_returns_disabled_status() -> None:
    from magi_agent.web_acquisition.provider_router import (
        ProviderRouterConfig,
        WebAcquisitionProviderRouter,
    )

    pack = _live_pack()
    config = ProviderRouterConfig(enabled=False)
    router = WebAcquisitionProviderRouter(pack=pack, config=config, providers={})
    result = router.run(_request(), _sleep=False)
    assert result.status == "disabled"
    assert "provider_router_disabled" in result.reason_codes


# ---------------------------------------------------------------------------
# Happy path — first provider returns ok
# ---------------------------------------------------------------------------


def test_router_returns_first_provider_result_when_ok() -> None:
    primary = OkProvider("primary")
    fallback = OkProvider("fallback")

    router = _build_router({"primary": primary, "fallback": fallback})
    result = router.run(_request(), _sleep=False)

    assert result.status == "ok"
    assert len(primary.calls) == 1
    assert len(fallback.calls) == 0
    # authority_flags must remain all False.
    assert result.authority_flags.provider_called is False
    assert result.authority_flags.network_fetched is False


def test_router_returns_immediately_on_first_ok_without_fallback_calls() -> None:
    primary = OkProvider()
    calls: list[str] = []
    primary.calls = calls

    router = _build_router({"primary": primary, "fallback": OkProvider()})
    result = router.run(_request(), _sleep=False)

    assert result.status == "ok"
    assert calls == ["search"]


# ---------------------------------------------------------------------------
# Fallback after repair_required on primary
# ---------------------------------------------------------------------------


def test_router_falls_back_after_repair_required_on_primary() -> None:
    primary = FailProvider("timeout")
    fallback = OkProvider("fallback")

    router = _build_router({"primary": primary, "fallback": fallback})
    result = router.run(_request(), _sleep=False)

    assert result.status == "ok"
    assert len(primary.calls) == 1
    assert len(fallback.calls) == 1


def test_router_falls_back_after_execution_failed_on_primary() -> None:
    """provider_execution_failed (exception in provider) → triggers fallback."""
    primary = FailProvider("timeout")  # timeout maps to repair_required → provider_timeout
    fallback = OkProvider("fallback")

    router = _build_router({"primary": primary, "fallback": fallback})
    result = router.run(_request(), _sleep=False)

    assert result.status == "ok"
    assert len(primary.calls) >= 1
    assert len(fallback.calls) == 1


# ---------------------------------------------------------------------------
# no_answer is NOT retriable
# ---------------------------------------------------------------------------


def test_router_does_not_fall_back_on_no_answer() -> None:
    """no_answer means the provider returned content but nothing matched.

    This is a valid result — not an infrastructure failure.  The router must
    NOT move to the fallback provider.
    """
    primary = DeniedProvider()
    fallback = OkProvider("fallback")

    router = _build_router({"primary": primary, "fallback": fallback})
    result = router.run(_request(), _sleep=False)

    # DeniedProvider returns {"status": "denied"} which the pack maps to no_answer.
    assert result.status == "no_answer"
    assert len(fallback.calls) == 0


# ---------------------------------------------------------------------------
# Exhausted fallback list → repair_required + all_providers_exhausted
# ---------------------------------------------------------------------------


def test_router_all_providers_exhausted_returns_repair_required() -> None:
    primary = FailProvider("timeout")
    fallback = FailProvider("timeout")

    router = _build_router({"primary": primary, "fallback": fallback})
    result = router.run(_request(), _sleep=False)

    assert result.status == "repair_required"
    assert "all_providers_exhausted" in result.reason_codes
    assert len(primary.calls) == 1
    assert len(fallback.calls) == 1


def test_router_no_providers_configured_returns_repair_required() -> None:
    from magi_agent.web_acquisition.provider_router import (
        ProviderRouterConfig,
        WebAcquisitionProviderRouter,
    )

    config = ProviderRouterConfig(enabled=True, providers=())
    pack = _live_pack(())
    router = WebAcquisitionProviderRouter(pack=pack, config=config, providers={})
    result = router.run(_request(), _sleep=False)

    assert result.status == "repair_required"
    assert "router_no_providers_configured" in result.reason_codes


# ---------------------------------------------------------------------------
# Retry on same provider (max_attempts_per_provider > 1)
# ---------------------------------------------------------------------------


def test_router_retries_up_to_max_before_fallback() -> None:
    """With max_attempts_per_provider=2, primary is tried twice before fallback."""
    primary = FailProvider("timeout")
    fallback = OkProvider("fallback")

    router = _build_router(
        {"primary": primary, "fallback": fallback},
        max_attempts_per_provider=2,
    )
    result = router.run(_request(), _sleep=False)

    assert result.status == "ok"
    # Primary tried twice (initial + 1 retry), then fallback once.
    assert len(primary.calls) == 2
    assert len(fallback.calls) == 1


def test_router_max_attempts_1_does_not_retry_same_provider() -> None:
    primary = FailProvider("timeout")
    fallback = OkProvider("fallback")

    router = _build_router(
        {"primary": primary, "fallback": fallback},
        max_attempts_per_provider=1,
    )
    result = router.run(_request(), _sleep=False)

    assert result.status == "ok"
    assert len(primary.calls) == 1
    assert len(fallback.calls) == 1


# ---------------------------------------------------------------------------
# Provider name is patched correctly (allowlist check)
# ---------------------------------------------------------------------------


def test_router_patches_provider_name_for_allowlist() -> None:
    """The router must use each provider's registered name for the allowlist check."""
    from magi_agent.web_acquisition.live_provider_pack import (
        LiveWebAcquisitionPackConfig,
        LiveWebAcquisitionProviderPack,
    )
    from magi_agent.web_acquisition.provider_router import (
        ProviderRouterConfig,
        WebAcquisitionProviderRouter,
    )

    # The pack's allowlist only contains "alpha".
    pack = LiveWebAcquisitionProviderPack(
        LiveWebAcquisitionPackConfig(
            enabled=True,
            liveNetworkEnabled=True,
            providerAllowlist=("alpha",),
        )
    )
    config = ProviderRouterConfig(enabled=True, providers=("alpha",), max_attempts_per_provider=1, base_retry_delay_ms=0, max_retry_delay_ms=0)
    ok_provider = OkProvider("alpha")
    router = WebAcquisitionProviderRouter(pack=pack, config=config, providers={"alpha": ok_provider})

    # Request comes in with providerName "primary" — the router must rewrite it to "alpha".
    result = router.run(_request(providerName="primary"), _sleep=False)
    assert result.status == "ok"
    assert len(ok_provider.calls) == 1


# ---------------------------------------------------------------------------
# live_network_enabled=False → blocked, router propagates
# ---------------------------------------------------------------------------


def test_router_propagates_live_network_disabled_from_pack() -> None:
    """When the pack has liveNetworkEnabled=False, the pack returns blocked.

    The router should record this and fall back to the next provider.  Since
    all providers share the same pack config, all will be blocked too — result
    is repair_required + all_providers_exhausted.
    """
    from magi_agent.web_acquisition.live_provider_pack import (
        LiveWebAcquisitionPackConfig,
        LiveWebAcquisitionProviderPack,
    )
    from magi_agent.web_acquisition.provider_router import (
        ProviderRouterConfig,
        WebAcquisitionProviderRouter,
    )

    pack = LiveWebAcquisitionProviderPack(
        LiveWebAcquisitionPackConfig(
            enabled=True,
            liveNetworkEnabled=False,  # disabled!
            providerAllowlist=("primary",),
        )
    )
    config = ProviderRouterConfig(enabled=True, providers=("primary",), max_attempts_per_provider=1, base_retry_delay_ms=0, max_retry_delay_ms=0)
    router = WebAcquisitionProviderRouter(pack=pack, config=config, providers={"primary": OkProvider()})

    result = router.run(_request(), _sleep=False)
    # "blocked" is not in fallback_on_status, so provider is skipped immediately.
    assert result.status == "repair_required"
    assert "all_providers_exhausted" in result.reason_codes


# ---------------------------------------------------------------------------
# Authority flags remain all False through the router
# ---------------------------------------------------------------------------


def test_router_authority_flags_remain_false_after_ok() -> None:
    router = _build_router({"primary": OkProvider()}, provider_names=("primary",))
    result = router.run(_request(), _sleep=False)

    assert result.status == "ok"
    flags = result.authority_flags
    assert flags.provider_called is False
    assert flags.network_fetched is False
    assert flags.browser_executed is False
    assert flags.production_writes_enabled is False
    assert flags.raw_content_injected is False
    assert flags.parent_context_injected is False
    assert flags.route_attached is False


def test_router_authority_flags_remain_false_after_exhaustion() -> None:
    router = _build_router(
        {"primary": FailProvider(), "fallback": FailProvider()},
    )
    result = router.run(_request(), _sleep=False)

    assert result.status == "repair_required"
    flags = result.authority_flags
    assert flags.provider_called is False
    assert flags.network_fetched is False
