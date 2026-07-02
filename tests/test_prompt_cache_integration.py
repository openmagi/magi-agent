"""Integration tests for PR 4: build_system_prompt_blocks + PromptCacheMetrics.

TDD approach — tests written before implementation.

Covers:
  1.  build_system_prompt_blocks cache_enabled=False → single block, no cache_control
  2.  build_system_prompt_blocks cache_enabled=False → text matches build_system_prompt()
  3.  build_system_prompt_blocks cache_enabled=True + claude model → static blocks have cache_control
  4.  build_system_prompt_blocks cache_enabled=True + gpt model → no cache_control markers
  5.  build_system_prompt_blocks with empty identity → valid blocks
  6.  build_system_prompt backward compat → existing function unchanged
  7.  PromptCacheMetrics.record_api_usage + cache_hit_rate correct
  8.  PromptCacheMetrics zero turns → hit rate 0.0
  9.  PromptCacheMetrics multiple turns accumulate
  10. PromptCacheMetrics.to_evidence() correct format
  11. load_cache_config reads from env vars
  12. load_cache_config defaults when env not set
"""

from __future__ import annotations

import importlib
import os
from datetime import UTC, datetime
from types import ModuleType


# ---------------------------------------------------------------------------
# Lazy module helpers
# ---------------------------------------------------------------------------


def _message_builder() -> ModuleType:
    try:
        return importlib.import_module(
            "magi_agent.runtime.message_builder"
        )
    except ModuleNotFoundError as exc:
        import pytest

        pytest.fail(f"message_builder module is missing: {exc}")


def _metrics_module() -> ModuleType:
    try:
        return importlib.import_module("magi_agent.prompt.metrics")
    except ModuleNotFoundError as exc:
        import pytest

        pytest.fail(f"magi_agent.prompt.metrics module is missing: {exc}")


def _prompt_package() -> ModuleType:
    try:
        return importlib.import_module("magi_agent.prompt")
    except ModuleNotFoundError as exc:
        import pytest

        pytest.fail(f"magi_agent.prompt package is missing: {exc}")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_FIXED_NOW = datetime(2026, 5, 27, 12, 0, 0, tzinfo=UTC)
_SESSION_KEY = "test-session-key"
_TURN_ID = "test-turn-id"
_IDENTITY = {
    "soul": "You are a helpful assistant.",
    "identity": "Name: TestBot",
}


def _build_blocks_disabled(**extra):
    mb = _message_builder()
    return mb.build_system_prompt_blocks(
        session_key=_SESSION_KEY,
        turn_id=_TURN_ID,
        identity=_IDENTITY,
        now=_FIXED_NOW,
        cache_enabled=False,
        **extra,
    )


def _build_blocks_enabled(model: str = "claude-sonnet-4-6", **extra):
    mb = _message_builder()
    return mb.build_system_prompt_blocks(
        session_key=_SESSION_KEY,
        turn_id=_TURN_ID,
        identity=_IDENTITY,
        now=_FIXED_NOW,
        cache_enabled=True,
        model=model,
        **extra,
    )


# ---------------------------------------------------------------------------
# 1. cache_enabled=False → single block, no cache_control key
# ---------------------------------------------------------------------------


class TestBuildSystemPromptBlocksDisabled:
    def test_returns_single_block(self) -> None:
        blocks = _build_blocks_disabled()
        assert len(blocks) == 1

    def test_single_block_has_type_text(self) -> None:
        blocks = _build_blocks_disabled()
        assert blocks[0]["type"] == "text"

    def test_single_block_has_no_cache_control(self) -> None:
        blocks = _build_blocks_disabled()
        assert "cache_control" not in blocks[0]

    # 2. text matches build_system_prompt()
    def test_text_matches_build_system_prompt(self) -> None:
        mb = _message_builder()
        blocks = _build_blocks_disabled()
        expected = mb.build_system_prompt(
            session_key=_SESSION_KEY,
            turn_id=_TURN_ID,
            identity=_IDENTITY,
            now=_FIXED_NOW,
        )
        assert blocks[0]["text"] == expected


# ---------------------------------------------------------------------------
# 3. cache_enabled=True + claude model → multiple blocks, static ones have cache_control
# ---------------------------------------------------------------------------


