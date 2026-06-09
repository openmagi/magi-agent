from __future__ import annotations

import asyncio

from magi_agent.cli.contracts import (
    Command,
    CommandSurface,
    EngineResult,
    LocalCommand,
    PermissionDecision,
    PermissionGate,
    RuntimeEvent,
    Terminal,
    ToolRendererRegistry,
)
from magi_agent.cli.tui.app import MagiTuiApp

TUI = CommandSurface(tui=True, headless=False)


class FakeRegistry:
    def __init__(self, names: list[str]) -> None:
        self._commands: list[Command] = [
            LocalCommand(name=n, surface=TUI) for n in names
        ]

    def lookup(self, name: str) -> Command | None:
        for c in self._commands:
            if getattr(c, "name", None) == name:
                return c
        return None

    def list_for(self, surface: CommandSurface) -> list[Command]:
        _ = surface
        return list(self._commands)


class FakeEngine:
    async def run_turn_stream(self, runtime, turn_input, *, cancel, gate=None):
        yield RuntimeEvent(type="token", payload={"delta": "ok"}, turn_id="t")
        yield EngineResult(terminal=Terminal.completed, turn_id="t")


class AllowGate(PermissionGate):
    async def check(self, req) -> PermissionDecision:
        return PermissionDecision(kind="allow")


def _app(commands) -> MagiTuiApp:
    return MagiTuiApp(
        engine=FakeEngine(),
        gate=AllowGate(),
        commands=commands,
        renderers=ToolRendererRegistry(),
    )


def test_palette_provider_lists_slash_commands() -> None:
    async def _run() -> None:
        registry = FakeRegistry(["compact", "status"])
        app = _app(registry)
        async with app.run_test() as pilot:
            await pilot.pause()
            from magi_agent.cli.tui.palette import CommandPaletteProvider

            provider = CommandPaletteProvider(app.screen)
            provider._app_ref = app  # provider reads commands off the app
            hits = [h async for h in provider.search("compact")]
        labels = [str(getattr(h, "text", "") or h.match_display) for h in hits]
        assert any("/compact" in lbl for lbl in labels)

    asyncio.run(_run())


def test_palette_discover_lists_all_slash_commands() -> None:
    async def _run() -> None:
        registry = FakeRegistry(["compact", "status", "help"])
        app = _app(registry)
        async with app.run_test() as pilot:
            await pilot.pause()
            from magi_agent.cli.tui.palette import CommandPaletteProvider

            provider = CommandPaletteProvider(app.screen)
            provider._app_ref = app
            hits = [h async for h in provider.discover()]
        texts = [getattr(h, "text", "") or "" for h in hits]
        assert "/compact" in texts and "/status" in texts and "/help" in texts

    asyncio.run(_run())


def test_app_registers_command_palette_provider() -> None:
    async def _run() -> None:
        app = _app(FakeRegistry(["compact"]))
        from magi_agent.cli.tui.palette import CommandPaletteProvider

        assert CommandPaletteProvider in app.COMMANDS
        assert app.COMMAND_PALETTE_BINDING == "ctrl+p"

    asyncio.run(_run())


def test_palette_runner_routes_through_submit_command() -> None:
    async def _run() -> None:
        calls: list[tuple[str, str]] = []
        app = _app(FakeRegistry(["compact"]))
        async with app.run_test() as pilot:
            await pilot.pause()
            app.submit_command = lambda name, args="": calls.append((name, args))  # type: ignore[assignment]
            from magi_agent.cli.tui.palette import CommandPaletteProvider

            provider = CommandPaletteProvider(app.screen)
            provider._app_ref = app
            hits = [h async for h in provider.discover()]
            chosen = next(h for h in hits if (h.text or "") == "/compact")
            chosen.command()  # invoke the hit's callback
        assert ("compact", "") in calls

    asyncio.run(_run())
