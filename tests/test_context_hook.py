"""Tests for ContextManagementHook — the full pipeline orchestrator."""

from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from magi_agent.context.hook import (
    HOOK_NAME,
    HOOK_PRIORITY,
    ContextManagementHook,
    PipelineResult,
    load_config_from_env,
    make_context_management_manifest,
)
from magi_agent.context.types import ContextManagementConfig, WarningLevel
from magi_agent.hooks.context import HookContext
from magi_agent.hooks.manifest import HookPoint


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def mock_classifier(prompt: str) -> str:
    return "Summary of: " + prompt[:50]


def _make_hook_context() -> HookContext:
    return HookContext(botId="test-bot")


def make_messages_at_utilization(
    target: float,
    model: str = "claude-opus-4-6",
) -> list[dict]:
    """Build a message list whose estimated token count hits *target* utilization.

    Token estimate: ``len(json.dumps(msg)) // 4``
    context_window for claude-opus-4-6 = 150_000 tokens.
    """
    context_window = 150_000
    target_tokens = int(context_window * target)
    # json.dumps adds ~30 chars of overhead for {"role":"user","content":"..."}
    # We overshoot slightly to ensure we cross the threshold
    content_size = target_tokens * 4 - 30
    return [{"role": "user", "content": "x" * max(content_size, 1)}]


