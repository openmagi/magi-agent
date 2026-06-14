from __future__ import annotations

import os
from collections.abc import Mapping, Sequence

from magi_agent.runtime.child_runner_live import (
    LIVE_CHILD_RUNNER_KILL_SWITCH_ENV,
    is_live_child_runner_enabled,
)
from magi_agent.runtime.child_toolset import resolve_child_toolset_profile


def child_runner_availability_metadata(
    *,
    legacy_child_execution_allowed: bool,
    allowed_tool_names: Sequence[str] = (),
    env: Mapping[str, str] | None = None,
) -> dict[str, object]:
    source = os.environ if env is None else env
    live_enabled = is_live_child_runner_enabled(source)
    kill_switch_enabled = _env_truthy(source.get(LIVE_CHILD_RUNNER_KILL_SWITCH_ENV, ""))
    spawn_agent_exposed = "SpawnAgent" in set(allowed_tool_names)
    live_attached = live_enabled and spawn_agent_exposed
    return {
        "legacyChildExecutionAllowed": bool(legacy_child_execution_allowed),
        "liveChildRunnerEnabled": live_enabled,
        "liveChildRunnerKillSwitchEnabled": kill_switch_enabled,
        "childRunnerToolset": resolve_child_toolset_profile(source),
        "spawnAgentExposed": spawn_agent_exposed,
        "liveChildRunnerAttached": live_attached,
        "effectiveChildRunnerAvailable": live_attached,
        "availabilityStatus": _availability_status(
            live_enabled=live_enabled,
            kill_switch_enabled=kill_switch_enabled,
            spawn_agent_exposed=spawn_agent_exposed,
        ),
    }


def _availability_status(
    *,
    live_enabled: bool,
    kill_switch_enabled: bool,
    spawn_agent_exposed: bool,
) -> str:
    if live_enabled and spawn_agent_exposed:
        return "live_attached"
    if kill_switch_enabled:
        return "kill_switch_enabled"
    if live_enabled and not spawn_agent_exposed:
        return "spawn_agent_not_exposed"
    return "disabled"


def _env_truthy(value: object) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


__all__ = ["child_runner_availability_metadata"]
