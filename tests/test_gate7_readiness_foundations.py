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
        "BOT_ID": "bot-gate7",
        "USER_ID": "owner-gate7",
        "GATEWAY_TOKEN": "gateway-token",
        "CORE_AGENT_API_PROXY_URL": "http://api-proxy.local",
        "CORE_AGENT_CHAT_PROXY_URL": "http://chat-proxy.local",
        "CORE_AGENT_REDIS_URL": "redis://redis.local:6379/0",
        "CORE_AGENT_MODEL": "gpt-5.2",
    }
    env.update(overrides)
    return env


def test_gate7_readiness_disabled_by_default_and_healthz_has_no_authority() -> None:
    runtime = OpenMagiRuntime(config=parse_runtime_env(_base_env()))
    client = TestClient(create_app(runtime))

    response = client.get("/healthz")

    assert response.status_code == 200
    body = response.json()
    gate7 = body["gate7Readiness"]
    assert gate7["enabled"] is False
    assert gate7["status"] == "disabled"
    assert gate7["readinessReady"] is False
    assert gate7["selectedScopeMatched"] is False
    assert gate7["policyMode"] == "disabled"
    assert gate7["reasonCodes"] == ["gate_disabled"]
    assert gate7["routeAttached"] is False
    assert gate7["adkRunnerInvoked"] is False
    assert gate7["localFakeChildRunnerReady"] is False
    assert gate7["childExecutionAllowed"] is False
    assert gate7["realChildRunnerExecuted"] is False
    assert gate7["workspaceAdoptionApplied"] is False
    assert gate7["workspaceMutationAllowed"] is False
    assert gate7["modelCallAllowed"] is False
    assert gate7["providerCredentialAllowed"] is False
    assert gate7["proxyEgressAllowed"] is False
    assert gate7["toolHostDispatchAllowed"] is False
    assert gate7["liveToolsExecuted"] is False
    assert gate7["memoryWriteAllowed"] is False
    assert gate7["browserWebNetworkAllowed"] is False
    assert gate7["channelDeliveryAllowed"] is False
    assert gate7["schedulerMutationAllowed"] is False
    assert gate7["dbWriteAllowed"] is False
    assert body["workspaceMutationAllowed"] is False
    assert body["childExecutionAllowed"] is False
    assert body["userVisibleOutputAllowed"] is False
    assert body["canaryRoutingAllowed"] is False
    assert body["adk"]["invoked"] is False
    assert body["activeTools"] == []


