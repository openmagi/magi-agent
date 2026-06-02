"""Tests for MicrocompactEngine (Tier 4 LLM-based tool-result compression)."""
from __future__ import annotations

import json
import pytest

from openmagi_core_agent.context.microcompact import (
    MIN_RESULT_TOKENS_FOR_COMPACT,
    MicrocompactEngine,
    MicrocompactResult,
)
from openmagi_core_agent.context.types import WarningLevel


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

async def mock_classifier(prompt: str) -> str:
    return "Summarized: " + prompt[:50]


async def failing_classifier(prompt: str) -> str:
    raise RuntimeError("classifier unavailable")


def _big_content(char_count: int = 40_000) -> str:
    """Return a string whose tool-result message will exceed MIN_RESULT_TOKENS_FOR_COMPACT."""
    return "x" * char_count


def _small_content() -> str:
    """Return content whose tool-result message is well below the token threshold."""
    return "small result"


def _make_tool_result(
    content: str,
    *,
    role: str = "tool",
    tool_use_id: str | None = None,
) -> dict:
    msg: dict = {"role": role, "content": content}
    if tool_use_id is not None:
        msg["tool_use_id"] = tool_use_id
    return msg


def _make_tool_result_type(
    content: str,
    *,
    tool_use_id: str | None = None,
) -> dict:
    msg: dict = {"type": "tool_result", "content": content}
    if tool_use_id is not None:
        msg["tool_use_id"] = tool_use_id
    return msg


def _make_user_message(text: str) -> dict:
    return {"role": "user", "content": text}


def _estimate_tokens(msg: dict) -> int:
    return len(json.dumps(msg, default=str)) // 4


# ---------------------------------------------------------------------------
# 1. WarningLevel NORMAL → no processing
# ---------------------------------------------------------------------------

class TestWarningLevelNormal:
    @pytest.mark.asyncio
    async def test_normal_level_returns_messages_unchanged(self):
        engine = MicrocompactEngine(mock_classifier)
        msg = _make_tool_result(_big_content(), tool_use_id="id1")
        messages = [msg]

        result_msgs, stats = await engine.apply(messages, WarningLevel.NORMAL)

        assert result_msgs == messages
        assert stats.messages_processed == 0
        assert stats.messages_compacted == 0
        assert stats.cache_hits == 0
        assert stats.tokens_freed == 0

    @pytest.mark.asyncio
    async def test_normal_level_does_not_call_classifier(self):
        calls: list[str] = []

        async def tracking_classifier(prompt: str) -> str:
            calls.append(prompt)
            return "summary"

        engine = MicrocompactEngine(tracking_classifier)
        messages = [_make_tool_result(_big_content(), tool_use_id="id1")]

        await engine.apply(messages, WarningLevel.NORMAL)

        assert calls == [], "classifier must not be called at NORMAL level"


# ---------------------------------------------------------------------------
# 2. WarningLevel MODERATE → no processing
# ---------------------------------------------------------------------------

class TestWarningLevelModerate:
    @pytest.mark.asyncio
    async def test_moderate_level_returns_messages_unchanged(self):
        engine = MicrocompactEngine(mock_classifier)
        msg = _make_tool_result(_big_content(), tool_use_id="id2")
        messages = [msg]

        result_msgs, stats = await engine.apply(messages, WarningLevel.MODERATE)

        assert result_msgs == messages
        assert stats.messages_processed == 0
        assert stats.messages_compacted == 0

    @pytest.mark.asyncio
    async def test_moderate_level_does_not_call_classifier(self):
        calls: list[str] = []

        async def tracking_classifier(prompt: str) -> str:
            calls.append(prompt)
            return "summary"

        engine = MicrocompactEngine(tracking_classifier)
        messages = [_make_tool_result(_big_content(), tool_use_id="id2")]

        await engine.apply(messages, WarningLevel.MODERATE)

        assert calls == []


# ---------------------------------------------------------------------------
# 3. WarningLevel HIGH → processing activated
# ---------------------------------------------------------------------------

