"""PR-M7 tests: hosted MissionProjector (outbound work_queue -> chat-proxy).

Covers the design-mandated cases (section 7.1 / 12): enqueue-never-blocks even
when the transport raises, retry-then-drop after N, idempotent re-create after a
lost mapping row (same ``wq:<task_id>``), heartbeat throttle window, status
projection through the M1 kernel (not raw TaskStatus), and inert-when-config-
absent. All use a FAKE injected transport — no network.
"""
from __future__ import annotations

import threading
import time

import pytest

from magi_agent.missions.projection import map_task_status
from magi_agent.missions.projector import (
    MissionProjector,
    MissionTransportError,
    build_projector_from_env,
    notify_task_created,
    projector_active,
    reset_active_projector,
)
from magi_agent.missions.work_queue.models import WorkTask
from magi_agent.missions.work_queue.store import SqliteWorkQueueStore


# ---------------------------------------------------------------------------
# Fakes / fixtures
# ---------------------------------------------------------------------------


class FakeTransport:
    """Records every endpoint call; no network. Optionally raises or blocks."""

    def __init__(
        self,
        *,
        mission_id: str = "mission-1",
        run_id: str = "run-1",
        raise_on: set[str] | None = None,
        block: threading.Event | None = None,
    ) -> None:
        self.calls: list[tuple[str, tuple]] = []
        self._mission_id = mission_id
        self._run_id = run_id
        self._raise_on = raise_on or set()
        self._block = block
        self.lock = threading.Lock()

    def _record(self, name: str, *args) -> None:
        with self.lock:
            self.calls.append((name, args))

    def count(self, name: str) -> int:
        with self.lock:
            return sum(1 for n, _ in self.calls if n == name)

    def bodies(self, name: str) -> list[dict]:
        with self.lock:
            return [args[-1] for n, args in self.calls if n == name]

    def create_mission(self, body: dict) -> dict:
        self._record("create_mission", body)
        if self._block is not None:
            self._block.wait(5)
        if "create_mission" in self._raise_on:
            raise MissionTransportError("boom-create")
        return {"id": self._mission_id}

    def create_run(self, mission_id: str, body: dict) -> dict:
        self._record("create_run", mission_id, body)
        return {"id": self._run_id}

    def update_run(self, mission_id: str, run_id: str, body: dict) -> dict:
        self._record("update_run", mission_id, run_id, body)
        return {"id": run_id}

    def append_event(self, mission_id: str, body: dict) -> dict:
        self._record("append_event", mission_id, body)
        return {"id": "event-1"}

    def create_artifact(self, mission_id: str, body: dict) -> dict:
        self._record("create_artifact", mission_id, body)
        return {"id": "artifact-1"}

    def list_action_events(self, params=None) -> list[dict]:
        self._record("list_action_events", params)
        return []

    def restart_recovery(self, body: dict) -> dict:
        self._record("restart_recovery", body)
        return {"abandoned": 0, "missionIds": []}


@pytest.fixture()
def store(tmp_path):
    s = SqliteWorkQueueStore(tmp_path / "wq.db")
    yield s
    s.close()


def _task(task_id: str = "t-1", *, status: str = "running", **kw) -> WorkTask:
    return WorkTask(
        id=task_id,
        title=kw.pop("title", "Do a thing"),
        status=status,
        created_at=kw.pop("created_at", 1_700_000_000),
        session_id=kw.pop("session_id", "sess-1"),
        goal_mode=kw.pop("goal_mode", False),
        **kw,
    )


# ---------------------------------------------------------------------------
# 1. enqueue never blocks — even when the transport blocks / raises
# ---------------------------------------------------------------------------


def test_enqueue_never_blocks_when_transport_stalls(store):
    gate = threading.Event()
    transport = FakeTransport(block=gate)
    store.create(_task())
    projector = MissionProjector(transport, store, queue_maxsize=1000)
    projector.start()
    try:
        start = time.monotonic()
        for _ in range(200):
            projector.on_task_created(_task())
        elapsed = time.monotonic() - start
        # The worker is stuck on the first create_mission (gate not set); every
        # enqueue must still return without waiting on it.
        assert elapsed < 1.0
    finally:
        gate.set()
        projector.stop()


