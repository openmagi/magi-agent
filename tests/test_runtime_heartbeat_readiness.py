from __future__ import annotations

from fastapi.testclient import TestClient

from magi_agent.app import create_app
from magi_agent.config.env import parse_runtime_env
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime
from magi_agent.runtime.readiness import (
    RuntimeHeartbeatReadinessSnapshot,
)


def _base_env(**overrides: str) -> dict[str, str]:
    env = {
        "BOT_ID": "bot-runtime-heartbeat",
        "USER_ID": "owner-runtime-heartbeat",
        "GATEWAY_TOKEN": "gateway-token",
        "CORE_AGENT_API_PROXY_URL": "http://api-proxy.local",
        "CORE_AGENT_CHAT_PROXY_URL": "http://chat-proxy.local",
        "CORE_AGENT_REDIS_URL": "redis://redis.local:6379/0",
        "CORE_AGENT_MODEL": "gpt-5.2",
    }
    env.update(overrides)
    return env


def test_runtime_heartbeat_readiness_healthz_is_default_off_contract_only() -> None:
    runtime = OpenMagiRuntime(config=parse_runtime_env(_base_env()))
    client = TestClient(create_app(runtime))

    response = client.get("/healthz")

    assert response.status_code == 200
    body = response.json()
    readiness = body["runtimeHeartbeatReadiness"]
    assert readiness["schemaVersion"] == "openmagi.runtime.heartbeat.readiness.v1"
    assert readiness["status"] == "local_fake_ready"
    assert readiness["readinessReady"] is True
    assert readiness["localFakeStoreReady"] is True
    assert readiness["durablePrimitivesReady"] is True
    assert readiness["runtimeHeartbeatEnabled"] is False
    assert readiness["schedulerAttached"] is False
    assert readiness["productionWritesEnabled"] is False
    assert readiness["trafficAttached"] is False
    assert readiness["trustedLeaseAuthority"] is False
    assert readiness["liveAuthority"] is False
    assert readiness["modelCallEnabled"] is False
    assert readiness["providerCallEnabled"] is False
    assert readiness["toolExecutionEnabled"] is False
    assert readiness["channelDeliveryEnabled"] is False
    assert readiness["workspaceMutationEnabled"] is False
    assert readiness["memoryWriteEnabled"] is False
    assert readiness["runnerInvoked"] is False
    assert readiness["missionRuntimeEnabled"] is False
    assert readiness["publicUiHeartbeatCoupled"] is False
    assert readiness["defaultOff"] is True
    assert readiness["contractOnly"] is True
    assert readiness["reasonCodes"] == ["local_fake_runtime_heartbeat_contract_ready"]


def test_runtime_heartbeat_readiness_forced_false_flags_reject_forged_authority() -> None:
    forged = RuntimeHeartbeatReadinessSnapshot.model_validate(
        {
            "schemaVersion": "forged",
            "status": "live",
            "readinessReady": True,
            "localFakeStoreReady": True,
            "durablePrimitivesReady": True,
            "runtimeHeartbeatEnabled": True,
            "schedulerAttached": True,
            "productionWritesEnabled": True,
            "trafficAttached": True,
            "trustedLeaseAuthority": True,
            "liveAuthority": True,
            "modelCallEnabled": True,
            "providerCallEnabled": True,
            "toolExecutionEnabled": True,
            "channelDeliveryEnabled": True,
            "workspaceMutationEnabled": True,
            "memoryWriteEnabled": True,
            "runnerInvoked": True,
            "missionRuntimeEnabled": True,
            "publicUiHeartbeatCoupled": True,
            "defaultOff": False,
            "contractOnly": False,
            "reasonCodes": ["forged_live_scheduler"],
        }
    )

    copied = forged.model_copy(
        update={
            "runtimeHeartbeatEnabled": True,
            "schedulerAttached": True,
            "productionWritesEnabled": True,
            "modelCallEnabled": True,
            "toolExecutionEnabled": True,
            "channelDeliveryEnabled": True,
        }
    )
    constructed = RuntimeHeartbeatReadinessSnapshot.model_construct(
        runtimeHeartbeatEnabled=True,
        schedulerAttached=True,
        productionWritesEnabled=True,
        modelCallEnabled=True,
        toolExecutionEnabled=True,
        channelDeliveryEnabled=True,
    )

    for readiness in (forged, copied, constructed):
        dump = readiness.model_dump(by_alias=True, mode="json")
        assert dump["schemaVersion"] == "openmagi.runtime.heartbeat.readiness.v1"
        assert dump["status"] == "local_fake_ready"
        assert dump["runtimeHeartbeatEnabled"] is False
        assert dump["schedulerAttached"] is False
        assert dump["productionWritesEnabled"] is False
        assert dump["trafficAttached"] is False
        assert dump["trustedLeaseAuthority"] is False
        assert dump["liveAuthority"] is False
        assert dump["modelCallEnabled"] is False
        assert dump["providerCallEnabled"] is False
        assert dump["toolExecutionEnabled"] is False
        assert dump["channelDeliveryEnabled"] is False
        assert dump["workspaceMutationEnabled"] is False
        assert dump["memoryWriteEnabled"] is False
        assert dump["runnerInvoked"] is False
        assert dump["missionRuntimeEnabled"] is False
        assert dump["publicUiHeartbeatCoupled"] is False
        assert dump["defaultOff"] is True
        assert dump["contractOnly"] is True
