"""Unit tests for the pure no-tool-finalizer decision helpers (B9 backstop)."""

from __future__ import annotations

import pytest

from magi_agent.runtime.no_tool_finalizer import (
    NoToolFinalizerConfig,
    build_no_tool_finalizer_message,
    should_run_no_tool_finalizer,
)


def _cfg(enabled: bool = True, allowance: int = 64) -> NoToolFinalizerConfig:
    return NoToolFinalizerConfig(enabled=enabled, event_allowance=allowance)


def test_config_defaults():
    cfg = NoToolFinalizerConfig()
    assert cfg.enabled is True
    assert cfg.event_allowance == 64


def test_none_config_never_fires():
    assert should_run_no_tool_finalizer(None, emitted_text="", recoveries_used=0) is False


def test_disabled_never_fires():
    assert (
        should_run_no_tool_finalizer(_cfg(enabled=False), emitted_text="", recoveries_used=0)
        is False
    )


def test_blank_turn_fires():
    assert should_run_no_tool_finalizer(_cfg(), emitted_text="", recoveries_used=0) is True


def test_whitespace_only_is_blank_and_fires():
    assert (
        should_run_no_tool_finalizer(_cfg(), emitted_text="   \n\t ", recoveries_used=0) is True
    )


def test_non_blank_turn_does_not_fire():
    assert (
        should_run_no_tool_finalizer(_cfg(), emitted_text="Here is the answer.", recoveries_used=0)
        is False
    )


def test_recovery_owned_turn_does_not_fire():
    # Empty-response recovery already re-invoked; the finalizer defers.
    assert should_run_no_tool_finalizer(_cfg(), emitted_text="", recoveries_used=1) is False


def test_message_content():
    msg = build_no_tool_finalizer_message()
    assert isinstance(msg, str) and msg
    lower = msg.lower()
    assert "do not call any tools" in lower
    assert "no text answer" in lower
    # It must invite an honest "what is missing" instead of forcing a
    # confident answer after failed tools.
    assert "missing" in lower


def test_env_reader_default_on_full_profile():
    from magi_agent.engine.engine_recovery import build_no_tool_finalizer_config

    # No profile set (full profile) -> default-ON.
    cfg = build_no_tool_finalizer_config(env={})
    assert cfg is not None
    assert cfg.enabled is True
    assert cfg.event_allowance == 64


def test_env_reader_kill_switch():
    from magi_agent.engine.engine_recovery import build_no_tool_finalizer_config

    assert build_no_tool_finalizer_config(env={"MAGI_NO_TOOL_FINALIZER_ENABLED": "0"}) is None


def test_env_reader_safe_profile_off():
    from magi_agent.engine.engine_recovery import build_no_tool_finalizer_config

    assert build_no_tool_finalizer_config(env={"MAGI_RUNTIME_PROFILE": "safe"}) is None


def test_env_reader_allowance_override():
    from magi_agent.engine.engine_recovery import build_no_tool_finalizer_config

    cfg = build_no_tool_finalizer_config(env={"MAGI_NO_TOOL_FINALIZER_EVENT_ALLOWANCE": "128"})
    assert cfg is not None and cfg.event_allowance == 128


def test_env_reader_allowance_below_one_raises():
    from magi_agent.config.env import RuntimeEnvError, parse_no_tool_finalizer_env

    with pytest.raises(RuntimeEnvError):
        parse_no_tool_finalizer_env({"MAGI_NO_TOOL_FINALIZER_EVENT_ALLOWANCE": "0"})
