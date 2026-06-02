from __future__ import annotations

import json

import pytest

from openmagi_core_agent.runtime.error_recovery.types import (
    ErrorKind,
    ErrorRecoveryConfig,
    MessageDict,
    RecoverableError,
    RecoveryAttemptState,
    RecoveryContext,
    RecoveryResult,
)
from openmagi_core_agent.runtime.error_recovery.strategies import (
    CollapseDrainStrategy,
    MediaRemovalStrategy,
    OutputEscalationStrategy,
    RateLimitStrategy,
    RecoveryMessageStrategy,
)

pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_error(kind: ErrorKind, tokens_over: int | None = None) -> RecoverableError:
    return RecoverableError(
        kind=kind,
        original_error="test error",
        tokens_over=tokens_over,
    )


def _make_context(
    kind: ErrorKind,
    messages: list[MessageDict] | None = None,
    attempt: int = 0,
    tokens_over: int | None = None,
) -> RecoveryContext:
    return RecoveryContext(
        error=_make_error(kind, tokens_over=tokens_over),
        messages=messages or [],
        attempt=attempt,
        session_key="test-session",
        turn_id="test-turn",
    )


def _default_config() -> ErrorRecoveryConfig:
    return ErrorRecoveryConfig(recovery_enabled=True)


def _make_round(user_text: str, assistant_text: str, tool_result: str | None = None) -> list[MessageDict]:
    """Build a user->assistant->tool_result round of messages."""
    msgs: list[MessageDict] = [
        {"role": "user", "content": user_text},
        {"role": "assistant", "content": assistant_text},
    ]
    if tool_result is not None:
        msgs.append({"role": "user", "content": [{"type": "tool_result", "content": tool_result}]})
    return msgs


# ---------------------------------------------------------------------------
# CollapseDrainStrategy
# ---------------------------------------------------------------------------


class TestCollapseDrainAppliesTo:
    def test_applies_to_prompt_too_long(self) -> None:
        s = CollapseDrainStrategy(_default_config())
        assert s.applies_to(_make_error(ErrorKind.PROMPT_TOO_LONG)) is True

    def test_does_not_apply_to_rate_limit(self) -> None:
        s = CollapseDrainStrategy(_default_config())
        assert s.applies_to(_make_error(ErrorKind.RATE_LIMIT)) is False

    def test_does_not_apply_to_max_output(self) -> None:
        s = CollapseDrainStrategy(_default_config())
        assert s.applies_to(_make_error(ErrorKind.MAX_OUTPUT_TOKENS)) is False


class TestCollapseDrainRecover:
    async def test_drops_oldest_rounds(self) -> None:
        # 5 rounds => drop 20% = 1 round (rounds[1], keeping first and last)
        rounds = []
        for i in range(5):
            rounds.extend(_make_round(f"user-{i}", f"assistant-{i}", f"tool-{i}"))
        ctx = _make_context(ErrorKind.PROMPT_TOO_LONG, messages=rounds)
        s = CollapseDrainStrategy(_default_config())
        result = await s.recover(ctx)
        assert result.success is True
        assert result.modified_messages is not None
        # First round (3 msgs) preserved, last round (3 msgs) preserved, 1 middle dropped
        # 5 rounds * 3 msgs = 15, drop 1 round (3 msgs) = 12
        assert len(result.modified_messages) == 12
        # First message must be preserved
        assert result.modified_messages[0]["content"] == "user-0"
        # Last message must be preserved
        assert result.modified_messages[-1]["content"] == [{"type": "tool_result", "content": "tool-4"}]

    async def test_guard_rejects_when_already_attempted(self) -> None:
        rounds = []
        for i in range(5):
            rounds.extend(_make_round(f"user-{i}", f"assistant-{i}", f"tool-{i}"))
        ctx = _make_context(ErrorKind.PROMPT_TOO_LONG, messages=rounds)
        state = RecoveryAttemptState(collapse_attempted=True)
        s = CollapseDrainStrategy(_default_config())
        result = await s.recover(ctx, state)
        assert result.success is False

    async def test_empty_messages(self) -> None:
        ctx = _make_context(ErrorKind.PROMPT_TOO_LONG, messages=[])
        s = CollapseDrainStrategy(_default_config())
        result = await s.recover(ctx)
        assert result.success is False

    async def test_single_round_not_droppable(self) -> None:
        msgs = _make_round("user-0", "assistant-0", "tool-0")
        ctx = _make_context(ErrorKind.PROMPT_TOO_LONG, messages=msgs)
        s = CollapseDrainStrategy(_default_config())
        result = await s.recover(ctx)
        assert result.success is False

    async def test_two_rounds_not_droppable(self) -> None:
        # 2 rounds without tool_results = exactly 2 partitioned rounds
        # first + last protected, nothing droppable in between
        msgs: list[MessageDict] = [
            {"role": "user", "content": "user-0"},
            {"role": "assistant", "content": "assistant-0"},
            {"role": "user", "content": "user-1"},
            {"role": "assistant", "content": "assistant-1"},
        ]
        ctx = _make_context(ErrorKind.PROMPT_TOO_LONG, messages=msgs)
        s = CollapseDrainStrategy(_default_config())
        result = await s.recover(ctx)
        assert result.success is False

    async def test_tokens_freed_estimated(self) -> None:
        rounds = []
        for i in range(10):
            rounds.extend(_make_round(f"user-{i}", f"assistant-{i}", f"tool-{i}"))
        ctx = _make_context(ErrorKind.PROMPT_TOO_LONG, messages=rounds)
        s = CollapseDrainStrategy(_default_config())
        result = await s.recover(ctx)
        assert result.success is True
        assert result.tokens_freed > 0

    def test_name_property(self) -> None:
        s = CollapseDrainStrategy(_default_config())
        assert s.name == "collapse_drain"


