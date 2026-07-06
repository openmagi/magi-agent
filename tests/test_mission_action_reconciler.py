"""PR-M8 tests: hosted MissionActionReconciler (inbound UI actions -> queue).

Covers the design-mandated cases (section 7.4 / 5.2 / 12): each of the four
action types maps to the correct store transition, mission_id -> task_id
resolution (and skip when unmapped), cursor advances and persists across a
simulated restart (no reprocessing), dedupe / idempotent repeat is a safe no-op,
an illegal transition (retry on a running task) is logged and the cursor still
advances (no wedge), and inert-without-config. All use a FAKE injected transport
(the reconciler reuses M7's ``MissionTransport`` seam) — no network.
"""
from __future__ import annotations

import pytest

from magi_agent.missions.action_reconciler import (
    MissionActionReconciler,
    build_reconciler_from_env,
    reconciler_active,
)
from magi_agent.missions.work_queue.models import WorkTask
from magi_agent.missions.work_queue.store import SqliteWorkQueueStore


# ---------------------------------------------------------------------------
# Fakes / fixtures
# ---------------------------------------------------------------------------


class FakeActionTransport:
    """Returns a canned action-event list; records the ``params`` each poll uses.

    Mirrors the ``MissionTransport`` seam subset the reconciler consumes
    (``list_action_events``); every other endpoint is present but unused so the
    fake also satisfies the full Protocol shape.
    """

    def __init__(self, batches: list[list[dict]] | None = None) -> None:
        # If a list of batches is given, each poll pops the next batch (last
        # batch repeats). A single flat list repeats every poll (simulates the
        # inclusive ``created_at >= since`` boundary re-returning the last row).
        self._batches = batches if batches is not None else [[]]
        self._i = 0
        self.params_seen: list[dict | None] = []

    def set_events(self, events: list[dict]) -> None:
        self._batches = [events]
        self._i = 0

    def list_action_events(self, params=None) -> list[dict]:
        self.params_seen.append(dict(params) if params else None)
        batch = self._batches[min(self._i, len(self._batches) - 1)]
        self._i += 1
        return list(batch)

    # unused endpoints (Protocol completeness)
    def create_mission(self, body: dict) -> dict:  # pragma: no cover
        return {"id": "m"}

    def create_run(self, mission_id: str, body: dict) -> dict:  # pragma: no cover
        return {"id": "r"}

    def update_run(self, mission_id: str, run_id: str, body: dict) -> dict:  # pragma: no cover
        return {"id": run_id}

    def append_event(self, mission_id: str, body: dict) -> dict:  # pragma: no cover
        return {"id": "e"}

    def create_artifact(self, mission_id: str, body: dict) -> dict:  # pragma: no cover
        return {"id": "a"}

    def restart_recovery(self, body: dict) -> dict:  # pragma: no cover
        return {}


@pytest.fixture()
def store(tmp_path):
    s = SqliteWorkQueueStore(tmp_path / "wq.db")
    yield s
    s.close()


def _task(task_id: str, *, status: str, **kw) -> WorkTask:
    return WorkTask(
        id=task_id,
        title=kw.pop("title", "Do a thing"),
        status=status,
        created_at=kw.pop("created_at", 1_700_000_000),
        session_id=kw.pop("session_id", "sess-1"),
        goal_mode=kw.pop("goal_mode", False),
        consecutive_failures=kw.pop("consecutive_failures", 0),
        **kw,
    )


def _seed(store: SqliteWorkQueueStore, task_id: str, mission_id: str, *, status: str, **kw):
    task = _task(task_id, status=status, **kw)
    store.create(task)
    store.upsert_mission_projection(
        task_id, mission_id=mission_id, last_projected_status=None
    )
    return task


def _action(
    eid: str,
    mission_id: str,
    event_type: str,
    created_at: str,
    *,
    message: str | None = None,
    payload: dict | None = None,
    actor_type: str = "user",
) -> dict:
    ev: dict = {
        "id": eid,
        "mission_id": mission_id,
        "event_type": event_type,
        "created_at": created_at,
        "actor_type": actor_type,
    }
    if message is not None:
        ev["message"] = message
    if payload is not None:
        ev["payload"] = payload
    return ev


def _kinds(store: SqliteWorkQueueStore, task_id: str) -> list[str]:
    return [e["kind"] for e in store.list_task_events(task_id)]


