from __future__ import annotations

import asyncio
import ast
import importlib
import json
from pathlib import Path

import pytest
from google.adk.memory import BaseMemoryService
from google.adk.memory.base_memory_service import SearchMemoryResponse
from google.adk.memory.memory_entry import MemoryEntry
from google.genai import types

from openmagi_core_agent.memory.contracts import (
    MemoryRecord,
    RecallRequest,
    RecallResult,
    UnsupportedMemoryOperationError,
)
from openmagi_core_agent.memory.policy import MemoryPolicy


def recall_request() -> RecallRequest:
    return RecallRequest(
        scope={"tenantId": "tenant-1", "botId": "bot-1", "sessionKey": "session-1"},
        query="launch plan",
        purpose="answer_user",
    )


class FakeProvider:
    openmagi_local_fake_provider = True

    def __init__(self) -> None:
        self.calls = 0

    async def recall(
        self,
        request: RecallRequest,
        *,
        policy: MemoryPolicy,
    ) -> RecallResult:
        self.calls += 1
        return RecallResult(
            provider_id="fake-hipocampus-qmd",
            records=(
                MemoryRecord(
                    id="raw-provider-record",
                    scope="bot",
                    kind="note",
                    body=(
                        "Launch decision: keep runtime memory disabled by default.\n"
                        "raw_prompt: include sk-provider-secret\n"
                        "raw_tool_log: Authorization: Bearer unsafe\n"
                        "raw_child_output: /Users/kevin/private/child.txt\n"
                        "chain_of_thought: hidden scratchpad\n"
                        "private_reasoning: hidden analysis\n"
                        "reasoning_trace: hidden tokens\n"
                        "model_internal: hidden state"
                    ),
                    source_ref="s3://private-bucket/memory/ROOT.md?token=unsafe",
                    provider_id="fake-hipocampus-qmd",
                    confidence="observed",
                    visibility="private",
                    score=0.92,
                    custom_metadata={
                        "context": "Hipocampus qmd local fixture",
                        "hiddenRawPrompt": "raw prompt must not leak",
                        "privatePath": "/Users/kevin/.ssh/id_rsa",
                        "authorization": "Bearer unsafe",
                        "sourcePointer": "https://example.test/page?access_token=unsafe",
                    },
                ),
            ),
            recall_allowed=True,
            write_allowed=False,
            prompt_projection_allowed=False,
            public_projection_allowed=True,
            reason_codes=("provider_fixture",),
        )


class FakeADKMemoryService(BaseMemoryService):
    openmagi_local_fake_provider = True

    def __init__(self) -> None:
        self.search_calls: list[tuple[str, str, str]] = []
        self.add_memory_calls = 0
        self.add_session_calls = 0

    def add_session_to_memory(self, session: object) -> None:
        self.add_session_calls += 1

    def search_memory(
        self,
        *,
        app_name: str,
        user_id: str,
        query: str,
    ) -> SearchMemoryResponse:
        self.search_calls.append((app_name, user_id, query))
        return SearchMemoryResponse(
            memories=[
                MemoryEntry(
                    id="adk-memory-1",
                    author="hipocampus",
                    timestamp="2026-05-20T00:00:00Z",
                    content=types.Content(
                        role="model",
                        parts=[
                            types.Part(
                                text=(
                                    "Launch plan memory from ADK.\n"
                                    "secret_token=sk-adk-secret\n"
                                    "raw_child_output: /workspace/bot/private.txt"
                                )
                            )
                        ],
                    ),
                    custom_metadata={
                        "sourceRef": "https://user:pass@example.test/private?api_key=unsafe",
                        "scope": "bot",
                        "kind": "note",
                        "confidence": "observed",
                        "visibility": "private",
                        "score": 0.88,
                        "rawToolLog": "Authorization: Bearer unsafe",
                    },
                )
            ]
        )

    def add_memory(self, **_: object) -> None:
        self.add_memory_calls += 1


