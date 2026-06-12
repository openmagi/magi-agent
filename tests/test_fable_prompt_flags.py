"""Flag helpers for the Fable prompt-pattern port (D1-D4) — default OFF."""
from __future__ import annotations

import pytest

from magi_agent.config.env import (
    is_prompt_examples_enabled,
    is_prompt_redflags_enabled,
    is_prompt_search_rules_enabled,
    is_tool_usage_guidance_enabled,
)

_HELPERS = (
    (is_tool_usage_guidance_enabled, "MAGI_TOOL_USAGE_GUIDANCE_ENABLED"),
    (is_prompt_examples_enabled, "MAGI_PROMPT_EXAMPLES_ENABLED"),
    (is_prompt_search_rules_enabled, "MAGI_PROMPT_SEARCH_RULES_ENABLED"),
    (is_prompt_redflags_enabled, "MAGI_PROMPT_REDFLAGS_ENABLED"),
)


@pytest.mark.parametrize(("helper", "env_name"), _HELPERS)
def test_default_off(helper, env_name) -> None:
    assert helper({}) is False
    assert helper({env_name: ""}) is False
    assert helper({env_name: "0"}) is False
    assert helper({env_name: "false"}) is False


@pytest.mark.parametrize(("helper", "env_name"), _HELPERS)
def test_truthy_opt_in(helper, env_name) -> None:
    assert helper({env_name: "1"}) is True
    assert helper({env_name: "true"}) is True
    assert helper({env_name: "on"}) is True


@pytest.mark.parametrize(("helper", "env_name"), _HELPERS)
def test_flags_are_independent(helper, env_name) -> None:
    others = {name: "1" for _, name in _HELPERS if name != env_name}
    assert helper(others) is False
