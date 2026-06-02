import pytest

from magi_agent.config.env import RuntimeEnvError, parse_python_memory_adapter_env
from magi_agent.memory.contracts import (
    MemoryProviderCapabilities,
    MemoryRecord,
    RecallResult,
    RecallRequest,
    UnsupportedMemoryOperationError,
)
from magi_agent.memory.policy import MemoryPolicy, evaluate_memory_policy


def test_memory_adapter_env_defaults_off_and_blocks_prompt_projection() -> None:
    config = parse_python_memory_adapter_env({})

    assert config.enabled is False
    assert config.mode == "disabled"
    assert config.adapter == "off"
    assert config.prompt_projection_enabled is False
    assert config.live_provider_calls_enabled is False
    assert config.adk_memory_service_attachment_enabled is False


def test_memory_adapter_env_accepts_generic_safe_adapter_ref_without_live_calls() -> None:
    config = parse_python_memory_adapter_env(
        {
            "CORE_AGENT_PYTHON_MEMORY_ADAPTER": "agentmemory-readonly",
            "CORE_AGENT_PYTHON_MEMORY_ADAPTER_MODE": "readonly_local",
        }
    )

    assert config.enabled is True
    assert config.adapter == "agentmemory_readonly"
    assert config.mode == "readonly_local"
    assert config.prompt_projection_enabled is False
    assert config.live_provider_calls_enabled is False
    assert config.adk_memory_service_attachment_enabled is False


@pytest.mark.parametrize(
    "env_name",
    (
        "CORE_AGENT_PYTHON_MEMORY_PROMPT_PROJECTION",
        "CORE_AGENT_PYTHON_MEMORY_LIVE_PROVIDER_CALLS",
        "CORE_AGENT_PYTHON_MEMORY_ADK_SERVICE_ATTACHMENT",
    ),
)
def test_memory_adapter_env_rejects_unapproved_live_memory_flags(env_name: str) -> None:
    with pytest.raises(RuntimeEnvError, match="not approved"):
        parse_python_memory_adapter_env({env_name: "1"})


def test_memory_capabilities_reject_write_support_in_readonly_contract() -> None:
    capabilities = MemoryProviderCapabilities(
        provider_id="hipocampus-qmd-readonly",
        storage_model="file",
        supports_search=True,
        supports_export=True,
    )

    assert capabilities.supports_write is False
    assert capabilities.supports_delete == "none"

    with pytest.raises(ValueError, match="read-only"):
        MemoryProviderCapabilities(
            provider_id="hipocampus-qmd-readonly",
            storage_model="file",
            supports_write=True,
            supports_search=True,
        )


def test_memory_policy_never_allows_prompt_projection_for_readonly_slice() -> None:
    request = RecallRequest(
        scope={"tenantId": "tenant-1", "botId": "bot-1", "sessionKey": "session-1"},
        query="continue the launch plan",
        purpose="answer_user",
    )

    decision = evaluate_memory_policy(
        request,
        MemoryPolicy(memory_mode="normal", source_authority="long_term_allowed"),
    )

    assert decision.recall_allowed is True
    assert decision.write_allowed is False
    assert decision.prompt_projection_allowed is False
    assert "prompt_projection_disabled" in decision.reason_codes


def test_memory_policy_blocks_incognito_recall_and_all_writes() -> None:
    request = RecallRequest(
        scope={"tenantId": "tenant-1", "botId": "bot-1"},
        query="anything",
        purpose="audit",
    )

    incognito = evaluate_memory_policy(
        request,
        MemoryPolicy(memory_mode="incognito", source_authority="long_term_allowed"),
    )
    read_only = evaluate_memory_policy(
        request,
        MemoryPolicy(memory_mode="read_only", source_authority="long_term_allowed"),
        write_intent=True,
    )

    assert incognito.recall_allowed is False
    assert "incognito_blocks_recall" in incognito.reason_codes
    assert read_only.write_allowed is False
    assert "memory_writes_disabled" in read_only.reason_codes


