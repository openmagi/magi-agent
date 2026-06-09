from __future__ import annotations

from collections.abc import Mapping
import os
from typing import Literal

from pydantic import BaseModel, ConfigDict

BROWSER_TOOL_ENABLED_ENV = "MAGI_BROWSER_TOOL_ENABLED"
BROWSER_TOOL_KILL_SWITCH_ENV = "MAGI_BROWSER_TOOL_KILL_SWITCH"
_TRUE_VALUES = frozenset({"1", "on", "true", "yes"})


def _is_true(value: object) -> bool:
    return str(value or "").strip().casefold() in _TRUE_VALUES


class BrowserToolConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    enabled: bool = False
    production_network_enabled: Literal[False] = False
    max_steps: int = 25


def browser_tool_active(*, env: Mapping[str, str] | None = None) -> bool:
    resolved = os.environ if env is None else env
    return _is_true(resolved.get(BROWSER_TOOL_ENABLED_ENV)) and not _is_true(
        resolved.get(BROWSER_TOOL_KILL_SWITCH_ENV)
    )
