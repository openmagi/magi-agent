from __future__ import annotations

import json

from fastapi.testclient import TestClient

from magi_agent.app import create_app
from magi_agent.config.models import BuildInfo, RuntimeConfig
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime


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


def _client_with_gateway_token(gateway_token: str) -> TestClient:
    runtime = OpenMagiRuntime(
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
    return TestClient(create_app(runtime))


def test_local_dashboard_route_serves_adk_local_app_shell() -> None:
    response = _client().get("/dashboard")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    html = response.text
    assert "Open Magi Agent" in html
    assert 'id="chat-form"' in html
    assert 'class="app"' in html
    assert 'class="sidebar"' in html
    assert 'class="inspector"' in html
    assert 'id="panel-work"' in html
    assert html.count('id="panel-knowledge"') == 1
    assert 'id="panel-settings"' in html
    assert "Work Stream" in html
    assert "Magi Agent is ready." in html
    assert "Local workspace ready" in html
    assert "Runtime surfaces" in html
    assert "First-party surfaces" in html
    assert "ADK Python" in html
    assert "current local session" in html
    assert 'id="agent-state-pill"' in html
    assert "class=\"status-band\"" in html
    assert "/v1/chat/stream" in html
    assert "/healthz" in html
    assert "ADK runtime" in html


def test_local_dashboard_chat_panel_uses_streaming_chat_contract() -> None:
    response = _client().get("/dashboard")
    html = response.text

    # Chat panel posts the single-channel streaming contract, not OpenAI deltas.
    assert "/v1/chat/stream" in html
    assert "/v1/chat/control-response" in html
    assert "/v1/chat/cancel" in html
    assert "x-openclaw-session-key" in html
    assert "foldAgentEvent" in html
    assert "local-dashboard" in html
    # Approval modal + cancel wiring are present.
    assert 'id="approval-modal"' in html
    assert "AbortController" in html
    # Updated disabled-route guidance for the streaming flag.
    assert "MAGI_STREAMING_CHAT=on" in html
    # The OpenAI choices[].delta path no longer drives chat text.
    assert "appendDelta" not in html
    assert "choices[0].delta" not in html


def test_local_dashboard_renders_workbench_not_empty_mockup() -> None:
    response = _client().get("/dashboard")
    html = response.text

    assert 'id="thread-list"' in html
    assert 'id="quick-actions"' in html
    assert 'id="composer-status"' in html
    assert 'id="work-stream-events"' in html
    assert "Current run" in html
    assert "Ready to run" in html
    assert "No active run" in html
    assert "Attach local context" in html
    assert "Work in progress" in html
    assert "Main session" in html
    assert 'id="workspace-board"' in html
    assert "Workload" in html
    assert "Receipts" in html
    assert 'id="metric-sse"' in html
    assert 'id="metric-events"' in html
    assert 'id="receipt-list"' in html
    assert "Run local agent work from one dashboard." not in html


def test_local_dashboard_restores_historical_stream_chat_components() -> None:
    response = _client().get("/dashboard")
    html = response.text

    assert (
        'data-historical-stream-source="legacy-stream:220e54ea4+5fcadc4cd"'
        in html
    )
    assert "StreamChatContainer" in html
    assert "StreamTranscript" in html
    assert "AssistantBubble" in html
    assert "ThinkingBlock" in html
    assert "ToolCallCard" in html
    assert "TodoListCard" in html
    assert "ActivityList" in html
    assert "ApprovalModal" in html
    assert "TerminalNotice" in html
    assert "createAgentSseTokenizer" in html
    assert "foldRuntimeEvent" in html
    assert "nextQueueOnTurnEnd" in html
    assert "control_request" in html
    assert "tool_start" in html
    assert "tool_end" in html
    assert "thinking_delta" in html
    assert "queued-messages" in html
    assert "approval-modal" in html


def test_local_dashboard_has_operational_work_stream_metrics() -> None:
    response = _client().get("/dashboard")
    html = response.text

    assert "SSE" in html
    assert "Events" in html
    assert "Tools" in html
    assert "Receipts" in html
    assert 'id="board-turn-state"' in html
    assert 'id="board-tool-state"' in html
    assert 'id="board-evidence-state"' in html
    assert 'id="board-transport-state"' in html
    assert "setRunBoard" in html
    assert "renderReceiptList" in html


def test_local_dashboard_restores_hosted_style_three_column_work_shell() -> None:
    response = _client().get("/dashboard")
    html = response.text

    assert 'data-shell-region="channel-nav"' in html
    assert 'data-shell-region="chat-transcript"' in html
    assert 'data-shell-region="work-stream"' in html
    assert 'data-channel="general"' in html
    assert 'data-channel="research"' in html
    assert 'id="work-group-now"' in html
    assert 'id="work-group-runtime-checks"' in html
    assert 'id="work-group-current-steps"' in html
    assert 'id="work-group-missions"' in html
    assert "Now" in html
    assert "Runtime checks" in html
    assert "Current steps" in html
    assert "Missions" in html


def test_local_dashboard_prefills_default_local_gateway_token() -> None:
    response = _client_with_gateway_token("local-dev-token").get("/dashboard")
    html = response.text

    assert '"gatewayToken":"local-dev-token"' in html
    assert 'localStorage.getItem(tokenKey) || bootstrap.gatewayToken || ""' in html


def test_local_dashboard_does_not_expose_custom_gateway_token() -> None:
    response = _client_with_gateway_token("custom-secret-token").get("/dashboard")
    html = response.text

    assert "custom-secret-token" not in html
    assert '"gatewayToken":""' in html


def test_local_dashboard_guides_gateway_token_failures() -> None:
    response = _client_with_gateway_token("custom-secret-token").get("/dashboard")
    html = response.text

    assert 'id="token-help"' in html
    assert 'id="token-error"' in html
    assert 'aria-describedby="token-help token-error"' in html
    assert "function setTokenError(message)" in html
    assert "function describeChatFailure(response, text)" in html
    assert "response.status === 401" in html
    assert "Enter the gateway token from GATEWAY_TOKEN" in html
    assert "Gateway token required" in html
    assert "tokenInput.focus()" in html
    assert "custom-secret-token" not in html


def test_local_dashboard_exposes_runtime_surface_panels() -> None:
    response = _client().get("/dashboard")
    html = response.text

    assert 'id="tool-count"' in html
    assert 'id="tool-list"' in html
    assert 'id="harness-list"' in html
    assert 'id="evidence-list"' in html
    assert "Active tools" in html
    assert "Harness packs" in html
    assert "Evidence gates" in html
    assert "renderSurfaceStatus" in html


def test_local_dashboard_has_accessible_dashboard_interaction_polish() -> None:
    response = _client().get("/dashboard")
    html = response.text

    assert ":focus-visible" in html
    assert "@media (prefers-reduced-motion: reduce)" in html
    assert 'role="tabpanel"' in html
    assert 'aria-controls="panel-work"' in html
    assert 'aria-selected="true"' in html
    assert 'aria-label="Send prompt"' in html
    assert 'data-target-panel="knowledge"' in html
    assert "activatePanel" in html


def test_local_dashboard_exposes_local_runtime_command_center() -> None:
    response = _client().get("/dashboard")
    html = response.text

    assert 'id="runtime-command-center"' in html
    assert 'id="activity-lane"' in html
    assert 'id="transport-log"' in html
    assert "Command center" in html
    assert "Session Memory" in html
    assert "Tool Registry" in html
    assert "Policy Boundary" in html
    assert "SSE Transport" in html
    assert "Local-only controls" in html
    assert "renderActivityLane" in html
    assert "renderTransportLog" in html


def test_local_dashboard_deep_links_serve_same_app_shell() -> None:
    for path in (
        "/dashboard/local/chat",
        "/dashboard/local/knowledge",
        "/dashboard/local/settings",
    ):
        response = _client().get(path)

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert "Open Magi Agent Dashboard" in response.text
        assert 'id="runtime-bootstrap"' in response.text
        assert 'class="app"' in response.text


def test_root_redirects_to_dashboard() -> None:
    response = _client().get("/", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/dashboard"


def test_local_dashboard_public_html_avoids_hosted_and_legacy_branding() -> None:
    response = _client().get("/dashboard")
    lowered = response.text.lower()

    forbidden = (
        "cla" + "wy",
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
        "runtimeEngine": "adk-python",
        "version": "0.1.0",
        "gatewayToken": "",
    }


def test_local_dashboard_chat_route_streams_local_adk_events(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MAGI_AGENT_LOCAL_CHAT_ROUTE", "on")
    monkeypatch.setenv("MAGI_CONFIG", str(tmp_path / "missing-config.toml"))
    client = _client()

    response = client.post(
        "/v1/chat/completions",
        headers={"authorization": "Bearer local-token"},
        json={
            "stream": True,
            "messages": [{"role": "user", "content": "hello"}],
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    text = response.text
    assert "Running local ADK" in text
    assert "Set a provider or API proxy configuration" in text
    assert "enable generated replies" in text
    assert "data: [DONE]" in text