class TestWarningLevelHigh:
    @pytest.mark.asyncio
    async def test_high_level_activates_processing(self):
        engine = MicrocompactEngine(mock_classifier)
        big = _big_content()
        messages = [_make_tool_result(big, tool_use_id="id3")]

        result_msgs, stats = await engine.apply(messages, WarningLevel.HIGH)

        assert stats.messages_processed == 1
        assert stats.messages_compacted == 1

    @pytest.mark.asyncio
    async def test_high_level_reduces_content(self):
        engine = MicrocompactEngine(mock_classifier)
        messages = [_make_tool_result(_big_content(), tool_use_id="id3b")]

        result_msgs, stats = await engine.apply(messages, WarningLevel.HIGH)

        # The compacted message must be shorter than the original
        assert len(result_msgs[0]["content"]) < len(_big_content())


# ---------------------------------------------------------------------------
# 4. WarningLevel CRITICAL → processing activated
# ---------------------------------------------------------------------------

class TestWarningLevelCritical:
    @pytest.mark.asyncio
    async def test_critical_level_activates_processing(self):
        engine = MicrocompactEngine(mock_classifier)
        messages = [_make_tool_result(_big_content(), tool_use_id="id4")]

        result_msgs, stats = await engine.apply(messages, WarningLevel.CRITICAL)

        assert stats.messages_processed == 1
        assert stats.messages_compacted == 1

    @pytest.mark.asyncio
    async def test_critical_level_tokens_freed_positive(self):
        engine = MicrocompactEngine(mock_classifier)
        messages = [_make_tool_result(_big_content(), tool_use_id="id4b")]

        _, stats = await engine.apply(messages, WarningLevel.CRITICAL)

        assert stats.tokens_freed > 0


# ---------------------------------------------------------------------------
# 5. Cache hit → uses cached summary, no classifier call
# ---------------------------------------------------------------------------

class TestCacheHit:
    @pytest.mark.asyncio
    async def test_cache_hit_uses_cached_summary(self):
        pre_cache = {"cached-id": "pre-cached summary"}
        engine = MicrocompactEngine(mock_classifier, cache=pre_cache)
        messages = [_make_tool_result(_big_content(), tool_use_id="cached-id")]

        result_msgs, stats = await engine.apply(messages, WarningLevel.HIGH)

        assert result_msgs[0]["content"] == "pre-cached summary"
        assert stats.cache_hits == 1
        assert stats.compacted == 1 if hasattr(stats, "compacted") else stats.messages_compacted == 1

    @pytest.mark.asyncio
    async def test_cache_hit_does_not_call_classifier(self):
        calls: list[str] = []

        async def tracking_classifier(prompt: str) -> str:
            calls.append(prompt)
            return "summary"

        pre_cache = {"cached-id": "pre-cached summary"}
        engine = MicrocompactEngine(tracking_classifier, cache=pre_cache)
        messages = [_make_tool_result(_big_content(), tool_use_id="cached-id")]

        await engine.apply(messages, WarningLevel.HIGH)

        assert calls == [], "classifier must not be called on cache hit"


# ---------------------------------------------------------------------------
# 6. Cache miss → calls classifier, stores in cache
# ---------------------------------------------------------------------------

class TestCacheMiss:
    @pytest.mark.asyncio
    async def test_cache_miss_calls_classifier(self):
        calls: list[str] = []

        async def tracking_classifier(prompt: str) -> str:
            calls.append(prompt)
            return "fresh summary"

        engine = MicrocompactEngine(tracking_classifier)
        messages = [_make_tool_result(_big_content(), tool_use_id="fresh-id")]

        await engine.apply(messages, WarningLevel.HIGH)

        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_cache_miss_stores_result_in_cache(self):
        engine = MicrocompactEngine(mock_classifier)
        messages = [_make_tool_result(_big_content(), tool_use_id="store-id")]

        await engine.apply(messages, WarningLevel.HIGH)

        assert "store-id" in engine.cache


# ---------------------------------------------------------------------------
# 7. Small result (< 2000 tokens) → skipped, not compacted
# ---------------------------------------------------------------------------

