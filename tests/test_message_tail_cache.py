"""Tests for message-level (conversation tail) prompt caching — PR11.

OpenCode marks the last ~2 non-system conversation messages with an Anthropic
``cache_control: {type: ephemeral}`` marker so the growing conversation tail is
cached (not just the system prefix), cutting per-turn input cost.

This PR extends the EXISTING ``magi_agent.prompt`` infra rather than adding a
new pack:

- ``CacheControlInjector.mark_message_tail`` marks the last N non-system
  messages (default 2) for Anthropic only; OpenAI/Google auto-cache prefixes
  so it is a no-op there.
- ``config.env.is_message_cache_enabled`` reads ``MAGI_MESSAGE_CACHE_ENABLED``
  (default OFF) as the single source of truth.
- ``runtime.prompt_snapshot.message_tail_fingerprint`` produces a stable
  fingerprint that EXCLUDES ``cache_control`` markers so the fork-snapshot
  fingerprint is not destabilised by rolling-tail markers.

TDD: tests written before the implementation.
"""

from __future__ import annotations

import importlib
from types import ModuleType

import pytest


def _injection_module() -> ModuleType:
    return importlib.import_module("magi_agent.prompt.injection")


def _env_module() -> ModuleType:
    return importlib.import_module("magi_agent.config.env")


def _snapshot_module() -> ModuleType:
    return importlib.import_module("magi_agent.runtime.prompt_snapshot")


def _msg(role: str, text: str) -> dict:
    return {"role": role, "content": [{"type": "text", "text": text}]}


def _has_cache_control(message: dict) -> bool:
    content = message.get("content")
    if isinstance(content, list):
        return any(
            isinstance(block, dict) and "cache_control" in block for block in content
        )
    return "cache_control" in message


def _count_breakpoints(messages: list[dict]) -> int:
    total = 0
    for message in messages:
        content = message.get("content")
        if isinstance(content, list):
            total += sum(
                1
                for block in content
                if isinstance(block, dict) and "cache_control" in block
            )
        elif "cache_control" in message:
            total += 1
    return total


# ---------------------------------------------------------------------------
# CacheControlInjector.mark_message_tail — Anthropic
# ---------------------------------------------------------------------------


class TestMarkMessageTailAnthropic:
    def test_marks_last_two_non_system_messages(self) -> None:
        injector = _injection_module().CacheControlInjector(provider="anthropic")
        messages = [
            _msg("user", "first"),
            _msg("assistant", "second"),
            _msg("user", "third"),
            _msg("assistant", "fourth"),
        ]
        marked = injector.mark_message_tail(messages)

        assert not _has_cache_control(marked[0])
        assert not _has_cache_control(marked[1])
        assert _has_cache_control(marked[2])
        assert _has_cache_control(marked[3])

    def test_marker_is_ephemeral(self) -> None:
        injector = _injection_module().CacheControlInjector(provider="anthropic")
        messages = [_msg("user", "only")]
        marked = injector.mark_message_tail(messages)
        block = marked[-1]["content"][-1]
        assert block["cache_control"] == {"type": "ephemeral"}

    def test_does_not_mark_system_messages(self) -> None:
        injector = _injection_module().CacheControlInjector(provider="anthropic")
        messages = [
            {"role": "system", "content": [{"type": "text", "text": "sys"}]},
            _msg("user", "hi"),
            _msg("assistant", "yo"),
        ]
        marked = injector.mark_message_tail(messages)
        assert not _has_cache_control(marked[0])
        assert _has_cache_control(marked[1])
        assert _has_cache_control(marked[2])

    def test_string_content_message_is_marked(self) -> None:
        injector = _injection_module().CacheControlInjector(provider="anthropic")
        messages = [{"role": "user", "content": "plain string"}]
        marked = injector.mark_message_tail(messages)
        assert _has_cache_control(marked[-1])

    def test_does_not_mutate_input(self) -> None:
        injector = _injection_module().CacheControlInjector(provider="anthropic")
        messages = [_msg("user", "a"), _msg("assistant", "b")]
        injector.mark_message_tail(messages)
        assert not _has_cache_control(messages[0])
        assert not _has_cache_control(messages[1])

    def test_fewer_than_tail_size_marks_all_available(self) -> None:
        injector = _injection_module().CacheControlInjector(provider="anthropic")
        messages = [_msg("user", "only")]
        marked = injector.mark_message_tail(messages)
        assert _has_cache_control(marked[0])
        assert _count_breakpoints(marked) == 1

    def test_empty_messages_returns_empty(self) -> None:
        injector = _injection_module().CacheControlInjector(provider="anthropic")
        assert injector.mark_message_tail([]) == []


# ---------------------------------------------------------------------------
# Breakpoint budget — never exceed Anthropic's 4-breakpoint limit
# ---------------------------------------------------------------------------