def test_bridge_defaults_off_and_never_calls_provider_or_adk_service() -> None:
    from openmagi_core_agent.memory.adk_bridge import (
        ADKMemoryBridgeConfig,
        ADKMemoryServiceBridge,
    )

    provider = FakeProvider()
    adk_service = FakeADKMemoryService()
    bridge = ADKMemoryServiceBridge(
        ADKMemoryBridgeConfig(),
        provider=provider,
        adk_memory_service=adk_service,
    )

    outcome = asyncio.run(
        bridge.recall(
            recall_request(),
            policy=MemoryPolicy(memory_mode="normal", source_authority="long_term_allowed"),
        )
    )

    assert provider.calls == 0
    assert adk_service.search_calls == []
    assert outcome.result.records == ()
    assert outcome.result.prompt_projection_allowed is False
    assert outcome.result.write_allowed is False
    assert outcome.diagnostic_metadata == {
        "provider_called": False,
        "adk_service_called": False,
        "prompt_projection_allowed": False,
        "memory_writes_enabled": False,
        "production_storage_enabled": False,
    }


def test_bridge_local_fake_provider_path_respects_policy_and_sanitizes_public_projection() -> None:
    from openmagi_core_agent.memory.adk_bridge import (
        ADKMemoryBridgeConfig,
        ADKMemoryServiceBridge,
    )

    provider = FakeProvider()
    bridge = ADKMemoryServiceBridge(
        ADKMemoryBridgeConfig(enabled=True, local_fake_provider_enabled=True),
        provider=provider,
    )

    outcome = asyncio.run(
        bridge.recall(
            recall_request(),
            policy=MemoryPolicy(memory_mode="read_only", source_authority="long_term_allowed"),
        )
    )

    rendered_projection = json.dumps(outcome.public_projection(), sort_keys=True)
    rendered_diagnostics = json.dumps(outcome.diagnostic_metadata, sort_keys=True)

    assert provider.calls == 1
    assert outcome.result.recall_allowed is True
    assert outcome.result.write_allowed is False
    assert outcome.diagnostic_metadata["provider_called"] is True
    assert outcome.diagnostic_metadata["adk_service_called"] is False
    assert "Launch decision" in outcome.result.records[0].body
    assert "Launch decision" not in rendered_projection
    assert "snippet" not in rendered_projection
    assert "s3://private-bucket" not in rendered_projection
    assert "access_token=unsafe" not in rendered_projection
    assert "sk-provider-secret" not in rendered_projection
    assert "Authorization: Bearer unsafe" not in rendered_projection
    assert "raw_prompt" not in rendered_projection
    assert "raw_tool_log" not in rendered_projection
    assert "raw_child_output" not in rendered_projection
    assert "chain_of_thought" not in rendered_projection
    assert "private_reasoning" not in rendered_projection
    assert "reasoning_trace" not in rendered_projection
    assert "model_internal" not in rendered_projection
    assert "/Users/kevin" not in rendered_projection
    assert ".ssh" not in rendered_projection
    assert "raw prompt must not leak" not in rendered_projection
    assert "/Users/kevin" not in rendered_diagnostics


@pytest.mark.parametrize(
    "policy, reason",
    (
        (
            MemoryPolicy(memory_mode="incognito", source_authority="long_term_allowed"),
            "incognito_blocks_recall",
        ),
        (
            MemoryPolicy(memory_mode="normal", source_authority="long_term_disabled"),
            "source_authority_disables_long_term_memory",
        ),
    ),
)
def test_bridge_blocks_recall_before_calls_when_policy_disallows_memory(
    policy: MemoryPolicy,
    reason: str,
) -> None:
    from openmagi_core_agent.memory.adk_bridge import (
        ADKMemoryBridgeConfig,
        ADKMemoryServiceBridge,
    )

    provider = FakeProvider()
    adk_service = FakeADKMemoryService()
    bridge = ADKMemoryServiceBridge(
        ADKMemoryBridgeConfig(
            enabled=True,
            local_fake_provider_enabled=True,
            local_fake_adk_service_enabled=True,
        ),
        provider=provider,
        adk_memory_service=adk_service,
    )

    outcome = asyncio.run(bridge.recall(recall_request(), policy=policy))

    assert provider.calls == 0
    assert adk_service.search_calls == []
    assert outcome.result.records == ()
    assert outcome.result.recall_allowed is False
    assert reason in outcome.result.reason_codes
    assert outcome.diagnostic_metadata["provider_called"] is False
    assert outcome.diagnostic_metadata["adk_service_called"] is False


