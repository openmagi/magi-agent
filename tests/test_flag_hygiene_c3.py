"""C3 flag-hygiene batch (N-43, N-21 targeted, N-37).

Locks in:
  1. Deletion of the reader-less master gate MAGI_WORK_QUEUE_ENABLED (the three
     real gates survive).
  2. Parity of the two same-named ``_env_shadow_flag`` helpers (scheduler and
     skill_curator) after aligning the false-set.
  3. Agreement between ErrorRecoveryConfig.from_env and the canonical
     parse_error_recovery_env reader (default = registered profile-bool).
  4. Removal of the dead ``_TRUTHY_VALUES`` constant in telemetry.trace_context.
"""

from __future__ import annotations

import os
from unittest import mock

import pytest


def test_work_queue_master_flag_is_gone() -> None:
    from magi_agent.config.flags import FLAGS_BY_NAME

    assert "MAGI_WORK_QUEUE_ENABLED" not in FLAGS_BY_NAME
    # The three real gates that actually drive behaviour survive.
    for name in (
        "MAGI_WORK_QUEUE_EXECUTOR_ENABLED",
        "MAGI_WORK_QUEUE_BOARD_API_ENABLED",
        "MAGI_WORK_QUEUE_NOTIFY_ENABLED",
    ):
        assert name in FLAGS_BY_NAME


def test_dogfood_profile_does_not_export_dead_work_queue_flag() -> None:
    from tests.test_dogfood_full_on_profile import _load_profile

    profile = _load_profile()
    assert "MAGI_WORK_QUEUE_ENABLED" not in profile
    # The surviving gates are still exported.
    assert "MAGI_WORK_QUEUE_BOARD_API_ENABLED" in profile
    assert "MAGI_WORK_QUEUE_NOTIFY_ENABLED" in profile


_SHADOW_CASES = [
    ("1", True),
    ("true", True),
    ("yes", True),
    ("on", True),
    ("0", False),
    ("false", False),
    ("no", False),
    ("off", False),
    ("garbage", True),  # permissive-true convention: unknown -> True
    (None, True),  # unset -> default (True)
]


@pytest.mark.parametrize("value,expected", _SHADOW_CASES)
def test_env_shadow_flag_parity_between_scheduler_and_curator(
    monkeypatch: pytest.MonkeyPatch, value, expected
) -> None:
    from magi_agent.harness.scheduler_job_execution import (
        _env_shadow_flag as scheduler_flag,
    )
    from magi_agent.harness.skill_curator import (
        _env_shadow_flag as curator_flag,
    )

    name = "MAGI_TEST_SHADOW_X"
    if value is None:
        monkeypatch.delenv(name, raising=False)
    else:
        monkeypatch.setenv(name, value)

    scheduler_result = scheduler_flag(name, default=True)
    curator_result = curator_flag(name, default=True)
    assert scheduler_result == curator_result
    assert scheduler_result is expected


_RECOVERY_MATRIX = [
    {},
    {"MAGI_ERROR_RECOVERY_ENABLED": "on"},
    {"MAGI_ERROR_RECOVERY_ENABLED": "0"},
    {"MAGI_ERROR_RECOVERY_ENABLED": "false"},
    {"MAGI_RUNTIME_PROFILE": "safe"},
    {"MAGI_RUNTIME_PROFILE": "full"},
]


@pytest.mark.parametrize("case", _RECOVERY_MATRIX)
def test_error_recovery_readers_agree(case: dict[str, str]) -> None:
    from magi_agent.config.env import parse_error_recovery_env
    from magi_agent.runtime.error_recovery.types import ErrorRecoveryConfig

    with mock.patch.dict(os.environ, case, clear=True):
        from_env_value = ErrorRecoveryConfig.from_env().recovery_enabled
        canonical_value = parse_error_recovery_env(os.environ).enabled
    assert from_env_value == canonical_value


def test_error_recovery_default_is_profile_on() -> None:
    from magi_agent.runtime.error_recovery.types import ErrorRecoveryConfig

    # Unset flag + unset profile resolves to the full profile -> ON.
    with mock.patch.dict(os.environ, {}, clear=True):
        assert ErrorRecoveryConfig.from_env().recovery_enabled is True


def test_trace_context_has_no_dead_truthy_constant() -> None:
    from magi_agent.telemetry import trace_context

    assert not hasattr(trace_context, "_TRUTHY_VALUES")
