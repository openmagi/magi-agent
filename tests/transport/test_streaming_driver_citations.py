"""Wave 3a: drive_streaming_chat composes the terminal citations payload.

Verifies the live SSE path accumulates visible text, reaches the registry via a
provider, and rides a citations payload on the terminal frame. A None provider
(default) stays byte-identical. No em-dashes per the citation feature style rule.
"""
from __future__ import annotations

import asyncio
import json

from magi_agent.engine.contracts import EngineResult, Terminal
from magi_agent.evidence.citation_registry import SessionSourceRegistry
from magi_agent.runtime.events import RuntimeEvent
from magi_agent.transport.active_turn import ActiveTurnTable
from magi_agent.transport.streaming_driver import drive_streaming_chat


class _FakeSink:
    def close(self) -> None:
        return None


class _FakeEngine:
    def __init__(self, text: str, session_id: str, turn_id: str) -> None:
        self._text = text
        self._session_id = session_id
        self._turn_id = turn_id

    async def run_turn_stream(self, runtime, turn_input, *, cancel, gate=None):
        yield RuntimeEvent(
            type="token", payload={"delta": self._text}, turn_id=self._turn_id
        )
        yield EngineResult(
            terminal=Terminal.completed,
            usage={},
            cost_usd=0.0,
            error=None,
            session_id=self._session_id,
            turn_id=self._turn_id,
        )


async def _collect(engine, provider) -> list[bytes]:
    cancel = asyncio.Event()
    queue: asyncio.Queue = asyncio.Queue()
    chunks: list[bytes] = []
    async for chunk in drive_streaming_chat(
        engine,
        object(),
        {"prompt": "p", "session_id": "s1", "turn_id": "t1"},
        cancel=cancel,
        queue=queue,
        sink=_FakeSink(),
        registry=ActiveTurnTable(),
        session_id="s1",
        turn_id="t1",
        citation_registry_provider=provider,
    ):
        chunks.append(chunk)
    return chunks


def _terminal_citations(chunks: list[bytes]) -> dict | None:
    for chunk in chunks:
        text = chunk.decode()
        if '"type":"turn_result"' in text or '"type": "turn_result"' in text:
            body = text[len("event: agent\ndata: ") :].strip()
            return json.loads(body).get("citations")
    raise AssertionError("no turn_result frame")


def test_provider_none_terminal_frame_has_no_citations() -> None:
    engine = _FakeEngine("no markers", "s1", "t1")
    chunks = asyncio.run(_collect(engine, None))
    assert _terminal_citations(chunks) is None
    assert b"citations" not in b"".join(chunks)


def test_provider_populates_terminal_citations() -> None:
    registry = SessionSourceRegistry(session_id="s1")
    record = registry.register(
        "web_fetch",
        "https://sec.gov/tsla",
        turn_id="t1",
        tool_name="web_fetch",
        title="Tesla 10-Q",
        trust_tier="official",
        inspected=True,
    )
    assert record is not None
    src = record.source_id
    engine = _FakeEngine(f"Revenue was 12.77B [{src}].", "s1", "t1")
    chunks = asyncio.run(_collect(engine, lambda sid: registry))
    citations = _terminal_citations(chunks)
    assert citations is not None
    assert citations["markers"] == [[src, 1]]
    assert citations["verdict"] == "cited"
    assert citations["sources"][0]["uri"] == "https://sec.gov/tsla"
