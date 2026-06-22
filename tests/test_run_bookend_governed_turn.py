"""Wiring: ``run_governed_turn`` persists ONE run-bookend per turn when the flag
is on, and stays byte-identical (no extra ledger write) when off.

Uses a fake runtime whose engine streams a couple of text deltas then a terminal
``EngineResult`` (the shape the real engine yields), so the test exercises the
funnel without the full ADK/engine machinery.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from magi_agent.cli.contracts import EngineResult, Terminal
from magi_agent.evidence.ledger_store import EvidenceLedgerReader
from magi_agent.evidence.run_bookend import RUN_BOOKEND_TOOL_NAME
from magi_agent.runtime.events import RuntimeEvent
from magi_agent.runtime.governed_turn import run_governed_turn
from magi_agent.runtime.turn_context import TurnContext


class _FakeEngine:
    def __init__(self, items: list[object]) -> None:
        self._items = items

    async def run_turn_stream(
        self, _none: object, _turn_input: object, *, cancel: object, gate: object
    ) -> AsyncIterator[object]:
        for item in self._items:
            yield item


class _FakeRuntime:
    def __init__(self, items: list[object]) -> None:
        self.engine = _FakeEngine(items)
        self.gate = None


def _stream() -> list[object]:
    return [
        RuntimeEvent(type="token", payload={"type": "text_delta", "delta": "Fixed 12 "}),
        RuntimeEvent(type="token", payload={"type": "text_delta", "delta": "issues."}),
        EngineResult(
            terminal=Terminal.completed,
            # Real engine yields ADK snake_case usage keys (see _adk_usage_metadata).
            usage={"input_tokens": 1500, "output_tokens": 800},
            cost_usd=0.05,
            session_id="sess-1",
            turn_id="turn-1",
        ),
    ]


def _ctx() -> TurnContext:
    return TurnContext(
        prompt="Fix the lint errors and open a PR",
        session_id="sess-1",
        turn_id="turn-1",
        provider="anthropic",
        model="claude-opus-4-8",
    )


async def _drain(ctx: TurnContext, runtime: object) -> list[object]:
    return [item async for item in run_governed_turn(ctx, runtime=runtime)]


@pytest.mark.asyncio
async def test_flag_off_writes_no_bookend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAGI_EVIDENCE_LEDGER_DIR", str(tmp_path))
    monkeypatch.delenv("MAGI_PERSIST_RUN_BOOKENDS_ENABLED", raising=False)

    items = await _drain(_ctx(), _FakeRuntime(_stream()))

    # Stream is passed through unchanged.
    assert len(items) == 3
    rows = EvidenceLedgerReader(tmp_path).read("sess-1")
    assert [r for r in rows if r.get("toolName") == RUN_BOOKEND_TOOL_NAME] == []


@pytest.mark.asyncio
async def test_flag_on_persists_one_bookend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAGI_EVIDENCE_LEDGER_DIR", str(tmp_path))
    monkeypatch.setenv("MAGI_PERSIST_RUN_BOOKENDS_ENABLED", "1")

    items = await _drain(_ctx(), _FakeRuntime(_stream()))
    assert len(items) == 3  # stream unchanged

    rows = EvidenceLedgerReader(tmp_path).read("sess-1")
    bookends = [r for r in rows if r.get("toolName") == RUN_BOOKEND_TOOL_NAME]
    assert len(bookends) == 1
    payload = bookends[0]["record"]
    assert payload["goal"] == "Fix the lint errors and open a PR"
    assert payload["result"] == "Fixed 12 issues."  # accumulated from text deltas
    assert payload["status"] == "ok"  # Terminal.completed -> ok
    assert payload["model"] == {"label": "claude-opus-4-8", "provider": "anthropic"}
    assert payload["usage"] == {"inputTokens": 1500, "outputTokens": 800}
    assert payload["costUsd"] == 0.05


@pytest.mark.asyncio
async def test_flag_on_records_aborted_status_with_no_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAGI_EVIDENCE_LEDGER_DIR", str(tmp_path))
    monkeypatch.setenv("MAGI_PERSIST_RUN_BOOKENDS_ENABLED", "1")

    stream: list[object] = [
        EngineResult(
            terminal=Terminal.aborted,
            usage={},
            cost_usd=0.0,
            error="cancelled",
            session_id="sess-1",
            turn_id="turn-1",
        )
    ]
    await _drain(_ctx(), _FakeRuntime(stream))

    rows = EvidenceLedgerReader(tmp_path).read("sess-1")
    bookends = [r for r in rows if r.get("toolName") == RUN_BOOKEND_TOOL_NAME]
    assert len(bookends) == 1
    payload = bookends[0]["record"]
    assert payload["status"] == "aborted"
    assert "result" not in payload  # nothing was emitted
    assert "usage" not in payload


@pytest.mark.asyncio
async def test_flag_on_early_close_persists_exactly_one_bookend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A consumer that stops before the terminal EngineResult still gets exactly
    one bookend (persisted in the generator's finally), with status 'unknown'."""
    monkeypatch.setenv("MAGI_EVIDENCE_LEDGER_DIR", str(tmp_path))
    monkeypatch.setenv("MAGI_PERSIST_RUN_BOOKENDS_ENABLED", "1")

    gen = run_governed_turn(_ctx(), runtime=_FakeRuntime(_stream()))
    first = await gen.__anext__()  # consume only the first text delta
    assert getattr(first, "payload", {}).get("type") == "text_delta"
    await gen.aclose()  # early close -> triggers the finally

    rows = EvidenceLedgerReader(tmp_path).read("sess-1")
    bookends = [r for r in rows if r.get("toolName") == RUN_BOOKEND_TOOL_NAME]
    assert len(bookends) == 1
    assert bookends[0]["record"]["status"] == "unknown"  # terminal never seen


@pytest.mark.asyncio
async def test_flag_on_but_durable_sink_disabled_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # MAGI_EVIDENCE_LEDGER_DIR=off disables the durable sink entirely.
    monkeypatch.setenv("MAGI_EVIDENCE_LEDGER_DIR", "off")
    monkeypatch.setenv("MAGI_PERSIST_RUN_BOOKENDS_ENABLED", "1")

    # Should not raise even though there is nowhere to write.
    await _drain(_ctx(), _FakeRuntime(_stream()))
    # Nothing under tmp_path.
    assert list(tmp_path.glob("*.jsonl")) == []