def test_gate7_readiness_accepts_selected_local_replay_evaluation_only() -> None:
    runtime = OpenMagiRuntime(
        config=parse_runtime_env(
            _base_env(
                CORE_AGENT_PYTHON_GATE7_READINESS_ENABLED="1",
                CORE_AGENT_PYTHON_GATE7_READINESS_KILL_SWITCH="0",
                CORE_AGENT_PYTHON_GATE7_READINESS_LOCAL_REPLAY_HARNESS="1",
                CORE_AGENT_PYTHON_GATE7_READINESS_SELECTED_BOT_DIGEST=_digest(
                    "bot-gate7"
                ),
                CORE_AGENT_PYTHON_GATE7_READINESS_TRUSTED_OWNER_USER_ID_DIGEST=_digest(
                    "owner-gate7"
                ),
                CORE_AGENT_PYTHON_GATE7_READINESS_ENVIRONMENT="production",
                CORE_AGENT_PYTHON_GATE7_READINESS_ENV_ALLOWLIST="production",
                CORE_AGENT_PYTHON_GATE7_READINESS_MAX_LOCAL_CHILD_TASKS="1",
                CORE_AGENT_PYTHON_GATE7_READINESS_MAX_ENVELOPE_BYTES="8192",
                CORE_AGENT_PYTHON_GATE7_READINESS_MAX_ADOPTION_PREFLIGHTS="1",
            )
        )
    )
    client = TestClient(create_app(runtime))

    response = client.get("/healthz")

    assert response.status_code == 200
    gate7 = response.json()["gate7Readiness"]
    assert gate7["enabled"] is True
    assert gate7["status"] == "ready"
    assert gate7["readinessReady"] is True
    assert gate7["selectedScopeMatched"] is True
    assert gate7["policyMode"] == "local_child_replay_evaluation"
    assert gate7["localOnly"] is True
    assert gate7["fakeOnly"] is True
    assert gate7["maxLocalChildTasks"] == 1
    assert gate7["maxEnvelopeBytes"] == 8192
    assert gate7["maxAdoptionPreflights"] == 1
    assert gate7["childModulesReady"] is True
    assert gate7["readySurfaces"] == [
        "local_child_runner_boundary",
        "child_runtime_envelope",
        "workspace_adoption_preflight_contract",
        "workspace_adoption_boundary",
        "coding_subagent_recipe_boundary",
    ]
    assert gate7["reasonCodes"] == ["selected_local_child_replay_ready"]
    assert gate7["routeAttached"] is False
    assert gate7["adkRunnerInvoked"] is False
    assert gate7["localFakeChildRunnerReady"] is True
    assert gate7["childExecutionAllowed"] is False
    assert gate7["realChildRunnerExecuted"] is False
    assert gate7["workspaceAdoptionApplied"] is False
    assert gate7["workspaceMutationAllowed"] is False
    assert gate7["modelCallAllowed"] is False
    assert gate7["providerCredentialAllowed"] is False
    assert gate7["proxyEgressAllowed"] is False
    assert gate7["toolHostDispatchAllowed"] is False
    assert gate7["liveToolsExecuted"] is False
    assert gate7["memoryWriteAllowed"] is False
    assert gate7["browserWebNetworkAllowed"] is False
    assert gate7["channelDeliveryAllowed"] is False
    assert gate7["schedulerMutationAllowed"] is False
    assert gate7["dbWriteAllowed"] is False


def test_gate7_research_recipe_surface_is_not_a_generic_optional_surface() -> None:
    runtime = OpenMagiRuntime(
        config=parse_runtime_env(
            _base_env(
                CORE_AGENT_PYTHON_GATE7_READINESS_ENABLED="1",
                CORE_AGENT_PYTHON_GATE7_READINESS_KILL_SWITCH="0",
                CORE_AGENT_PYTHON_GATE7_READINESS_LOCAL_REPLAY_HARNESS="1",
                CORE_AGENT_PYTHON_GATE7_READINESS_SELECTED_BOT_DIGEST=_digest(
                    "bot-gate7"
                ),
                CORE_AGENT_PYTHON_GATE7_READINESS_TRUSTED_OWNER_USER_ID_DIGEST=_digest(
                    "owner-gate7"
                ),
                CORE_AGENT_PYTHON_GATE7_READINESS_ENVIRONMENT="local",
                CORE_AGENT_PYTHON_GATE7_READINESS_ENV_ALLOWLIST="local",
                CORE_AGENT_PYTHON_GATE7_READINESS_MAX_LOCAL_CHILD_TASKS="1",
                CORE_AGENT_PYTHON_GATE7_READINESS_MAX_ENVELOPE_BYTES="8192",
                CORE_AGENT_PYTHON_GATE7_READINESS_MAX_ADOPTION_PREFLIGHTS="1",
                CORE_AGENT_PYTHON_GATE7_READINESS_OPTIONAL_SURFACES=(
                    "research_child_runner_recipe_boundary"
                ),
            )
        )
    )
    gate7 = TestClient(create_app(runtime)).get("/healthz").json()["gate7Readiness"]

    assert gate7["status"] == "blocked"
    assert "research_child_runner_recipe_boundary" not in gate7["readySurfaces"]
    assert "unknown_ready_surface" in gate7["reasonCodes"]


