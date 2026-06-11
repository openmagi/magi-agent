import pytest

from magi_agent.config.env import RuntimeEnvError, parse_runtime_env


def sha256_digest(value: str) -> str:
    import hashlib

    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def minimal_env() -> dict[str, str]:
    return {
        "BOT_ID": "bot-test",
        "USER_ID": "user-test",
        "GATEWAY_TOKEN": "gateway-token",
        "CORE_AGENT_API_PROXY_URL": "http://api-proxy.local",
        "CORE_AGENT_CHAT_PROXY_URL": "http://chat-proxy.local",
        "CORE_AGENT_REDIS_URL": "redis://redis.local:6379/0",
        "CORE_AGENT_MODEL": "gpt-5.2",
        "CORE_AGENT_VERSION": "0.1.0-adk-scaffold",
        "CORE_AGENT_BUILD_SHA": "sha-test",
    }


def test_parse_runtime_env_preserves_existing_contract_and_sets_python_engine() -> None:
    config = parse_runtime_env(minimal_env())

    assert config.bot_id == "bot-test"
    assert config.user_id == "user-test"
    assert config.runtime == "magi-agent"
    assert config.runtime_engine == "adk-python"
    assert config.model == "gpt-5.2"
    assert str(config.api_proxy_url) == "http://api-proxy.local/"
    assert str(config.chat_proxy_url) == "http://chat-proxy.local/"
    assert str(config.redis_url) == "redis://redis.local:6379/0"
    assert config.build.version == "0.1.0-adk-scaffold"
    assert config.build.build_sha == "sha-test"
    assert config.authority.user_visible_output_allowed is False
    assert config.authority.canary_routing_allowed is False
    assert config.authority.transcript_write_allowed is False
    assert config.authority.sse_write_allowed is False
    assert config.authority.channel_write_allowed is False
    assert config.authority.db_write_allowed is False
    assert config.authority.workspace_mutation_allowed is False
    assert config.authority.child_execution_allowed is False
    assert config.authority.mission_runtime_allowed is False
    assert config.authority.evidence_block_mode_allowed is False
    assert config.context_continuity.enabled is False
    assert config.context_continuity.continuity_canary_ready is False
    assert config.context_continuity.production_authority_allowed is False


def test_parse_runtime_env_derives_false_authority_from_gate5b_disabled_env() -> None:
    env = minimal_env() | {
        "CORE_AGENT_PYTHON_USER_VISIBLE_OUTPUT": "0",
        "CORE_AGENT_PYTHON_CANARY_ROUTING": "0",
        "CORE_AGENT_PYTHON_TRANSCRIPT_WRITE": "0",
        "CORE_AGENT_PYTHON_SSE_WRITE": "0",
        "CORE_AGENT_PYTHON_CHANNEL_DELIVERY": "0",
        "CORE_AGENT_PYTHON_DB_WRITE": "0",
        "CORE_AGENT_PYTHON_WORKSPACE_MUTATION": "0",
        "CORE_AGENT_PYTHON_CHILD_EXECUTION": "0",
        "CORE_AGENT_PYTHON_MISSION_RUNTIME": "0",
        "CORE_AGENT_PYTHON_EVIDENCE_BLOCK_MODE": "0",
        "CORE_AGENT_PYTHON_OUTPUT_MODE": "diagnostic_only",
    }

    config = parse_runtime_env(env)

    assert config.authority.user_visible_output_allowed is False
    assert config.authority.canary_routing_allowed is False
    assert config.authority.transcript_write_allowed is False
    assert config.authority.sse_write_allowed is False
    assert config.authority.channel_write_allowed is False
    assert config.authority.db_write_allowed is False
    assert config.authority.workspace_mutation_allowed is False
    assert config.authority.child_execution_allowed is False
    assert config.authority.mission_runtime_allowed is False
    assert config.authority.evidence_block_mode_allowed is False


@pytest.mark.parametrize(
    "env_name",
    (
        "CORE_AGENT_PYTHON_USER_VISIBLE_OUTPUT",
        "CORE_AGENT_PYTHON_CANARY_ROUTING",
        "CORE_AGENT_PYTHON_TRANSCRIPT_WRITE",
        "CORE_AGENT_PYTHON_SSE_WRITE",
        "CORE_AGENT_PYTHON_CHANNEL_DELIVERY",
        "CORE_AGENT_PYTHON_DB_WRITE",
        "CORE_AGENT_PYTHON_WORKSPACE_MUTATION",
        "CORE_AGENT_PYTHON_CHILD_EXECUTION",
        "CORE_AGENT_PYTHON_MISSION_RUNTIME",
        "CORE_AGENT_PYTHON_EVIDENCE_BLOCK_MODE",
    ),
)
def test_parse_runtime_env_rejects_enabled_authority_flags(env_name: str) -> None:
    env = minimal_env() | {env_name: "1"}

    with pytest.raises(RuntimeEnvError) as excinfo:
        parse_runtime_env(env)

    assert env_name in str(excinfo.value)


