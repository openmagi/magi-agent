"""Tests for ContentReplacer (Tier 2-3 context snip compaction)."""
from __future__ import annotations

import json
import pytest

from openmagi_core_agent.context.content_replacement import (
    MAX_RESULT_TOKENS,
    ContentReplacer,
    SnipResult,
)
from openmagi_core_agent.context.types import WarningLevel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tool_result(content: str, *, role: str = "tool") -> dict:
    """Build a minimal tool-result message by role."""
    return {"role": role, "content": content}


def _make_tool_result_type(content) -> dict:
    """Build a tool-result message identified by type field (ADK style)."""
    return {"type": "tool_result", "content": content}


def _big_content(lines: int = 1000) -> str:
    """Generate content that will exceed MAX_RESULT_TOKENS when JSON-encoded.

    Each line is ~420 chars.  1000 lines ≈ 420 KB → ~105 K tokens (// 4).
    Use lines=1000+ to reliably exceed the 100 000-token threshold.
    """
    return "\n".join(f"line {i:05d}: " + "x" * 410 for i in range(lines))


def _token_estimate(msg: dict) -> int:
    return len(json.dumps(msg, default=str)) // 4


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

class TestWarningLevelNormal:
    """Warning level NORMAL → zero processing, messages returned as-is."""

    def test_normal_level_returns_messages_unchanged(self):
        replacer = ContentReplacer()
        msgs = [_make_tool_result("some content"), {"role": "user", "content": "hi"}]
        result_msgs, stats = replacer.apply(msgs, WarningLevel.NORMAL)
        assert result_msgs is msgs  # same object, not a copy
        assert stats == SnipResult(messages_processed=0, messages_snipped=0, tokens_freed=0)

    def test_normal_level_with_oversized_tool_result_still_no_op(self):
        replacer = ContentReplacer()
        big = _make_tool_result(_big_content(2000))
        msgs = [big]
        result_msgs, stats = replacer.apply(msgs, WarningLevel.NORMAL)
        assert result_msgs[0]["content"] == big["content"]
        assert stats.messages_snipped == 0
        assert stats.tokens_freed == 0


class TestUnderBudget:
    """Messages under MAX_RESULT_TOKENS are left unchanged (even at MODERATE+)."""

    def test_small_tool_result_unchanged_at_moderate(self):
        replacer = ContentReplacer()
        msg = _make_tool_result("short result")
        result_msgs, stats = replacer.apply([msg], WarningLevel.MODERATE)
        assert result_msgs[0]["content"] == "short result"
        assert stats.messages_processed == 1
        assert stats.messages_snipped == 0
        assert stats.tokens_freed == 0

    def test_small_tool_result_unchanged_at_critical(self):
        replacer = ContentReplacer()
        msg = _make_tool_result("another short result")
        result_msgs, stats = replacer.apply([msg], WarningLevel.CRITICAL)
        assert result_msgs[0]["content"] == "another short result"
        assert stats.messages_snipped == 0


class TestOversizedSnipping:
    """Single oversized tool_result is snipped with head/tail preserved."""

    def test_oversized_tool_result_is_snipped(self):
        replacer = ContentReplacer()
        content = _big_content(1000)
        msg = _make_tool_result(content)
        assert _token_estimate(msg) > MAX_RESULT_TOKENS

        result_msgs, stats = replacer.apply([msg], WarningLevel.MODERATE)
        snipped_content = result_msgs[0]["content"]
        assert "[... " in snipped_content
        assert "lines snipped" in snipped_content
        assert stats.messages_snipped == 1
        assert stats.tokens_freed > 0

    def test_oversized_snip_preserves_head_lines(self):
        replacer = ContentReplacer()
        lines = [f"line {i}" for i in range(1000)]
        content = "\n".join(lines)
        msg = _make_tool_result(content)

        result_msgs, _ = replacer.apply([msg], WarningLevel.HIGH)
        result_content = result_msgs[0]["content"]

        # head 25% of 1000 = 250 lines → first 250 lines present
        for i in range(250):
            assert f"line {i}" in result_content

    def test_oversized_snip_preserves_tail_lines(self):
        replacer = ContentReplacer()
        lines = [f"line {i}" for i in range(1000)]
        content = "\n".join(lines)
        msg = _make_tool_result(content)

        result_msgs, _ = replacer.apply([msg], WarningLevel.HIGH)
        result_content = result_msgs[0]["content"]

        # tail 25% of 1000 = 250 lines → last 250 lines present
        for i in range(750, 1000):
            assert f"line {i}" in result_content

    def test_type_tool_result_also_snipped(self):
        """Messages identified by type=tool_result (not role) are also processed."""
        replacer = ContentReplacer()
        content = _big_content(1000)
        msg = _make_tool_result_type(content)
        assert _token_estimate(msg) > MAX_RESULT_TOKENS

        result_msgs, stats = replacer.apply([msg], WarningLevel.MODERATE)
        assert stats.messages_snipped == 1


