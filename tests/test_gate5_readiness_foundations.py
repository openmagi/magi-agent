from __future__ import annotations

import hashlib

from fastapi.testclient import TestClient

from magi_agent.app import create_app
from magi_agent.config.env import parse_runtime_env
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime


def _digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _base_env(**overrides: str) -> dict[str, str]:
    env = {
        "BOT_ID": "bot-gate5",
        "USER_ID": "owner-gate5",
        "GATEWAY_TOKEN": "gateway-token",
        "CORE_AGENT_API_PROXY_URL": "http://api-proxy.local",
        "CORE_AGENT_CHAT_PROXY_URL": "http://chat-proxy.local",
        "CORE_AGENT_REDIS_URL": "redis://redis.local:6379/0",
        "CORE_AGENT_MODEL": "gpt-5.2",
    }
    env.update(overrides)
    return env


def test_gate5_readiness_disabled_by_default_and_healthz_has_no_authority() -> None:
    runtime = OpenMagiRuntime(config=parse_runtime_env(_base_env()))
    client = TestClient(create_app(runtime))

    response = client.get("/healthz")

    assert response.status_code == 200
    body = response.json()
    gate5 = body["gate5Readiness"]
    assert gate5["enabled"] is False
    assert gate5["status"] == "disabled"
    assert gate5["readinessReady"] is False
    assert gate5["selectedScopeMatched"] is False
    assert gate5["policyMode"] == "disabled"
    assert gate5["reasonCodes"] == ["gate_disabled"]
    assert gate5["routeAttached"] is False
    assert gate5["shadowEndpointEnabled"] is False
    assert gate5["adkRunnerInvoked"] is False
    assert gate5["liveRunnerAttached"] is False
    assert gate5["modelCallAllowed"] is False
    assert gate5["providerCredentialAllowed"] is False
    assert gate5["proxyEgressAllowed"] is False
    assert gate5["toolHostDispatchAllowed"] is False
    assert gate5["liveToolsExecuted"] is False
    assert gate5["workspaceMutationAllowed"] is False
    assert gate5["memoryWriteAllowed"] is False
    assert gate5["browserWebNetworkAllowed"] is False
    assert gate5["channelDeliveryAllowed"] is False
    assert gate5["schedulerMutationAllowed"] is False
    assert gate5["dbWriteAllowed"] is False
    assert body["workspaceMutationAllowed"] is False
    assert body["userVisibleOutputAllowed"] is False
    assert body["canaryRoutingAllowed"] is False
    assert body["adk"]["invoked"] is False
    assert "FileRead" in body["activeTools"]
    assert "AgentMemorySearch" in body["activeTools"]


