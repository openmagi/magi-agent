"""M4 (missions x work-queue unification) - local-profile activation.

PR-M4 flips the work-queue / background-task surface ON for the local ``full``
profile (and ``lab``, which layers on top of ``full``):

  * ``MAGI_WORK_QUEUE_BOARD_API_ENABLED`` (the read-only board HTTP API),
  * ``MAGI_WORK_QUEUE_NOTIFY_ENABLED`` (the terminal-event notifier), and
  * the background-task tool pair
    ``MAGI_BACKGROUND_TASK_TOOL_ENABLED`` + ``MAGI_BACKGROUND_TASKS_ATTACHED``
    (which together un-block ``run_in_background`` -> a real ``WorkTask``).

It deliberately does NOT enable ``MAGI_SCHEDULER_ATTACHED`` (cron scheduling is a
separate, unbuilt surface). The safe / eval / off profiles keep every one of
these OFF, and an explicit operator ``0`` still wins (setdefault semantics).

BOARD_API/NOTIFY are profile-aware default-ON (``_pb``) so they resolve ON under
the full profile; the background pair are raw env reads (NOT registered flags),
so the local overlay is the only thing that turns them on for a fresh
``magi serve`` install. Both are asserted through their REAL runtime readers, not
just by membership in the overlay dict (flag-promotion rule: prove ON).
"""
from __future__ import annotations

from typing import Iterator

import pytest

from magi_agent.config.flags import flag_profile_bool
from magi_agent.plugins.native.scheduled_work import (
    _background_tasks_attached,
    background_task_tool_enabled,
)
from magi_agent.runtime.local_defaults import (
    apply_local_eval_runtime_defaults,
    apply_local_full_runtime_defaults,
)

# Profile-aware default-ON (``_pb``) gates, read via flag_profile_bool.
_WORK_QUEUE_PB_FLAGS = (
    "MAGI_WORK_QUEUE_BOARD_API_ENABLED",
    "MAGI_WORK_QUEUE_NOTIFY_ENABLED",
)
# Raw env-read background-task flags (NOT in the flag registry).
_BACKGROUND_FLAGS = (
    "MAGI_BACKGROUND_TASK_TOOL_ENABLED",
    "MAGI_BACKGROUND_TASKS_ATTACHED",
)
_M4_FLAGS = (*_WORK_QUEUE_PB_FLAGS, *_BACKGROUND_FLAGS)
_SCHEDULER_FLAG = "MAGI_SCHEDULER_ATTACHED"

# Clear every knob first so an exported shell env cannot give a false green.
_HERMETIC_KEYS = (
    *_M4_FLAGS,
    _SCHEDULER_FLAG,
    "MAGI_RUNTIME_PROFILE",
    "MAGI_AGENT_LOCAL_FULL_RUNTIME_DEFAULTS",
)


@pytest.fixture
def hermetic_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for key in _HERMETIC_KEYS:
        monkeypatch.delenv(key, raising=False)
    yield


def test_full_profile_enables_work_queue_surface(hermetic_env: None) -> None:
    env: dict[str, str] = {}
    apply_local_full_runtime_defaults(env)
    # All four M4 flags are seeded ON by the full overlay ...
    for flag in _M4_FLAGS:
        assert env.get(flag) == "1", flag
    # ... the two _pb gates resolve ON via the profile-aware reader ...
    for flag in _WORK_QUEUE_PB_FLAGS:
        assert flag_profile_bool(flag, env=env) is True, flag
    # ... and the background-task tool is live + attached via its real readers.
    assert background_task_tool_enabled(env) is True
    assert _background_tasks_attached(env) is True


def test_full_profile_leaves_scheduler_unset(hermetic_env: None) -> None:
    # Cron scheduling is an unbuilt surface; the overlay must NOT attach it.
    env: dict[str, str] = {}
    apply_local_full_runtime_defaults(env)
    assert _SCHEDULER_FLAG not in env


def test_lab_profile_inherits_work_queue_surface(hermetic_env: None) -> None:
    from magi_agent.runtime.local_defaults import apply_lab_runtime_defaults

    env: dict[str, str] = {}
    apply_lab_runtime_defaults(env)
    for flag in _M4_FLAGS:
        assert env.get(flag) == "1", flag
    assert _SCHEDULER_FLAG not in env


@pytest.mark.parametrize("profile", ["safe", "off", "minimal", "conservative"])
def test_safe_profile_keeps_work_queue_surface_off(
    hermetic_env: None, profile: str
) -> None:
    env = {"MAGI_RUNTIME_PROFILE": profile}
    apply_local_full_runtime_defaults(env)
    for flag in _M4_FLAGS:
        assert flag not in env, f"{profile}:{flag}"
    # The _pb gates resolve OFF under a safe profile (profile-aware) ...
    for flag in _WORK_QUEUE_PB_FLAGS:
        assert flag_profile_bool(flag, env=env) is False, f"{profile}:{flag}"
    # ... and the background readers stay OFF (raw env unset).
    assert background_task_tool_enabled(env) is False
    assert _background_tasks_attached(env) is False


def test_eval_profile_keeps_work_queue_surface_off(hermetic_env: None) -> None:
    env: dict[str, str] = {}
    apply_local_eval_runtime_defaults(env)
    for flag in _M4_FLAGS:
        assert flag not in env, flag


def test_explicit_off_overrides_full_profile(hermetic_env: None) -> None:
    # setdefault semantics: an explicit operator "0" walks each feature back.
    env = {flag: "0" for flag in _M4_FLAGS}
    apply_local_full_runtime_defaults(env)
    for flag in _M4_FLAGS:
        assert env[flag] == "0", flag
    assert background_task_tool_enabled(env) is False
    assert _background_tasks_attached(env) is False
