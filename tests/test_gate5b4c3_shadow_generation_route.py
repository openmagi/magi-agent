from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from magi_agent.app import create_app
from magi_agent.config.env import (
    RuntimeEnvError,
    parse_gate5b4c3_shadow_generation_route_env,
)
from magi_agent.config.models import BuildInfo, RuntimeConfig
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime
from magi_agent.shadow.gate5b4c3_live_runner_boundary import (
    Gate5B4C3LiveAdkPrimitives,
)
from magi_agent.shadow.gate5b4c3_shadow_generation_contract import (
    Gate5B4C3ShadowGenerationConfig,
    Gate5B4C3ShadowGenerationProviderCredentialBinding,
    Gate5B4C3ShadowGenerationRequest,
    build_gate5b4c3_shadow_generation_diagnostic,
)
from magi_agent.shadow.gate5b4c3_shadow_counter_store import (
    Gate5B4C3ShadowCounterStore,
)
from magi_agent.transport.shadow_generations import (
    Gate5B4C3MockedAdkPrimitivesLoader,
    Gate5B4C3ShadowGenerationRouteConfig,
)


BOT_DIGEST = "sha256:" + "a" * 64
OWNER_DIGEST = "sha256:" + "b" * 64
TURN_DIGEST = "sha256:" + "c" * 64
REQUEST_DIGEST = "sha256:" + "d" * 64
TRACE_DIGEST = "sha256:" + "e" * 64
SESSION_DIGEST = "sha256:" + "f" * 64
SANITIZED_DIGEST = "sha256:" + "1" * 64
ROUTER_DIGEST = "sha256:" + "2" * 64
PROFILE_DIGEST = "sha256:" + "3" * 64
BOT_CONFIG_DIGEST = "sha256:" + "4" * 64
GOOGLE_PROVIDER = "google"
GOOGLE_MODEL = "gemini-3.5-flash"
GOOGLE_CREDENTIAL_REF = "gate5b-google-api-key-smoke-v1"
GOOGLE_CREDENTIAL_ENV = "GOOGLE" + "_API_KEY"
ANTHROPIC_CREDENTIAL_ENV = "ANTHROPIC" + "_API_KEY"
OPENAI_CREDENTIAL_ENV = "OPENAI" + "_API_KEY"
FIREWORKS_CREDENTIAL_ENV = "FIREWORKS" + "_API_KEY"


def _runtime() -> OpenMagiRuntime:
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
        )
    )


def _client() -> TestClient:
    return TestClient(create_app(_runtime()))


def _configured_client(route_config: Gate5B4C3ShadowGenerationRouteConfig) -> TestClient:
    runtime = _runtime()
    runtime.gate5b4c3_shadow_generation_route_config = route_config
    return TestClient(create_app(runtime))


def _enabled_gate_config() -> Gate5B4C3ShadowGenerationConfig:
    return Gate5B4C3ShadowGenerationConfig(
        enabled=True,
        killSwitchActive=False,
        capStateInitialized=True,
        providerProjectSpendControlsVerified=True,
        selectedBotDigest=BOT_DIGEST,
        trustedOwnerUserIdDigest=OWNER_DIGEST,
        environment="production",
        allowedProviderLabels=("anthropic",),
        allowedModelLabels=("claude-3-5-sonnet-latest",),
        allowedModelRoutes=("anthropic:claude-3-5-sonnet-latest",),
        allowedShadowCredentialRefs=("server-shadow-ref",),
        providerCredentialBindings=(
            Gate5B4C3ShadowGenerationProviderCredentialBinding(
                providerLabel="anthropic",
                credentialRef="server-shadow-ref",
                credentialSource="env_presence",
                requiredEnvVars=(ANTHROPIC_CREDENTIAL_ENV,),
                presentEnvVars=(ANTHROPIC_CREDENTIAL_ENV,),
                adkNative=False,
            ),
        ),
    )


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
    created_kwargs: dict[str, object] = {}

    def __init__(self, **kwargs: object) -> None:
        type(self).created_kwargs = kwargs


class _FakeRunner:
    created_kwargs: dict[str, object] = {}
    run_kwargs: dict[str, object] = {}
    fail_mode: str | None = None

    def __init__(self, **kwargs: object) -> None:
        type(self).created_kwargs = kwargs

    async def run_async(self, **kwargs: object) -> object:
        type(self).run_kwargs = kwargs
        if type(self).fail_mode == "timeout":
            raise TimeoutError("mock timeout")
        if type(self).fail_mode == "error":
            raise RuntimeError("provider failed with Authorization: Bearer raw-secret-token")
        yield {"text": "internal diagnostic event only"}


def _fake_primitives() -> Gate5B4C3LiveAdkPrimitives:
    _FakeAgent.created_kwargs = {}
    _FakeRunner.created_kwargs = {}
    _FakeRunner.run_kwargs = {}
    _FakeRunner.fail_mode = None
    _FakeGenerateContentConfig.created_kwargs = {}
    return Gate5B4C3LiveAdkPrimitives(
        Agent=_FakeAgent,
        Runner=_FakeRunner,
        InMemorySessionService=_FakeSessionService,
        Content=_FakeContent,
        Part=_FakePart,
        GenerateContentConfig=_FakeGenerateContentConfig,
    )


def _mock_loader() -> Gate5B4C3MockedAdkPrimitivesLoader:
    return Gate5B4C3MockedAdkPrimitivesLoader(_fake_primitives)


