from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ActivityEvent(BaseModel):
    """A sanitized, persistable record of one bot-activity signal."""

    model_config = ConfigDict(extra="forbid")

    ts: float = Field(default_factory=time.time)
    session_id: str | None = None
    run_id: str | None = None
    parent_run_id: str | None = None
    kind: str
    tool_name: str | None = None
    status: str | None = None
    summary: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    elapsed_ms: int | None = None
