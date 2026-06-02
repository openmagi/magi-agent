from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from magi_agent.runtime.llm_stream_reader import (
    LLMStreamReader,
    LLMStreamReaderConfig,
    LLMStreamReaderError,
)


class FakeStreamProvider:
    openmagi_local_fake_provider = True

    def __init__(self, events: list[dict[str, Any]]) -> None:
        self.events = events
        self.requests: list[dict[str, Any]] = []
        self.yielded_count = 0

    def stream(self, request: dict[str, Any]) -> Any:
        self.requests.append(request)

        async def _events() -> Any:
            for event in self.events:
                self.yielded_count += 1
                yield event

        return _events()


class AbortFlag:
    aborted = True
    reason = RuntimeError("user_interrupt_handoff")


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _enabled_reader(
    provider: FakeStreamProvider,
    *,
    preview_chars: int = 4_000,
) -> LLMStreamReader:
    return LLMStreamReader(
        provider,
        config=LLMStreamReaderConfig(
            enabled=True,
            local_fake_provider_enabled=True,
            document_preview_chars=preview_chars,
        ),
    )


def test_default_config_is_disabled_and_does_not_call_provider() -> None:
    provider = FakeStreamProvider(
        [
            {"kind": "text_delta", "blockIndex": 0, "delta": "should not stream"},
        ],
    )
    reader = LLMStreamReader(provider)

    result = _run(reader.read_one(model="test", messages=[]))

    assert provider.requests == []
    assert result.blocks == []
    assert result.public_events == []
    assert result.stop_reason is None
    assert result.skipped_reason == "disabled"


def test_enabled_reader_requires_local_fake_provider_gate_and_marker() -> None:
    class UnmarkedProvider(FakeStreamProvider):
        openmagi_local_fake_provider = False

    disabled_provider = FakeStreamProvider(
        [{"kind": "text_delta", "blockIndex": 0, "delta": "should not stream"}],
    )
    unmarked_provider = UnmarkedProvider(
        [{"kind": "text_delta", "blockIndex": 0, "delta": "should not stream"}],
    )

    disabled = _run(
        LLMStreamReader(
            disabled_provider,
            config=LLMStreamReaderConfig(enabled=True),
        ).read_one(model="test", messages=[])
    )
    untrusted = _run(
        LLMStreamReader(
            unmarked_provider,
            config=LLMStreamReaderConfig(enabled=True, local_fake_provider_enabled=True),
        ).read_one(model="test", messages=[])
    )

    assert disabled.skipped_reason == "local_fake_stream_provider_disabled"
    assert untrusted.skipped_reason == "local_fake_stream_provider_untrusted"
    assert disabled_provider.requests == []
    assert unmarked_provider.requests == []


def test_text_deltas_are_ordered_public_events_without_legacy_rendering() -> None:
    provider = FakeStreamProvider(
        [
            {"kind": "text_delta", "blockIndex": 0, "delta": "Hello "},
            {"kind": "text_delta", "blockIndex": 0, "delta": "world."},
            {"kind": "block_stop", "blockIndex": 0},
            {
                "kind": "message_end",
                "stopReason": "end_turn",
                "usage": {"inputTokens": 10, "outputTokens": 5},
            },
        ],
    )

    result = _run(_enabled_reader(provider).read_one(model="test", messages=[]))

    assert result.blocks == [{"type": "text", "text": "Hello world."}]
    assert result.stop_reason == "end_turn"
    assert result.usage == {"inputTokens": 10, "outputTokens": 5}
    assert result.public_events == [
        {"type": "text_delta", "delta": "Hello "},
        {"type": "text_delta", "delta": "world."},
    ]
    assert all(event["type"] != "legacy_delta" for event in result.public_events)


def test_preserves_thinking_signature_and_mixed_block_order() -> None:
    provider = FakeStreamProvider(
        [
            {"kind": "thinking_delta", "blockIndex": 0, "delta": "step1 "},
            {"kind": "thinking_delta", "blockIndex": 0, "delta": "step2"},
            {"kind": "thinking_signature", "blockIndex": 0, "signature": "sig-xyz"},
            {"kind": "block_stop", "blockIndex": 0},
            {"kind": "text_delta", "blockIndex": 1, "delta": "done."},
            {"kind": "block_stop", "blockIndex": 1},
            {"kind": "tool_use_start", "blockIndex": 2, "id": "tu_1", "name": "Bash"},
            {"kind": "tool_use_input_delta", "blockIndex": 2, "partial": "{\"cmd\":\"ls\"}"},
            {"kind": "block_stop", "blockIndex": 2},
            {
                "kind": "message_end",
                "stopReason": "tool_use",
                "usage": {"inputTokens": 1, "outputTokens": 1},
            },
        ],
    )

    result = _run(_enabled_reader(provider).read_one(model="test", messages=[]))

    assert result.blocks == [
        {"type": "thinking", "thinking": "step1 step2", "signature": "sig-xyz"},
        {"type": "text", "text": "done."},
        {"type": "tool_use", "id": "tu_1", "name": "Bash", "input": {"cmd": "ls"}},
    ]