def _payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "schemaVersion": "gate5b4c3.chatProxyShadowGeneration.v1",
        "mode": "shadow_generation_diagnostic",
        "responseAuthority": "typescript",
        "shadowGenerationId": "shadow_gen_001",
        "requestIdDigest": REQUEST_DIGEST,
        "traceIdDigest": TRACE_DIGEST,
        "createdAt": 1779200000000,
        "selection": {
            "botIdDigest": BOT_DIGEST,
            "ownerUserIdDigest": OWNER_DIGEST,
            "environment": "production",
            "selectedTarget": "gate5b_selected_bot",
            "sessionKeyDigest": SESSION_DIGEST,
        },
        "turn": {
            "turnId": "turn_opaque_001",
            "turnDigest": TURN_DIGEST,
            "sanitizedCurrentTurnText": "Please summarize the approved redacted note.",
            "sanitizedInputTextDigest": SANITIZED_DIGEST,
            "channelName": "app_channel",
            "tsResponseCorrelationId": "ts_corr_001",
            "attachmentMetadata": [
                {"kind": "image", "count": 1, "digest": "sha256:" + "5" * 64},
            ],
        },
        "modelRouting": {
            "routingSource": "per_turn_injected",
            "providerLabel": "anthropic",
            "modelLabel": "claude-3-5-sonnet-latest",
            "routerDecisionDigest": ROUTER_DIGEST,
            "routingProfileDigest": PROFILE_DIGEST,
            "botConfigModelDigest": BOT_CONFIG_DIGEST,
            "shadowCredentialRef": "server-shadow-ref",
            "credentialRefSource": "server_config",
            "temperature": 0.2,
            "maxOutputTokens": 512,
        },
        "recipeProfile": {
            "recipeId": "office-assistant",
            "recipeVersion": "2026-05-19",
            "profileId": "selected-bot-shadow",
            "profileVersion": "v1",
            "runtimeEngine": "adk-python",
            "toolsPolicy": "disabled",
            "memoryMode": "disabled",
            "sourceAuthority": "current_turn_only",
        },
        "policy": {
            "typeScriptResponseAuthority": True,
            "pythonDiagnosticOnly": True,
            "outputIsolation": "local_diagnostic_only",
            "toolsDisabled": True,
            "toolHostDispatchAllowed": False,
            "memoryProviderCallsAllowed": False,
            "memoryWritesAllowed": False,
            "promptMemoryInjectionAllowed": False,
            "workspaceMutationAllowed": False,
            "childExecutionAllowed": False,
            "missionRuntimeAllowed": False,
            "evidenceBlockModeAllowed": False,
        },
        "budgets": {},
        "redaction": {
            "sanitizerId": "chat-proxy-sanitizer",
            "sanitizerVersion": "v1",
            "policyId": "gate5b4c3-redaction",
            "status": "passed",
            "redactedAt": 1779200000001,
            "redactedByteCount": 47,
            "forbiddenFieldScan": "passed",
            "sanitizedPayloadDigest": SANITIZED_DIGEST,
            "droppedFieldReasons": ["raw_auth_header"],
        },
        "comparison": {
            "typeScriptFinalAnswerDigest": "sha256:" + "6" * 64,
            "typeScriptTerminalStatus": "completed",
        },
        "authority": {},
    }
    base.update(overrides)
    return base


def _google_payload(**overrides: object) -> dict[str, object]:
    payload = _payload(
        modelRouting={
            **_payload()["modelRouting"],  # type: ignore[arg-type]
            "providerLabel": GOOGLE_PROVIDER,
            "modelLabel": GOOGLE_MODEL,
            "shadowCredentialRef": GOOGLE_CREDENTIAL_REF,
        }
    )
    payload.update(overrides)
    return payload


def _live_smoke_env(**overrides: str) -> dict[str, str]:
    env = {
        "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_ENABLED": "1",
        "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_KILL_SWITCH": "0",
        "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_CAP_STATE_INITIALIZED": "1",
        "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_PROVIDER_PROJECT_SPEND_CONTROLS_VERIFIED": "1",
        "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_SELECTED_BOT_DIGEST": BOT_DIGEST,
        "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_TRUSTED_OWNER_USER_ID_DIGEST": OWNER_DIGEST,
        "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_ENVIRONMENT": "production",
        "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_PROVIDER_LABEL": GOOGLE_PROVIDER,
        "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_MODEL_LABEL": GOOGLE_MODEL,
        "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_CREDENTIAL_REF": GOOGLE_CREDENTIAL_REF,
        "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_CREDENTIAL_ENV": GOOGLE_CREDENTIAL_ENV,
        "GOOGLE_GENAI_USE_VERTEXAI": "FALSE",
        GOOGLE_CREDENTIAL_ENV: "sample-fixture-value-must-not-leak",
    }
    env.update(overrides)
    return env


def _assert_invalid_generation_response(response_body: dict[str, object]) -> None:
    assert response_body == {
        "error": "invalid_shadow_generation_contract",
        "responseAuthority": "typescript",
        "diagnosticOnly": True,
    }


