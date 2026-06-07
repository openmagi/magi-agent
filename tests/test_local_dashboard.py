from __future__ import annotations

import json

from fastapi.testclient import TestClient

from magi_agent.app import create_app
from magi_agent.config.models import BuildInfo, RuntimeConfig
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime
from magi_agent.transport import web_dashboard


def _runtime(gateway_token: str = "local-token") -> OpenMagiRuntime:
    return OpenMagiRuntime(
        config=RuntimeConfig(
            bot_id="local-bot",
            user_id="local-user",
            gateway_token=gateway_token,
            api_proxy_url="http://api-proxy.local",
            chat_proxy_url="http://chat-proxy.local",
            redis_url="redis://redis.local:6379/0",
            model="gpt-5.2",
            build=BuildInfo(version="0.1.0", build_sha="sha-test"),
        )
    )


def _client(gateway_token: str = "local-token") -> TestClient:
    return TestClient(create_app(_runtime(gateway_token)))


def test_bundle_is_present() -> None:
    # The restored static dashboard export must ship in the package so a clean
    # `magi-agent serve` exposes the UI with no Node runtime.
    assert web_dashboard.bundle_available()
    assert (web_dashboard.BUNDLE_ROOT / "dashboard.html").is_file()
    assert (web_dashboard.BUNDLE_ROOT / "_next").is_dir()


def test_dashboard_serves_restored_static_ui() -> None:
    response = _client().get("/dashboard")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    html = response.text
    # Real Next.js static export shell, not the inline workbench mock.
    assert "/_next/static/" in html
    assert "<script" in html
    # Restored brand, no legacy branding in the served shell.
    assert "Open Magi" in html
    legacy_brand = "".join(["c", "l", "a", "w", "y"])
    assert legacy_brand not in html.lower()


def test_dashboard_bundle_uses_local_streaming_chat_contract() -> None:
    bundle_text = "\n".join(
        path.read_text(errors="ignore")
        for path in web_dashboard.BUNDLE_ROOT.glob("_next/static/chunks/*.js")
    )

    assert "/v1/chat/stream" in bundle_text
    assert "/v1/chat/control-response" in bundle_text
    assert "/v1/chat/cancel" in bundle_text


def test_dashboard_bootstrap_is_local_first() -> None:
    # local-dev token is surfaced so the bundle auto-authenticates locally.
    payload = _client("local-dev-token").get("/app/bootstrap.json").json()
    assert payload == {
        "ok": True,
        "agentUrl": "",
        "tokenRequired": False,
        "token": "local-dev-token",
    }


def test_dashboard_bootstrap_hides_real_gateway_token() -> None:
    # A real secret is never embedded in the digest-safe bootstrap surface.
    payload = _client("super-secret-token").get("/app/bootstrap.json").json()
    assert payload["token"] is None
    assert payload["tokenRequired"] is True
    assert payload["agentUrl"] == ""


def test_dashboard_deep_link_prerendered_route() -> None:
    response = _client().get("/dashboard/local/chat/general")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "/_next/static/" in response.text


def test_dashboard_deep_link_falls_back_to_app_shell() -> None:
    # A not-prerendered deep link still serves the SPA shell (never blanks);
    # client-side routing resolves the rest.
    response = _client().get("/dashboard/local/chat/some-unbuilt-channel")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "/_next/static/" in response.text


def test_dashboard_serves_hashed_next_assets() -> None:
    chunks = sorted((web_dashboard.BUNDLE_ROOT / "_next/static/chunks").glob("*.js"))
    assert chunks, "expected at least one built JS chunk"
    rel = chunks[0].relative_to(web_dashboard.BUNDLE_ROOT)
    response = _client().get("/" + str(rel))
    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]


def test_dashboard_serves_root_static_asset() -> None:
    response = _client().get("/favicon.ico")
    assert response.status_code == 200


def test_root_redirects_to_dashboard() -> None:
    response = _client().get("/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/dashboard"


def test_dashboard_boots_without_hosted_dependencies() -> None:
    # Build the app and hit the dashboard with only local config — no API keys,
    # no chat-proxy, no hosted auth required to serve the UI.
    response = _client().get("/dashboard")
    assert response.status_code == 200


def test_control_request_endpoints_return_empty_for_local() -> None:
    client = _client()
    auth = {"authorization": "Bearer local-token"}

    requests = client.get("/v1/control-requests", headers=auth)
    assert requests.status_code == 200
    assert requests.json() == {"requests": []}

    events = client.get("/v1/control-events?lastSeq=7", headers=auth)
    assert events.status_code == 200
    assert events.json() == {"events": [], "lastSeq": 7}

    resp = client.post("/v1/control-requests/req-1/response", headers=auth, json={})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_control_request_endpoints_require_gateway_token() -> None:
    assert _client().get("/v1/control-requests").status_code == 401


def test_inline_shell_is_fallback_when_bundle_missing(monkeypatch) -> None:
    # When the static bundle is absent (source checkout without a web build),
    # the inline workbench shell is served instead.
    monkeypatch.setattr(web_dashboard, "bundle_available", lambda: False)
    response = _client().get("/dashboard")
    assert response.status_code == 200
    assert 'class="app"' in response.text
    assert "Open Magi Agent" in response.text
    assert 'id="chat-form"' in response.text
    assert 'id="panel-work"' in response.text
    assert 'id="panel-knowledge"' in response.text
    assert 'id="panel-settings"' in response.text
    assert "/v1/chat/stream" in response.text
    assert "/v1/chat/control-response" in response.text
    assert "/v1/chat/cancel" in response.text
    assert "MAGI_STREAMING_CHAT=on" in response.text


def test_local_dashboard_chat_route_streams_local_adk_events(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MAGI_STREAMING_CHAT", "on")
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "missing-config.toml"))
    client = _client()

    response = client.post(
        "/v1/chat/stream",
        headers={"authorization": "Bearer local-token"},
        json={
            "sessionId": "agent:main:app:general",
            "stream": True,
            "messages": [{"role": "user", "content": "hello"}],
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    text = response.text
    # The restored UI renders text from `event: agent` frames whose vocabulary
    # matches the runtime public events (text_delta / turn_phase / error / ...).
    assert "event: agent" in text
    assert "Local ADK runtime ready" in text
    assert "data: [DONE]" in text


def test_exposes_digest_safe_runtime_bootstrap_for_inline_shell(monkeypatch) -> None:
    monkeypatch.setattr(web_dashboard, "bundle_available", lambda: False)
    response = _client().get("/dashboard")
    marker = '<script type="application/json" id="runtime-bootstrap">'
    start = response.text.index(marker) + len(marker)
    end = response.text.index("</script>", start)
    bootstrap = json.loads(response.text[start:end])
    assert bootstrap == {
        "botId": "local-bot",
        "model": "gpt-5.2",
        "runtime": "magi-agent",
        "runtimeEngine": "adk-python",
        "version": "0.1.0",
        "gatewayToken": "",
    }