def test_enqueue_does_not_raise_when_transport_raises(store):
    transport = FakeTransport(raise_on={"create_mission"})
    store.create(_task())
    projector = MissionProjector(transport, store, max_retries=2)
    projector.start()
    try:
        projector.on_task_created(_task())  # must not raise into the caller
        assert projector.flush(timeout=5)
    finally:
        projector.stop()


# ---------------------------------------------------------------------------
# 2. retry then drop after N
# ---------------------------------------------------------------------------


def test_retry_then_drop_after_n_attempts(store):
    transport = FakeTransport(raise_on={"create_mission"})
    store.create(_task())
    projector = MissionProjector(transport, store, max_retries=3)
    projector.start()
    try:
        projector.on_task_created(_task())
        assert projector.flush(timeout=5)
    finally:
        projector.stop()
    # Exactly max_retries attempts, then dropped (no mapping persisted).
    assert transport.count("create_mission") == 3
    assert store.get_mission_projection("t-1") is None


# ---------------------------------------------------------------------------
# 3. idempotent re-create after a lost mapping row (same wq:<task_id>)
# ---------------------------------------------------------------------------


def test_idempotent_recreate_after_lost_mapping_row(store):
    transport = FakeTransport(mission_id="mission-XYZ")
    task = _task(status="running")
    store.create(task)
    projector = MissionProjector(transport, store)
    projector.start()
    try:
        projector.on_task_created(task)
        assert projector.flush(timeout=5)
        mapped = store.get_mission_projection("t-1")
        assert mapped is not None and mapped["mission_id"] == "mission-XYZ"

        # Simulate a lost mapping row, then a later checkpoint.
        store.delete_mission_projection("t-1")
        projector.on_task_checkpoint(
            task_id="t-1", checkpoint_kind="completed", summary_text="all done"
        )
        assert projector.flush(timeout=5)
    finally:
        projector.stop()

    # Re-created with the SAME idempotency key; the fake receiver returns the
    # same mission id (dedupe), so the mapping recovers without duplication.
    create_bodies = transport.bodies("create_mission")
    assert len(create_bodies) == 2
    assert all(b["idempotencyKey"] == "wq:t-1" for b in create_bodies)
    remapped = store.get_mission_projection("t-1")
    assert remapped is not None and remapped["mission_id"] == "mission-XYZ"
    assert transport.count("append_event") >= 1


# ---------------------------------------------------------------------------
# 4. heartbeat throttle window
# ---------------------------------------------------------------------------


def test_heartbeat_throttled_to_one_per_window(store):
    now = {"t": 0.0}
    transport = FakeTransport()
    store.create(_task())
    projector = MissionProjector(
        transport, store, heartbeat_window_seconds=600, clock=lambda: now["t"]
    )
    projector.start()
    try:
        now["t"] = 0.0
        projector.on_heartbeat("t-1")      # emits
        now["t"] = 100.0
        projector.on_heartbeat("t-1")      # inside window -> dropped at enqueue
        now["t"] = 700.0
        projector.on_heartbeat("t-1")      # past window -> emits
        assert projector.flush(timeout=5)
    finally:
        projector.stop()

    heartbeat_events = [
        b for b in transport.bodies("append_event") if b.get("eventType") == "heartbeat"
    ]
    assert len(heartbeat_events) == 2


# ---------------------------------------------------------------------------
# 5. status projection goes through the M1 kernel, not raw TaskStatus
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("task_status", "expected_mission_status"),
    [("todo", "queued"), ("ready", "queued"), ("running", "running"), ("archived", "cancelled")],
)
def test_create_body_status_uses_m1_kernel(store, task_status, expected_mission_status):
    transport = FakeTransport()
    task = _task(status=task_status)
    projector = MissionProjector(transport, store)
    projector.start()
    try:
        projector.on_task_created(task)
        assert projector.flush(timeout=5)
    finally:
        projector.stop()

    body = transport.bodies("create_mission")[0]
    assert body["status"] == expected_mission_status
    assert body["status"] == map_task_status(task_status)
    # create INPUT shape is camelCase (chat-proxy validateCreateMission).
    assert body["channelType"] == "app"
    assert body["idempotencyKey"] == "wq:t-1"
    assert body["kind"] == "manual"


