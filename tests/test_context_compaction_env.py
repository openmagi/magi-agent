"""PR13: single-source env-flag tests for live context compaction."""

from __future__ import annotations

import pytest

from magi_agent.config.env import (
    ContextCompactionEnv,
    RuntimeEnvError,
    parse_context_compaction_env,
)


def test_default_off() -> None:
    cfg = parse_context_compaction_env({})
    assert cfg == ContextCompactionEnv(
        enabled=False, token_threshold=24_000, tail_events=16
    )


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