def test_shadow_generation_endpoint_is_registered_disabled_and_diagnostic_only() -> None:
    response = _client().post("/v1/internal/gate5b/shadow-generations", json=_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["schemaVersion"] == "gate5b4c3.shadowGenerationDiagnostic.v1"
    assert body["accepted"] is False
    assert body["status"] == "skipped"
    assert body["reason"] == "disabled"
    assert body["responseAuthority"] == "typescript"
    assert body["diagnosticOnly"] is True
    assert body["failOpen"] is True
    assert body["adkInvoked"] is False
    assert body["runnerAttempted"] is False
    assert body["modelCallAttempted"] is False
    assert body["outputMetadata"] == {
        "localOnly": True,
        "outputPreviewIncluded": False,
        "outputHashIncluded": False,
        "comparisonArtifactIncluded": False,
    }
    assert body["authority"]["userVisibleOutputAllowed"] is False
    assert body["authority"]["canaryRoutingAllowed"] is False
    assert body["authority"]["toolDispatchAllowed"] is False
    assert body["authority"]["memoryWriteAllowed"] is False
    for forbidden_key in ("content", "message", "text", "finalAnswer", "generationOutput"):
        assert forbidden_key not in body


def test_shadow_generation_endpoint_default_does_not_invoke_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    from magi_agent.transport import shadow_generations

    async def fail_if_called(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("live Runner boundary must not be called by default")

    monkeypatch.setattr(
        shadow_generations,
        "run_gate5b4c3_live_runner_boundary_async",
        fail_if_called,
    )

    response = _client().post("/v1/internal/gate5b/shadow-generations", json=_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["schemaVersion"] == "gate5b4c3.shadowGenerationDiagnostic.v1"
    assert body["reason"] == "disabled"
    assert body["adkInvoked"] is False
    assert body["runnerAttempted"] is False
    assert body["modelCallAttempted"] is False


def test_shadow_generation_live_smoke_env_is_disabled_by_default() -> None:
    route_config = parse_gate5b4c3_shadow_generation_route_env({})

    assert route_config.mocked_runner_boundary_enabled is False
    diagnostic = build_gate5b4c3_shadow_generation_diagnostic(
        Gate5B4C3ShadowGenerationRequest.model_validate(_google_payload()),
        config=route_config.generation_config,
    )

    assert diagnostic.accepted is False
    assert diagnostic.status == "skipped"
    assert diagnostic.reason == "disabled"
    assert diagnostic.runner_attempted is False
    assert diagnostic.model_call_attempted is False


def test_shadow_generation_live_smoke_env_requires_provider_credential_presence() -> None:
    env = _live_smoke_env()
    del env[GOOGLE_CREDENTIAL_ENV]

    route_config = parse_gate5b4c3_shadow_generation_route_env(env)
    diagnostic = build_gate5b4c3_shadow_generation_diagnostic(
        Gate5B4C3ShadowGenerationRequest.model_validate(_google_payload()),
        config=route_config.generation_config,
    )

    assert diagnostic.accepted is False
    assert diagnostic.status == "dropped"
    assert diagnostic.reason == "provider_credential_binding_missing"


def test_shadow_generation_live_smoke_env_reuses_user_visible_scope_and_google_binding() -> None:
    env = _live_smoke_env(
        CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_SELECTED_BOT_DIGEST=BOT_DIGEST,
        CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_TRUSTED_OWNER_USER_ID_DIGEST=OWNER_DIGEST,
        CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_ENVIRONMENT="production",
    )
    del env["CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_SELECTED_BOT_DIGEST"]
    del env["CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_TRUSTED_OWNER_USER_ID_DIGEST"]
    del env["CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_ENVIRONMENT"]
    del env["CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_CREDENTIAL_REF"]
    del env["CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_CREDENTIAL_ENV"]

    route_config = parse_gate5b4c3_shadow_generation_route_env(env)

    config = route_config.generation_config
    assert config.selected_bot_digest == BOT_DIGEST
    assert config.trusted_owner_user_id_digest == OWNER_DIGEST
    assert config.environment == "production"
    assert config.allowed_shadow_credential_refs == (GOOGLE_CREDENTIAL_REF,)
    assert len(config.provider_credential_bindings) == 1
    binding = config.provider_credential_bindings[0]
    assert binding.provider_label == GOOGLE_PROVIDER
    assert binding.credential_ref == GOOGLE_CREDENTIAL_REF
    assert binding.required_env_vars == (GOOGLE_CREDENTIAL_ENV,)
    assert binding.present_env_vars == (GOOGLE_CREDENTIAL_ENV,)


def test_shadow_generation_live_smoke_env_accepts_full_toolhost_runner_timeout() -> None:
    env = _live_smoke_env(
        CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_RUNNER_TIMEOUT_MS="600000",
    )

    route_config = parse_gate5b4c3_shadow_generation_route_env(env)

    assert route_config.generation_config.approved_budgets.python_runner_timeout_ms == 600_000


def test_shadow_generation_live_smoke_env_accepts_selected_adk_llm_call_budget() -> None:
    env = _live_smoke_env(
        CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_MAX_ADK_LLM_CALLS="32",
    )

    route_config = parse_gate5b4c3_shadow_generation_route_env(env)

    assert route_config.generation_config.approved_budgets.max_adk_llm_calls == 32


def test_shadow_generation_live_smoke_env_accepts_selected_production_caps() -> None:
    route_config = parse_gate5b4c3_shadow_generation_route_env(
        _live_smoke_env(
            CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_COST_OWNER_WAIVER="1",
            CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_MAX_CONCURRENT="4",
            CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_MAX_PENDING="16",
            CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_MAX_DAILY="1000",
            CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_MAX_COST_USD="1000",
            CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_MAX_DAILY_COST_USD="100000",
        )
    )

    budgets = route_config.generation_config.approved_budgets

    assert route_config.generation_config.cost_owner_waiver is True
    assert budgets.max_concurrent_generation_runs == 4
    assert budgets.max_pending_generation_runs == 16
    assert budgets.max_daily_generation_runs == 1000
    assert budgets.max_cost_usd == pytest.approx(1000)
    assert budgets.max_daily_generation_cost_usd == pytest.approx(100000)


def test_shadow_generation_live_canary_env_accepts_multi_provider_allowlist_and_bindings() -> None:
    env = _live_smoke_env(
        CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_ALLOWED_MODEL_ROUTES=(
            "google:gemini-3.5-flash,"
            "anthropic:claude-sonnet-4-6,"
            "openai:gpt-5.5,"
            "fireworks:kimi-k2p6"
        ),
        CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_PROVIDER_CREDENTIAL_BINDINGS=(
            "google:gate5b-google-api-key-smoke-v1:GOOGLE_API_KEY:adk,"
            "anthropic:bot-secrets-canary:ANTHROPIC_API_KEY:litellm,"
            "openai:platform-proxy-openai:OPENAI_API_KEY:litellm,"
            "fireworks:platform-proxy-fireworks:FIREWORKS_API_KEY:litellm"
        ),
        CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_PROVIDER_LABEL="",
        CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_MODEL_LABEL="",
        CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_CREDENTIAL_REF="",
        CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_CREDENTIAL_ENV="",
    )
    env[ANTHROPIC_CREDENTIAL_ENV] = "sample-fixture-value-must-not-leak"
    env[OPENAI_CREDENTIAL_ENV] = "sample-fixture-value-must-not-leak"
    env[FIREWORKS_CREDENTIAL_ENV] = "sample-fixture-value-must-not-leak"

    route_config = parse_gate5b4c3_shadow_generation_route_env(env)

    config = route_config.generation_config
    assert config.allowed_provider_labels == (
        "google",
        "anthropic",
        "openai",
        "fireworks",
    )
    assert config.allowed_model_labels == (
        "gemini-3.5-flash",
        "claude-sonnet-4-6",
        "gpt-5.5",
        "kimi-k2p6",
    )
    assert config.allowed_model_routes == (
        "google:gemini-3.5-flash",
        "anthropic:claude-sonnet-4-6",
        "openai:gpt-5.5",
        "fireworks:kimi-k2p6",
    )
    assert config.allowed_shadow_credential_refs == (
        "gate5b-google-api-key-smoke-v1",
        "bot-secrets-canary",
        "platform-proxy-openai",
        "platform-proxy-fireworks",
    )
    assert {
        (binding.provider_label, binding.credential_ref, binding.required_env_vars)
        for binding in config.provider_credential_bindings
    } == {
        ("google", "gate5b-google-api-key-smoke-v1", ("GOOGLE_API_KEY",)),
        ("anthropic", "bot-secrets-canary", ("ANTHROPIC_API_KEY",)),
        ("openai", "platform-proxy-openai", ("OPENAI_API_KEY",)),
        ("fireworks", "platform-proxy-fireworks", ("FIREWORKS_API_KEY",)),
    }


def test_shadow_generation_live_smoke_env_rejects_unsupported_provider() -> None:
    env = _live_smoke_env(
        CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_PROVIDER_LABEL="anthropic",
        CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_MODEL_LABEL="claude-3-5-sonnet-latest",
        CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_CREDENTIAL_REF="anthropic-shadow-ref",
        CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_CREDENTIAL_ENV=ANTHROPIC_CREDENTIAL_ENV,
    )
    env[ANTHROPIC_CREDENTIAL_ENV] = "sample-fixture-value-must-not-leak"

    with pytest.raises(RuntimeEnvError) as excinfo:
        parse_gate5b4c3_shadow_generation_route_env(env)

    assert "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_PROVIDER_LABEL" in str(excinfo.value)
    assert "google" in str(excinfo.value)


def test_shadow_generation_live_smoke_env_rejects_unsupported_model() -> None:
    env = _live_smoke_env(
        CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_MODEL_LABEL="gemini-2.5-flash",
    )

    with pytest.raises(RuntimeEnvError) as excinfo:
        parse_gate5b4c3_shadow_generation_route_env(env)

    assert "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_MODEL_LABEL" in str(excinfo.value)
    assert GOOGLE_MODEL in str(excinfo.value)


@pytest.mark.parametrize(
    ("override", "expected_name"),
    (
        (
            {
                "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_CREDENTIAL_REF": "wrong-ref",
            },
            "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_CREDENTIAL_REF",
        ),
        (
            {
                "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_CREDENTIAL_ENV": "GEMINI_API_KEY",
            },
            "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_CREDENTIAL_ENV",
        ),
        (
            {
                "GOOGLE_GENAI_USE_VERTEXAI": "1",
            },
            "GOOGLE_GENAI_USE_VERTEXAI",
        ),
        (
            {
                "GOOGLE_GENAI_USE_VERTEXAI": "",
            },
            "GOOGLE_GENAI_USE_VERTEXAI",
        ),
        (
            {
                "GOOGLE_GENAI_USE_ENTERPRISE": "true",
            },
            "GOOGLE_GENAI_USE_ENTERPRISE",
        ),
    ),
)
def test_shadow_generation_live_smoke_env_rejects_invalid_google_binding(
    override: dict[str, str],
    expected_name: str,
) -> None:
    env = _live_smoke_env(**override)

    with pytest.raises(RuntimeEnvError) as excinfo:
        parse_gate5b4c3_shadow_generation_route_env(env)

    assert expected_name in str(excinfo.value)


def test_shadow_generation_request_only_credential_metadata_cannot_enable_live_gate() -> None:
    config = Gate5B4C3ShadowGenerationConfig(
        enabled=True,
        killSwitchActive=False,
        capStateInitialized=True,
        providerProjectSpendControlsVerified=True,
        selectedBotDigest=BOT_DIGEST,
        trustedOwnerUserIdDigest=OWNER_DIGEST,
        environment="production",
        allowedProviderLabels=(GOOGLE_PROVIDER,),
        allowedModelLabels=(GOOGLE_MODEL,),
        allowedModelRoutes=(f"{GOOGLE_PROVIDER}:{GOOGLE_MODEL}",),
        allowedShadowCredentialRefs=(GOOGLE_CREDENTIAL_REF,),
        providerCredentialBindingRequired=True,
    )

    diagnostic = build_gate5b4c3_shadow_generation_diagnostic(
        Gate5B4C3ShadowGenerationRequest.model_validate(_google_payload()),
        config=config,
    )

    assert diagnostic.accepted is False
    assert diagnostic.status == "dropped"
    assert diagnostic.reason == "provider_credential_binding_missing"


def test_shadow_generation_bot_config_fallback_requires_server_fallback_approval() -> None:
    route_config = parse_gate5b4c3_shadow_generation_route_env(_live_smoke_env())
    payload = _google_payload(
        modelRouting={
            **_google_payload()["modelRouting"],  # type: ignore[arg-type]
            "routingSource": "bot_config_fallback",
            "routerDecisionDigest": None,
            "routingProfileDigest": None,
            "fallbackReason": "router_unavailable",
            "fallbackApproved": True,
            "botConfigModelDigest": BOT_CONFIG_DIGEST,
        }
    )

    diagnostic = build_gate5b4c3_shadow_generation_diagnostic(
        Gate5B4C3ShadowGenerationRequest.model_validate(payload),
        config=route_config.generation_config,
    )

    assert diagnostic.accepted is False
    assert diagnostic.status == "dropped"
    assert diagnostic.reason == "model_routing_source_not_allowed"


def test_shadow_generation_live_smoke_env_rejects_caps_above_selected_user_visible_limit() -> None:
    env = _live_smoke_env(
        CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_MAX_OUTPUT_TOKENS="4097",
    )

    with pytest.raises(RuntimeEnvError) as excinfo:
        parse_gate5b4c3_shadow_generation_route_env(env)

    assert "CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_MAX_OUTPUT_TOKENS" in str(excinfo.value)


def test_shadow_generation_live_smoke_env_accepts_lower_daily_cost_cap() -> None:
    route_config = parse_gate5b4c3_shadow_generation_route_env(
        _live_smoke_env(
            CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_MAX_DAILY_COST_USD="0.05",
        )
    )

    diagnostic = build_gate5b4c3_shadow_generation_diagnostic(
        Gate5B4C3ShadowGenerationRequest.model_validate(
            _google_payload(budgets={"maxDailyGenerationCostUsd": 0.50})
        ),
        config=route_config.generation_config,
    )

    assert diagnostic.accepted is False
    assert diagnostic.status == "dropped"
    assert diagnostic.reason == "budget_exhausted"


def test_shadow_generation_live_smoke_env_requires_cost_waiver_or_spend_proof() -> None:
    route_config = parse_gate5b4c3_shadow_generation_route_env(
        _live_smoke_env(
            CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_PROVIDER_PROJECT_SPEND_CONTROLS_VERIFIED="0",
            CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_COST_OWNER_WAIVER="0",
        )
    )

    diagnostic = build_gate5b4c3_shadow_generation_diagnostic(
        Gate5B4C3ShadowGenerationRequest.model_validate(_google_payload()),
        config=route_config.generation_config,
    )

    assert diagnostic.accepted is False
    assert diagnostic.status == "dropped"
    assert diagnostic.reason == "budget_exhausted"


def test_shadow_generation_all_live_smoke_gates_present_reaches_mocked_runner_without_secret_leak() -> None:
    parsed_config = parse_gate5b4c3_shadow_generation_route_env(_live_smoke_env())
    client = _configured_client(
        Gate5B4C3ShadowGenerationRouteConfig(
            mockedRunnerBoundaryEnabled=True,
            generationConfig=parsed_config.generation_config,
            mockedAdkPrimitivesLoader=_mock_loader(),
        )
    )

    response = client.post("/v1/internal/gate5b/shadow-generations", json=_google_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["schemaVersion"] == "gate5b4c3.runnerReport.v1"
    assert body["status"] == "completed"
    assert body["reason"] == "runner_completed"
    assert body["responseAuthority"] == "typescript"
    assert body["diagnosticOnly"] is True
    assert body["localOnly"] is True
    assert body["userVisibleOutput"] is None
    assert body["adkRunnerInvoked"] is True
    assert body["modelCallAttempted"] is True
    assert body["authority"]["userVisibleOutputAllowed"] is False
    assert body["authority"]["canaryRoutingAllowed"] is False
    assert body["authority"]["toolDispatchAllowed"] is False
    assert body["authority"]["memoryWriteAllowed"] is False
    assert "sample-fixture-value-must-not-leak" not in str(body)
    assert _FakeAgent.created_kwargs["tools"] == []


def test_shadow_generation_route_uses_env_gate_config_to_enter_fake_live_boundary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from magi_agent.transport import shadow_generations

    runtime = _runtime()
    runtime.gate5b4c3_shadow_generation_route_config = (
        parse_gate5b4c3_shadow_generation_route_env(
            _live_smoke_env(
                CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_COUNTER_STATE_PATH=str(
                    tmp_path / "gate5b-shadow-counters.json"
                )
            )
        )
    )
    client = TestClient(create_app(runtime))
    calls: list[Gate5B4C3ShadowGenerationRequest] = []

    async def fake_live_boundary(
        generation: Gate5B4C3ShadowGenerationRequest,
        *,
        config: Gate5B4C3ShadowGenerationConfig,
        adk_primitives_loader: object | None = None,
    ) -> object:
        assert adk_primitives_loader is None
        calls.append(generation)
        diagnostic = build_gate5b4c3_shadow_generation_diagnostic(
            generation,
            config=config,
        )
        return SimpleNamespace(
            diagnostic=diagnostic,
            status="completed",
            reason="runner_completed",
            adk_invoked=True,
            model_call_via_adk_runner_attempted=True,
            event_count=1,
            latency_ms=12,
            error_class=None,
            error_preview=None,
        )

    monkeypatch.setattr(
        shadow_generations,
        "run_gate5b4c3_live_runner_boundary_async",
        fake_live_boundary,
    )

    response = client.post("/v1/internal/gate5b/shadow-generations", json=_google_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["schemaVersion"] == "gate5b4c3.runnerReport.v1"
    assert body["status"] == "completed"
    assert body["reason"] == "runner_completed"
    assert body["responseAuthority"] == "typescript"
    assert body["diagnosticOnly"] is True
    assert body["localOnly"] is True
    assert body["userVisibleOutput"] is None
    assert body["adkRunnerInvoked"] is True
    assert body["modelCallAttempted"] is True
    assert body["counterStatus"] == "reserved"
    assert body["counterState"]["dailyGenerationRunsUsed"] == 1
    assert body["counterState"]["inFlightGenerationRuns"] == 0
    assert body["authority"]["userVisibleOutputAllowed"] is False
    assert body["authority"]["toolDispatchAllowed"] is False
    assert len(calls) == 1


def test_shadow_generation_live_route_without_counter_store_fails_open_before_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from magi_agent.transport import shadow_generations

    runtime = _runtime()
    runtime.gate5b4c3_shadow_generation_route_config = (
        parse_gate5b4c3_shadow_generation_route_env(_live_smoke_env())
    )
    client = TestClient(create_app(runtime))

    async def fail_if_called(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("live Runner boundary must require durable counters")

    monkeypatch.setattr(
        shadow_generations,
        "run_gate5b4c3_live_runner_boundary_async",
        fail_if_called,
    )

    response = client.post("/v1/internal/gate5b/shadow-generations", json=_google_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["schemaVersion"] == "gate5b4c3.runnerReport.v1"
    assert body["status"] == "error"
    assert body["reason"] == "counter_store_unavailable"
    assert body["counterStatus"] == "unavailable"
    assert body["adkRunnerInvoked"] is False
    assert body["modelCallAttempted"] is False
    assert body["responseAuthority"] == "typescript"
    assert body["userVisibleOutput"] is None


def test_shadow_generation_route_counter_store_deduplicates_retries(
    tmp_path: Path,
) -> None:
    counter_store = Gate5B4C3ShadowCounterStore(tmp_path / "gate5b-shadow-counters.json")
    client = _configured_client(
        Gate5B4C3ShadowGenerationRouteConfig(
            mockedRunnerBoundaryEnabled=True,
            generationConfig=_enabled_gate_config(),
            mockedAdkPrimitivesLoader=_mock_loader(),
            counterStore=counter_store,
        )
    )

    first = client.post("/v1/internal/gate5b/shadow-generations", json=_payload())
    duplicate = client.post("/v1/internal/gate5b/shadow-generations", json=_payload())

    assert first.status_code == 200
    assert first.json()["status"] == "completed"
    assert first.json()["counterState"]["dailyGenerationRunsUsed"] == 1
    assert duplicate.status_code == 200
    body = duplicate.json()
    assert body["status"] == "skipped"
    assert body["reason"] == "idempotency_replay"
    assert body["counterStatus"] == "duplicate_replay"
    assert body["counterState"]["dailyGenerationRunsUsed"] == 1
    assert body["adkRunnerInvoked"] is False
    assert body["modelCallAttempted"] is False


def test_shadow_generation_route_uses_store_time_for_stale_idempotency(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from magi_agent.shadow import gate5b4c3_shadow_counter_store

    counter_store = Gate5B4C3ShadowCounterStore(
        tmp_path / "gate5b-shadow-counters.json",
        stale_after_ms=1_000,
    )
    counter_store.reserve(
        request_digest=REQUEST_DIGEST,
        shadow_generation_id="shadow_gen_001",
        selected_bot_digest=BOT_DIGEST,
        trusted_owner_user_id_digest=OWNER_DIGEST,
        environment="production",
        max_daily_generation_runs=10,
        max_daily_generation_cost_usd=0.50,
        max_concurrent_generation_runs=1,
        max_pending_generation_runs=1,
        cost_cap_usd=0.05,
        now_ms=1_779_200_000_000,
    )
    monkeypatch.setattr(
        gate5b4c3_shadow_counter_store,
        "_coerce_now_ms",
        lambda value: 1_779_200_002_000 if value is None else int(value),
    )
    client = _configured_client(
        Gate5B4C3ShadowGenerationRouteConfig(
            mockedRunnerBoundaryEnabled=True,
            generationConfig=_enabled_gate_config(),
            mockedAdkPrimitivesLoader=_mock_loader(),
            counterStore=counter_store,
        )
    )

    response = client.post("/v1/internal/gate5b/shadow-generations", json=_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "skipped"
    assert body["reason"] == "idempotency_replay"
    assert body["counterStatus"] == "duplicate_replay"
    assert body["counterState"]["staleInFlightReleased"] == 1
    assert body["counterState"]["dailyGenerationRunsUsed"] == 1
    assert body["counterState"]["pendingGenerationRuns"] == 0
    assert body["adkRunnerInvoked"] is False
    assert body["modelCallAttempted"] is False


def test_shadow_generation_route_counter_finish_failure_fails_open(
    tmp_path: Path,
) -> None:
    class FailingFinishCounterStore(Gate5B4C3ShadowCounterStore):
        def finish(self, *args: object, **kwargs: object) -> object:
            raise OSError("counter store write failed")

    client = _configured_client(
        Gate5B4C3ShadowGenerationRouteConfig(
            mockedRunnerBoundaryEnabled=True,
            generationConfig=_enabled_gate_config(),
            mockedAdkPrimitivesLoader=_mock_loader(),
            counterStore=FailingFinishCounterStore(
                tmp_path / "gate5b-shadow-counters.json"
            ),
        )
    )

    response = client.post("/v1/internal/gate5b/shadow-generations", json=_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "error"
    assert body["reason"] == "counter_store_error"
    assert body["counterStatus"] == "error"
    assert body["responseAuthority"] == "typescript"
    assert body["userVisibleOutput"] is None
    assert body["adkRunnerInvoked"] is True
    assert body["modelCallAttempted"] is True


def test_shadow_generation_live_smoke_env_missing_kill_switch_fails_closed() -> None:
    env = _live_smoke_env()
    del env["CORE_AGENT_PYTHON_GATE5B_SHADOW_GENERATION_KILL_SWITCH"]
    route_config = parse_gate5b4c3_shadow_generation_route_env(env)

    diagnostic = build_gate5b4c3_shadow_generation_diagnostic(
        Gate5B4C3ShadowGenerationRequest.model_validate(_google_payload()),
        config=route_config.generation_config,
    )

    assert diagnostic.accepted is False
    assert diagnostic.status == "skipped"
    assert diagnostic.reason == "kill_switch_active"


def test_shadow_generation_endpoint_enabled_mocked_route_invokes_fake_runner_only() -> None:
    client = _configured_client(
        Gate5B4C3ShadowGenerationRouteConfig(
            mockedRunnerBoundaryEnabled=True,
            generationConfig=_enabled_gate_config(),
            mockedAdkPrimitivesLoader=_mock_loader(),
        )
    )

    response = client.post("/v1/internal/gate5b/shadow-generations", json=_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["schemaVersion"] == "gate5b4c3.runnerReport.v1"
    assert body["status"] == "completed"
    assert body["reason"] == "runner_completed"
    assert body["responseAuthority"] == "typescript"
    assert body["diagnosticOnly"] is True
    assert body["localOnly"] is True
    assert body["failOpen"] is True
    assert body["adkRunnerInvoked"] is True
    assert body["modelCallAttempted"] is True
    assert body["eventCount"] == 1
    assert body["runnerTimeoutMs"] == 30_000
    assert body["maxOutputTokens"] == 512
    assert body["maxEstimatedInputTokens"] == 2048
    assert body["maxTotalEstimatedTokens"] == 2560
    assert body["retryPolicy"] == "none"
    assert body["costCapUsd"] == 0.05
    assert body["routingSource"] == "per_turn_injected"
    assert body["routerDecisionDigest"] == ROUTER_DIGEST
    assert body["routingProfileDigest"] == PROFILE_DIGEST
    assert body["botConfigModelDigest"] == BOT_CONFIG_DIGEST
    assert body["shadowCredentialRef"] == "server-shadow-ref"
    assert body["userVisibleOutput"] is None
    assert body["productionWriteTargets"] == []
    assert body["outputDigest"] is not None
    assert body["outputPreviewInternal"] is None
    assert "internal diagnostic event only" not in str(body)
    assert body["authority"]["userVisibleOutputAllowed"] is False
    assert body["authority"]["canaryRoutingAllowed"] is False
    assert body["authority"]["toolDispatchAllowed"] is False
    assert body["authority"]["memoryWriteAllowed"] is False
    assert _FakeAgent.created_kwargs["tools"] == []
    assert _FakeGenerateContentConfig.created_kwargs == {"maxOutputTokens": 512}
    message = _FakeRunner.run_kwargs["new_message"]
    assert isinstance(message, _FakeContent)
    assert message.parts[0].text == "Please summarize the approved redacted note."


def test_shadow_generation_endpoint_enabled_but_gate_skipped_does_not_invoke_runner() -> None:
    client = _configured_client(
        Gate5B4C3ShadowGenerationRouteConfig(
            mockedRunnerBoundaryEnabled=True,
            generationConfig=Gate5B4C3ShadowGenerationConfig(
                enabled=True,
                killSwitchActive=True,
                capStateInitialized=True,
                providerProjectSpendControlsVerified=True,
                selectedBotDigest=BOT_DIGEST,
                trustedOwnerUserIdDigest=OWNER_DIGEST,
                environment="production",
                allowedProviderLabels=("anthropic",),
                allowedModelLabels=("claude-3-5-sonnet-latest",),
                allowedModelRoutes=("anthropic:claude-3-5-sonnet-latest",),
                allowedShadowCredentialRefs=("server-shadow-ref",),
            ),
            mockedAdkPrimitivesLoader=Gate5B4C3MockedAdkPrimitivesLoader(lambda: (_ for _ in ()).throw(
                AssertionError("Runner must not load when kill switch is active")
            )),
        )
    )

    response = client.post("/v1/internal/gate5b/shadow-generations", json=_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["schemaVersion"] == "gate5b4c3.shadowGenerationDiagnostic.v1"
    assert body["status"] == "skipped"
    assert body["reason"] == "kill_switch_active"
    assert body["adkInvoked"] is False
    assert body["runnerAttempted"] is False
    assert body["modelCallAttempted"] is False


def test_shadow_generation_endpoint_enabled_mocked_route_uses_adapter_resolved_caps() -> None:
    client = _configured_client(
        Gate5B4C3ShadowGenerationRouteConfig(
            mockedRunnerBoundaryEnabled=True,
            generationConfig=_enabled_gate_config(),
            mockedAdkPrimitivesLoader=_mock_loader(),
        )
    )
    payload = _payload(
        modelRouting={
            **_payload()["modelRouting"],  # type: ignore[arg-type]
            "maxOutputTokens": 128,
        }
    )

    response = client.post("/v1/internal/gate5b/shadow-generations", json=payload)

    assert response.status_code == 200
    assert response.json()["maxOutputTokens"] == 128
    assert _FakeGenerateContentConfig.created_kwargs == {"maxOutputTokens": 128}


def test_shadow_generation_endpoint_runner_exception_fails_open_with_redacted_report() -> None:
    def failing_primitives() -> Gate5B4C3LiveAdkPrimitives:
        primitives = _fake_primitives()
        _FakeRunner.fail_mode = "error"
        return primitives

    client = _configured_client(
        Gate5B4C3ShadowGenerationRouteConfig(
            mockedRunnerBoundaryEnabled=True,
            generationConfig=_enabled_gate_config(),
            mockedAdkPrimitivesLoader=Gate5B4C3MockedAdkPrimitivesLoader(failing_primitives),
        )
    )

    response = client.post("/v1/internal/gate5b/shadow-generations", json=_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["schemaVersion"] == "gate5b4c3.runnerReport.v1"
    assert body["status"] == "error"
    assert body["reason"] == "runner_error"
    assert body["failOpen"] is True
    assert body["responseAuthority"] == "typescript"
    assert body["errorClass"] == "RuntimeError"
    assert "raw-secret-token" not in body["errorPreview"]
    assert "Authorization:" not in body["errorPreview"]
    assert body["userVisibleOutput"] is None


def test_shadow_generation_endpoint_runner_timeout_fails_open_diagnostically() -> None:
    def timeout_primitives() -> Gate5B4C3LiveAdkPrimitives:
        primitives = _fake_primitives()
        _FakeRunner.fail_mode = "timeout"
        return primitives

    client = _configured_client(
        Gate5B4C3ShadowGenerationRouteConfig(
            mockedRunnerBoundaryEnabled=True,
            generationConfig=_enabled_gate_config(),
            mockedAdkPrimitivesLoader=Gate5B4C3MockedAdkPrimitivesLoader(timeout_primitives),
        )
    )

    response = client.post("/v1/internal/gate5b/shadow-generations", json=_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "error"
    assert body["reason"] == "runner_timeout"
    assert body["failOpen"] is True
    assert body["responseAuthority"] == "typescript"
    assert body["userVisibleOutput"] is None


def test_shadow_generation_endpoint_rejects_unwrapped_loader_and_does_not_invoke_runner() -> None:
    calls: list[str] = []

    def unwrapped_loader() -> Gate5B4C3LiveAdkPrimitives:
        calls.append("called")
        return _fake_primitives()

    client = _configured_client(
        Gate5B4C3ShadowGenerationRouteConfig(
            mockedRunnerBoundaryEnabled=True,
            generationConfig=_enabled_gate_config(),
            adkPrimitivesLoader=unwrapped_loader,
        )
    )

    response = client.post("/v1/internal/gate5b/shadow-generations", json=_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["schemaVersion"] == "gate5b4c3.runnerReport.v1"
    assert body["status"] == "error"
    assert body["reason"] == "runner_error"
    assert body["errorClass"] == "MissingMockedAdkPrimitivesLoader"
    assert body["adkRunnerInvoked"] is False
    assert body["modelCallAttempted"] is False
    assert calls == []


def test_shadow_generation_endpoint_non_boolean_runner_gate_does_not_invoke_runner() -> None:
    _FakeRunner.run_kwargs = {}
    client = _configured_client(
        Gate5B4C3ShadowGenerationRouteConfig(
            mockedRunnerBoundaryEnabled="false",  # type: ignore[arg-type]
            generationConfig=_enabled_gate_config(),
            mockedAdkPrimitivesLoader=_mock_loader(),
        )
    )

    response = client.post("/v1/internal/gate5b/shadow-generations", json=_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["schemaVersion"] == "gate5b4c3.shadowGenerationDiagnostic.v1"
    assert body["reason"] == "accepted"
    assert body["adkInvoked"] is False
    assert body["runnerAttempted"] is False
    assert body["modelCallAttempted"] is False
    assert _FakeRunner.run_kwargs == {}


def test_shadow_generation_endpoint_rejects_truthy_authority_strings_before_runner() -> None:
    _FakeRunner.run_kwargs = {}
    client = _configured_client(
        Gate5B4C3ShadowGenerationRouteConfig(
            mockedRunnerBoundaryEnabled=True,
            generationConfig=_enabled_gate_config(),
            mockedAdkPrimitivesLoader=_mock_loader(),
        )
    )

    response = client.post(
        "/v1/internal/gate5b/shadow-generations",
        json=_payload(authority={"userVisibleOutputAllowed": "true"}),
    )

    assert response.status_code == 422
    _assert_invalid_generation_response(response.json())
    assert _FakeRunner.run_kwargs == {}


def test_shadow_generation_request_provided_gate_values_are_rejected_before_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from magi_agent.transport import shadow_generations

    async def fail_if_called(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("request-controlled gate values must not invoke Runner")

    monkeypatch.setattr(
        shadow_generations,
        "run_gate5b4c3_live_runner_boundary_async",
        fail_if_called,
    )

    response = _client().post(
        "/v1/internal/gate5b/shadow-generations",
        json=_google_payload(
            gateConfig={
                "enabled": True,
                "killSwitchActive": False,
                "credentialEnv": GOOGLE_CREDENTIAL_ENV,
            }
        ),
    )

    assert response.status_code == 422
    _assert_invalid_generation_response(response.json())


@pytest.mark.parametrize(
    "payload",
    (
        _payload(rawUserText="raw user text must not cross this boundary"),
        _payload(
            authority={
                "userVisibleOutputAllowed": True,
                "canaryRoutingAllowed": False,
            }
        ),
        _payload(
            policy={
                **_payload()["policy"],  # type: ignore[arg-type]
                "memoryProviderCallsAllowed": True,
            }
        ),
        _payload(budgets={"maxOutputTokens": 4097}),
        _payload(
            modelRouting={
                **_payload()["modelRouting"],  # type: ignore[arg-type]
                "routingSource": "invalid_or_missing",
                "fallbackReason": "missing_model",
            }
        ),
        _payload(
            modelRouting={
                **_payload()["modelRouting"],  # type: ignore[arg-type]
                "providerLabel": "anthropic:unsafe",
            }
        ),
    ),
)
def test_shadow_generation_endpoint_rejects_unsafe_or_authoritative_payloads(
    payload: dict[str, object],
) -> None:
    response = _client().post("/v1/internal/gate5b/shadow-generations", json=payload)

    assert response.status_code == 422
    _assert_invalid_generation_response(response.json())


def test_shadow_generation_endpoint_returns_422_for_malformed_json() -> None:
    response = _client().post(
        "/v1/internal/gate5b/shadow-generations",
        content='{"schemaVersion":',
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 422
    _assert_invalid_generation_response(response.json())


def test_shadow_generation_route_import_boundary_is_schema_validation_only() -> None:
    route_path = (
        Path(__file__).parents[1]
        / "magi_agent"
        / "transport"
        / "shadow_generations.py"
    )
    source = route_path.read_text(encoding="utf-8")
    forbidden_imports = (
        "google.adk",
        "magi_agent.adk_bridge.runner_adapter",
        "magi_agent.tools",
        "magi_agent.memory",
        "magi_agent.runtime.openmagi_runtime",
        "magi_agent.routing",
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
    assert "Runner(" not in source
    assert "run_async" not in source
    assert "Agent(" not in source
    assert "ToolDispatcher" not in source
    assert "ToolHost" not in source
    assert "MemoryService" not in source
    assert "AgentMemory" not in source
    assert "run_async" not in source
    assert "load_gate5b4c3_live_adk_primitives" not in source
