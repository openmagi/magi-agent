"""Tests for Tier 6-7: proactive recovery in ContextManagementHook pipeline."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from magi_agent.context.hook import (
    ContextManagementHook,
    PipelineResult,
    _make_proactive_recovery_context,
)
from magi_agent.context.types import ContextManagementConfig, WarningLevel
from magi_agent.runtime.error_recovery.types import (
    ErrorKind,
    RecoveryContext,
    RecoveryResult,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _mock_classifier(prompt: str) -> str:
    return "Summary of: " + prompt[:50]


def _make_critical_multi_turn_messages(
    num_turns: int = 8,
    model: str = "claude-opus-4-6",
) -> list[dict]:
    """Build a multi-turn conversation that hits CRITICAL (>90%) utilization.

    Each turn = user + assistant + tool result.
    context_window for claude-opus-4-6 = 150_000 tokens.
    Token estimate: len(json.dumps(msg)) // 4
    """
    context_window = 150_000
    target_tokens = int(context_window * 0.93)
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


def _proactive_config(*, enabled: bool = True) -> ContextManagementConfig:
    return ContextManagementConfig(
        enabled=True,
        proactive_recovery_enabled=enabled,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestProactiveDisabled:
    @pytest.mark.asyncio
    async def test_tier67_do_not_fire_when_proactive_disabled(self) -> None:
        """Proactive disabled -> Tier 6-7 don't fire even at CRITICAL."""
        hook = ContextManagementHook(
            classifier=_mock_classifier,
            config=_proactive_config(enabled=False),
            model="claude-opus-4-6",
        )
        messages = _make_critical_multi_turn_messages()
        _, result = await hook.run_pipeline(messages)
        assert result.warning_level == WarningLevel.CRITICAL
        assert result.proactive_collapse_applied is False
        assert result.proactive_collapse_tokens_freed == 0
        assert result.proactive_compact_applied is False
        assert result.proactive_compact_tokens_freed == 0


class TestProactiveNotCritical:
    @pytest.mark.asyncio
    async def test_tier67_do_not_fire_below_critical(self) -> None:
        """Proactive enabled + not CRITICAL -> Tier 6-7 don't fire."""
        hook = ContextManagementHook(
            classifier=_mock_classifier,
            config=_proactive_config(enabled=True),
            model="claude-opus-4-6",
        )
        # Small messages -> NORMAL
        messages = [{"role": "user", "content": "hello"}]
        _, result = await hook.run_pipeline(messages)
        assert result.warning_level == WarningLevel.NORMAL
        assert result.proactive_collapse_applied is False
        assert result.proactive_compact_applied is False


class TestTier5ReducesBelowCritical:
    @pytest.mark.asyncio
    async def test_tier67_skip_when_tier5_reduces_to_non_critical(self) -> None:
        """Proactive enabled + CRITICAL, but Tier 5 reduces below CRITICAL -> no Tier 6-7."""
        hook = ContextManagementHook(
            classifier=_mock_classifier,
            config=_proactive_config(enabled=True),
            model="claude-opus-4-6",
        )

        # Mock auto_compact to return very small messages (drops below CRITICAL)
        small_messages = [
            {"role": "user", "content": "summary"},
            {"role": "assistant", "content": "ok"},
        ]

        class FakeAcResult:
            activated = True
            turns_summarized = 5

        hook._auto_compact.apply = AsyncMock(return_value=(small_messages, FakeAcResult()))

        messages = _make_critical_multi_turn_messages()
        result_messages, result = await hook.run_pipeline(messages)

        assert result.warning_level == WarningLevel.CRITICAL  # original warning level
        assert result.auto_compact_applied is True
        # After Tier 5 reduced to non-CRITICAL, Tier 6-7 should NOT fire
        assert result.proactive_collapse_applied is False
        assert result.proactive_compact_applied is False


class TestTier6Fires:
    @pytest.mark.asyncio
    async def test_collapse_drain_fires_when_still_critical_after_tier5(self) -> None:
        """Proactive enabled + still CRITICAL after Tier 5 -> Tier 6 fires."""
        hook = ContextManagementHook(
            classifier=_mock_classifier,
            config=_proactive_config(enabled=True),
            model="claude-opus-4-6",
        )

        # Mock auto_compact to return messages still at CRITICAL
        critical_msgs = _make_critical_multi_turn_messages()

        class FakeAcResult:
            activated = True
            turns_summarized = 2

        hook._auto_compact.apply = AsyncMock(return_value=(critical_msgs, FakeAcResult()))

        messages = _make_critical_multi_turn_messages()
        _, result = await hook.run_pipeline(messages)

        assert result.warning_level == WarningLevel.CRITICAL
        # Collapse drain should have fired (messages have enough rounds)
        assert result.proactive_collapse_applied is True
        assert result.proactive_collapse_tokens_freed > 0


