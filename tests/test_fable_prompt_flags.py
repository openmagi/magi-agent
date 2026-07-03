"""Flag helpers for the Fable prompt-pattern port (D1-D4).

The 3 guidance flags below were promoted _b -> _pb (profile-aware default-ON)
under the no-default-off policy: unset resolves ON in the full runtime profile,
OFF only under an explicit falsy value or a safe/eval profile.
``MAGI_PROMPT_SEARCH_RULES_ENABLED`` stays strict default-OFF (it self-suppresses
without web-search keys).
"""
from __future__ import annotations

import pytest

from magi_agent.config.env import (
    is_prompt_examples_enabled,
    is_prompt_redflags_enabled,
    is_prompt_search_rules_enabled,
    is_tool_usage_guidance_enabled,
)

_DEFAULT_ON = (
    (is_tool_usage_guidance_enabled, "MAGI_TOOL_USAGE_GUIDANCE_ENABLED"),
    (is_prompt_examples_enabled, "MAGI_PROMPT_EXAMPLES_ENABLED"),
    (is_prompt_redflags_enabled, "MAGI_PROMPT_REDFLAGS_ENABLED"),
)
_DEFAULT_OFF = ((is_prompt_search_rules_enabled, "MAGI_PROMPT_SEARCH_RULES_ENABLED"),)
_ALL = _DEFAULT_ON + _DEFAULT_OFF


@pytest.mark.parametrize(("helper", "env_name"), _DEFAULT_ON)
def test_default_on(helper, env_name) -> None:
    assert helper({}) is True  # unset -> ON in the full profile
    assert helper({env_name: "0"}) is False
    assert helper({env_name: "false"}) is False
    assert helper({"MAGI_RUNTIME_PROFILE": "safe"}) is False


@pytest.mark.parametrize(("helper", "env_name"), _DEFAULT_OFF)
def test_default_off(helper, env_name) -> None:
    assert helper({}) is False
    assert helper({env_name: ""}) is False
    assert helper({env_name: "0"}) is False
    assert helper({env_name: "false"}) is False


@pytest.mark.parametrize(("helper", "env_name"), _ALL)
def test_truthy_opt_in(helper, env_name) -> None:
    assert helper({env_name: "1"}) is True
    assert helper({env_name: "true"}) is True
    assert helper({env_name: "on"}) is True


@pytest.mark.parametrize(("helper", "env_name"), _ALL)
def test_explicit_off_wins_over_other_flags(helper, env_name) -> None:
    others_on = {name: "1" for _, name in _ALL if name != env_name}
    assert helper({**others_on, env_name: "0"}) is False
