from __future__ import annotations

import hashlib

from fastapi.testclient import TestClient

from openmagi_core_agent.app import create_app
from openmagi_core_agent.config.env import parse_runtime_env
from openmagi_core_agent.runtime.openmagi_runtime import OpenMagiRuntime


def _digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _base_env(**overrides: str) -> dict[str, str]:
    env = {
        "BOT_ID": "bot-gate4",
        "USER_ID": "owner-gate4",
        "GATEWAY_TOKEN": "gateway-token",
        "CORE_AGENT_API_PROXY_URL": "http://api-proxy.local",
        "CORE_AGENT_CHAT_PROXY_URL": "http://chat-proxy.local",
        "CORE_AGENT_REDIS_URL": "redis://redis.local:6379/0",
        "CORE_AGENT_MODEL": "gpt-5.2",
    }
    env.update(overrides)
    return env


def test_gate4_readiness_disabled_by_default_and_healthz_has_no_authority() -> None:
    runtime = OpenMagiRuntime(config=parse_runtime_env(_base_env()))
    client = TestClient(create_app(runtime))

    response = client.get("/healthz")

    assert response.status_code == 200
    body = response.json()
    gate4 = body["gate4Readiness"]
    assert gate4["enabled"] is False
    assert gate4["status"] == "disabled"
    assert gate4["readinessReady"] is False
    assert gate4["selectedScopeMatched"] is False
    assert gate4["policyMode"] == "disabled"
    assert gate4["reasonCodes"] == ["gate_disabled"]
    assert gate4["routeAttached"] is False
    assert gate4["adkRunnerInvoked"] is False
    assert gate4["liveRunnerAttached"] is False
    assert gate4["modelCallAllowed"] is False
    assert gate4["toolHostDispatchAllowed"] is False
    assert gate4["liveToolsExecuted"] is False
    assert gate4["workspaceMutationAllowed"] is False
    assert gate4["memoryWriteAllowed"] is False
    assert gate4["browserWebNetworkAllowed"] is False
    assert gate4["channelDeliveryAllowed"] is False
    assert gate4["schedulerMutationAllowed"] is False
    assert gate4["dbWriteAllowed"] is False
    assert body["workspaceMutationAllowed"] is False
    assert body["userVisibleOutputAllowed"] is False
    assert body["canaryRoutingAllowed"] is False
    assert body["adk"]["invoked"] is False
    assert body["activeTools"] == []


def test_gate4_readiness_accepts_selected_local_shadow_adk_attachment_only() -> None:
    runtime = OpenMagiRuntime(
        config=parse_runtime_env(
            _base_env(
                CORE_AGENT_PYTHON_GATE4_READINESS_ENABLED="1",
                CORE_AGENT_PYTHON_GATE4_READINESS_KILL_SWITCH="0",
                CORE_AGENT_PYTHON_GATE4_READINESS_LOCAL_SHADOW_HARNESS="1",
                CORE_AGENT_PYTHON_GATE4_READINESS_SELECTED_BOT_DIGEST=_digest(
                    "bot-gate4"
                ),
                CORE_AGENT_PYTHON_GATE4_READINESS_TRUSTED_OWNER_USER_ID_DIGEST=_digest(
                    "owner-gate4"
                ),
                CORE_AGENT_PYTHON_GATE4_READINESS_ENVIRONMENT="production",
                CORE_AGENT_PYTHON_GATE4_READINESS_ENV_ALLOWLIST="production",
                CORE_AGENT_PYTHON_GATE4_READINESS_MAX_LOCAL_BUNDLES="2",
            )
        )
    )
    client = TestClient(create_app(runtime))

    response = client.get("/healthz")

    assert response.status_code == 200
    gate4 = response.json()["gate4Readiness"]
    assert gate4["enabled"] is True
    assert gate4["status"] == "ready"
    assert gate4["readinessReady"] is True
    assert gate4["selectedScopeMatched"] is True
    assert gate4["policyMode"] == "local_shadow_adk_attachment"
    assert gate4["localOnly"] is True
    assert gate4["maxLocalBundles"] == 2
    assert gate4["shadowModulesReady"] is True
    assert gate4["readySurfaces"] == [
        "gate4_local_shadow_consumer",
        "gate4c0_shadow_config",
        "gate4c1_dry_run_boundary",
        "gate4c1_runner_invoker_contract",
        "gate4c2_shadow_comparison_report",
        "gate4d_local_shadow_diagnostics",
    ]
    assert gate4["reasonCodes"] == ["selected_local_shadow_ready"]
    assert gate4["routeAttached"] is False
    assert gate4["adkRunnerInvoked"] is False
    assert gate4["liveRunnerAttached"] is False
    assert gate4["modelCallAllowed"] is False
    assert gate4["toolHostDispatchAllowed"] is False
    assert gate4["liveToolsExecuted"] is False
    assert gate4["workspaceMutationAllowed"] is False
    assert gate4["memoryWriteAllowed"] is False
    assert gate4["browserWebNetworkAllowed"] is False
    assert gate4["channelDeliveryAllowed"] is False
    assert gate4["schedulerMutationAllowed"] is False
    assert gate4["dbWriteAllowed"] is False


