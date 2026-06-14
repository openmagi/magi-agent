import json
import time
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from magi_agent.cli.contracts import EngineResult, Terminal
from magi_agent.adk_bridge.primitives import AdkPrimitiveBoundary
from magi_agent.app import create_app
from magi_agent.config.models import (
    BuildInfo,
    PythonRuntimeAuthorityConfig,
    RuntimeConfig,
)
from magi_agent.config.env import (
    parse_gate5b4c3_shadow_generation_route_env,
    parse_runtime_env,
)
from magi_agent.evidence.observed_egress import (
    LiveEgressTelemetryEvidenceProvider,
    LocalObservedEgressEvidenceProvider,
    ObservedEgressEvidence,
)
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime
from magi_agent.shadow.gate5b4c3_live_runner_boundary import (
    Gate5B4C3LiveAdkPrimitives,
)
from magi_agent.shadow.gate5b4c3_shadow_generation_contract import (
    Gate5B4C3ShadowGenerationConfig,
)
from magi_agent.shadow.gate5b4c3_shadow_counter_store import (
    Gate5B4C3ShadowCounterStore,
)
from magi_agent.transport.shadow_generations import (
    Gate5B4C3ShadowGenerationRouteConfig,
)
from magi_agent.transport import chat as chat_module
from magi_agent.transport import chat_routes as chat_routes_module
from magi_agent.transport.chat import (
    Gate5BUserVisibleChatRouteConfig,
    build_gate5b_full_toolhost_config_from_env,
    build_gate5b_user_visible_chat_route_config_from_env,
    build_gate5b_user_visible_canary_runner_request,
    build_public_identity_policy,
)

FIRST_PARTY_RECIPE_PACK_IDS = (
    "openmagi.context-safety",
    "openmagi.evidence",
    "openmagi.agent-methodology",
    "openmagi.superpowers-compat",
    "openmagi.web-acquisition",
    "openmagi.research",
    "openmagi.dev-coding",
    "openmagi.missions",
    "openmagi.scheduled-work",
    "openmagi.memory-agentmemory",
    "openmagi.channel-delivery",
    "openmagi.office-automation",
    "openmagi.artifact-delivery",
    "openmagi.spreadsheet-automation",
    "openmagi.browser-automation",
    "openmagi.document-review",
    "openmagi.lightweight-scripting",
)


def _sha256(value: str) -> str:
    import hashlib

    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def make_runtime(
    *,
    authority: PythonRuntimeAuthorityConfig | None = None,
) -> OpenMagiRuntime:
    return OpenMagiRuntime(
        config=RuntimeConfig(
            bot_id="bot-test",
            user_id="user-test",
            gateway_token="gateway-token",
            api_proxy_url="http://api-proxy.local",
            chat_proxy_url="http://chat-proxy.local",
            redis_url="redis://redis.local:6379/0",
            model="gpt-5.2",
            build=BuildInfo(version="0.1.0-adk-scaffold", build_sha="sha-test"),
            authority=authority or PythonRuntimeAuthorityConfig(),
        )
    )


