from __future__ import annotations

import asyncio
import io
import json

from openmagi_core_agent.cli.ndjson import NdjsonWriter, ndjson_dumps
from openmagi_core_agent.cli.protocol import AssistantFrame, ResultFrame, SystemInit


def test_ndjson_dumps_golden() -> None:
    frame = SystemInit(uuid="u1", session_id="s1", model="m", tools=["t"], cwd="/w")
    line = ndjson_dumps(frame)
    assert "\n" not in line
    parsed = json.loads(line)
    assert parsed["type"] == "system"
    assert parsed["subtype"] == "init"
    assert parsed["model"] == "m"
    assert parsed["tools"] == ["t"]
    assert parsed["uuid"] == "u1"


def test_ndjson_dumps_escapes_u2028_u2029() -> None:
    frame = AssistantFrame(message={"content": "a b c"})
    line = ndjson_dumps(frame)
    # Raw separators must NOT appear; escaped forms MUST appear.
    assert " " not in line
    assert " " not in line
    assert "\\u2028" in line
    assert "\\u2029" in line
    # Still valid JSON whose decoded value contains the raw chars.
    parsed = json.loads(line)
    assert parsed["message"]["content"] == "a b c"


def test_writer_fifo_ordering() -> None:
    buffer = io.StringIO()

    async def run() -> None:
        writer = NdjsonWriter(buffer)
        for i in range(10):
            await writer.write(
                AssistantFrame(uuid=f"u{i}", session_id="s", message={"i": i})
            )
        await writer.aclose()

    asyncio.run(run())
    lines = [line for line in buffer.getvalue().splitlines() if line]
    assert len(lines) == 10
    order = [json.loads(line)["message"]["i"] for line in lines]
    assert order == list(range(10))


def test_aclose_no_pending_task() -> None:
    buffer = io.StringIO()

    async def run() -> NdjsonWriter:
        writer = NdjsonWriter(buffer)
        await writer.write(ResultFrame(subtype="success", result="ok"))
        await writer.aclose()
        return writer

    writer = asyncio.run(run())
    # Drainer task has been awaited and cleared.
    assert writer._task is None
    # Buffer received exactly the one frame.
    lines = [line for line in buffer.getvalue().splitlines() if line]
    assert len(lines) == 1


def test_aclose_without_write_is_safe() -> None:
    buffer = io.StringIO()

    async def run() -> None:
        writer = NdjsonWriter(buffer)
        await writer.aclose()

    asyncio.run(run())
    assert buffer.getvalue() == ""


def test_drainer_survives_per_frame_write_error() -> None:
    """A failing write on one frame must not kill the drainer or lose later frames."""

    class FlakyStream(io.StringIO):
        def __init__(self) -> None:
            super().__init__()
            self._calls = 0

        def write(self, s: str) -> int:  # type: ignore[override]
            self._calls += 1
            if self._calls == 1:
                raise BrokenPipeError("boom on first write")
            return super().write(s)

    stream = FlakyStream()

    async def run() -> None:
        writer = NdjsonWriter(stream)
        await writer.write(AssistantFrame(uuid="u0", session_id="s", message={"i": 0}))
        await writer.write(AssistantFrame(uuid="u1", session_id="s", message={"i": 1}))
        await writer.aclose()

    # aclose must NOT re-raise the drainer's per-frame error.
    asyncio.run(run())
    lines = [line for line in stream.getvalue().splitlines() if line]
    # First frame's write raised and was swallowed; the second still made it.
    assert len(lines) == 1
    assert json.loads(lines[0])["message"]["i"] == 1
