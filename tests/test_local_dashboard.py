from __future__ import annotations

import importlib

import pytest
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
    # local-dev token is surfaced so the bundle auto-authenticates locally. The
    # additive `setup` block is also present (default-OFF wizard -> not needed).
    payload = _client("local-dev-token").get("/app/bootstrap.json").json()
    assert payload["ok"] is True
    assert payload["agentUrl"] == ""
    assert payload["tokenRequired"] is False
    assert payload["token"] == "local-dev-token"
    assert payload["setup"]["needed"] is False


def test_dashboard_bootstrap_hides_real_gateway_token() -> None:
    # A real secret is never embedded in the digest-safe bootstrap surface.
    payload = _client("super-secret-token").get("/app/bootstrap.json").json()
    assert payload["token"] is None
    assert payload["tokenRequired"] is True
    assert payload["agentUrl"] == ""


# ---------------------------------------------------------------------------
# Onboarding wizard: bootstrap `setup` block (PR1.1).
# ---------------------------------------------------------------------------
def _bootstrap(runtime, monkeypatch, *, flag: str | None, provider_env: bool) -> dict:
    # Hermetic: control the flag and every provider-selecting env var explicitly.
    from magi_agent.transport import web_dashboard as wd

    for name in (
        "MAGI_ONBOARDING_WIZARD_ENABLED",
        "MAGI_PROVIDER",
        "MAGI_MODEL",
        "MAGI_CONFIG",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "FIREWORKS_API_KEY",
        "OPENROUTER_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    # Point config at a non-existent file so no real ~/.magi/config.toml leaks in.
    monkeypatch.setenv("MAGI_CONFIG", "/nonexistent/magi-onboard-test/config.toml")
    if flag is not None:
        monkeypatch.setenv("MAGI_ONBOARDING_WIZARD_ENABLED", flag)
    if provider_env:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-present")
    return wd.local_dashboard_bootstrap(runtime)


def test_bootstrap_setup_lists_providers_and_shape(monkeypatch) -> None:
    from magi_agent.cli.providers import SUPPORTED_PROVIDERS

    payload = _bootstrap(_runtime(), monkeypatch, flag="1", provider_env=False)
    setup = payload["setup"]
    assert set(setup.keys()) == {"needed", "hasProvider", "providers"}
    assert setup["providers"] == list(SUPPORTED_PROVIDERS)
    # Existing keys are unchanged.
    assert payload["ok"] is True
    assert payload["agentUrl"] == ""


def test_bootstrap_setup_needed_when_flag_on_and_no_provider(monkeypatch) -> None:
    payload = _bootstrap(_runtime(), monkeypatch, flag="1", provider_env=False)
    assert payload["setup"]["needed"] is True
    assert payload["setup"]["hasProvider"] is False


def test_bootstrap_setup_not_needed_when_provider_present(monkeypatch) -> None:
    payload = _bootstrap(_runtime(), monkeypatch, flag="1", provider_env=True)
    assert payload["setup"]["hasProvider"] is True
    assert payload["setup"]["needed"] is False


def test_bootstrap_setup_not_needed_when_flag_off(monkeypatch) -> None:
    # Flag OFF => never needed, regardless of provider presence.
    off = _bootstrap(_runtime(), monkeypatch, flag=None, provider_env=False)
    assert off["setup"]["needed"] is False
    assert off["setup"]["hasProvider"] is False
    off_explicit = _bootstrap(_runtime(), monkeypatch, flag="0", provider_env=False)
    assert off_explicit["setup"]["needed"] is False


# ---------------------------------------------------------------------------
# Onboarding round-trip integration: the wizard's PUT /v1/app/config save path
# (_write_config -> [model].api_key) must clear setup.needed on the next
# bootstrap. This crosses the _write_config -> bootstrap seam that previously
# hid the infinite-onboarding-loop bug (bootstrap read only [providers.*]/env
# via configured_providers, never [model].api_key where the wizard saves).
# ---------------------------------------------------------------------------
def _hermetic_onboarding_env(monkeypatch, config_path) -> None:
    for name in (
        "MAGI_ONBOARDING_WIZARD_ENABLED",
        "MAGI_PROVIDER",
        "MAGI_MODEL",
        "MAGI_CONFIG",
        "MAGI_LLM_API_KEY",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "FIREWORKS_API_KEY",
        "OPENROUTER_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MAGI_CONFIG", str(config_path))
    monkeypatch.setenv("MAGI_ONBOARDING_WIZARD_ENABLED", "1")


def test_wizard_save_via_write_config_clears_setup_needed(monkeypatch, tmp_path) -> None:
    # Isolate the config path to a real (initially absent) temp file so the
    # real ~/.magi/config.toml is never read or written.
    from magi_agent.transport import app_api
    from magi_agent.transport import web_dashboard as wd

    config_path = tmp_path / "config.toml"
    _hermetic_onboarding_env(monkeypatch, config_path)
    runtime = _runtime()

    # RED precondition: flag ON, no env key, no config -> onboarding needed.
    before = wd.local_dashboard_bootstrap(runtime)
    assert before["setup"]["needed"] is True
    assert before["setup"]["hasProvider"] is False
    assert not config_path.exists()

    # Exactly what the wizard's PUT /v1/app/config performs: write the api_key
    # to the [model] table (provider/model/api_key), NOT [providers.*].
    app_api._write_config(
        {
            "llm": {
                "provider": "anthropic",
                "model": "claude-sonnet-4-6",
                "apiKey": "sk-ant-test",
            }
        }
    )
    assert config_path.exists()

    # GREEN: a fresh bootstrap must now see the provider and stop re-popping.
    after = wd.local_dashboard_bootstrap(runtime)
    assert after["setup"]["hasProvider"] is True
    assert after["setup"]["needed"] is False


def test_bootstrap_has_provider_true_for_env_key_roundtrip(monkeypatch, tmp_path) -> None:
    # The env-var-key case must still resolve hasProvider True (no regression).
    from magi_agent.transport import web_dashboard as wd

    config_path = tmp_path / "config.toml"
    _hermetic_onboarding_env(monkeypatch, config_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env")

    payload = wd.local_dashboard_bootstrap(_runtime())
    assert payload["setup"]["hasProvider"] is True
    assert payload["setup"]["needed"] is False


def test_dashboard_deep_link_prerendered_route() -> None:
    response = _client().get("/dashboard/local/chat/general")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "/_next/static/" in response.text


def test_dashboard_deep_link_falls_back_to_app_shell() -> None:
    # A not-prerendered, non-chat deep link still serves the SPA shell (never
    # blanks); client-side routing resolves the rest.
    response = _client().get("/dashboard/settings/unknown-section")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "/_next/static/" in response.text


def test_dashboard_chat_channel_deep_link_serves_chat_shell() -> None:
    # A user-created (not-prerendered) channel must get the chat shell, not the
    # dashboard index. The index redirects to /chat/general, which would bounce
    # the user straight back out of the channel they just opened.
    response = _client().get("/dashboard/local/chat/some-unbuilt-channel")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")

    shell = (
        web_dashboard.BUNDLE_ROOT / "dashboard/local/chat/general.html"
    ).read_text()
    index = (web_dashboard.BUNDLE_ROOT / "dashboard.html").read_text()
    assert response.text == shell
    assert response.text != index


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


def test_bundle_missing_serves_build_instructions_placeholder(monkeypatch) -> None:
    # When the static bundle is absent (source checkout without a web build),
    # /dashboard serves an honest build-instruction placeholder — not a second
    # inline web frontend.
    monkeypatch.setattr(web_dashboard, "bundle_available", lambda: False)
    response = _client().get("/dashboard")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    html = response.text
    assert "dashboard bundle not built" in html
    assert "scripts/build-web-dashboard.sh" in html
    assert "Node" in html
    assert "Homebrew" in html
    assert "https://github.com/openmagi/magi-agent" in html
    # The retired inline workbench shell must not come back: assert known
    # inline-only markers are gone from the served page.
    assert 'id="chat-form"' not in html
    assert 'id="panel-work"' not in html
    assert 'class="app"' not in html
    assert "/v1/chat/stream" not in html
    assert "MAGI_STREAMING_CHAT=on" not in html


def test_bundle_missing_deep_link_serves_placeholder(monkeypatch) -> None:
    monkeypatch.setattr(web_dashboard, "bundle_available", lambda: False)
    response = _client().get("/dashboard/local/chat/general")
    assert response.status_code == 200
    assert "dashboard bundle not built" in response.text


def test_bundle_missing_root_still_redirects_to_dashboard(monkeypatch) -> None:
    monkeypatch.setattr(web_dashboard, "bundle_available", lambda: False)
    response = _client().get("/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/dashboard"


def test_local_dashboard_chat_route_streams_local_adk_events(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MAGI_STREAMING_CHAT", "on")
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "missing-config.toml"))
    # Hermetic: this test asserts the STUB-runner marker ("Local ADK runtime
    # ready"), which is emitted only when NO model provider resolves. Any
    # ambient provider key in the env (CI, dev shells) would otherwise make the
    # runner build a real model and attempt a live call (401/timeout → retries,
    # no marker). Clear every provider-selecting env var so resolution
    # deterministically yields the local stub regardless of the host env.
    for _provider_env in (
        "MAGI_PROVIDER",
        "MAGI_MODEL",
        "MAGI_VISION_PROVIDER",
        "MAGI_VISION_MODEL",
        "MAGI_LLM_API_KEY",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "FIREWORKS_API_KEY",
        "OPENROUTER_API_KEY",
    ):
        monkeypatch.delenv(_provider_env, raising=False)
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


def test_inline_dashboard_module_is_deleted() -> None:
    # The 2.4K-LOC inline f-string dashboard (transport/dashboard.py) is gone
    # wholesale; transport/web_dashboard.py is the single dashboard path.
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("magi_agent.transport.dashboard")


def test_placeholder_page_has_no_runtime_bootstrap_or_app_logic(monkeypatch) -> None:
    # The placeholder is a static template: no runtime config, no embedded
    # bootstrap JSON, no token surface.
    monkeypatch.setattr(web_dashboard, "bundle_available", lambda: False)
    html = _client("super-secret-token").get("/dashboard").text
    assert 'id="runtime-bootstrap"' not in html
    assert "super-secret-token" not in html