def test_parse_runtime_env_context_continuity_local_diagnostic_is_not_gate8_ready() -> None:
    env = minimal_env() | {
        "CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_ENABLED": "1",
        "CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_MODE": "local_diagnostic",
        "CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_IMPORTED_EVENT_COUNT": "4",
        "CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_REJECTED_ENTRY_COUNT": "1",
        "CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_COMPACTION_APPLIED": "1",
        "CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_PROJECTION_DIGEST": "sha256:" + "a" * 64,
        "CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_SOURCE_TRANSCRIPT_HEAD_DIGEST": (
            "sha256:" + "b" * 64
        ),
        "CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_REASON_CODES": (
            "committed_history_imported,private_payload_rejected"
        ),
    }

    config = parse_runtime_env(env)

    continuity = config.context_continuity
    assert continuity.enabled is True
    assert continuity.mode == "local_diagnostic"
    assert continuity.imported_event_count == 4
    assert continuity.rejected_entry_count == 1
    assert continuity.compaction_applied is True
    assert continuity.projection_digest_present is True
    assert continuity.source_transcript_head_digest_present is True
    assert continuity.reason_codes == (
        "committed_history_imported",
        "private_payload_rejected",
    )
    assert continuity.continuity_canary_ready is False
    assert continuity.production_authority_allowed is False


def test_parse_runtime_env_context_continuity_selected_canary_pass_is_metadata_only() -> None:
    env = minimal_env() | {
        "CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_ENABLED": "1",
        "CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_MODE": "selected_canary",
        "CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_CANARY_STATUS": "pass",
        "CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_IMPORTED_EVENT_COUNT": "3",
        "CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_PROJECTION_DIGEST": "sha256:" + "c" * 64,
        "CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_MODEL_VISIBLE_DIGEST": "sha256:" + "d" * 64,
        "CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_SOURCE_TRANSCRIPT_HEAD_DIGEST": (
            "sha256:" + "e" * 64
        ),
        "CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_FALLBACK_STATUS": "none",
    }

    config = parse_runtime_env(env)

    continuity = config.context_continuity
    assert continuity.enabled is True
    assert continuity.mode == "selected_canary"
    assert continuity.canary_status == "pass"
    assert continuity.canary_evidence_verified is False
    assert continuity.continuity_canary_ready is False
    assert continuity.model_visible_digest_present is True
    assert continuity.fallback_status == "none"
    assert config.authority.user_visible_output_allowed is False
    assert config.authority.canary_routing_allowed is False


@pytest.mark.parametrize(
    "env_name",
    (
        "CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_PRODUCTION_AUTHORITY",
        "CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_TRANSCRIPT_WRITE",
        "CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_SSE_WRITE",
        "CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_DB_WRITE",
        "CORE_AGENT_PYTHON_CONTEXT_CONTINUITY_CANARY_EVIDENCE_VERIFIED",
    ),
)
def test_parse_runtime_env_rejects_context_continuity_live_authority_flags(
    env_name: str,
) -> None:
    env = minimal_env() | {env_name: "1"}

    with pytest.raises(RuntimeEnvError) as excinfo:
        parse_runtime_env(env)

    assert env_name in str(excinfo.value)


def test_parse_runtime_env_allows_user_visible_canary_authority_only_with_server_gates() -> None:
    env = minimal_env() | {
        "CORE_AGENT_PYTHON_CHAT_ROUTE": "on",
        "CORE_AGENT_PYTHON_OUTPUT_MODE": "user_visible_canary",
        "CORE_AGENT_PYTHON_USER_VISIBLE_OUTPUT": "1",
        "CORE_AGENT_PYTHON_CANARY_ROUTING": "1",
        "CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_ENABLED": "1",
        "CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_KILL_SWITCH": "0",
        "CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_SELECTED_BOT_DIGEST": sha256_digest(
            "bot-test"
        ),
        "CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_TRUSTED_OWNER_USER_ID_DIGEST": sha256_digest(
            "user-test"
        ),
        "CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_ENVIRONMENT": "production",
        "CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_ENV_ALLOWLIST": "production",
    }

    config = parse_runtime_env(env)

    assert config.authority.user_visible_output_allowed is True
    assert config.authority.canary_routing_allowed is True
    assert config.authority.transcript_write_allowed is False
    assert config.authority.sse_write_allowed is False
    assert config.authority.channel_write_allowed is False
    assert config.authority.db_write_allowed is False
    assert config.authority.workspace_mutation_allowed is False
    assert config.authority.child_execution_allowed is False
    assert config.authority.mission_runtime_allowed is False
    assert config.authority.evidence_block_mode_allowed is False