# ---------------------------------------------------------------------------
# OutputEscalationStrategy
# ---------------------------------------------------------------------------


class TestOutputEscalationAppliesTo:
    def test_applies_to_max_output_tokens(self) -> None:
        s = OutputEscalationStrategy(_default_config())
        assert s.applies_to(_make_error(ErrorKind.MAX_OUTPUT_TOKENS)) is True

    def test_does_not_apply_to_prompt_too_long(self) -> None:
        s = OutputEscalationStrategy(_default_config())
        assert s.applies_to(_make_error(ErrorKind.PROMPT_TOO_LONG)) is False


class TestOutputEscalationRecover:
    async def test_sets_retry_config(self) -> None:
        msgs = [{"role": "user", "content": "hello"}]
        ctx = _make_context(ErrorKind.MAX_OUTPUT_TOKENS, messages=msgs)
        s = OutputEscalationStrategy(_default_config())
        result = await s.recover(ctx)
        assert result.success is True
        assert result.retry_with_config == {"max_tokens": 65536}
        assert result.modified_messages == msgs

    async def test_guard_rejects_when_already_attempted(self) -> None:
        msgs = [{"role": "user", "content": "hello"}]
        ctx = _make_context(ErrorKind.MAX_OUTPUT_TOKENS, messages=msgs)
        state = RecoveryAttemptState(escalation_attempted=True)
        s = OutputEscalationStrategy(_default_config())
        result = await s.recover(ctx, state)
        assert result.success is False

    async def test_custom_max_tokens(self) -> None:
        config = ErrorRecoveryConfig(recovery_enabled=True, max_output_tokens_escalation=32768)
        msgs = [{"role": "user", "content": "hello"}]
        ctx = _make_context(ErrorKind.MAX_OUTPUT_TOKENS, messages=msgs)
        s = OutputEscalationStrategy(config)
        result = await s.recover(ctx)
        assert result.retry_with_config == {"max_tokens": 32768}

    def test_name_property(self) -> None:
        s = OutputEscalationStrategy(_default_config())
        assert s.name == "output_escalation"


# ---------------------------------------------------------------------------
# RecoveryMessageStrategy
# ---------------------------------------------------------------------------


class TestRecoveryMessageAppliesTo:
    def test_applies_to_max_output_tokens(self) -> None:
        s = RecoveryMessageStrategy(_default_config())
        assert s.applies_to(_make_error(ErrorKind.MAX_OUTPUT_TOKENS)) is True

    def test_does_not_apply_to_media_size(self) -> None:
        s = RecoveryMessageStrategy(_default_config())
        assert s.applies_to(_make_error(ErrorKind.MEDIA_SIZE)) is False