def test_gate7_optional_research_surface_blocks_as_unknown_outside_local() -> None:
    runtime = OpenMagiRuntime(
        config=parse_runtime_env(
            _base_env(
                CORE_AGENT_PYTHON_GATE7_READINESS_ENABLED="1",
                CORE_AGENT_PYTHON_GATE7_READINESS_KILL_SWITCH="0",
                CORE_AGENT_PYTHON_GATE7_READINESS_LOCAL_REPLAY_HARNESS="1",
                CORE_AGENT_PYTHON_GATE7_READINESS_SELECTED_BOT_DIGEST=_digest(
                    "bot-gate7"
                ),
                CORE_AGENT_PYTHON_GATE7_READINESS_TRUSTED_OWNER_USER_ID_DIGEST=_digest(
                    "owner-gate7"
                ),
                CORE_AGENT_PYTHON_GATE7_READINESS_ENVIRONMENT="production",
                CORE_AGENT_PYTHON_GATE7_READINESS_ENV_ALLOWLIST="production",
                CORE_AGENT_PYTHON_GATE7_READINESS_MAX_LOCAL_CHILD_TASKS="1",
                CORE_AGENT_PYTHON_GATE7_READINESS_MAX_ENVELOPE_BYTES="8192",
                CORE_AGENT_PYTHON_GATE7_READINESS_MAX_ADOPTION_PREFLIGHTS="1",
                CORE_AGENT_PYTHON_GATE7_READINESS_OPTIONAL_SURFACES=(
                    "research_child_runner_recipe_boundary"
                ),
            )
        )
    )

    gate7 = TestClient(create_app(runtime)).get("/healthz").json()["gate7Readiness"]

    assert gate7["status"] == "blocked"
    assert "research_child_runner_recipe_boundary" not in gate7["readySurfaces"]
    assert "unknown_ready_surface" in gate7["reasonCodes"]


def test_gate7_research_recipe_surface_cannot_be_required_by_generic_core() -> None:
    runtime = OpenMagiRuntime(
        config=parse_runtime_env(
            _base_env(
                CORE_AGENT_PYTHON_GATE7_READINESS_ENABLED="1",
                CORE_AGENT_PYTHON_GATE7_READINESS_KILL_SWITCH="0",
                CORE_AGENT_PYTHON_GATE7_READINESS_LOCAL_REPLAY_HARNESS="1",
                CORE_AGENT_PYTHON_GATE7_READINESS_SELECTED_BOT_DIGEST=_digest(
                    "bot-gate7"
                ),
                CORE_AGENT_PYTHON_GATE7_READINESS_TRUSTED_OWNER_USER_ID_DIGEST=_digest(
                    "owner-gate7"
                ),
                CORE_AGENT_PYTHON_GATE7_READINESS_ENVIRONMENT="production",
                CORE_AGENT_PYTHON_GATE7_READINESS_ENV_ALLOWLIST="production",
                CORE_AGENT_PYTHON_GATE7_READINESS_MAX_LOCAL_CHILD_TASKS="1",
                CORE_AGENT_PYTHON_GATE7_READINESS_MAX_ENVELOPE_BYTES="8192",
                CORE_AGENT_PYTHON_GATE7_READINESS_MAX_ADOPTION_PREFLIGHTS="1",
                CORE_AGENT_PYTHON_GATE7_READINESS_REQUIRED_SURFACES=(
                    "research_child_runner_recipe_boundary"
                ),
            )
        )
    )

    gate7 = TestClient(create_app(runtime)).get("/healthz").json()["gate7Readiness"]

    assert gate7["status"] == "blocked"
    assert gate7["readySurfaces"] == []
    assert "unknown_ready_surface" in gate7["reasonCodes"]


