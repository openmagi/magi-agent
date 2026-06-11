"""PR4.1 — curated theme registration + ctrl+t cycle + persistence + picker.

Theme persistence reuses the SAME session root the rest of the TUI uses
(``session_log._session_root`` / ``MAGI_CLI_SESSION_DIR`` override), NOT a
separate ``MAGI_HOME`` env (which does not exist in this codebase). Tests isolate
the on-disk settings file by pointing ``MAGI_CLI_SESSION_DIR`` at a tmp dir, which
``conftest.restore_process_state`` reverts after each test.
"""

from __future__ import annotations

import asyncio

import pytest

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
from magi_agent.cli.tui.theme import (
    MAGI_THEMES,
    load_saved_theme,
    register_magi_themes,
)

TUI = CommandSurface(tui=True, headless=False)


@pytest.fixture(autouse=True)
def _isolated_session_dir(tmp_path, monkeypatch):
    """Point the settings/session root at a tmp dir for EVERY theme test.

    ``action_cycle_theme``/``select_theme`` persist the choice; without this the
    cycle tests would write to the real ``~/.magi/tui/settings.json``. conftest's
    ``restore_process_state`` reverts the env after each test regardless.
    """

    monkeypatch.setenv("MAGI_CLI_SESSION_DIR", str(tmp_path))


class _Registry:
    def __init__(self) -> None:
        self._commands = [LocalCommand(name="compact", surface=TUI)]

    def lookup(self, name: str):
        for c in self._commands:
            if getattr(c, "name", None) == name:
                return c
        return None

    def list_for(self, surface: CommandSurface):
        return list(self._commands)


class _Engine:
    async def run_turn_stream(self, runtime, turn_input, *, cancel, gate=None):
        yield RuntimeEvent(type="token", payload={"delta": "hi"}, turn_id="t")
        yield EngineResult(terminal=Terminal.completed, turn_id="t")


class _AllowGate(PermissionGate):
    async def check(self, req: ControlRequest) -> PermissionDecision:
        _ = req
        return PermissionDecision(kind="allow")


def _make_app() -> MagiTuiApp:
    return MagiTuiApp(
        engine=_Engine(),
        gate=_AllowGate(),
        commands=_Registry(),
        renderers=ToolRendererRegistry(),
    )


# -- Task 1: curated list + custom theme registration -----------------------
def test_magi_themes_list_is_curated_and_ordered() -> None:
    # ~6 curated names, tokyo-night first (the historical default), magi-dark last.
    assert MAGI_THEMES[0] == "tokyo-night"
    assert "magi-dark" in MAGI_THEMES
    for name in ("nord", "gruvbox", "dracula", "catppuccin-mocha", "monokai"):
        assert name in MAGI_THEMES
    assert 6 <= len(MAGI_THEMES) <= 8
    assert len(set(MAGI_THEMES)) == len(MAGI_THEMES)  # no dupes


def test_register_magi_themes_registers_custom_magi_dark() -> None:
    async def _run() -> None:
        app = _make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            register_magi_themes(app)
            # The custom theme is now resolvable and every curated name available.
            assert app.get_theme("magi-dark") is not None
            for name in MAGI_THEMES:
                assert name in app.available_themes

    asyncio.run(_run())


# -- Task 2: ctrl+t cycle + persistence -------------------------------------
def test_ctrl_t_cycles_theme_in_order() -> None:
    async def _run() -> None:
        app = _make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.theme = "tokyo-night"
            await pilot.press("ctrl+t")
            await pilot.pause()
            assert app.theme == "nord"  # next in MAGI_THEMES
            await pilot.press("ctrl+t")
            await pilot.pause()
            assert app.theme == "gruvbox"

    asyncio.run(_run())


def test_theme_choice_is_persisted_and_restored(tmp_path, monkeypatch) -> None:
    async def _run() -> None:
        monkeypatch.setenv("MAGI_CLI_SESSION_DIR", str(tmp_path))
        app = _make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.theme = "tokyo-night"
            app.action_cycle_theme()  # tokyo-night -> nord
            await pilot.pause()
            assert app.theme == "nord"
        assert load_saved_theme() == "nord"  # written to disk
        # A fresh app restores the saved theme from disk.
        app2 = _make_app()
        async with app2.run_test() as pilot:
            await pilot.pause()
            assert app2.theme == "nord"

    asyncio.run(_run())


def test_default_theme_when_nothing_persisted() -> None:
    async def _run() -> None:
        app = _make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.theme == "tokyo-night"

    asyncio.run(_run())


# -- Task 3: palette theme picker -------------------------------------------
def test_theme_picker_sets_and_persists_theme(tmp_path, monkeypatch) -> None:
    async def _run() -> None:
        monkeypatch.setenv("MAGI_CLI_SESSION_DIR", str(tmp_path))
        app = _make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            # The app exposes a select-theme entrypoint the ThemeProvider calls.
            app.select_theme("dracula")
            await pilot.pause()
            assert app.theme == "dracula"
        app2 = _make_app()
        async with app2.run_test() as pilot:
            await pilot.pause()
            assert app2.theme == "dracula"

    asyncio.run(_run())


def test_select_theme_ignores_unknown_name() -> None:
    async def _run() -> None:
        app = _make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.theme = "nord"
            app.select_theme("not-a-real-theme")
            await pilot.pause()
            assert app.theme == "nord"  # unchanged

    asyncio.run(_run())


def test_theme_provider_registered_on_commands() -> None:
    from magi_agent.cli.tui.palette import ThemeProvider

    assert ThemeProvider in MagiTuiApp.COMMANDS


def test_app_uses_terminal_ansi_colors() -> None:
    """The app must run with ``ansi_color=True``.

    ``Screen { background: transparent }`` alone only reveals the App-level
    theme background (a solid #1a1b26 under tokyo-night), NOT the terminal's
    own background. ``ansi_color=True`` makes Textual emit ANSI default
    colors so the user's terminal background actually shows through.
    """

    app = _make_app()
    assert app.ansi_color is True


def test_theme_switch_keeps_regions_transparent() -> None:
    """A theme switch must NOT repaint Screen/transcript with a solid bg.

    Kevin's flat-look revision hardcodes ``background: transparent`` on the
    Screen + transcript/live/prompt regions. Switching ``App.theme`` retints
    accent/text/primary but the regions stay flat — assert their resolved
    background carries no opaque paint after a cycle.
    """

    from textual.color import TRANSPARENT

    async def _run() -> None:
        app = _make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            # Cycle to a fully different palette (e.g. light-ish monokai/gruvbox).
            for _ in range(len(MAGI_THEMES)):
                app.action_cycle_theme()
                await pilot.pause()
                screen_bg = app.screen.styles.background
                transcript = app.query_one("#transcript")
                trans_bg = transcript.styles.background
                # Background stays transparent (alpha 0) — no solid theme paint.
                assert screen_bg == TRANSPARENT, (app.theme, screen_bg)
                assert trans_bg == TRANSPARENT, (app.theme, trans_bg)

    asyncio.run(_run())
