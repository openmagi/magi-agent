from __future__ import annotations

import json

from fastapi.testclient import TestClient

from openmagi_core_agent.app import create_app
from openmagi_core_agent.config.models import BuildInfo, RuntimeConfig
from openmagi_core_agent.runtime.openmagi_runtime import OpenMagiRuntime


def _client() -> TestClient:
    runtime = OpenMagiRuntime(
        config=RuntimeConfig(
            bot_id="local-bot",
            user_id="local-user",
            gateway_token="local-token",
            api_proxy_url="http://api-proxy.local",
            chat_proxy_url="http://chat-proxy.local",
            redis_url="redis://redis.local:6379/0",
            model="gpt-5.2",
            build=BuildInfo(version="0.1.0", build_sha="sha-test"),
        )
    )
    return TestClient(create_app(runtime))


def test_local_dashboard_route_serves_self_contained_html() -> None:
    response = _client().get("/dashboard")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    html = response.text
    assert "Open Magi Agent" in html
    assert 'id="chat-form"' in html
    assert "/v1/chat/completions" in html
    assert "/healthz" in html
    assert "event: agent" in html


def test_local_dashboard_public_html_avoids_hosted_and_legacy_branding() -> None:
    response = _client().get("/dashboard")
    lowered = response.text.lower()

    forbidden = (
        "clawy",
        "privy",
        "supabase",
        "stripe",
        "billing",
        "hosted",
        "cloud",
        "selected-bot",
        "rollout",
    )
    for term in forbidden:
        assert term not in lowered


def test_local_dashboard_route_is_not_backed_by_external_assets() -> None:
    response = _client().get("/dashboard")
    html = response.text

    assert "https://" not in html
    assert "http://" not in html
    assert "<script src=" not in html
    assert "<link rel=" not in html


def test_local_dashboard_exposes_digest_safe_runtime_bootstrap() -> None:
    response = _client().get("/dashboard")
    marker = '<script type="application/json" id="runtime-bootstrap">'
    start = response.text.index(marker) + len(marker)
    end = response.text.index("</script>", start)

    bootstrap = json.loads(response.text[start:end])
    assert bootstrap == {
        "botId": "local-bot",
        "model": "gpt-5.2",
        "runtime": "magi-agent",
        "version": "0.1.0",
    }
