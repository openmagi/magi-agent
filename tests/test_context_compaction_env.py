"""PR13: single-source env-flag tests for live context compaction."""

from __future__ import annotations

import pytest

from magi_agent.config.env import (
    ContextCompactionEnv,
    RuntimeEnvError,
    parse_context_compaction_env,
)


def test_default_local_profile_on() -> None:
    cfg = parse_context_compaction_env({})
    assert cfg == ContextCompactionEnv(
        enabled=True, token_threshold=24_000, tail_events=16
    )


def test_safe_profile_disables_default() -> None:
    cfg = parse_context_compaction_env({"MAGI_RUNTIME_PROFILE": "safe"})
    assert cfg.enabled is False


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", "TRUE", "On"])
def test_enabled_truthy_tokens(value: str) -> None:
    cfg = parse_context_compaction_env({"MAGI_CONTEXT_COMPACTION_ENABLED": value})
    assert cfg.enabled is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", ""])
def test_disabled_falsy_tokens(value: str) -> None:
    cfg = parse_context_compaction_env({"MAGI_CONTEXT_COMPACTION_ENABLED": value})
    assert cfg.enabled is False


def test_custom_thresholds() -> None:
    cfg = parse_context_compaction_env(
        {
            "MAGI_CONTEXT_COMPACTION_ENABLED": "1",
            "MAGI_COMPACTION_TOKEN_THRESHOLD": "8000",
            "MAGI_COMPACTION_TAIL_EVENTS": "8",
        }
    )
    assert cfg.enabled is True
    assert cfg.token_threshold == 8000
    assert cfg.tail_events == 8


def test_rejects_non_positive_token_threshold() -> None:
    with pytest.raises(RuntimeEnvError):
        parse_context_compaction_env({"MAGI_COMPACTION_TOKEN_THRESHOLD": "0"})


def test_rejects_non_positive_tail_events() -> None:
    with pytest.raises(RuntimeEnvError):
        parse_context_compaction_env({"MAGI_COMPACTION_TAIL_EVENTS": "0"})


def test_rejects_non_integer_threshold() -> None:
    with pytest.raises(RuntimeEnvError):
        parse_context_compaction_env({"MAGI_COMPACTION_TOKEN_THRESHOLD": "abc"})


# ---------------------------------------------------------------------------
# G2 — real-token accounting fields (additive; default-OFF)
# ---------------------------------------------------------------------------


def test_pct_and_reserve_env_parse_defaults() -> None:
    cfg = parse_context_compaction_env({})
    # Additive G2 fields default to the conservative, flag-OFF shape.
    assert cfg.real_tokens_enabled is False
    assert cfg.real_tokens_pct == 0.75
    assert cfg.output_reserve == 8_000


def test_real_tokens_enabled_strict_truthy() -> None:
    # Strict default-OFF bool flag (NOT profile-aware): unset/false stays OFF
    # even in the full runtime profile.
    assert parse_context_compaction_env({}).real_tokens_enabled is False
    assert (
        parse_context_compaction_env(
            {"MAGI_COMPACTION_REAL_TOKENS_ENABLED": "1"}
        ).real_tokens_enabled
        is True
    )
    assert (
        parse_context_compaction_env(
            {"MAGI_COMPACTION_REAL_TOKENS_ENABLED": "0"}
        ).real_tokens_enabled
        is False
    )


def test_pct_and_reserve_explicit_values() -> None:
    cfg = parse_context_compaction_env(
        {
            "MAGI_COMPACTION_REAL_TOKENS_ENABLED": "1",
            "MAGI_COMPACTION_REAL_TOKENS_PCT": "0.5",
            "MAGI_COMPACTION_OUTPUT_RESERVE": "12000",
        }
    )
    assert cfg.real_tokens_enabled is True
    assert cfg.real_tokens_pct == 0.5
    assert cfg.output_reserve == 12_000


@pytest.mark.parametrize("value", ["0", "0.0", "-0.1", "1.5", "abc"])
def test_rejects_out_of_range_pct(value: str) -> None:
    # pct must be in (0, 1]; non-numeric or out-of-range raises.
    with pytest.raises(RuntimeEnvError):
        parse_context_compaction_env({"MAGI_COMPACTION_REAL_TOKENS_PCT": value})


def test_pct_upper_bound_one_is_allowed() -> None:
    cfg = parse_context_compaction_env({"MAGI_COMPACTION_REAL_TOKENS_PCT": "1"})
    assert cfg.real_tokens_pct == 1.0


def test_rejects_negative_output_reserve() -> None:
    with pytest.raises(RuntimeEnvError):
        parse_context_compaction_env({"MAGI_COMPACTION_OUTPUT_RESERVE": "-1"})


def test_zero_output_reserve_allowed() -> None:
    cfg = parse_context_compaction_env({"MAGI_COMPACTION_OUTPUT_RESERVE": "0"})
    assert cfg.output_reserve == 0


# ---------------------------------------------------------------------------
# G4 — tool-output prune pre-tier (strict default-OFF)
# ---------------------------------------------------------------------------


def test_tool_prune_defaults_off() -> None:
    cfg = parse_context_compaction_env({})
    assert cfg.tool_prune_enabled is False
    assert cfg.prune_protect == 40_000
    assert cfg.prune_minimum == 20_000


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", "TRUE", "On"])
def test_tool_prune_enabled_strict_truthy(value: str) -> None:
    # STRICT truthy parse (NOT profile-aware): only an explicit truthy value ON.
    cfg = parse_context_compaction_env(
        {"MAGI_COMPACTION_TOOL_PRUNE_ENABLED": value}
    )
    assert cfg.tool_prune_enabled is True


