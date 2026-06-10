"""PR4.4 — which-key chord-hint overlay."""

from __future__ import annotations

import asyncio

from textual.app import App, ComposeResult

from magi_agent.cli.keybindings.defaults import default_bindings
from magi_agent.cli.keybindings.schema import Context, parse_keystroke
from magi_agent.cli.tui.widgets.whichkey import WhichKeyOverlay, chord_continuations


# ---------------------------------------------------------------------------
# Pure seam: chord_continuations
# ---------------------------------------------------------------------------
def test_chord_continuations_lists_next_keys_after_prefix() -> None:
    bindings = default_bindings()  # contains the chord ctrl+x ctrl+k -> killAgents
    pending = (parse_keystroke("ctrl+x"),)
    hints = chord_continuations(pending, [Context.CHAT, Context.GLOBAL], bindings)
    # The continuation 'ctrl+k' -> 'chat:killAgents' is offered.
    keys = [k for k, _ in hints]
    actions = [a for _, a in hints]
    assert any("k" in k for k in keys)
    assert "chat:killAgents" in actions


def test_chord_continuations_empty_when_no_pending() -> None:
    bindings = default_bindings()
    assert chord_continuations(None, [Context.CHAT], bindings) == []
    assert chord_continuations((), [Context.CHAT], bindings) == []


def test_chord_continuations_filters_by_active_context() -> None:
    bindings = default_bindings()
    pending = (parse_keystroke("ctrl+x"),)
    # The killAgents chord lives in the Chat context; with only Global active it
    # is not offered.
    hints = chord_continuations(pending, [Context.GLOBAL], bindings)
    assert hints == []


# ---------------------------------------------------------------------------
# Widget rendering: WhichKeyOverlay
# ---------------------------------------------------------------------------
class _Harness(App[None]):
    def compose(self) -> ComposeResult:
        yield WhichKeyOverlay(id="whichkey")


def test_overlay_show_hide_toggles_visibility_class() -> None:
    async def _run() -> None:
        app = _Harness()
        async with app.run_test() as pilot:
            await pilot.pause()
            overlay = app.query_one(WhichKeyOverlay)
            assert "visible" not in overlay.classes
            overlay.show_hints([("ctrl+k", "chat:killAgents")])
            assert "visible" in overlay.classes
            overlay.hide_hints()
            assert "visible" not in overlay.classes

    asyncio.run(_run())


def test_overlay_show_empty_hints_stays_hidden() -> None:
    async def _run() -> None:
        app = _Harness()
        async with app.run_test() as pilot:
            await pilot.pause()
            overlay = app.query_one(WhichKeyOverlay)
            overlay.show_hints([])
            assert "visible" not in overlay.classes

    asyncio.run(_run())