class TestBuildSystemPromptBlocksEnabledClaude:
    def test_returns_multiple_blocks(self) -> None:
        blocks = _build_blocks_enabled("claude-sonnet-4-6")
        assert len(blocks) > 1

    def test_at_least_one_block_has_cache_control(self) -> None:
        blocks = _build_blocks_enabled("claude-sonnet-4-6")
        has_cache = any("cache_control" in b for b in blocks)
        assert has_cache, "Expected at least one block with cache_control for Anthropic provider"

    def test_cache_control_type_is_ephemeral(self) -> None:
        blocks = _build_blocks_enabled("claude-sonnet-4-6")
        for block in blocks:
            if "cache_control" in block:
                assert block["cache_control"] == {"type": "ephemeral"}

    def test_all_blocks_have_type_and_text(self) -> None:
        blocks = _build_blocks_enabled("claude-sonnet-4-6")
        for block in blocks:
            assert "type" in block
            assert "text" in block
            assert block["type"] == "text"

    def test_concatenated_text_matches_build_system_prompt(self) -> None:
        """Text in all blocks combined (joined with \\n\\n) should match build_system_prompt."""
        mb = _message_builder()
        blocks = _build_blocks_enabled("claude-sonnet-4-6")
        combined = "\n\n".join(b["text"] for b in blocks)
        expected = mb.build_system_prompt(
            session_key=_SESSION_KEY,
            turn_id=_TURN_ID,
            identity=_IDENTITY,
            now=_FIXED_NOW,
        )
        assert combined == expected


# ---------------------------------------------------------------------------
# 4. cache_enabled=True + gpt model → multiple blocks, no cache_control markers
# ---------------------------------------------------------------------------


class TestBuildSystemPromptBlocksEnabledGPT:
    def test_returns_multiple_blocks(self) -> None:
        blocks = _build_blocks_enabled("gpt-5.4")
        assert len(blocks) > 1

    def test_no_cache_control_markers_for_openai(self) -> None:
        blocks = _build_blocks_enabled("gpt-5.4")
        has_cache = any("cache_control" in b for b in blocks)
        assert not has_cache, "OpenAI provider should have no cache_control markers"

    def test_concatenated_text_matches_build_system_prompt_gpt(self) -> None:
        mb = _message_builder()
        blocks = _build_blocks_enabled("gpt-5.4")
        combined = "\n\n".join(b["text"] for b in blocks)
        expected = mb.build_system_prompt(
            session_key=_SESSION_KEY,
            turn_id=_TURN_ID,
            identity=_IDENTITY,
            now=_FIXED_NOW,
        )
        assert combined == expected


# ---------------------------------------------------------------------------
# 5. Empty identity → still produces valid blocks
# ---------------------------------------------------------------------------


class TestBuildSystemPromptBlocksEmptyIdentity:
    def test_empty_identity_disabled_returns_single_block(self) -> None:
        mb = _message_builder()
        blocks = mb.build_system_prompt_blocks(
            session_key=_SESSION_KEY,
            turn_id=_TURN_ID,
            identity={},
            now=_FIXED_NOW,
            cache_enabled=False,
        )
        assert len(blocks) == 1
        assert blocks[0]["type"] == "text"
        assert len(blocks[0]["text"]) > 0

    def test_empty_identity_enabled_returns_multiple_blocks(self) -> None:
        mb = _message_builder()
        blocks = mb.build_system_prompt_blocks(
            session_key=_SESSION_KEY,
            turn_id=_TURN_ID,
            identity={},
            now=_FIXED_NOW,
            cache_enabled=True,
            model="claude-sonnet-4-6",
        )
        assert len(blocks) >= 1
        for block in blocks:
            assert "type" in block
            assert "text" in block

    def test_empty_identity_enabled_no_crash_gpt(self) -> None:
        mb = _message_builder()
        blocks = mb.build_system_prompt_blocks(
            session_key=_SESSION_KEY,
            turn_id=_TURN_ID,
            identity=None,
            now=_FIXED_NOW,
            cache_enabled=True,
            model="gpt-5.5",
        )
        assert isinstance(blocks, list)
        assert len(blocks) >= 1


# ---------------------------------------------------------------------------
# 6. Backward compat — build_system_prompt unchanged
# ---------------------------------------------------------------------------


