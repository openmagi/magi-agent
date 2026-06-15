"""PR4.2 — reasoning/thinking inline display (REDESIGNED).

The runtime surfaces model reasoning to the TUI as a ``status`` ``RuntimeEvent``
whose payload ``type`` is ``"thinking_delta"`` (engine maps ``thinking_delta`` →
``status`` via ``_map_event_kind``; the engine sanitizer only lets it through when
``MAGI_STREAM_THINKING`` is set, otherwise it is dropped upstream and never
reaches the TUI). The quiet-by-default ``_fold_event`` filter drops ALL ``status``
events unless ``MAGI_TUI_VERBOSE=1`` — so reasoning would be invisible.

This PR intercepts the reasoning-marked ``status`` events in ``_fold_event``
*before* the quiet drop and commits them as a DIM ONE-LINE inline block
(``● thinking  <preview>``), distinct in style from assistant text and tool
lines. Plumbing ``status`` events (runner_policy_*, phase_route_*, turn_end) stay
hidden by default. Search fidelity: the reasoning preview is in the committed
snapshot.

Style: this package has no ``pytest-asyncio``; async tests are SYNC functions
driving the coroutine via ``asyncio.run`` with a nested ``async def _run`` that
uses Textual's ``App.run_test()`` harness. The engine is ALWAYS mocked.
"""

from __future__ import annotations

import asyncio

from magi_agent.cli.contracts import (
    CommandSurface,
    ControlRequest,
    EngineResult,
    LocalCommand,
    PermissionDecision,
    PermissionGate,
    RuntimeEvent,
    Terminal,
    ToolRendererRegistry,
)
from magi_agent.cli.tui.app import MagiTuiApp
from magi_agent.cli.tui.tool_render import build_tool_renderers

_TUI = CommandSurface(tui=True, headless=False)


class _Reg:
    def __init__(self) -> None:
        self._c = [LocalCommand(name="compact", surface=_TUI)]

    def lookup(self, name):
        return next((c for c in self._c if c.name == name), None)

    def list_for(self, surface):
        _ = surface
        return list(self._c)


class _Allow(PermissionGate):
    async def check(self, req: ControlRequest) -> PermissionDecision:
        _ = req
        return PermissionDecision(kind="allow")


def _reasoning_event(text: str) -> RuntimeEvent:
    # Exactly what reaches the TUI: a status event whose inner type is
    # ``thinking_delta`` carrying the (already-sanitized) reasoning ``delta``.
    return RuntimeEvent(
        type="status",
        payload={"type": "thinking_delta", "delta": text},
        turn_id="t",
    )


def _plumbing_event() -> RuntimeEvent:
    # A representative plumbing status event that MUST stay hidden by default.
    return RuntimeEvent(
        type="status",
        payload={"type": "runner_policy_assembly", "phase": "executing"},
        turn_id="t",
    )


class _ThinkingEngine:
    def __init__(self, events: list[RuntimeEvent]) -> None:
        self._events = events

    async def run_turn_stream(self, runtime, turn_input, *, cancel, gate=None):
        _ = (runtime, cancel, gate)
        turn_id = getattr(turn_input, "turn_id", "t")
        for ev in self._events:
            yield RuntimeEvent(type=ev.type, payload=ev.payload, turn_id=turn_id)
        yield EngineResult(terminal=Terminal.completed, turn_id=turn_id)


def _make_app(events: list[RuntimeEvent], *, renderers=None) -> MagiTuiApp:
    return MagiTuiApp(
        engine=_ThinkingEngine(events),
        gate=_Allow(),
        commands=_Reg(),
        renderers=renderers if renderers is not None else ToolRendererRegistry(),
    )


def _bash_start(command: str) -> RuntimeEvent:
    return RuntimeEvent(
        type="tool",
        payload={"type": "tool_start", "id": "c1", "name": "Bash",
                 "input": {"command": command}},
        turn_id="t",
    )


def _bash_end(stdout: str) -> RuntimeEvent:
    return RuntimeEvent(
        type="tool",
        payload={
            "type": "tool_end",
            "id": "c1",
            "name": "Bash",
            "status": "ok",
            "output_preview": {"stdout": stdout},
        },
        turn_id="t",
    )


def test_reasoning_event_commits_a_dim_thinking_line_by_default() -> None:
    async def _run() -> None:
        app = _make_app([_reasoning_event("planning the edit")])
        async with app.run_test() as pilot:
            app.start_turn("go")
            await app.workers.wait_for_complete()
            await pilot.pause()
            blocks = app.controller.committed_blocks_snapshot()
            # A thinking line was committed BY DEFAULT (no MAGI_TUI_VERBOSE).
            thinking = [b for b in blocks if "thinking" in b]
            assert thinking, f"expected a thinking line, got {blocks!r}"
            # One-line: the preview text is on the same committed block.
            assert any("planning the edit" in b for b in thinking)

    asyncio.run(_run())


