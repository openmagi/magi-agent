"""WS9 PR9c: profile activation of MCP connection resilience.

The McpResilience primitive (PR9a) and the live composio dispatcher wiring (PR9a-2,
keyed on a per-endpoint digest) are implemented and wired but inert. PR9c turns
MAGI_MCP_RESILIENCE_ENABLED ON in the local FULL self-host profile and the hosted
resilience control-stage (and up), so a default bot's composio MCP tool calls get a
caller-bounded timeout + per-endpoint circuit breaker instead of silently burning a
turn on a dead endpoint. safe/eval profiles and the hosted off stage keep it OFF.
The flag is a registered kind="bool" FlagSpec resolved strictly (profile-independent)
via flag_bool, so it only turns on when a profile/overlay dict explicitly sets it.

Design: WS9 MCP robustness, PR9c (activation).
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

_FLAG = "MAGI_MCP_RESILIENCE_ENABLED"

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


def test_local_full_profile_enables_mcp_resilience(hermetic_env: None) -> None:
    env: dict[str, str] = {}
    apply_local_full_runtime_defaults(env)
    assert env.get(_FLAG) == "1"
    assert flag_bool(_FLAG, env=env) is True


def test_full_profile_builds_an_enabled_policy(hermetic_env: None) -> None:
    # The ON-path tie: applying the full profile makes parse_mcp_resilience_env
    # return an enabled policy (it is disabled when the flag is OFF).
    from magi_agent.config.env import parse_mcp_resilience_env

    env: dict[str, str] = {}
    apply_local_full_runtime_defaults(env)
    assert parse_mcp_resilience_env(env).enabled is True


def test_hosted_resilience_stage_enables_mcp_resilience(hermetic_env: None) -> None:
    from magi_agent.runtime.hosted_defaults import apply_hosted_runtime_defaults

    env = {"MAGI_DEPLOYMENT": "hosted", "MAGI_CONTROL_STAGE": "resilience"}
    apply_hosted_runtime_defaults(env)
    assert env.get(_FLAG) == "1"
    assert flag_bool(_FLAG, env=env) is True


@pytest.mark.parametrize("stage", ["full", "hardgate"])
def test_hosted_higher_stages_inherit_mcp_resilience(
    hermetic_env: None, stage: str
) -> None:
    from magi_agent.runtime.hosted_defaults import apply_hosted_runtime_defaults

    env = {"MAGI_DEPLOYMENT": "hosted", "MAGI_CONTROL_STAGE": stage}
    apply_hosted_runtime_defaults(env)
    assert env.get(_FLAG) == "1", stage


def test_hosted_off_stage_keeps_mcp_resilience_off(hermetic_env: None) -> None:
    from magi_agent.runtime.hosted_defaults import apply_hosted_runtime_defaults

    env = {"MAGI_DEPLOYMENT": "hosted", "MAGI_CONTROL_STAGE": "off"}
    apply_hosted_runtime_defaults(env)
    assert _FLAG not in env
    assert flag_bool(_FLAG, env=env) is False


@pytest.mark.parametrize("profile", ["safe", "off", "minimal", "conservative"])
def test_safe_profile_keeps_mcp_resilience_off(
    hermetic_env: None, profile: str
) -> None:
    env = {"MAGI_RUNTIME_PROFILE": profile}
    apply_local_full_runtime_defaults(env)
    assert _FLAG not in env, profile
    assert flag_bool(_FLAG, env=env) is False, profile


def test_eval_profile_keeps_mcp_resilience_off(hermetic_env: None) -> None:
    env: dict[str, str] = {}
    apply_local_eval_runtime_defaults(env)
    assert _FLAG not in env
    assert flag_bool(_FLAG, env=env) is False


def test_explicit_off_overrides_full_profile(hermetic_env: None) -> None:
    env = {_FLAG: "0"}
    apply_local_full_runtime_defaults(env)
    assert env.get(_FLAG) == "0"
    assert flag_bool(_FLAG, env=env) is False