def test_memory_record_public_projection_is_redacted_and_source_linked() -> None:
    record = MemoryRecord(
        id="root-memory",
        scope="bot",
        kind="note",
        body="Authorization: Bearer unsafe-token and ghp_abcdefghijklmnopqrstuvwxyz012345",
        source_ref="memory/ROOT.md",
        provider_id="hipocampus-qmd-readonly",
        confidence="observed",
        visibility="private",
    )

    projection = record.public_projection()

    assert projection["sourceRef"] == "memory/ROOT.md"
    assert projection["providerId"] == "hipocampus-qmd-readonly"
    assert "snippet" not in projection


def test_memory_record_public_projection_hashes_sensitive_source_refs() -> None:
    record = MemoryRecord(
        id="signed-memory",
        scope="bot",
        kind="note",
        body="Private body.",
        source_ref="https://storage.googleapis.com/private-bucket/object?X-Amz-Signature=unsafe",
        provider_id="hipocampus-qmd-readonly",
        confidence="observed",
        visibility="private",
    )

    projection = record.public_projection()

    assert projection["sourceRef"].startswith("memory:")
    assert "storage.googleapis.com" not in projection["sourceRef"]
    assert "private-bucket" not in projection["sourceRef"]


def test_memory_record_public_projection_hashes_sensitive_record_ids() -> None:
    record = MemoryRecord(
        id="/home/kevin/.ssh/id_rsa",
        scope="bot",
        kind="note",
        body="Private body.",
        source_ref="memory/private.md",
        provider_id="hipocampus-qmd-readonly",
        confidence="observed",
        visibility="private",
    )

    projection = record.public_projection()
    encoded = str(projection)

    assert projection["id"].startswith("memory:")
    assert "/home/kevin" not in encoded
    assert ".ssh" not in encoded


def test_memory_record_public_projection_hashes_sensitive_provider_ids() -> None:
    record = MemoryRecord(
        id="safe-record",
        scope="bot",
        kind="note",
        body="Private body.",
        source_ref="memory/private.md",
        provider_id="sk-live-secretprovider12345",
        confidence="observed",
        visibility="private",
    )

    projection = record.public_projection()
    encoded = str(projection)

    assert projection["providerId"].startswith("provider:")
    assert "sk-live-secretprovider12345" not in encoded


def test_memory_record_public_projection_hashes_raw_child_and_tool_identifier_shapes() -> None:
    record = MemoryRecord(
        id="<tool_log>secret</tool_log>",
        scope="bot",
        kind="note",
        body=(
            "Public summary.\n"
            "<tool_log>secret</tool_log>\n"
            "raw_child_transcript: hidden\n"
            "raw_subagent_transcript_secret: private transcript\n"
            "tool log: internal command output\n"
            "child prompt: private instruction\n"
            "private_memory: diary secret\n"
            "private memory: diary secret\n"
            "private-memory-note"
            "\nraw_subagent_transcript_secret:\nTRANSCRIPT_PAYLOAD_DO_NOT_LEAK"
            "\nprivate_reasoning:\nCOT_PAYLOAD_DO_NOT_LEAK"
            "\nprivate_reasoning:\n\nBLANK_LINE_COT_PAYLOAD_DO_NOT_LEAK"
            "\nraw_subagent_transcript_secret:\n"
            "MULTILINE_PAYLOAD_LINE_ONE_DO_NOT_LEAK\n"
            "MULTILINE_PAYLOAD_LINE_TWO_DO_NOT_LEAK"
        ),
        source_ref="private_memory_note",
        provider_id="private-memory-note",
        confidence="observed",
        visibility="public-safe",
    )

    projection = record.public_projection()
    encoded = str(projection)

    assert projection["id"].startswith("memory:")
    assert projection["sourceRef"].startswith("memory:")
    assert projection["providerId"].startswith("provider:")
    assert projection["snippet"] == "Public summary."
    assert "<tool_log>" not in encoded
    assert "raw_child_transcript" not in encoded
    assert "raw_subagent_transcript" not in encoded
    assert "tool log" not in encoded
    assert "child prompt" not in encoded
    assert "private_memory" not in encoded
    assert "private memory" not in encoded
    assert "private-memory" not in encoded
    assert "diary secret" not in encoded
    assert "TRANSCRIPT_PAYLOAD_DO_NOT_LEAK" not in encoded
    assert "COT_PAYLOAD_DO_NOT_LEAK" not in encoded
    assert "BLANK_LINE_COT_PAYLOAD_DO_NOT_LEAK" not in encoded
    assert "MULTILINE_PAYLOAD_LINE_ONE_DO_NOT_LEAK" not in encoded
    assert "MULTILINE_PAYLOAD_LINE_TWO_DO_NOT_LEAK" not in encoded
    assert "hidden" not in encoded