class TestMultipleMessages:
    """Only oversized tool results are snipped; others pass through unchanged."""

    def test_only_oversized_tool_result_snipped(self):
        replacer = ContentReplacer()
        small = _make_tool_result("small result")
        large = _make_tool_result(_big_content(1000))
        user_msg = {"role": "user", "content": "question"}

        result_msgs, stats = replacer.apply([small, large, user_msg], WarningLevel.MODERATE)

        # small unchanged
        assert result_msgs[0]["content"] == "small result"
        # large snipped
        assert "[... " in result_msgs[1]["content"]
        # user msg untouched
        assert result_msgs[2]["content"] == "question"

        assert stats.messages_processed == 2  # both tool results counted
        assert stats.messages_snipped == 1
        assert stats.tokens_freed > 0

    def test_multiple_oversized_tool_results_all_snipped(self):
        replacer = ContentReplacer()
        large1 = _make_tool_result(_big_content(1000))
        large2 = _make_tool_result(_big_content(1000))

        result_msgs, stats = replacer.apply([large1, large2], WarningLevel.HIGH)

        assert stats.messages_snipped == 2
        assert stats.messages_processed == 2
        assert stats.tokens_freed > 0


class TestNonToolMessages:
    """Non-tool messages (user, assistant) are never snipped regardless of size."""

    def test_user_message_never_snipped(self):
        replacer = ContentReplacer()
        msg = {"role": "user", "content": _big_content(1000)}
        result_msgs, stats = replacer.apply([msg], WarningLevel.CRITICAL)
        assert result_msgs[0]["content"] == msg["content"]
        assert stats.messages_processed == 0
        assert stats.messages_snipped == 0

    def test_assistant_message_never_snipped(self):
        replacer = ContentReplacer()
        msg = {"role": "assistant", "content": _big_content(1000)}
        result_msgs, stats = replacer.apply([msg], WarningLevel.CRITICAL)
        assert result_msgs[0]["content"] == msg["content"]
        assert stats.messages_snipped == 0


class TestEmptyContent:
    """Tool results with empty or minimal content are not snipped."""

    def test_empty_string_content_unchanged(self):
        replacer = ContentReplacer()
        msg = _make_tool_result("")
        result_msgs, stats = replacer.apply([msg], WarningLevel.HIGH)
        assert result_msgs[0]["content"] == ""
        assert stats.messages_snipped == 0

    def test_none_content_unchanged(self):
        replacer = ContentReplacer()
        msg = {"role": "tool", "content": None}
        # None content: _estimate_tokens will be tiny, won't exceed threshold
        result_msgs, stats = replacer.apply([msg], WarningLevel.HIGH)
        assert stats.messages_snipped == 0


class TestShortToolResult:
    """Tool results with <=10 lines are NOT snipped even if token-large."""

    def test_10_lines_no_snip(self):
        """Exactly 10 lines → too short to snip."""
        replacer = ContentReplacer()
        # Build content that is token-large but only 10 lines
        single_line = "x" * 50_000  # ~12500 tokens per line
        content = "\n".join([single_line] * 10)
        msg = _make_tool_result(content)
        # Verify it's token-large
        assert _token_estimate(msg) > MAX_RESULT_TOKENS

        result_msgs, stats = replacer.apply([msg], WarningLevel.CRITICAL)
        # Should NOT be snipped because line count <= 10
        assert "[... " not in result_msgs[0]["content"]
        assert stats.messages_snipped == 0

    def test_11_lines_can_snip(self):
        """11 lines → eligible for snipping if token-large."""
        replacer = ContentReplacer()
        single_line = "x" * 50_000
        content = "\n".join([single_line] * 11)
        msg = _make_tool_result(content)
        assert _token_estimate(msg) > MAX_RESULT_TOKENS

        result_msgs, stats = replacer.apply([msg], WarningLevel.CRITICAL)
        # 11 lines: head=2, tail=2, snipped_count=7 > 0 → snipped
        assert stats.messages_snipped == 1