def test_malformed_tool_input_is_internal_only_and_does_not_throw() -> None:
    provider = FakeStreamProvider(
        [
            {"kind": "tool_use_start", "blockIndex": 0, "id": "tu_bad", "name": "Bash"},
            {"kind": "tool_use_input_delta", "blockIndex": 0, "partial": "{not-json"},
            {"kind": "block_stop", "blockIndex": 0},
            {
                "kind": "message_end",
                "stopReason": "tool_use",
                "usage": {"inputTokens": 1, "outputTokens": 1},
            },
        ],
    )

    result = _run(_enabled_reader(provider).read_one(model="test", messages=[]))

    assert result.blocks == [
        {
            "type": "tool_use",
            "id": "tu_bad",
            "name": "Bash",
            "input": {"_malformed": True, "_raw": "{not-json"},
        },
    ]
    assert "_raw" not in repr(result.public_events)


def test_document_drafts_are_sanitized_capped_and_document_targets_only() -> None:
    long_body = "# Title\\n0123456789abcdef"
    provider = FakeStreamProvider(
        [
            {"kind": "tool_use_start", "blockIndex": 0, "id": "tu_md", "name": "FileWrite"},
            {
                "kind": "tool_use_input_delta",
                "blockIndex": 0,
                "partial": "{\"path\":\"docs/report.md\",\"content\":\"# Title\\n",
            },
            {
                "kind": "tool_use_input_delta",
                "blockIndex": 0,
                "partial": "0123456789abcdef\"}",
            },
            {"kind": "block_stop", "blockIndex": 0},
            {
                "kind": "tool_use_start",
                "blockIndex": 1,
                "id": "tu_txt",
                "name": "DocumentWrite",
            },
            {
                "kind": "tool_use_input_delta",
                "blockIndex": 1,
                "partial": (
                    "{\"filename\":\"memo.txt\",\"format\":\"txt\","
                    "\"source\":{\"text\":\"hello memo\"}}"
                ),
            },
            {"kind": "block_stop", "blockIndex": 1},
            {"kind": "tool_use_start", "blockIndex": 2, "id": "tu_json", "name": "FileWrite"},
            {
                "kind": "tool_use_input_delta",
                "blockIndex": 2,
                "partial": "{\"path\":\"data/report.json\",\"content\":\"{\\\"ok\\\":true}\"}",
            },
            {"kind": "block_stop", "blockIndex": 2},
            {
                "kind": "message_end",
                "stopReason": "tool_use",
                "usage": {"inputTokens": 1, "outputTokens": 1},
            },
        ],
    )

    result = _run(
        _enabled_reader(provider, preview_chars=12).read_one(
            model="test",
            messages=[],
        ),
    )

    drafts = [
        event for event in result.public_events if event.get("type") == "document_draft"
    ]
    assert len(drafts) >= 2
    assert drafts[-2] == {
        "type": "document_draft",
        "id": "tu_md",
        "filename": "docs/report.md",
        "format": "md",
        "contentPreview": long_body[-12:].replace("\\n", "\n"),
        "contentLength": len(long_body.replace("\\n", "\n")),
        "truncated": True,
    }
    assert drafts[-1] == {
        "type": "document_draft",
        "id": "tu_txt",
        "filename": "memo.txt",
        "format": "txt",
        "contentPreview": "hello memo",
        "contentLength": len("hello memo"),
        "truncated": False,
    }
    assert all(event.get("id") != "tu_json" for event in drafts)
    assert "_raw" not in repr(drafts)


def test_request_metadata_propagates_without_request_controlled_escalation() -> None:
    provider = FakeStreamProvider(
        [
            {
                "kind": "message_end",
                "stopReason": "end_turn",
                "usage": {"inputTokens": 1, "outputTokens": 1},
            },
        ],
    )

    result = _run(
        _enabled_reader(provider).read_one(
            model="server-selected-model",
            messages=[{"role": "user", "content": "hi"}],
            system_prompt="SYS",
            trace_id="trace-123",
            authoritative_model=True,
            routing={
                "profileId": "standard",
                "tier": "MEDIUM",
                "provider": "fireworks",
                "confidence": "classifier",
            },
            request_controlled_metadata={
                "model": "attacker-selected-model",
                "credentialRef": "sk-live-secret",
            },
        ),
    )

    assert result.provider_request == provider.requests[0]
    assert provider.requests[0] == {
        "model": "server-selected-model",
        "system": "SYS",
        "messages": [{"role": "user", "content": "hi"}],
        "traceId": "trace-123",
        "authoritativeModel": True,
        "routing": {
            "profileId": "standard",
            "tier": "MEDIUM",
            "provider": "fireworks",
            "confidence": "classifier",
        },
        "requestControlledEscalationRejected": True,
    }
    assert "tools" not in provider.requests[0]