def test_bridge_local_fake_adk_memory_service_path_converts_memory_entries_safely() -> None:
    from openmagi_core_agent.memory.adk_bridge import (
        ADKMemoryBridgeConfig,
        ADKMemoryServiceBridge,
    )

    adk_service = FakeADKMemoryService()
    bridge = ADKMemoryServiceBridge(
        ADKMemoryBridgeConfig(
            enabled=True,
            local_fake_adk_service_enabled=True,
            app_name="openmagi-test",
            user_id="user-1",
        ),
        adk_memory_service=adk_service,
    )

    outcome = asyncio.run(
        bridge.recall(
            recall_request(),
            policy=MemoryPolicy(memory_mode="normal", source_authority="long_term_allowed"),
        )
    )

    rendered_projection = json.dumps(outcome.public_projection(), sort_keys=True)

    assert adk_service.search_calls == [("openmagi-test", "user-1", "launch plan")]
    assert adk_service.add_memory_calls == 0
    assert adk_service.add_session_calls == 0
    assert outcome.diagnostic_metadata["adk_service_called"] is True
    assert outcome.result.records[0].id == "adk-memory-1"
    assert outcome.result.records[0].provider_id == "adk-memory-service"
    assert "Launch plan memory from ADK" in outcome.result.records[0].body
    assert "Launch plan memory from ADK" not in rendered_projection
    assert "snippet" not in rendered_projection
    assert "user:pass" not in rendered_projection
    assert "api_key=unsafe" not in rendered_projection
    assert "sk-adk-secret" not in rendered_projection
    assert "raw_child_output" not in rendered_projection
    assert "/workspace/bot" not in rendered_projection
    assert "Authorization: Bearer unsafe" not in rendered_projection


def test_bridge_write_methods_stay_disabled_even_when_enabled_for_local_recall() -> None:
    from openmagi_core_agent.memory.adk_bridge import (
        ADKMemoryBridgeConfig,
        ADKMemoryServiceBridge,
    )

    adk_service = FakeADKMemoryService()
    bridge = ADKMemoryServiceBridge(
        ADKMemoryBridgeConfig(enabled=True, local_fake_adk_service_enabled=True),
        adk_memory_service=adk_service,
    )

    with pytest.raises(UnsupportedMemoryOperationError, match="disabled"):
        asyncio.run(bridge.remember({"body": "do not write"}))

    assert adk_service.add_memory_calls == 0
    assert adk_service.add_session_calls == 0


def test_bridge_import_boundary_allows_only_adk_memory_primitives() -> None:
    module = importlib.import_module("openmagi_core_agent.memory.adk_bridge")
    source = Path(module.__file__).read_text(encoding="utf-8")
    parsed = ast.parse(source)

    imported_modules: set[str] = set()
    for node in ast.walk(parsed):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported_modules.add(node.module)

    forbidden_prefixes = (
        "google.adk",
        "google.genai",
        "google.adk.runners",
        "google.adk.sessions",
        "google.adk.tools",
        "openmagi_core_agent.adk_bridge.local_runner",
        "openmagi_core_agent.adk_bridge.runner_adapter",
        "openmagi_core_agent.adk_bridge.tool_adapter",
        "openmagi_core_agent.adk_bridge.local_toolhost",
        "openmagi_core_agent.transport",
        "openmagi_core_agent.routes",
        "openmagi_core_agent.plugins.agentmemory",
        "openmagi_core_agent.services.memory",
        "openmagi_core_agent.hipocampus",
        "openmagi_core_agent.qmd",
        "subprocess",
        "socket",
        "http",
        "httpx",
        "requests",
        "urllib",
        "openai",
        "anthropic",
        "google.cloud",
    )
    forbidden = sorted(
        name
        for name in imported_modules
        if any(name == prefix or name.startswith(f"{prefix}.") for prefix in forbidden_prefixes)
    )
    assert forbidden == []
    forbidden_source_fragments = (
        "__import__(",
        "importlib.import_module",
        "google.adk.memory",
        "google.adk.runners",
        "google.adk.sessions",
        "google.adk.tools",
        "socket.",
        "urllib.",
        "http.client",
        "requests.",
        "httpx.",
    )
    for fragment in forbidden_source_fragments:
        assert fragment not in source


