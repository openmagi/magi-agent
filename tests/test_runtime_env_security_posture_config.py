from __future__ import annotations

import pytest

from magi_agent.config.env import RuntimeEnvError, parse_runtime_env
from magi_agent.config.models import PythonSecurityPostureConfig


def _base_env() -> dict[str, str]:
    return {
        "BOT_ID": "bot-1",
        "USER_ID": "user-1",
        "GATEWAY_TOKEN": "synthetic-gateway-placeholder",
        "CORE_AGENT_API_PROXY_URL": "https://api.openmagi.test",
        "CORE_AGENT_CHAT_PROXY_URL": "https://chat.openmagi.test",
        "CORE_AGENT_REDIS_URL": "redis://redis.openmagi.test:6379/0",
        "CORE_AGENT_MODEL": "test-model",
    }


def test_security_posture_config_defaults_disabled() -> None:
    config = parse_runtime_env(_base_env())

    assert config.security_posture.enabled is False
    assert config.security_posture.posture_preflight_attached is False
    assert config.security_posture.external_surface_dispatch_attached is False
    assert config.security_posture.credential_broker_attached is False
    assert config.security_posture.context_guard_blocks_prompt_projection is False
    assert config.security_posture.supply_chain_startup_banner_attached is False


@pytest.mark.parametrize(
    "flag",
    (
        "CORE_AGENT_PYTHON_SECURITY_EXTERNAL_SURFACE_DISPATCH",
        "CORE_AGENT_PYTHON_SECURITY_CREDENTIAL_BROKER_ATTACHMENT",
        "CORE_AGENT_PYTHON_SECURITY_CONTEXT_GUARD_BLOCK_MODE",
        "CORE_AGENT_PYTHON_SECURITY_SUPPLY_CHAIN_STARTUP_BANNER",
    ),
)
def test_live_security_posture_flags_are_not_approved(flag: str) -> None:
    env = _base_env()
    env[flag] = "true"

    with pytest.raises(RuntimeEnvError, match=f"{flag} is not approved"):
        parse_runtime_env(env)


def test_preflight_artifact_only_flag_is_allowed() -> None:
    env = _base_env()
    env["CORE_AGENT_PYTHON_SECURITY_POSTURE_PREFLIGHT"] = "true"

    config = parse_runtime_env(env)

    assert config.security_posture.enabled is True
    assert config.security_posture.posture_preflight_attached is True
    assert config.security_posture.external_surface_dispatch_attached is False
    assert config.security_posture.credential_broker_attached is False
    assert config.security_posture.context_guard_blocks_prompt_projection is False
    assert config.security_posture.supply_chain_startup_banner_attached is False


def test_security_posture_false_only_flags_cannot_be_forged() -> None:
    constructed = PythonSecurityPostureConfig.model_construct(
        externalSurfaceDispatchAttached=True,
        credentialBrokerAttached=True,
        contextGuardBlocksPromptProjection=True,
        supplyChainStartupBannerAttached=True,
    )
    copied = PythonSecurityPostureConfig().model_copy(
        update={
            "externalSurfaceDispatchAttached": True,
            "credentialBrokerAttached": True,
            "contextGuardBlocksPromptProjection": True,
            "supplyChainStartupBannerAttached": True,
        },
    )

    assert constructed.external_surface_dispatch_attached is False
    assert constructed.credential_broker_attached is False
    assert constructed.context_guard_blocks_prompt_projection is False
    assert constructed.supply_chain_startup_banner_attached is False
    assert copied.external_surface_dispatch_attached is False
    assert copied.credential_broker_attached is False
    assert copied.context_guard_blocks_prompt_projection is False
    assert copied.supply_chain_startup_banner_attached is False


def test_security_posture_enabled_cannot_be_forged_without_preflight() -> None:
    constructed = PythonSecurityPostureConfig.model_construct(
        enabled=True,
        posturePreflightAttached=False,
    )
    copied = PythonSecurityPostureConfig().model_copy(update={"enabled": True})

    assert constructed.enabled is False
    assert constructed.posture_preflight_attached is False
    assert copied.enabled is False
    assert copied.posture_preflight_attached is False
