"""B1 — GoalState persistence tests.

RED → GREEN → REFACTOR following TDD protocol.

Tests cover:
- GoalState model: frozen, defaults, camelCase aliases
- InMemoryGoalStateStore: set/get round-trip, advance, exhaustion, clear, isolation
- SqliteGoalStateStore: persistence survives a fresh store instance
- Policy opt-out respected by set_goal
- Import boundary: no ADK/network top-level imports
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from magi_agent.harness.goal_state import (
    DEFAULT_GOAL_LOOP_MAX_TURNS,
    GoalState,
    GoalStateStatus,
    InMemoryGoalStateStore,
    SqliteGoalStateStore,
)
from magi_agent.harness.goal_loop import GoalLoopOptOutState


# ---------------------------------------------------------------------------
# GoalState model tests
# ---------------------------------------------------------------------------


class TestGoalStateModel:
    def test_defaults(self) -> None:
        gs = GoalState(goal="write tests", session_id="sess-1")
        assert gs.goal == "write tests"
        assert gs.session_id == "sess-1"
        assert gs.turns_used == 0
        assert gs.max_turns == DEFAULT_GOAL_LOOP_MAX_TURNS
        assert gs.status == "active"

    def test_default_max_turns_is_module_constant(self) -> None:
        assert DEFAULT_GOAL_LOOP_MAX_TURNS == 20

    def test_camel_alias_round_trip(self) -> None:
        payload = {
            "goal": "hello",
            "sessionId": "s1",
            "turnsUsed": 3,
            "maxTurns": 10,
            "status": "active",
        }
        gs = GoalState.model_validate(payload)
        assert gs.turns_used == 3
        assert gs.max_turns == 10
        dumped = gs.model_dump(by_alias=True)
        assert dumped["turnsUsed"] == 3
        assert dumped["maxTurns"] == 10
        assert dumped["sessionId"] == "s1"

    def test_frozen(self) -> None:
        gs = GoalState(goal="g", session_id="s")
        with pytest.raises((ValidationError, TypeError)):
            gs.turns_used = 5  # type: ignore[misc]

    def test_model_copy_returns_new_instance(self) -> None:
        gs = GoalState(goal="g", session_id="s", turns_used=1)
        gs2 = gs.model_copy(update={"turns_used": 2})
        assert gs.turns_used == 1
        assert gs2.turns_used == 2
        assert gs is not gs2

    def test_status_literals(self) -> None:
        for status in ("active", "satisfied", "exhausted", "preempted", "cleared"):
            gs = GoalState(goal="g", session_id="s", status=status)  # type: ignore[arg-type]
            assert gs.status == status

    def test_invalid_status_rejected(self) -> None:
        with pytest.raises(ValidationError):
            GoalState(goal="g", session_id="s", status="unknown")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# InMemoryGoalStateStore tests
# ---------------------------------------------------------------------------


class TestInMemoryGoalStateStore:
    def test_set_get_round_trip(self) -> None:
        store = InMemoryGoalStateStore()
        gs = store.set_goal("sess-1", "finish the sprint")
        got = store.get_goal("sess-1")
        assert got is not None
        assert got.goal == "finish the sprint"
        assert got.session_id == "sess-1"
        assert got.status == "active"
        assert got.turns_used == 0
        assert got is gs

    def test_get_missing_returns_none(self) -> None:
        store = InMemoryGoalStateStore()
        assert store.get_goal("nonexistent") is None

    def test_set_goal_with_custom_max_turns(self) -> None:
        store = InMemoryGoalStateStore()
        gs = store.set_goal("s1", "goal", max_turns=5)
        assert gs.max_turns == 5

    def test_advance_increments_turns_used(self) -> None:
        store = InMemoryGoalStateStore()
        store.set_goal("sess-1", "goal")
        gs1 = store.advance("sess-1")
        assert gs1.turns_used == 1
        assert gs1.status == "active"
        gs2 = store.advance("sess-1")
        assert gs2.turns_used == 2
        assert gs2.status == "active"

    def test_advance_to_max_turns_transitions_to_exhausted(self) -> None:
        store = InMemoryGoalStateStore()
        store.set_goal("sess-1", "goal", max_turns=3)
        store.advance("sess-1")
        store.advance("sess-1")
        gs = store.advance("sess-1")
        assert gs.turns_used == 3
        assert gs.status == "exhausted"

    def test_advance_beyond_max_turns_stays_exhausted(self) -> None:
        """Once exhausted, advance is a no-op — turns_used must NOT increment."""
        store = InMemoryGoalStateStore()
        store.set_goal("sess-1", "goal", max_turns=2)
        store.advance("sess-1")
        gs_exhausted = store.advance("sess-1")
        assert gs_exhausted.status == "exhausted"
        assert gs_exhausted.turns_used == 2
        # Extra advance on an already-exhausted goal must return unchanged state.
        gs_again = store.advance("sess-1")
        assert gs_again.status == "exhausted"
        assert gs_again.turns_used == 2  # NOT incremented past max

    def test_advance_on_satisfied_goal_is_noop(self) -> None:
        """advance() on a satisfied goal must return the goal unchanged."""
        store = InMemoryGoalStateStore()
        store.set_goal("sess-1", "goal", max_turns=10)
        # Manually inject a satisfied state (B2 would do this via model_copy).
        from magi_agent.harness.goal_state import GoalState
        satisfied = GoalState(
            goal="goal", session_id="sess-1", turns_used=3, max_turns=10, status="satisfied"
        )
        store._states["sess-1"] = satisfied
        gs = store.advance("sess-1")
        assert gs.status == "satisfied"
        assert gs.turns_used == 3  # unchanged

    def test_advance_on_preempted_goal_is_noop(self) -> None:
        """advance() on a preempted goal must return the goal unchanged."""
        store = InMemoryGoalStateStore()
        store.set_goal("sess-1", "goal", max_turns=10)
        from magi_agent.harness.goal_state import GoalState
        preempted = GoalState(
            goal="goal", session_id="sess-1", turns_used=5, max_turns=10, status="preempted"
        )
        store._states["sess-1"] = preempted
        gs = store.advance("sess-1")
        assert gs.status == "preempted"
        assert gs.turns_used == 5  # unchanged

    def test_advance_missing_session_raises(self) -> None:
        store = InMemoryGoalStateStore()
        with pytest.raises(KeyError):
            store.advance("no-such-session")

    def test_clear_removes_goal(self) -> None:
        store = InMemoryGoalStateStore()
        store.set_goal("s1", "goal")
        store.clear("s1")
        assert store.get_goal("s1") is None

    def test_clear_missing_session_is_noop(self) -> None:
        store = InMemoryGoalStateStore()
        store.clear("nope")  # should not raise

    def test_sessions_are_isolated(self) -> None:
        store = InMemoryGoalStateStore()
        store.set_goal("s1", "goal-a")
        store.set_goal("s2", "goal-b")
        store.advance("s1")
        assert store.get_goal("s1").turns_used == 1  # type: ignore[union-attr]
        assert store.get_goal("s2").turns_used == 0  # type: ignore[union-attr]

    def test_set_goal_replaces_existing_goal(self) -> None:
        store = InMemoryGoalStateStore()
        store.set_goal("s1", "old goal")
        store.advance("s1")
        store.set_goal("s1", "new goal")
        gs = store.get_goal("s1")
        assert gs is not None
        assert gs.goal == "new goal"
        assert gs.turns_used == 0  # reset

    def test_opt_out_state_blocks_set_goal(self) -> None:
        store = InMemoryGoalStateStore()
        opt_out = GoalLoopOptOutState(
            opted_out=True,
            disabled_reason="test opt-out",
        )
        with pytest.raises(ValueError, match="opted out"):
            store.set_goal("s1", "goal", opt_out=opt_out)


# ---------------------------------------------------------------------------
# SqliteGoalStateStore persistence tests
# ---------------------------------------------------------------------------


def _insert_session(store: SqliteGoalStateStore, session_id: str) -> None:
    """Insert a minimal sessions row so goal_states FK constraint is satisfied."""
    conn = store._get_conn()
    conn.execute(
        """
        INSERT OR IGNORE INTO sessions (id, app_name, user_id)
        VALUES (?, 'test-app', 'test-user')
        """,
        (session_id,),
    )
    conn.commit()


class TestSqliteGoalStateStore:
    def test_set_get_round_trip(self, tmp_path: Path) -> None:
        db = tmp_path / "goals.db"
        store = SqliteGoalStateStore(db)
        _insert_session(store, "sess-1")
        gs = store.set_goal("sess-1", "persistent goal")
        got = store.get_goal("sess-1")
        assert got is not None
        assert got.goal == "persistent goal"
        assert got.session_id == "sess-1"
        store.close()

    def test_persistence_survives_fresh_store_instance(self, tmp_path: Path) -> None:
        db = tmp_path / "goals.db"
        store1 = SqliteGoalStateStore(db)
        _insert_session(store1, "sess-1")
        store1.set_goal("sess-1", "survive restart")
        store1.advance("sess-1")
        store1.close()

        store2 = SqliteGoalStateStore(db)
        got = store2.get_goal("sess-1")
        assert got is not None
        assert got.goal == "survive restart"
        assert got.turns_used == 1
        store2.close()

    def test_advance_persisted(self, tmp_path: Path) -> None:
        db = tmp_path / "goals.db"
        store = SqliteGoalStateStore(db)
        _insert_session(store, "s1")
        store.set_goal("s1", "g", max_turns=5)
        store.advance("s1")
        store.advance("s1")
        store.close()

        store2 = SqliteGoalStateStore(db)
        gs = store2.get_goal("s1")
        assert gs is not None
        assert gs.turns_used == 2
        store2.close()

    def test_advance_exhaustion_persisted(self, tmp_path: Path) -> None:
        db = tmp_path / "goals.db"
        store = SqliteGoalStateStore(db)
        _insert_session(store, "s1")
        store.set_goal("s1", "g", max_turns=2)
        store.advance("s1")
        store.advance("s1")
        store.close()

        store2 = SqliteGoalStateStore(db)
        gs = store2.get_goal("s1")
        assert gs is not None
        assert gs.status == "exhausted"
        store2.close()

    def test_clear_persisted(self, tmp_path: Path) -> None:
        db = tmp_path / "goals.db"
        store = SqliteGoalStateStore(db)
        _insert_session(store, "s1")
        store.set_goal("s1", "g")
        store.clear("s1")
        store.close()

        store2 = SqliteGoalStateStore(db)
        assert store2.get_goal("s1") is None
        store2.close()

    def test_get_missing_returns_none(self, tmp_path: Path) -> None:
        db = tmp_path / "goals.db"
        store = SqliteGoalStateStore(db)
        assert store.get_goal("nope") is None
        store.close()

    def test_advance_missing_raises(self, tmp_path: Path) -> None:
        db = tmp_path / "goals.db"
        store = SqliteGoalStateStore(db)
        with pytest.raises(KeyError):
            store.advance("no-such-session")
        store.close()

    def test_opt_out_blocks_set_goal(self, tmp_path: Path) -> None:
        db = tmp_path / "goals.db"
        store = SqliteGoalStateStore(db)
        opt_out = GoalLoopOptOutState(opted_out=True, disabled_reason="blocked")
        with pytest.raises(ValueError, match="opted out"):
            store.set_goal("s1", "goal", opt_out=opt_out)
        store.close()


# ---------------------------------------------------------------------------
# upsert tests (B1 Protocol promotion)
# ---------------------------------------------------------------------------


class TestInMemoryUpsert:
    def test_upsert_round_trips(self) -> None:
        store = InMemoryGoalStateStore()
        gs = GoalState(goal="write tests", session_id="s1", status="active")
        result = store.upsert(gs)
        assert result is gs
        assert store.get_goal("s1") is gs

    def test_upsert_overwrites_existing_state(self) -> None:
        store = InMemoryGoalStateStore()
        store.set_goal("s1", "old goal")
        store.advance("s1")
        updated = GoalState(goal="new goal", session_id="s1", turns_used=5, max_turns=20, status="satisfied")
        store.upsert(updated)
        got = store.get_goal("s1")
        assert got is not None
        assert got.goal == "new goal"
        assert got.turns_used == 5
        assert got.status == "satisfied"


class TestSqliteUpsert:
    def test_upsert_round_trips(self, tmp_path: Path) -> None:
        db = tmp_path / "goals.db"
        store = SqliteGoalStateStore(db)
        _insert_session(store, "s1")
        gs = GoalState(goal="upsert goal", session_id="s1", status="active")
        result = store.upsert(gs)
        assert result.goal == gs.goal
        assert result.status == gs.status
        got = store.get_goal("s1")
        assert got is not None
        assert got.goal == "upsert goal"
        store.close()

    def test_upsert_overwrites_existing_state(self, tmp_path: Path) -> None:
        db = tmp_path / "goals.db"
        store = SqliteGoalStateStore(db)
        _insert_session(store, "s1")
        store.set_goal("s1", "original goal")
        store.advance("s1")
        updated = GoalState(goal="replaced goal", session_id="s1", turns_used=3, max_turns=20, status="satisfied")
        store.upsert(updated)
        # Read back through a fresh store to confirm durability.
        store.close()
        fresh = SqliteGoalStateStore(db)
        got = fresh.get_goal("s1")
        assert got is not None
        assert got.goal == "replaced goal"
        assert got.turns_used == 3
        assert got.status == "satisfied"
        fresh.close()


# ---------------------------------------------------------------------------
# Import boundary test
# ---------------------------------------------------------------------------


def test_goal_state_import_boundary_does_not_load_adk_or_network_modules() -> None:
    """goal_state module must not pull in ADK, network, or agent-spawn modules."""
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

importlib.import_module("magi_agent.harness.goal_state")
forbidden_prefixes = ("google.adk",)
forbidden_modules = (
    "magi_agent.adk_bridge.runner_adapter",
    "magi_agent.adk_bridge.tool_adapter",
    "magi_agent.transport.chat",
    "magi_agent.transport.tools",
    "magi_agent.tools.dispatcher",
    "magi_agent.hooks.bus",
)
loaded = [
    module
    for module in sys.modules
    if module.startswith(forbidden_prefixes) or module in forbidden_modules
]
if loaded:
    raise AssertionError(f"goal_state import loaded forbidden modules: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
