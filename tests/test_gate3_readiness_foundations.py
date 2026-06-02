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
        "BOT_ID": "bot-gate3",
        "USER_ID": "owner-gate3",
        "GATEWAY_TOKEN": "gateway-token",
        "CORE_AGENT_API_PROXY_URL": "http://api-proxy.local",
        "CORE_AGENT_CHAT_PROXY_URL": "http://chat-proxy.local",
        "CORE_AGENT_REDIS_URL": "redis://redis.local:6379/0",
        "CORE_AGENT_MODEL": "gpt-5.2",
    }
    env.update(overrides)
    return env


def test_gate3_readiness_disabled_by_default_and_healthz_has_no_authority() -> None:
    runtime = OpenMagiRuntime(config=parse_runtime_env(_base_env()))
    client = TestClient(create_app(runtime))

    response = client.get("/healthz")

    assert response.status_code == 200
    body = response.json()
    gate3 = body["gate3Readiness"]
    assert gate3["enabled"] is False
    assert gate3["status"] == "disabled"
    assert gate3["readinessReady"] is False
    assert gate3["selectedScopeMatched"] is False
    assert gate3["policyMode"] == "disabled"
    assert gate3["reasonCodes"] == ["gate_disabled"]
    assert gate3["routeAttached"] is False
    assert gate3["liveCaptureAllowed"] is False
    assert gate3["modelCallAllowed"] is False
    assert gate3["toolHostDispatchAllowed"] is False
    assert gate3["workspaceMutationAllowed"] is False
    assert gate3["memoryWriteAllowed"] is False
    assert gate3["browserWebNetworkAllowed"] is False
    assert gate3["channelDeliveryAllowed"] is False
    assert gate3["dbWriteAllowed"] is False
    assert body["workspaceMutationAllowed"] is False
    assert body["userVisibleOutputAllowed"] is False
    assert body["canaryRoutingAllowed"] is False
    assert body["adk"]["invoked"] is False
    assert body["activeTools"] == []


def test_gate3_readiness_accepts_selected_local_replay_comparison_only() -> None:
    runtime = OpenMagiRuntime(
        config=parse_runtime_env(
            _base_env(
                CORE_AGENT_PYTHON_GATE3_READINESS_ENABLED="1",
                CORE_AGENT_PYTHON_GATE3_READINESS_KILL_SWITCH="0",
                CORE_AGENT_PYTHON_GATE3_READINESS_LOCAL_REPLAY_HARNESS="1",
                CORE_AGENT_PYTHON_GATE3_READINESS_SELECTED_BOT_DIGEST=_digest(
                    "bot-gate3"
                ),
                CORE_AGENT_PYTHON_GATE3_READINESS_TRUSTED_OWNER_USER_ID_DIGEST=_digest(
                    "owner-gate3"
                ),
                CORE_AGENT_PYTHON_GATE3_READINESS_ENVIRONMENT="production",
                CORE_AGENT_PYTHON_GATE3_READINESS_ENV_ALLOWLIST="production",
                CORE_AGENT_PYTHON_GATE3_READINESS_MAX_REPLAY_BUNDLES="2",
            )
        )
    )
    client = TestClient(create_app(runtime))

    response = client.get("/healthz")

    assert response.status_code == 200
    gate3 = response.json()["gate3Readiness"]
    assert gate3["enabled"] is True
    assert gate3["status"] == "ready"
    assert gate3["readinessReady"] is True
    assert gate3["selectedScopeMatched"] is True
    assert gate3["policyMode"] == "recorded_replay_comparison"
    assert gate3["localOnly"] is True
    assert gate3["maxReplayBundles"] == 2
    assert gate3["replayModulesReady"] is True
    assert gate3["readySurfaces"] == [
        "gate3a_recorded_replay",
        "gate3a_comparison_report",
        "gate3b_local_consumer",
        "gate3b_local_report",
        "gate3b_metrics",
    ]
    assert gate3["reasonCodes"] == ["selected_local_replay_ready"]
    assert gate3["liveCaptureAllowed"] is False
    assert gate3["modelCallAllowed"] is False
    assert gate3["toolHostDispatchAllowed"] is False
    assert gate3["workspaceMutationAllowed"] is False
    assert gate3["memoryWriteAllowed"] is False
    assert gate3["browserWebNetworkAllowed"] is False
    assert gate3["channelDeliveryAllowed"] is False
    assert gate3["dbWriteAllowed"] is False


