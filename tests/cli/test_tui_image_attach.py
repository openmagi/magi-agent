"""Tests for Task 4: clipboard image attach wired into MagiTuiApp.

Three behaviors:
1. attach success → pending_attachments has the one block.
2. attach with reader→None → buffer stays empty (+ a toast).
3. submitting a turn passes image_blocks into the TurnInput and clears the buffer.
"""

from __future__ import annotations

import asyncio
import base64

import pytest

_PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n").decode("ascii")
_BLOCK: dict = {
    "type": "image",
    "source": {"type": "base64", "media_type": "image/png", "data": _PNG},
}


# ---------------------------------------------------------------------------
# Helpers: minimal fake engine + make_app mirroring existing test pattern
# ---------------------------------------------------------------------------

from magi_agent.cli.contracts import (
    CommandSurface,
    EngineResult,
    LocalCommand,
    PermissionDecision,
    PermissionGate,
    RuntimeEvent,
    Terminal,
    ToolRendererRegistry,
    TurnInput,
)
from magi_agent.cli.contracts import ControlRequest


class _FakeRegistry:
    def __init__(self) -> None:
        self._commands = [LocalCommand(name="compact", surface=CommandSurface(tui=True, headless=False))]

    def lookup(self, name: str):
        for cmd in self._commands:
            if getattr(cmd, "name", None) == name:
                return cmd
        return None

    def list_for(self, surface):
        return list(self._commands)


class _AllowGate(PermissionGate):
    async def check(self, req: ControlRequest) -> PermissionDecision:
        return PermissionDecision(kind="allow")


class _RecordingEngine:
    """Fake engine that records the TurnInput it receives."""

    def __init__(self) -> None:
        self.received: list[TurnInput] = []

    async def run_turn_stream(self, runtime, turn_input, *, cancel, gate=None):
        self.received.append(turn_input)
        turn_id = getattr(turn_input, "turn_id", "t")
        yield RuntimeEvent(type="token", payload={"delta": "ok"}, turn_id=turn_id)
        yield EngineResult(terminal=Terminal.completed, turn_id=turn_id)


def _make_app(reader, engine=None, **extra):
    from magi_agent.cli.tui.app import MagiTuiApp

    return MagiTuiApp(
        engine=engine if engine is not None else _RecordingEngine(),
        gate=_AllowGate(),
        commands=_FakeRegistry(),
        renderers=ToolRendererRegistry(),
        clipboard_reader=reader,
        **extra,
    )


# ---------------------------------------------------------------------------
# Test 1: attach success → buffer gains the block
# ---------------------------------------------------------------------------


def test_attach_appends_to_pending_buffer() -> None:
    async def _run() -> None:
        app = _make_app(lambda: _BLOCK)
        async with app.run_test():
            app.attach_clipboard_image()
            assert list(app.pending_attachments) == [_BLOCK]

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Test 2: reader returns None → buffer stays empty
# ---------------------------------------------------------------------------


def test_attach_noop_when_no_image() -> None:
    async def _run() -> None:
        app = _make_app(lambda: None)
        async with app.run_test():
            app.attach_clipboard_image()
            assert list(app.pending_attachments) == []

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Test 3: submitting a turn passes image_blocks and clears the buffer
# ---------------------------------------------------------------------------


def test_submit_includes_image_blocks_and_clears_buffer() -> None:
    async def _run() -> None:
        engine = _RecordingEngine()
        app = _make_app(lambda: _BLOCK, engine=engine)
        async with app.run_test() as pilot:
            # Attach an image first (the engine is passed explicitly so we can inspect it)
            app.attach_clipboard_image()
            assert list(app.pending_attachments) == [_BLOCK]

            # Now start a turn (mirrors how test_tui_app.py tests do it)
            app.start_turn("hello")
            await app.workers.wait_for_complete()
            await pilot.pause()

        # Engine received the TurnInput with the image block
        assert len(engine.received) == 1
        turn_input = engine.received[0]
        assert turn_input.image_blocks == (_BLOCK,), (
            f"Expected image_blocks=({_BLOCK!r},), got {turn_input.image_blocks!r}"
        )
        # Buffer was cleared after submission
        assert list(app.pending_attachments) == []

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Test 4: multiple attach calls accumulate; buffer cleared after submit
# ---------------------------------------------------------------------------


def test_multiple_attaches_accumulate_then_clear() -> None:
    async def _run() -> None:
        engine = _RecordingEngine()
        call_count = 0

        def reader():
            nonlocal call_count
            call_count += 1
            return _BLOCK

        app = _make_app(reader, engine=engine)
        async with app.run_test() as pilot:
            app.attach_clipboard_image()
            app.attach_clipboard_image()
            assert len(app.pending_attachments) == 2

            app.start_turn("two images")
            await app.workers.wait_for_complete()
            await pilot.pause()

        assert len(engine.received) == 1
        assert len(engine.received[0].image_blocks) == 2
        assert list(app.pending_attachments) == []

    asyncio.run(_run())
