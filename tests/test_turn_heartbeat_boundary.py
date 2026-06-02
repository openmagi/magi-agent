from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Callable

from openmagi_core_agent.runtime.heartbeat_contract import RunLease
from openmagi_core_agent.runtime.heartbeat_store import LocalFakeHeartbeatStore
from openmagi_core_agent.runtime.heartbeat_boundary import (
    RuntimeHeartbeatBoundary,
    RuntimeHeartbeatBoundaryConfig,
)
from openmagi_core_agent.runtime.stale_run_detector import (
    StaleRunDetectorConfig,
    evaluate_stale_run,
)
from openmagi_core_agent.runtime.turn_maintenance import (
    HEARTBEAT_INTERVAL_MS,
    HEARTBEAT_SILENCE_MS,
    HeartbeatMonitor,
    wrap_event_sink_with_runtime_heartbeat_boundary,
)


class FakeClock:
    def __init__(self) -> None:
        self._now = 0
        self._queue: list[dict[str, Any]] = []

    def now(self) -> int:
        return self._now

    def schedule(self, callback: Callable[[], None], delay_ms: int) -> Callable[[], None]:
        task = {
            "callback": callback,
            "fire_at": self._now + delay_ms,
            "cancelled": False,
        }
        self._queue.append(task)

        def cancel() -> None:
            task["cancelled"] = True

        return cancel

    def advance(self, ms: int) -> None:
        target = self._now + ms
        while True:
            due = [
                task
                for task in self._queue
                if not task["cancelled"] and task["fire_at"] <= target
            ]
            if not due:
                break
            next_task = min(due, key=lambda task: task["fire_at"])
            self._now = next_task["fire_at"]
            next_task["cancelled"] = True
            next_task["callback"]()
        self._now = target


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _digest(character: str) -> str:
    return "sha256:" + character * 64


def _lease() -> RunLease:
    return RunLease.model_validate(
        {
            "runId": "run:boundary",
            "turnId": "turn:001",
            "sessionKey": "sess:boundary",
            "workerId": "worker:west-1",
            "leaseId": "lease:boundary",
            "leaseAcquiredAt": "2026-05-27T12:00:00Z",
            "leaseExpiresAt": "2026-05-27T13:00:00Z",
            "phase": "running",
            "activeBoundary": "turn-controller",
            "authorityScope": "runtime-contract-default-off",
            "generation": 1,
            "fencingToken": _digest("a"),
        }
    )


def _enabled_boundary() -> tuple[RuntimeHeartbeatBoundary, LocalFakeHeartbeatStore]:
    store = LocalFakeHeartbeatStore()
    boundary = RuntimeHeartbeatBoundary(
        store=store,
        lease=_lease(),
        config=RuntimeHeartbeatBoundaryConfig(enabled=True),
    )
    boundary.start(now=_dt("2026-05-27T12:00:00Z"))
    return boundary, store


def test_public_sse_heartbeat_monitor_behavior_is_unchanged() -> None:
    clock = FakeClock()
    events: list[dict[str, object]] = []
    monitor = HeartbeatMonitor(turn_id="turn_1", event_sink=events.append, clock=clock)

    monitor.start(3)
    clock.advance(HEARTBEAT_SILENCE_MS)
    clock.advance(HEARTBEAT_INTERVAL_MS)

    assert events == [
        {
            "type": "heartbeat",
            "turnId": "turn_1",
            "iter": 3,
            "elapsedMs": HEARTBEAT_SILENCE_MS,
            "lastEventAt": 0,
        },
        {
            "type": "heartbeat",
            "turnId": "turn_1",
            "iter": 3,
            "elapsedMs": HEARTBEAT_SILENCE_MS + HEARTBEAT_INTERVAL_MS,
            "lastEventAt": 0,
        },
    ]


def test_runtime_heartbeat_boundary_is_disabled_by_default_and_writes_nothing() -> None:
    store = LocalFakeHeartbeatStore()
    boundary = RuntimeHeartbeatBoundary(store=store, lease=_lease())

    start_result = boundary.start(now=_dt("2026-05-27T12:00:00Z"))
    consume_result = boundary.consume_event(
        {"type": "tool.call.completed", "eventId": "event:tool-1"},
        emitted_at=_dt("2026-05-27T12:00:10Z"),
    )

    assert boundary.enabled is False
    assert start_result.status == "disabled"
    assert consume_result.status == "disabled"
    assert store.get_run("run:boundary") is None
    assert consume_result.public_projection()["liveAuthority"] is False
    assert consume_result.public_projection()["workspaceMutationEnabled"] is False