class TestBuildSystemPromptBackwardCompat:
    def test_build_system_prompt_still_returns_str(self) -> None:
        mb = _message_builder()
        result = mb.build_system_prompt(
            session_key=_SESSION_KEY,
            turn_id=_TURN_ID,
            identity=_IDENTITY,
            now=_FIXED_NOW,
        )
        assert isinstance(result, str)

    def test_build_system_prompt_contains_session_key(self) -> None:
        mb = _message_builder()
        result = mb.build_system_prompt(
            session_key=_SESSION_KEY,
            turn_id=_TURN_ID,
            identity=_IDENTITY,
            now=_FIXED_NOW,
        )
        assert _SESSION_KEY in result

    def test_build_system_prompt_contains_deferral_prevention(self) -> None:
        mb = _message_builder()
        result = mb.build_system_prompt(
            session_key=_SESSION_KEY,
            turn_id=_TURN_ID,
            identity=_IDENTITY,
            now=_FIXED_NOW,
        )
        assert "deferral-prevention" in result

    def test_build_system_prompt_contains_output_rules(self) -> None:
        mb = _message_builder()
        result = mb.build_system_prompt(
            session_key=_SESSION_KEY,
            turn_id=_TURN_ID,
            identity=_IDENTITY,
            now=_FIXED_NOW,
        )
        assert "output-rules" in result

    def test_build_system_prompt_blocks_does_not_modify_build_system_prompt(
        self,
    ) -> None:
        """Calling build_system_prompt_blocks does not affect build_system_prompt."""
        mb = _message_builder()
        before = mb.build_system_prompt(
            session_key=_SESSION_KEY,
            turn_id=_TURN_ID,
            identity=_IDENTITY,
            now=_FIXED_NOW,
        )
        # call blocks variant
        mb.build_system_prompt_blocks(
            session_key=_SESSION_KEY,
            turn_id=_TURN_ID,
            identity=_IDENTITY,
            now=_FIXED_NOW,
            cache_enabled=True,
            model="claude-sonnet-4-6",
        )
        after = mb.build_system_prompt(
            session_key=_SESSION_KEY,
            turn_id=_TURN_ID,
            identity=_IDENTITY,
            now=_FIXED_NOW,
        )
        assert before == after


# ---------------------------------------------------------------------------
# 7. PromptCacheMetrics — record_api_usage + cache_hit_rate correct
# ---------------------------------------------------------------------------


class TestPromptCacheMetricsBasic:
    def test_initial_state_all_zeros(self) -> None:
        PromptCacheMetrics = _metrics_module().PromptCacheMetrics
        m = PromptCacheMetrics()
        assert m.cache_creation_tokens == 0
        assert m.cache_read_tokens == 0
        assert m.total_input_tokens == 0
        assert m.turns_recorded == 0

    def test_record_api_usage_increments_correctly(self) -> None:
        PromptCacheMetrics = _metrics_module().PromptCacheMetrics
        m = PromptCacheMetrics()
        usage = {
            "cache_creation_input_tokens": 500,
            "cache_read_input_tokens": 200,
            "input_tokens": 700,
        }
        m.record_api_usage(usage)
        assert m.cache_creation_tokens == 500
        assert m.cache_read_tokens == 200
        assert m.total_input_tokens == 700
        assert m.turns_recorded == 1

    def test_cache_hit_rate_correct(self) -> None:
        PromptCacheMetrics = _metrics_module().PromptCacheMetrics
        m = PromptCacheMetrics()
        m.record_api_usage({
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 300,
            "input_tokens": 1000,
        })
        assert m.cache_hit_rate == 0.3

    def test_tokens_saved_equals_cache_read_tokens(self) -> None:
        PromptCacheMetrics = _metrics_module().PromptCacheMetrics
        m = PromptCacheMetrics()
        m.record_api_usage({
            "cache_creation_input_tokens": 100,
            "cache_read_input_tokens": 400,
            "input_tokens": 500,
        })
        assert m.tokens_saved == 400


# ---------------------------------------------------------------------------
# 8. PromptCacheMetrics — zero turns → hit rate 0.0
# ---------------------------------------------------------------------------