def install_fake_local_headless(monkeypatch, captured: dict[str, object]) -> None:
    class FakeEngine:
        async def run_turn_stream(self, runtime, turn_input, *, cancel, gate):
            yield EngineResult(
                terminal=Terminal.completed,
                session_id=turn_input["session_id"],
                turn_id=turn_input["turn_id"],
            )

    def fake_build_headless_runtime(**kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(engine=FakeEngine(), gate=None)

    import magi_agent.cli.wiring as wiring

    monkeypatch.setattr(wiring, "build_headless_runtime", fake_build_headless_runtime)


def test_user_visible_generation_request_selects_allowlisted_model_from_body() -> None:
    route_config = parse_gate5b4c3_shadow_generation_route_env(
        {
            "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_ENABLED": "1",
            "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_KILL_SWITCH": "0",
            "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_CAP_STATE_INITIALIZED": "1",
            "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_PROVIDER_PROJECT_SPEND_CONTROLS_VERIFIED": "1",
            "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_SELECTED_BOT_DIGEST": _sha256("bot-test"),
            "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_TRUSTED_OWNER_USER_ID_DIGEST": _sha256("user-test"),
            "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_ENVIRONMENT": "production",
            "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_ALLOWED_MODEL_ROUTES": (
                "google:gemini-3.5-flash,fireworks:kimi-k2p6"
            ),
            "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_PROVIDER_CREDENTIAL_BINDINGS": (
                "google:gate5b-google-api-key-smoke-v1:GOOGLE_API_KEY:adk,"
                "fireworks:platform-proxy-fireworks:FIREWORKS_API_KEY:litellm"
            ),
            "GOOGLE_GENAI_USE_VERTEXAI": "FALSE",
            "GOOGLE_API_KEY": "sample-fixture-value-must-not-leak",
            "FIREWORKS_API_KEY": "sample-fixture-value-must-not-leak",
        }
    )

    generation = chat_module._build_user_visible_generation_request(
        runtime=make_runtime(),
        route_config=Gate5BUserVisibleChatRouteConfig(environment="production"),
        generation_config=route_config.generation_config,
        payload={
            "model": "fireworks/kimi-k2p6",
            "messages": [{"role": "user", "content": "run a Kimi canary"}],
        },
        trace_id="trace-1",
    )

    assert generation.model_routing.provider_label == "fireworks"
    assert generation.model_routing.model_label == "kimi-k2p6"
    assert generation.model_routing.shadow_credential_ref == "platform-proxy-fireworks"


def test_generation_request_envelope_drops_dead_ts_era_metadata(monkeypatch) -> None:
    """08-PR2 (D5): the generation-request envelope must not carry the dead
    TS-era ``mode``/``responseAuthority`` metadata (never consumed by the
    serving path), while the serving wire body keeps ``responseAuthority:
    "python"`` plus the authority/safety flag blocks chat-proxy validates."""
    route_config = parse_gate5b4c3_shadow_generation_route_env(
        {
            "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_ENABLED": "1",
            "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_KILL_SWITCH": "0",
            "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_CAP_STATE_INITIALIZED": "1",
            "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_PROVIDER_PROJECT_SPEND_CONTROLS_VERIFIED": "1",
            "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_SELECTED_BOT_DIGEST": _sha256("bot-test"),
            "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_TRUSTED_OWNER_USER_ID_DIGEST": _sha256("user-test"),
            "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_ENVIRONMENT": "production",
            "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_ALLOWED_MODEL_ROUTES": (
                "google:gemini-3.5-flash"
            ),
            "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_PROVIDER_CREDENTIAL_BINDINGS": (
                "google:gate5b-google-api-key-smoke-v1:GOOGLE_API_KEY:adk"
            ),
            "GOOGLE_GENAI_USE_VERTEXAI": "FALSE",
            "GOOGLE_API_KEY": "sample-fixture-value-must-not-leak",
        }
    )

    generation = chat_module._build_user_visible_generation_request(
        runtime=make_runtime(),
        route_config=Gate5BUserVisibleChatRouteConfig(environment="production"),
        generation_config=route_config.generation_config,
        payload={
            "model": "google/gemini-3.5-flash",
            "messages": [{"role": "user", "content": "envelope honesty probe"}],
        },
        trace_id="trace-envelope-1",
    )

    dumped = generation.model_dump(by_alias=True, mode="json")
    assert "mode" not in dumped
    assert "responseAuthority" not in dumped
    assert dumped["schemaVersion"] == "gate5b4c3.chatProxyShadowGeneration.v1"

    # Serving wire contract is unchanged by the envelope cleanup: the body
    # still declares python authority plus the authority/safety blocks.
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    runtime = make_runtime(
        authority=PythonRuntimeAuthorityConfig(
            userVisibleOutputAllowed=True,
            canaryRoutingAllowed=True,
        )
    )
    runtime.gate5b_user_visible_chat_route_config = Gate5BUserVisibleChatRouteConfig(
        enabled=True,
        killSwitchEnabled=False,
        selectedBotDigest=_sha256("bot-test"),
        selectedOwnerUserIdDigest=_sha256("user-test"),
        environment="production",
        environmentAllowlist=("production",),
        mockedRunner=lambda request: {
            "content": "mocked user-visible Python canary response",
            "eventCount": 1,
        },
    )

    response = TestClient(create_app(runtime)).post(
        "/v1/chat/completions",
        headers={"authorization": "Bearer gateway-token"},
        json={
            "messages": [{"role": "user", "content": "synthetic canary prompt"}],
            "authority": {"userVisibleOutputAllowed": True},
        },
    )

    assert response.status_code == 200, response.json()
    body = response.json()
    assert body["responseAuthority"] == "python"
    assert body["authority"]["userVisibleOutputAllowed"] is True
    assert body["authority"]["canaryRoutingAllowed"] is True
    assert body["safety"]["toolsActive"] is False
    assert body["safety"]["productionDbWritesAllowed"] is False


def test_chat_completions_route_is_disabled_when_chat_routes_are_off(monkeypatch) -> None:
    monkeypatch.delenv("CORE_AGENT_PYTHON_CHAT_ROUTE", raising=False)
    monkeypatch.setenv("MAGI_AGENT_LOCAL_CHAT_ROUTE", "off")
    client = TestClient(create_app(make_runtime()))

    response = client.post(
        "/v1/chat/completions",
        headers={"authorization": "Bearer gateway-token"},
        json={"messages": [{"role": "user", "content": "hello"}]},
    )

    assert response.status_code == 503
    assert response.json() == {
        "error": "chat_route_disabled",
        "runtime": "magi-agent",
        "runtimeEngine": "adk-python",
    }


def test_chat_completions_route_uses_local_adk_when_local_route_is_enabled(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}
    install_fake_local_headless(monkeypatch, captured)
    monkeypatch.delenv("CORE_AGENT_PYTHON_CHAT_ROUTE", raising=False)
    monkeypatch.setenv("MAGI_AGENT_LOCAL_CHAT_ROUTE", "on")
    client = TestClient(create_app(make_runtime()))

    response = client.post(
        "/v1/chat/completions",
        headers={"authorization": "Bearer gateway-token"},
        json={
            "sessionId": "local-session",
            "turnId": "local-turn",
            "messages": [{"role": "user", "content": "hello"}],
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "data: [DONE]" in response.text
    assert captured["session_id"] == "local-session"
    assert captured["recall_query"] == "hello"


def test_chat_route_checks_bearer_before_disabled_response(monkeypatch) -> None:
    monkeypatch.delenv("CORE_AGENT_PYTHON_CHAT_ROUTE", raising=False)
    client = TestClient(create_app(make_runtime()))

    response = client.post("/v1/chat/completions", json={"messages": []})

    assert response.status_code == 401
    assert response.json()["error"] == "unauthorized"


def test_chat_route_enabled_without_canary_gate_fails_open_to_typescript(monkeypatch) -> None:
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    client = TestClient(create_app(make_runtime()))

    response = client.post(
        "/v1/chat/completions",
        headers={"authorization": "Bearer gateway-token"},
        json={"messages": [{"role": "user", "content": "hello"}]},
    )

    assert response.status_code == 503
    assert response.json() == {
        "status": "python_disabled",
        "fallbackStatus": "fallback_to_typescript",
        "responseAuthority": "typescript",
        "reason": "canary_gate_disabled",
        "runtime": "magi-agent",
        "runtimeEngine": "adk-python",
    }


def test_chat_route_mocked_canary_success_has_explicit_python_authority(monkeypatch) -> None:
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    runtime = make_runtime(
        authority=PythonRuntimeAuthorityConfig(
            userVisibleOutputAllowed=True,
            canaryRoutingAllowed=True,
        )
    )
    runtime.gate5b_user_visible_chat_route_config = Gate5BUserVisibleChatRouteConfig(
        enabled=True,
        killSwitchEnabled=False,
        selectedBotDigest=_sha256("bot-test"),
        selectedOwnerUserIdDigest=_sha256("user-test"),
        environment="production",
        environmentAllowlist=("production",),
        mockedRunner=lambda request: {
            "content": "mocked user-visible Python canary response",
            "eventCount": 1,
        },
    )
    client = TestClient(create_app(runtime))

    response = client.post(
        "/v1/chat/completions",
        headers={"authorization": "Bearer gateway-token"},
        json={
            "messages": [{"role": "user", "content": "synthetic canary prompt"}],
            "authority": {"userVisibleOutputAllowed": True},
        },
    )

    assert response.status_code == 200, response.json()
    body = response.json()
    assert body["status"] == "python_ready"
    assert body["fallbackStatus"] == "none"
    assert body["responseAuthority"] == "python"
    assert body["authority"] == {
        "userVisibleOutputAllowed": True,
        "canaryRoutingAllowed": True,
        "memoryWriteAllowed": False,
        "toolDispatchAllowed": False,
        "transcriptWritesAllowed": False,
        "sseWritesAllowed": False,
        "channelWritesAllowed": False,
        "dbWritesAllowed": False,
        "workspaceMutationAllowed": False,
        "childExecutionAllowed": False,
        "missionRuntimeAllowed": False,
        "evidenceBlockModeAllowed": False,
    }
    assert body["safety"] == {
        "toolsActive": False,
        "memoryProviderActive": False,
        "browserActive": False,
        "workspaceMutationAllowed": False,
        "childExecutionAllowed": False,
        "missionRuntimeAllowed": False,
        "telegramDeliveryAllowed": False,
        "artifactChannelDeliveryAllowed": False,
        "evidenceBlockModeAllowed": False,
        "productionTranscriptWritesAllowed": False,
        "productionSseWritesAllowed": False,
        "productionDbWritesAllowed": False,
    }
    assert body["choices"][0]["message"]["content"] == "mocked user-visible Python canary response"
    assert body["adk"]["invoked"] is False
    assert body["mockedRunnerInvoked"] is True
    assert body["eventCount"] == 1


def test_public_identity_policy_is_canonical_and_model_visible_safe() -> None:
    policy = build_public_identity_policy()

    assert policy["canonicalName"] == "Magi Agent"
    assert policy["platformName"] == "OpenMagi"
    assert "Magi Agent" in policy["modelVisibleSystemContext"]
    assert "OpenMagi" in policy["modelVisibleSystemContext"]
    serialized = str(policy).lower()
    for forbidden in ("magi-agent", "magi_agent"):
        assert forbidden not in serialized


def test_user_visible_runner_request_normalizes_legacy_workspace_identity_fixture() -> None:
    request = build_gate5b_user_visible_canary_runner_request(
        {
            "messages": [
                {
                    "role": "system",
                    "content": "You are a Magi Agent running on magi-agent.",
                },
                {"role": "user", "content": "Synthetic canary prompt."},
            ],
            "workspaceIdentityText": (
                "You are a Magi Agent running on the Magi platform "
                "and the Magi Agent runtime (`magi-agent`)."
            ),
        }
    )

    serialized = str(request).lower()
    assert "magi agent" in serialized
    assert "openmagi" in serialized
    for forbidden in ("magi-agent", "magi_agent"):
        assert forbidden not in serialized
    assert request["legacyIdentitySignals"] == (
        "legacy_public_identity_normalized",
        "legacy_runtime_identity_normalized",
    )


def test_user_visible_runner_request_preserves_latest_visible_chat_context() -> None:
    messages: list[dict[str, str]] = [
        {"role": "system", "content": "Public runtime policy."},
    ]
    for index in range(18):
        messages.append(
            {
                "role": "assistant" if index % 2 else "user",
                "content": f"old visible turn {index}",
            }
        )
    messages.append(
        {
            "role": "assistant",
            "content": "I started the multibagger report but did not finish it.",
        }
    )
    messages.append(
        {
            "role": "user",
            "content": "어캐돼가",
        }
    )

    request = build_gate5b_user_visible_canary_runner_request({"messages": messages})
    model_messages = request["messages"]

    assert isinstance(model_messages, tuple)
    assert model_messages[-2:] == (
        {
            "role": "assistant",
            "content": "I started the multibagger report but did not finish it.",
        },
        {"role": "user", "content": "어캐돼가"},
    )
    assert {"role": "user", "content": "old visible turn 0"} not in model_messages


def test_chat_route_passes_canonical_identity_context_to_mocked_runner(monkeypatch) -> None:
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    captured: dict[str, object] = {}

    def mocked_runner(request):
        captured.update(request)
        return {
            "content": "mocked Magi Agent response from magi-agent",
            "eventCount": 1,
        }

    runtime = make_runtime(
        authority=PythonRuntimeAuthorityConfig(
            userVisibleOutputAllowed=True,
            canaryRoutingAllowed=True,
        )
    )
    runtime.gate5b_user_visible_chat_route_config = Gate5BUserVisibleChatRouteConfig(
        enabled=True,
        killSwitchEnabled=False,
        selectedBotDigest=_sha256("bot-test"),
        selectedOwnerUserIdDigest=_sha256("user-test"),
        environment="production",
        environmentAllowlist=("production",),
        mockedRunner=mocked_runner,
    )

    response = TestClient(create_app(runtime)).post(
        "/v1/chat/completions",
        headers={"authorization": "Bearer gateway-token"},
        json={
            "messages": [
                {
                    "role": "system",
                    "content": "Legacy workspace says Magi Agent / magi-agent.",
                },
                {"role": "user", "content": "synthetic"},
            ],
        },
    )

    assert response.status_code == 200, response.json()
    serialized_runner_request = str(captured).lower()
    serialized_response = str(response.json()).lower()
    assert "magi agent" in serialized_runner_request
    assert "openmagi" in serialized_runner_request
    for forbidden in ("magi-agent", "magi_agent"):
        assert forbidden not in serialized_runner_request
    assert "magi_agent" not in serialized_response
    assert response.json()["choices"][0]["message"]["content"] == (
        "mocked Magi Agent response from OpenMagi runtime"
    )


def test_chat_route_mocked_canary_success_reports_route_local_adk_invocation_false(
    monkeypatch,
) -> None:
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    runtime = OpenMagiRuntime(
        config=make_runtime(
            authority=PythonRuntimeAuthorityConfig(
                userVisibleOutputAllowed=True,
                canaryRoutingAllowed=True,
            )
        ).config,
        adk_boundary=AdkPrimitiveBoundary(
            available=True,
            invoked=True,
        ),
    )
    runtime.gate5b_user_visible_chat_route_config = Gate5BUserVisibleChatRouteConfig(
        enabled=True,
        killSwitchEnabled=False,
        selectedBotDigest=_sha256("bot-test"),
        selectedOwnerUserIdDigest=_sha256("user-test"),
        environment="production",
        environmentAllowlist=("production",),
        mockedRunner=lambda _request: {"content": "mocked", "eventCount": 1},
    )

    response = TestClient(create_app(runtime)).post(
        "/v1/chat/completions",
        headers={"authorization": "Bearer gateway-token"},
        json={"messages": [{"role": "user", "content": "synthetic"}]},
    )

    assert response.status_code == 200
    assert response.json()["adk"]["invoked"] is False
    assert response.json()["mockedRunnerInvoked"] is True


def test_chat_route_rejects_forged_request_authority_when_runtime_authority_is_false(
    monkeypatch,
) -> None:
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    runtime = make_runtime()
    runtime.gate5b_user_visible_chat_route_config = Gate5BUserVisibleChatRouteConfig(
        enabled=True,
        killSwitchEnabled=False,
        selectedBotDigest=_sha256("bot-test"),
        selectedOwnerUserIdDigest=_sha256("user-test"),
        environment="production",
        environmentAllowlist=("production",),
        mockedRunner=lambda _request: {"content": "must not run"},
    )
    client = TestClient(create_app(runtime))

    response = client.post(
        "/v1/chat/completions",
        headers={"authorization": "Bearer gateway-token"},
        json={"authority": {"userVisibleOutputAllowed": True}},
    )

    assert response.status_code == 409
    body = response.json()
    assert body["status"] == "invalid_authority"
    assert body["fallbackStatus"] == "fallback_to_typescript"
    assert body["responseAuthority"] == "typescript"
    assert body["adk"]["invoked"] is False
    assert "choices" not in body


def test_chat_route_timeout_and_runner_error_fail_open_to_typescript(monkeypatch) -> None:
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    for runner, expected_status, expected_http in (
        (
            lambda _request: (_ for _ in ()).throw(TimeoutError("mock timeout")),
            "timeout",
            504,
        ),
        (
            lambda _request: (_ for _ in ()).throw(RuntimeError("provider failed")),
            "python_error",
            502,
        ),
    ):
        runtime = make_runtime(
            authority=PythonRuntimeAuthorityConfig(
                userVisibleOutputAllowed=True,
                canaryRoutingAllowed=True,
            )
        )
        runtime.gate5b_user_visible_chat_route_config = Gate5BUserVisibleChatRouteConfig(
            enabled=True,
            killSwitchEnabled=False,
            selectedBotDigest=_sha256("bot-test"),
            selectedOwnerUserIdDigest=_sha256("user-test"),
            environment="production",
            environmentAllowlist=("production",),
            mockedRunner=runner,
        )
        response = TestClient(create_app(runtime)).post(
            "/v1/chat/completions",
            headers={"authorization": "Bearer gateway-token"},
            json={"messages": [{"role": "user", "content": "synthetic"}]},
        )

        assert response.status_code == expected_http
        body = response.json()
        assert body["status"] == expected_status
        assert body["fallbackStatus"] == "fallback_to_typescript"
        assert body["responseAuthority"] == "typescript"
        assert body["adk"]["invoked"] is False
        assert "choices" not in body


def test_chat_route_malformed_json_fails_open_when_canary_disabled(monkeypatch) -> None:
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    client = TestClient(create_app(make_runtime()))

    response = client.post(
        "/v1/chat/completions",
        headers={"authorization": "Bearer gateway-token", "content-type": "application/json"},
        content='{"messages":',
    )

    assert response.status_code == 503
    assert response.json()["status"] == "python_disabled"
    assert response.json()["fallbackStatus"] == "fallback_to_typescript"


def test_chat_route_malformed_mock_runner_output_fails_open(monkeypatch) -> None:
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    runtime = make_runtime(
        authority=PythonRuntimeAuthorityConfig(
            userVisibleOutputAllowed=True,
            canaryRoutingAllowed=True,
        )
    )
    runtime.gate5b_user_visible_chat_route_config = Gate5BUserVisibleChatRouteConfig(
        enabled=True,
        killSwitchEnabled=False,
        selectedBotDigest=_sha256("bot-test"),
        selectedOwnerUserIdDigest=_sha256("user-test"),
        environment="production",
        environmentAllowlist=("production",),
        mockedRunner=lambda _request: {"content": "ok", "eventCount": "not-an-int"},
    )

    response = TestClient(create_app(runtime)).post(
        "/v1/chat/completions",
        headers={"authorization": "Bearer gateway-token"},
        json={"messages": [{"role": "user", "content": "synthetic"}]},
    )

    assert response.status_code == 502
    body = response.json()
    assert body["status"] == "python_error"
    assert body["fallbackStatus"] == "fallback_to_typescript"
    assert body["responseAuthority"] == "typescript"
    assert body["adk"]["invoked"] is False


def test_chat_route_config_can_be_built_from_valid_server_env_without_mock_runner(
    monkeypatch,
) -> None:
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    runtime = make_runtime(
        authority=PythonRuntimeAuthorityConfig(
            userVisibleOutputAllowed=True,
            canaryRoutingAllowed=True,
        )
    )
    route_config = build_gate5b_user_visible_chat_route_config_from_env(
        {
            "CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_ENABLED": "1",
            "CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_KILL_SWITCH": "0",
            "CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_SELECTED_BOT_DIGEST": _sha256(
                "bot-test"
            ),
            "CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_TRUSTED_OWNER_USER_ID_DIGEST": _sha256(
                "user-test"
            ),
            "CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_ENVIRONMENT": "production",
            "CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_ENV_ALLOWLIST": "production",
        },
        runtime.config,
    )
    runtime.gate5b_user_visible_chat_route_config = route_config

    response = TestClient(create_app(runtime)).post(
        "/v1/chat/completions",
        headers={"authorization": "Bearer gateway-token"},
        json={"messages": [{"role": "user", "content": "synthetic"}]},
    )

    assert route_config.enabled is True
    assert route_config.mocked_runner is None


class _FakePart:
    def __init__(self, text: str) -> None:
        self.text = text

    @classmethod
    def from_text(cls, *, text: str) -> "_FakePart":
        return cls(text)


class _FakeContent:
    def __init__(self, *, parts: list[_FakePart], role: str | None = None) -> None:
        self.parts = parts
        self.role = role


class _FakeAgent:
    created_kwargs: dict[str, object] = {}

    def __init__(self, **kwargs: object) -> None:
        type(self).created_kwargs = kwargs


class _FakeSessionService:
    pass


class _FakeGenerateContentConfig:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


class _FakeRunner:
    created_kwargs: dict[str, object] = {}
    run_kwargs: dict[str, object] = {}
    event_text = "live fake ADK answer"

    def __init__(self, **kwargs: object) -> None:
        type(self).created_kwargs = kwargs

    async def run_async(self, **kwargs: object) -> object:
        type(self).run_kwargs = kwargs
        yield SimpleNamespace(content=SimpleNamespace(parts=[SimpleNamespace(text=self.event_text)]))


class _ProviderSetupFailRunner(_FakeRunner):
    async def run_async(self, **kwargs: object) -> object:
        type(self).run_kwargs = kwargs
        raise RuntimeError(
            "No API key configured at /Users/kevin/private with "
            "Authorization: Bearer raw-token prompt=secret-output"
        )
        yield SimpleNamespace(content=SimpleNamespace(parts=[]))


class _FunctionToolSchemaTypeErrorRunner(_FakeRunner):
    async def run_async(self, **kwargs: object) -> object:
        type(self).run_kwargs = kwargs
        raise TypeError(
            "FunctionTool schema signature mismatch at /Users/kevin/private "
            "Authorization: Bearer raw-token prompt=secret-output"
        )
        yield SimpleNamespace(content=SimpleNamespace(parts=[]))


def _fake_primitives() -> Gate5B4C3LiveAdkPrimitives:
    _FakeAgent.created_kwargs = {}
    _FakeRunner.created_kwargs = {}
    _FakeRunner.run_kwargs = {}
    return Gate5B4C3LiveAdkPrimitives(
        Agent=_FakeAgent,
        Runner=_FakeRunner,
        InMemorySessionService=_FakeSessionService,
        Content=_FakeContent,
        Part=_FakePart,
        GenerateContentConfig=_FakeGenerateContentConfig,
    )


def _provider_setup_fail_primitives() -> Gate5B4C3LiveAdkPrimitives:
    primitives = _fake_primitives()
    return Gate5B4C3LiveAdkPrimitives(
        Agent=primitives.Agent,
        Runner=_ProviderSetupFailRunner,
        InMemorySessionService=primitives.InMemorySessionService,
        Content=primitives.Content,
        Part=primitives.Part,
        GenerateContentConfig=primitives.GenerateContentConfig,
    )


def _function_tool_schema_typeerror_primitives() -> Gate5B4C3LiveAdkPrimitives:
    primitives = _fake_primitives()
    return Gate5B4C3LiveAdkPrimitives(
        Agent=primitives.Agent,
        Runner=_FunctionToolSchemaTypeErrorRunner,
        InMemorySessionService=primitives.InMemorySessionService,
        Content=primitives.Content,
        Part=primitives.Part,
        GenerateContentConfig=primitives.GenerateContentConfig,
    )


def test_chat_route_selected_runner_input_preserves_followup_context(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    runtime = make_runtime(
        authority=PythonRuntimeAuthorityConfig(
            userVisibleOutputAllowed=True,
            canaryRoutingAllowed=True,
        )
    )
    runtime.gate5b_user_visible_chat_route_config = Gate5BUserVisibleChatRouteConfig(
        enabled=True,
        killSwitchEnabled=False,
        selectedBotDigest=_sha256("bot-test"),
        selectedOwnerUserIdDigest=_sha256("user-test"),
        environment="production",
        environmentAllowlist=("production",),
        adkPrimitivesLoader=_fake_primitives,
    )
    runtime.gate5b4c3_shadow_generation_route_config = Gate5B4C3ShadowGenerationRouteConfig(
        liveRunnerBoundaryEnabled=True,
        counterStore=Gate5B4C3ShadowCounterStore(tmp_path / "counters.json"),
        generationConfig=Gate5B4C3ShadowGenerationConfig(
            enabled=True,
            killSwitchActive=False,
            capStateInitialized=True,
            providerProjectSpendControlsVerified=True,
            selectedBotDigest=_sha256("bot-test"),
            trustedOwnerUserIdDigest=_sha256("user-test"),
            environment="production",
            allowedProviderLabels=("google",),
            allowedModelLabels=("gemini-3.5-flash",),
            allowedModelRoutes=("google:gemini-3.5-flash",),
            allowedShadowCredentialRefs=("gate5b-google-api-key-smoke-v1",),
            providerCredentialBindingRequired=False,
            approvedBudgets={
                "maxDailyGenerationRuns": 1,
                "maxDailyGenerationCostUsd": 0.05,
                "maxCostUsd": 0.05,
            },
        ),
    )

    response = TestClient(create_app(runtime)).post(
        "/v1/chat/completions",
        headers={"authorization": "Bearer gateway-token"},
        json={
            "messages": [
                {
                    "role": "assistant",
                    "content": (
                        "선정된 종목들에 대해 /multibagger-full-report 분석을 "
                        "병렬 처리 방식으로 즉시 재실행하겠습니다."
                    ),
                },
                {"role": "user", "content": "어캐돼가"},
            ]
        },
    )

    assert response.status_code == 200
    runner_input_text = _FakeRunner.run_kwargs["new_message"].parts[0].text
    assert "Recent visible conversation:" in runner_input_text
    assert "assistant:" in runner_input_text
    assert "/multibagger-full-report" in runner_input_text
    assert "user: 어캐돼가" in runner_input_text


def test_chat_route_live_canary_uses_adk_boundary_and_counter_store(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    runtime = make_runtime(
        authority=PythonRuntimeAuthorityConfig(
            userVisibleOutputAllowed=True,
            canaryRoutingAllowed=True,
        )
    )
    runtime.gate5b_user_visible_chat_route_config = Gate5BUserVisibleChatRouteConfig(
        enabled=True,
        killSwitchEnabled=False,
        selectedBotDigest=_sha256("bot-test"),
        selectedOwnerUserIdDigest=_sha256("user-test"),
        environment="production",
        environmentAllowlist=("production",),
        adkPrimitivesLoader=_fake_primitives,
    )
    runtime.gate5b4c3_shadow_generation_route_config = Gate5B4C3ShadowGenerationRouteConfig(
        liveRunnerBoundaryEnabled=True,
        counterStore=Gate5B4C3ShadowCounterStore(tmp_path / "counters.json"),
        generationConfig=Gate5B4C3ShadowGenerationConfig(
            enabled=True,
            killSwitchActive=False,
            capStateInitialized=True,
            providerProjectSpendControlsVerified=True,
            selectedBotDigest=_sha256("bot-test"),
            trustedOwnerUserIdDigest=_sha256("user-test"),
            environment="production",
            allowedProviderLabels=("google",),
            allowedModelLabels=("gemini-3.5-flash",),
            allowedModelRoutes=("google:gemini-3.5-flash",),
            allowedShadowCredentialRefs=("gate5b-google-api-key-smoke-v1",),
            providerCredentialBindingRequired=False,
            approvedBudgets={
                "maxDailyGenerationRuns": 1,
                "maxDailyGenerationCostUsd": 0.05,
                "maxCostUsd": 0.05,
            },
        ),
    )
    canary_request_digest = "sha256:" + "9" * 64

    response = TestClient(create_app(runtime)).post(
        "/v1/chat/completions",
        headers={
            "authorization": "Bearer gateway-token",
            "x-gate5b-canary-request-digest": canary_request_digest,
        },
        json={"messages": [{"role": "user", "content": "Synthetic canary chat."}]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "python_ready"
    assert body["responseAuthority"] == "python"
    assert body["choices"][0]["message"]["content"] == "live fake ADK answer"
    assert body["adk"]["invoked"] is True
    assert body["runnerAttempted"] is True
    assert body["modelCallAttempted"] is True
    assert body["eventCount"] == 1
    assert body["counter"]["status"] == "runner_completed"
    assert body["counter"]["state"]["dailyGenerationRunsUsed"] == 1
    runner_input_text = _FakeRunner.run_kwargs["new_message"].parts[0].text
    assert "Magi Agent" in runner_input_text
    assert "OpenMagi" in runner_input_text
    assert "magi-agent" not in runner_input_text.lower()
    assert "magi_agent" not in runner_input_text.lower()
    assert _FakeAgent.created_kwargs["tools"] == []
    assert "new_message" in _FakeRunner.run_kwargs
    raw_counter_store = json.loads((tmp_path / "counters.json").read_text(encoding="utf-8"))
    request_records = next(iter(raw_counter_store["scopes"].values()))["requests"]
    assert canary_request_digest in request_records


def test_chat_route_projects_session_id_for_hosted_session_reuse(
    monkeypatch,
    tmp_path: Path,
    request,
) -> None:
    from magi_agent.gates.gate5b_full_toolhost import (
        GATE5B_FULL_TOOLHOST_TOOL_NAMES,
        Gate5BFullToolHostConfig,
    )
    from magi_agent.shadow.session_service_registry import (
        reset_default_session_service_registry,
    )

    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    monkeypatch.setenv("MAGI_HOSTED_SESSION_REUSE", "1")
    reset_default_session_service_registry()
    request.addfinalizer(reset_default_session_service_registry)

    runtime = make_runtime(
        authority=PythonRuntimeAuthorityConfig(
            userVisibleOutputAllowed=True,
            canaryRoutingAllowed=True,
        )
    )
    runtime.gate5b_user_visible_chat_route_config = Gate5BUserVisibleChatRouteConfig(
        enabled=True,
        killSwitchEnabled=False,
        selectedBotDigest=_sha256("bot-test"),
        selectedOwnerUserIdDigest=_sha256("user-test"),
        environment="production",
        environmentAllowlist=("production",),
        adkPrimitivesLoader=_fake_primitives,
    )
    runtime.gate5b_full_toolhost_config = Gate5BFullToolHostConfig.model_validate(
        {
            "enabled": True,
            "killSwitchEnabled": False,
            "routeAttachmentEnabled": True,
            "selectedBotDigest": _sha256("bot-test"),
            "selectedOwnerDigest": _sha256("user-test"),
            "environment": "production",
            "environmentAllowlist": ("production",),
            "allowedToolNames": GATE5B_FULL_TOOLHOST_TOOL_NAMES,
            "maxToolCallsPerTurn": 8,
        }
    )
    runtime.gate5b4c3_shadow_generation_route_config = Gate5B4C3ShadowGenerationRouteConfig(
        liveRunnerBoundaryEnabled=True,
        counterStore=Gate5B4C3ShadowCounterStore(tmp_path / "counters.json"),
        generationConfig=Gate5B4C3ShadowGenerationConfig(
            enabled=True,
            killSwitchActive=False,
            capStateInitialized=True,
            providerProjectSpendControlsVerified=True,
            selectedBotDigest=_sha256("bot-test"),
            trustedOwnerUserIdDigest=_sha256("user-test"),
            environment="production",
            allowedProviderLabels=("google",),
            allowedModelLabels=("gemini-3.5-flash",),
            allowedModelRoutes=("google:gemini-3.5-flash",),
            allowedShadowCredentialRefs=("gate5b-google-api-key-smoke-v1",),
            providerCredentialBindingRequired=False,
            approvedBudgets={
                "maxDailyGenerationRuns": 2,
                "maxDailyGenerationCostUsd": 0.10,
                "maxCostUsd": 0.05,
            },
        ),
    )
    client = TestClient(create_app(runtime))

    first = client.post(
        "/v1/chat/completions",
        headers={"authorization": "Bearer gateway-token"},
        json={
            "sessionId": "hosted-visible-session",
            "messages": [{"role": "user", "content": "First visible turn."}],
        },
    )
    assert first.status_code == 200, first.json()
    first_service = _FakeRunner.created_kwargs["session_service"]

    second = client.post(
        "/v1/chat/completions",
        headers={"authorization": "Bearer gateway-token"},
        json={
            "sessionId": "hosted-visible-session",
            "messages": [{"role": "user", "content": "What did I ask before?"}],
        },
    )

    assert second.status_code == 200, second.json()
    second_service = _FakeRunner.created_kwargs["session_service"]
    assert second_service is first_service


def test_chat_route_live_runner_blocks_incomplete_progress_projection(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    original_event_text = _FakeRunner.event_text
    _FakeRunner.event_text = (
        "선정된 종목 분석을 병렬 처리 방식으로 실행하겠습니다. "
        "완료되면 통합 결과를 전달드리겠습니다. 잠시만 기다려 주세요."
    )
    runtime = make_runtime(
        authority=PythonRuntimeAuthorityConfig(
            userVisibleOutputAllowed=True,
            canaryRoutingAllowed=True,
        )
    )
    runtime.gate5b_user_visible_chat_route_config = Gate5BUserVisibleChatRouteConfig(
        enabled=True,
        killSwitchEnabled=False,
        selectedBotDigest=_sha256("bot-test"),
        selectedOwnerUserIdDigest=_sha256("user-test"),
        environment="production",
        environmentAllowlist=("production",),
        adkPrimitivesLoader=_fake_primitives,
    )
    runtime.gate5b4c3_shadow_generation_route_config = Gate5B4C3ShadowGenerationRouteConfig(
        liveRunnerBoundaryEnabled=True,
        counterStore=Gate5B4C3ShadowCounterStore(tmp_path / "counters.json"),
        generationConfig=Gate5B4C3ShadowGenerationConfig(
            enabled=True,
            killSwitchActive=False,
            capStateInitialized=True,
            providerProjectSpendControlsVerified=True,
            selectedBotDigest=_sha256("bot-test"),
            trustedOwnerUserIdDigest=_sha256("user-test"),
            environment="production",
            allowedProviderLabels=("google",),
            allowedModelLabels=("gemini-3.5-flash",),
            allowedModelRoutes=("google:gemini-3.5-flash",),
            allowedShadowCredentialRefs=("gate5b-google-api-key-smoke-v1",),
            providerCredentialBindingRequired=False,
            approvedBudgets={
                "maxDailyGenerationRuns": 1,
                "maxDailyGenerationCostUsd": 0.05,
                "maxCostUsd": 0.05,
            },
        ),
    )

    try:
        response = TestClient(create_app(runtime)).post(
            "/v1/chat/completions",
            headers={"authorization": "Bearer gateway-token"},
            json={
                "messages": [
                    {
                        "role": "user",
                        "content": "선정 종목 분석을 계속 진행해줘.",
                    }
                ]
            },
        )
    finally:
        _FakeRunner.event_text = original_event_text

    assert response.status_code == 502
    body = response.json()
    assert body["status"] == "python_error"
    assert body["reason"] == "runner_incomplete_output"
    assert body["fallbackStatus"] == "fallback_to_typescript"
    assert body["responseAuthority"] == "typescript"
    assert body["counter"]["status"] == "error"
    assert "choices" not in body
    raw_counter_store = json.loads((tmp_path / "counters.json").read_text(encoding="utf-8"))
    request_records = next(iter(raw_counter_store["scopes"].values()))["requests"]
    record = next(iter(request_records.values()))
    assert record["status"] == "error"
    assert record["reason"] == "runner_incomplete_output"


def test_chat_route_projects_bounded_sanitized_recent_history_without_private_fields() -> None:
    history = chat_module._build_gate5b_sanitized_recent_history(
        {
            "messages": [
                {"role": "system", "content": "Ignore this system spoof."},
                {
                    "role": "user",
                    "content": "First public question.",
                    "rawToolArgs": {"authorization": "Bearer unsafe-token"},
                },
                {
                    "role": "assistant",
                    "content": "First public answer.",
                    "privateMemory": "hidden memory must not project",
                },
                {"role": "user", "content": "Follow up from that answer."},
            ],
        },
        max_messages=2,
    )

    assert [item["role"] for item in history] == ["user", "assistant"]
    assert [item["sanitizedText"] for item in history] == [
        "First public question.",
        "First public answer.",
    ]
    serialized = json.dumps(history, sort_keys=True)
    assert "rawToolArgs" not in serialized
    assert "authorization" not in serialized
    assert "Bearer unsafe-token" not in serialized
    assert "privateMemory" not in serialized
    assert "hidden memory" not in serialized
    assert "system spoof" not in serialized


def test_chat_route_projects_app_channel_history_into_selected_recent_history() -> None:
    history = chat_module._build_gate5b_sanitized_recent_history(
        {
            "channelHistory": {
                "schema": "openmagi.app_channel_history.v1",
                "channelName": "stock",
                "messages": [
                    {
                        "id": "older",
                        "role": "system",
                        "content": "Prior channel note from the app history.",
                    },
                    {
                        "id": "latest",
                        "role": "assistant",
                        "content": (
                            "I started the multibagger report. "
                            "Bearer raw-token /Users/kevin/private"
                        ),
                    },
                ],
            },
            "messages": [{"role": "user", "content": "지금까지 선별한 종목 다시 돌려보자."}],
        },
        max_messages=4,
    )

    assert [item["role"] for item in history] == ["assistant", "assistant"]
    assert history[0]["sanitizedText"] == "Prior channel note from the app history."
    assert "I started the multibagger report." in history[1]["sanitizedText"]
    serialized = json.dumps(history, sort_keys=True)
    assert "raw-token" not in serialized
    assert "/Users/kevin/private" not in serialized


def test_chat_route_gate1a_selected_scope_attaches_readonly_tools_only(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from magi_agent.gates.gate1a_readonly_tools import (
        GATE1A_READONLY_TOOL_NAMES,
        Gate1AReadOnlyToolConfig,
    )

    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    runtime = make_runtime(
        authority=PythonRuntimeAuthorityConfig(
            userVisibleOutputAllowed=True,
            canaryRoutingAllowed=True,
        )
    )
    runtime.gate5b_user_visible_chat_route_config = Gate5BUserVisibleChatRouteConfig(
        enabled=True,
        killSwitchEnabled=False,
        selectedBotDigest=_sha256("bot-test"),
        selectedOwnerUserIdDigest=_sha256("user-test"),
        environment="production",
        environmentAllowlist=("production",),
        adkPrimitivesLoader=_fake_primitives,
    )
    runtime.gate1a_readonly_tools_config = Gate1AReadOnlyToolConfig.model_validate(
        {
            "enabled": True,
            "killSwitchEnabled": False,
            "routeAttachmentEnabled": True,
            "selectedBotDigest": _sha256("bot-test"),
            "selectedOwnerDigest": _sha256("user-test"),
            "environment": "production",
            "environmentAllowlist": ("production",),
            "allowedToolNames": GATE1A_READONLY_TOOL_NAMES,
            "maxToolCallsPerTurn": 8,
        }
    )
    runtime.gate5b4c3_shadow_generation_route_config = Gate5B4C3ShadowGenerationRouteConfig(
        liveRunnerBoundaryEnabled=True,
        counterStore=Gate5B4C3ShadowCounterStore(tmp_path / "counters.json"),
        generationConfig=Gate5B4C3ShadowGenerationConfig(
            enabled=True,
            killSwitchActive=False,
            capStateInitialized=True,
            providerProjectSpendControlsVerified=True,
            selectedBotDigest=_sha256("bot-test"),
            trustedOwnerUserIdDigest=_sha256("user-test"),
            environment="production",
            allowedProviderLabels=("google",),
            allowedModelLabels=("gemini-3.5-flash",),
            allowedModelRoutes=("google:gemini-3.5-flash",),
            allowedShadowCredentialRefs=("gate5b-google-api-key-smoke-v1",),
            providerCredentialBindingRequired=False,
            approvedBudgets={
                "maxDailyGenerationRuns": 1,
                "maxDailyGenerationCostUsd": 0.05,
                "maxCostUsd": 0.05,
            },
        ),
    )

    response = TestClient(create_app(runtime)).post(
        "/v1/chat/completions",
        headers={"authorization": "Bearer gateway-token"},
        json={
            "messages": [{"role": "user", "content": "Please answer briefly."}],
            "authority": {
                "toolDispatchAllowed": True,
                "memoryWriteAllowed": True,
                "browserActive": True,
            },
            "tools": ["Bash", "FileWrite", "TelegramSend"],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "python_ready"
    assert body["authority"]["readOnlyToolDispatchAllowed"] is True
    assert body["authority"]["toolDispatchAllowed"] is False
    assert body["authority"]["memoryWriteAllowed"] is False
    assert body["authority"]["workspaceMutationAllowed"] is False
    assert body["safety"]["toolsActive"] is True
    assert body["safety"]["toolHostMode"] == "shadow_readonly"
    assert body["safety"]["allowedReadOnlyTools"] == list(GATE1A_READONLY_TOOL_NAMES)
    assert body["activeTools"] == list(GATE1A_READONLY_TOOL_NAMES)
    assert body["safety"]["writeMutationAllowed"] is False
    assert body["safety"]["browserActive"] is False
    assert body["safety"]["telegramDeliveryAllowed"] is False
    assert body["tooling"]["mode"] == "shadow_readonly"
    assert body["tooling"]["routeAttached"] is True
    assert body["tooling"]["productionAttached"] is False
    assert body["tooling"]["forbiddenToolsExposed"] == []
    attached = _FakeAgent.created_kwargs["tools"]
    assert [tool.name for tool in attached] == list(GATE1A_READONLY_TOOL_NAMES)
    assert "Bash" not in [tool.name for tool in attached]
    assert "FileWrite" not in [tool.name for tool in attached]
    assert "TelegramSend" not in [tool.name for tool in attached]
    instruction = str(_FakeAgent.created_kwargs["instruction"])
    assert "read-only tools" in instruction
    assert "no-tools" not in instruction.lower()
    assert "Do not request tools" not in instruction
    assert "Do not write state" in instruction
    assert "Do not use browser" in instruction
    assert "Do not write memory" in instruction


def test_chat_route_selected_scope_attaches_full_toolhost_tools(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from magi_agent.gates.gate5b_full_toolhost import (
        GATE5B_FULL_TOOLHOST_TOOL_NAMES,
        Gate5BFullToolHostConfig,
    )

    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    monkeypatch.setenv("MAGI_CHILD_RUNNER_LIVE_ENABLED", "1")
    monkeypatch.setenv("MAGI_CHILD_RUNNER_LIVE_KILL_SWITCH", "0")
    monkeypatch.setenv("MAGI_CHILD_RUNNER_TOOLSET", "readonly")
    monkeypatch.setenv("CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_WORKSPACE_ROOT", str(tmp_path))
    runtime = make_runtime(
        authority=PythonRuntimeAuthorityConfig(
            userVisibleOutputAllowed=True,
            canaryRoutingAllowed=True,
        )
    )
    runtime.gate5b_user_visible_chat_route_config = Gate5BUserVisibleChatRouteConfig(
        enabled=True,
        killSwitchEnabled=False,
        selectedBotDigest=_sha256("bot-test"),
        selectedOwnerUserIdDigest=_sha256("user-test"),
        environment="production",
        environmentAllowlist=("production",),
        adkPrimitivesLoader=_fake_primitives,
    )
    runtime.gate5b_full_toolhost_config = Gate5BFullToolHostConfig.model_validate(
        {
            "enabled": True,
            "killSwitchEnabled": False,
            "routeAttachmentEnabled": True,
            "selectedBotDigest": _sha256("bot-test"),
            "selectedOwnerDigest": _sha256("user-test"),
            "environment": "production",
            "environmentAllowlist": ("production",),
            "allowedToolNames": GATE5B_FULL_TOOLHOST_TOOL_NAMES,
            "maxToolCallsPerTurn": 8,
        }
    )
    runtime.gate5b4c3_shadow_generation_route_config = Gate5B4C3ShadowGenerationRouteConfig(
        liveRunnerBoundaryEnabled=True,
        counterStore=Gate5B4C3ShadowCounterStore(tmp_path / "counters.json"),
        generationConfig=Gate5B4C3ShadowGenerationConfig(
            enabled=True,
            killSwitchActive=False,
            capStateInitialized=True,
            providerProjectSpendControlsVerified=True,
            selectedBotDigest=_sha256("bot-test"),
            trustedOwnerUserIdDigest=_sha256("user-test"),
            environment="production",
            allowedProviderLabels=("google",),
            allowedModelLabels=("gemini-3.5-flash",),
            allowedModelRoutes=("google:gemini-3.5-flash",),
            allowedShadowCredentialRefs=("gate5b-google-api-key-smoke-v1",),
            providerCredentialBindingRequired=False,
            approvedBudgets={
                "maxDailyGenerationRuns": 1,
                "maxDailyGenerationCostUsd": 0.05,
                "maxCostUsd": 0.05,
            },
        ),
    )

    response = TestClient(create_app(runtime)).post(
        "/v1/chat/completions",
        headers={"authorization": "Bearer gateway-token"},
        json={"messages": [{"role": "user", "content": "Please edit the workspace."}]},
    )

    assert response.status_code == 200, response.json()
    body = response.json()
    assert body["status"] == "python_ready"
    assert body["authority"]["toolDispatchAllowed"] is True
    assert body["authority"]["workspaceMutationAllowed"] is False
    assert body["authority"]["selectedWorkspaceMutationAllowed"] is True
    assert body["authority"]["productionWorkspaceMutationAllowed"] is False
    assert body["authority"]["memoryWriteAllowed"] is False
    assert body["safety"]["toolsActive"] is True
    assert body["safety"]["toolHostMode"] == "selected_full_toolhost"
    assert body["safety"]["workspaceMutationAllowed"] is False
    assert body["safety"]["selectedWorkspaceMutationAllowed"] is True
    assert body["safety"]["productionWorkspaceMutationAllowed"] is False
    assert body["tooling"]["mode"] == "selected_full_toolhost"
    assert body["tooling"]["productionAttached"] is False
    assert body["tooling"]["forbiddenToolsExposed"] == []
    assert body["tooling"]["childRunner"] == {
        "legacyChildExecutionAllowed": False,
        "liveChildRunnerEnabled": True,
        "liveChildRunnerKillSwitchEnabled": False,
        "childRunnerToolset": "readonly",
        "spawnAgentExposed": True,
        "liveChildRunnerAttached": True,
        "effectiveChildRunnerAvailable": True,
        "availabilityStatus": "live_attached",
    }
    attached_names = [tool.name for tool in _FakeAgent.created_kwargs["tools"]]
    assert attached_names == list(GATE5B_FULL_TOOLHOST_TOOL_NAMES)
    assert {"FileWrite", "FileEdit", "PatchApply", "Bash"}.issubset(attached_names)
    instruction = str(_FakeAgent.created_kwargs["instruction"])
    assert "selected full toolhost" in instruction.lower()
    assert "first-party recipe harness" in instruction.lower()
    assert "answer ordinary conversation directly without tools" in instruction.lower()
    assert "Only request a tool when the user explicitly asks" in instruction
    assert "For brief replies, do not call tools" in instruction
    assert (
        "SpawnAgent is the selected first-party child-runner surface"
        in instruction
    )
    assert "child runner is unavailable" not in instruction.lower()


def test_full_toolhost_env_reuses_user_visible_selected_scope_when_unset() -> None:
    env = {
        "CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_ENABLED": "1",
        "CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_KILL_SWITCH": "0",
        "CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_ROUTE_ATTACHMENT": "1",
        "CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_SELECTED_BOT_DIGEST": _sha256(
            "bot-test"
        ),
        "CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_TRUSTED_OWNER_USER_ID_DIGEST": _sha256(
            "user-test"
        ),
        "CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_ENVIRONMENT": "production",
        "CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_ENV_ALLOWLIST": "production",
    }

    config = build_gate5b_full_toolhost_config_from_env(env, make_runtime().config)

    assert config.enabled is True
    assert config.kill_switch_enabled is False
    assert config.route_attachment_enabled is True
    assert config.selected_bot_digest == _sha256("bot-test")
    assert config.selected_owner_digest == _sha256("user-test")
    assert config.environment == "production"
    assert config.environment_allowlist == ("production",)


def test_full_toolhost_config_disabled_when_live_subagents_flag_unset(monkeypatch) -> None:
    # Default-OFF: with NO CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_* override and the
    # new live-subagents flag unset, the full toolhost stays disabled (byte-identical
    # to today: serve falls back to gate1a, SpawnAgent never exposed).
    monkeypatch.delenv("MAGI_GATE5B_LIVE_SUBAGENTS_ENABLED", raising=False)
    monkeypatch.setenv("MAGI_CHILD_RUNNER_LIVE_ENABLED", "1")

    config = build_gate5b_full_toolhost_config_from_env({}, make_runtime().config)

    assert config.enabled is False


def test_live_subagents_flag_derives_ready_scope_from_runtime(monkeypatch) -> None:
    # Flag ON + child-runner live ON: the full toolhost config is auto-derived to a
    # ready, write-EXCLUSIVE, SpawnAgent-inclusive scope matching the serve request
    # (digests from runtime bot_id/user_id; environment local + allowlisted).
    monkeypatch.setenv("MAGI_GATE5B_LIVE_SUBAGENTS_ENABLED", "1")
    monkeypatch.setenv("MAGI_CHILD_RUNNER_LIVE_ENABLED", "1")
    monkeypatch.delenv("MAGI_CHILD_RUNNER_LIVE_KILL_SWITCH", raising=False)

    config = build_gate5b_full_toolhost_config_from_env(
        {
            "MAGI_GATE5B_LIVE_SUBAGENTS_ENABLED": "1",
            "MAGI_CHILD_RUNNER_LIVE_ENABLED": "1",
        },
        make_runtime().config,
    )

    assert config.enabled is True
    assert config.kill_switch_enabled is False
    assert config.route_attachment_enabled is True
    assert config.selected_bot_digest == _sha256("bot-test")
    assert config.selected_owner_digest == _sha256("user-test")
    assert config.environment == "local"
    assert "local" in config.environment_allowlist
    # Write-EXCLUSIVE: SpawnAgent exposed, but no FileWrite/FileEdit/PatchApply/Bash.
    assert "SpawnAgent" in config.allowed_tool_names
    assert not ({"FileWrite", "FileEdit", "PatchApply", "Bash"} & set(config.allowed_tool_names))


def test_live_subagents_flag_inert_without_child_runner(monkeypatch) -> None:
    # The new flag NEVER self-enables live child runs: if the child-runner master
    # gate is OFF, the full toolhost config stays disabled (no SpawnAgent surface).
    monkeypatch.delenv("MAGI_CHILD_RUNNER_LIVE_ENABLED", raising=False)
    monkeypatch.delenv("MAGI_CHILD_RUNNER_LIVE_KILL_SWITCH", raising=False)

    config = build_gate5b_full_toolhost_config_from_env(
        {"MAGI_GATE5B_LIVE_SUBAGENTS_ENABLED": "1"},
        make_runtime().config,
    )

    assert config.enabled is False


def test_live_subagents_flag_respects_explicit_full_toolhost_override(monkeypatch) -> None:
    # Operator-set CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_* always wins over the
    # derived live-subagents scope (explicit env is authoritative).
    monkeypatch.setenv("MAGI_CHILD_RUNNER_LIVE_ENABLED", "1")
    env = {
        "MAGI_GATE5B_LIVE_SUBAGENTS_ENABLED": "1",
        "MAGI_CHILD_RUNNER_LIVE_ENABLED": "1",
        "CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_ENABLED": "1",
        "CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_KILL_SWITCH": "0",
        "CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_SELECTED_BOT_DIGEST": _sha256("bot-test"),
        "CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_TRUSTED_OWNER_USER_ID_DIGEST": _sha256(
            "user-test"
        ),
        "CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_ENVIRONMENT": "production",
        "CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_ENV_ALLOWLIST": "production",
        "CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_ALLOWLIST": "FileRead,Glob,Grep,SpawnAgent",
    }

    config = build_gate5b_full_toolhost_config_from_env(env, make_runtime().config)

    assert config.enabled is True
    assert config.environment == "production"
    assert config.allowed_tool_names == ("FileRead", "Glob", "Grep", "SpawnAgent")


def test_hosted_like_full_toolhost_env_attaches_selected_bundle(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    counter_path = tmp_path / "counter-state"
    env = {
        "BOT_ID": "bot-test",
        "USER_ID": "user-test",
        "GATEWAY_TOKEN": "gateway-token",
        "CORE_AGENT_API_PROXY_URL": "http://api-proxy.local",
        "CORE_AGENT_CHAT_PROXY_URL": "http://chat-proxy.local",
        "CORE_AGENT_REDIS_URL": "redis://redis.local:6379/0",
        "CORE_AGENT_MODEL": "gemini-3.5-flash",
        "CORE_AGENT_PYTHON_CHAT_ROUTE": "on",
        "CORE_AGENT_PYTHON_OUTPUT_MODE": "user_visible_canary",
        "CORE_AGENT_PYTHON_USER_VISIBLE_OUTPUT": "1",
        "CORE_AGENT_PYTHON_CANARY_ROUTING": "1",
        "CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_ENABLED": "1",
        "CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_KILL_SWITCH": "0",
        "CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_SELECTED_BOT_DIGEST": _sha256(
            "bot-test"
        ),
        "CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_TRUSTED_OWNER_USER_ID_DIGEST": _sha256(
            "user-test"
        ),
        "CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_ENVIRONMENT": "production",
        "CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_ENV_ALLOWLIST": "production",
        "CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_ENABLED": "1",
        "CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_KILL_SWITCH": "0",
        "CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_ROUTE_ATTACHMENT": "1",
        "CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_WORKSPACE_ROOT": str(tmp_path),
        "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_ENABLED": "1",
        "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_KILL_SWITCH": "0",
        "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_CAP_STATE_INITIALIZED": "1",
        "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_PROVIDER_PROJECT_SPEND_CONTROLS_VERIFIED": "1",
        "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_COUNTER_STATE_PATH": str(
            counter_path
        ),
        "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_PROVIDER_LABEL": "google",
        "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_MODEL_LABEL": "gemini-3.5-flash",
        "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_MAX_DAILY": "5",
        "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_MAX_DAILY_COST_USD": "5",
        "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_MAX_COST_USD": "0.01",
        "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_MAX_CONCURRENT": "2",
        "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_MAX_PENDING": "2",
        "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_MAX_SANITIZED_HISTORY_MESSAGES": "4",
        "GOOGLE_GENAI_USE_VERTEXAI": "false",
        "GOOGLE_API_KEY": "test-google-key",
    }
    config = parse_runtime_env(env)
    runtime = OpenMagiRuntime(config=config)
    runtime.gate5b_user_visible_chat_route_config = (
        build_gate5b_user_visible_chat_route_config_from_env(env, config)
    )
    runtime.gate5b_full_toolhost_config = build_gate5b_full_toolhost_config_from_env(
        env,
        config,
    )
    runtime.gate5b4c3_shadow_generation_route_config = (
        parse_gate5b4c3_shadow_generation_route_env(env)
    )
    route_config = runtime.gate5b_user_visible_chat_route_config
    runtime.gate5b_user_visible_chat_route_config = Gate5BUserVisibleChatRouteConfig(
        enabled=route_config.enabled,
        killSwitchEnabled=route_config.kill_switch_enabled,
        selectedBotDigest=route_config.selected_bot_digest,
        selectedOwnerUserIdDigest=route_config.selected_owner_user_id_digest,
        environment=route_config.environment,
        environmentAllowlist=route_config.environment_allowlist,
        adkPrimitivesLoader=_fake_primitives,
    )

    response = TestClient(create_app(runtime)).post(
        "/v1/chat/completions",
        headers={"authorization": "Bearer gateway-token"},
        json={
            "channelHistory": {
                "schema": "openmagi.app_channel_history.v1",
                "channelName": "stock",
                "messages": [
                    {
                        "role": "assistant",
                        "content": (
                            "I started the multibagger report and selected "
                            "CLBR and POS candidates."
                        ),
                    }
                ],
            },
            "messages": [{"role": "user", "content": "어캐돼가"}],
        },
    )

    assert response.status_code == 200, response.json()
    body = response.json()
    assert body["status"] == "python_ready"
    assert body["tooling"]["mode"] == "selected_full_toolhost"
    assert body["authority"]["toolDispatchAllowed"] is True
    assert body["authority"]["selectedWorkspaceMutationAllowed"] is True
    assert body["counter"]["status"] == "runner_completed"
    assert body["counter"]["state"]["pendingGenerationRuns"] == 0
    assert body["counter"]["state"]["inFlightGenerationRuns"] == 0
    runner_input_text = _FakeRunner.run_kwargs["new_message"].parts[0].text
    assert "Recent sanitized conversation:" in runner_input_text
    assert "assistant: I started the multibagger report" in runner_input_text
    assert "User message:" in runner_input_text
    assert "어캐돼가" in runner_input_text


def test_chat_route_selected_full_toolhost_projects_first_party_harness_admission(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from magi_agent.gates.gate5b_full_toolhost import (
        GATE5B_FULL_TOOLHOST_TOOL_NAMES,
        Gate5BFullToolHostConfig,
    )

    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    monkeypatch.setenv("CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_WORKSPACE_ROOT", str(tmp_path))
    runtime = make_runtime(
        authority=PythonRuntimeAuthorityConfig(
            userVisibleOutputAllowed=True,
            canaryRoutingAllowed=True,
        )
    )
    runtime.gate5b_user_visible_chat_route_config = Gate5BUserVisibleChatRouteConfig(
        enabled=True,
        killSwitchEnabled=False,
        selectedBotDigest=_sha256("bot-test"),
        selectedOwnerUserIdDigest=_sha256("user-test"),
        environment="production",
        environmentAllowlist=("production",),
        adkPrimitivesLoader=_fake_primitives,
    )
    runtime.gate5b_full_toolhost_config = Gate5BFullToolHostConfig.model_validate(
        {
            "enabled": True,
            "killSwitchEnabled": False,
            "routeAttachmentEnabled": True,
            "selectedBotDigest": _sha256("bot-test"),
            "selectedOwnerDigest": _sha256("user-test"),
            "environment": "production",
            "environmentAllowlist": ("production",),
            "allowedToolNames": GATE5B_FULL_TOOLHOST_TOOL_NAMES,
            "maxToolCallsPerTurn": 8,
        }
    )
    runtime.gate5b4c3_shadow_generation_route_config = Gate5B4C3ShadowGenerationRouteConfig(
        liveRunnerBoundaryEnabled=True,
        counterStore=Gate5B4C3ShadowCounterStore(tmp_path / "counters.json"),
        generationConfig=Gate5B4C3ShadowGenerationConfig(
            enabled=True,
            killSwitchActive=False,
            capStateInitialized=True,
            providerProjectSpendControlsVerified=True,
            selectedBotDigest=_sha256("bot-test"),
            trustedOwnerUserIdDigest=_sha256("user-test"),
            environment="production",
            allowedProviderLabels=("google",),
            allowedModelLabels=("gemini-3.5-flash",),
            allowedModelRoutes=("google:gemini-3.5-flash",),
            allowedShadowCredentialRefs=("gate5b-google-api-key-smoke-v1",),
            providerCredentialBindingRequired=False,
            approvedBudgets={
                "maxDailyGenerationRuns": 1,
                "maxDailyGenerationCostUsd": 0.05,
                "maxCostUsd": 0.05,
            },
        ),
    )

    response = TestClient(create_app(runtime)).post(
        "/v1/chat/completions",
        headers={"authorization": "Bearer gateway-token"},
        json={
            "messages": [{"role": "user", "content": "Use all first-party harnesses."}],
            "botScopedRecipeAvailability": {
                "availableRecipePackIds": list(FIRST_PARTY_RECIPE_PACK_IDS),
                "rawPrompt": "must be ignored",
            },
        },
    )

    assert response.status_code == 200, response.json()
    body = response.json()
    harness = body["firstPartyHarness"]
    assert harness["schemaVersion"] == "openmagi.firstPartyHarnessAdmission.v1"
    assert harness["status"] == "ready"
    assert set(harness["selectedPackIds"]) == set(FIRST_PARTY_RECIPE_PACK_IDS)
    assert {
        "coding",
        "research",
        "general_automation",
        "memory",
        "scheduler",
        "channel_delivery",
        "browser",
        "methodology",
    }.issubset(set(harness["harnessFamilies"]))
    assert "tool:file.read" in harness["toolIntents"]
    assert "tool:test.run" in harness["toolIntents"]
    assert "provider:web.search" in harness["providerIntents"]
    assert "scheduler:cron.create" in harness["schedulerIntents"]
    assert "channel:dispatcher.push" in harness["channelIntents"]
    assert harness["liveAttachmentRefs"] == []
    assert set(harness["attachmentFlags"].values()) == {False}
    assert harness["activeSelectedToolhost"]["mode"] == "selected_full_toolhost"
    assert "FileWrite" in harness["activeSelectedToolhost"]["allowedToolNames"]
    encoded = json.dumps(harness, sort_keys=True)
    assert "rawPrompt" not in encoded
    assert "must be ignored" not in encoded


def test_chat_route_gate1a_success_includes_observed_egress_evidence_when_available(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from magi_agent.gates.gate1a_readonly_tools import (
        GATE1A_READONLY_TOOL_NAMES,
        Gate1AReadOnlyToolConfig,
    )

    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    canary_request_digest = "sha256:" + "9" * 64
    runtime = make_runtime(
        authority=PythonRuntimeAuthorityConfig(
            userVisibleOutputAllowed=True,
            canaryRoutingAllowed=True,
        )
    )
    runtime.gate5b_user_visible_chat_route_config = Gate5BUserVisibleChatRouteConfig(
        enabled=True,
        killSwitchEnabled=False,
        selectedBotDigest=_sha256("bot-test"),
        selectedOwnerUserIdDigest=_sha256("user-test"),
        environment="production",
        environmentAllowlist=("production",),
        adkPrimitivesLoader=_fake_primitives,
    )
    runtime.gate1a_readonly_tools_config = Gate1AReadOnlyToolConfig.model_validate(
        {
            "enabled": True,
            "killSwitchEnabled": False,
            "routeAttachmentEnabled": True,
            "selectedBotDigest": _sha256("bot-test"),
            "selectedOwnerDigest": _sha256("user-test"),
            "environment": "production",
            "environmentAllowlist": ("production",),
            "allowedToolNames": GATE1A_READONLY_TOOL_NAMES,
            "maxToolCallsPerTurn": 8,
        }
    )
    runtime.gate5b4c3_shadow_generation_route_config = Gate5B4C3ShadowGenerationRouteConfig(
        liveRunnerBoundaryEnabled=True,
        counterStore=Gate5B4C3ShadowCounterStore(tmp_path / "counters.json"),
        generationConfig=Gate5B4C3ShadowGenerationConfig(
            enabled=True,
            killSwitchActive=False,
            capStateInitialized=True,
            providerProjectSpendControlsVerified=True,
            selectedBotDigest=_sha256("bot-test"),
            trustedOwnerUserIdDigest=_sha256("user-test"),
            environment="production",
            allowedProviderLabels=("google",),
            allowedModelLabels=("gemini-3.5-flash",),
            allowedModelRoutes=("google:gemini-3.5-flash",),
            allowedShadowCredentialRefs=("gate5b-google-api-key-smoke-v1",),
            providerCredentialBindingRequired=False,
            approvedBudgets={
                "maxDailyGenerationRuns": 1,
                "maxDailyGenerationCostUsd": 0.05,
                "maxCostUsd": 0.05,
            },
        ),
    )
    runtime.gate1a_observed_egress_evidence_provider = LocalObservedEgressEvidenceProvider(
        ObservedEgressEvidence.model_validate(
            {
                "requestDigest": canary_request_digest,
                "providerRequestCount": 1,
                "egressTunnelCount": 2,
                "egressHostClasses": ["gemini_proxy"],
                "observedWindowStart": "2026-05-24T10:00:00.000Z",
                "observedWindowEnd": "2026-05-24T10:00:02.000Z",
                "evidenceSource": "local_fixture",
                "redactionStatus": "public_safe",
                "decisionReason": "observed_gemini_proxy_tunnel",
            }
        )
    )

    response = TestClient(create_app(runtime)).post(
        "/v1/chat/completions",
        headers={
            "authorization": "Bearer gateway-token",
            "x-gate5b-canary-request-digest": canary_request_digest,
        },
        json={"messages": [{"role": "user", "content": "Please answer briefly."}]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["egressEvidenceStatus"] == "observed_egress_evidence_present"
    assert body["providerRequestCount"] == 1
    assert body["egressTunnelCount"] == 2
    assert body["egressHostClasses"] == ["gemini_proxy"]
    assert body["egressDisciplineMode"] == "bounded_provider_tunnels"
    assert body["expectedEgressTunnelRange"] == {"min": 0, "max": 2}
    assert body["observedEgressEvidence"]["requestDigest"] == canary_request_digest
    assert body["observedEgressEvidence"]["evidenceSource"] == "local_fixture"
    assert body["observedEgressEvidence"]["redactionStatus"] == "public_safe"
    serialized = json.dumps(body["observedEgressEvidence"])
    for forbidden in (
        "Please answer briefly",
        "gateway-token",
        "generativelanguage.googleapis.com",
        "/Users/",
        "Bearer",
        "api_key",
    ):
        assert forbidden not in serialized


def test_chat_route_gate1a_success_uses_live_egress_proxy_telemetry(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from magi_agent.gates.gate1a_readonly_tools import (
        GATE1A_READONLY_TOOL_NAMES,
        Gate1AReadOnlyToolConfig,
    )

    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    canary_request_digest = "sha256:" + "9" * 64
    telemetry_path = tmp_path / "egress.jsonl"
    telemetry_path.write_text(
        json.dumps(
            {
                "schemaVersion": "gate1a.egressProxyTelemetry.v1",
                "observedAt": "2026-05-24T10:00:01.000Z",
                "requestDigest": canary_request_digest,
                "correlationDigest": canary_request_digest,
                "modelAttemptDigest": _sha256(
                    f"{canary_request_digest}:google:gemini-3.5-flash:attempt:1"
                ),
                "egressHostClass": "gemini_proxy",
                "evidenceSource": "gate5b_egress_proxy",
                "redactionStatus": "public_safe",
                "decisionReason": "connect_tunnel_established",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    timestamps = iter(
        (
            "2026-05-24T10:00:00.000Z",
            "2026-05-24T10:00:05.000Z",
        )
    )
    monkeypatch.setattr(chat_routes_module, "_utc_now_iso", lambda: next(timestamps))
    runtime = make_runtime(
        authority=PythonRuntimeAuthorityConfig(
            userVisibleOutputAllowed=True,
            canaryRoutingAllowed=True,
        )
    )
    runtime.gate5b_user_visible_chat_route_config = Gate5BUserVisibleChatRouteConfig(
        enabled=True,
        killSwitchEnabled=False,
        selectedBotDigest=_sha256("bot-test"),
        selectedOwnerUserIdDigest=_sha256("user-test"),
        environment="production",
        environmentAllowlist=("production",),
        adkPrimitivesLoader=_fake_primitives,
    )
    runtime.gate1a_readonly_tools_config = Gate1AReadOnlyToolConfig.model_validate(
        {
            "enabled": True,
            "killSwitchEnabled": False,
            "routeAttachmentEnabled": True,
            "selectedBotDigest": _sha256("bot-test"),
            "selectedOwnerDigest": _sha256("user-test"),
            "environment": "production",
            "environmentAllowlist": ("production",),
            "allowedToolNames": GATE1A_READONLY_TOOL_NAMES,
            "maxToolCallsPerTurn": 8,
        }
    )
    runtime.gate5b4c3_shadow_generation_route_config = Gate5B4C3ShadowGenerationRouteConfig(
        liveRunnerBoundaryEnabled=True,
        counterStore=Gate5B4C3ShadowCounterStore(tmp_path / "counters.json"),
        generationConfig=Gate5B4C3ShadowGenerationConfig(
            enabled=True,
            killSwitchActive=False,
            capStateInitialized=True,
            providerProjectSpendControlsVerified=True,
            selectedBotDigest=_sha256("bot-test"),
            trustedOwnerUserIdDigest=_sha256("user-test"),
            environment="production",
            allowedProviderLabels=("google",),
            allowedModelLabels=("gemini-3.5-flash",),
            allowedModelRoutes=("google:gemini-3.5-flash",),
            allowedShadowCredentialRefs=("gate5b-google-api-key-smoke-v1",),
            providerCredentialBindingRequired=False,
            approvedBudgets={
                "maxDailyGenerationRuns": 1,
                "maxDailyGenerationCostUsd": 0.05,
                "maxCostUsd": 0.05,
            },
        ),
    )
    runtime.gate1a_observed_egress_evidence_provider = (
        LiveEgressTelemetryEvidenceProvider(
            telemetry_path,
            proxy_url="http://gate5b-gemini-egress-proxy.magi-system.svc.cluster.local:8080",
        )
    )

    response = TestClient(create_app(runtime)).post(
        "/v1/chat/completions",
        headers={
            "authorization": "Bearer gateway-token",
            "x-gate5b-canary-request-digest": canary_request_digest,
        },
        json={"messages": [{"role": "user", "content": "Please answer briefly."}]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["egressEvidenceStatus"] == "observed_egress_evidence_present"
    assert body["observedEgressEvidence"]["evidenceSource"] == "gate5b_egress_proxy"
    assert body["egressHostClasses"] == ["gemini_proxy"]
    assert body["egressTunnelCount"] == 1
    agent_model = _FakeAgent.created_kwargs["model"]
    assert getattr(agent_model, "model", None) == "gemini-3.5-flash"
    assert getattr(agent_model, "openmagi_gate1a_proxy_connect_headers_enabled", False) is True
    serialized = json.dumps(body["observedEgressEvidence"])
    assert "generativelanguage.googleapis.com" not in serialized
    assert "gateway-token" not in serialized
    assert "Please answer briefly" not in serialized


def test_chat_route_gate1a_success_marks_missing_egress_evidence_without_counts(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from magi_agent.gates.gate1a_readonly_tools import (
        GATE1A_READONLY_TOOL_NAMES,
        Gate1AReadOnlyToolConfig,
    )

    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    runtime = make_runtime(
        authority=PythonRuntimeAuthorityConfig(
            userVisibleOutputAllowed=True,
            canaryRoutingAllowed=True,
        )
    )
    runtime.gate5b_user_visible_chat_route_config = Gate5BUserVisibleChatRouteConfig(
        enabled=True,
        killSwitchEnabled=False,
        selectedBotDigest=_sha256("bot-test"),
        selectedOwnerUserIdDigest=_sha256("user-test"),
        environment="production",
        environmentAllowlist=("production",),
        adkPrimitivesLoader=_fake_primitives,
    )
    runtime.gate1a_readonly_tools_config = Gate1AReadOnlyToolConfig.model_validate(
        {
            "enabled": True,
            "killSwitchEnabled": False,
            "routeAttachmentEnabled": True,
            "selectedBotDigest": _sha256("bot-test"),
            "selectedOwnerDigest": _sha256("user-test"),
            "environment": "production",
            "environmentAllowlist": ("production",),
            "allowedToolNames": GATE1A_READONLY_TOOL_NAMES,
            "maxToolCallsPerTurn": 8,
        }
    )
    runtime.gate5b4c3_shadow_generation_route_config = Gate5B4C3ShadowGenerationRouteConfig(
        liveRunnerBoundaryEnabled=True,
        counterStore=Gate5B4C3ShadowCounterStore(tmp_path / "counters.json"),
        generationConfig=Gate5B4C3ShadowGenerationConfig(
            enabled=True,
            killSwitchActive=False,
            capStateInitialized=True,
            providerProjectSpendControlsVerified=True,
            selectedBotDigest=_sha256("bot-test"),
            trustedOwnerUserIdDigest=_sha256("user-test"),
            environment="production",
            allowedProviderLabels=("google",),
            allowedModelLabels=("gemini-3.5-flash",),
            allowedModelRoutes=("google:gemini-3.5-flash",),
            allowedShadowCredentialRefs=("gate5b-google-api-key-smoke-v1",),
            providerCredentialBindingRequired=False,
            approvedBudgets={
                "maxDailyGenerationRuns": 1,
                "maxDailyGenerationCostUsd": 0.05,
                "maxCostUsd": 0.05,
            },
        ),
    )

    response = TestClient(create_app(runtime)).post(
        "/v1/chat/completions",
        headers={"authorization": "Bearer gateway-token"},
        json={"messages": [{"role": "user", "content": "Please answer briefly."}]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["modelAttemptCount"] == 1
    assert body["egressEvidenceStatus"] == "missing_observed_egress_evidence"
    assert "providerRequestCount" not in body
    assert "egressTunnelCount" not in body
    assert "egressHostClasses" not in body
    assert "observedEgressEvidence" not in body
    assert _FakeAgent.created_kwargs["model"] == "gemini-3.5-flash"


def test_chat_route_gate1a_non_selected_scope_keeps_tools_disabled(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from magi_agent.gates.gate1a_readonly_tools import (
        GATE1A_READONLY_TOOL_NAMES,
        Gate1AReadOnlyToolConfig,
    )

    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    runtime = make_runtime(
        authority=PythonRuntimeAuthorityConfig(
            userVisibleOutputAllowed=True,
            canaryRoutingAllowed=True,
        )
    )
    runtime.gate5b_user_visible_chat_route_config = Gate5BUserVisibleChatRouteConfig(
        enabled=True,
        killSwitchEnabled=False,
        selectedBotDigest=_sha256("bot-test"),
        selectedOwnerUserIdDigest=_sha256("user-test"),
        environment="production",
        environmentAllowlist=("production",),
        adkPrimitivesLoader=_fake_primitives,
    )
    runtime.gate1a_readonly_tools_config = Gate1AReadOnlyToolConfig.model_validate(
        {
            "enabled": True,
            "killSwitchEnabled": False,
            "routeAttachmentEnabled": True,
            "selectedBotDigest": _sha256("other-bot"),
            "selectedOwnerDigest": _sha256("user-test"),
            "environment": "production",
            "environmentAllowlist": ("production",),
            "allowedToolNames": GATE1A_READONLY_TOOL_NAMES,
            "maxToolCallsPerTurn": 8,
        }
    )
    runtime.gate5b4c3_shadow_generation_route_config = Gate5B4C3ShadowGenerationRouteConfig(
        liveRunnerBoundaryEnabled=True,
        counterStore=Gate5B4C3ShadowCounterStore(tmp_path / "counters.json"),
        generationConfig=Gate5B4C3ShadowGenerationConfig(
            enabled=True,
            killSwitchActive=False,
            capStateInitialized=True,
            providerProjectSpendControlsVerified=True,
            selectedBotDigest=_sha256("bot-test"),
            trustedOwnerUserIdDigest=_sha256("user-test"),
            environment="production",
            allowedProviderLabels=("google",),
            allowedModelLabels=("gemini-3.5-flash",),
            allowedModelRoutes=("google:gemini-3.5-flash",),
            allowedShadowCredentialRefs=("gate5b-google-api-key-smoke-v1",),
            providerCredentialBindingRequired=False,
            approvedBudgets={
                "maxDailyGenerationRuns": 1,
                "maxDailyGenerationCostUsd": 0.05,
                "maxCostUsd": 0.05,
            },
        ),
    )

    response = TestClient(create_app(runtime)).post(
        "/v1/chat/completions",
        headers={"authorization": "Bearer gateway-token"},
        json={"messages": [{"role": "user", "content": "Please answer briefly."}]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["safety"]["toolsActive"] is False
    assert body["activeTools"] == []
    assert body["authority"].get("readOnlyToolDispatchAllowed", False) is False
    assert _FakeAgent.created_kwargs["tools"] == []
    assert _FakeAgent.created_kwargs["model"] == "gemini-3.5-flash"


def test_chat_route_gate1a_mocked_runner_does_not_claim_tool_attachment(
    monkeypatch,
) -> None:
    from magi_agent.gates.gate1a_readonly_tools import (
        GATE1A_READONLY_TOOL_NAMES,
        Gate1AReadOnlyToolConfig,
    )

    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    runtime = make_runtime(
        authority=PythonRuntimeAuthorityConfig(
            userVisibleOutputAllowed=True,
            canaryRoutingAllowed=True,
        )
    )
    runtime.gate5b_user_visible_chat_route_config = Gate5BUserVisibleChatRouteConfig(
        enabled=True,
        killSwitchEnabled=False,
        selectedBotDigest=_sha256("bot-test"),
        selectedOwnerUserIdDigest=_sha256("user-test"),
        environment="production",
        environmentAllowlist=("production",),
        mockedRunner=lambda _request: {"content": "mocked", "eventCount": 1},
    )
    runtime.gate1a_readonly_tools_config = Gate1AReadOnlyToolConfig.model_validate(
        {
            "enabled": True,
            "killSwitchEnabled": False,
            "routeAttachmentEnabled": True,
            "selectedBotDigest": _sha256("bot-test"),
            "selectedOwnerDigest": _sha256("user-test"),
            "environment": "production",
            "environmentAllowlist": ("production",),
            "allowedToolNames": GATE1A_READONLY_TOOL_NAMES,
            "maxToolCallsPerTurn": 8,
        }
    )

    response = TestClient(create_app(runtime)).post(
        "/v1/chat/completions",
        headers={"authorization": "Bearer gateway-token"},
        json={"messages": [{"role": "user", "content": "Please answer briefly."}]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["safety"]["toolsActive"] is False
    assert body["authority"].get("readOnlyToolDispatchAllowed", False) is False
    assert "tooling" not in body


def test_chat_route_blocks_same_digest_while_canary_in_flight(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    canary_request_digest = "sha256:" + "8" * 64
    counter_store = Gate5B4C3ShadowCounterStore(tmp_path / "counters.json")
    now_ms = int(time.time() * 1000)
    counter_store.reserve(
        request_digest=canary_request_digest,
        shadow_generation_id="uv_canary_existing",
        selected_bot_digest=_sha256("bot-test"),
        trusted_owner_user_id_digest=_sha256("user-test"),
        environment="production",
        max_daily_generation_runs=1,
        max_daily_generation_cost_usd=0.05,
        max_concurrent_generation_runs=1,
        max_pending_generation_runs=1,
        cost_cap_usd=0.05,
        now_ms=now_ms,
    )
    _FakeRunner.run_kwargs = {}
    runtime = make_runtime(
        authority=PythonRuntimeAuthorityConfig(
            userVisibleOutputAllowed=True,
            canaryRoutingAllowed=True,
        )
    )
    runtime.gate5b_user_visible_chat_route_config = Gate5BUserVisibleChatRouteConfig(
        enabled=True,
        killSwitchEnabled=False,
        selectedBotDigest=_sha256("bot-test"),
        selectedOwnerUserIdDigest=_sha256("user-test"),
        environment="production",
        environmentAllowlist=("production",),
        adkPrimitivesLoader=_fake_primitives,
    )
    runtime.gate5b4c3_shadow_generation_route_config = Gate5B4C3ShadowGenerationRouteConfig(
        liveRunnerBoundaryEnabled=True,
        counterStore=counter_store,
        generationConfig=Gate5B4C3ShadowGenerationConfig(
            enabled=True,
            killSwitchActive=False,
            capStateInitialized=True,
            providerProjectSpendControlsVerified=True,
            selectedBotDigest=_sha256("bot-test"),
            trustedOwnerUserIdDigest=_sha256("user-test"),
            environment="production",
            allowedProviderLabels=("google",),
            allowedModelLabels=("gemini-3.5-flash",),
            allowedModelRoutes=("google:gemini-3.5-flash",),
            allowedShadowCredentialRefs=("gate5b-google-api-key-smoke-v1",),
            providerCredentialBindingRequired=False,
            approvedBudgets={
                "maxDailyGenerationRuns": 1,
                "maxDailyGenerationCostUsd": 0.05,
                "maxCostUsd": 0.05,
            },
        ),
    )

    response = TestClient(create_app(runtime)).post(
        "/v1/chat/completions",
        headers={
            "authorization": "Bearer gateway-token",
            "x-gate5b-canary-request-digest": canary_request_digest,
        },
        json={"messages": [{"role": "user", "content": "Synthetic canary chat."}]},
    )

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "python_disabled"
    assert body["fallbackStatus"] == "fallback_to_typescript"
    assert body["responseAuthority"] == "typescript"
    assert body["reason"] == "counter_duplicate_in_flight"
    assert body["counter"]["status"] == "blocked"
    assert body["counter"]["state"]["dailyGenerationRunsUsed"] == 1
    assert body["counter"]["state"]["inFlightGenerationRuns"] == 1
    assert body["counter"]["state"]["pendingGenerationRuns"] == 1
    assert _FakeRunner.run_kwargs == {}


def test_chat_route_failed_canary_records_explicit_counter_error(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    counter_path = tmp_path / "counters.json"

    async def fail_boundary(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("synthetic runner failure")

    monkeypatch.setattr(
        chat_routes_module,
        "run_gate5b4c3_live_runner_boundary_async",
        fail_boundary,
    )
    runtime = make_runtime(
        authority=PythonRuntimeAuthorityConfig(
            userVisibleOutputAllowed=True,
            canaryRoutingAllowed=True,
        )
    )
    runtime.gate5b_user_visible_chat_route_config = Gate5BUserVisibleChatRouteConfig(
        enabled=True,
        killSwitchEnabled=False,
        selectedBotDigest=_sha256("bot-test"),
        selectedOwnerUserIdDigest=_sha256("user-test"),
        environment="production",
        environmentAllowlist=("production",),
        adkPrimitivesLoader=_fake_primitives,
    )
    runtime.gate5b4c3_shadow_generation_route_config = Gate5B4C3ShadowGenerationRouteConfig(
        liveRunnerBoundaryEnabled=True,
        counterStore=Gate5B4C3ShadowCounterStore(counter_path),
        generationConfig=Gate5B4C3ShadowGenerationConfig(
            enabled=True,
            killSwitchActive=False,
            capStateInitialized=True,
            providerProjectSpendControlsVerified=True,
            selectedBotDigest=_sha256("bot-test"),
            trustedOwnerUserIdDigest=_sha256("user-test"),
            environment="production",
            allowedProviderLabels=("google",),
            allowedModelLabels=("gemini-3.5-flash",),
            allowedModelRoutes=("google:gemini-3.5-flash",),
            allowedShadowCredentialRefs=("gate5b-google-api-key-smoke-v1",),
            providerCredentialBindingRequired=False,
            approvedBudgets={
                "maxDailyGenerationRuns": 1,
                "maxDailyGenerationCostUsd": 0.05,
                "maxCostUsd": 0.05,
            },
        ),
    )
    canary_request_digest = "sha256:" + "7" * 64

    response = TestClient(create_app(runtime)).post(
        "/v1/chat/completions",
        headers={
            "authorization": "Bearer gateway-token",
            "x-gate5b-canary-request-digest": canary_request_digest,
        },
        json={"messages": [{"role": "user", "content": "Synthetic canary chat."}]},
    )

    assert response.status_code == 502
    body = response.json()
    assert body["status"] == "python_error"
    assert body["reason"] == "runner_error"
    assert body["fallbackStatus"] == "fallback_to_typescript"
    assert body["responseAuthority"] == "typescript"
    assert body["counter"]["status"] == "error"
    assert body["counter"]["state"]["inFlightGenerationRuns"] == 0
    assert body["counter"]["state"]["pendingGenerationRuns"] == 0
    raw_counter_store = json.loads(counter_path.read_text(encoding="utf-8"))
    request_record = next(iter(raw_counter_store["scopes"].values()))["requests"][
        canary_request_digest
    ]
    assert request_record["status"] == "error"
    assert request_record["reason"] == "runner_error"
    assert "choices" not in body


def test_chat_route_gate1a_provider_setup_error_records_digest_only_diagnostic(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from magi_agent.gates.gate1a_readonly_tools import (
        GATE1A_READONLY_TOOL_NAMES,
        Gate1AReadOnlyToolConfig,
    )

    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    counter_path = tmp_path / "counters.json"
    runtime = make_runtime(
        authority=PythonRuntimeAuthorityConfig(
            userVisibleOutputAllowed=True,
            canaryRoutingAllowed=True,
        )
    )
    runtime.gate5b_user_visible_chat_route_config = Gate5BUserVisibleChatRouteConfig(
        enabled=True,
        killSwitchEnabled=False,
        selectedBotDigest=_sha256("bot-test"),
        selectedOwnerUserIdDigest=_sha256("user-test"),
        environment="production",
        environmentAllowlist=("production",),
        adkPrimitivesLoader=_provider_setup_fail_primitives,
    )
    runtime.gate1a_readonly_tools_config = Gate1AReadOnlyToolConfig.model_validate(
        {
            "enabled": True,
            "killSwitchEnabled": False,
            "routeAttachmentEnabled": True,
            "selectedBotDigest": _sha256("bot-test"),
            "selectedOwnerDigest": _sha256("user-test"),
            "environment": "production",
            "environmentAllowlist": ("production",),
            "allowedToolNames": GATE1A_READONLY_TOOL_NAMES,
            "maxToolCallsPerTurn": 8,
        }
    )
    counter_store = Gate5B4C3ShadowCounterStore(counter_path)
    runtime.gate5b4c3_shadow_generation_route_config = Gate5B4C3ShadowGenerationRouteConfig(
        liveRunnerBoundaryEnabled=True,
        counterStore=counter_store,
        generationConfig=Gate5B4C3ShadowGenerationConfig(
            enabled=True,
            killSwitchActive=False,
            capStateInitialized=True,
            providerProjectSpendControlsVerified=True,
            selectedBotDigest=_sha256("bot-test"),
            trustedOwnerUserIdDigest=_sha256("user-test"),
            environment="production",
            allowedProviderLabels=("google",),
            allowedModelLabels=("gemini-3.5-flash",),
            allowedModelRoutes=("google:gemini-3.5-flash",),
            allowedShadowCredentialRefs=("gate5b-google-api-key-smoke-v1",),
            providerCredentialBindingRequired=False,
            approvedBudgets={
                "maxDailyGenerationRuns": 1,
                "maxDailyGenerationCostUsd": 0.05,
                "maxCostUsd": 0.05,
            },
        ),
    )
    canary_request_digest = "sha256:" + "8" * 64
    client = TestClient(create_app(runtime))

    response = client.post(
        "/v1/chat/completions",
        headers={
            "authorization": "Bearer gateway-token",
            "x-gate5b-canary-request-digest": canary_request_digest,
        },
        json={
            "messages": [
                {
                    "role": "user",
                    "content": "Synthetic canary chat for runner diagnostics.",
                }
            ]
        },
    )

    assert response.status_code == 502
    body = response.json()
    assert body["status"] == "python_error"
    assert body["reason"] == "runner_error"
    assert body["fallbackStatus"] == "fallback_to_typescript"
    assert body["responseAuthority"] == "typescript"
    assert body["adk"]["invoked"] is True
    assert body["runnerErrorDiagnostic"]["stage"] == "provider_client_setup"
    assert body["runnerErrorDiagnostic"]["reasonCode"] == "provider_client_setup_failed"
    assert body["runnerErrorDiagnostic"]["exceptionCategory"] == (
        "provider_client_setup_failure"
    )
    assert body["runnerErrorDiagnostic"]["modelCallAttempted"] is False
    assert body["runnerErrorDiagnostic"]["gateMode"] == "gate1a_readonly_tools"
    assert "choices" not in body
    serialized_body = json.dumps(body)
    for forbidden in (
        "raw-token",
        "prompt=secret-output",
        "Authorization:",
        "/Users/kevin",
        "/private/path",
        "Synthetic canary chat for runner diagnostics",
    ):
        assert forbidden not in serialized_body

    receipt = client.post(
        "/v1/internal/gate5b/user-visible-delivery-receipts",
        headers={"authorization": "Bearer gateway-token"},
        json={
            "schemaVersion": "gate5b.userVisibleDeliveryReceipt.v1",
            "requestDigest": canary_request_digest,
            "bodyDigest": "sha256:" + "b" * 64,
            "routeDecision": "typescript_fallback",
            "gate": "gate1a_readonly_tools",
            "deliveryStatus": "fallback_served",
            "reason": "runner_error",
            "fallbackReason": "python_error",
            "responseAuthority": "typescript",
            "servedAt": "2026-05-25T18:45:00.000Z",
            "sseFrameCount": 1,
            "toolReceiptCount": 0,
            "modelAttemptCount": 0,
            "providerRequestCount": 0,
            "expectedModelAttemptCount": 0,
        },
    )

    assert receipt.status_code == 202
    raw_counter_store = json.loads(counter_path.read_text(encoding="utf-8"))
    request_record = next(iter(raw_counter_store["scopes"].values()))["requests"][
        canary_request_digest
    ]
    assert request_record["status"] == "error"
    assert request_record["reason"] == "runner_error"
    assert request_record["deliveryStatus"] == "fallback_served"
    assert request_record["responseAuthority"] == "typescript"
    assert request_record["fallbackReason"] == "python_error"
    assert request_record["modelAttemptCount"] == 0
    assert request_record["providerRequestCount"] == 0
    assert "egressEvidenceStatus" not in request_record
    assert request_record["runnerErrorDiagnostic"]["stage"] == "provider_client_setup"
    assert request_record["runnerErrorDiagnostic"]["modelCallAttempted"] is False
    evidence = counter_store.validate_delivery_evidence(
        request_digest=canary_request_digest,
        selected_bot_digest=_sha256("bot-test"),
        trusted_owner_user_id_digest=_sha256("user-test"),
        environment="production",
        gate="gate1a_readonly_tools",
    )
    assert evidence.status == "passed"
    assert evidence.runner_error_diagnostic is not None
    assert evidence.runner_error_diagnostic["stage"] == "provider_client_setup"
    serialized_record = json.dumps(request_record)
    for forbidden in (
        "raw-token",
        "prompt=secret-output",
        "Authorization:",
        "/Users/kevin",
        "/private/path",
        "Synthetic canary chat for runner diagnostics",
    ):
        assert forbidden not in serialized_record


def test_chat_route_gate1a_function_tool_typeerror_keeps_provider_counts_zero(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from magi_agent.gates.gate1a_readonly_tools import (
        GATE1A_READONLY_TOOL_NAMES,
        Gate1AReadOnlyToolConfig,
    )

    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    counter_path = tmp_path / "gate5b4c3-counters.json"
    runtime = make_runtime(
        authority=PythonRuntimeAuthorityConfig(
            userVisibleOutputAllowed=True,
            canaryRoutingAllowed=True,
        )
    )
    runtime.gate5b_user_visible_chat_route_config = Gate5BUserVisibleChatRouteConfig(
        enabled=True,
        killSwitchEnabled=False,
        selectedBotDigest=_sha256("bot-test"),
        selectedOwnerUserIdDigest=_sha256("user-test"),
        environment="production",
        environmentAllowlist=("production",),
        adkPrimitivesLoader=_function_tool_schema_typeerror_primitives,
    )
    runtime.gate1a_readonly_tools_config = Gate1AReadOnlyToolConfig.model_validate(
        {
            "enabled": True,
            "killSwitchEnabled": False,
            "routeAttachmentEnabled": True,
            "selectedBotDigest": _sha256("bot-test"),
            "selectedOwnerDigest": _sha256("user-test"),
            "environment": "production",
            "environmentAllowlist": ("production",),
            "allowedToolNames": GATE1A_READONLY_TOOL_NAMES,
            "maxToolCallsPerTurn": 8,
        }
    )
    counter_store = Gate5B4C3ShadowCounterStore(counter_path)
    runtime.gate5b4c3_shadow_generation_route_config = Gate5B4C3ShadowGenerationRouteConfig(
        liveRunnerBoundaryEnabled=True,
        counterStore=counter_store,
        generationConfig=Gate5B4C3ShadowGenerationConfig(
            enabled=True,
            killSwitchActive=False,
            capStateInitialized=True,
            providerProjectSpendControlsVerified=True,
            selectedBotDigest=_sha256("bot-test"),
            trustedOwnerUserIdDigest=_sha256("user-test"),
            environment="production",
            allowedProviderLabels=("google",),
            allowedModelLabels=("gemini-3.5-flash",),
            allowedModelRoutes=("google:gemini-3.5-flash",),
            allowedShadowCredentialRefs=("gate5b-google-api-key-smoke-v1",),
            providerCredentialBindingRequired=False,
            approvedBudgets={
                "maxDailyGenerationRuns": 1,
                "maxDailyGenerationCostUsd": 0.05,
                "maxCostUsd": 0.05,
            },
        ),
    )
    canary_request_digest = "sha256:" + "9" * 64
    client = TestClient(create_app(runtime))

    response = client.post(
        "/v1/chat/completions",
        headers={
            "authorization": "Bearer gateway-token",
            "x-gate5b-canary-request-digest": canary_request_digest,
        },
        json={
            "messages": [
                {
                    "role": "user",
                    "content": "Synthetic canary chat for FunctionTool TypeError.",
                }
            ]
        },
    )

    assert response.status_code == 502
    body = response.json()
    assert body["status"] == "python_error"
    assert body["reason"] == "runner_error"
    assert body["runnerErrorDiagnostic"]["stage"] == "adk_tool_schema"
    assert body["runnerErrorDiagnostic"]["reasonCode"] == (
        "adk_function_tool_schema_mismatch"
    )
    assert body["runnerErrorDiagnostic"]["exceptionClass"] == "TypeError"
    assert body["runnerErrorDiagnostic"]["modelCallAttempted"] is False
    assert body["runnerErrorDiagnostic"]["activeToolNames"] == list(
        GATE1A_READONLY_TOOL_NAMES
    )
    assert "[REDACTED]" in body["runnerErrorDiagnostic"]["errorPreview"]
    assert body["runnerErrorDiagnostic"]["tracebackMarkers"]
    serialized_body = json.dumps(body)
    for forbidden in (
        "raw-token",
        "prompt=secret-output",
        "Authorization:",
        "/Users/kevin",
        "/private/path",
        "Synthetic canary chat for FunctionTool TypeError",
    ):
        assert forbidden not in serialized_body

    receipt = client.post(
        "/v1/internal/gate5b/user-visible-delivery-receipts",
        headers={"authorization": "Bearer gateway-token"},
        json={
            "schemaVersion": "gate5b.userVisibleDeliveryReceipt.v1",
            "requestDigest": canary_request_digest,
            "bodyDigest": "sha256:" + "b" * 64,
            "routeDecision": "typescript_fallback",
            "gate": "gate1a_readonly_tools",
            "deliveryStatus": "fallback_served",
            "reason": "runner_error",
            "fallbackReason": "python_error",
            "responseAuthority": "typescript",
            "servedAt": "2026-05-25T18:45:00.000Z",
            "sseFrameCount": 1,
            "toolReceiptCount": 0,
            "modelAttemptCount": 0,
            "providerRequestCount": 0,
            "expectedModelAttemptCount": 0,
        },
    )

    assert receipt.status_code == 202
    raw_counter_store = json.loads(counter_path.read_text(encoding="utf-8"))
    request_record = next(iter(raw_counter_store["scopes"].values()))["requests"][
        canary_request_digest
    ]
    assert request_record["status"] == "error"
    assert request_record["reason"] == "runner_error"
    assert request_record["deliveryStatus"] == "fallback_served"
    assert request_record["modelAttemptCount"] == 0
    assert request_record["providerRequestCount"] == 0
    assert "egressEvidenceStatus" not in request_record
    assert request_record["runnerErrorDiagnostic"]["stage"] == "adk_tool_schema"
    assert request_record["runnerErrorDiagnostic"]["modelCallAttempted"] is False
    assert "[REDACTED]" in request_record["runnerErrorDiagnostic"]["errorPreview"]
    assert request_record["runnerErrorDiagnostic"]["tracebackMarkers"]
    evidence = counter_store.validate_delivery_evidence(
        request_digest=canary_request_digest,
        selected_bot_digest=_sha256("bot-test"),
        trusted_owner_user_id_digest=_sha256("user-test"),
        environment="production",
        gate="gate1a_readonly_tools",
    )
    assert evidence.status == "passed"
    assert evidence.runner_error_diagnostic is not None
    assert evidence.runner_error_diagnostic["stage"] == "adk_tool_schema"
    serialized_record = json.dumps(request_record)
    for forbidden in (
        "raw-token",
        "prompt=secret-output",
        "Authorization:",
        "/Users/kevin",
        "/private/path",
        "Synthetic canary chat for FunctionTool TypeError",
    ):
        assert forbidden not in serialized_record


def test_chat_route_marks_late_runner_completion_after_client_timeout(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    counter_path = tmp_path / "counters.json"
    runtime = make_runtime(
        authority=PythonRuntimeAuthorityConfig(
            userVisibleOutputAllowed=True,
            canaryRoutingAllowed=True,
        )
    )
    runtime.gate5b_user_visible_chat_route_config = Gate5BUserVisibleChatRouteConfig(
        enabled=True,
        killSwitchEnabled=False,
        selectedBotDigest=_sha256("bot-test"),
        selectedOwnerUserIdDigest=_sha256("user-test"),
        environment="production",
        environmentAllowlist=("production",),
        adkPrimitivesLoader=_fake_primitives,
        clientDisconnectedProbe=lambda _request: True,
    )
    runtime.gate5b4c3_shadow_generation_route_config = Gate5B4C3ShadowGenerationRouteConfig(
        liveRunnerBoundaryEnabled=True,
        counterStore=Gate5B4C3ShadowCounterStore(counter_path),
        generationConfig=Gate5B4C3ShadowGenerationConfig(
            enabled=True,
            killSwitchActive=False,
            capStateInitialized=True,
            providerProjectSpendControlsVerified=True,
            selectedBotDigest=_sha256("bot-test"),
            trustedOwnerUserIdDigest=_sha256("user-test"),
            environment="production",
            allowedProviderLabels=("google",),
            allowedModelLabels=("gemini-3.5-flash",),
            allowedModelRoutes=("google:gemini-3.5-flash",),
            allowedShadowCredentialRefs=("gate5b-google-api-key-smoke-v1",),
            providerCredentialBindingRequired=False,
            approvedBudgets={
                "maxDailyGenerationRuns": 1,
                "maxDailyGenerationCostUsd": 0.05,
                "maxCostUsd": 0.05,
            },
        ),
    )

    response = TestClient(create_app(runtime)).post(
        "/v1/chat/completions",
        headers={"authorization": "Bearer gateway-token"},
        json={"messages": [{"role": "user", "content": "Synthetic canary chat."}]},
    )

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "python_error"
    assert body["reason"] == "client_aborted_after_runner"
    assert body["fallbackStatus"] == "fallback_to_typescript"
    assert body["responseAuthority"] == "typescript"
    assert body["adk"]["invoked"] is True
    assert body["counter"]["status"] == "completed_after_client_timeout"
    assert body["counter"]["state"]["inFlightGenerationRuns"] == 0
    assert body["counter"]["state"]["pendingGenerationRuns"] == 0
    assert "choices" not in body


def test_chat_delivery_receipt_route_records_served_to_client_without_output_payload(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    counter_path = tmp_path / "counters.json"
    runtime = make_runtime(
        authority=PythonRuntimeAuthorityConfig(
            userVisibleOutputAllowed=True,
            canaryRoutingAllowed=True,
        )
    )
    runtime.gate5b_user_visible_chat_route_config = Gate5BUserVisibleChatRouteConfig(
        enabled=True,
        killSwitchEnabled=False,
        selectedBotDigest=_sha256("bot-test"),
        selectedOwnerUserIdDigest=_sha256("user-test"),
        environment="production",
        environmentAllowlist=("production",),
        adkPrimitivesLoader=_fake_primitives,
    )
    runtime.gate5b4c3_shadow_generation_route_config = Gate5B4C3ShadowGenerationRouteConfig(
        liveRunnerBoundaryEnabled=True,
        counterStore=Gate5B4C3ShadowCounterStore(counter_path),
        generationConfig=Gate5B4C3ShadowGenerationConfig(
            enabled=True,
            killSwitchActive=False,
            capStateInitialized=True,
            providerProjectSpendControlsVerified=True,
            selectedBotDigest=_sha256("bot-test"),
            trustedOwnerUserIdDigest=_sha256("user-test"),
            environment="production",
            allowedProviderLabels=("google",),
            allowedModelLabels=("gemini-3.5-flash",),
            allowedModelRoutes=("google:gemini-3.5-flash",),
            allowedShadowCredentialRefs=("gate5b-google-api-key-smoke-v1",),
            providerCredentialBindingRequired=False,
            approvedBudgets={
                "maxDailyGenerationRuns": 2,
                "maxDailyGenerationCostUsd": 0.10,
                "maxCostUsd": 0.05,
            },
        ),
    )
    client = TestClient(create_app(runtime))
    canary_digest = "sha256:" + "c" * 64
    response = client.post(
        "/v1/chat/completions",
        headers={
            "authorization": "Bearer gateway-token",
            "x-gate5b-canary-request-digest": canary_digest,
        },
        json={"messages": [{"role": "user", "content": "Synthetic canary chat."}]},
    )
    assert response.status_code == 200

    receipt = client.post(
        "/v1/internal/gate5b/user-visible-delivery-receipts",
        headers={"authorization": "Bearer gateway-token"},
        json={
            "schemaVersion": "gate5b.userVisibleDeliveryReceipt.v1",
            "requestDigest": canary_digest,
            "deliveryStatus": "served_to_client",
            "reason": "served_to_client",
            "responseAuthority": "python",
            "rawUserText": "must-not-be-accepted",
        },
    )

    assert receipt.status_code == 422

    receipt = client.post(
        "/v1/internal/gate5b/user-visible-delivery-receipts",
        headers={"authorization": "Bearer gateway-token"},
        json={
            "schemaVersion": "gate5b.userVisibleDeliveryReceipt.v1",
            "requestDigest": canary_digest,
            "bodyDigest": "sha256:" + "b" * 64,
            "routeDecision": "python_selected",
            "gate": "gate1a_readonly_tools",
            "deliveryStatus": "served_to_client",
            "reason": "served_to_client",
            "responseAuthority": "python",
            "servedAt": "2026-05-23T21:45:00.000Z",
            "sseFrameCount": 4,
            "toolReceiptCount": 1,
            "modelAttemptCount": 1,
            "providerRequestCount": 1,
            "expectedModelAttemptCount": 1,
            "egressTunnelCount": 2,
            "egressDisciplineMode": "bounded_provider_tunnels",
            "egressEvidenceStatus": "observed_egress_evidence_present",
            "egressEvidenceSource": "local_fixture",
            "egressEvidenceRedactionStatus": "public_safe",
            "egressEvidenceDecisionReason": "observed_gemini_proxy_tunnel",
            "maxProviderTunnelsPerModelAttempt": 2,
            "egressHostClasses": ["gemini_proxy"],
            "egressWindowStartedAt": "2026-05-23T21:44:55.000Z",
            "egressWindowEndedAt": "2026-05-23T21:45:00.000Z",
            "egressCorrelationDigest": "sha256:" + "9" * 64,
            "outputDigest": "sha256:" + "e" * 64,
            "outputLeakStatus": {
                "leaked": False,
                "category": "none",
                "matchedPolicyTermClass": None,
                "length": 44,
            },
        },
    )

    assert receipt.status_code == 202
    body = receipt.json()
    assert body["status"] == "receipt_recorded"
    assert body["deliveryStatus"] == "served_to_client"
    assert body["responseAuthority"] == "typescript"
    assert "choices" not in body
    assert "Synthetic canary chat" not in json.dumps(body)
    raw = json.loads(counter_path.read_text(encoding="utf-8"))
    record = next(iter(raw["scopes"].values()))["requests"][canary_digest]
    assert record["status"] == "runner_completed"
    assert record["deliveryStatus"] == "served_to_client"
    assert record["bodyDigest"] == "sha256:" + "b" * 64
    assert record["routeDecision"] == "python_selected"
    assert record["gate"] == "gate1a_readonly_tools"
    assert record["sseFrameCount"] == 4
    assert record["toolReceiptCount"] == 1
    assert record["modelAttemptCount"] == 1
    assert record["providerRequestCount"] == 1
    assert record["egressTunnelCount"] == 2
    assert record["egressDisciplineMode"] == "bounded_provider_tunnels"
    assert record["egressEvidenceStatus"] == "observed_egress_evidence_present"
    assert record["egressEvidenceSource"] == "local_fixture"
    assert record["maxProviderTunnelsPerModelAttempt"] == 2
    assert record["egressHostClasses"] == ["gemini_proxy"]
    assert record["egressCorrelationDigest"] == "sha256:" + "9" * 64
    assert record["expectedEgressTunnelRange"] == {"min": 0, "max": 2}
    assert record["egressDisciplineReason"] == "bounded_provider_tunnels_ok"
    assert record["deliveryEvidenceStatus"] == "delivery_evidence_ok"
    assert "Synthetic canary chat" not in json.dumps(record)


def test_chat_delivery_receipt_route_records_gate1a_fallback_without_python_counter(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    counter_path = tmp_path / "counters.json"
    counter_store = Gate5B4C3ShadowCounterStore(counter_path)
    runtime = make_runtime(
        authority=PythonRuntimeAuthorityConfig(
            userVisibleOutputAllowed=True,
            canaryRoutingAllowed=True,
        )
    )
    runtime.gate5b_user_visible_chat_route_config = Gate5BUserVisibleChatRouteConfig(
        enabled=True,
        killSwitchEnabled=False,
        selectedBotDigest=_sha256("bot-test"),
        selectedOwnerUserIdDigest=_sha256("user-test"),
        environment="production",
        environmentAllowlist=("production",),
        adkPrimitivesLoader=_fake_primitives,
    )
    runtime.gate5b4c3_shadow_generation_route_config = Gate5B4C3ShadowGenerationRouteConfig(
        liveRunnerBoundaryEnabled=True,
        counterStore=counter_store,
        generationConfig=Gate5B4C3ShadowGenerationConfig(
            enabled=True,
            killSwitchActive=False,
            capStateInitialized=True,
            providerProjectSpendControlsVerified=True,
            selectedBotDigest=_sha256("bot-test"),
            trustedOwnerUserIdDigest=_sha256("user-test"),
            environment="production",
            allowedProviderLabels=("google",),
            allowedModelLabels=("gemini-3.5-flash",),
            allowedModelRoutes=("google:gemini-3.5-flash",),
            allowedShadowCredentialRefs=("gate5b-google-api-key-smoke-v1",),
            providerCredentialBindingRequired=False,
            approvedBudgets={
                "maxDailyGenerationRuns": 2,
                "maxDailyGenerationCostUsd": 0.10,
                "maxCostUsd": 0.05,
            },
        ),
    )
    canary_digest = "sha256:" + "f" * 64

    receipt = TestClient(create_app(runtime)).post(
        "/v1/internal/gate5b/user-visible-delivery-receipts",
        headers={"authorization": "Bearer gateway-token"},
        json={
            "schemaVersion": "gate5b.userVisibleDeliveryReceipt.v1",
            "requestDigest": canary_digest,
            "bodyDigest": "sha256:" + "b" * 64,
            "routeDecision": "typescript_fallback",
            "gate": "gate1a_readonly_tools",
            "deliveryStatus": "fallback_served",
            "reason": "python_error",
            "fallbackReason": "runner_error",
            "responseAuthority": "typescript",
            "servedAt": "2026-05-25T18:45:00.000Z",
            "pythonAttempted": True,
            "pythonCounterRecordPresent": False,
            "selectedScope": {
                "selectedBotDigest": _sha256("bot-test"),
                "selectedOwnerUserIdDigest": _sha256("user-test"),
                "environment": "production",
            },
            "sseFrameCount": 1,
            "toolReceiptCount": 0,
            "modelAttemptCount": 0,
            "providerRequestCount": 0,
            "expectedModelAttemptCount": 0,
        },
    )

    assert receipt.status_code == 202
    assert receipt.json()["receiptStatus"] == "recorded"
    raw = json.loads(counter_path.read_text(encoding="utf-8"))
    record = next(iter(raw["scopes"].values()))["requests"][canary_digest]
    assert record["status"] == "fallback_served"
    assert record["attemptEvidenceSource"] == "chat_proxy_fallback_receipt"
    assert record["pythonAttempted"] is True
    assert record["pythonCounterRecordPresent"] is False
    assert record["deliveryStatus"] == "fallback_served"
    assert record["routeDecision"] == "typescript_fallback"
    assert record["responseAuthority"] == "typescript"
    assert record["modelAttemptCount"] == 0
    assert record["providerRequestCount"] == 0
    evidence = counter_store.validate_delivery_evidence(
        request_digest=canary_digest,
        selected_bot_digest=_sha256("bot-test"),
        trusted_owner_user_id_digest=_sha256("user-test"),
        environment="production",
        gate="gate1a_readonly_tools",
    )
    assert evidence.status == "passed"
    assert evidence.attempt_evidence_source == "chat_proxy_fallback_receipt"
    serialized = json.dumps(record)
    for forbidden in ("raw prompt", "Authorization:", "Bearer", "cookie", "/Users/kevin"):
        assert forbidden not in serialized

    rejected = TestClient(create_app(runtime)).post(
        "/v1/internal/gate5b/user-visible-delivery-receipts",
        headers={"authorization": "Bearer gateway-token"},
        json={
            "schemaVersion": "gate5b.userVisibleDeliveryReceipt.v1",
            "requestDigest": "sha256:" + "e" * 64,
            "bodyDigest": "sha256:" + "b" * 64,
            "routeDecision": "typescript_fallback",
            "gate": "gate1a_readonly_tools",
            "deliveryStatus": "fallback_served",
            "reason": "python_error",
            "fallbackReason": "runner_error",
            "responseAuthority": "typescript",
            "servedAt": "2026-05-25T18:45:01.000Z",
            "pythonAttempted": True,
            "pythonCounterRecordPresent": False,
            "sseFrameCount": 1,
        },
    )
    assert rejected.status_code == 409
    assert rejected.json()["reason"] == "selected_scope_required"


def test_chat_gate1a_selected_attempt_preflight_reports_fresh_and_blocked_states(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    counter_path = tmp_path / "counters.json"
    counter_store = Gate5B4C3ShadowCounterStore(counter_path)
    runtime = make_runtime(
        authority=PythonRuntimeAuthorityConfig(
            userVisibleOutputAllowed=True,
            canaryRoutingAllowed=True,
        )
    )
    runtime.gate5b_user_visible_chat_route_config = Gate5BUserVisibleChatRouteConfig(
        enabled=True,
        killSwitchEnabled=False,
        selectedBotDigest=_sha256("bot-test"),
        selectedOwnerUserIdDigest=_sha256("user-test"),
        environment="production",
        environmentAllowlist=("production",),
        adkPrimitivesLoader=_fake_primitives,
    )
    runtime.gate5b4c3_shadow_generation_route_config = Gate5B4C3ShadowGenerationRouteConfig(
        liveRunnerBoundaryEnabled=True,
        counterStore=counter_store,
        generationConfig=Gate5B4C3ShadowGenerationConfig(
            enabled=True,
            killSwitchActive=False,
            capStateInitialized=True,
            providerProjectSpendControlsVerified=True,
            selectedBotDigest=_sha256("bot-test"),
            trustedOwnerUserIdDigest=_sha256("user-test"),
            environment="production",
            allowedProviderLabels=("google",),
            allowedModelLabels=("gemini-3.5-flash",),
            allowedModelRoutes=("google:gemini-3.5-flash",),
            allowedShadowCredentialRefs=("gate5b-google-api-key-smoke-v1",),
            providerCredentialBindingRequired=False,
            approvedBudgets={
                "maxDailyGenerationRuns": 1,
                "maxDailyGenerationCostUsd": 0.05,
                "maxCostUsd": 0.05,
            },
        ),
    )
    client = TestClient(create_app(runtime))
    canary_digest = "sha256:" + "1" * 64

    ready = client.post(
        "/v1/internal/gate1a/selected-attempt-preflight",
        headers={"authorization": "Bearer gateway-token"},
        json={
            "schemaVersion": "gate1a.selectedAttemptPreflightRequest.v1",
            "requestDigest": canary_digest,
            "fallbackReceiptPathAvailable": True,
            "selectedScope": {
                "selectedBotDigest": _sha256("bot-test"),
                "selectedOwnerUserIdDigest": _sha256("user-test"),
                "environment": "production",
            },
        },
    )

    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"
    assert ready.json()["reason"] == "fresh_attempt_ready"
    assert ready.json()["counterStoreWritable"] is True
    assert ready.json()["responseAuthority"] == "typescript"

    counter_store.record_delivery_receipt(
        request_digest=canary_digest,
        selected_bot_digest=_sha256("bot-test"),
        trusted_owner_user_id_digest=_sha256("user-test"),
        environment="production",
        delivery_status="fallback_served",
        reason="python_error",
        gate="gate1a_readonly_tools",
        response_authority="typescript",
        python_attempted=True,
        python_counter_record_present=False,
    )
    blocked = client.post(
        "/v1/internal/gate1a/selected-attempt-preflight",
        headers={"authorization": "Bearer gateway-token"},
        json={
            "schemaVersion": "gate1a.selectedAttemptPreflightRequest.v1",
            "requestDigest": canary_digest,
            "fallbackReceiptPathAvailable": True,
        },
    )
    unavailable = client.post(
        "/v1/internal/gate1a/selected-attempt-preflight",
        headers={"authorization": "Bearer gateway-token"},
        json={
            "schemaVersion": "gate1a.selectedAttemptPreflightRequest.v1",
            "requestDigest": "sha256:" + "2" * 64,
            "fallbackReceiptPathAvailable": False,
        },
    )

    assert blocked.status_code == 409
    assert blocked.json()["status"] == "blocked"
    assert blocked.json()["reason"] == "idempotency_collision"
    assert unavailable.status_code == 409
    assert unavailable.json()["reason"] == "fallback_receipt_path_unavailable"
    serialized = json.dumps([ready.json(), blocked.json(), unavailable.json()])
    for forbidden in ("bot-test", "user-test", "gateway-token", "Authorization:", "/Users/"):
        assert forbidden not in serialized


def test_chat_route_import_boundary_keeps_live_surfaces_unwired() -> None:
    route_path = (
        Path(__file__).parents[1]
        / "magi_agent"
        / "transport"
        / "chat.py"
    )
    source = route_path.read_text(encoding="utf-8")
    forbidden_imports = (
        "google.adk",
        "magi_agent.tools",
        "magi_agent.memory",
        "magi_agent.browser",
        "magi_agent.workspace",
        "magi_agent.channels",
        "magi_agent.database",
        "openai",
        "anthropic",
        "requests",
        "httpx",
    )
    for forbidden in forbidden_imports:
        assert f"import {forbidden}" not in source
        assert f"from {forbidden}" not in source
    for forbidden_symbol in (
        "ToolHost(",
        "ToolDispatcher(",
        "MemoryService(",
        "Browser(",
        "Workspace(",
        "Telegram(",
        "Channel(",
        "ArtifactService(",
        "Runner(",
        "Agent(",
    ):
        assert forbidden_symbol not in source