def test_gate5_readiness_accepts_selected_non_user_visible_diagnostics_only() -> None:
    runtime = OpenMagiRuntime(
        config=parse_runtime_env(
            _base_env(
                CORE_AGENT_PYTHON_GATE5_READINESS_ENABLED="1",
                CORE_AGENT_PYTHON_GATE5_READINESS_KILL_SWITCH="0",
                CORE_AGENT_PYTHON_GATE5_READINESS_NON_USER_VISIBLE_HARNESS="1",
                CORE_AGENT_PYTHON_GATE5_READINESS_SELECTED_BOT_DIGEST=_digest(
                    "bot-gate5"
                ),
                CORE_AGENT_PYTHON_GATE5_READINESS_TRUSTED_OWNER_USER_ID_DIGEST=_digest(
                    "owner-gate5"
                ),
                CORE_AGENT_PYTHON_GATE5_READINESS_ENVIRONMENT="production",
                CORE_AGENT_PYTHON_GATE5_READINESS_ENV_ALLOWLIST="production",
                CORE_AGENT_PYTHON_GATE5_READINESS_MAX_SHADOW_CHECKS="2",
            )
        )
    )
    client = TestClient(create_app(runtime))

    response = client.get("/healthz")

    assert response.status_code == 200
    gate5 = response.json()["gate5Readiness"]
    assert gate5["enabled"] is True
    assert gate5["status"] == "ready"
    assert gate5["readinessReady"] is True
    assert gate5["selectedScopeMatched"] is True
    assert gate5["policyMode"] == "non_user_visible_shadow_diagnostic"
    assert gate5["localOnly"] is True
    assert gate5["maxShadowChecks"] == 2
    assert gate5["shadowModulesReady"] is True
    assert gate5["readySurfaces"] == [
        "gate5a_no_memory_shadow_canary",
        "gate5b4_internal_endpoint_contract",
        "gate5b4c2_shadow_invocation_contract",
        "gate5b4c3_shadow_generation_contract",
        "gate5b4c3_shadow_generation_report",
        "gate5b4d_stream_fixture_audit",
        "gate5b_user_visible_routing_canary_contract",
    ]
    assert gate5["reasonCodes"] == ["selected_non_user_visible_shadow_ready"]
    assert gate5["routeAttached"] is False
    assert gate5["shadowEndpointEnabled"] is False
    assert gate5["adkRunnerInvoked"] is False
    assert gate5["liveRunnerAttached"] is False
    assert gate5["modelCallAllowed"] is False
    assert gate5["providerCredentialAllowed"] is False
    assert gate5["proxyEgressAllowed"] is False
    assert gate5["toolHostDispatchAllowed"] is False
    assert gate5["liveToolsExecuted"] is False
    assert gate5["workspaceMutationAllowed"] is False
    assert gate5["memoryWriteAllowed"] is False
    assert gate5["browserWebNetworkAllowed"] is False
    assert gate5["channelDeliveryAllowed"] is False
    assert gate5["schedulerMutationAllowed"] is False
    assert gate5["dbWriteAllowed"] is False


def test_gate5_readiness_non_selected_and_malformed_config_fail_closed() -> None:
    selected_env = dict(
        CORE_AGENT_PYTHON_GATE5_READINESS_ENABLED="1",
        CORE_AGENT_PYTHON_GATE5_READINESS_KILL_SWITCH="0",
        CORE_AGENT_PYTHON_GATE5_READINESS_NON_USER_VISIBLE_HARNESS="1",
        CORE_AGENT_PYTHON_GATE5_READINESS_ENVIRONMENT="production",
        CORE_AGENT_PYTHON_GATE5_READINESS_ENV_ALLOWLIST="production",
    )
    non_selected_runtime = OpenMagiRuntime(
        config=parse_runtime_env(
            _base_env(
                **selected_env,
                CORE_AGENT_PYTHON_GATE5_READINESS_SELECTED_BOT_DIGEST=_digest(
                    "other-bot"
                ),
                CORE_AGENT_PYTHON_GATE5_READINESS_TRUSTED_OWNER_USER_ID_DIGEST=_digest(
                    "owner-gate5"
                ),
            )
        )
    )
    malformed_runtime = OpenMagiRuntime(
        config=parse_runtime_env(
            _base_env(
                **selected_env,
                CORE_AGENT_PYTHON_GATE5_READINESS_SELECTED_BOT_DIGEST="bot-gate5",
                CORE_AGENT_PYTHON_GATE5_READINESS_TRUSTED_OWNER_USER_ID_DIGEST=_digest(
                    "owner-gate5"
                ),
            )
        )
    )

    non_selected_gate5 = TestClient(create_app(non_selected_runtime)).get(
        "/healthz"
    ).json()["gate5Readiness"]
    malformed_gate5 = TestClient(create_app(malformed_runtime)).get("/healthz").json()[
        "gate5Readiness"
    ]

    assert non_selected_gate5["status"] == "blocked"
    assert non_selected_gate5["readinessReady"] is False
    assert "bot_not_selected" in non_selected_gate5["reasonCodes"]
    assert non_selected_gate5["shadowEndpointEnabled"] is False
    assert non_selected_gate5["modelCallAllowed"] is False
    assert malformed_gate5["status"] == "blocked"
    assert malformed_gate5["readinessReady"] is False
    assert "malformed_selected_scope" in malformed_gate5["reasonCodes"]
    assert malformed_gate5["shadowEndpointEnabled"] is False
    assert malformed_gate5["modelCallAllowed"] is False


