"""WS5 PR5a: profile activation of empty-response recovery.

The recovery + grace helpers (should_recover_empty / should_grace) and the engine
re-invocation seam are already implemented and wired; the flag was lab-only. PR5a
turns MAGI_EMPTY_RESPONSE_RECOVERY_ENABLED ON in the local FULL self-host profile
and the hosted resilience stage (and up), so a default turn that runs tools but
emits no user-visible text is re-invoked once instead of completing blank.
safe/eval/off and the hosted off stage keep it OFF. The engine-consumption ON-path
is covered by the empty-response suites run with the flag ON by CI.

Design: WS5 empty-response recovery, PR5a (activation).
"""
from __future__ import annotations

from typing import Iterator

import pytest

from magi_agent.config.flags import flag_bool
from magi_agent.runtime.local_defaults import (
    LOCAL_FULL_RUNTIME_ENV_DEFAULTS,
    apply_local_eval_runtime_defaults,
    apply_local_full_runtime_defaults,
)

_FLAG = "MAGI_EMPTY_RESPONSE_RECOVERY_ENABLED"

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


def test_local_full_profile_enables_empty_response_recovery(hermetic_env: None) -> None:
    env: dict[str, str] = {}
    apply_local_full_runtime_defaults(env)
    assert env.get(_FLAG) == "1"
    assert flag_bool(_FLAG, env=env) is True


def test_full_profile_builds_a_recovery_config(hermetic_env: None) -> None:
    # The ON-path tie: applying the full profile makes the engine's config builder
    # return a real config (it returns None when the flag is OFF).
    from magi_agent.cli.engine import build_empty_response_recovery_config

    env: dict[str, str] = {}
    apply_local_full_runtime_defaults(env)
    assert build_empty_response_recovery_config(env) is not None


def test_hosted_resilience_stage_enables_empty_response_recovery(hermetic_env: None) -> None:
    from magi_agent.runtime.hosted_defaults import apply_hosted_runtime_defaults

    env = {"MAGI_DEPLOYMENT": "hosted", "MAGI_CONTROL_STAGE": "resilience"}
    apply_hosted_runtime_defaults(env)
    assert env.get(_FLAG) == "1"
    assert flag_bool(_FLAG, env=env) is True


def test_hosted_off_stage_keeps_empty_response_recovery_off(hermetic_env: None) -> None:
    from magi_agent.runtime.hosted_defaults import apply_hosted_runtime_defaults

    env = {"MAGI_DEPLOYMENT": "hosted", "MAGI_CONTROL_STAGE": "off"}
    apply_hosted_runtime_defaults(env)
    assert _FLAG not in env
    assert flag_bool(_FLAG, env=env) is False


@pytest.mark.parametrize("profile", ["safe", "off", "minimal", "conservative"])
def test_safe_profile_keeps_empty_response_recovery_off(
    hermetic_env: None, profile: str
) -> None:
    env = {"MAGI_RUNTIME_PROFILE": profile}
    apply_local_full_runtime_defaults(env)
    assert _FLAG not in env, profile
    assert flag_bool(_FLAG, env=env) is False, profile


def test_eval_profile_keeps_empty_response_recovery_off(hermetic_env: None) -> None:
    env: dict[str, str] = {}
    apply_local_eval_runtime_defaults(env)
    assert _FLAG not in env
    assert flag_bool(_FLAG, env=env) is False


def test_explicit_off_overrides_full_profile(hermetic_env: None) -> None:
    env = {_FLAG: "0"}
    apply_local_full_runtime_defaults(env)
    assert env.get(_FLAG) == "0"
    assert flag_bool(_FLAG, env=env) is False
