"""PR-04-PR1: activate the SessionLog write path in run_headless (gated).

The resume reconstruction machine (``session_log.reconstruct_messages`` etc.)
was already built but had NO production writer: ``SessionLog.append`` had zero
callers, so resume had nothing to read. These tests pin the newly-activated
drain tap: a live headless turn, with the gate ON, writes the user prompt +
the sanitized engine events to a JSONL transcript that ``reconstruct_messages``
can replay. With the gate OFF (default, stage 1) no file is written.
"""

from __future__ import annotations

import asyncio
import io
from collections.abc import AsyncGenerator

import pytest

from magi_agent.cli.contracts import EngineResult, RuntimeEvent, Terminal
from magi_agent.cli.headless import run_headless
from magi_agent.cli.session_log import (
    SessionLog,
    load,
    reconstruct_linear_chain,
    reconstruct_messages,
)


class _TranscriptDriver:
    """Fake engine driver emitting realistically-sanitized agent events.

    Mirrors the real engine: assistant text arrives as ``text_delta`` payloads
    (``payload={"type": "text_delta", "delta": ...}``) and tool activity as a
    ``tool`` event. ``reconstruct_messages`` reads the inner ``payload["type"]``,
    not the ``RuntimeEvent.type`` envelope kind.
    """

    def __init__(self, *, deltas: tuple[str, ...]) -> None:
        self._deltas = deltas

    async def run_turn_stream(
        self,
        runtime: object,
        turn_input: object,
        *,
        cancel: asyncio.Event,
        gate: object | None = None,
    ) -> AsyncGenerator[RuntimeEvent, EngineResult]:
        _ = (runtime, turn_input, gate, cancel)
        turn_id = "t1"
        yield RuntimeEvent(
            type="status",
            payload={"type": "turn_start"},
            turn_id=turn_id,
        )
        for delta in self._deltas:
            yield RuntimeEvent(
                type="token",
                payload={"type": "text_delta", "delta": delta},
                turn_id=turn_id,
            )
        yield RuntimeEvent(
            type="tool",
            payload={"type": "tool_start", "name": "FileRead"},
            turn_id=turn_id,
        )
        yield EngineResult(  # type: ignore[misc]
            terminal=Terminal.completed,
            usage={"input_tokens": 1, "output_tokens": 2},
            cost_usd=0.0,
            error=None,
        )


def _new_log(tmp_path) -> SessionLog:
    return SessionLog(session_id="sess-1", cwd=str(tmp_path))


def test_gate_on_writes_user_and_assistant_transcript(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
    monkeypatch.setenv("MAGI_CLI_SESSION_LOG_ENABLED", "1")

    log = _new_log(tmp_path)
    buffer = io.StringIO()
    code = asyncio.run(
        run_headless(
            "hello there",
            output="stream-json",
            driver=_TranscriptDriver(deltas=("Hi", " back")),
            session_id="sess-1",
            session_log=log,
            stream=buffer,
        )
    )
    assert code == 0

    # The JSONL file exists on disk.
    assert log.path.exists()

    envelopes = load(log.path)
    assert envelopes, "expected at least one persisted envelope"

    # The parent_uuid chain is linear (every non-root envelope's parent is the
    # immediately-preceding uuid).
    uuids = [e.uuid for e in envelopes]
    assert envelopes[0].parent_uuid is None
    for prev, env in zip(uuids, envelopes[1:]):
        assert env.parent_uuid == prev

    chain = reconstruct_linear_chain(envelopes)
    messages = reconstruct_messages(chain)
    assert {"role": "user", "content": "hello there"} in messages
    assert {"role": "assistant", "content": "Hi back"} in messages


def test_gate_off_writes_no_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
    monkeypatch.setenv("MAGI_CLI_SESSION_LOG_ENABLED", "0")

    log = _new_log(tmp_path)
    buffer = io.StringIO()
    code = asyncio.run(
        run_headless(
            "hello there",
            output="stream-json",
            driver=_TranscriptDriver(deltas=("Hi",)),
            session_id="sess-1",
            session_log=log,
            stream=buffer,
        )
    )
    assert code == 0
    # Gate OFF: nothing persisted.
    assert not log.path.exists()


def test_default_gate_is_off(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("MAGI_CLI_ENABLED", "1")
    monkeypatch.delenv("MAGI_CLI_SESSION_LOG_ENABLED", raising=False)
    # Neutralize any inherited runtime-profile so the strict default applies.
    monkeypatch.delenv("MAGI_RUNTIME_PROFILE", raising=False)

    log = _new_log(tmp_path)
    buffer = io.StringIO()
    asyncio.run(
        run_headless(
            "hello there",
            output="text",
            driver=_TranscriptDriver(deltas=("Hi",)),
            session_id="sess-1",
            session_log=log,
            stream=buffer,
        )
    )
    # Stage 1 default-OFF: no file unless explicitly enabled.
    assert not log.path.exists()
