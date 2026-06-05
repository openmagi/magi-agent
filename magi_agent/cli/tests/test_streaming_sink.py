"""Tests for magi_agent.transport.streaming_sink.

TDD Step 1 (RED): these tests are written BEFORE the implementation module
exists and must fail until it is created.

Style: sync wrappers driving async via ``asyncio.run(...)`` — matches the
project convention in test_permissions.py / test_engine.py.
"""
from __future__ import annotations

import asyncio

from magi_agent.cli.protocol import ControlResponse
from magi_agent.cli.wiring import build_headless_runtime
from magi_agent.runtime.control import ControlRequest
from magi_agent.runtime.events import RuntimeEvent
from magi_agent.transport.streaming_sink import (
    QueueFrameWriter,
    build_streaming_prompt_sink,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_req(
    *,
    request_id: str = "req-001",
    tool_name: str = "Bash",
    arguments: dict | None = None,
    reason: str = "Need to run a command",
    turn_id: str = "turn-1",
) -> ControlRequest:
    return ControlRequest(
        request_id=request_id,
        tool_name=tool_name,
        arguments=arguments or {"cmd": "ls -la"},
        reason=reason,
        turn_id=turn_id,
    )


# ---------------------------------------------------------------------------
# Test 1: QueueFrameWriter enqueues a control_request RuntimeEvent
# ---------------------------------------------------------------------------

def test_queue_writer_enqueues_control_request_event():
    """HeadlessSink via QueueFrameWriter must emit a control RuntimeEvent."""

    async def scenario():
        queue: asyncio.Queue[RuntimeEvent] = asyncio.Queue()
        sink = build_streaming_prompt_sink(queue, permission_mode="default", turn_id="t1")
        req = _make_req(request_id="req-abc", tool_name="FileWrite", reason="Write file")

        # Start sink.ask — it will emit the frame then block waiting for a response.
        task = asyncio.create_task(sink.ask(req))

        # The frame must be in the queue within 1 second.
        ev = await asyncio.wait_for(queue.get(), timeout=1.0)

        assert ev.type == "control"
        assert ev.payload["type"] == "control_request"
        assert ev.payload["request_id"] == "req-abc"
        assert ev.payload["tool_name"] == "FileWrite"
        assert ev.turn_id == "t1"

        # Deliver a positive response so the ask task completes cleanly.
        sink.deliver(
            ControlResponse(
                request_id="req-abc",
                response={"decision": "allow"},
            )
        )
        decision = await asyncio.wait_for(task, timeout=1.0)
        assert decision.kind == "allow"

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# Test 2: close() fails-closed → deny
# ---------------------------------------------------------------------------

def test_close_fails_closed_to_deny():
    """After close(), a pending ask must resolve to kind='deny'."""

    async def scenario():
        queue: asyncio.Queue[RuntimeEvent] = asyncio.Queue()
        sink = build_streaming_prompt_sink(queue, permission_mode="default", turn_id="t2")
        req = _make_req(request_id="req-close-001")

        task = asyncio.create_task(sink.ask(req))

        # Wait for the control_request event to land in the queue.
        _ev = await asyncio.wait_for(queue.get(), timeout=1.0)

        # Simulate EOF / channel close — all pending asks must deny.
        sink.close()

        decision = await asyncio.wait_for(task, timeout=1.0)
        assert decision.kind == "deny"

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# Test 3: build_headless_runtime attaches the prompt_sink to the gate
# ---------------------------------------------------------------------------

def test_build_headless_runtime_attaches_sink():
    """When prompt_sink is passed, it must appear in rt.gate.sinks."""
    q: asyncio.Queue[RuntimeEvent] = asyncio.Queue()
    sink = build_streaming_prompt_sink(q)
    rt = build_headless_runtime(prompt_sink=sink)
    assert sink in rt.gate.sinks


# ---------------------------------------------------------------------------
# Test 4: bypassPermissions mode never enqueues — allow without a frame
# ---------------------------------------------------------------------------

def test_bypass_permissions_no_frame():
    """bypassPermissions mode returns allow without writing to the queue."""

    async def scenario():
        queue: asyncio.Queue[RuntimeEvent] = asyncio.Queue()
        sink = build_streaming_prompt_sink(
            queue, permission_mode="bypassPermissions", turn_id="t3"
        )
        req = _make_req(request_id="req-bypass")

        decision = await asyncio.wait_for(sink.ask(req), timeout=1.0)
        assert decision.kind == "allow"
        assert queue.empty(), "bypassPermissions should not enqueue any frame"

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# Test 5: bypassPermissions mode ignores prompt_sink for sink selection
# ---------------------------------------------------------------------------

def test_bypass_mode_ignores_prompt_sink_for_sink_selection():
    """build_headless_runtime with bypassPermissions must NOT use prompt_sink as gate sink.

    The bypass NullFrameWriter sink must remain in gate.sinks; the provided
    prompt_sink must NOT be the gate's selected sink when bypassPermissions is
    active.
    """
    q: asyncio.Queue[RuntimeEvent] = asyncio.Queue()
    prompt_sink = build_streaming_prompt_sink(q)
    rt = build_headless_runtime(
        permission_mode="bypassPermissions",
        prompt_sink=prompt_sink,
    )
    # The prompt_sink must NOT appear in gate.sinks when bypass is active.
    assert prompt_sink not in rt.gate.sinks, (
        "prompt_sink must not override the bypass no-frame sink in bypassPermissions mode"
    )
