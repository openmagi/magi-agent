from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict

BROWSER_TOOL_ENABLED_ENV = "MAGI_BROWSER_TOOL_ENABLED"
BROWSER_TOOL_KILL_SWITCH_ENV = "MAGI_BROWSER_TOOL_KILL_SWITCH"


class BrowserToolConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    enabled: bool = False
    production_network_enabled: Literal[False] = False
    max_steps: int = 25


def browser_tool_active(*, env: Mapping[str, str] | None = None) -> bool:
    """Whether the autonomous browser tool is active.

    Delegates to the env-layer single-source helper, which honors BOTH the
    enable flag and the kill-switch (kill-switch wins). Importing the low-level
    config helper from this higher-level module keeps a single authority and
    avoids a layering violation (``config.env`` must not import this module).
    """
    from magi_agent.config.env import browser_tool_enabled

    return browser_tool_enabled(env=env)