class PublicSafeProvider:
    openmagi_local_fake_provider = True

    async def recall(
        self,
        request: RecallRequest,
        *,
        policy: MemoryPolicy,
    ) -> RecallResult:
        return RecallResult(
            provider_id="fake-public-safe",
            records=(
                MemoryRecord(
                    id="public-safe-record",
                    scope="bot",
                    kind="note",
                    body=(
                        "Safe public summary.\n"
                        "<tool_log>\nCookie: session=unsafe\nraw tool detail\n</tool_log>\n"
                        "<child_prompt>\nraw child prompt\n</child_prompt>\n"
                        "Telegram token https://api.telegram.org/bot123:ABC/sendMessage"
                        "\nAWS_ACCESS_KEY_ID=AKIAUNSAFEKEY"
                        "\ns3://private-bucket/object?X-Amz-Signature=unsafe"
                        "\nhttps://storage.googleapis.com/private-bucket/object"
                    ),
                    source_ref="https://storage.googleapis.com/private-bucket/object",
                    provider_id="fake-public-safe",
                    confidence="observed",
                    visibility="public-safe",
                ),
            ),
            recall_allowed=True,
            write_allowed=False,
            prompt_projection_allowed=False,
            public_projection_allowed=True,
            reason_codes=("provider_fixture",),
        )


def test_bridge_public_safe_records_strip_xml_tool_child_cookie_and_telegram_tokens() -> None:
    from openmagi_core_agent.memory.adk_bridge import (
        ADKMemoryBridgeConfig,
        ADKMemoryServiceBridge,
    )

    bridge = ADKMemoryServiceBridge(
        ADKMemoryBridgeConfig(enabled=True, local_fake_provider_enabled=True),
        provider=PublicSafeProvider(),
    )

    outcome = asyncio.run(
        bridge.recall(
            recall_request(),
            policy=MemoryPolicy(memory_mode="normal", source_authority="long_term_allowed"),
        )
    )

    rendered_projection = json.dumps(outcome.public_projection(), sort_keys=True)

    assert "Safe public summary" in rendered_projection
    assert "<tool_log>" not in rendered_projection
    assert "Cookie:" not in rendered_projection
    assert "session=unsafe" not in rendered_projection
    assert "raw child prompt" not in rendered_projection
    assert "api.telegram.org" not in rendered_projection
    assert "bot123:ABC" not in rendered_projection
    assert "AWS_ACCESS_KEY_ID" not in outcome.public_projection()["records"][0]["snippet"]
    assert "AKIAUNSAFEKEY" not in rendered_projection
    assert "s3://private-bucket" not in rendered_projection
    assert "X-Amz-Signature" not in rendered_projection
    assert "storage.googleapis.com" not in rendered_projection
    assert "private-bucket" not in rendered_projection
    assert outcome.public_projection()["records"][0]["sourceRef"].startswith("memory:")


class StandaloneUrlPayloadProvider:
    openmagi_local_fake_provider = True

    def __init__(self, body: str) -> None:
        self.body = body

    async def recall(
        self,
        request: RecallRequest,
        *,
        policy: MemoryPolicy,
    ) -> RecallResult:
        return RecallResult(
            provider_id="standalone-url-provider",
            records=(
                MemoryRecord(
                    id="standalone-url-record",
                    scope="bot",
                    kind="note",
                    body=self.body,
                    source_ref="memory/public.md",
                    provider_id="standalone-url-provider",
                    confidence="observed",
                    visibility="public-safe",
                ),
            ),
            recall_allowed=True,
            write_allowed=False,
            prompt_projection_allowed=False,
            public_projection_allowed=True,
            reason_codes=("provider_fixture",),
        )


def test_bridge_public_projection_redacts_standalone_url_payload_blocks() -> None:
    from openmagi_core_agent.memory.adk_bridge import (
        ADKMemoryBridgeConfig,
        ADKMemoryServiceBridge,
    )

    bodies = (
        (
            "Safe public summary.\n"
            "https://api.telegram.org/bot123:ABC/sendMessage\n"
            "TELEGRAM_PAYLOAD_DO_NOT_LEAK"
        ),
        (
            "Safe public summary.\n"
            "https://storage.googleapis.com/private-bucket/object\n"
            "OBJECT_PAYLOAD_DO_NOT_LEAK"
        ),
    )

    for body in bodies:
        bridge = ADKMemoryServiceBridge(
            ADKMemoryBridgeConfig(enabled=True, local_fake_provider_enabled=True),
            provider=StandaloneUrlPayloadProvider(body),
        )
        outcome = asyncio.run(
            bridge.recall(
                recall_request(),
                policy=MemoryPolicy(memory_mode="normal", source_authority="long_term_allowed"),
            )
        )
        encoded = json.dumps(outcome.public_projection(), sort_keys=True)

        assert "Safe public summary" in encoded
        assert "api.telegram.org" not in encoded
        assert "bot123:ABC" not in encoded
        assert "storage.googleapis.com" not in encoded
        assert "private-bucket" not in encoded
        assert "TELEGRAM_PAYLOAD_DO_NOT_LEAK" not in encoded
        assert "OBJECT_PAYLOAD_DO_NOT_LEAK" not in encoded


