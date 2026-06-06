"""Onboarding/usability fixes for the local OSS install.

Covers: current default model ids, the ``local-dev`` sentinel handling, the CLI
text-mode error surfacing, the OpenTelemetry teardown-noise filter, the serve
startup notice, and per-subagent / inherited child model routing.
"""

from __future__ import annotations

import logging

from magi_agent.cli.headless import _text_mode_body
from magi_agent.cli.protocol import ResultFrame
from magi_agent.cli.providers import _DEFAULT_MODEL, resolve_provider_config
from magi_agent.config.env import LOCAL_DEV_MODEL_SENTINEL
from magi_agent.ops.otel_noise import silence_otel_detach_noise
from magi_agent.runtime.adk_turn_runner import AdkTurnRunnerConfig
from magi_agent.runtime.child_runner_boundary import (
    ChildRunnerConfig,
    ChildTaskRequest,
)
from magi_agent.runtime.model_tiers import ModelTierRegistry


# --------------------------------------------------------------------------- #
# Default model ids                                                            #
# --------------------------------------------------------------------------- #
def test_default_models_are_current_not_retired() -> None:
    # Guards against the stale ids (gpt-4o / gemini-2.0-flash / claude-sonnet-4-5)
    # that produced model-not-found on a fresh `magi <prompt>`.
    assert _DEFAULT_MODEL["anthropic"] == "claude-sonnet-4-6"
    assert _DEFAULT_MODEL["openai"] == "gpt-5.5"
    assert _DEFAULT_MODEL["gemini"] == "gemini-3.5-flash"


def test_resolve_provider_uses_current_default_model_per_key() -> None:
    cfg = resolve_provider_config(
        env={"GEMINI_API_KEY": "k"},
        config={},
    )
    assert cfg is not None
    assert cfg.provider == "gemini"
    assert cfg.model == "gemini-3.5-flash"
    assert cfg.litellm_model == "gemini/gemini-3.5-flash"


# --------------------------------------------------------------------------- #
# local-dev sentinel                                                           #
# --------------------------------------------------------------------------- #
def test_local_dev_sentinel_value() -> None:
    assert LOCAL_DEV_MODEL_SENTINEL == "local-dev"


def test_no_model_override_falls_through_to_provider_default() -> None:
    # When the dashboard treats the sentinel as "unset" it passes model=None;
    # resolution must then use the per-provider default, NOT "<provider>/local-dev".
    cfg = resolve_provider_config(
        model_override=None,
        env={"ANTHROPIC_API_KEY": "k"},
        config={},
    )
    assert cfg is not None
    assert cfg.model == "claude-sonnet-4-6"
    assert "local-dev" not in cfg.litellm_model


# --------------------------------------------------------------------------- #
# CLI text-mode error surfacing                                               #
# --------------------------------------------------------------------------- #
def test_text_mode_body_returns_assistant_text_on_success() -> None:
    frame = ResultFrame(result="hello there", is_error=False)
    assert _text_mode_body(frame) == "hello there"


def test_text_mode_body_surfaces_error_when_no_text() -> None:
    frame = ResultFrame(
        result=None,
        is_error=True,
        subtype="error_during_execution",
        errors=["litellm.AuthenticationError: invalid x-api-key"],
    )
    body = _text_mode_body(frame)
    assert "invalid x-api-key" in body
    assert "API key" in body  # actionable hint, not a bare empty line


def test_text_mode_body_empty_on_non_error_no_text() -> None:
    frame = ResultFrame(result=None, is_error=False)
    assert _text_mode_body(frame) == ""


# --------------------------------------------------------------------------- #
# OpenTelemetry teardown noise                                                 #
# --------------------------------------------------------------------------- #
def test_silence_otel_detach_noise_drops_only_target(caplog) -> None:
    silence_otel_detach_noise()
    logger = logging.getLogger("opentelemetry.context")
    with caplog.at_level(logging.ERROR, logger="opentelemetry.context"):
        logger.error("Failed to detach context")
        logger.error("some other real error")
    messages = [r.getMessage() for r in caplog.records]
    assert "Failed to detach context" not in messages
    assert "some other real error" in messages


def test_silence_otel_detach_noise_is_idempotent() -> None:
    logger = logging.getLogger("opentelemetry.context")
    before = list(logger.filters)
    silence_otel_detach_noise()
    silence_otel_detach_noise()
    # No duplicate filters stacked from repeated calls.
    assert len([f for f in logger.filters if f not in before]) <= 1


# --------------------------------------------------------------------------- #
# Child model: inherited route + per-subagent override                        #
# --------------------------------------------------------------------------- #
def test_child_runner_config_carries_child_route() -> None:
    cfg = ChildRunnerConfig(childProvider="openai", childModel="gpt-5.5")
    assert cfg.child_provider == "openai"
    assert cfg.child_model == "gpt-5.5"


def test_child_runner_config_defaults_preserve_historical_route() -> None:
    cfg = ChildRunnerConfig()
    assert cfg.child_provider == "google"
    assert cfg.child_model == "gemini-3.5-flash"


def test_child_task_request_accepts_per_subagent_override() -> None:
    req = ChildTaskRequest(
        parentExecutionId="p",
        turnId="t",
        taskId="k",
        objective="do work",
        provider="anthropic",
        model="haiku",
    )
    assert req.provider == "anthropic"
    assert req.model == "haiku"


def _resolve_child_route(config: ChildRunnerConfig, request: ChildTaskRequest):
    """Mirror the boundary's spawn-time resolution (override > config)."""
    provider = request.provider or config.child_provider
    model = request.model or config.child_model
    tier = ModelTierRegistry.with_defaults().resolve(provider=provider, model=model).tier
    return AdkTurnRunnerConfig(enabled=True, provider=provider, model=model, modelTier=tier)


def test_child_spawn_uses_request_override_over_config() -> None:
    config = ChildRunnerConfig(childProvider="google", childModel="gemini-3.5-flash")
    request = ChildTaskRequest(
        parentExecutionId="p",
        turnId="t",
        taskId="k",
        objective="o",
        provider="openai",
        model="gpt-5.5",
    )
    runner_config = _resolve_child_route(config, request)
    assert (runner_config.provider, runner_config.model) == ("openai", "gpt-5.5")
    assert runner_config.model_tier == "sota"


def test_child_spawn_inherits_config_route_when_no_override() -> None:
    config = ChildRunnerConfig(childProvider="anthropic", childModel="haiku")
    request = ChildTaskRequest(
        parentExecutionId="p", turnId="t", taskId="k", objective="o"
    )
    runner_config = _resolve_child_route(config, request)
    assert (runner_config.provider, runner_config.model) == ("anthropic", "haiku")


def test_child_spawn_rejects_unknown_model_route() -> None:
    config = ChildRunnerConfig(childProvider="anthropic", childModel="claude-bogus-9")
    request = ChildTaskRequest(
        parentExecutionId="p", turnId="t", taskId="k", objective="o"
    )
    import pytest

    with pytest.raises(Exception):
        _resolve_child_route(config, request)