def test_runtime_heartbeat_boundary_records_tool_source_child_and_activity_receipts() -> None:
    boundary, store = _enabled_boundary()

    results = [
        boundary.consume_event(
            {"type": "tool.call.completed", "eventId": "event:tool-1"},
            emitted_at=_dt("2026-05-27T12:00:10Z"),
        ),
        boundary.consume_event(
            {"type": "source.inspected", "eventId": "event:source-1"},
            emitted_at=_dt("2026-05-27T12:00:20Z"),
        ),
        boundary.consume_event(
            {"type": "child.completed", "eventId": "event:child-1"},
            emitted_at=_dt("2026-05-27T12:00:30Z"),
        ),
        boundary.consume_event(
            {"type": "runtime.activity", "eventId": "event:activity-1"},
            emitted_at=_dt("2026-05-27T12:00:40Z"),
        ),
    ]

    record = store.get_run("run:boundary")
    assert record is not None
    assert [result.status for result in results] == ["activity_appended"] * 4
    assert [activity.activity_type for activity in record.activities] == [
        "tool_event",
        "source_inspected",
        "child_event",
        "runtime_activity",
    ]
    assert record.public_projection()["schedulerAttached"] is False
    assert record.public_projection()["modelCallEnabled"] is False


def test_public_heartbeat_events_do_not_reset_runtime_inactivity() -> None:
    boundary, store = _enabled_boundary()
    first = boundary.consume_event(
        {"type": "tool.call.completed", "eventId": "event:tool-1"},
        emitted_at=_dt("2026-05-27T12:00:10Z"),
    )
    public_heartbeat = boundary.consume_event(
        {"type": "heartbeat", "turnId": "turn_1"},
        emitted_at=_dt("2026-05-27T12:04:59Z"),
    )

    record = store.get_run("run:boundary")
    assert record is not None
    verdict = evaluate_stale_run(
        record,
        checked_at=_dt("2026-05-27T12:05:11Z"),
        config=StaleRunDetectorConfig(
            heartbeat_silence_after_seconds=60,
            inactivity_timeout_seconds=300,
            worker_lost_after_seconds=600,
        ),
    )

    assert first.status == "activity_appended"
    assert public_heartbeat.status == "ignored_heartbeat"
    assert len(record.activities) == 1
    assert verdict.verdict == "inactive_timeout"


def test_runtime_heartbeat_receipts_are_separate_from_public_heartbeat_events() -> None:
    boundary, store = _enabled_boundary()
    boundary.consume_event(
        {"type": "tool.call.completed", "eventId": "event:tool-1"},
        emitted_at=_dt("2026-05-27T12:00:10Z"),
    )
    ignored = boundary.consume_event(
        {"type": "runtime.heartbeat", "eventId": "event:public-heartbeat"},
        emitted_at=_dt("2026-05-27T12:00:20Z"),
    )
    emitted = boundary.emit_runtime_heartbeat(
        emitted_at=_dt("2026-05-27T12:00:30Z"),
        phase="running",
    )

    record = store.get_run("run:boundary")
    assert record is not None
    assert ignored.status == "ignored_heartbeat"
    assert emitted.status == "heartbeat_appended"
    assert len(record.activities) == 1
    assert len(record.heartbeats) == 1
    assert record.heartbeats[0].heartbeat_id == "heartbeat:000001"
    assert record.heartbeats[0].last_activity_receipt_digest == record.activities[0].digest


def test_turn_maintenance_wrapper_forwards_events_and_optionally_records_activity() -> None:
    boundary, store = _enabled_boundary()
    forwarded: list[dict[str, object]] = []
    wrapped = wrap_event_sink_with_runtime_heartbeat_boundary(forwarded.append, boundary)

    wrapped(
        {
            "type": "source.inspected",
            "eventId": "event:source-1",
            "emittedAt": "2026-05-27T12:00:10Z",
        }
    )
    wrapped({"type": "heartbeat", "turnId": "turn_1"})

    record = store.get_run("run:boundary")
    assert record is not None
    assert forwarded == [
        {
            "type": "source.inspected",
            "eventId": "event:source-1",
            "emittedAt": "2026-05-27T12:00:10Z",
        },
        {"type": "heartbeat", "turnId": "turn_1"},
    ]
    assert len(record.activities) == 1
    assert record.activities[0].activity_type == "source_inspected"
