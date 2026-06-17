"""Engine run_turn wrapper for channels (PR1.5).

Drives ONE governed turn and returns the final assistant text, reusing the
existing ``collect_governed_child_turn`` extractor (so we inherit the engine's
event contract rather than reinventing normalization).  ``stream_factory`` is
injected so the wrapper is tested against a fake event stream with no engine or
network.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

from magi_agent.channels.turn_bridge import ChannelInbound
from magi_agent.channels.turn_engine import (
    make_engine_run_turn,
    run_channel_turn_async,
)
from magi_agent.cli.contracts import EngineResult, Terminal
from magi_agent.runtime.events import RuntimeEvent
from magi_agent.runtime.turn_context import TurnContext


def _delta(text: str) -> RuntimeEvent:
    return RuntimeEvent(type="token", payload={"type": "text_delta", "delta": text})


def test_run_channel_turn_async_builds_ctx_and_collects_final_text() -> None:
    captured: dict[str, object] = {}

    def fake_factory(ctx: TurnContext) -> AsyncGenerator[object, None]:
        captured["prompt"] = ctx.prompt
        captured["session_id"] = ctx.session_id
        captured["turn_id"] = ctx.turn_id

        async def gen() -> AsyncGenerator[object, None]:
            yield _delta("po")
            yield _delta("ng")
            yield EngineResult(terminal=Terminal.completed)

        return gen()

    out = asyncio.run(
        run_channel_turn_async(
            "agent:main:telegram:42",
            "ping",
            turn_id="t-1",
            stream_factory=fake_factory,
        )
    )

    assert out == "pong"
    assert captured == {
        "prompt": "ping",
        "session_id": "agent:main:telegram:42",
        "turn_id": "t-1",
    }


def test_make_engine_run_turn_matches_bridge_runturn_signature() -> None:
    def fake_factory(ctx: TurnContext) -> AsyncGenerator[object, None]:
        async def gen() -> AsyncGenerator[object, None]:
            yield _delta("hi there")
            yield EngineResult(terminal=Terminal.completed)

        return gen()

    run_turn = make_engine_run_turn(stream_factory=fake_factory)

    reply = run_turn(
        "agent:main:discord:9",
        ChannelInbound(channel_type="discord", channel_id="9", text="yo", message_id="m1"),
    )

    assert reply == "hi there"