def test_gate4_readiness_non_selected_and_malformed_config_fail_closed() -> None:
    selected_env = dict(
        CORE_AGENT_PYTHON_GATE4_READINESS_ENABLED="1",
        CORE_AGENT_PYTHON_GATE4_READINESS_KILL_SWITCH="0",
        CORE_AGENT_PYTHON_GATE4_READINESS_LOCAL_SHADOW_HARNESS="1",
        CORE_AGENT_PYTHON_GATE4_READINESS_ENVIRONMENT="production",
        CORE_AGENT_PYTHON_GATE4_READINESS_ENV_ALLOWLIST="production",
    )
    non_selected_runtime = OpenMagiRuntime(
        config=parse_runtime_env(
            _base_env(
                **selected_env,
                CORE_AGENT_PYTHON_GATE4_READINESS_SELECTED_BOT_DIGEST=_digest(
                    "other-bot"
                ),
                CORE_AGENT_PYTHON_GATE4_READINESS_TRUSTED_OWNER_USER_ID_DIGEST=_digest(
                    "owner-gate4"
                ),
            )
        )
    )
    malformed_runtime = OpenMagiRuntime(
        config=parse_runtime_env(
            _base_env(
                **selected_env,
                CORE_AGENT_PYTHON_GATE4_READINESS_SELECTED_BOT_DIGEST="bot-gate4",
                CORE_AGENT_PYTHON_GATE4_READINESS_TRUSTED_OWNER_USER_ID_DIGEST=_digest(
                    "owner-gate4"
                ),
            )
        )
    )

    non_selected_gate4 = TestClient(create_app(non_selected_runtime)).get(
        "/healthz"
    ).json()["gate4Readiness"]
    malformed_gate4 = TestClient(create_app(malformed_runtime)).get("/healthz").json()[
        "gate4Readiness"
    ]

    assert non_selected_gate4["status"] == "blocked"
    assert non_selected_gate4["readinessReady"] is False
    assert "bot_not_selected" in non_selected_gate4["reasonCodes"]
    assert non_selected_gate4["liveRunnerAttached"] is False
    assert malformed_gate4["status"] == "blocked"
    assert malformed_gate4["readinessReady"] is False
    assert "malformed_selected_scope" in malformed_gate4["reasonCodes"]
    assert malformed_gate4["liveRunnerAttached"] is False


def test_gate4_readiness_ignores_forged_authority_env_flags() -> None:
    runtime = OpenMagiRuntime(
        config=parse_runtime_env(
            _base_env(
                CORE_AGENT_PYTHON_GATE4_READINESS_ENABLED="1",
                CORE_AGENT_PYTHON_GATE4_READINESS_KILL_SWITCH="0",
                CORE_AGENT_PYTHON_GATE4_READINESS_LOCAL_SHADOW_HARNESS="1",
                CORE_AGENT_PYTHON_GATE4_READINESS_SELECTED_BOT_DIGEST=_digest(
                    "bot-gate4"
                ),
                CORE_AGENT_PYTHON_GATE4_READINESS_TRUSTED_OWNER_USER_ID_DIGEST=_digest(
                    "owner-gate4"
                ),
                CORE_AGENT_PYTHON_GATE4_READINESS_ENVIRONMENT="production",
                CORE_AGENT_PYTHON_GATE4_READINESS_ENV_ALLOWLIST="production",
                CORE_AGENT_PYTHON_GATE4_READINESS_MAX_LOCAL_BUNDLES="2",
                CORE_AGENT_PYTHON_GATE4_READINESS_ROUTE_ATTACHED="true",
                CORE_AGENT_PYTHON_GATE4_READINESS_ADK_RUNNER_INVOKED="true",
                CORE_AGENT_PYTHON_GATE4_READINESS_LIVE_RUNNER_ATTACHED="true",
                CORE_AGENT_PYTHON_GATE4_READINESS_MODEL_CALL_ALLOWED="true",
                CORE_AGENT_PYTHON_GATE4_READINESS_TOOLHOST_DISPATCH_ALLOWED="true",
                CORE_AGENT_PYTHON_GATE4_READINESS_LIVE_TOOLS_EXECUTED="true",
                CORE_AGENT_PYTHON_GATE4_READINESS_WORKSPACE_MUTATION_ALLOWED="true",
                CORE_AGENT_PYTHON_GATE4_READINESS_MEMORY_WRITE_ALLOWED="true",
                CORE_AGENT_PYTHON_GATE4_READINESS_CHANNEL_DELIVERY_ALLOWED="true",
                CORE_AGENT_PYTHON_GATE4_READINESS_DB_WRITE_ALLOWED="true",
            )
        )
    )
    client = TestClient(create_app(runtime))

    gate4 = client.get("/healthz").json()["gate4Readiness"]

    assert gate4["readinessReady"] is True
    assert gate4["routeAttached"] is False
    assert gate4["adkRunnerInvoked"] is False
    assert gate4["liveRunnerAttached"] is False
    assert gate4["modelCallAllowed"] is False
    assert gate4["toolHostDispatchAllowed"] is False
    assert gate4["liveToolsExecuted"] is False
    assert gate4["workspaceMutationAllowed"] is False
    assert gate4["memoryWriteAllowed"] is False
    assert gate4["channelDeliveryAllowed"] is False
    assert gate4["dbWriteAllowed"] is False