class TestSmallResult:
    @pytest.mark.asyncio
    async def test_small_result_not_compacted(self):
        calls: list[str] = []

        async def tracking_classifier(prompt: str) -> str:
            calls.append(prompt)
            return "summary"

        engine = MicrocompactEngine(tracking_classifier)
        messages = [_make_tool_result(_small_content(), tool_use_id="small-id")]

        # Verify the small content is actually below the threshold
        msg = messages[0]
        token_count = len(json.dumps(msg, default=str)) // 4
        assert token_count < MIN_RESULT_TOKENS_FOR_COMPACT

        result_msgs, stats = await engine.apply(messages, WarningLevel.HIGH)

        assert calls == [], "classifier must not be called for small results"
        assert stats.messages_compacted == 0
        assert result_msgs[0] == messages[0]

    @pytest.mark.asyncio
    async def test_small_result_still_counted_as_processed(self):
        engine = MicrocompactEngine(mock_classifier)
        messages = [_make_tool_result(_small_content(), tool_use_id="small-id2")]

        _, stats = await engine.apply(messages, WarningLevel.HIGH)

        assert stats.messages_processed == 1
        assert stats.messages_compacted == 0


# ---------------------------------------------------------------------------
# 8. Large result (>= 2000 tokens) → compacted via classifier
# ---------------------------------------------------------------------------

class TestLargeResult:
    @pytest.mark.asyncio
    async def test_large_result_is_compacted(self):
        engine = MicrocompactEngine(mock_classifier)
        big = _big_content(char_count=50_000)  # well above threshold
        msg = _make_tool_result(big, tool_use_id="large-id")

        # Verify it's above threshold
        token_count = _estimate_tokens(msg)
        assert token_count >= MIN_RESULT_TOKENS_FOR_COMPACT

        result_msgs, stats = await engine.apply([msg], WarningLevel.HIGH)

        assert stats.messages_compacted == 1
        assert len(result_msgs[0]["content"]) < len(big)

    @pytest.mark.asyncio
    async def test_large_result_tokens_freed_matches_delta(self):
        engine = MicrocompactEngine(mock_classifier)
        big = _big_content(char_count=50_000)
        msg = _make_tool_result(big, tool_use_id="large-id2")
        original_tokens = _estimate_tokens(msg)

        result_msgs, stats = await engine.apply([msg], WarningLevel.HIGH)

        new_tokens = _estimate_tokens(result_msgs[0])
        expected_freed = original_tokens - new_tokens
        assert stats.tokens_freed == expected_freed


# ---------------------------------------------------------------------------
# 9. Classifier failure → fail-open, original message preserved
# ---------------------------------------------------------------------------

class TestClassifierFailure:
    @pytest.mark.asyncio
    async def test_fail_open_on_classifier_error(self):
        engine = MicrocompactEngine(failing_classifier)
        big = _big_content()
        msg = _make_tool_result(big, tool_use_id="fail-id")

        result_msgs, stats = await engine.apply([msg], WarningLevel.HIGH)

        # Original message preserved
        assert result_msgs[0]["content"] == big
        # Not counted as compacted
        assert stats.messages_compacted == 0

    @pytest.mark.asyncio
    async def test_fail_open_preserves_all_fields(self):
        engine = MicrocompactEngine(failing_classifier)
        big = _big_content()
        msg = _make_tool_result(big, tool_use_id="fail-id2")

        result_msgs, _ = await engine.apply([msg], WarningLevel.HIGH)

        assert result_msgs[0] == msg

    @pytest.mark.asyncio
    async def test_fail_open_does_not_cache(self):
        engine = MicrocompactEngine(failing_classifier)
        msg = _make_tool_result(_big_content(), tool_use_id="fail-cache-id")

        await engine.apply([msg], WarningLevel.HIGH)

        assert "fail-cache-id" not in engine.cache


# ---------------------------------------------------------------------------
# 10. MicrocompactResult stats correct
# ---------------------------------------------------------------------------