def test_tool_prune_not_profile_aware() -> None:
    # The full profile must NOT auto-enable the prune pre-tier.
    cfg = parse_context_compaction_env({"MAGI_RUNTIME_PROFILE": "full"})
    assert cfg.tool_prune_enabled is False


def test_tool_prune_int_config_parsed() -> None:
    cfg = parse_context_compaction_env(
        {
            "MAGI_COMPACTION_TOOL_PRUNE_ENABLED": "1",
            "MAGI_COMPACTION_PRUNE_PROTECT": "50000",
            "MAGI_COMPACTION_PRUNE_MINIMUM": "10000",
        }
    )
    assert cfg.tool_prune_enabled is True
    assert cfg.prune_protect == 50_000
    assert cfg.prune_minimum == 10_000


@pytest.mark.parametrize("value", ["0", "-1"])
def test_rejects_invalid_prune_protect(value: str) -> None:
    with pytest.raises(RuntimeEnvError):
        parse_context_compaction_env({"MAGI_COMPACTION_PRUNE_PROTECT": value})


@pytest.mark.parametrize("value", ["0", "-5"])
def test_rejects_invalid_prune_minimum(value: str) -> None:
    with pytest.raises(RuntimeEnvError):
        parse_context_compaction_env({"MAGI_COMPACTION_PRUNE_MINIMUM": value})


# ---------------------------------------------------------------------------
# G1 — summary injection (strict default-OFF)
# ---------------------------------------------------------------------------


def test_summarize_defaults_off() -> None:
    cfg = parse_context_compaction_env({})
    assert cfg.summarize_enabled is False
    assert cfg.summary_model == ""
    assert cfg.summary_timeout == 30.0


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", "TRUE", "On"])
def test_summarize_enabled_strict_truthy(value: str) -> None:
    # STRICT truthy parse (NOT profile-aware): only an explicit truthy value ON.
    cfg = parse_context_compaction_env(
        {"MAGI_COMPACTION_SUMMARIZE_ENABLED": value}
    )
    assert cfg.summarize_enabled is True


def test_summarize_not_profile_aware() -> None:
    # The full profile must NOT auto-enable summary injection.
    cfg = parse_context_compaction_env({"MAGI_RUNTIME_PROFILE": "full"})
    assert cfg.summarize_enabled is False


def test_summarize_model_and_timeout_parsed() -> None:
    cfg = parse_context_compaction_env(
        {
            "MAGI_COMPACTION_SUMMARIZE_ENABLED": "1",
            "MAGI_COMPACTION_SUMMARY_MODEL": "anthropic/claude-haiku-4-5",
            "MAGI_COMPACTION_SUMMARY_TIMEOUT": "12.5",
        }
    )
    assert cfg.summarize_enabled is True
    assert cfg.summary_model == "anthropic/claude-haiku-4-5"
    assert cfg.summary_timeout == 12.5


@pytest.mark.parametrize("value", ["0", "-1"])
def test_rejects_invalid_summary_timeout(value: str) -> None:
    with pytest.raises(RuntimeEnvError):
        parse_context_compaction_env({"MAGI_COMPACTION_SUMMARY_TIMEOUT": value})


# ---------------------------------------------------------------------------
# G5/G6 — anchored summary + circuit breaker (strict default-OFF / default-3)
# ---------------------------------------------------------------------------


def test_anchored_and_max_failures_defaults() -> None:
    cfg = parse_context_compaction_env({})
    assert cfg.anchored_summary_enabled is False
    assert cfg.summary_max_failures == 3


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", "TRUE", "On"])
def test_anchored_enabled_strict_truthy(value: str) -> None:
    # STRICT truthy parse (NOT profile-aware), matching summarize.
    cfg = parse_context_compaction_env(
        {"MAGI_COMPACTION_ANCHORED_SUMMARY_ENABLED": value}
    )
    assert cfg.anchored_summary_enabled is True


def test_anchored_not_profile_aware() -> None:
    cfg = parse_context_compaction_env({"MAGI_RUNTIME_PROFILE": "full"})
    assert cfg.anchored_summary_enabled is False


def test_summary_max_failures_parsed() -> None:
    cfg = parse_context_compaction_env(
        {"MAGI_COMPACTION_SUMMARY_MAX_FAILURES": "5"}
    )
    assert cfg.summary_max_failures == 5


def test_summary_max_failures_zero_disables_breaker() -> None:
    cfg = parse_context_compaction_env(
        {"MAGI_COMPACTION_SUMMARY_MAX_FAILURES": "0"}
    )
    assert cfg.summary_max_failures == 0


def test_rejects_negative_summary_max_failures() -> None:
    with pytest.raises(RuntimeEnvError):
        parse_context_compaction_env(
            {"MAGI_COMPACTION_SUMMARY_MAX_FAILURES": "-1"}
        )


def test_manual_enabled_default_off() -> None:
    assert parse_context_compaction_env({}).manual_enabled is False


@pytest.mark.parametrize("value", ["1", "true", "yes", "on"])
def test_manual_enabled_strict_truthy(value: str) -> None:
    cfg = parse_context_compaction_env({"MAGI_COMPACTION_MANUAL_ENABLED": value})
    assert cfg.manual_enabled is True


def test_manual_enabled_falsy_garbage() -> None:
    cfg = parse_context_compaction_env({"MAGI_COMPACTION_MANUAL_ENABLED": "nope"})
    assert cfg.manual_enabled is False