def test_parse_runtime_env_rejects_user_visible_canary_authority_with_digest_mismatch() -> None:
    env = minimal_env() | {
        "CORE_AGENT_PYTHON_CHAT_ROUTE": "on",
        "CORE_AGENT_PYTHON_OUTPUT_MODE": "user_visible_canary",
        "CORE_AGENT_PYTHON_USER_VISIBLE_OUTPUT": "1",
        "CORE_AGENT_PYTHON_CANARY_ROUTING": "1",
        "CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_ENABLED": "1",
        "CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_KILL_SWITCH": "0",
        "CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_SELECTED_BOT_DIGEST": sha256_digest(
            "other-bot"
        ),
        "CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_TRUSTED_OWNER_USER_ID_DIGEST": sha256_digest(
            "user-test"
        ),
        "CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_ENVIRONMENT": "production",
        "CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_ENV_ALLOWLIST": "production",
    }

    with pytest.raises(RuntimeEnvError, match="selected bot digest"):
        parse_runtime_env(env)


def test_parse_runtime_env_rejects_user_visible_canary_when_global_gate5b_kill_switch_is_on() -> None:
    env = minimal_env() | {
        "CORE_AGENT_PYTHON_GATE5B_KILL_SWITCH": "1",
        "CORE_AGENT_PYTHON_CHAT_ROUTE": "on",
        "CORE_AGENT_PYTHON_OUTPUT_MODE": "user_visible_canary",
        "CORE_AGENT_PYTHON_USER_VISIBLE_OUTPUT": "1",
        "CORE_AGENT_PYTHON_CANARY_ROUTING": "1",
        "CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_ENABLED": "1",
        "CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_KILL_SWITCH": "0",
        "CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_SELECTED_BOT_DIGEST": sha256_digest(
            "bot-test"
        ),
        "CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_TRUSTED_OWNER_USER_ID_DIGEST": sha256_digest(
            "user-test"
        ),
        "CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_ENVIRONMENT": "production",
        "CORE_AGENT_PYTHON_GATE5B_USER_VISIBLE_CANARY_ENV_ALLOWLIST": "production",
    }

    with pytest.raises(RuntimeEnvError, match="kill switch"):
        parse_runtime_env(env)


def test_parse_runtime_env_requires_existing_identity_and_proxy_fields() -> None:
    env = minimal_env()
    del env["BOT_ID"]

    with pytest.raises(RuntimeEnvError) as excinfo:
        parse_runtime_env(env)

    assert "BOT_ID" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Generic tool-exception reflection env (MAGI_TOOL_EXCEPTION_REFLECTION_ENABLED)
# ---------------------------------------------------------------------------


def test_parse_tool_exception_reflection_env_defaults_off() -> None:
    from magi_agent.config.env import parse_tool_exception_reflection_env

    cfg = parse_tool_exception_reflection_env({})

    assert cfg.enabled is False
    assert cfg.max_attempts == 2


def test_parse_tool_exception_reflection_env_is_profile_independent() -> None:
    """Unlike _runtime_feature_enabled flags, the unset flag stays OFF even
    under the full runtime profile (eval-profile benchmark runs opt in
    explicitly)."""
    from magi_agent.config.env import parse_tool_exception_reflection_env

    assert parse_tool_exception_reflection_env({"MAGI_RUNTIME_PROFILE": "full"}).enabled is False
    assert parse_tool_exception_reflection_env({"MAGI_RUNTIME_PROFILE": "eval"}).enabled is False
    assert (
        parse_tool_exception_reflection_env(
            {"MAGI_RUNTIME_PROFILE": "eval", "MAGI_TOOL_EXCEPTION_REFLECTION_ENABLED": "1"}
        ).enabled
        is True
    )


def test_parse_tool_exception_reflection_env_opt_in_and_budget() -> None:
    from magi_agent.config.env import parse_tool_exception_reflection_env

    on = parse_tool_exception_reflection_env(
        {
            "MAGI_TOOL_EXCEPTION_REFLECTION_ENABLED": "1",
            "MAGI_TOOL_EXCEPTION_MAX_ATTEMPTS": "3",
        }
    )
    assert on.enabled is True
    assert on.max_attempts == 3

    true_form = parse_tool_exception_reflection_env(
        {"MAGI_TOOL_EXCEPTION_REFLECTION_ENABLED": "true"}
    )
    assert true_form.enabled is True
    assert true_form.max_attempts == 2

    off = parse_tool_exception_reflection_env(
        {"MAGI_TOOL_EXCEPTION_REFLECTION_ENABLED": "0"}
    )
    assert off.enabled is False


def test_parse_tool_exception_reflection_env_rejects_invalid_budget() -> None:
    from magi_agent.config.env import (
        RuntimeEnvError as _RuntimeEnvError,
        parse_tool_exception_reflection_env,
    )

    with pytest.raises(_RuntimeEnvError):
        parse_tool_exception_reflection_env({"MAGI_TOOL_EXCEPTION_MAX_ATTEMPTS": "0"})
