"""Smoke test for the capture_boundary helper (Task 2).

Drives the gate5b4c3 serving boundary through the fake-ADK harness and asserts
that the three observable output streams are all captured.
"""

import asyncio

from tests.support.gate5b4c3_fakes import _FakeRunner, final_event
from tests.support.gate5b4c3_capture import capture_boundary
from magi_agent.shadow.gate5b4c3_live_runner_boundary import (  # noqa: F401
    run_gate5b4c3_live_runner_boundary_async,
)
from tests.test_gate5b4c3_live_runner_boundary import _enabled_config, _request


def test_capture_returns_three_streams(monkeypatch):
    monkeypatch.setenv("MAGI_SESSION_TRANSCRIPT_ENABLED", "1")
    snap = asyncio.run(
        capture_boundary(
            _request(),
            _FakeRunner([final_event("done")]),
            config=_enabled_config(),
        )
    )
    assert set(snap) == {"public_events", "transcript_records", "result"}
    assert snap["result"]["status"] in {"completed", "ok", "succeeded"}
    assert any(r.get("type") == "message" for r in snap["transcript_records"])
