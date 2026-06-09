from __future__ import annotations

from magi_agent.cli.tool_runtime import build_cli_instruction
from magi_agent.runtime.memory_mode_context import (
    MAGI_MEMORY_MODE_ROUTING_ENABLED_ENV,
    current_memory_mode,
    memory_mode_request_scope,
    memory_mode_routing_enabled,
)
from magi_agent.runtime.message_builder import (
    INCOGNITO_MEMORY_MODE_BLOCK,
    READ_ONLY_MEMORY_MODE_BLOCK,
)
from magi_agent.runtime.session_identity import MemoryMode


def test_routing_disabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv(MAGI_MEMORY_MODE_ROUTING_ENABLED_ENV, raising=False)
    assert memory_mode_routing_enabled() is False


def test_scope_is_noop_when_gate_off(monkeypatch) -> None:
    monkeypatch.delenv(MAGI_MEMORY_MODE_ROUTING_ENABLED_ENV, raising=False)
    with memory_mode_request_scope({"x-core-agent-memory-mode": "incognito"}):
        assert current_memory_mode() is MemoryMode.NORMAL
    assert current_memory_mode() is MemoryMode.NORMAL


def test_scope_reads_header_when_gate_on(monkeypatch) -> None:
    monkeypatch.setenv(MAGI_MEMORY_MODE_ROUTING_ENABLED_ENV, "1")
    with memory_mode_request_scope({"x-core-agent-memory-mode": "incognito"}):
        assert current_memory_mode() is MemoryMode.INCOGNITO
    assert current_memory_mode() is MemoryMode.NORMAL

    with memory_mode_request_scope({"x-core-agent-memory-mode": "read_only"}):
        assert current_memory_mode() is MemoryMode.READ_ONLY
    assert current_memory_mode() is MemoryMode.NORMAL


def test_scope_missing_header_is_normal_when_gate_on(monkeypatch) -> None:
    monkeypatch.setenv(MAGI_MEMORY_MODE_ROUTING_ENABLED_ENV, "1")
    with memory_mode_request_scope({}):
        assert current_memory_mode() is MemoryMode.NORMAL
    assert current_memory_mode() is MemoryMode.NORMAL


def test_build_cli_instruction_injects_incognito_block() -> None:
    prompt = build_cli_instruction(session_id="s", memory_mode=MemoryMode.INCOGNITO)
    assert "memory_mode: incognito" in prompt
    assert INCOGNITO_MEMORY_MODE_BLOCK in prompt
    assert READ_ONLY_MEMORY_MODE_BLOCK not in prompt


def test_build_cli_instruction_injects_read_only_block_from_str() -> None:
    prompt = build_cli_instruction(session_id="s", memory_mode="read_only")
    assert "memory_mode: read_only" in prompt
    assert READ_ONLY_MEMORY_MODE_BLOCK in prompt
    assert INCOGNITO_MEMORY_MODE_BLOCK not in prompt


def test_build_cli_instruction_normal_injects_no_block() -> None:
    prompt = build_cli_instruction(session_id="s")
    assert "memory_mode: incognito" not in prompt
    assert "memory_mode: read_only" not in prompt
