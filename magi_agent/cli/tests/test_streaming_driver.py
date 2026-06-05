"""Tests for magi_agent.transport.streaming_driver.drive_streaming_chat.

Style note: this package has no ``pytest-asyncio`` configured, so every test is
a SYNC function that drives async code via ``asyncio.run(...)`` — matching the
``test_engine.py`` / ``test_permissions.py`` convention.

The FakeEngine below mirrors the REAL ``MagiEngineDriver.run_turn_stream``
contract (see ``magi_agent/cli/engine.py`` ~279-545): it is an async generator
that yields ``RuntimeEvent`` objects, the FINAL yielded item is an
``EngineResult`` (NOT a RuntimeEvent), and on cancel it synthesizes a balanced
orphan ``tool_end(interrupted)`` + ``turn_end(user_interrupt)`` then yields
``EngineResult(terminal=aborted, error="cancelled")``.
"""

from __future__ import annotations

import asyncio
import json

from magi_agent.cli.contracts import EngineResult, Terminal
from magi_agent.cli.permissions import HeadlessSink
from magi_agent.runtime.control import ControlRequest
from magi_agent.cli.protocol import ControlResponse
from magi_agent.runtime.events import RuntimeEvent
from magi_agent.transport.active_turn import ActiveTurnTable
from magi_agent.transport.streaming_driver import drive_streaming_chat
from magi_agent.transport.streaming_sink import build_streaming_prompt_sink


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _data_lines(text: str) -> list[dict]:
    """Parse every ``data: {...}`` JSON line (skipping the [DONE] sentinel)."""
    out: list[dict] = []
    for line in text.splitlines():
        if line.startswith("data:"):
            body = line[len("data:"):].strip()
            if body == "[DONE]":
                continue
            out.append(json.loads(body))
    return out


def _ev(event_type: str, **payload: object) -> RuntimeEvent:
    return RuntimeEvent(
        type="status",
        payload={"type": event_type, **payload},
        turn_id="t-turn",
    )


# ---------------------------------------------------------------------------
# Test 1 — happy path
# ---------------------------------------------------------------------------
def test_happy_path_streams_events_then_terminal_and_done():
    registry = ActiveTurnTable()
    queue: asyncio.Queue[object] = asyncio.Queue()
    sink = build_streaming_prompt_sink(queue, turn_id="t-turn")
    cancel = asyncio.Event()

    class FakeEngine:
        async def run_turn_stream(self, runtime, turn_input, *, cancel, gate):
            yield _ev("text_delta", delta="hello world")
            yield _ev("tool_start", id="c1", name="Bash")
            yield EngineResult(
                terminal=Terminal.completed,
                usage={"input_tokens": 3},
                session_id="s1",
                turn_id="t-turn",
            )

    async def _run() -> str:
        chunks: list[bytes] = []
        registered_during = False
        async for chunk in drive_streaming_chat(
            FakeEngine(),
            None,
            {"prompt": "hi", "session_id": "s1", "turn_id": "t-turn"},
            cancel=cancel,
            queue=queue,
            sink=sink,
            registry=registry,
            session_id="s1",
            turn_id="t-turn",
        ):
            chunks.append(chunk)
            # The turn must be registered while the stream is being consumed.
            if registry.get("s1") is not None:
                registered_during = True
        assert registered_during, "turn was never registered during the run"
        return b"".join(chunks).decode()

    text = asyncio.run(_run())

    assert "text_delta" in text
    assert "hello world" in text
    assert "tool_start" in text

    payloads = _data_lines(text)
    types = [p["type"] for p in payloads]
    # ordering: text_delta, tool_start, then turn_result LAST
    assert types[-1] == "turn_result"
    assert "text_delta" in types
    assert "tool_start" in types

    turn_result = payloads[-1]
    assert turn_result["terminal"] == "completed"
    assert text.rstrip().endswith("data: [DONE]")

    # Unregistered after the run completed.
    assert registry.get("s1") is None


# ---------------------------------------------------------------------------
# Test 2 — control_request interleave (real sink + queue + driver, fake engine)
# ---------------------------------------------------------------------------
def test_control_request_interleaves_before_engine_resumes():
    registry = ActiveTurnTable()
    queue: asyncio.Queue[object] = asyncio.Queue()
    sink = build_streaming_prompt_sink(queue, turn_id="t-turn")
    cancel = asyncio.Event()

    request = ControlRequest(
        requestId="req-1",
        turnId="t-turn",
        toolName="Bash",
        arguments={"command": "ls"},
        reason="needs approval",
    )

    class FakeEngine:
        async def run_turn_stream(self, runtime, turn_input, *, cancel, gate):
            yield _ev("text_delta", delta="before tool")
            # Park on the gate: sink.ask puts a control_request on the queue and
            # awaits the matching control_response. This is what the REAL engine
            # does while a tool awaits permission.
            decision = await sink.ask(request)
            yield _ev(
                "tool_end",
                id="c1",
                status="ok" if decision.kind == "allow" else "blocked",
            )
            yield EngineResult(
                terminal=Terminal.completed,
                session_id="s1",
                turn_id="t-turn",
            )

    async def _run() -> str:
        chunks: list[bytes] = []
        gen = drive_streaming_chat(
            FakeEngine(),
            None,
            {"prompt": "hi", "session_id": "s1", "turn_id": "t-turn"},
            cancel=cancel,
            queue=queue,
            sink=sink,
            registry=registry,
            session_id="s1",
            turn_id="t-turn",
        )
        # Drain frames until we observe the control_request — proving the
        # consumer emitted it WHILE the engine is still parked on the gate.
        saw_control = False
        async for chunk in gen:
            chunks.append(chunk)
            text_so_far = b"".join(chunks).decode()
            if "control_request" in text_so_far and not saw_control:
                saw_control = True
                # The engine has NOT resumed yet (no tool_end emitted).
                assert "tool_end" not in text_so_far
                # Now answer the parked ask; the engine resumes.
                sink.deliver(
                    ControlResponse(
                        request_id="req-1",
                        response={"decision": "allow"},
                    )
                )
        assert saw_control, "control_request was never emitted to the client"
        return b"".join(chunks).decode()

    text = asyncio.run(_run())

    payloads = _data_lines(text)
    types = [p["type"] for p in payloads]
    assert "control_request" in types
    # control_request must come BEFORE the tool_end the engine emits on resume.
    assert types.index("control_request") < types.index("tool_end")
    assert types[-1] == "turn_result"
    assert payloads[-1]["terminal"] == "completed"
    assert text.rstrip().endswith("data: [DONE]")
    assert registry.get("s1") is None