def test_aborts_before_provider_stream_when_flag_is_already_set() -> None:
    provider = FakeStreamProvider(
        [
            {"kind": "text_delta", "blockIndex": 0, "delta": "should not stream"},
        ],
    )

    with pytest.raises(RuntimeError, match="user_interrupt_handoff"):
        _run(
            _enabled_reader(provider).read_one(
                model="test",
                messages=[],
                abort_flag=AbortFlag(),
            ),
        )

    assert provider.requests == []
    assert provider.yielded_count == 0


def test_error_events_call_on_error_and_raise_typed_exception() -> None:
    provider = FakeStreamProvider(
        [
            {"kind": "error", "code": "rate_limited", "message": "too many requests"},
        ],
    )
    calls: list[tuple[str, BaseException]] = []

    with pytest.raises(LLMStreamReaderError) as raised:
        _run(
            _enabled_reader(provider).read_one(
                model="test",
                messages=[],
                on_error=lambda code, err: calls.append((code, err)),
            ),
        )

    assert raised.value.code == "rate_limited"
    assert "too many requests" in str(raised.value)
    assert len(calls) == 1
    assert calls[0][0] == "rate_limited"


def test_repetition_detection_aborts_with_public_warning_and_forced_end_turn() -> None:
    repeated = (
        "사장님, KB에 직접 파일 업로드 기능이 없어요. "
        "document-reader 스킬로 업로드하는 것 같습니다. 확인하겠습니다."
    )
    events = [
        {"kind": "text_delta", "blockIndex": 0, "delta": repeated}
        for _ in range(10)
    ]
    events.append(
        {
            "kind": "message_end",
            "stopReason": "end_turn",
            "usage": {"inputTokens": 100, "outputTokens": 500},
        },
    )
    provider = FakeStreamProvider(events)

    result = _run(_enabled_reader(provider).read_one(model="test", messages=[]))

    assert result.stop_reason == "end_turn"
    assert provider.yielded_count < len(events)
    assert result.public_events[-1]["type"] == "warning"
    assert result.public_events[-1]["code"] == "repetition_detected"


def test_llm_stream_reader_import_boundary_is_local_fake_stream_only() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

module = importlib.import_module("magi_agent.runtime.llm_stream_reader")
assert hasattr(module, "LLMStreamReader")

forbidden_exact = (
    "google.adk.runners",
    "google.adk.agents",
    "google.adk.models",
    "openai",
    "anthropic",
    "requests",
    "httpx",
    "urllib.request",
    "http.client",
    "socket",
    "subprocess",
    "kubernetes",
    "supabase",
    "asyncpg",
    "psycopg",
    "sqlalchemy",
    "magi_agent.tools.dispatcher",
    "magi_agent.tools.tool_host",
)
forbidden_prefixes = (
    "google.adk",
    "magi_agent.adk_bridge",
    "magi_agent.tools",
    "magi_agent.transport",
    "magi_agent.channels",
    "magi_agent.db",
    "magi_agent.supabase",
    "magi_agent.deployment",
    "magi_agent.k8s",
)
loaded = [
    name
    for name in sys.modules
    if name in forbidden_exact
    or any(name.startswith(f"{exact}.") for exact in forbidden_exact)
    or any(name == prefix or name.startswith(f"{prefix}.") for prefix in forbidden_prefixes)
]
if loaded:
    raise AssertionError(f"llm_stream_reader loaded forbidden modules: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr

    root = Path(__file__).parents[1]
    source = (
        root
        / "magi_agent"
        / "runtime"
        / "llm_stream_reader.py"
    ).read_text(encoding="utf-8")
    forbidden_source_terms = (
        "google.adk",
        "openai",
        "anthropic",
        "requests",
        "httpx",
        "urllib",
        "http.client",
        "socket",
        "subprocess",
        "kubernetes",
        "kubectl",
        "supabase",
        "asyncpg",
        "psycopg",
        "sqlalchemy",
        "ToolHost",
        "ToolDispatcher",
        "APIRouter",
        "FastAPI",
    )
    for forbidden in forbidden_source_terms:
        assert forbidden not in source
