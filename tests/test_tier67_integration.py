"""Integration tests: full 7-tier context management pipeline.

PR4 — End-to-end demonstration of the complete tier progression,
proactive recovery gating, shared token estimation parity, and
backward-compatible PipelineResult defaults.

All tests use asyncio.run() — NOT @pytest.mark.asyncio — so that
the suite runs without pytest-asyncio installed.
"""

from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock

from openmagi_core_agent.context.hook import (
    ContextManagementHook,
    PipelineResult,
    load_config_from_env,
)
from openmagi_core_agent.context.token_tracker import TokenBudgetTracker
from openmagi_core_agent.context.types import ContextManagementConfig, WarningLevel
from openmagi_core_agent.runtime.error_recovery.types import RecoveryResult
from openmagi_core_agent.shared.token_estimation import (
    estimate_message_tokens,
    estimate_messages_tokens,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def mock_classifier(prompt: str) -> str:
    return "Summary: " + prompt[:100]


class MockLLMCompactCaller:
    async def compact(self, messages_text: str, prompt: str) -> str:
        return f"[Compacted {len(messages_text)} chars]"


def make_messages(target_utilization: float, context_window: int = 150_000) -> list[dict]:
    """Create a single-message list that hits a target utilization level."""
    target_tokens = int(context_window * target_utilization)
    # json.dumps overhead ~30 chars for {"role":"user","content":"..."}
    content_size = max(1, target_tokens * 4 - 30)
    return [{"role": "user", "content": "x" * content_size}]


def make_multi_turn_messages(
    target_utilization: float,
    num_turns: int = 8,
    context_window: int = 150_000,
) -> list[dict]:
    """Create multi-turn conversation at target utilization.

    Mirrors _make_critical_multi_turn_messages() from test_tier67_pipeline.py
    to ensure consistent CRITICAL utilization.  Each turn = user + assistant
    + tool result.  The formula distributes target_tokens across all messages
    proportionally.
    """
    target_tokens = int(context_window * target_utilization)
    tokens_per_turn = target_tokens // num_turns
    # 3 message slots per turn (user + assistant + tool)
    chars_per_msg = (tokens_per_turn * 4) // 3

    messages: list[dict] = []
    for i in range(num_turns):
        messages.append({"role": "user", "content": f"Turn {i}: " + "u" * chars_per_msg})
        messages.append({"role": "assistant", "content": f"Response {i}: " + "a" * chars_per_msg})
        # Tool result: many lines so ContentReplacer can operate on it
        lines = [f"line-{j}: " + "d" * max(1, chars_per_msg // 200) for j in range(200)]
        messages.append({
            "role": "tool",
            "tool_use_id": f"tool_turn_{i}",
            "content": "\n".join(lines),
        })
    return messages


def _make_full_config(*, proactive: bool = True) -> ContextManagementConfig:
    return ContextManagementConfig(
        enabled=True,
        moderate_threshold=0.60,
        high_threshold=0.75,
        critical_threshold=0.90,
        proactive_recovery_enabled=proactive,
    )


# ---------------------------------------------------------------------------
# Test 1: Full 7-tier progression
# ---------------------------------------------------------------------------

def test_full_7_tier_progression() -> None:
    """Demonstrate full tier progression from NORMAL through CRITICAL.

    Each tier activates only at its threshold:
    - NORMAL (< 60%): no tiers fire
    - MODERATE (60-75%): Tiers 2-3 eligible (content replacement)
    - HIGH (75-90%): Tiers 2-4 eligible (+ microcompact)
    - CRITICAL (> 90%): All 5 base tiers available; proactive disabled here
    """

    async def _run() -> None:
        hook = ContextManagementHook(
            classifier=mock_classifier,
            config=_make_full_config(proactive=False),
            model="claude-opus-4-6",
        )

        # --- NORMAL (10%) ---
        msgs_normal = make_messages(0.10)
        _, result_normal = await hook.run_pipeline(msgs_normal)
        assert result_normal.warning_level == WarningLevel.NORMAL
        assert result_normal.content_replacement_applied is False
        assert result_normal.microcompact_applied is False
        assert result_normal.auto_compact_applied is False
        assert result_normal.proactive_collapse_applied is False
        assert result_normal.proactive_compact_applied is False

        # --- MODERATE (65%) ---
        msgs_moderate = make_messages(0.65)
        _, result_moderate = await hook.run_pipeline(msgs_moderate)
        assert result_moderate.warning_level == WarningLevel.MODERATE
        # Microcompact and auto-compact must NOT fire at MODERATE
        assert result_moderate.microcompact_applied is False
        assert result_moderate.auto_compact_applied is False
        assert result_moderate.proactive_collapse_applied is False
        assert result_moderate.proactive_compact_applied is False

        # --- HIGH (80%) ---
        msgs_high = make_messages(0.80)
        _, result_high = await hook.run_pipeline(msgs_high)
        assert result_high.warning_level == WarningLevel.HIGH
        # Auto compact must NOT fire at HIGH (only at CRITICAL)
        assert result_high.auto_compact_applied is False
        assert result_high.proactive_collapse_applied is False
        assert result_high.proactive_compact_applied is False

        # --- CRITICAL (93%) --- proactive disabled, so Tiers 6-7 must stay False
        msgs_critical = make_multi_turn_messages(0.93, num_turns=8)
        _, result_critical = await hook.run_pipeline(msgs_critical)
        assert result_critical.warning_level == WarningLevel.CRITICAL
        assert result_critical.proactive_collapse_applied is False
        assert result_critical.proactive_compact_applied is False

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Test 2: Proactive disabled → Tiers 6-7 never instantiated or fired
# ---------------------------------------------------------------------------

def test_proactive_disabled_tiers67_never_fire() -> None:
    """proactive_recovery_enabled=False: Tiers 6-7 never fire even at CRITICAL."""

    async def _run() -> None:
        hook = ContextManagementHook(
            classifier=mock_classifier,
            config=ContextManagementConfig(
                enabled=True,
                proactive_recovery_enabled=False,
            ),
            model="claude-opus-4-6",
        )
        # Verify no proactive strategies were instantiated
        assert hook._collapse_drain is None
        assert hook._reactive_compact is None

        msgs = make_multi_turn_messages(0.93, num_turns=8)
        _, result = await hook.run_pipeline(msgs)

        assert result.warning_level == WarningLevel.CRITICAL
        assert result.proactive_collapse_applied is False
        assert result.proactive_collapse_tokens_freed == 0
        assert result.proactive_compact_applied is False
        assert result.proactive_compact_tokens_freed == 0

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Test 3: Proactive enabled → CRITICAL messages undergo proactive recovery
# ---------------------------------------------------------------------------

def test_proactive_enabled_compacts_critical_messages() -> None:
    """Proactive recovery enabled: CRITICAL messages get proactive collapse/compact."""

    async def _run() -> None:
        hook = ContextManagementHook(
            classifier=mock_classifier,
            config=_make_full_config(proactive=True),
            model="claude-opus-4-6",
            llm_compact_caller=MockLLMCompactCaller(),
        )

        # Critical multi-turn conversation — at 93% utilization
        msgs = make_multi_turn_messages(0.93, num_turns=8)

        # Mock Tier 5 to leave messages still at CRITICAL
        class FakeAcResult:
            activated = True
            turns_summarized = 2

        # Return the same critical messages from Tier 5 so Tier 6 fires
        hook._auto_compact.apply = AsyncMock(return_value=(msgs[:], FakeAcResult()))

        result_msgs, result = await hook.run_pipeline(msgs)

        assert result.warning_level == WarningLevel.CRITICAL
        # At least Tier 6 (proactive collapse) should have fired
        assert result.proactive_collapse_applied is True
        assert result.proactive_collapse_tokens_freed > 0
        assert isinstance(result_msgs, list)
        assert len(result_msgs) > 0

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Test 4: Both systems share estimate_tokens
# ---------------------------------------------------------------------------

def test_both_systems_share_estimate_tokens() -> None:
    """TokenBudgetTracker.estimate_tokens == estimate_message_tokens for same input."""
    messages = [
        {"role": "user", "content": "hello world"},
        {"role": "assistant", "content": "hi there"},
        {"role": "tool", "tool_use_id": "tool_1", "content": "result data"},
        {"role": "user", "content": "x" * 10_000},
        {},  # empty message
    ]

    for msg in messages:
        tracker_result = TokenBudgetTracker.estimate_tokens(msg)
        shared_result = estimate_message_tokens(msg)
        assert tracker_result == shared_result, (
            f"Mismatch for msg with role={msg.get('role')!r}: "
            f"tracker={tracker_result}, shared={shared_result}"
        )

    # Also verify via TokenBudgetTracker.add_message accumulation
    tracker = TokenBudgetTracker(model="claude-sonnet-4-6")
    for msg in messages:
        tracker.add_message(msg, role=msg.get("role", ""))

    snapshot = tracker.snapshot()
    expected_total = estimate_messages_tokens(messages)
    assert snapshot.total_tokens == expected_total


# ---------------------------------------------------------------------------
# Test 5: Tier 5 sufficient → Tier 6-7 don't fire
# ---------------------------------------------------------------------------

def test_tier5_sufficient_tier67_skip() -> None:
    """Auto compact resolves CRITICAL → proactive tiers skip."""

    async def _run() -> None:
        hook = ContextManagementHook(
            classifier=mock_classifier,
            config=_make_full_config(proactive=True),
            model="claude-opus-4-6",
        )

        # Mock auto_compact to return tiny messages (well below CRITICAL)
        small_messages = [
            {"role": "user", "content": "compacted summary"},
            {"role": "assistant", "content": "ok"},
        ]

        class FakeAcResult:
            activated = True
            turns_summarized = 5

        hook._auto_compact.apply = AsyncMock(return_value=(small_messages, FakeAcResult()))

        msgs = make_multi_turn_messages(0.93, num_turns=8)
        _, result = await hook.run_pipeline(msgs)

        # original warning level is still reported as CRITICAL
        assert result.warning_level == WarningLevel.CRITICAL
        assert result.auto_compact_applied is True
        # After Tier 5 reduced to non-CRITICAL, Tier 6-7 must NOT fire
        assert result.proactive_collapse_applied is False
        assert result.proactive_compact_applied is False

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Test 6: Backward compat — PipelineResult default fields
# ---------------------------------------------------------------------------

def test_pipeline_result_default_fields_backward_compat() -> None:
    """Old code reading PipelineResult without Tier 6-7 fields still works."""
    # Construct using only the pre-Tier-6 positional fields
    result = PipelineResult(
        warning_level=WarningLevel.NORMAL,
        content_replacement_applied=False,
        snip_tokens_freed=0,
        microcompact_applied=False,
        microcompact_cache_hits=0,
        microcompact_tokens_freed=0,
        auto_compact_applied=False,
        auto_compact_turns_summarized=0,
        messages_before=10,
        messages_after=10,
        # Note: NOT passing proactive_* fields
    )
    # New fields must default to False/0
    assert result.proactive_collapse_applied is False
    assert result.proactive_collapse_tokens_freed == 0
    assert result.proactive_compact_applied is False
    assert result.proactive_compact_tokens_freed == 0

    # Also verify with proactive fields explicitly set
    result_with_tier67 = PipelineResult(
        warning_level=WarningLevel.CRITICAL,
        content_replacement_applied=True,
        snip_tokens_freed=100,
        microcompact_applied=True,
        microcompact_cache_hits=2,
        microcompact_tokens_freed=500,
        auto_compact_applied=True,
        auto_compact_turns_summarized=3,
        messages_before=24,
        messages_after=2,
        proactive_collapse_applied=True,
        proactive_collapse_tokens_freed=50_000,
        proactive_compact_applied=True,
        proactive_compact_tokens_freed=30_000,
    )
    assert result_with_tier67.proactive_collapse_applied is True
    assert result_with_tier67.proactive_collapse_tokens_freed == 50_000
    assert result_with_tier67.proactive_compact_applied is True
    assert result_with_tier67.proactive_compact_tokens_freed == 30_000


# ---------------------------------------------------------------------------
# Test 7: Empty messages → no processing at any tier
# ---------------------------------------------------------------------------

def test_empty_messages_no_processing() -> None:
    """All 7 tiers produce no-op result for empty message list."""

    async def _run() -> None:
        hook = ContextManagementHook(
            classifier=mock_classifier,
            config=_make_full_config(proactive=True),
            model="claude-opus-4-6",
        )
        result_msgs, result = await hook.run_pipeline([])

        assert result_msgs == []
        assert result.warning_level == WarningLevel.NORMAL
        assert result.messages_before == 0
        assert result.messages_after == 0
        assert result.content_replacement_applied is False
        assert result.snip_tokens_freed == 0
        assert result.microcompact_applied is False
        assert result.microcompact_tokens_freed == 0
        assert result.auto_compact_applied is False
        assert result.auto_compact_turns_summarized == 0
        assert result.proactive_collapse_applied is False
        assert result.proactive_collapse_tokens_freed == 0
        assert result.proactive_compact_applied is False
        assert result.proactive_compact_tokens_freed == 0

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Test 8: Config from env vars
# ---------------------------------------------------------------------------

def test_config_from_env_vars() -> None:
    """Verify all env vars load correctly including MAGI_CONTEXT_PROACTIVE_RECOVERY_ENABLED."""

    def _with_env(env: dict[str, str], also_unset: list[str] | None = None) -> ContextManagementConfig:
        """Temporarily apply env and load config."""
        # Save existing values
        keys = list(env.keys()) + (also_unset or [])
        saved = {k: os.environ.get(k) for k in keys}
        try:
            for k, v in env.items():
                os.environ[k] = v
            for k in also_unset or []:
                os.environ.pop(k, None)
            return load_config_from_env()
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    # --- Full config with proactive enabled ---
    cfg = _with_env({
        "MAGI_CONTEXT_MGMT_ENABLED": "1",
        "MAGI_CONTEXT_MODERATE_THRESHOLD": "0.55",
        "MAGI_CONTEXT_HIGH_THRESHOLD": "0.70",
        "MAGI_CONTEXT_CRITICAL_THRESHOLD": "0.85",
        "MAGI_CONTEXT_PROACTIVE_RECOVERY_ENABLED": "1",
    })
    assert cfg.enabled is True
    assert abs(cfg.moderate_threshold - 0.55) < 1e-9
    assert abs(cfg.high_threshold - 0.70) < 1e-9
    assert abs(cfg.critical_threshold - 0.85) < 1e-9
    assert cfg.proactive_recovery_enabled is True

    # --- Default (no proactive env var) → disabled ---
    cfg_default = _with_env(
        {"MAGI_CONTEXT_MGMT_ENABLED": "1"},
        also_unset=["MAGI_CONTEXT_PROACTIVE_RECOVERY_ENABLED"],
    )
    assert cfg_default.proactive_recovery_enabled is False

    # --- Various truthy values for proactive flag ---
    for truthy_val in ("true", "True", "yes", "1"):
        cfg_truthy = _with_env({
            "MAGI_CONTEXT_MGMT_ENABLED": "0",
            "MAGI_CONTEXT_PROACTIVE_RECOVERY_ENABLED": truthy_val,
        })
        assert cfg_truthy.proactive_recovery_enabled is True, (
            f"Expected proactive=True for '{truthy_val}'"
        )

    # --- Falsy values ---
    for falsy_val in ("0", "false", "False", "no"):
        cfg_falsy = _with_env({
            "MAGI_CONTEXT_MGMT_ENABLED": "0",
            "MAGI_CONTEXT_PROACTIVE_RECOVERY_ENABLED": falsy_val,
        })
        assert cfg_falsy.proactive_recovery_enabled is False, (
            f"Expected proactive=False for '{falsy_val}'"
        )


# ---------------------------------------------------------------------------
# Test 9: Tier 6 fires, Tier 7 skips when Tier 6 resolves CRITICAL
# ---------------------------------------------------------------------------

def test_tier6_resolves_critical_tier7_skips() -> None:
    """Tier 6 fires and reduces below CRITICAL → Tier 7 does NOT fire."""

    async def _run() -> None:
        hook = ContextManagementHook(
            classifier=mock_classifier,
            config=_make_full_config(proactive=True),
            model="claude-opus-4-6",
        )

        critical_msgs = make_multi_turn_messages(0.93, num_turns=8)

        # Tier 5 leaves messages still at CRITICAL
        class FakeAcResult:
            activated = False
            turns_summarized = 0

        hook._auto_compact.apply = AsyncMock(return_value=(critical_msgs[:], FakeAcResult()))

        # Tier 6 succeeds and returns small (non-CRITICAL) messages
        small_after_collapse = make_messages(0.50)
        hook._collapse_drain.recover = AsyncMock(return_value=RecoveryResult(
            success=True,
            strategy_name="collapse_drain",
            modified_messages=small_after_collapse,
            tokens_freed=50_000,
        ))

        msgs = make_multi_turn_messages(0.93, num_turns=8)
        _, result = await hook.run_pipeline(msgs)

        assert result.proactive_collapse_applied is True
        assert result.proactive_collapse_tokens_freed == 50_000
        # Tier 7 must NOT fire (Tier 6 dropped below CRITICAL)
        assert result.proactive_compact_applied is False

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Test 10: messages_before / messages_after are tracked correctly
# ---------------------------------------------------------------------------

def test_messages_before_after_tracking() -> None:
    """messages_before and messages_after are correctly tracked through pipeline."""

    async def _run() -> None:
        hook = ContextManagementHook(
            classifier=mock_classifier,
            config=_make_full_config(proactive=False),
            model="claude-opus-4-6",
        )

        # Normal messages — no compaction expected
        msgs = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}]
        _, result = await hook.run_pipeline(msgs)

        assert result.messages_before == 2
        assert isinstance(result.messages_before, int)
        assert isinstance(result.messages_after, int)
        # Without compaction, after == before
        assert result.messages_after == 2

        # Test with empty list
        _, result_empty = await hook.run_pipeline([])
        assert result_empty.messages_before == 0
        assert result_empty.messages_after == 0

    asyncio.run(_run())
