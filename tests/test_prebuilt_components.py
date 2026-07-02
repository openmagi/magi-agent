"""PR-P4: prebuilt (always-on) components catalog + read-only route."""

from __future__ import annotations

from fastapi.testclient import TestClient

from magi_agent.app import create_app
from magi_agent.config.models import BuildInfo, RuntimeConfig
from magi_agent.customize.prebuilt_components import prebuilt_components_view
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime

_TOKEN = "test-gateway-token"


def test_view_surfaces_read_before_write_and_is_always_on() -> None:
    components = prebuilt_components_view()
    keys = {c["key"] for c in components}
    # The behavior Kevin could not find anywhere in the dashboard.
    assert "read_before_write" in keys
    for c in components:
        assert c["alwaysOn"] is True
        assert set(c) == {"key", "name", "description", "where", "alwaysOn"}


def _runtime() -> OpenMagiRuntime:
    return OpenMagiRuntime(
        config=RuntimeConfig(
            bot_id="local-bot",
            user_id="local-user",
            gateway_token=_TOKEN,
            api_proxy_url="http://api-proxy.local",
            chat_proxy_url="http://chat-proxy.local",
            redis_url="redis://redis.local:6379/0",
            model="gpt-5.2",
            build=BuildInfo(version="0.1.0", build_sha="sha-test"),
        )
    )


def test_route_requires_auth() -> None:
    client = TestClient(create_app(_runtime()))
    assert client.get("/v1/app/prebuilt-components").status_code == 401


def test_route_returns_components() -> None:
    client = TestClient(create_app(_runtime()))
    client.headers.update({"x-gateway-token": _TOKEN})
    resp = client.get("/v1/app/prebuilt-components")
    assert resp.status_code == 200
    comps = resp.json()["components"]
    assert any(c["key"] == "read_before_write" for c in comps)