class TestPromptCacheMetricsZeroTurns:
    def test_cache_hit_rate_zero_when_no_turns(self) -> None:
        PromptCacheMetrics = _metrics_module().PromptCacheMetrics
        m = PromptCacheMetrics()
        assert m.cache_hit_rate == 0.0

    def test_tokens_saved_zero_when_no_turns(self) -> None:
        PromptCacheMetrics = _metrics_module().PromptCacheMetrics
        m = PromptCacheMetrics()
        assert m.tokens_saved == 0

    def test_record_usage_missing_keys_defaults_to_zero(self) -> None:
        PromptCacheMetrics = _metrics_module().PromptCacheMetrics
        m = PromptCacheMetrics()
        m.record_api_usage({})
        assert m.cache_creation_tokens == 0
        assert m.cache_read_tokens == 0
        assert m.total_input_tokens == 0
        assert m.turns_recorded == 1


# ---------------------------------------------------------------------------
# 9. PromptCacheMetrics — multiple turns accumulate
# ---------------------------------------------------------------------------


class TestPromptCacheMetricsMultipleTurns:
    def test_multiple_turns_accumulate_creation_tokens(self) -> None:
        PromptCacheMetrics = _metrics_module().PromptCacheMetrics
        m = PromptCacheMetrics()
        m.record_api_usage({"cache_creation_input_tokens": 100, "cache_read_input_tokens": 0, "input_tokens": 100})
        m.record_api_usage({"cache_creation_input_tokens": 50, "cache_read_input_tokens": 0, "input_tokens": 50})
        assert m.cache_creation_tokens == 150
        assert m.turns_recorded == 2

    def test_multiple_turns_accumulate_read_tokens(self) -> None:
        PromptCacheMetrics = _metrics_module().PromptCacheMetrics
        m = PromptCacheMetrics()
        m.record_api_usage({"cache_creation_input_tokens": 0, "cache_read_input_tokens": 200, "input_tokens": 200})
        m.record_api_usage({"cache_creation_input_tokens": 0, "cache_read_input_tokens": 300, "input_tokens": 300})
        assert m.cache_read_tokens == 500
        assert m.total_input_tokens == 500

    def test_multiple_turns_hit_rate_aggregates(self) -> None:
        PromptCacheMetrics = _metrics_module().PromptCacheMetrics
        m = PromptCacheMetrics()
        # turn 1: 0 cache reads out of 1000
        m.record_api_usage({"cache_creation_input_tokens": 1000, "cache_read_input_tokens": 0, "input_tokens": 1000})
        # turn 2: 1000 cache reads out of 1000 (full hit)
        m.record_api_usage({"cache_creation_input_tokens": 0, "cache_read_input_tokens": 1000, "input_tokens": 1000})
        # total: 1000 reads / 2000 input = 0.5
        assert abs(m.cache_hit_rate - 0.5) < 1e-9


# ---------------------------------------------------------------------------
# 10. PromptCacheMetrics.to_evidence() correct format
# ---------------------------------------------------------------------------


class TestPromptCacheMetricsToEvidence:
    def test_to_evidence_has_correct_type(self) -> None:
        PromptCacheMetrics = _metrics_module().PromptCacheMetrics
        m = PromptCacheMetrics()
        ev = m.to_evidence()
        assert ev["type"] == "prompt_cache_metrics"

    def test_to_evidence_has_all_required_keys(self) -> None:
        PromptCacheMetrics = _metrics_module().PromptCacheMetrics
        m = PromptCacheMetrics()
        ev = m.to_evidence()
        required = {
            "type",
            "cache_creation_tokens",
            "cache_read_tokens",
            "total_input_tokens",
            "turns_recorded",
            "cache_hit_rate",
            "tokens_saved",
        }
        assert required <= set(ev.keys())

    def test_to_evidence_reflects_recorded_usage(self) -> None:
        PromptCacheMetrics = _metrics_module().PromptCacheMetrics
        m = PromptCacheMetrics()
        m.record_api_usage({
            "cache_creation_input_tokens": 200,
            "cache_read_input_tokens": 800,
            "input_tokens": 1000,
        })
        ev = m.to_evidence()
        assert ev["cache_creation_tokens"] == 200
        assert ev["cache_read_tokens"] == 800
        assert ev["total_input_tokens"] == 1000
        assert ev["turns_recorded"] == 1
        assert ev["cache_hit_rate"] == 0.8
        assert ev["tokens_saved"] == 800

    def test_to_evidence_hit_rate_rounded_to_4_decimals(self) -> None:
        PromptCacheMetrics = _metrics_module().PromptCacheMetrics
        m = PromptCacheMetrics()
        # 1 / 3 = 0.3333...
        m.record_api_usage({
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 1,
            "input_tokens": 3,
        })
        ev = m.to_evidence()
        # round(1/3, 4) == 0.3333
        assert ev["cache_hit_rate"] == round(1 / 3, 4)


