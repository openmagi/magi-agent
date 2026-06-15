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


def test_show_hints_renders_friendly_label() -> None:
    """The overlay shows a human label for a known action, not the raw id."""

    async def _run() -> None:
        app = _Harness()
        async with app.run_test() as pilot:
            await pilot.pause()
            overlay = app.query_one(WhichKeyOverlay)
            overlay.show_hints([("ctrl+k", "chat:killAgents")])
            rendered = str(overlay.render())
            assert "Stop agents" in rendered
            assert "chat:killAgents" not in rendered

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# App wiring: overlay shows on a pending chord, hides on resolve/cancel
# ---------------------------------------------------------------------------
from magi_agent.cli.contracts import (  # noqa: E402
    CommandSurface,
    ControlRequest,
    EngineResult,
    LocalCommand,
    PermissionDecision,
    PermissionGate,
    Terminal,
    ToolRendererRegistry,
)
from magi_agent.cli.tui.app import MagiTuiApp  # noqa: E402

_TUI = CommandSurface(tui=True, headless=False)


class _Reg:
    def __init__(self):
        self._c = [LocalCommand(name="compact", surface=_TUI)]

    def lookup(self, name):
        return next((c for c in self._c if c.name == name), None)

    def list_for(self, surface):
        return list(self._c)


class _Engine:
    async def run_turn_stream(self, runtime, turn_input, *, cancel, gate=None):
        yield EngineResult(terminal=Terminal.completed, turn_id="t")


class _Allow(PermissionGate):
    async def check(self, req: ControlRequest) -> PermissionDecision:
        _ = req
        return PermissionDecision(kind="allow")


def _make_chord_app() -> MagiTuiApp:
    return MagiTuiApp(
        engine=_Engine(),
        gate=_Allow(),
        commands=_Reg(),
        renderers=ToolRendererRegistry(),
    )


def test_chord_start_shows_whichkey_then_hides_on_complete() -> None:
    async def _run() -> None:
        app = _make_chord_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            overlay = app.query_one(WhichKeyOverlay)
            # First key of the ctrl+x ctrl+k chord -> overlay visible with a hint.
            await pilot.press("ctrl+x")
            await pilot.pause()
            assert "visible" in overlay.classes
            # The raw action id is humanized for display.
            assert "Stop agents" in str(overlay.render())
            # Completing the chord hides the overlay.
            await pilot.press("ctrl+k")
            await pilot.pause()
            assert "visible" not in overlay.classes

    asyncio.run(_run())


def test_chord_cancel_hides_whichkey() -> None:
    async def _run() -> None:
        app = _make_chord_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            overlay = app.query_one(WhichKeyOverlay)
            await pilot.press("ctrl+x")
            await pilot.pause()
            assert "visible" in overlay.classes
            # Escape cancels the pending chord -> overlay hides.
            await pilot.press("escape")
            await pilot.pause()
            assert "visible" not in overlay.classes
            assert app._pending is None

    asyncio.run(_run())


def test_start_turn_clears_dangling_chord_and_hides_whichkey() -> None:
    """A turn starting mid-chord must not leave the which-key overlay stuck.

    If the user presses a chord prefix (overlay shown) and a turn then begins
    (e.g. a submit), ``start_turn`` clears ``_pending`` and hides the overlay.
    """

    async def _run() -> None:
        app = _make_chord_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            overlay = app.query_one(WhichKeyOverlay)
            await pilot.press("ctrl+x")  # chord prefix -> overlay visible
            await pilot.pause()
            assert "visible" in overlay.classes
            assert app._pending is not None

            app.start_turn("do it")  # a turn begins mid-chord
            await pilot.pause()
            assert app._pending is None
            assert "visible" not in overlay.classes
            await app.workers.wait_for_complete()

    asyncio.run(_run())
