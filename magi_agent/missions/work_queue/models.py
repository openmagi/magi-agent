from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

TaskStatus = Literal[
    "triage", "todo", "ready", "running",
    "completed", "blocked", "failed", "archived",
]
DONE_STATES: frozenset[str] = frozenset({"completed", "archived"})
TERMINAL_STATES: frozenset[str] = frozenset({"completed", "blocked", "failed", "archived"})

class WorkTask(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: str
    title: str
    status: TaskStatus
    created_at: int
    body: str | None = None
    assignee: str | None = None
    priority: int = 0
    tenant: str | None = None
    session_id: str | None = None
    idempotency_key: str | None = None
    claim_lock: str | None = None
    claim_expires: int | None = None
    worker_pid: int | None = None
    last_heartbeat_at: int | None = None
    current_run_id: int | None = None
    consecutive_failures: int = 0
    max_retries: int | None = None
    goal_mode: bool = False
    goal_max_turns: int | None = None
    result: str | None = None
    last_failure_error: str | None = None
    started_at: int | None = None
    completed_at: int | None = None