# ---------------------------------------------------------------------------
# 1. Action-event -> correct store transition (all 4 action types, per 5.2)
# ---------------------------------------------------------------------------


def test_cancel_requested_archives_task(store):
    _seed(store, "t1", "m1", status="running")
    tr = FakeActionTransport([[_action("a1", "m1", "cancel_requested", "2026-07-05T00:00:01Z")]])
    rec = MissionActionReconciler(tr, store)

    applied = rec.poll_once()

    assert applied == 1
    assert store.get("t1").status == "archived"
    assert "cancel_requested" in _kinds(store, "t1")


def test_retry_requested_from_failed_resets_to_ready(store):
    _seed(store, "t1", "m1", status="failed", consecutive_failures=3)
    tr = FakeActionTransport([[_action("a1", "m1", "retry_requested", "2026-07-05T00:00:01Z")]])
    rec = MissionActionReconciler(tr, store)

    rec.poll_once()

    task = store.get("t1")
    assert task.status == "ready"
    assert task.consecutive_failures == 0
    assert "retry_requested" in _kinds(store, "t1")


def test_unblocked_from_blocked_resets_to_ready(store):
    _seed(store, "t1", "m1", status="blocked")
    tr = FakeActionTransport([[_action("a1", "m1", "unblocked", "2026-07-05T00:00:01Z")]])
    rec = MissionActionReconciler(tr, store)

    rec.poll_once()

    assert store.get("t1").status == "ready"
    assert "unblocked" in _kinds(store, "t1")


def test_comment_appends_ledger_event_without_status_change(store):
    _seed(store, "t1", "m1", status="running")
    tr = FakeActionTransport(
        [[_action("a1", "m1", "comment", "2026-07-05T00:00:01Z", message="looks good",
                  payload={"author": "kevin"})]]
    )
    rec = MissionActionReconciler(tr, store)

    rec.poll_once()

    assert store.get("t1").status == "running"  # ledger-only (D5)
    comments = [e for e in store.list_task_events("t1") if e["kind"] == "comment"]
    assert len(comments) == 1
    assert comments[0]["payload"]["message"] == "looks good"
    assert comments[0]["payload"]["author"] == "kevin"


# ---------------------------------------------------------------------------
# 2. mission_id -> task_id resolution, and skip when unmapped
# ---------------------------------------------------------------------------


def test_unmapped_mission_is_skipped_but_cursor_advances(store):
    # No task/mapping for m-unknown: this is an action for a mission this pod did
    # not produce. It must be skipped (no crash) and the cursor must still move
    # so it is not reprocessed forever.
    tr = FakeActionTransport([[_action("a1", "m-unknown", "cancel_requested", "2026-07-05T00:00:01Z")]])
    rec = MissionActionReconciler(tr, store)

    applied = rec.poll_once()

    assert applied == 1  # consumed
    cursor = store.get_mission_action_cursor()
    assert cursor is not None
    assert cursor["last_created_at"] == "2026-07-05T00:00:01Z"
    assert "a1" in cursor["processed_ids"]


# ---------------------------------------------------------------------------
# 3. Cursor advances + persists across restart (no reprocessing)
# ---------------------------------------------------------------------------


def test_cursor_persists_across_restart_no_reprocess(store):
    _seed(store, "t1", "m1", status="running")
    events = [_action("a1", "m1", "comment", "2026-07-05T00:00:01Z", message="c1", payload={"author": "k"})]
    tr = FakeActionTransport([events])
    rec = MissionActionReconciler(tr, store)
    rec.poll_once()

    # Simulated restart: fresh reconciler, SAME store, transport re-returns the
    # same event (inclusive gte boundary). It must not be reprocessed.
    tr2 = FakeActionTransport([events])
    rec2 = MissionActionReconciler(tr2, store)
    applied2 = rec2.poll_once()

    assert applied2 == 0
    comments = [e for e in store.list_task_events("t1") if e["kind"] == "comment"]
    assert len(comments) == 1  # not double-appended
    # Second poll passes the persisted cursor as ``since``.
    assert tr2.params_seen[0] is not None
    assert tr2.params_seen[0].get("since") == "2026-07-05T00:00:01Z"


def test_first_poll_sends_no_since(store):
    tr = FakeActionTransport([[]])
    rec = MissionActionReconciler(tr, store)
    rec.poll_once()
    assert tr.params_seen[0] is None or "since" not in tr.params_seen[0]