def test_gate3_readiness_non_selected_and_malformed_config_fail_closed() -> None:
    selected_env = dict(
        CORE_AGENT_PYTHON_GATE3_READINESS_ENABLED="1",
        CORE_AGENT_PYTHON_GATE3_READINESS_KILL_SWITCH="0",
        CORE_AGENT_PYTHON_GATE3_READINESS_LOCAL_REPLAY_HARNESS="1",
        CORE_AGENT_PYTHON_GATE3_READINESS_ENVIRONMENT="production",
        CORE_AGENT_PYTHON_GATE3_READINESS_ENV_ALLOWLIST="production",
    )
    non_selected_runtime = OpenMagiRuntime(
        config=parse_runtime_env(
            _base_env(
                **selected_env,
                CORE_AGENT_PYTHON_GATE3_READINESS_SELECTED_BOT_DIGEST=_digest(
                    "other-bot"
                ),
                CORE_AGENT_PYTHON_GATE3_READINESS_TRUSTED_OWNER_USER_ID_DIGEST=_digest(
                    "owner-gate3"
                ),
            )
        )
    )
    malformed_runtime = OpenMagiRuntime(
        config=parse_runtime_env(
            _base_env(
                **selected_env,
                CORE_AGENT_PYTHON_GATE3_READINESS_SELECTED_BOT_DIGEST="bot-gate3",
                CORE_AGENT_PYTHON_GATE3_READINESS_TRUSTED_OWNER_USER_ID_DIGEST=_digest(
                    "owner-gate3"
                ),
            )
        )
    )

    non_selected_gate3 = TestClient(create_app(non_selected_runtime)).get(
        "/healthz"
    ).json()["gate3Readiness"]
    malformed_gate3 = TestClient(create_app(malformed_runtime)).get("/healthz").json()[
        "gate3Readiness"
    ]

    assert non_selected_gate3["status"] == "blocked"
    assert non_selected_gate3["readinessReady"] is False
    assert "bot_not_selected" in non_selected_gate3["reasonCodes"]
    assert non_selected_gate3["workspaceMutationAllowed"] is False
    assert malformed_gate3["status"] == "blocked"
    assert malformed_gate3["readinessReady"] is False
    assert "malformed_selected_scope" in malformed_gate3["reasonCodes"]
    assert malformed_gate3["workspaceMutationAllowed"] is False


def test_gate3_readiness_ignores_forged_authority_env_flags() -> None:
    runtime = OpenMagiRuntime(
        config=parse_runtime_env(
            _base_env(
                CORE_AGENT_PYTHON_GATE3_READINESS_ENABLED="1",
                CORE_AGENT_PYTHON_GATE3_READINESS_KILL_SWITCH="0",
                CORE_AGENT_PYTHON_GATE3_READINESS_LOCAL_REPLAY_HARNESS="1",
                CORE_AGENT_PYTHON_GATE3_READINESS_SELECTED_BOT_DIGEST=_digest(
                    "bot-gate3"
                ),
                CORE_AGENT_PYTHON_GATE3_READINESS_TRUSTED_OWNER_USER_ID_DIGEST=_digest(
                    "owner-gate3"
                ),
                CORE_AGENT_PYTHON_GATE3_READINESS_ENVIRONMENT="production",
                CORE_AGENT_PYTHON_GATE3_READINESS_ENV_ALLOWLIST="production",
                CORE_AGENT_PYTHON_GATE3_READINESS_MAX_REPLAY_BUNDLES="2",
                CORE_AGENT_PYTHON_GATE3_READINESS_ROUTE_ATTACHED="true",
                CORE_AGENT_PYTHON_GATE3_READINESS_LIVE_CAPTURE_ALLOWED="true",
                CORE_AGENT_PYTHON_GATE3_READINESS_MODEL_CALL_ALLOWED="true",
                CORE_AGENT_PYTHON_GATE3_READINESS_TOOLHOST_DISPATCH_ALLOWED="true",
                CORE_AGENT_PYTHON_GATE3_READINESS_WORKSPACE_MUTATION_ALLOWED="true",
                CORE_AGENT_PYTHON_GATE3_READINESS_MEMORY_WRITE_ALLOWED="true",
                CORE_AGENT_PYTHON_GATE3_READINESS_CHANNEL_DELIVERY_ALLOWED="true",
                CORE_AGENT_PYTHON_GATE3_READINESS_DB_WRITE_ALLOWED="true",
            )
        )
    )
    client = TestClient(create_app(runtime))

    gate3 = client.get("/healthz").json()["gate3Readiness"]

    assert gate3["readinessReady"] is True
    assert gate3["routeAttached"] is False
    assert gate3["liveCaptureAllowed"] is False
    assert gate3["modelCallAllowed"] is False
    assert gate3["toolHostDispatchAllowed"] is False
    assert gate3["workspaceMutationAllowed"] is False
    assert gate3["memoryWriteAllowed"] is False
    assert gate3["channelDeliveryAllowed"] is False
    assert gate3["dbWriteAllowed"] is False