def test_gate7_configured_required_surfaces_cannot_replace_default_hard_surfaces() -> None:
    runtime = OpenMagiRuntime(
        config=parse_runtime_env(
            _base_env(
                CORE_AGENT_PYTHON_GATE7_READINESS_ENABLED="1",
                CORE_AGENT_PYTHON_GATE7_READINESS_KILL_SWITCH="0",
                CORE_AGENT_PYTHON_GATE7_READINESS_LOCAL_REPLAY_HARNESS="1",
                CORE_AGENT_PYTHON_GATE7_READINESS_SELECTED_BOT_DIGEST=_digest(
                    "bot-gate7"
                ),
                CORE_AGENT_PYTHON_GATE7_READINESS_TRUSTED_OWNER_USER_ID_DIGEST=_digest(
                    "owner-gate7"
                ),
                CORE_AGENT_PYTHON_GATE7_READINESS_ENVIRONMENT="production",
                CORE_AGENT_PYTHON_GATE7_READINESS_ENV_ALLOWLIST="production",
                CORE_AGENT_PYTHON_GATE7_READINESS_MAX_LOCAL_CHILD_TASKS="1",
                CORE_AGENT_PYTHON_GATE7_READINESS_MAX_ENVELOPE_BYTES="8192",
                CORE_AGENT_PYTHON_GATE7_READINESS_MAX_ADOPTION_PREFLIGHTS="1",
                CORE_AGENT_PYTHON_GATE7_READINESS_REQUIRED_SURFACES=(
                    "local_child_runner_boundary"
                ),
            )
        )
    )

    gate7 = TestClient(create_app(runtime)).get("/healthz").json()["gate7Readiness"]

    assert gate7["status"] == "ready"
    assert gate7["readySurfaces"] == [
        "local_child_runner_boundary",
        "child_runtime_envelope",
        "workspace_adoption_preflight_contract",
        "workspace_adoption_boundary",
        "coding_subagent_recipe_boundary",
    ]
    assert gate7["reasonCodes"] == ["selected_local_child_replay_ready"]


def test_gate7_readiness_non_selected_and_malformed_config_fail_closed() -> None:
    selected_env = dict(
        CORE_AGENT_PYTHON_GATE7_READINESS_ENABLED="1",
        CORE_AGENT_PYTHON_GATE7_READINESS_KILL_SWITCH="0",
        CORE_AGENT_PYTHON_GATE7_READINESS_LOCAL_REPLAY_HARNESS="1",
        CORE_AGENT_PYTHON_GATE7_READINESS_ENVIRONMENT="production",
        CORE_AGENT_PYTHON_GATE7_READINESS_ENV_ALLOWLIST="production",
        CORE_AGENT_PYTHON_GATE7_READINESS_MAX_LOCAL_CHILD_TASKS="1",
        CORE_AGENT_PYTHON_GATE7_READINESS_MAX_ENVELOPE_BYTES="8192",
        CORE_AGENT_PYTHON_GATE7_READINESS_MAX_ADOPTION_PREFLIGHTS="1",
    )
    non_selected_runtime = OpenMagiRuntime(
        config=parse_runtime_env(
            _base_env(
                **selected_env,
                CORE_AGENT_PYTHON_GATE7_READINESS_SELECTED_BOT_DIGEST=_digest(
                    "other-bot"
                ),
                CORE_AGENT_PYTHON_GATE7_READINESS_TRUSTED_OWNER_USER_ID_DIGEST=_digest(
                    "owner-gate7"
                ),
            )
        )
    )
    malformed_runtime = OpenMagiRuntime(
        config=parse_runtime_env(
            _base_env(
                **selected_env,
                CORE_AGENT_PYTHON_GATE7_READINESS_SELECTED_BOT_DIGEST="bot-gate7",
                CORE_AGENT_PYTHON_GATE7_READINESS_TRUSTED_OWNER_USER_ID_DIGEST=_digest(
                    "owner-gate7"
                ),
            )
        )
    )

    non_selected_gate7 = TestClient(create_app(non_selected_runtime)).get(
        "/healthz"
    ).json()["gate7Readiness"]
    malformed_gate7 = TestClient(create_app(malformed_runtime)).get("/healthz").json()[
        "gate7Readiness"
    ]

    assert non_selected_gate7["status"] == "blocked"
    assert non_selected_gate7["readinessReady"] is False
    assert "bot_not_selected" in non_selected_gate7["reasonCodes"]
    assert non_selected_gate7["childExecutionAllowed"] is False
    assert non_selected_gate7["workspaceMutationAllowed"] is False
    assert malformed_gate7["status"] == "blocked"
    assert malformed_gate7["readinessReady"] is False
    assert "malformed_selected_scope" in malformed_gate7["reasonCodes"]
    assert malformed_gate7["childExecutionAllowed"] is False
    assert malformed_gate7["workspaceMutationAllowed"] is False