class TestRecoveryMessageRecover:
    async def test_appends_recovery_message(self) -> None:
        msgs: list[MessageDict] = [{"role": "user", "content": "hello"}]
        ctx = _make_context(ErrorKind.MAX_OUTPUT_TOKENS, messages=msgs)
        s = RecoveryMessageStrategy(_default_config())
        result = await s.recover(ctx)
        assert result.success is True
        assert result.modified_messages is not None
        assert len(result.modified_messages) == 2
        last = result.modified_messages[-1]
        assert last["role"] == "user"
        assert "truncated" in str(last["content"]).lower()

    async def test_guard_rejects_after_three(self) -> None:
        msgs: list[MessageDict] = [{"role": "user", "content": "hello"}]
        ctx = _make_context(ErrorKind.MAX_OUTPUT_TOKENS, messages=msgs)
        state = RecoveryAttemptState(recovery_messages_sent=3)
        s = RecoveryMessageStrategy(_default_config())
        result = await s.recover(ctx, state)
        assert result.success is False

    async def test_allows_second_message(self) -> None:
        msgs: list[MessageDict] = [{"role": "user", "content": "hello"}]
        ctx = _make_context(ErrorKind.MAX_OUTPUT_TOKENS, messages=msgs)
        state = RecoveryAttemptState(recovery_messages_sent=2)
        s = RecoveryMessageStrategy(_default_config())
        result = await s.recover(ctx, state)
        assert result.success is True

    async def test_does_not_mutate_original(self) -> None:
        msgs: list[MessageDict] = [{"role": "user", "content": "hello"}]
        ctx = _make_context(ErrorKind.MAX_OUTPUT_TOKENS, messages=msgs)
        s = RecoveryMessageStrategy(_default_config())
        result = await s.recover(ctx)
        # Original context messages unchanged (frozen model)
        assert len(ctx.messages) == 1

    def test_name_property(self) -> None:
        s = RecoveryMessageStrategy(_default_config())
        assert s.name == "recovery_message"


# ---------------------------------------------------------------------------
# MediaRemovalStrategy
# ---------------------------------------------------------------------------


class TestMediaRemovalAppliesTo:
    def test_applies_to_media_size(self) -> None:
        s = MediaRemovalStrategy(_default_config())
        assert s.applies_to(_make_error(ErrorKind.MEDIA_SIZE)) is True

    def test_does_not_apply_to_rate_limit(self) -> None:
        s = MediaRemovalStrategy(_default_config())
        assert s.applies_to(_make_error(ErrorKind.RATE_LIMIT)) is False

    def test_does_not_apply_to_prompt_too_long(self) -> None:
        s = MediaRemovalStrategy(_default_config())
        assert s.applies_to(_make_error(ErrorKind.PROMPT_TOO_LONG)) is False


class TestMediaRemovalRecover:
    async def test_removes_image_blocks(self) -> None:
        msgs: list[MessageDict] = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Look at this"},
                    {"type": "image", "source": {"data": "abc" * 1000}},
                ],
            }
        ]
        ctx = _make_context(ErrorKind.MEDIA_SIZE, messages=msgs)
        s = MediaRemovalStrategy(_default_config())
        result = await s.recover(ctx)
        assert result.success is True
        assert result.modified_messages is not None
        content = result.modified_messages[0]["content"]
        assert isinstance(content, list)
        assert len(content) == 1
        assert content[0]["type"] == "text"

    async def test_removes_image_url_blocks(self) -> None:
        msgs: list[MessageDict] = [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": "https://example.com/img.png"}},
                ],
            }
        ]
        ctx = _make_context(ErrorKind.MEDIA_SIZE, messages=msgs)
        s = MediaRemovalStrategy(_default_config())
        result = await s.recover(ctx)
        assert result.modified_messages is not None
        content = result.modified_messages[0]["content"]
        assert content == "[Media removed due to size constraints]"

    async def test_removes_document_and_file_blocks(self) -> None:
        msgs: list[MessageDict] = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Check this doc"},
                    {"type": "document", "source": {"data": "d" * 500}},
                    {"type": "file", "source": {"data": "f" * 500}},
                ],
            }
        ]
        ctx = _make_context(ErrorKind.MEDIA_SIZE, messages=msgs)
        s = MediaRemovalStrategy(_default_config())
        result = await s.recover(ctx)
        assert result.modified_messages is not None
        content = result.modified_messages[0]["content"]
        assert isinstance(content, list)
        assert len(content) == 1
        assert content[0]["type"] == "text"

    async def test_preserves_text_only_messages(self) -> None:
        msgs: list[MessageDict] = [
            {"role": "user", "content": "just text"},
            {"role": "assistant", "content": "response"},
        ]
        ctx = _make_context(ErrorKind.MEDIA_SIZE, messages=msgs)
        s = MediaRemovalStrategy(_default_config())
        result = await s.recover(ctx)
        assert result.success is False
        assert result.tokens_freed == 0

    async def test_no_media_returns_failure(self) -> None:
        msgs: list[MessageDict] = [
            {"role": "user", "content": "just text"},
            {"role": "assistant", "content": "response"},
        ]
        ctx = _make_context(ErrorKind.MEDIA_SIZE, messages=msgs)
        s = MediaRemovalStrategy(_default_config())
        result = await s.recover(ctx)
        assert result.success is False
        assert result.tokens_freed == 0

    async def test_empty_messages(self) -> None:
        ctx = _make_context(ErrorKind.MEDIA_SIZE, messages=[])
        s = MediaRemovalStrategy(_default_config())
        result = await s.recover(ctx)
        assert result.success is False
        assert result.modified_messages is None

    async def test_tokens_freed_positive_when_media_removed(self) -> None:
        msgs: list[MessageDict] = [
            {
                "role": "user",
                "content": [
                    {"type": "image", "source": {"data": "x" * 4000}},
                ],
            }
        ]
        ctx = _make_context(ErrorKind.MEDIA_SIZE, messages=msgs)
        s = MediaRemovalStrategy(_default_config())
        result = await s.recover(ctx)
        assert result.tokens_freed > 0

    def test_name_property(self) -> None:
        s = MediaRemovalStrategy(_default_config())
        assert s.name == "media_removal"


