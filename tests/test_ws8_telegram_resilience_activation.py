"""WS8 PR8a-3: profile activation of Telegram inbound poll resilience.

The poll_resilience policy + the build_channel_poll_watcher ON branch are already
implemented and wired (PR8a feature); the flag was OFF everywhere. PR8a-3 turns
MAGI_TELEGRAM_POLL_RESILIENCE_ENABLED ON in the local FULL self-host profile and
the hosted resilience control-stage (and up), so a default bot's Telegram poll
loop backs off with a circuit breaker instead of a fixed-interval retry storm.
safe/eval profiles and the hosted off stage keep it OFF. The flag resolves via
env_bool (resolve_poll_resilience_config), not a registry FlagSpec, so it is read
straight off the overlaid env mapping.

Design: WS8 telegram robustness, PR8a-3 (activation).
"""
from __future__ import annotations

from typing import Iterator

import pytest

from magi_agent.gateway.poll_resilience import resolve_poll_resilience_config
from magi_agent.runtime.local_defaults import (
    LOCAL_FULL_RUNTIME_ENV_DEFAULTS,
    apply_local_eval_runtime_defaults,
    apply_local_full_runtime_defaults,
)

_FLAG = "MAGI_TELEGRAM_POLL_RESILIENCE_ENABLED"

_HERMETIC_KEYS = (
    _FLAG,
    "MAGI_RUNTIME_PROFILE",
    "MAGI_AGENT_LOCAL_FULL_RUNTIME_DEFAULTS",
    "MAGI_DEPLOYMENT",
    "MAGI_CONTROL_STAGE",
)


@pytest.fixture
def hermetic_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for key in _HERMETIC_KEYS:
        monkeypatch.delenv(key, raising=False)
    yield


def test_flag_in_local_full_defaults() -> None:
    assert _FLAG in LOCAL_FULL_RUNTIME_ENV_DEFAULTS
    assert LOCAL_FULL_RUNTIME_ENV_DEFAULTS[_FLAG] == "1"


def test_local_full_profile_enables_poll_resilience(hermetic_env: None) -> None:
    env: dict[str, str] = {}
    apply_local_full_runtime_defaults(env)
    assert env.get(_FLAG) == "1"
    # The ON-path tie: the overlaid env produces an enabled config.
    assert resolve_poll_resilience_config(env).enabled is True


def test_hosted_resilience_stage_enables_poll_resilience(hermetic_env: None) -> None:
    from magi_agent.runtime.hosted_defaults import apply_hosted_runtime_defaults

    env = {"MAGI_DEPLOYMENT": "hosted", "MAGI_CONTROL_STAGE": "resilience"}
    apply_hosted_runtime_defaults(env)
    assert env.get(_FLAG) == "1"
    assert resolve_poll_resilience_config(env).enabled is True


@pytest.mark.parametrize("stage", ["full", "hardgate"])
def test_hosted_higher_stages_inherit_poll_resilience(
    hermetic_env: None, stage: str
) -> None:
    from magi_agent.runtime.hosted_defaults import apply_hosted_runtime_defaults

    env = {"MAGI_DEPLOYMENT": "hosted", "MAGI_CONTROL_STAGE": stage}
    apply_hosted_runtime_defaults(env)
    assert env.get(_FLAG) == "1", stage


def test_hosted_off_stage_keeps_poll_resilience_off(hermetic_env: None) -> None:
    from magi_agent.runtime.hosted_defaults import apply_hosted_runtime_defaults

    env = {"MAGI_DEPLOYMENT": "hosted", "MAGI_CONTROL_STAGE": "off"}
    apply_hosted_runtime_defaults(env)
    assert _FLAG not in env
    assert resolve_poll_resilience_config(env).enabled is False


@pytest.mark.parametrize("profile", ["safe", "off", "minimal", "conservative"])
def test_safe_profile_keeps_poll_resilience_off(
    hermetic_env: None, profile: str
) -> None:
    env = {"MAGI_RUNTIME_PROFILE": profile}
    apply_local_full_runtime_defaults(env)
    assert _FLAG not in env, profile
    assert resolve_poll_resilience_config(env).enabled is False, profile


def test_eval_profile_keeps_poll_resilience_off(hermetic_env: None) -> None:
    env: dict[str, str] = {}
    apply_local_eval_runtime_defaults(env)
    assert _FLAG not in env
    assert resolve_poll_resilience_config(env).enabled is False


def test_explicit_off_overrides_full_profile(hermetic_env: None) -> None:
    env = {_FLAG: "0"}
    apply_local_full_runtime_defaults(env)
    assert env.get(_FLAG) == "0"
    assert resolve_poll_resilience_config(env).enabled is False
