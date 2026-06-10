from __future__ import annotations

from pathlib import Path

import pytest

from magi_agent.config.env import (
    RuntimeEnvError,
    parse_gate5b4c3_shadow_generation_route_env,
)
from magi_agent.shadow.gate5b4c3_shadow_generation_contract import (
    Gate5B4C3ShadowGenerationConfig,
    Gate5B4C3ShadowGenerationProviderCredentialBinding,
    Gate5B4C3ShadowGenerationRequest,
    build_gate5b4c3_shadow_generation_diagnostic,
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


def _payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "schemaVersion": "gate5b4c3.chatProxyShadowGeneration.v1",
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