def test_public_safe_memory_record_projection_strips_raw_and_private_payloads() -> None:
    record = MemoryRecord(
        id="safe-memory",
        scope="bot",
        kind="note",
        body=(
            "Public-safe summary.\n"
            "raw_tool_result: Cookie: session=unsafe\n"
            "s3://private-bucket/object?X-Amz-Signature=unsafe\n"
            "/workspace/bot/private.txt"
        ),
        source_ref="memory/safe.md",
        provider_id="hipocampus-qmd-readonly",
        confidence="observed",
        visibility="public-safe",
    )

    projection = record.public_projection()

    assert projection["snippet"] == "Public-safe summary."


def test_public_safe_memory_record_projection_redacts_home_and_exact_kubelet_paths() -> None:
    record = MemoryRecord(
        id="safe-memory-paths",
        scope="bot",
        kind="note",
        body=(
            "Public-safe summary.\n"
            "/home/kevin/.ssh/id_rsa\n"
            "/var/lib/kubelet\n"
            "/var/lib/kubelet/pods/x/token"
        ),
        source_ref="/home/kevin/.ssh/id_rsa",
        provider_id="hipocampus-qmd-readonly",
        confidence="observed",
        visibility="public-safe",
    )

    projection = record.public_projection()
    encoded = str(projection)

    assert "Public-safe summary" in encoded
    assert "/home/kevin" not in encoded
    assert "/var/lib/kubelet" not in encoded
    assert projection["sourceRef"].startswith("memory:")


def test_recall_result_public_projection_omits_records_when_public_projection_is_blocked() -> None:
    result = RecallResult(
        provider_id="hipocampus-qmd-readonly",
        records=(
            MemoryRecord(
                id="private-memory",
                scope="bot",
                kind="note",
                body="Private memory body must not be exposed.",
                source_ref="memory/private.md",
                provider_id="hipocampus-qmd-readonly",
                confidence="observed",
                visibility="private",
            ),
        ),
        recall_allowed=True,
        write_allowed=False,
        prompt_projection_allowed=False,
        public_projection_allowed=False,
        reason_codes=("source_authority_background_only",),
    )

    projection = result.public_projection()

    assert projection["publicProjectionAllowed"] is False
    assert projection["records"] == []


def test_recall_result_public_projection_hashes_sensitive_record_ids() -> None:
    result = RecallResult(
        provider_id="hipocampus-qmd-readonly",
        records=(
            MemoryRecord(
                id="/var/lib/kubelet/pods/x/token",
                scope="bot",
                kind="note",
                body="Public-safe summary.",
                source_ref="memory/public.md",
                provider_id="hipocampus-qmd-readonly",
                confidence="observed",
                visibility="public-safe",
            ),
        ),
        recall_allowed=True,
        write_allowed=False,
        prompt_projection_allowed=False,
        public_projection_allowed=True,
    )

    projection = result.public_projection()
    encoded = str(projection)

    assert projection["records"][0]["id"].startswith("memory:")
    assert "/var/lib/kubelet" not in encoded