# ---------------------------------------------------------------------------
# 11 & 12. load_cache_config — env vars
# ---------------------------------------------------------------------------


class TestLoadCacheConfig:
    def test_defaults_when_env_not_set(self, monkeypatch) -> None:
        # C1 / N-10: profile-aware default-ON. Unset flag + unset profile
        # resolves to the full profile, so prompt caching is ON.
        monkeypatch.delenv("MAGI_PROMPT_CACHE_ENABLED", raising=False)
        monkeypatch.delenv("MAGI_PROMPT_CACHE_PROVIDER", raising=False)
        monkeypatch.delenv("MAGI_RUNTIME_PROFILE", raising=False)
        load_cache_config = _metrics_module().load_cache_config
        enabled, provider = load_cache_config()
        assert enabled is True
        assert provider == "auto"

    def test_disabled_under_safe_profile(self, monkeypatch) -> None:
        monkeypatch.delenv("MAGI_PROMPT_CACHE_ENABLED", raising=False)
        monkeypatch.setenv("MAGI_RUNTIME_PROFILE", "safe")
        load_cache_config = _metrics_module().load_cache_config
        enabled, _ = load_cache_config()
        assert enabled is False

    def test_enabled_true_when_env_is_on(self, monkeypatch) -> None:
        monkeypatch.setenv("MAGI_PROMPT_CACHE_ENABLED", "on")
        load_cache_config = _metrics_module().load_cache_config
        enabled, _ = load_cache_config()
        assert enabled is True

    def test_enabled_true_when_env_is_1(self, monkeypatch) -> None:
        monkeypatch.setenv("MAGI_PROMPT_CACHE_ENABLED", "1")
        monkeypatch.delenv("MAGI_PROMPT_CACHE_PROVIDER", raising=False)
        load_cache_config = _metrics_module().load_cache_config
        enabled, provider = load_cache_config()
        assert enabled is True

    def test_enabled_true_when_env_is_true(self, monkeypatch) -> None:
        monkeypatch.setenv("MAGI_PROMPT_CACHE_ENABLED", "true")
        load_cache_config = _metrics_module().load_cache_config
        enabled, _ = load_cache_config()
        assert enabled is True

    def test_enabled_true_when_env_is_yes(self, monkeypatch) -> None:
        monkeypatch.setenv("MAGI_PROMPT_CACHE_ENABLED", "yes")
        load_cache_config = _metrics_module().load_cache_config
        enabled, _ = load_cache_config()
        assert enabled is True

    def test_enabled_false_when_env_is_0(self, monkeypatch) -> None:
        monkeypatch.setenv("MAGI_PROMPT_CACHE_ENABLED", "0")
        load_cache_config = _metrics_module().load_cache_config
        enabled, _ = load_cache_config()
        assert enabled is False

    def test_provider_read_from_env(self, monkeypatch) -> None:
        monkeypatch.setenv("MAGI_PROMPT_CACHE_ENABLED", "1")
        monkeypatch.setenv("MAGI_PROMPT_CACHE_PROVIDER", "anthropic")
        load_cache_config = _metrics_module().load_cache_config
        enabled, provider = load_cache_config()
        assert enabled is True
        assert provider == "anthropic"

    def test_provider_default_is_auto(self, monkeypatch) -> None:
        monkeypatch.delenv("MAGI_PROMPT_CACHE_PROVIDER", raising=False)
        load_cache_config = _metrics_module().load_cache_config
        _, provider = load_cache_config()
        assert provider == "auto"


# ---------------------------------------------------------------------------
# __init__.py re-exports
# ---------------------------------------------------------------------------


class TestPromptPackageReexports:
    def test_prompt_cache_metrics_importable_from_package(self) -> None:
        pkg = _prompt_package()
        assert hasattr(pkg, "PromptCacheMetrics")

    def test_load_cache_config_importable_from_package(self) -> None:
        pkg = _prompt_package()
        assert hasattr(pkg, "load_cache_config")