def make_tool_result_messages_at_utilization(
    target: float,
    model: str = "claude-opus-4-6",
    *,
    num_tool_results: int = 1,
    lines_per_result: int = 200,
) -> list[dict]:
    """Build messages with large tool results that hit *target* utilization.

    Tool results have many lines so ContentReplacer can snip them.
    """
    context_window = 150_000
    target_tokens = int(context_window * target)
    # Reserve some tokens for user and assistant messages
    user_msg = {"role": "user", "content": "Please analyze this data"}
    assistant_msg = {"role": "assistant", "content": "Let me check."}

    overhead_tokens = (
        len(json.dumps(user_msg)) // 4 + len(json.dumps(assistant_msg)) // 4
    )
    remaining_tokens = target_tokens - overhead_tokens
    tokens_per_result = remaining_tokens // max(num_tool_results, 1)

    # Each line is ~chars_per_line chars, json.dumps overhead ~50 chars
    chars_per_result = tokens_per_result * 4 - 50
    chars_per_line = max(chars_per_result // lines_per_result, 10)

    messages: list[dict] = [user_msg, assistant_msg]
    for i in range(num_tool_results):
        content_lines = [f"line-{j}: " + "d" * chars_per_line for j in range(lines_per_result)]
        messages.append({
            "role": "tool",
            "tool_use_id": f"tool_{i}",
            "content": "\n".join(content_lines),
        })
    return messages


def make_multi_turn_messages_at_utilization(
    target: float,
    model: str = "claude-opus-4-6",
    *,
    num_turns: int = 6,
) -> list[dict]:
    """Build a multi-turn conversation that hits *target* utilization.

    Each turn = user + assistant + tool result. Needed for auto compact
    which requires >= 4 user turns to have something to compact.
    """
    context_window = 150_000
    target_tokens = int(context_window * target)
    tokens_per_turn = target_tokens // num_turns
    chars_per_msg = (tokens_per_turn * 4) // 3  # 3 messages per turn

    messages: list[dict] = []
    for i in range(num_turns):
        messages.append({"role": "user", "content": f"Turn {i}: " + "u" * chars_per_msg})
        messages.append({"role": "assistant", "content": f"Response {i}: " + "a" * chars_per_msg})
        lines = [f"line-{j}: " + "d" * (chars_per_msg // 200) for j in range(200)]
        messages.append({
            "role": "tool",
            "tool_use_id": f"tool_turn_{i}",
            "content": "\n".join(lines),
        })
    return messages


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestManifest:
    def test_manifest_name_and_point(self) -> None:
        m = make_context_management_manifest()
        assert m.name == HOOK_NAME
        assert m.name == "context_management"
        assert m.point == HookPoint.BEFORE_LLM_CALL

    def test_manifest_priority(self) -> None:
        m = make_context_management_manifest()
        assert m.priority == HOOK_PRIORITY
        assert m.priority == 10

    def test_manifest_source(self) -> None:
        m = make_context_management_manifest()
        assert m.source.kind == "builtin"
        assert m.source.package == "context_management"

    def test_manifest_non_blocking_fail_open(self) -> None:
        m = make_context_management_manifest()
        assert m.blocking is False
        assert m.fail_open is True

    def test_manifest_timeout(self) -> None:
        m = make_context_management_manifest()
        assert m.timeout_ms == 30_000

    def test_hook_instance_manifest(self) -> None:
        hook = ContextManagementHook(model="claude-opus-4-6")
        m = hook.manifest
        assert m.name == "context_management"
        assert m.point == HookPoint.BEFORE_LLM_CALL


class TestHookCall:
    @pytest.mark.asyncio
    async def test_call_returns_continue(self) -> None:
        hook = ContextManagementHook(model="claude-opus-4-6")
        result = await hook(_make_hook_context())
        assert result.action == "continue"

    @pytest.mark.asyncio
    async def test_call_is_non_blocking(self) -> None:
        """Hook __call__ never blocks — always returns continue."""
        hook = ContextManagementHook(
            classifier=mock_classifier,
            model="claude-opus-4-6",
        )
        result = await hook(_make_hook_context())
        assert result.action == "continue"


class TestPipelineEmpty:
    @pytest.mark.asyncio
    async def test_empty_messages(self) -> None:
        hook = ContextManagementHook(
            model="claude-opus-4-6",
            config=ContextManagementConfig(enabled=True),
        )
        messages, result = await hook.run_pipeline([])
        assert messages == []
        assert result.warning_level == WarningLevel.NORMAL
        assert result.messages_before == 0
        assert result.messages_after == 0
        assert result.content_replacement_applied is False
        assert result.microcompact_applied is False
        assert result.auto_compact_applied is False


class TestPipelineNormal:
    @pytest.mark.asyncio
    async def test_small_session_no_tiers_activate(self) -> None:
        hook = ContextManagementHook(
            classifier=mock_classifier,
            config=ContextManagementConfig(enabled=True),
            model="claude-opus-4-6",
        )
        messages = [
            {"role": "user", "content": "Hello, how are you?"},
            {"role": "assistant", "content": "I'm doing well!"},
        ]
        result_messages, result = await hook.run_pipeline(messages)
        assert result.warning_level == WarningLevel.NORMAL
        assert result.content_replacement_applied is False
        assert result.microcompact_applied is False
        assert result.auto_compact_applied is False
        assert result.messages_before == 2
        assert result.messages_after == 2
        assert result_messages == messages


class TestPipelineModerate:
    @pytest.mark.asyncio
    async def test_moderate_level_only_content_replacement(self) -> None:
        """At MODERATE, only content replacement/snip runs."""
        hook = ContextManagementHook(
            classifier=mock_classifier,
            config=ContextManagementConfig(enabled=True),
            model="claude-opus-4-6",
        )
        # Build messages at ~62% utilization with large tool results
        messages = make_tool_result_messages_at_utilization(0.62)
        _, result = await hook.run_pipeline(messages)
        assert result.warning_level == WarningLevel.MODERATE
        # Content replacement should have been attempted
        # (may or may not snip depending on tool result size)
        assert result.microcompact_applied is False
        assert result.auto_compact_applied is False


class TestPipelineHigh:
    @pytest.mark.asyncio
    async def test_high_level_content_replacement_and_microcompact(self) -> None:
        """At HIGH, content replacement + microcompact run."""
        hook = ContextManagementHook(
            classifier=mock_classifier,
            config=ContextManagementConfig(enabled=True),
            model="claude-opus-4-6",
        )
        messages = make_tool_result_messages_at_utilization(0.77, num_tool_results=3)
        _, result = await hook.run_pipeline(messages)
        assert result.warning_level == WarningLevel.HIGH
        assert result.auto_compact_applied is False
        # Microcompact should have been attempted on tool results
        # (actual compaction depends on tool result token count vs threshold)


class TestPipelineCritical:
    @pytest.mark.asyncio
    async def test_critical_level_all_tiers_run(self) -> None:
        """At CRITICAL, all tiers run including auto compact."""
        hook = ContextManagementHook(
            classifier=mock_classifier,
            config=ContextManagementConfig(enabled=True),
            model="claude-opus-4-6",
        )
        messages = make_multi_turn_messages_at_utilization(0.92, num_turns=6)
        _, result = await hook.run_pipeline(messages)
        assert result.warning_level == WarningLevel.CRITICAL
        # Auto compact should activate (6 turns, keeps 3 recent)
        assert result.auto_compact_applied is True
        assert result.auto_compact_turns_summarized > 0


class TestNoClassifier:
    @pytest.mark.asyncio
    async def test_no_classifier_skips_microcompact_and_autocompact(self) -> None:
        """Without a classifier, microcompact and auto compact are None."""
        hook = ContextManagementHook(
            model="claude-opus-4-6",
            config=ContextManagementConfig(enabled=True),
        )
        assert hook._microcompact is None
        assert hook._auto_compact is None

        # Even at CRITICAL, pipeline completes without error
        messages = make_multi_turn_messages_at_utilization(0.92, num_turns=6)
        _, result = await hook.run_pipeline(messages)
        assert result.warning_level == WarningLevel.CRITICAL
        assert result.microcompact_applied is False
        assert result.auto_compact_applied is False


class TestConfigFromEnv:
    def test_config_when_disabled(self) -> None:
        with patch.dict(
            os.environ,
            {
                "MAGI_CONTEXT_MGMT_ENABLED": "0",
                "MAGI_CONTEXT_PROACTIVE_RECOVERY_ENABLED": "0",
            },
            clear=True,
        ):
            config = load_config_from_env()
        assert config.enabled is False
        assert config.moderate_threshold == 0.60
        assert config.high_threshold == 0.75
        assert config.critical_threshold == 0.90

    def test_custom_env_thresholds(self) -> None:
        env = {
            "MAGI_CONTEXT_MODERATE_THRESHOLD": "0.50",
            "MAGI_CONTEXT_HIGH_THRESHOLD": "0.70",
            "MAGI_CONTEXT_CRITICAL_THRESHOLD": "0.85",
        }
        with patch.dict(os.environ, env, clear=True):
            config = load_config_from_env()
        assert config.moderate_threshold == 0.50
        assert config.high_threshold == 0.70
        assert config.critical_threshold == 0.85


class TestCustomConfig:
    @pytest.mark.asyncio
    async def test_custom_thresholds_change_activation(self) -> None:
        """Custom config with lower thresholds activates earlier."""
        config = ContextManagementConfig(
            enabled=True,
            moderate_threshold=0.30,
            high_threshold=0.40,
            critical_threshold=0.50,
        )
        hook = ContextManagementHook(
            classifier=mock_classifier,
            config=config,
            model="claude-opus-4-6",
        )
        # 35% utilization — would be NORMAL with defaults, MODERATE with custom
        messages = make_messages_at_utilization(0.35)
        _, result = await hook.run_pipeline(messages)
        assert result.warning_level == WarningLevel.MODERATE


class TestPipelineResultStats:
    @pytest.mark.asyncio
    async def test_result_stats_normal(self) -> None:
        hook = ContextManagementHook(
            classifier=mock_classifier,
            config=ContextManagementConfig(enabled=True),
            model="claude-opus-4-6",
        )
        messages = [{"role": "user", "content": "hi"}]
        _, result = await hook.run_pipeline(messages)
        assert result.warning_level == WarningLevel.NORMAL
        assert result.snip_tokens_freed == 0
        assert result.microcompact_cache_hits == 0
        assert result.microcompact_tokens_freed == 0
        assert result.auto_compact_turns_summarized == 0
        assert result.messages_before == 1
        assert result.messages_after == 1

    @pytest.mark.asyncio
    async def test_result_stats_critical_with_compaction(self) -> None:
        hook = ContextManagementHook(
            classifier=mock_classifier,
            config=ContextManagementConfig(enabled=True),
            model="claude-opus-4-6",
        )
        messages = make_multi_turn_messages_at_utilization(0.92, num_turns=6)
        _, result = await hook.run_pipeline(messages)
        assert result.warning_level == WarningLevel.CRITICAL
        assert result.messages_before > result.messages_after
        assert result.auto_compact_applied is True


class TestPipelineIdempotent:
    @pytest.mark.asyncio
    async def test_pipeline_idempotent_on_normal(self) -> None:
        """Running pipeline twice on NORMAL-level messages produces same result."""
        hook = ContextManagementHook(
            classifier=mock_classifier,
            config=ContextManagementConfig(enabled=True),
            model="claude-opus-4-6",
        )
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
        ]
        msgs1, r1 = await hook.run_pipeline(messages)
        msgs2, r2 = await hook.run_pipeline(msgs1)
        assert msgs1 == msgs2
        assert r1.warning_level == r2.warning_level
        assert r1.messages_after == r2.messages_after


class TestProgressivePipeline:
    @pytest.mark.asyncio
    async def test_progressive_tier_activation(self) -> None:
        """Add messages until each tier activates in order."""
        hook = ContextManagementHook(
            classifier=mock_classifier,
            config=ContextManagementConfig(enabled=True),
            model="claude-opus-4-6",
        )

        # Under 60% — NORMAL
        small = make_messages_at_utilization(0.40)
        _, r = await hook.run_pipeline(small)
        assert r.warning_level == WarningLevel.NORMAL

        # 60-75% — MODERATE
        moderate = make_messages_at_utilization(0.65)
        _, r = await hook.run_pipeline(moderate)
        assert r.warning_level == WarningLevel.MODERATE

        # 75-90% — HIGH
        high = make_messages_at_utilization(0.80)
        _, r = await hook.run_pipeline(high)
        assert r.warning_level == WarningLevel.HIGH

        # Over 90% — CRITICAL
        critical = make_messages_at_utilization(0.92)
        _, r = await hook.run_pipeline(critical)
        assert r.warning_level == WarningLevel.CRITICAL


class TestPipelineResultFrozen:
    def test_pipeline_result_is_frozen(self) -> None:
        result = PipelineResult(
            warning_level=WarningLevel.NORMAL,
            content_replacement_applied=False,
            snip_tokens_freed=0,
            microcompact_applied=False,
            microcompact_cache_hits=0,
            microcompact_tokens_freed=0,
            auto_compact_applied=False,
            auto_compact_turns_summarized=0,
            messages_before=0,
            messages_after=0,
        )
        with pytest.raises(AttributeError):
            result.warning_level = WarningLevel.HIGH  # type: ignore[misc]


class TestPerTierFailOpen:
    """Fix 1: Each tier is wrapped in try/except so a single tier failure
    does not abort the whole pipeline."""

    @pytest.mark.asyncio
    async def test_content_replacer_raises_pipeline_continues(self) -> None:
        """If ContentReplacer.apply raises, pipeline continues with original messages."""
        hook = ContextManagementHook(
            config=ContextManagementConfig(enabled=True),
            model="claude-opus-4-6",
        )
        # Patch ContentReplacer.apply to raise
        hook._replacer.apply = MagicMock(side_effect=RuntimeError("replacer boom"))

        messages = make_tool_result_messages_at_utilization(0.65)
        result_messages, result = await hook.run_pipeline(messages)

        # Pipeline must not raise and must return messages unchanged
        assert result_messages == messages
        assert result.warning_level == WarningLevel.MODERATE
        assert result.content_replacement_applied is False

    @pytest.mark.asyncio
    async def test_microcompact_raises_pipeline_continues(self) -> None:
        """If MicrocompactEngine.apply raises, pipeline continues."""

        async def failing_classifier(prompt: str) -> str:
            return "summary"

        hook = ContextManagementHook(
            classifier=failing_classifier,
            config=ContextManagementConfig(enabled=True),
            model="claude-opus-4-6",
        )
        assert hook._microcompact is not None
        hook._microcompact.apply = AsyncMock(side_effect=RuntimeError("microcompact boom"))

        messages = make_tool_result_messages_at_utilization(0.77, num_tool_results=3)
        result_messages, result = await hook.run_pipeline(messages)

        assert result.warning_level == WarningLevel.HIGH
        assert result.microcompact_applied is False

    @pytest.mark.asyncio
    async def test_auto_compact_raises_pipeline_continues(self) -> None:
        """If AutoCompactionEngine.apply raises, pipeline continues."""

        async def ok_classifier(prompt: str) -> str:
            return "summary"

        hook = ContextManagementHook(
            classifier=ok_classifier,
            config=ContextManagementConfig(enabled=True),
            model="claude-opus-4-6",
        )
        assert hook._auto_compact is not None
        hook._auto_compact.apply = AsyncMock(side_effect=RuntimeError("auto_compact boom"))

        messages = make_multi_turn_messages_at_utilization(0.92, num_turns=6)
        result_messages, result = await hook.run_pipeline(messages)

        assert result.warning_level == WarningLevel.CRITICAL
        assert result.auto_compact_applied is False


class TestCompactionCallbacks:
    """Fix 2: on_before_compaction / on_after_compaction fire around Tier 5."""

    @pytest.mark.asyncio
    async def test_callbacks_fire_around_auto_compact(self) -> None:
        """Both callbacks are awaited when auto compact activates."""
        call_order: list[str] = []

        async def before() -> None:
            call_order.append("before")

        async def after() -> None:
            call_order.append("after")

        hook = ContextManagementHook(
            classifier=mock_classifier,
            config=ContextManagementConfig(enabled=True),
            model="claude-opus-4-6",
            on_before_compaction=before,
            on_after_compaction=after,
        )

        messages = make_multi_turn_messages_at_utilization(0.92, num_turns=6)
        _, result = await hook.run_pipeline(messages)

        assert result.warning_level == WarningLevel.CRITICAL
        assert result.auto_compact_applied is True
        assert call_order == ["before", "after"]

    @pytest.mark.asyncio
    async def test_callbacks_not_called_below_critical(self) -> None:
        """Callbacks are not fired when warning level < CRITICAL."""
        fired: list[str] = []

        async def before() -> None:
            fired.append("before")

        async def after() -> None:
            fired.append("after")

        hook = ContextManagementHook(
            classifier=mock_classifier,
            config=ContextManagementConfig(enabled=True),
            model="claude-opus-4-6",
            on_before_compaction=before,
            on_after_compaction=after,
        )

        # Use HIGH-level messages — no auto compact
        messages = make_tool_result_messages_at_utilization(0.77, num_tool_results=3)
        _, result = await hook.run_pipeline(messages)

        assert result.warning_level == WarningLevel.HIGH
        assert fired == []

    @pytest.mark.asyncio
    async def test_no_callbacks_is_fine(self) -> None:
        """Hook works without callbacks (default None)."""
        hook = ContextManagementHook(
            classifier=mock_classifier,
            config=ContextManagementConfig(enabled=True),
            model="claude-opus-4-6",
        )
        messages = make_multi_turn_messages_at_utilization(0.92, num_turns=6)
        _, result = await hook.run_pipeline(messages)
        assert result.auto_compact_applied is True


class TestEnabledGating:
    """Fix 3: Pipeline is a no-op when enabled=False."""

    @pytest.mark.asyncio
    async def test_disabled_returns_messages_unchanged(self) -> None:
        """When enabled=False, pipeline returns original messages with NORMAL level."""
        hook = ContextManagementHook(
            classifier=mock_classifier,
            config=ContextManagementConfig(enabled=False),
            model="claude-opus-4-6",
        )
        # Even CRITICAL-level messages should be untouched
        messages = make_multi_turn_messages_at_utilization(0.92, num_turns=6)
        result_messages, result = await hook.run_pipeline(messages)

        assert result_messages is messages  # same object, not a copy
        assert result.warning_level == WarningLevel.NORMAL
        assert result.content_replacement_applied is False
        assert result.microcompact_applied is False
        assert result.auto_compact_applied is False
        assert result.messages_before == len(messages)
        assert result.messages_after == len(messages)

    @pytest.mark.asyncio
    async def test_enabled_true_runs_pipeline(self) -> None:
        """When enabled=True, pipeline processes messages normally."""
        hook = ContextManagementHook(
            classifier=mock_classifier,
            config=ContextManagementConfig(enabled=True),
            model="claude-opus-4-6",
        )
        messages = make_multi_turn_messages_at_utilization(0.92, num_turns=6)
        _, result = await hook.run_pipeline(messages)
        # Pipeline ran — warning level reflects actual utilization
        assert result.warning_level == WarningLevel.CRITICAL


class TestEnabledEnvVar:
    """Fix 3: MAGI_CONTEXT_MGMT_ENABLED env var controls enabled flag."""

    def test_env_var_1_enables(self) -> None:
        with patch.dict(os.environ, {"MAGI_CONTEXT_MGMT_ENABLED": "1"}, clear=True):
            config = load_config_from_env()
        assert config.enabled is True

    def test_env_var_true_enables(self) -> None:
        with patch.dict(os.environ, {"MAGI_CONTEXT_MGMT_ENABLED": "true"}, clear=True):
            config = load_config_from_env()
        assert config.enabled is True

    def test_env_var_True_enables(self) -> None:
        with patch.dict(os.environ, {"MAGI_CONTEXT_MGMT_ENABLED": "True"}, clear=True):
            config = load_config_from_env()
        assert config.enabled is True

    def test_env_var_yes_enables(self) -> None:
        with patch.dict(os.environ, {"MAGI_CONTEXT_MGMT_ENABLED": "yes"}, clear=True):
            config = load_config_from_env()
        assert config.enabled is True

    def test_env_var_0_disables(self) -> None:
        with patch.dict(os.environ, {"MAGI_CONTEXT_MGMT_ENABLED": "0"}, clear=True):
            config = load_config_from_env()
        assert config.enabled is False

    def test_env_var_missing_under_safe_profile_disables(self) -> None:
        # Promoted to profile-aware default-ON: missing resolves OFF only under a
        # safe runtime profile (full profile self-enables).
        with patch.dict(
            os.environ, {"MAGI_RUNTIME_PROFILE": "safe"}, clear=True
        ):
            config = load_config_from_env()
        assert config.enabled is False