# ---------------------------------------------------------------------------
# 4. Dedupe / idempotent repeat is a safe no-op
# ---------------------------------------------------------------------------


def test_duplicate_event_id_in_batch_applied_once(store):
    _seed(store, "t1", "m1", status="running")
    dup = _action("a1", "m1", "comment", "2026-07-05T00:00:01Z", message="hi", payload={"author": "k"})
    tr = FakeActionTransport([[dup, dup]])
    rec = MissionActionReconciler(tr, store)

    rec.poll_once()

    comments = [e for e in store.list_task_events("t1") if e["kind"] == "comment"]
    assert len(comments) == 1


def test_idempotent_cancel_repeat_is_safe_noop(store):
    _seed(store, "t1", "m1", status="running")
    tr = FakeActionTransport([[_action("a1", "m1", "cancel_requested", "2026-07-05T00:00:01Z")]])
    rec = MissionActionReconciler(tr, store)
    rec.poll_once()
    assert store.get("t1").status == "archived"

    # A distinct-id cancel event on the already-archived task: CAS no-op, no
    # wedge, cursor advances.
    tr.set_events([_action("a2", "m1", "cancel_requested", "2026-07-05T00:00:02Z")])
    applied = rec.poll_once()
    assert applied == 1
    assert store.get("t1").status == "archived"


# ---------------------------------------------------------------------------
# 5. Illegal transition (retry on running) is logged and cursor still advances
# ---------------------------------------------------------------------------


def test_illegal_retry_on_running_advances_cursor_no_wedge(store):
    _seed(store, "t1", "m1", status="running")
    tr = FakeActionTransport([[_action("a1", "m1", "retry_requested", "2026-07-05T00:00:01Z")]])
    rec = MissionActionReconciler(tr, store)

    applied = rec.poll_once()

    assert applied == 1  # consumed, not wedged
    assert store.get("t1").status == "running"  # unchanged (illegal)
    assert "retry_requested" not in _kinds(store, "t1")  # no event appended
    cursor = store.get_mission_action_cursor()
    assert "a1" in cursor["processed_ids"]


# ---------------------------------------------------------------------------
# 6. Ordering: events are applied in (created_at, id) order
# ---------------------------------------------------------------------------


def test_events_applied_in_created_at_order(store):
    _seed(store, "t1", "m1", status="blocked")
    # Deliberately out-of-order input; unblocked (t=1) then cancel (t=2).
    tr = FakeActionTransport(
        [[
            _action("a2", "m1", "cancel_requested", "2026-07-05T00:00:02Z"),
            _action("a1", "m1", "unblocked", "2026-07-05T00:00:01Z"),
        ]]
    )
    rec = MissionActionReconciler(tr, store)
    rec.poll_once()

    # unblock (ready) applied first, then cancel (archived) -> final archived.
    assert store.get("t1").status == "archived"
    kinds = _kinds(store, "t1")
    assert kinds.index("unblocked") < kinds.index("cancel_requested")


# ---------------------------------------------------------------------------
# 7. Inert without config (OSS local): reconciler is naturally off
# ---------------------------------------------------------------------------


def test_reconciler_inert_without_config():
    assert reconciler_active({}) is False
    assert reconciler_active({"CORE_AGENT_CHAT_PROXY_URL": "http://x"}) is False  # token missing
    assert build_reconciler_from_env({}) is None


def test_reconciler_active_requires_kill_switch():
    env = {
        "CORE_AGENT_CHAT_PROXY_URL": "http://chat-proxy",
        "GATEWAY_TOKEN": "tok",
        # CORE_AGENT_PYTHON_MISSION_RUNTIME not set -> inert (kill-switch off).
    }
    assert reconciler_active(env) is False


# ---------------------------------------------------------------------------
# 8. Watcher registration (daemon lifecycle, gated on config presence)
# ---------------------------------------------------------------------------


def test_reconciler_watcher_registered_and_inert_by_default(monkeypatch):
    from magi_agent.gateway import watchers as w

    monkeypatch.delenv("CORE_AGENT_CHAT_PROXY_URL", raising=False)
    monkeypatch.delenv("GATEWAY_TOKEN", raising=False)

    assert w.is_mission_action_reconciler_enabled() is False
    names = {wd.name for wd in w.build_default_watchers()}
    assert "mission_action_reconciler" in names
