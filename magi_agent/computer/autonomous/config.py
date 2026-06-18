from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict

COMPUTER_TOOL_ENABLED_ENV = "MAGI_COMPUTER_TOOL_ENABLED"
COMPUTER_TOOL_KILL_SWITCH_ENV = "MAGI_COMPUTER_TOOL_KILL_SWITCH"


class ComputerToolConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    enabled: bool = False
    max_steps: int = 25


def computer_tool_active(*, env: Mapping[str, str] | None = None) -> bool:
    """Whether the autonomous macOS computer-use tool is active.

    Delegates to the env-layer single-source helper (enable flag + kill-switch).
    """
    from magi_agent.config.env import computer_tool_enabled

    return computer_tool_enabled(env=env)