def test_recall_result_public_projection_hashes_sensitive_provider_ids() -> None:
    result = RecallResult(
        provider_id="/home/kevin/private-provider",
        records=(
            MemoryRecord(
                id="safe-record",
                scope="bot",
                kind="note",
                body="Public-safe summary.",
                source_ref="memory/public.md",
                provider_id="/var/lib/kubelet/pods/provider-token",
                confidence="observed",
                visibility="public-safe",
            ),
        ),
        recall_allowed=True,
        write_allowed=False,
        prompt_projection_allowed=False,
        public_projection_allowed=True,
    )

    projection = result.public_projection()
    encoded = str(projection)

    assert projection["providerId"].startswith("provider:")
    assert projection["records"][0]["providerId"].startswith("provider:")
    assert "/home/kevin" not in encoded
    assert "/var/lib/kubelet" not in encoded


def test_recall_result_public_projection_sanitizes_reason_codes() -> None:
    result = RecallResult(
        provider_id="safe-provider",
        records=(),
        recall_allowed=False,
        write_allowed=False,
        prompt_projection_allowed=False,
        public_projection_allowed=False,
        reason_codes=(
            "prompt_projection_disabled",
            "raw_child_transcript /Users/kevin/private Cookie: session=unsafe",
            "raw_subagent_transcript_secret",
            "private_memory:secret",
        ),
    )

    projection = result.public_projection()
    encoded = str(projection)

    assert projection["reasonCodes"][0] == "prompt_projection_disabled"
    assert str(projection["reasonCodes"][1]).startswith("reason:")
    assert str(projection["reasonCodes"][2]).startswith("reason:")
    assert str(projection["reasonCodes"][3]).startswith("reason:")
    assert "raw_child_transcript" not in encoded
    assert "raw_subagent_transcript" not in encoded
    assert "/Users/kevin" not in encoded
    assert "Cookie:" not in encoded
    assert "session=unsafe" not in encoded
    assert "private_memory" not in encoded


def test_recall_result_public_projection_forces_write_and_prompt_authority_false() -> None:
    result = RecallResult.model_construct(
        providerId="hipocampus-qmd-readonly",
        records=(),
        recallAllowed=True,
        writeAllowed=True,
        promptProjectionAllowed=True,
        publicProjectionAllowed=True,
    )
    copied = result.model_copy(update={"writeAllowed": True, "promptProjectionAllowed": True})

    assert result.write_allowed is False
    assert result.prompt_projection_allowed is False
    assert copied.write_allowed is False
    assert copied.prompt_projection_allowed is False
    assert result.public_projection()["writeAllowed"] is False
    assert result.public_projection()["promptProjectionAllowed"] is False


def test_unsupported_write_operations_use_explicit_error_type() -> None:
    error = UnsupportedMemoryOperationError("remember", provider_id="hipocampus-qmd-readonly")

    assert error.operation == "remember"
    assert error.provider_id == "hipocampus-qmd-readonly"
    assert "read-only" in str(error)


def test_unsupported_write_error_message_sanitizes_sensitive_provider_ids() -> None:
    error = UnsupportedMemoryOperationError(
        "remember",
        provider_id="/home/kevin/provider-sk-live-secretprovider12345",
    )
    rendered = str(error)

    assert error.provider_id == "/home/kevin/provider-sk-live-secretprovider12345"
    assert "provider:" in rendered
    assert "/home/kevin" not in rendered
    assert "sk-live-secretprovider12345" not in rendered


def test_unsupported_write_error_message_sanitizes_raw_tool_provider_ids() -> None:
    error = UnsupportedMemoryOperationError(
        "remember",
        provider_id="<tool_log>secret</tool_log>",
    )
    rendered = str(error)

    assert "provider:" in rendered
    assert "<tool_log>" not in rendered
    assert "secret" not in rendered