class TestBreakpointBudget:
    def test_default_tail_uses_at_most_two_breakpoints(self) -> None:
        injector = _injection_module().CacheControlInjector(provider="anthropic")
        messages = [_msg("user" if i % 2 == 0 else "assistant", str(i)) for i in range(10)]
        marked = injector.mark_message_tail(messages)
        assert _count_breakpoints(marked) <= 2

    def test_combined_with_system_breakpoints_stays_within_four(self) -> None:
        injector = _injection_module().CacheControlInjector(provider="anthropic")
        # System already used up to 2 breakpoints; tail must not exceed 2 more.
        system_breakpoints = 2
        messages = [_msg("user", str(i)) for i in range(6)]
        marked = injector.mark_message_tail(messages, tail_size=2)
        assert system_breakpoints + _count_breakpoints(marked) <= 4

    def test_tail_size_capped_to_remaining_budget(self) -> None:
        injector = _injection_module().CacheControlInjector(provider="anthropic")
        messages = [_msg("user", str(i)) for i in range(10)]
        # Even if a caller asks for more, never exceed the 4-breakpoint ceiling
        # (system reserves up to 2, so tail is capped at 2).
        marked = injector.mark_message_tail(messages, tail_size=10)
        assert _count_breakpoints(marked) <= 2


# ---------------------------------------------------------------------------
# Non-Anthropic providers — no-op (auto-cache prefixes)
# ---------------------------------------------------------------------------


class TestNonAnthropicNoOp:
    def test_openai_is_noop(self) -> None:
        injector = _injection_module().CacheControlInjector(provider="openai")
        messages = [_msg("user", "a"), _msg("assistant", "b")]
        marked = injector.mark_message_tail(messages)
        assert _count_breakpoints(marked) == 0

    def test_google_is_noop(self) -> None:
        injector = _injection_module().CacheControlInjector(provider="google")
        messages = [_msg("user", "a"), _msg("assistant", "b")]
        marked = injector.mark_message_tail(messages)
        assert _count_breakpoints(marked) == 0

    def test_auto_detect_gemini_is_noop(self) -> None:
        injector = _injection_module().CacheControlInjector(
            provider="auto", model="gemini-2.0-flash"
        )
        messages = [_msg("user", "a"), _msg("assistant", "b")]
        marked = injector.mark_message_tail(messages)
        assert _count_breakpoints(marked) == 0

    def test_auto_detect_claude_marks(self) -> None:
        injector = _injection_module().CacheControlInjector(
            provider="auto", model="claude-sonnet-4-6"
        )
        messages = [_msg("user", "a"), _msg("assistant", "b")]
        marked = injector.mark_message_tail(messages)
        assert _count_breakpoints(marked) == 2


# ---------------------------------------------------------------------------
# Flag — config.env.is_message_cache_enabled (MAGI_MESSAGE_CACHE_ENABLED)
# ---------------------------------------------------------------------------


class TestMessageCacheFlag:
    def test_default_off(self) -> None:
        is_enabled = _env_module().is_message_cache_enabled
        assert is_enabled({}) is False

    def test_explicit_off(self) -> None:
        is_enabled = _env_module().is_message_cache_enabled
        assert is_enabled({"MAGI_MESSAGE_CACHE_ENABLED": "0"}) is False

    @pytest.mark.parametrize("value", ["1", "true", "yes", "on", "True", "YES"])
    def test_truthy_values_enable(self, value: str) -> None:
        is_enabled = _env_module().is_message_cache_enabled
        assert is_enabled({"MAGI_MESSAGE_CACHE_ENABLED": value}) is True

    def test_reads_process_env_when_no_mapping(self, monkeypatch) -> None:
        is_enabled = _env_module().is_message_cache_enabled
        monkeypatch.setenv("MAGI_MESSAGE_CACHE_ENABLED", "1")
        assert is_enabled() is True
        monkeypatch.delenv("MAGI_MESSAGE_CACHE_ENABLED", raising=False)
        assert is_enabled() is False


# ---------------------------------------------------------------------------
# Fingerprint stability — markers must not destabilise the fork fingerprint
# ---------------------------------------------------------------------------


class TestFingerprintStability:
    def test_tail_markers_do_not_change_fingerprint(self) -> None:
        snapshot = _snapshot_module()
        injector = _injection_module().CacheControlInjector(provider="anthropic")
        messages = [_msg("user", "a"), _msg("assistant", "b"), _msg("user", "c")]
        marked = injector.mark_message_tail(messages)

        fp_before = snapshot.message_tail_fingerprint(messages)
        fp_after = snapshot.message_tail_fingerprint(marked)
        assert fp_before == fp_after

    def test_fingerprint_changes_when_text_changes(self) -> None:
        snapshot = _snapshot_module()
        a = [_msg("user", "a"), _msg("assistant", "b")]
        b = [_msg("user", "a"), _msg("assistant", "DIFFERENT")]
        assert snapshot.message_tail_fingerprint(a) != snapshot.message_tail_fingerprint(b)

    def test_frozen_prompt_snapshot_capture_ignores_tail_markers(self) -> None:
        # System-block snapshot fingerprint must be unaffected by message-tail
        # markers because the tail markers live on conversation messages, not
        # system blocks. This guards rule 4 (fingerprint stability).
        snapshot = _snapshot_module()
        system_blocks = [
            {"type": "text", "text": "identity", "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": "dynamic"},
        ]
        snap = snapshot.FrozenPromptSnapshot.capture(system_blocks)
        assert snap.restore() == system_blocks
