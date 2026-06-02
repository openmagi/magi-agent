from __future__ import annotations

import pytest

from openmagi_core_agent.runtime.error_recovery.types import (
    ErrorKind,
    ErrorRecoveryConfig,
    MessageDict,
    RecoverableError,
    RecoveryAttemptState,
    RecoveryContext,
)
from openmagi_core_agent.runtime.error_recovery.strategies.reactive_compact import (
    LLMCompactCaller,
    ReactiveCompactStrategy,
    StubLLMCompactCaller,
)

pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_error(kind: ErrorKind) -> RecoverableError:
    return RecoverableError(kind=kind, original_error="test error")


def _make_context(
    kind: ErrorKind,
    messages: list[MessageDict] | None = None,
) -> RecoveryContext:
    return RecoveryContext(
        error=_make_error(kind),
        messages=messages or [],
        session_key="test-session",
        turn_id="test-turn",
    )


def _default_config() -> ErrorRecoveryConfig:
    return ErrorRecoveryConfig(recovery_enabled=True)


class MockLLMCaller:
    """Mock LLM caller that returns a fixed summary."""

    def __init__(self, summary: str = "This is a mock summary.") -> None:
        self.summary = summary
        self.call_count = 0

    async def compact(self, messages_text: str, prompt: str) -> str:
        self.call_count += 1
        return self.summary


class FailingLLMCaller:
    """LLM caller that always raises."""

    async def compact(self, messages_text: str, prompt: str) -> str:
        raise RuntimeError("LLM service unavailable")


# ---------------------------------------------------------------------------
# applies_to
# ---------------------------------------------------------------------------


class TestReactiveCompactAppliesTo:
    def test_applies_to_prompt_too_long(self) -> None:
        s = ReactiveCompactStrategy(_default_config())
        assert s.applies_to(_make_error(ErrorKind.PROMPT_TOO_LONG)) is True

    def test_does_not_apply_to_rate_limit(self) -> None:
        s = ReactiveCompactStrategy(_default_config())
        assert s.applies_to(_make_error(ErrorKind.RATE_LIMIT)) is False


# ---------------------------------------------------------------------------
# Guard
# ---------------------------------------------------------------------------


class TestReactiveCompactGuard:
    async def test_rejects_when_compact_already_attempted(self) -> None:
        msgs: list[MessageDict] = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
            {"role": "user", "content": "follow up"},
        ]
        ctx = _make_context(ErrorKind.PROMPT_TOO_LONG, messages=msgs)
        state = RecoveryAttemptState(compact_attempted=True)
        s = ReactiveCompactStrategy(_default_config(), llm_caller=MockLLMCaller())
        result = await s.recover(ctx, state)
        assert result.success is False


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestReactiveCompactRecover:
    async def test_happy_path_compacts_messages(self) -> None:
        msgs: list[MessageDict] = [
            {"role": "user", "content": "first message"},
            {"role": "assistant", "content": "first reply"},
            {"role": "user", "content": "second message"},
            {"role": "assistant", "content": "second reply"},
            {"role": "user", "content": "current request"},
        ]
        mock = MockLLMCaller(summary="User discussed two topics.")
        s = ReactiveCompactStrategy(_default_config(), llm_caller=mock)
        ctx = _make_context(ErrorKind.PROMPT_TOO_LONG, messages=msgs)
        result = await s.recover(ctx)
        assert result.success is True
        assert result.modified_messages is not None
        assert len(result.modified_messages) == 2
        assert mock.call_count == 1

    async def test_summary_message_has_prefix(self) -> None:
        msgs: list[MessageDict] = [
            {"role": "user", "content": "old message"},
            {"role": "assistant", "content": "old reply"},
            {"role": "user", "content": "current"},
        ]
        mock = MockLLMCaller(summary="A summary.")
        s = ReactiveCompactStrategy(_default_config(), llm_caller=mock)
        ctx = _make_context(ErrorKind.PROMPT_TOO_LONG, messages=msgs)
        result = await s.recover(ctx)
        assert result.modified_messages is not None
        summary_msg = result.modified_messages[0]
        assert isinstance(summary_msg["content"], str)
        assert str(summary_msg["content"]).startswith("[Conversation Summary]\n")

    async def test_preserves_last_message(self) -> None:
        last: MessageDict = {"role": "user", "content": "the current request"}
        msgs: list[MessageDict] = [
            {"role": "user", "content": "old"},
            {"role": "assistant", "content": "old reply"},
            last,
        ]
        mock = MockLLMCaller()
        s = ReactiveCompactStrategy(_default_config(), llm_caller=mock)
        ctx = _make_context(ErrorKind.PROMPT_TOO_LONG, messages=msgs)
        result = await s.recover(ctx)
        assert result.modified_messages is not None
        assert result.modified_messages[-1] == last

    async def test_single_message_returns_failure(self) -> None:
        msgs: list[MessageDict] = [{"role": "user", "content": "only one"}]
        s = ReactiveCompactStrategy(_default_config(), llm_caller=MockLLMCaller())
        ctx = _make_context(ErrorKind.PROMPT_TOO_LONG, messages=msgs)
        result = await s.recover(ctx)
        assert result.success is False

    async def test_tokens_freed_positive(self) -> None:
        msgs: list[MessageDict] = [
            {"role": "user", "content": "a " * 500},
            {"role": "assistant", "content": "b " * 500},
            {"role": "user", "content": "c " * 500},
            {"role": "assistant", "content": "d " * 500},
            {"role": "user", "content": "current"},
        ]
        mock = MockLLMCaller(summary="Short summary.")
        s = ReactiveCompactStrategy(_default_config(), llm_caller=mock)
        ctx = _make_context(ErrorKind.PROMPT_TOO_LONG, messages=msgs)
        result = await s.recover(ctx)
        assert result.success is True
        assert result.tokens_freed > 0

    async def test_llm_failure_returns_graceful_failure(self) -> None:
        msgs: list[MessageDict] = [
            {"role": "user", "content": "old"},
            {"role": "assistant", "content": "old reply"},
            {"role": "user", "content": "current"},
        ]
        s = ReactiveCompactStrategy(_default_config(), llm_caller=FailingLLMCaller())
        ctx = _make_context(ErrorKind.PROMPT_TOO_LONG, messages=msgs)
        result = await s.recover(ctx)
        assert result.success is False


# ---------------------------------------------------------------------------
# StubLLMCompactCaller
# ---------------------------------------------------------------------------


class TestStubLLMCompactCaller:
    async def test_returns_placeholder(self) -> None:
        stub = StubLLMCompactCaller()
        result = await stub.compact("some text", "some prompt")
        assert "[Compacted summary of" in result
        assert "9 chars" in result  # len("some text") == 9


# ---------------------------------------------------------------------------
# Name property
# ---------------------------------------------------------------------------


class TestReactiveCompactName:
    def test_name_property(self) -> None:
        s = ReactiveCompactStrategy(_default_config())
        assert s.name == "reactive_compact"