class PublicSafePathProvider:
    openmagi_local_fake_provider = True

    async def recall(
        self,
        request: RecallRequest,
        *,
        policy: MemoryPolicy,
    ) -> RecallResult:
        return RecallResult(
            provider_id="fake-public-safe-paths",
            records=(
                MemoryRecord(
                    id="public-safe-path-record",
                    scope="bot",
                    kind="note",
                    body=(
                        "Safe public summary.\n"
                        "/home/kevin/.ssh/id_rsa\n"
                        "/var/lib/kubelet\n"
                        "/var/lib/kubelet/pods/x/token"
                    ),
                    source_ref="/home/kevin/.ssh/id_rsa",
                    provider_id="fake-public-safe-paths",
                    confidence="observed",
                    visibility="public-safe",
                ),
            ),
            recall_allowed=True,
            write_allowed=False,
            prompt_projection_allowed=False,
            public_projection_allowed=True,
            reason_codes=("provider_fixture",),
        )


def test_bridge_public_projection_redacts_home_and_exact_kubelet_paths() -> None:
    from openmagi_core_agent.memory.adk_bridge import (
        ADKMemoryBridgeConfig,
        ADKMemoryServiceBridge,
    )

    bridge = ADKMemoryServiceBridge(
        ADKMemoryBridgeConfig(enabled=True, local_fake_provider_enabled=True),
        provider=PublicSafePathProvider(),
    )

    outcome = asyncio.run(
        bridge.recall(
            recall_request(),
            policy=MemoryPolicy(memory_mode="normal", source_authority="long_term_allowed"),
        )
    )
    projection = outcome.public_projection()
    encoded = json.dumps(projection, sort_keys=True)

    assert "Safe public summary" in encoded
    assert "/home/kevin" not in encoded
    assert "/var/lib/kubelet" not in encoded
    assert projection["records"][0]["sourceRef"].startswith("memory:")


class PathShapedIdProvider:
    openmagi_local_fake_provider = True

    async def recall(
        self,
        request: RecallRequest,
        *,
        policy: MemoryPolicy,
    ) -> RecallResult:
        return RecallResult(
            provider_id="fake-path-shaped-id",
            records=(
                MemoryRecord(
                    id="/home/kevin/.ssh/id_rsa",
                    scope="bot",
                    kind="note",
                    body="Safe public summary.",
                    source_ref="memory/public.md",
                    provider_id="fake-path-shaped-id",
                    confidence="observed",
                    visibility="public-safe",
                ),
            ),
            recall_allowed=True,
            write_allowed=False,
            prompt_projection_allowed=False,
            public_projection_allowed=True,
            reason_codes=("provider_fixture",),
        )


def test_bridge_sanitizes_path_shaped_record_ids_before_result_and_public_projection() -> None:
    from openmagi_core_agent.memory.adk_bridge import (
        ADKMemoryBridgeConfig,
        ADKMemoryServiceBridge,
    )

    bridge = ADKMemoryServiceBridge(
        ADKMemoryBridgeConfig(enabled=True, local_fake_provider_enabled=True),
        provider=PathShapedIdProvider(),
    )

    outcome = asyncio.run(
        bridge.recall(
            recall_request(),
            policy=MemoryPolicy(memory_mode="normal", source_authority="long_term_allowed"),
        )
    )
    encoded = json.dumps(outcome.public_projection(), sort_keys=True)

    assert outcome.result.records[0].id.startswith("memory:")
    assert outcome.public_projection()["records"][0]["id"].startswith("memory:")
    assert "/home/kevin" not in encoded
    assert ".ssh" not in encoded


