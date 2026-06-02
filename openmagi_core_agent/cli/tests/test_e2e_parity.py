"""End-to-end parity: one engine, two surfaces (PR-F2b).

The CORE invariant of Stream F: the headless NDJSON surface and the interactive
Textual surface drive the SAME ``EngineDriver.run_turn_stream`` generator and
render the SAME logical events (same assistant text + the same sequence of
tool/status events). This test drives ONE fixed RuntimeEvent script through both
``run_headless`` and ``MagiTuiApp`` and asserts both consume that one generator
and surface the same logical content.

Style: no ``pytest-asyncio``; async code runs via ``asyncio.run``. The engine is
always a fake driver — no model is hit. The TUI half uses Textual's
``run_test()`` pilot, mirroring ``test_tui_app.py``.
"""

from __future__ import annotations

import asyncio
import io
import json

import pytest

from openmagi_core_agent.cli.contracts import (
    CommandSurface,
    EngineResult,
    NullPermissionGate,
    RuntimeEvent,
    Terminal,
    ToolRendererRegistry,
)
from openmagi_core_agent.cli.headless import run_headless
from openmagi_core_agent.cli.tui.app import MagiTuiApp

TUI = CommandSurface(tui=True, headless=False)


def _script(turn_id: str) -> list[RuntimeEvent]:
    """The single canonical engine event script shared by both surfaces."""
    return [
        RuntimeEvent(type="token", payload={"delta": "Listing "}, turn_id=turn_id),
        RuntimeEvent(type="token", payload={"delta": "files"}, turn_id=turn_id),
        RuntimeEvent(
            type="status",
            payload={"type": "turn_phase", "phase": "executing", "label": "exec"},
            turn_id=turn_id,
        ),
        RuntimeEvent(
            type="tool",
            payload={
                "type": "tool_start",
                "id": "call-1",
                "name": "Bash",
                "input_preview": '{"cmd":"ls"}',
            },
            turn_id=turn_id,
        ),
        RuntimeEvent(
            type="tool",
            payload={
                "type": "tool_end",
                "id": "call-1",
                "status": "ok",
                "output_preview": "a.txt",
            },
            turn_id=turn_id,
        ),
        RuntimeEvent(type="token", payload={"delta": "done"}, turn_id=turn_id),
    ]


class OneShotDriver:
    """An EngineDriver whose ``run_turn_stream`` yields ONE shared script.

    Records the sequence of yielded RuntimeEvents (the "logical events") so the
    parity assertion can compare against what each surface actually consumed.
    A fresh instance is used per surface; both are driven with the identical
    script so a single source-of-truth event list backs both renderings.
    """

    def __init__(self, events: list[RuntimeEvent]) -> None:
        self._events = events
        self.yielded: list[RuntimeEvent] = []
        self.call_count = 0

    async def run_turn_stream(self, runtime, turn_input, *, cancel, gate=None):
        self.call_count += 1
        turn_id = getattr(turn_input, "turn_id", None)
        if turn_id is None and isinstance(turn_input, dict):
            turn_id = turn_input.get("turn_id")
        for event in self._events:
            if cancel.is_set():
                yield EngineResult(terminal=Terminal.aborted, error="cancelled")
                return
            self.yielded.append(event)
            yield event
        yield EngineResult(terminal=Terminal.completed)


def _logical_summary(events: list[RuntimeEvent]) -> tuple[str, list[str]]:
    """Reduce a RuntimeEvent list to (assistant_text, ordered non-token tags)."""
    text_parts: list[str] = []
    tags: list[str] = []
    for ev in events:
        if ev.type == "token":
            for key in ("delta", "text"):
                value = ev.payload.get(key)
                if isinstance(value, str):
                    text_parts.append(value)
                    break
        else:
            inner = ev.payload.get("type") or ev.type
            tags.append(str(inner))
    return "".join(text_parts), tags


def test_one_engine_two_surfaces_render_same_logical_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_CLI_ENABLED", "1")

    # ----- Surface 1: headless NDJSON -----
    headless_driver = OneShotDriver(_script("h-turn"))
    buffer = io.StringIO()
    asyncio.run(
        run_headless(
            "list files",
            output="stream-json",
            include_partial=True,
            gate=NullPermissionGate(allow_in_test=True),
            driver=headless_driver,
            stream=buffer,
        )
    )
    headless_objs = [json.loads(line) for line in buffer.getvalue().splitlines() if line]

    # ----- Surface 2: Textual TUI -----
    tui_driver = OneShotDriver(_script("t-turn"))

    async def _run_tui() -> list[str]:
        app = MagiTuiApp(
            engine=tui_driver,
            gate=NullPermissionGate(allow_in_test=True),
            commands=_FakeRegistry(),
            renderers=ToolRendererRegistry(),
        )
        async with app.run_test() as pilot:
            app.start_turn("list files")
            await app.workers.wait_for_complete()
            await pilot.pause()
        return app.controller.committed_blocks_snapshot()

    tui_blocks = asyncio.run(_run_tui())

    # ----- Both consumed exactly ONE engine generator -----
    assert headless_driver.call_count == 1
    assert tui_driver.call_count == 1

    # ----- Both consumed the SAME logical event sequence -----
    h_text, h_tags = _logical_summary(headless_driver.yielded)
    t_text, t_tags = _logical_summary(tui_driver.yielded)
    assert h_text == t_text == "Listing filesdone"
    assert h_tags == t_tags  # same ordered non-token event tags on both

    # ----- The same assistant text appears on both surfaces -----
    # Consolidation yields a "Listing files" text frame (one run of tokens) and
    # a separate "done" frame (after the tool break).
    headless_text_frames = [
        o["message"]["content"]
        for o in headless_objs
        if o["type"] == "assistant" and isinstance(o["message"]["content"], str)
    ]
    assert "Listing files" in headless_text_frames
    assert "done" in headless_text_frames
    # The combined TUI transcript carries the full streamed assistant text.
    tui_text = " ".join(tui_blocks)
    assert "Listing files" in tui_text
    assert "done" in tui_text

    # ----- The same tool activity is observable on both surfaces -----
    assert any(
        o["type"] == "assistant"
        and isinstance(o["message"]["content"], list)
        and any(b.get("type") == "tool_use" for b in o["message"]["content"])
        for o in headless_objs
    )
    assert any(o["type"] == "user" for o in headless_objs)  # tool_result
    # TUI committed a one-line summary for the tool events.
    assert any("Bash" in b or "tool" in b.lower() for b in tui_blocks)


class _FakeRegistry:
    def lookup(self, name: str):
        _ = name
        return None

    def list_for(self, surface: CommandSurface):
        _ = surface
        return []