def test_gate5_readiness_ignores_forged_authority_env_flags() -> None:
    runtime = OpenMagiRuntime(
        config=parse_runtime_env(
            _base_env(
                CORE_AGENT_PYTHON_GATE5_READINESS_ENABLED="1",
                CORE_AGENT_PYTHON_GATE5_READINESS_KILL_SWITCH="0",
                CORE_AGENT_PYTHON_GATE5_READINESS_NON_USER_VISIBLE_HARNESS="1",
                CORE_AGENT_PYTHON_GATE5_READINESS_SELECTED_BOT_DIGEST=_digest(
                    "bot-gate5"
                ),
                CORE_AGENT_PYTHON_GATE5_READINESS_TRUSTED_OWNER_USER_ID_DIGEST=_digest(
                    "owner-gate5"
                ),
                CORE_AGENT_PYTHON_GATE5_READINESS_ENVIRONMENT="production",
                CORE_AGENT_PYTHON_GATE5_READINESS_ENV_ALLOWLIST="production",
                CORE_AGENT_PYTHON_GATE5_READINESS_MAX_SHADOW_CHECKS="2",
                CORE_AGENT_PYTHON_GATE5_READINESS_ROUTE_ATTACHED="true",
                CORE_AGENT_PYTHON_GATE5_READINESS_SHADOW_ENDPOINT_ENABLED="true",
                CORE_AGENT_PYTHON_GATE5_READINESS_ADK_RUNNER_INVOKED="true",
                CORE_AGENT_PYTHON_GATE5_READINESS_LIVE_RUNNER_ATTACHED="true",
                CORE_AGENT_PYTHON_GATE5_READINESS_MODEL_CALL_ALLOWED="true",
                CORE_AGENT_PYTHON_GATE5_READINESS_PROVIDER_CREDENTIAL_ALLOWED="true",
                CORE_AGENT_PYTHON_GATE5_READINESS_PROXY_EGRESS_ALLOWED="true",
                CORE_AGENT_PYTHON_GATE5_READINESS_TOOLHOST_DISPATCH_ALLOWED="true",
                CORE_AGENT_PYTHON_GATE5_READINESS_LIVE_TOOLS_EXECUTED="true",
                CORE_AGENT_PYTHON_GATE5_READINESS_WORKSPACE_MUTATION_ALLOWED="true",
                CORE_AGENT_PYTHON_GATE5_READINESS_MEMORY_WRITE_ALLOWED="true",
                CORE_AGENT_PYTHON_GATE5_READINESS_CHANNEL_DELIVERY_ALLOWED="true",
                CORE_AGENT_PYTHON_GATE5_READINESS_DB_WRITE_ALLOWED="true",
            )
        )
    )
    client = TestClient(create_app(runtime))

    gate5 = client.get("/healthz").json()["gate5Readiness"]

    assert gate5["readinessReady"] is True
    assert gate5["routeAttached"] is False
    assert gate5["shadowEndpointEnabled"] is False
    assert gate5["adkRunnerInvoked"] is False
    assert gate5["liveRunnerAttached"] is False
    assert gate5["modelCallAllowed"] is False
    assert gate5["providerCredentialAllowed"] is False
    assert gate5["proxyEgressAllowed"] is False
    assert gate5["toolHostDispatchAllowed"] is False
    assert gate5["liveToolsExecuted"] is False
    assert gate5["workspaceMutationAllowed"] is False
    assert gate5["memoryWriteAllowed"] is False
    assert gate5["channelDeliveryAllowed"] is False
    assert gate5["dbWriteAllowed"] is False