class SecretShapedProviderIdProvider:
    openmagi_local_fake_provider = True

    async def recall(
        self,
        request: RecallRequest,
        *,
        policy: MemoryPolicy,
    ) -> RecallResult:
        return RecallResult(
            provider_id="sk-live-secretprovider12345",
            records=(
                MemoryRecord(
                    id="safe-record",
                    scope="bot",
                    kind="note",
                    body="Safe public summary.",
                    source_ref="memory/public.md",
                    provider_id="/home/kevin/provider-token",
                    confidence="observed",
                    visibility="public-safe",
                ),
            ),
            recall_allowed=True,
            write_allowed=False,
            prompt_projection_allowed=False,
            public_projection_allowed=True,
            reason_codes=("provider_fixture",),
        )


def test_bridge_public_projection_sanitizes_secret_and_path_shaped_provider_ids() -> None:
    from openmagi_core_agent.memory.adk_bridge import (
        ADKMemoryBridgeConfig,
        ADKMemoryServiceBridge,
    )

    bridge = ADKMemoryServiceBridge(
        ADKMemoryBridgeConfig(
            enabled=True,
            local_fake_provider_enabled=True,
            provider_id="sk-live-secretbridge12345",
        ),
        provider=SecretShapedProviderIdProvider(),
    )

    outcome = asyncio.run(
        bridge.recall(
            recall_request(),
            policy=MemoryPolicy(memory_mode="normal", source_authority="long_term_allowed"),
        )
    )
    projection = outcome.public_projection()
    encoded = json.dumps(projection, sort_keys=True)

    assert projection["providerId"].startswith("provider:")
    assert projection["records"][0]["providerId"].startswith("provider:")
    assert "sk-live-secretprovider12345" not in encoded
    assert "sk-live-secretbridge12345" not in encoded
    assert "/home/kevin" not in encoded


class RawChildToolIdentifierProvider:
    openmagi_local_fake_provider = True

    async def recall(
        self,
        request: RecallRequest,
        *,
        policy: MemoryPolicy,
    ) -> RecallResult:
        return RecallResult(
            provider_id="raw_child_transcript: hidden",
            records=(
                MemoryRecord(
                    id="<tool_log>secret</tool_log>",
                    scope="bot",
                    kind="note",
                    body=(
                        "Safe public summary.\n"
                        "<tool_log>secret</tool_log>\n"
                        "raw_subagent_transcript_secret: private transcript\n"
                        "tool log: internal command output\n"
                        "child prompt: private instruction\n"
                        "hidden reasoning: private trace\n"
                        "private_memory: diary secret\n"
                        "raw_subagent_transcript_secret:\n"
                        "TRANSCRIPT_PAYLOAD_DO_NOT_LEAK\n"
                        "private_reasoning:\n"
                        "COT_PAYLOAD_DO_NOT_LEAK\n"
                        "private_reasoning:\n"
                        "\n"
                        "BLANK_LINE_COT_PAYLOAD_DO_NOT_LEAK\n"
                        "raw_subagent_transcript_secret:\n"
                        "MULTILINE_PAYLOAD_LINE_ONE_DO_NOT_LEAK\n"
                        "MULTILINE_PAYLOAD_LINE_TWO_DO_NOT_LEAK"
                    ),
                    source_ref="private_memory_note",
                    provider_id="private-memory-note",
                    confidence="observed",
                    visibility="public-safe",
                ),
            ),
            recall_allowed=True,
            write_allowed=False,
            prompt_projection_allowed=False,
            public_projection_allowed=True,
            reason_codes=("provider_fixture",),
        )