class TestTier7Fires:
    @pytest.mark.asyncio
    async def test_reactive_compact_fires_when_still_critical_after_tier6(self) -> None:
        """Proactive enabled + Tier 6 fires but still CRITICAL -> Tier 7 fires."""
        hook = ContextManagementHook(
            classifier=_mock_classifier,
            config=_proactive_config(enabled=True),
            model="claude-opus-4-6",
        )

        # Mock auto_compact to return messages still at CRITICAL
        critical_msgs = _make_critical_multi_turn_messages(num_turns=10)

        class FakeAcResult:
            activated = True
            turns_summarized = 2

        hook._auto_compact.apply = AsyncMock(return_value=(critical_msgs, FakeAcResult()))

        # Mock collapse drain to succeed but return messages still at CRITICAL
        still_critical = _make_critical_multi_turn_messages(num_turns=10)
        hook._collapse_drain.recover = AsyncMock(return_value=RecoveryResult(
            success=True,
            strategy_name="collapse_drain",
            modified_messages=still_critical,
            tokens_freed=1000,
        ))

        messages = _make_critical_multi_turn_messages(num_turns=10)
        _, result = await hook.run_pipeline(messages)

        assert result.warning_level == WarningLevel.CRITICAL
        assert result.proactive_collapse_applied is True
        # After collapse, still CRITICAL -> Tier 7 fires
        assert result.proactive_compact_applied is True
        assert result.proactive_compact_tokens_freed > 0


class TestTier6ResultStats:
    @pytest.mark.asyncio
    async def test_collapse_result_stats_in_pipeline_result(self) -> None:
        """PipelineResult correctly reports Tier 6 stats."""
        hook = ContextManagementHook(
            classifier=_mock_classifier,
            config=_proactive_config(enabled=True),
            model="claude-opus-4-6",
        )

        critical_msgs = _make_critical_multi_turn_messages()

        class FakeAcResult:
            activated = False
            turns_summarized = 0

        hook._auto_compact.apply = AsyncMock(return_value=(critical_msgs, FakeAcResult()))

        messages = _make_critical_multi_turn_messages()
        _, result = await hook.run_pipeline(messages)

        assert result.proactive_collapse_applied is True
        assert result.proactive_collapse_tokens_freed > 0
        assert isinstance(result.proactive_collapse_tokens_freed, int)


class TestTier7ResultStats:
    @pytest.mark.asyncio
    async def test_compact_result_stats_in_pipeline_result(self) -> None:
        """PipelineResult correctly reports Tier 7 stats."""
        hook = ContextManagementHook(
            classifier=_mock_classifier,
            config=_proactive_config(enabled=True),
            model="claude-opus-4-6",
        )

        critical_msgs = _make_critical_multi_turn_messages(num_turns=10)

        class FakeAcResult:
            activated = True
            turns_summarized = 2

        hook._auto_compact.apply = AsyncMock(return_value=(critical_msgs, FakeAcResult()))

        # Mock collapse drain to succeed but return still-CRITICAL messages
        still_critical = _make_critical_multi_turn_messages(num_turns=10)
        hook._collapse_drain.recover = AsyncMock(return_value=RecoveryResult(
            success=True,
            strategy_name="collapse_drain",
            modified_messages=still_critical,
            tokens_freed=500,
        ))

        messages = _make_critical_multi_turn_messages(num_turns=10)
        _, result = await hook.run_pipeline(messages)

        assert result.proactive_compact_applied is True
        assert result.proactive_compact_tokens_freed > 0
        assert isinstance(result.proactive_compact_tokens_freed, int)


class TestCollapseDrainFailOpen:
    @pytest.mark.asyncio
    async def test_collapse_drain_failure_is_fail_open(self) -> None:
        """Collapse drain strategy failure -> fail-open, Tier 7 still tries."""
        hook = ContextManagementHook(
            classifier=_mock_classifier,
            config=_proactive_config(enabled=True),
            model="claude-opus-4-6",
        )

        critical_msgs = _make_critical_multi_turn_messages()

        class FakeAcResult:
            activated = False
            turns_summarized = 0

        hook._auto_compact.apply = AsyncMock(return_value=(critical_msgs, FakeAcResult()))

        # Make collapse drain raise
        hook._collapse_drain.recover = AsyncMock(side_effect=RuntimeError("collapse boom"))

        messages = _make_critical_multi_turn_messages()
        # Should not raise
        result_messages, result = await hook.run_pipeline(messages)

        assert result.proactive_collapse_applied is False
        assert result.proactive_collapse_tokens_freed == 0
        # Tier 7 DOES fire regardless of Tier 6 outcome — re-check is unconditional.
        # Since messages are still CRITICAL after Tier 6 failed, Tier 7 activates.


