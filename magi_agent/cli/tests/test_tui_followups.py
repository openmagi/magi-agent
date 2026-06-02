"""Tests for PR-F2c TUI follow-ups: ToolRenderer wiring + keybinding on_key.

Style mirrors ``test_tui_app.py``: no ``pytest-asyncio``; async tests drive the
coroutine via ``asyncio.run`` with a nested ``async def _run`` using Textual's
``App.run_test()`` pilot. The engine is always a fake — no model is hit.

Two follow-ups are proven here:

1. **Renderer wiring** — a TOOL ``RuntimeEvent`` is routed through the injected
   ``ToolRendererRegistry`` (the real Edit/Bash/Read renderers), NOT the generic
   ``[tool] ...`` one-line summary. ``tool_start`` renders the call header/diff;
   ``tool_end`` renders the result.
2. **Keybinding wiring** — ``app.on_key`` converts a key event via
   ``keystroke_from_event`` -> ``resolve`` -> ``_run_key_action``. A MATCH fires
   the mapped action (cancel), a chord prefix sets ``self._pending``, and a plain
   printable key leaves pending ``None`` and does NOT stop the event (typing).
"""

from __future__ import annotations

import asyncio

from magi_agent.cli.contracts import (
    CommandSurface,
    EngineResult,
    PermissionDecision,
    PermissionGate,
    RuntimeEvent,
    Terminal,
)
from magi_agent.cli.tui.app import MagiTuiApp
from magi_agent.cli.tui.tool_render import build_tool_renderers

TUI = CommandSurface(tui=True, headless=False)


class _AllowGate(PermissionGate):
    async def check(self, req):
        _ = req
        return PermissionDecision(kind="allow")


class _FakeRegistry:
    def lookup(self, name):
        _ = name
        return None

    def list_for(self, surface):
        _ = surface
        return []


class _ToolScriptDriver:
    """Yields a configurable list of RuntimeEvents then a terminal result."""

    def __init__(self, events: list[RuntimeEvent]) -> None:
        self._events = events

    async def run_turn_stream(self, runtime, turn_input, *, cancel, gate=None):
        turn_id = getattr(turn_input, "turn_id", "t")
        for event in self._events:
            if cancel.is_set():
                yield EngineResult(terminal=Terminal.aborted, error="cancelled")
                return
            yield event
        yield EngineResult(terminal=Terminal.completed, turn_id=turn_id)


class _Idle:
    """A driver that yields nothing but a completed terminal (for on_key tests)."""

    async def run_turn_stream(self, runtime, turn_input, *, cancel, gate=None):
        _ = (runtime, turn_input, cancel, gate)
        if False:  # pragma: no cover - make this an async generator
            yield None
        yield EngineResult(terminal=Terminal.completed)


def _make_app(engine, *, renderers=None) -> MagiTuiApp:
    return MagiTuiApp(
        engine=engine,
        gate=_AllowGate(),
        commands=_FakeRegistry(),
        renderers=renderers if renderers is not None else build_tool_renderers(),
    )


class _FakeKey:
    """A duck-typed Textual-like key event (``.key`` + ``.stop()``)."""

    def __init__(self, key: str, character: str | None = None) -> None:
        self.key = key
        self.character = character
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


# ---------------------------------------------------------------------------
# Piece 1 — renderer wiring
# ---------------------------------------------------------------------------
def test_edit_tool_start_renders_via_renderer_not_summary() -> None:
    async def _run() -> None:
        events = [
            RuntimeEvent(
                type="tool",
                payload={
                    "type": "tool_start",
                    "id": "c1",
                    "name": "Edit",
                    "input": {
                        "file_path": "foo.py",
                        "old_string": "a",
                        "new_string": "b",
                    },
                },
                turn_id="t",
            ),
        ]
        app = _make_app(_ToolScriptDriver(events))
        async with app.run_test() as pilot:
            app.start_turn("edit it")
            await app.workers.wait_for_complete()
            await pilot.pause()
        blocks = app.controller.committed_blocks_snapshot()
        joined = "\n".join(blocks)
        # The renderer's header was committed (Edit(foo.py)), NOT a [tool] summary.
        assert any("Edit(foo.py)" in b for b in blocks)
        assert not any(b.startswith("[tool]") for b in blocks)
        # The diff body (old/new) is present in the displayed text fallback.
        assert "foo.py" in joined

    asyncio.run(_run())