def test_plumbing_status_event_stays_hidden_by_default() -> None:
    async def _run() -> None:
        app = _make_app([_plumbing_event()])
        async with app.run_test() as pilot:
            app.start_turn("go")
            await app.workers.wait_for_complete()
            await pilot.pause()
            blocks = app.controller.committed_blocks_snapshot()
            # The plumbing status event produced no committed block.
            assert not any("runner_policy_assembly" in b for b in blocks), blocks

    asyncio.run(_run())


def test_reasoning_line_is_styled_dim_and_distinct() -> None:
    """The reasoning render is a DIM rich block (distinct from tool/assistant)."""

    from magi_agent.cli.tui.app import _render_thinking_node

    node = _render_thinking_node("considering the file layout")
    # The committed/search text carries the marker + preview (search fidelity).
    assert "thinking" in node.text
    assert "considering the file layout" in node.text
    # The rich renderable styles the whole line dim (distinct from the teal/blue
    # tool dot styles). Every styled span on the line is dim.
    rich = node.rich
    assert rich is not None
    styles = [str(span.style) for span in rich.spans]
    assert styles, "expected styled spans on the thinking line"
    assert all("dim" in s for s in styles), styles


def test_multiline_reasoning_is_truncated_to_a_short_preview() -> None:
    from magi_agent.cli.tui.app import _render_thinking_node

    long = "first line of reasoning\n" + "\n".join(f"line {i}" for i in range(40))
    node = _render_thinking_node(long)
    # Truncated to a terse preview: first line present, not the whole essay.
    assert "first line of reasoning" in node.text
    assert "line 39" not in node.text
    # One committed line (no embedded raw newline essay).
    assert node.text.count("\n") <= 1


def test_cjk_reasoning_preview_is_cell_bounded() -> None:
    """A long single-line Hangul reasoning preview is bounded by
    ``_THINKING_PREVIEW_MAX_CHARS`` in *cells*, not codepoints (which would be
    ~2x and overflow the one-line ``● thinking <preview>``)."""

    from magi_agent.cli.render.width import display_width
    from magi_agent.cli.tui.app import _THINKING_PREVIEW_MAX_CHARS, _render_thinking_node

    node = _render_thinking_node("가" * 150)
    # The preview text is the committed node text minus the "● thinking  " label
    # (strip the label's 2-space separator before measuring the preview itself).
    preview = node.text.split("thinking", 1)[-1].lstrip()
    assert display_width(preview) <= _THINKING_PREVIEW_MAX_CHARS
    assert node.text.endswith("…")


def test_streaming_reasoning_deltas_are_coalesced_into_one_block() -> None:
    """Several thinking deltas in a turn fold terse — not one line per token."""

    async def _run() -> None:
        app = _make_app(
            [
                _reasoning_event("step one"),
                _reasoning_event("step two"),
                _reasoning_event("step three"),
            ]
        )
        async with app.run_test() as pilot:
            app.start_turn("go")
            await app.workers.wait_for_complete()
            await pilot.pause()
            blocks = app.controller.committed_blocks_snapshot()
            thinking = [b for b in blocks if "thinking" in b]
            # Coalesced: the deltas fold into a single updating thinking block,
            # not three separate spammy lines.
            assert len(thinking) == 1, thinking
            # The latest delta is reflected in the coalesced preview.
            assert "step three" in thinking[0]

    asyncio.run(_run())


def test_interleaved_thinking_tool_thinking_keeps_one_thinking_block() -> None:
    """Index-stability invariant: a tool block committing BETWEEN two thinking
    deltas must NOT cause a duplicate thinking line, and the second thinking
    delta must patch the ORIGINAL thinking line in place — never overwrite the
    intervening tool block.

    This locks the append-only ``_committed`` index-stability invariant. If
    ``update_coalesced`` keyed off a stale/absolute index instead of the stored
    handle, the second delta ("b") would land on the TOOL block's slot — this
    test would then see the tool entry mutated into thinking text (failing the
    "$ echo hi"/stdout assertions) and/or a second thinking line.
    """

    async def _run() -> None:
        app = _make_app(
            [
                _reasoning_event("a"),
                _bash_start("echo hi"),
                _bash_end("hi\n"),
                _reasoning_event("b"),
            ],
            renderers=build_tool_renderers(),
        )
        async with app.run_test() as pilot:
            app.start_turn("go")
            await app.workers.wait_for_complete()
            await pilot.pause()
            blocks = app.controller.committed_blocks_snapshot()

            # Exactly ONE thinking block, coalesced to reflect the latest
            # reasoning "b" (the deltas folded into the single line, NOT a
            # duplicated second thinking line).
            thinking = [b for b in blocks if "thinking" in b]
            assert len(thinking) == 1, thinking
            assert "b" in thinking[0], thinking

            # The tool block is PRESENT and UNAFFECTED — the second thinking
            # delta did not overwrite the intervening tool entry.
            assert any("$ echo hi" in b for b in blocks), blocks
            assert any("hi" in b and "thinking" not in b for b in blocks), blocks
            # And the tool entry was never mutated into a thinking line.
            assert not any("thinking" in b and "echo hi" in b for b in blocks), blocks

    asyncio.run(_run())