# ---------------------------------------------------------------------------
# RateLimitStrategy
# ---------------------------------------------------------------------------


class TestRateLimitAppliesTo:
    def test_applies_to_rate_limit(self) -> None:
        s = RateLimitStrategy(_default_config())
        assert s.applies_to(_make_error(ErrorKind.RATE_LIMIT)) is True

    def test_does_not_apply_to_prompt_too_long(self) -> None:
        s = RateLimitStrategy(_default_config())
        assert s.applies_to(_make_error(ErrorKind.PROMPT_TOO_LONG)) is False


class TestRateLimitRecover:
    async def test_succeeds_within_max_retries(self) -> None:
        config = ErrorRecoveryConfig(
            recovery_enabled=True,
            rate_limit_base_delay_seconds=0.001,
        )
        msgs: list[MessageDict] = [{"role": "user", "content": "hello"}]
        ctx = _make_context(ErrorKind.RATE_LIMIT, messages=msgs)
        ctx = ctx.model_copy(update={"attempt": 0})
        s = RateLimitStrategy(config)
        result = await s.recover(ctx)
        assert result.success is True
        assert result.modified_messages == msgs

    async def test_guard_rejects_over_max_retries(self) -> None:
        config = ErrorRecoveryConfig(
            recovery_enabled=True,
            rate_limit_max_retries=3,
            rate_limit_base_delay_seconds=0.001,
        )
        msgs: list[MessageDict] = [{"role": "user", "content": "hello"}]
        ctx = _make_context(ErrorKind.RATE_LIMIT, messages=msgs)
        ctx = ctx.model_copy(update={"attempt": 3})
        s = RateLimitStrategy(config)
        result = await s.recover(ctx)
        assert result.success is False

    async def test_allows_attempt_below_max(self) -> None:
        config = ErrorRecoveryConfig(
            recovery_enabled=True,
            rate_limit_max_retries=3,
            rate_limit_base_delay_seconds=0.001,
        )
        msgs: list[MessageDict] = [{"role": "user", "content": "hello"}]
        ctx = _make_context(ErrorKind.RATE_LIMIT, messages=msgs)
        ctx = ctx.model_copy(update={"attempt": 2})
        s = RateLimitStrategy(config)
        result = await s.recover(ctx)
        assert result.success is True

    def test_name_property(self) -> None:
        s = RateLimitStrategy(_default_config())
        assert s.name == "rate_limit"

    async def test_messages_unchanged(self) -> None:
        config = ErrorRecoveryConfig(
            recovery_enabled=True,
            rate_limit_base_delay_seconds=0.001,
        )
        msgs: list[MessageDict] = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}]
        ctx = _make_context(ErrorKind.RATE_LIMIT, messages=msgs)
        s = RateLimitStrategy(config)
        result = await s.recover(ctx)
        assert result.modified_messages == msgs