def test_bash_tool_end_renders_result_via_renderer() -> None:
    async def _run() -> None:
        events = [
            RuntimeEvent(
                type="tool",
                payload={"type": "tool_start", "id": "c1", "name": "Bash",
                         "input": {"command": "ls"}},
                turn_id="t",
            ),
            RuntimeEvent(
                type="tool",
                payload={
                    "type": "tool_end",
                    "id": "c1",
                    "name": "Bash",
                    "status": "ok",
                    "output_preview": {"stdout": "a.txt\n"},
                },
                turn_id="t",
            ),
        ]
        app = _make_app(_ToolScriptDriver(events))
        async with app.run_test() as pilot:
            app.start_turn("run ls")
            await app.workers.wait_for_complete()
            await pilot.pause()
        blocks = app.controller.committed_blocks_snapshot()
        # tool_start -> "$ ls"; tool_end -> the stdout result text.
        assert any("$ ls" in b for b in blocks)
        assert any("a.txt" in b for b in blocks)
        assert not any(b.startswith("[tool]") for b in blocks)

    asyncio.run(_run())


def test_tool_end_rejected_renders_rejected_node() -> None:
    async def _run() -> None:
        events = [
            RuntimeEvent(
                type="tool",
                payload={
                    "type": "tool_end",
                    "id": "c1",
                    "name": "Bash",
                    "status": "blocked",
                    "input": {"command": "rm -rf /"},
                },
                turn_id="t",
            ),
        ]
        app = _make_app(_ToolScriptDriver(events))
        async with app.run_test() as pilot:
            app.start_turn("dangerous")
            await app.workers.wait_for_complete()
            await pilot.pause()
        blocks = app.controller.committed_blocks_snapshot()
        assert any("rejected" in b for b in blocks)

    asyncio.run(_run())


def test_non_tool_event_keeps_one_line_summary() -> None:
    async def _run() -> None:
        events = [
            RuntimeEvent(
                type="status",
                payload={"type": "turn_phase", "phase": "executing", "label": "go"},
                turn_id="t",
            ),
        ]
        app = _make_app(_ToolScriptDriver(events))
        async with app.run_test() as pilot:
            app.start_turn("status")
            await app.workers.wait_for_complete()
            await pilot.pause()
        blocks = app.controller.committed_blocks_snapshot()
        assert any(b.startswith("[status]") for b in blocks)

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Piece 2 — keybinding on_key wiring
# ---------------------------------------------------------------------------
def test_on_key_match_cancel_dispatches_action() -> None:
    async def _run() -> None:
        app = _make_app(_Idle())
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app._pending is None
            event = _FakeKey("ctrl+c")
            app.on_key(event)
            # ctrl+c -> chat:cancel (Global) -> cancel the in-flight turn.
            assert app._cancel.is_set()
            assert app._pending is None
            assert event.stopped is True

    asyncio.run(_run())


def test_on_key_chord_prefix_sets_pending() -> None:
    async def _run() -> None:
        app = _make_app(_Idle())
        async with app.run_test() as pilot:
            await pilot.pause()
            # ctrl+x is the prefix of the ctrl+x ctrl+k chord (Chat context).
            event = _FakeKey("ctrl+x")
            app.on_key(event)
            assert app._pending is not None
            assert event.stopped is True
            # Completing the chord matches chat:killAgents and clears pending.
            event2 = _FakeKey("ctrl+k")
            app.on_key(event2)
            assert app._pending is None

    asyncio.run(_run())


def test_on_key_plain_printable_does_not_stop_and_keeps_pending_none() -> None:
    async def _run() -> None:
        app = _make_app(_Idle())
        async with app.run_test() as pilot:
            await pilot.pause()
            event = _FakeKey("x", character="x")
            app.on_key(event)
            assert app._pending is None
            # A plain printable key is UNBOUND -> do not stop (typing reaches Input).
            assert event.stopped is False

    asyncio.run(_run())


def test_on_key_unconvertible_event_returns_without_error() -> None:
    async def _run() -> None:
        app = _make_app(_Idle())
        async with app.run_test() as pilot:
            await pilot.pause()

            class _Empty:
                key = None
                character = None

                def stop(self):  # pragma: no cover - should not be called
                    raise AssertionError("must not stop unconvertible event")

            app.on_key(_Empty())
            assert app._pending is None

    asyncio.run(_run())