class TestSnipResultStats:
    """SnipResult statistics are accurate."""

    def test_snip_result_fields_accurate(self):
        replacer = ContentReplacer()
        small = _make_tool_result("tiny")
        large = _make_tool_result(_big_content(1000))

        original_large_tokens = _token_estimate(large)
        result_msgs, stats = replacer.apply([small, large], WarningLevel.MODERATE)

        new_large_tokens = _token_estimate(result_msgs[1])
        expected_freed = original_large_tokens - new_large_tokens

        assert stats.messages_processed == 2
        assert stats.messages_snipped == 1
        assert stats.tokens_freed == expected_freed
        assert stats.tokens_freed > 0

    def test_zero_stats_when_nothing_to_snip(self):
        replacer = ContentReplacer()
        msgs = [{"role": "user", "content": "hi"}, _make_tool_result("small")]
        _, stats = replacer.apply(msgs, WarningLevel.MODERATE)
        assert stats.messages_processed == 1
        assert stats.messages_snipped == 0
        assert stats.tokens_freed == 0

    def test_snip_result_is_frozen(self):
        s = SnipResult(messages_processed=1, messages_snipped=0, tokens_freed=0)
        with pytest.raises((AttributeError, TypeError)):
            s.messages_processed = 99  # type: ignore[misc]


class TestStructuredContent:
    """Structured content (list of content blocks) is handled correctly."""

    def test_list_content_blocks_concatenated_and_snipped(self):
        replacer = ContentReplacer()
        # Build oversized structured content: two 1000-line blocks → >200K tokens
        big_text = _big_content(1000)
        content_blocks = [
            {"type": "text", "text": big_text},
            {"type": "text", "text": big_text},
        ]
        msg = {"role": "tool", "content": content_blocks}
        assert _token_estimate(msg) > MAX_RESULT_TOKENS

        result_msgs, stats = replacer.apply([msg], WarningLevel.MODERATE)
        # Content should be flattened to string with snip marker
        result_content = result_msgs[0]["content"]
        assert isinstance(result_content, str)
        assert "[... " in result_content
        assert stats.messages_snipped == 1

    def test_list_content_small_blocks_not_snipped(self):
        replacer = ContentReplacer()
        content_blocks = [
            {"type": "text", "text": "hello"},
            {"type": "text", "text": "world"},
        ]
        msg = {"role": "tool", "content": content_blocks}
        result_msgs, stats = replacer.apply([msg], WarningLevel.HIGH)
        assert stats.messages_snipped == 0

    def test_list_content_string_blocks_handled(self):
        """String items inside content list are also collected."""
        replacer = ContentReplacer()
        big_text = _big_content(1000)
        content_blocks = [big_text, big_text]  # plain strings in list
        msg = {"role": "tool", "content": content_blocks}
        assert _token_estimate(msg) > MAX_RESULT_TOKENS

        result_msgs, stats = replacer.apply([msg], WarningLevel.MODERATE)
        result_content = result_msgs[0]["content"]
        assert isinstance(result_content, str)
        assert "[... " in result_content
        assert stats.messages_snipped == 1

    def test_mixed_content_text_and_image_blocks_unchanged(self):
        """Mixed content (text + image block) → message returned unchanged."""
        replacer = ContentReplacer()
        big_text = _big_content(1000)
        content_blocks = [
            {"type": "text", "text": big_text},
            {"type": "image", "url": "https://example.com/image.png"},
        ]
        msg = {"role": "tool", "content": content_blocks}
        assert _token_estimate(msg) > MAX_RESULT_TOKENS

        result_msgs, stats = replacer.apply([msg], WarningLevel.CRITICAL)
        # Should NOT be snipped because of mixed-type blocks
        assert result_msgs[0]["content"] == content_blocks
        assert stats.messages_snipped == 0
