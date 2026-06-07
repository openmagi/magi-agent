"""B1 — GoalState: persistent session-scoped goal state layer.

Pure state CRUD.  No judge, no loop continuation, no agent spawn.
traffic_attached and execution_attached remain Literal[False] (enforced in
GoalLoopPolicy — see goal_loop.py).

Store hierarchy
---------------
GoalStateStore (Protocol)
    InMemoryGoalStateStore  — deterministic fake for tests
    SqliteGoalStateStore    — durable backend reusing the migrations seam
                              from magi_agent/storage/migrations.py

Persistence: GoalState rows are stored in a dedicated ``goal_states`` table
added as migration 3 to the same SQLite DB used by SessionSqliteStore
(default: .openmagi/sessions.db).  Each row holds the full GoalState JSON so
a fresh SqliteGoalStateStore instance can reconstruct state after a restart.

Forbidden imports: google.adk, network, agent-spawn (verified by test).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from magi_agent.harness.goal_loop import GoalLoopOptOutState
from magi_agent.storage.migrations import run_migrations


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

DEFAULT_GOAL_LOOP_MAX_TURNS: int = 20

GoalStateStatus = Literal["active", "satisfied", "exhausted", "preempted", "cleared"]


# ---------------------------------------------------------------------------
# GoalState model
# ---------------------------------------------------------------------------

class GoalState(BaseModel):
    """Frozen snapshot of a session's active goal.

    Use ``model_copy(update=...)`` to derive updated states (immutability
    pattern mirrored from ScheduledJobRecord / GoalLoopPolicy).
    """

    model_config = ConfigDict(
        frozen=True,
        populate_by_name=True,
        extra="forbid",
    )

    goal: str
    session_id: str = Field(alias="sessionId")
    turns_used: int = Field(default=0, alias="turnsUsed")
    max_turns: int = Field(default=DEFAULT_GOAL_LOOP_MAX_TURNS, alias="maxTurns")
    status: GoalStateStatus = Field(default="active")


# ---------------------------------------------------------------------------
# GoalStateStore Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class GoalStateStore(Protocol):
    """Minimal seam for session-scoped GoalState CRUD.

    Concrete implementations may be backed by SQLite (SqliteGoalStateStore)
    or an in-memory dict (InMemoryGoalStateStore) for tests.
    """

    def set_goal(
        self,
        session_id: str,
        goal: str,
        *,
        max_turns: int = DEFAULT_GOAL_LOOP_MAX_TURNS,
        opt_out: GoalLoopOptOutState | None = None,
    ) -> GoalState:
        """Create or replace the active goal for *session_id*.

        Raises ValueError if ``opt_out.opted_out`` is True.
        """
        ...

    def get_goal(self, session_id: str) -> GoalState | None:
        """Return the current GoalState for *session_id*, or None."""
        ...

    def advance(self, session_id: str) -> GoalState:
        """Increment turns_used by 1.

        Transitions status to ``"exhausted"`` when turns_used >= max_turns.
        Returns the new (frozen) GoalState.
        Raises KeyError if no goal is set for *session_id*.
        """
        ...

    def clear(self, session_id: str) -> None:
        """Remove the goal for *session_id*.  No-op if none exists."""
        ...


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _check_opt_out(opt_out: GoalLoopOptOutState | None) -> None:
    if opt_out is not None and opt_out.opted_out:
        raise ValueError(
            "goal loop opted out for this session — cannot set goal"
        )


_TERMINAL_STATUSES: frozenset[GoalStateStatus] = frozenset(
    {"exhausted", "satisfied", "preempted", "cleared"}
)


def _advance_state(current: GoalState) -> GoalState:
    """Return a new GoalState with turns_used incremented by 1.

    Contract:
    - If ``current.status`` is a terminal status (``"exhausted"``,
      ``"satisfied"``, ``"preempted"``, or ``"cleared"``) the state is
      returned UNCHANGED — no counter increment, no status clobber.
      This prevents piling turns_used past max on an exhausted goal and
      protects terminal statuses written by B2/B5 (satisfy/preempt/clear).
    - Only an ``"active"`` goal advances.  When turns_used reaches
      max_turns the status transitions to ``"exhausted"``.
    """
    if current.status in _TERMINAL_STATUSES:
        return current
    new_turns = current.turns_used + 1
    new_status: GoalStateStatus = (
        "exhausted" if new_turns >= current.max_turns else current.status
    )
    return current.model_copy(update={"turns_used": new_turns, "status": new_status})


# ---------------------------------------------------------------------------
# InMemoryGoalStateStore
# ---------------------------------------------------------------------------

class InMemoryGoalStateStore:
    """Deterministic in-memory goal state store for tests and local-fake mode."""

    goal_store_kind = "local_fake"

    def __init__(self) -> None:
        self._states: dict[str, GoalState] = {}

    def set_goal(
        self,
        session_id: str,
        goal: str,
        *,
        max_turns: int = DEFAULT_GOAL_LOOP_MAX_TURNS,
        opt_out: GoalLoopOptOutState | None = None,
    ) -> GoalState:
        _check_opt_out(opt_out)
        gs = GoalState(
            goal=goal,
            sessionId=session_id,
            turnsUsed=0,
            maxTurns=max_turns,
            status="active",
        )
        self._states[session_id] = gs
        return gs

    def get_goal(self, session_id: str) -> GoalState | None:
        return self._states.get(session_id)

    def advance(self, session_id: str) -> GoalState:
        current = self._states.get(session_id)
        if current is None:
            raise KeyError(f"no goal set for session: {session_id!r}")
        updated = _advance_state(current)
        self._states[session_id] = updated
        return updated

    def clear(self, session_id: str) -> None:
        self._states.pop(session_id, None)


# ---------------------------------------------------------------------------
# SqliteGoalStateStore
# ---------------------------------------------------------------------------


class SqliteGoalStateStore:
    """SQLite-backed GoalState store.

    Reuses the same DB file as SessionSqliteStore (default path
    ``.openmagi/sessions.db`` relative to workspace root), adding a
    ``goal_states`` table via migration version 3.

    May be pointed at an isolated DB path (e.g. a tmp_path in tests).
    """

    goal_store_kind = "sqlite"

    def __init__(
        self,
        db_path: str | Path,
        *,
        workspace_root: str | Path = "",
    ) -> None:
        if workspace_root:
            self._db_path = Path(workspace_root) / db_path
        else:
            # Consistent with session_store.py: fall back to cwd so a bare
            # relative path like "goals.db" resolves predictably.
            self._db_path = Path.cwd() / db_path if not Path(db_path).is_absolute() else Path(db_path)
        self._conn: sqlite3.Connection | None = None

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path), timeout=10, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        run_migrations(conn)
        self._conn = conn
        return conn

    def close(self) -> None:
        """Close the SQLite connection if open.  Safe to call multiple times."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __del__(self) -> None:
        """Defensive close on GC — avoids connection leak if close() was not called."""
        try:
            self.close()
        except Exception:  # noqa: BLE001
            pass

    def _load_raw(self, session_id: str) -> GoalState | None:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT goal_json FROM goal_states WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        return GoalState.model_validate_json(row["goal_json"])

    def _save_raw(self, gs: GoalState) -> None:
        conn = self._get_conn()
        conn.execute(
            """
            INSERT INTO goal_states (session_id, goal_json)
            VALUES (?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                goal_json = excluded.goal_json,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            """,
            (gs.session_id, gs.model_dump_json(by_alias=True)),
        )
        conn.commit()

    def set_goal(
        self,
        session_id: str,
        goal: str,
        *,
        max_turns: int = DEFAULT_GOAL_LOOP_MAX_TURNS,
        opt_out: GoalLoopOptOutState | None = None,
    ) -> GoalState:
        _check_opt_out(opt_out)
        gs = GoalState(
            goal=goal,
            sessionId=session_id,
            turnsUsed=0,
            maxTurns=max_turns,
            status="active",
        )
        self._save_raw(gs)
        return gs

    def get_goal(self, session_id: str) -> GoalState | None:
        return self._load_raw(session_id)

    def advance(self, session_id: str) -> GoalState:
        current = self._load_raw(session_id)
        if current is None:
            raise KeyError(f"no goal set for session: {session_id!r}")
        updated = _advance_state(current)
        self._save_raw(updated)
        return updated

    def clear(self, session_id: str) -> None:
        conn = self._get_conn()
        conn.execute("DELETE FROM goal_states WHERE session_id = ?", (session_id,))
        conn.commit()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "DEFAULT_GOAL_LOOP_MAX_TURNS",
    "GoalState",
    "GoalStateStatus",
    "GoalStateStore",
    "InMemoryGoalStateStore",
    "SqliteGoalStateStore",
]
