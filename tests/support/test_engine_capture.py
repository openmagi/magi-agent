"""Smoke test for engine_capture helper (Task 0.2)."""
import asyncio
import json

from tests.support.engine_capture import capture_engine_turn
from tests.support.engine_fakes import MockRunner, text_event


def test_capture_returns_jsonable_events_and_terminal():
    runner = MockRunner([text_event("hello"), text_event(" world")])
    snap = asyncio.run(
        capture_engine_turn(
            {"prompt": "go", "session_id": "s1", "turn_id": "t1"}, runner
        )
    )
    assert snap["terminal"]["terminal"] == "completed"
    assert isinstance(snap["events"], list) and snap["events"]
    # normalized volatile fields must never leak raw timings
    json.dumps(snap)  # must not raise