class TestReactiveCompactFailOpen:
    @pytest.mark.asyncio
    async def test_reactive_compact_failure_preserves_messages(self) -> None:
        """Reactive compact failure -> fail-open, original messages preserved."""
        hook = ContextManagementHook(
            classifier=_mock_classifier,
            config=_proactive_config(enabled=True),
            model="claude-opus-4-6",
        )

        critical_msgs = _make_critical_multi_turn_messages(num_turns=10)

        class FakeAcResult:
            activated = True
            turns_summarized = 2

        hook._auto_compact.apply = AsyncMock(return_value=(critical_msgs, FakeAcResult()))

        # Make reactive compact raise
        hook._reactive_compact.recover = AsyncMock(side_effect=RuntimeError("compact boom"))

        messages = _make_critical_multi_turn_messages(num_turns=10)
        result_messages, result = await hook.run_pipeline(messages)

        # Collapse should succeed, compact should fail-open
        assert result.proactive_collapse_applied is True
        assert result.proactive_compact_applied is False
        assert result.proactive_compact_tokens_freed == 0


class TestPipelineResultBackwardCompat:
    def test_new_fields_have_defaults(self) -> None:
        """PipelineResult backward compat: new Tier 6-7 fields have defaults."""
        result = PipelineResult(
            warning_level=WarningLevel.NORMAL,
            content_replacement_applied=False,
            snip_tokens_freed=0,
            microcompact_applied=False,
            microcompact_cache_hits=0,
            microcompact_tokens_freed=0,
            auto_compact_applied=False,
            auto_compact_turns_summarized=0,
            messages_before=5,
            messages_after=5,
        )
        # New fields should have default values
        assert result.proactive_collapse_applied is False
        assert result.proactive_collapse_tokens_freed == 0
        assert result.proactive_compact_applied is False
        assert result.proactive_compact_tokens_freed == 0


class TestMakeProactiveRecoveryContext:
    def test_creates_valid_recovery_context(self) -> None:
        """_make_proactive_recovery_context creates valid RecoveryContext."""
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        ctx = _make_proactive_recovery_context(messages)
        assert ctx.error.kind == ErrorKind.PROMPT_TOO_LONG
        assert ctx.error.original_error == "proactive_context_management"
        assert ctx.messages == messages
        assert ctx.session_key == "proactive"
        assert ctx.turn_id == "proactive"

    def test_custom_session_key_and_turn_id(self) -> None:
        """_make_proactive_recovery_context accepts custom session_key and turn_id."""
        ctx = _make_proactive_recovery_context(
            [{"role": "user", "content": "x"}],
            session_key="sess-123",
            turn_id="turn-456",
        )
        assert ctx.session_key == "sess-123"
        assert ctx.turn_id == "turn-456"


class TestProactiveConfigFromEnv:
    def test_proactive_env_var_enables(self) -> None:
        """MAGI_CONTEXT_PROACTIVE_RECOVERY_ENABLED=1 enables proactive recovery."""
        from magi_agent.context.hook import load_config_from_env

        env = {
            "MAGI_CONTEXT_MGMT_ENABLED": "1",
            "MAGI_CONTEXT_PROACTIVE_RECOVERY_ENABLED": "1",
        }
        with patch("os.environ", env):
            config = load_config_from_env()
        assert config.proactive_recovery_enabled is True

    def test_proactive_env_var_default_disabled(self) -> None:
        """MAGI_CONTEXT_PROACTIVE_RECOVERY_ENABLED absent -> disabled."""
        from magi_agent.context.hook import load_config_from_env

        env = {"MAGI_CONTEXT_MGMT_ENABLED": "1"}
        with patch("os.environ", env):
            config = load_config_from_env()
        assert config.proactive_recovery_enabled is False


class TestProactiveConfigDefault:
    def test_proactive_recovery_disabled_by_default(self) -> None:
        """ContextManagementConfig defaults proactive_recovery_enabled=False."""
        config = ContextManagementConfig(enabled=True)
        assert config.proactive_recovery_enabled is False
