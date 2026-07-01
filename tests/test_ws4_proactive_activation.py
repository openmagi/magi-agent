"""WS4 PR4b: profile activation of live proactive context recovery.

The #641-class guard at the resolution layer. PR4a wired tiers 6-7 into the live
MagiContextCompactionPlugin behind MAGI_CONTEXT_PROACTIVE_RECOVERY_ENABLED
(default-OFF); PR4b turns it ON in the local FULL profile and the hosted FULL
stage (NOT resilience, NOT eval/safe). These tests pin the profile resolution and
the tie to the compaction-env field the live plugin actually reads. The full
plugin-drive ON-path coverage lives in tests/test_context_compaction_proactive.py
(run with the flag ON by the ws4-context-onpath CI job).

Design: WS4 context proactive recovery, PR4b (activation + ON-path CI).
"""
from __future__ import annotations

from typing import Iterator

import pytest

from magi_agent.config.env import parse_context_compaction_env
from magi_agent.config.flags import flag_bool
from magi_agent.runtime.local_defaults import (
    apply_local_eval_runtime_defaults,
    apply_local_full_runtime_defaults,
)

_PROACTIVE_FLAG = "MAGI_CONTEXT_PROACTIVE_RECOVERY_ENABLED"
_COMPACTION_FLAG = "MAGI_CONTEXT_COMPACTION_ENABLED"

# Clear every knob these tests resolve so an exported shell env cannot give a
# false green (R4: non-hermetic suites are the documented hazard).
_HERMETIC_KEYS = (
    _PROACTIVE_FLAG,
    _COMPACTION_FLAG,
    "MAGI_CONTEXT_CRITICAL_THRESHOLD",
    "MAGI_CONTEXT_MGMT_ENABLED",
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


def test_local_full_profile_enables_proactive(hermetic_env: None) -> None:
    env: dict[str, str] = {}
    apply_local_full_runtime_defaults(env)
    # Both masters resolve ON together: the live plugin (compaction master) honors
    # the proactive flag, so tiers 6-7 engage at CRITICAL.
    assert env.get(_PROACTIVE_FLAG) == "1"
    # COMPACTION is a profile_bool flag (read via its own resolver, not flag_bool).
    assert env.get(_COMPACTION_FLAG) == "1"
    assert flag_bool(_PROACTIVE_FLAG, env=env) is True


def test_full_profile_resolves_proactive_in_compaction_env(hermetic_env: None) -> None:
    # The ON-path tie: applying the full profile flips the very field the live
    # plugin reads (parse_context_compaction_env, consumed by the plugin builder).
    env: dict[str, str] = {}
    apply_local_full_runtime_defaults(env)
    parsed = parse_context_compaction_env(env)
    assert parsed.proactive_recovery_enabled is True


def test_hosted_full_stage_enables_proactive(hermetic_env: None) -> None:
    from magi_agent.runtime.hosted_defaults import apply_hosted_runtime_defaults

    env = {"MAGI_DEPLOYMENT": "hosted", "MAGI_CONTROL_STAGE": "full"}
    apply_hosted_runtime_defaults(env)
    assert env.get(_PROACTIVE_FLAG) == "1"
    assert flag_bool(_PROACTIVE_FLAG, env=env) is True
    # Compaction (the live-plugin master) is also ON at the full stage.
    assert env.get(_COMPACTION_FLAG) == "1"


def test_hosted_resilience_stage_does_not_enable_proactive(hermetic_env: None) -> None:
    from magi_agent.runtime.hosted_defaults import apply_hosted_runtime_defaults

    env = {"MAGI_DEPLOYMENT": "hosted", "MAGI_CONTROL_STAGE": "resilience"}
    apply_hosted_runtime_defaults(env)
    # The resilience stage activates neither the proactive flag nor the live
    # compaction master, so the live plugin does not engage tiers 6-7.
    assert _PROACTIVE_FLAG not in env
    assert flag_bool(_PROACTIVE_FLAG, env=env) is False
    assert _COMPACTION_FLAG not in env


def test_hosted_off_stage_keeps_proactive_off(hermetic_env: None) -> None:
    from magi_agent.runtime.hosted_defaults import apply_hosted_runtime_defaults

    env = {"MAGI_DEPLOYMENT": "hosted", "MAGI_CONTROL_STAGE": "off"}
    apply_hosted_runtime_defaults(env)
    assert _PROACTIVE_FLAG not in env
    assert flag_bool(_PROACTIVE_FLAG, env=env) is False


@pytest.mark.parametrize("profile", ["safe", "off", "minimal", "conservative"])
def test_safe_profile_keeps_proactive_off(hermetic_env: None, profile: str) -> None:
    env = {"MAGI_RUNTIME_PROFILE": profile}
    apply_local_full_runtime_defaults(env)
    assert _PROACTIVE_FLAG not in env, profile
    assert flag_bool(_PROACTIVE_FLAG, env=env) is False, profile


def test_eval_profile_keeps_proactive_off(hermetic_env: None) -> None:
    env: dict[str, str] = {}
    apply_local_eval_runtime_defaults(env)
    assert _PROACTIVE_FLAG not in env
    assert flag_bool(_PROACTIVE_FLAG, env=env) is False


def test_explicit_off_overrides_full_profile(hermetic_env: None) -> None:
    # setdefault semantics: an operator opt-out wins over the full-profile flip.
    env = {_PROACTIVE_FLAG: "0"}
    apply_local_full_runtime_defaults(env)
    assert env.get(_PROACTIVE_FLAG) == "0"
    assert flag_bool(_PROACTIVE_FLAG, env=env) is False
    assert parse_context_compaction_env(env).proactive_recovery_enabled is False