# ---------------------------------------------------------------------------
# Test 3 — cancel -> balanced terminal
# ---------------------------------------------------------------------------
def test_cancel_yields_balanced_interrupt_terminal():
    registry = ActiveTurnTable()
    queue: asyncio.Queue[object] = asyncio.Queue()
    sink = build_streaming_prompt_sink(queue, turn_id="t-turn")
    cancel = asyncio.Event()

    class FakeEngine:
        async def run_turn_stream(self, runtime, turn_input, *, cancel, gate):
            yield _ev("tool_start", id="c1", name="Bash")
            # Wait until the driver/test requests cancellation.
            while not cancel.is_set():
                await asyncio.sleep(0)
            # Emulate the real engine's cancel contract (see
            # MagiEngineDriver._synthesize_orphan_tool_results): synthesize the
            # orphan tool_end + turn_end(user_interrupt), then abort. NOTE: the
            # engine sets interrupted=True on the orphan, but the public-surface
            # sanitizer strips that flag; the surviving signal is status="error"
            # + the "interrupted by user cancellation" output_preview.
            yield _ev(
                "tool_end",
                id="c1",
                status="error",
                output_preview="tool interrupted by user cancellation",
                durationMs=0,
                interrupted=True,
            )
            yield _ev("turn_end", turnId="t-turn", status="aborted", reason="user_interrupt")
            yield EngineResult(
                terminal=Terminal.aborted,
                error="cancelled",
                session_id="s1",
                turn_id="t-turn",
            )

    async def _run() -> str:
        chunks: list[bytes] = []
        gen = drive_streaming_chat(
            FakeEngine(),
            None,
            {"prompt": "hi", "session_id": "s1", "turn_id": "t-turn"},
            cancel=cancel,
            queue=queue,
            sink=sink,
            registry=registry,
            session_id="s1",
            turn_id="t-turn",
        )
        cancelled = False
        async for chunk in gen:
            chunks.append(chunk)
            if not cancelled:
                cancelled = True
                cancel.set()
        return b"".join(chunks).decode()

    text = asyncio.run(_run())

    payloads = _data_lines(text)
    types = [p["type"] for p in payloads]

    # interrupted tool_end present (the public-surface signal is status=error +
    # the cancellation output_preview; the raw `interrupted` flag is sanitized out)
    tool_end = next(p for p in payloads if p["type"] == "tool_end")
    assert tool_end.get("status") == "error"
    assert "interrupted by user cancellation" in tool_end.get("output_preview", "")
    # user_interrupt turn_end present
    turn_end = next(p for p in payloads if p["type"] == "turn_end")
    assert turn_end.get("reason") == "user_interrupt"
    assert turn_end.get("status") == "aborted"
    # terminal aborted
    assert types[-1] == "turn_result"
    assert payloads[-1]["terminal"] == "aborted"
    assert text.rstrip().endswith("data: [DONE]")
    assert registry.get("s1") is None


# ---------------------------------------------------------------------------
# Test 4 — producer exception -> error terminal, stream still closes
# ---------------------------------------------------------------------------
def test_producer_exception_yields_error_terminal_and_done():
    registry = ActiveTurnTable()
    queue: asyncio.Queue[object] = asyncio.Queue()
    sink = build_streaming_prompt_sink(queue, turn_id="t-turn")
    cancel = asyncio.Event()

    class FakeEngine:
        async def run_turn_stream(self, runtime, turn_input, *, cancel, gate):
            yield _ev("text_delta", delta="partial")
            raise RuntimeError("boom mid-iteration")

    async def _run() -> str:
        chunks: list[bytes] = []
        async for chunk in drive_streaming_chat(
            FakeEngine(),
            None,
            {"prompt": "hi", "session_id": "s1", "turn_id": "t-turn"},
            cancel=cancel,
            queue=queue,
            sink=sink,
            registry=registry,
            session_id="s1",
            turn_id="t-turn",
        ):
            chunks.append(chunk)
        return b"".join(chunks).decode()

    # Must NOT hang and must terminate cleanly.
    text = asyncio.run(asyncio.wait_for(_run(), timeout=5))

    payloads = _data_lines(text)
    types = [p["type"] for p in payloads]
    assert types[-1] == "turn_result"
    assert payloads[-1]["terminal"] == "error"
    assert "boom mid-iteration" in payloads[-1].get("error", "")
    assert text.rstrip().endswith("data: [DONE]")
    assert registry.get("s1") is None
