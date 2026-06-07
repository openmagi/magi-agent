from __future__ import annotations

from magi_agent.observability.models import ActivityEvent


def test_minimal_event_defaults():
    ev = ActivityEvent(kind="tool_start")
    assert ev.kind == "tool_start"
    assert ev.ts > 0
    assert ev.payload == {}
    assert ev.session_id is None


def test_payload_must_be_json_safe_dict():
    ev = ActivityEvent(kind="tool_end", tool_name="read", status="ok",
                       payload={"path": "a.py", "n": 3})
    dumped = ev.model_dump()
    assert dumped["tool_name"] == "read"
    assert dumped["payload"]["n"] == 3