def test_gate7_readiness_ignores_forged_authority_env_flags() -> None:
    runtime = OpenMagiRuntime(
        config=parse_runtime_env(
            _base_env(
                CORE_AGENT_PYTHON_GATE7_READINESS_ENABLED="1",
                CORE_AGENT_PYTHON_GATE7_READINESS_KILL_SWITCH="0",
                CORE_AGENT_PYTHON_GATE7_READINESS_LOCAL_REPLAY_HARNESS="1",
                CORE_AGENT_PYTHON_GATE7_READINESS_SELECTED_BOT_DIGEST=_digest(
                    "bot-gate7"
                ),
                CORE_AGENT_PYTHON_GATE7_READINESS_TRUSTED_OWNER_USER_ID_DIGEST=_digest(
                    "owner-gate7"
                ),
                CORE_AGENT_PYTHON_GATE7_READINESS_ENVIRONMENT="production",
                CORE_AGENT_PYTHON_GATE7_READINESS_ENV_ALLOWLIST="production",
                CORE_AGENT_PYTHON_GATE7_READINESS_MAX_LOCAL_CHILD_TASKS="1",
                CORE_AGENT_PYTHON_GATE7_READINESS_MAX_ENVELOPE_BYTES="8192",
                CORE_AGENT_PYTHON_GATE7_READINESS_MAX_ADOPTION_PREFLIGHTS="1",
                CORE_AGENT_PYTHON_GATE7_READINESS_ROUTE_ATTACHED="true",
                CORE_AGENT_PYTHON_GATE7_READINESS_ADK_RUNNER_INVOKED="true",
                CORE_AGENT_PYTHON_GATE7_READINESS_CHILD_EXECUTION_ALLOWED="true",
                CORE_AGENT_PYTHON_GATE7_READINESS_REAL_CHILD_RUNNER_EXECUTED="true",
                CORE_AGENT_PYTHON_GATE7_READINESS_WORKSPACE_ADOPTION_APPLIED="true",
                CORE_AGENT_PYTHON_GATE7_READINESS_WORKSPACE_MUTATION_ALLOWED="true",
                CORE_AGENT_PYTHON_GATE7_READINESS_MODEL_CALL_ALLOWED="true",
                CORE_AGENT_PYTHON_GATE7_READINESS_PROVIDER_CREDENTIAL_ALLOWED="true",
                CORE_AGENT_PYTHON_GATE7_READINESS_PROXY_EGRESS_ALLOWED="true",
                CORE_AGENT_PYTHON_GATE7_READINESS_TOOLHOST_DISPATCH_ALLOWED="true",
                CORE_AGENT_PYTHON_GATE7_READINESS_LIVE_TOOLS_EXECUTED="true",
                CORE_AGENT_PYTHON_GATE7_READINESS_MEMORY_WRITE_ALLOWED="true",
                CORE_AGENT_PYTHON_GATE7_READINESS_CHANNEL_DELIVERY_ALLOWED="true",
                CORE_AGENT_PYTHON_GATE7_READINESS_DB_WRITE_ALLOWED="true",
            )
        )
    )
    client = TestClient(create_app(runtime))

    gate7 = client.get("/healthz").json()["gate7Readiness"]

    assert gate7["readinessReady"] is True
    assert gate7["routeAttached"] is False
    assert gate7["adkRunnerInvoked"] is False
    assert gate7["childExecutionAllowed"] is False
    assert gate7["realChildRunnerExecuted"] is False
    assert gate7["workspaceAdoptionApplied"] is False
    assert gate7["workspaceMutationAllowed"] is False
    assert gate7["modelCallAllowed"] is False
    assert gate7["providerCredentialAllowed"] is False
    assert gate7["proxyEgressAllowed"] is False
    assert gate7["toolHostDispatchAllowed"] is False
    assert gate7["liveToolsExecuted"] is False
    assert gate7["memoryWriteAllowed"] is False
    assert gate7["channelDeliveryAllowed"] is False
    assert gate7["dbWriteAllowed"] is False