class TestMicrocompactResultStats:
    @pytest.mark.asyncio
    async def test_stats_processed_includes_small_and_large(self):
        engine = MicrocompactEngine(mock_classifier)
        messages = [
            _make_tool_result(_small_content(), tool_use_id="s1"),
            _make_tool_result(_big_content(), tool_use_id="l1"),
        ]

        _, stats = await engine.apply(messages, WarningLevel.HIGH)

        assert stats.messages_processed == 2
        assert stats.messages_compacted == 1

    @pytest.mark.asyncio
    async def test_stats_zero_when_no_tool_results(self):
        engine = MicrocompactEngine(mock_classifier)
        messages = [_make_user_message("hello"), _make_user_message("world")]

        _, stats = await engine.apply(messages, WarningLevel.HIGH)

        assert stats.messages_processed == 0
        assert stats.messages_compacted == 0
        assert stats.cache_hits == 0
        assert stats.tokens_freed == 0

    @pytest.mark.asyncio
    async def test_stats_cache_hits_counted_separately(self):
        pre_cache = {"c1": "cached"}
        engine = MicrocompactEngine(mock_classifier, cache=pre_cache)
        messages = [
            _make_tool_result(_big_content(), tool_use_id="c1"),  # cache hit
            _make_tool_result(_big_content(), tool_use_id="c2"),  # cache miss → LLM
        ]

        _, stats = await engine.apply(messages, WarningLevel.CRITICAL)

        assert stats.cache_hits == 1
        assert stats.messages_compacted == 2  # both cache hit and LLM compacted


# ---------------------------------------------------------------------------
# 11. clear_cache() empties the cache
# ---------------------------------------------------------------------------

class TestClearCache:
    @pytest.mark.asyncio
    async def test_clear_cache_empties(self):
        pre_cache = {"id1": "summary1", "id2": "summary2"}
        engine = MicrocompactEngine(mock_classifier, cache=pre_cache)

        assert len(engine.cache) == 2

        engine.clear_cache()

        assert len(engine.cache) == 0

    @pytest.mark.asyncio
    async def test_clear_cache_then_recompact_uses_classifier(self):
        calls: list[str] = []

        async def tracking_classifier(prompt: str) -> str:
            calls.append(prompt)
            return "fresh"

        pre_cache = {"id-x": "old summary"}
        engine = MicrocompactEngine(tracking_classifier, cache=pre_cache)

        engine.clear_cache()

        messages = [_make_tool_result(_big_content(), tool_use_id="id-x")]
        await engine.apply(messages, WarningLevel.HIGH)

        assert len(calls) == 1, "classifier should be called after cache cleared"


# ---------------------------------------------------------------------------
# 12. Mixed tool and non-tool messages → only tool results processed
# ---------------------------------------------------------------------------

class TestMixedMessages:
    @pytest.mark.asyncio
    async def test_only_tool_results_are_processed(self):
        calls: list[str] = []

        async def tracking_classifier(prompt: str) -> str:
            calls.append(prompt)
            return "summary"

        engine = MicrocompactEngine(tracking_classifier)
        messages = [
            _make_user_message("user message 1"),
            _make_tool_result(_big_content(), tool_use_id="t1"),
            {"role": "assistant", "content": "assistant response"},
            _make_tool_result(_big_content(), tool_use_id="t2"),
            _make_user_message("user message 2"),
        ]

        result_msgs, stats = await engine.apply(messages, WarningLevel.HIGH)

        # Only the 2 tool results should be processed
        assert stats.messages_processed == 2
        assert stats.messages_compacted == 2
        # 2 classifier calls (one per large tool result)
        assert len(calls) == 2

    @pytest.mark.asyncio
    async def test_non_tool_messages_pass_through_unchanged(self):
        engine = MicrocompactEngine(mock_classifier)
        user_msg = _make_user_message("unchanged user message")
        assistant_msg = {"role": "assistant", "content": "unchanged assistant"}
        tool_msg = _make_tool_result(_big_content(), tool_use_id="t3")

        messages = [user_msg, assistant_msg, tool_msg]
        result_msgs, _ = await engine.apply(messages, WarningLevel.HIGH)

        assert result_msgs[0] == user_msg
        assert result_msgs[1] == assistant_msg
        # tool result at index 2 should be compacted (different content)
        assert result_msgs[2] != tool_msg

    @pytest.mark.asyncio
    async def test_adk_style_type_field_tool_results_processed(self):
        """Tool results identified by type='tool_result' (ADK style) are also processed."""
        engine = MicrocompactEngine(mock_classifier)
        adk_msg = _make_tool_result_type(_big_content(), tool_use_id="adk-id")

        result_msgs, stats = await engine.apply([adk_msg], WarningLevel.HIGH)

        assert stats.messages_processed == 1
        assert stats.messages_compacted == 1