def test_bridge_public_projection_sanitizes_raw_child_and_tool_identifier_shapes() -> None:
    from openmagi_core_agent.memory.adk_bridge import (
        ADKMemoryBridgeConfig,
        ADKMemoryServiceBridge,
    )

    bridge = ADKMemoryServiceBridge(
        ADKMemoryBridgeConfig(
            enabled=True,
            local_fake_provider_enabled=True,
            provider_id="private_memory_note",
        ),
        provider=RawChildToolIdentifierProvider(),
    )

    outcome = asyncio.run(
        bridge.recall(
            recall_request(),
            policy=MemoryPolicy(memory_mode="normal", source_authority="long_term_allowed"),
        )
    )
    projection = outcome.public_projection()
    encoded = json.dumps(projection, sort_keys=True)

    assert projection["providerId"].startswith("provider:")
    assert projection["records"][0]["id"].startswith("memory:")
    assert projection["records"][0]["sourceRef"].startswith("memory:")
    assert projection["records"][0]["providerId"].startswith("provider:")
    assert "<tool_log>" not in encoded
    assert "raw_child_transcript" not in encoded
    assert "raw_subagent_transcript" not in encoded
    assert "tool log" not in encoded
    assert "child prompt" not in encoded
    assert "hidden reasoning" not in encoded
    assert "private_memory" not in encoded
    assert "private-memory" not in encoded
    assert "diary secret" not in encoded
    assert "TRANSCRIPT_PAYLOAD_DO_NOT_LEAK" not in encoded
    assert "COT_PAYLOAD_DO_NOT_LEAK" not in encoded
    assert "BLANK_LINE_COT_PAYLOAD_DO_NOT_LEAK" not in encoded
    assert "MULTILINE_PAYLOAD_LINE_ONE_DO_NOT_LEAK" not in encoded
    assert "MULTILINE_PAYLOAD_LINE_TWO_DO_NOT_LEAK" not in encoded
    assert "hidden" not in encoded


class PathShapedIdADKMemoryService(FakeADKMemoryService):
    def search_memory(
        self,
        *,
        app_name: str,
        user_id: str,
        query: str,
    ) -> SearchMemoryResponse:
        self.search_calls.append((app_name, user_id, query))
        return SearchMemoryResponse(
            memories=[
                MemoryEntry(
                    id="/var/lib/kubelet/pods/x/token",
                    author="hipocampus",
                    timestamp="2026-05-20T00:00:00Z",
                    content=types.Content(
                        role="model",
                        parts=[types.Part(text="Safe public summary.")],
                    ),
                    custom_metadata={
                        "sourceRef": "memory/public.md",
                        "visibility": "public-safe",
                    },
                )
            ]
        )


def test_bridge_sanitizes_path_shaped_adk_memory_entry_ids() -> None:
    from openmagi_core_agent.memory.adk_bridge import (
        ADKMemoryBridgeConfig,
        ADKMemoryServiceBridge,
    )

    adk_service = PathShapedIdADKMemoryService()
    bridge = ADKMemoryServiceBridge(
        ADKMemoryBridgeConfig(enabled=True, local_fake_adk_service_enabled=True),
        adk_memory_service=adk_service,
    )

    outcome = asyncio.run(
        bridge.recall(
            recall_request(),
            policy=MemoryPolicy(memory_mode="normal", source_authority="long_term_allowed"),
        )
    )
    encoded = json.dumps(outcome.public_projection(), sort_keys=True)

    assert outcome.result.records[0].id.startswith("memory:")
    assert outcome.public_projection()["records"][0]["id"].startswith("memory:")
    assert "/var/lib/kubelet" not in encoded