# ---------------------------------------------------------------------------
# 6. inert when config env absent
# ---------------------------------------------------------------------------


def test_projector_inert_without_config():
    assert projector_active({}) is False
    assert (
        projector_active(
            {"CORE_AGENT_CHAT_PROXY_URL": "http://x", "GATEWAY_TOKEN": "tok"}
        )
        is False
    )  # kill-switch default OFF
    assert (
        projector_active(
            {
                "CORE_AGENT_CHAT_PROXY_URL": "http://x",
                "GATEWAY_TOKEN": "tok",
                "CORE_AGENT_PYTHON_MISSION_RUNTIME": "1",
            }
        )
        is True
    )
    assert build_projector_from_env({}) is None
    assert (
        build_projector_from_env(
            {"CORE_AGENT_CHAT_PROXY_URL": "http://x", "GATEWAY_TOKEN": "tok"}
        )
        is None
    )


def test_build_projector_active_env(monkeypatch, tmp_path):
    monkeypatch.setenv("MAGI_WORK_QUEUE_DB_PATH", str(tmp_path / "wq.db"))
    projector = build_projector_from_env(
        {
            "CORE_AGENT_CHAT_PROXY_URL": "http://chat-proxy:3002",
            "GATEWAY_TOKEN": "tok",
            "CORE_AGENT_PYTHON_MISSION_RUNTIME": "1",
        },
        start=False,
    )
    assert isinstance(projector, MissionProjector)


# ---------------------------------------------------------------------------
# 7. seam wrapper is fail-open + inert-gated
# ---------------------------------------------------------------------------


def test_notify_task_created_no_op_when_inert(monkeypatch):
    reset_active_projector()
    monkeypatch.setattr(
        "magi_agent.missions.projector._get_active_projector", lambda: None
    )
    # Must not raise even though there is no active projector.
    notify_task_created(_task())


def test_notify_task_created_enqueues_when_active(monkeypatch):
    calls: list[WorkTask] = []

    class _Fake:
        def on_task_created(self, task):
            calls.append(task)

    monkeypatch.setattr(
        "magi_agent.missions.projector._get_active_projector", lambda: _Fake()
    )
    notify_task_created(_task("seam-1"))
    assert len(calls) == 1 and calls[0].id == "seam-1"


def test_notify_seam_swallows_projector_error(monkeypatch):
    class _Boom:
        def on_task_created(self, task):
            raise RuntimeError("projector exploded")

    monkeypatch.setattr(
        "magi_agent.missions.projector._get_active_projector", lambda: _Boom()
    )
    # Fail-open: the seam wrapper must swallow the projector error.
    notify_task_created(_task())


# ---------------------------------------------------------------------------
# 8. mission_projection store helpers (5.4; also consumed by PR-M8)
# ---------------------------------------------------------------------------


def test_mission_projection_roundtrip_and_reverse(store):
    assert store.get_mission_projection("t-1") is None
    store.upsert_mission_projection(
        "t-1", mission_id="m-1", last_projected_status="running"
    )
    row = store.get_mission_projection("t-1")
    assert row is not None
    assert row["mission_id"] == "m-1"
    assert row["last_projected_status"] == "running"
    assert isinstance(row["updated_at"], int)
    # Reverse resolution (mission_id -> task_id) for the PR-M8 reconciler.
    assert store.task_id_for_mission("m-1") == "t-1"
    assert store.task_id_for_mission("nope") is None


def test_mission_projection_upsert_is_idempotent(store):
    store.upsert_mission_projection("t-1", mission_id="m-1", last_projected_status="queued")
    store.upsert_mission_projection("t-1", mission_id="m-1", last_projected_status="completed")
    row = store.get_mission_projection("t-1")
    assert row is not None and row["last_projected_status"] == "completed"
    # Still exactly one row (PRIMARY KEY on task_id).
    assert store.task_id_for_mission("m-1") == "t-1"


def test_delete_mission_projection(store):
    store.upsert_mission_projection("t-1", mission_id="m-1", last_projected_status="queued")
    store.delete_mission_projection("t-1")
    assert store.get_mission_projection("t-1") is None
