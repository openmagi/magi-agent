"""Tests for WorkQueueNotifier — tail-from-now delivery + gated watcher.

TDD: tests written FIRST (Red → Green → Refactor).
"""
from __future__ import annotations

from magi_agent.missions.work_queue.notifier import WorkQueueNotifier, LoggingNotifySink


class _FakeStore:
    def __init__(self, events): self._events = events  # list of {"id":int,"kind":str,"task_id":str,...}
    def latest_terminal_event_id(self): return max((e["id"] for e in self._events), default=0)
    def terminal_events_since(self, since_id, *, limit=200):
        return [e for e in self._events if e["id"] > since_id][:limit]


class _RecordingSink:
    def __init__(self): self.delivered = []
    def deliver(self, event): self.delivered.append(event)


def test_first_poll_tails_from_now_no_replay():
    store = _FakeStore([{"id": 1, "kind": "completed", "task_id": "a"},
                        {"id": 2, "kind": "blocked", "task_id": "b"}])
    sink = _RecordingSink()
    n = WorkQueueNotifier(store, sink)
    assert n.poll_once() == 0 and sink.delivered == []     # pre-existing events NOT replayed


def test_subsequent_poll_delivers_new_terminal_events():
    store = _FakeStore([{"id": 1, "kind": "completed", "task_id": "a"}])
    sink = _RecordingSink()
    n = WorkQueueNotifier(store, sink)
    n.poll_once()                                          # cursor -> 1, nothing delivered
    store._events.append({"id": 2, "kind": "failed", "task_id": "c"})
    assert n.poll_once() == 1
    assert [e["task_id"] for e in sink.delivered] == ["c"]
    assert n.poll_once() == 0                              # cursor advanced; no re-delivery


def test_sink_exception_does_not_stop_batch():
    store = _FakeStore([{"id": 1, "kind": "completed", "task_id": "a"}])
    n = WorkQueueNotifier(store, _RecordingSink())
    n.poll_once()
    class _BoomSink:
        def deliver(self, event): raise RuntimeError("down")
    store2 = _FakeStore([])
    n2 = WorkQueueNotifier(store2, _BoomSink())
    n2.poll_once()
    store2._events.extend([{"id": 1, "kind": "completed", "task_id": "x"},
                           {"id": 2, "kind": "failed", "task_id": "y"}])
    # both raise but poll_once must not propagate; cursor still advances past both
    assert n2.poll_once() == 2 and n2.poll_once() == 0


def test_logging_sink_constructs_no_authority():
    LoggingNotifySink().deliver({"id": 1, "kind": "completed", "task_id": "a"})  # no raise, no network
