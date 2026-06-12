"""Env-accessor tests for the grounded-answer guard flag.

Follows the ``is_egress_gate_enabled`` precedent exactly: strict-truthy opt-in,
NOT the runtime-profile default-ON convention. Default OFF when unset.
"""
from __future__ import annotations

import pytest

from magi_agent.config.env import (
    MAGI_GROUNDED_ANSWER_GUARD_ENABLED_ENV,
    is_grounded_answer_guard_enabled,
)


def test_env_name_is_stable() -> None:
    assert MAGI_GROUNDED_ANSWER_GUARD_ENABLED_ENV == "MAGI_GROUNDED_ANSWER_GUARD_ENABLED"


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "FALSE", "Off"])
def test_falsy_values_disable(value: str) -> None:
    assert is_grounded_answer_guard_enabled({MAGI_GROUNDED_ANSWER_GUARD_ENABLED_ENV: value}) is False


def test_unset_is_disabled() -> None:
    assert is_grounded_answer_guard_enabled({}) is False


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", "TRUE", "On"])
def test_truthy_values_enable(value: str) -> None:
    assert is_grounded_answer_guard_enabled({MAGI_GROUNDED_ANSWER_GUARD_ENABLED_ENV: value}) is True


def test_does_not_follow_runtime_profile_default_on() -> None:
    # Unlike runtime-profile features, an unset flag stays OFF even when the
    # runtime profile would otherwise enable default-ON features.
    assert is_grounded_answer_guard_enabled({"MAGI_RUNTIME_PROFILE": "eval"}) is False