def test_bridge_public_projection_sanitizes_diagnostic_metadata() -> None:
    from openmagi_core_agent.memory.adk_bridge import ADKMemoryBridgeRecallOutcome

    outcome = ADKMemoryBridgeRecallOutcome(
        result=RecallResult(
            provider_id="safe-provider",
            records=(),
            recall_allowed=False,
            write_allowed=False,
            prompt_projection_allowed=False,
            public_projection_allowed=False,
            reason_codes=(
                "provider_fixture",
                "raw_child_transcript /Users/kevin/private Cookie: session=unsafe",
                "raw_subagent_transcript_secret",
                "private_memory:secret",
            ),
        ),
        diagnostic_metadata={
            "provider_error": "Authorization: Bearer unsafe at /home/kevin/private.txt",
            "nested": {"cookie": "session=unsafe", "safe": "kept"},
            "items": [
                "sk-live-secretprovider12345",
                "safe item",
                "raw_child_transcript data",
                "raw_subagent_transcript_secret",
                "raw_tool_args data",
                "<tool_log>secret</tool_log>",
                "tool log: internal command output",
                "child prompt: private instruction",
                "hidden reasoning: private trace",
                "private_memory: diary secret",
                "private-memory-note",
                "raw_subagent_transcript_secret:\nTRANSCRIPT_PAYLOAD_DO_NOT_LEAK",
                "private_reasoning:\nCOT_PAYLOAD_DO_NOT_LEAK",
                "https://api.telegram.org/bot123:ABC/sendMessage\nTELEGRAM_DIAGNOSTIC_PAYLOAD_DO_NOT_LEAK",
                "https://storage.googleapis.com/private-bucket/object\nOBJECT_DIAGNOSTIC_PAYLOAD_DO_NOT_LEAK",
            ],
        },
    )

    encoded = json.dumps(outcome.public_projection(), sort_keys=True)

    assert "unsafe" not in encoded
    assert "Bearer" not in encoded
    assert "/home/kevin" not in encoded
    assert "sk-live-secretprovider12345" not in encoded
    assert "raw_child_transcript" not in encoded
    assert "raw_subagent_transcript" not in encoded
    assert "raw_tool_args" not in encoded
    assert "<tool_log>" not in encoded
    assert "secret" not in encoded
    assert "tool log" not in encoded
    assert "child prompt" not in encoded
    assert "hidden reasoning" not in encoded
    assert "private_memory" not in encoded
    assert "private-memory" not in encoded
    assert "diary secret" not in encoded
    assert "TRANSCRIPT_PAYLOAD_DO_NOT_LEAK" not in encoded
    assert "COT_PAYLOAD_DO_NOT_LEAK" not in encoded
    assert "api.telegram.org" not in encoded
    assert "bot123:ABC" not in encoded
    assert "storage.googleapis.com" not in encoded
    assert "private-bucket" not in encoded
    assert "TELEGRAM_DIAGNOSTIC_PAYLOAD_DO_NOT_LEAK" not in encoded
    assert "OBJECT_DIAGNOSTIC_PAYLOAD_DO_NOT_LEAK" not in encoded
    assert "raw_child_transcript /Users" not in encoded
    assert "provider_fixture" in encoded
    assert "kept" in encoded
    assert "safe item" in encoded


def test_bridge_rejects_unmarked_local_fake_provider_and_adk_service() -> None:
    from openmagi_core_agent.memory.adk_bridge import (
        ADKMemoryBridgeConfig,
        ADKMemoryServiceBridge,
    )

    class UnmarkedProvider(FakeProvider):
        openmagi_local_fake_provider = False

    class UnmarkedADKMemoryService(FakeADKMemoryService):
        openmagi_local_fake_provider = False

    provider = UnmarkedProvider()
    adk_service = UnmarkedADKMemoryService()

    provider_outcome = asyncio.run(
        ADKMemoryServiceBridge(
            ADKMemoryBridgeConfig(enabled=True, local_fake_provider_enabled=True),
            provider=provider,
        ).recall(
            recall_request(),
            policy=MemoryPolicy(memory_mode="normal", source_authority="long_term_allowed"),
        )
    )
    adk_outcome = asyncio.run(
        ADKMemoryServiceBridge(
            ADKMemoryBridgeConfig(enabled=True, local_fake_adk_service_enabled=True),
            adk_memory_service=adk_service,
        ).recall(
            recall_request(),
            policy=MemoryPolicy(memory_mode="normal", source_authority="long_term_allowed"),
        )
    )

    assert provider_outcome.result.records == ()
    assert "local_fake_memory_provider_untrusted" in provider_outcome.result.reason_codes
    assert provider.calls == 0
    assert adk_outcome.result.records == ()
    assert "local_fake_adk_memory_service_untrusted" in adk_outcome.result.reason_codes
    assert adk_service.search_calls == []


def test_bridge_provider_records_honor_request_max_bytes_before_projection() -> None:
    from openmagi_core_agent.memory.adk_bridge import (
        ADKMemoryBridgeConfig,
        ADKMemoryServiceBridge,
    )

    provider = FakeProvider()
    bridge = ADKMemoryServiceBridge(
        ADKMemoryBridgeConfig(enabled=True, local_fake_provider_enabled=True),
        provider=provider,
    )

    request = recall_request().model_copy(update={"max_bytes": 20})
    outcome = asyncio.run(
        bridge.recall(
            request,
            policy=MemoryPolicy(memory_mode="normal", source_authority="long_term_allowed"),
        )
    )

    assert len(outcome.result.records[0].body.encode("utf-8")) <= 20
    assert "Launch decision" not in json.dumps(outcome.public_projection(), sort_keys=True)
