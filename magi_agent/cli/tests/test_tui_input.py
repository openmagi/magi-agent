"""Tests for the PR-E2 prompt input + submission routing.

``classify_line`` is pure logic; the widget behavior is exercised via Textual's
``App.run_test()`` harness with a tiny host app. We inject a FAKE
``CommandRegistry``.
"""

from __future__ import annotations

import asyncio

from textual.app import App, ComposeResult

from magi_agent.cli.contracts import (
    Command,
    CommandSurface,
    LocalCommand,
)
from magi_agent.cli.tui.input import PromptInput, Submission, classify_line

TUI = CommandSurface(tui=True, headless=False)


class FakeRegistry:
    def __init__(self, names: list[str]) -> None:
        self._commands: list[Command] = [
            LocalCommand(name=name, surface=TUI) for name in names
        ]

    def lookup(self, name: str) -> Command | None:
        for command in self._commands:
            if getattr(command, "name", None) == name:
                return command
        return None

    def list_for(self, surface: CommandSurface) -> list[Command]:
        _ = surface
        return list(self._commands)


# ---------------------------------------------------------------------------
# classify_line (pure)
# ---------------------------------------------------------------------------
def test_slash_line_classified_as_command() -> None:
    registry = FakeRegistry(["compact"])
    sub = classify_line("/compact extra args", registry)
    assert sub.kind == "command"
    assert sub.command_name == "compact"
    assert sub.args == "extra args"
    assert sub.command is not None


def test_unknown_slash_command_has_none_lookup() -> None:
    registry = FakeRegistry(["compact"])
    sub = classify_line("/nope", registry)
    assert sub.kind == "command"
    assert sub.command_name == "nope"
    assert sub.command is None


def test_plain_line_classified_as_prompt() -> None:
    registry = FakeRegistry(["compact"])
    sub = classify_line("hello there", registry)
    assert sub.kind == "prompt"
    assert sub.text == "hello there"


# ---------------------------------------------------------------------------
# Widget submission routing via run_test()
# ---------------------------------------------------------------------------
class _InputHostApp(App[None]):
    def __init__(self, registry: FakeRegistry) -> None:
        super().__init__()
        self._command_registry = registry
        self.submissions: list[Submission] = []
        self.input: PromptInput | None = None

    def compose(self) -> ComposeResult:
        self.input = PromptInput(commands=self._command_registry, id="prompt")
        yield self.input

    def on_prompt_input_prompt_submitted(
        self, event: PromptInput.PromptSubmitted
    ) -> None:
        self.submissions.append(event.submission)


def test_widget_submits_prompt() -> None:
    async def _run() -> None:
        app = _InputHostApp(FakeRegistry(["compact"]))
        async with app.run_test() as pilot:
            app.input.focus()
            await pilot.pause()
            app.input.value = "hello world"
            await pilot.press("enter")
            await pilot.pause()
        assert len(app.submissions) == 1
        assert app.submissions[0].kind == "prompt"
        assert app.submissions[0].text == "hello world"

    asyncio.run(_run())


def test_widget_submits_command() -> None:
    async def _run() -> None:
        app = _InputHostApp(FakeRegistry(["compact"]))
        async with app.run_test() as pilot:
            app.input.focus()
            await pilot.pause()
            app.input.value = "/compact now"
            await pilot.press("enter")
            await pilot.pause()
        assert len(app.submissions) == 1
        sub = app.submissions[0]
        assert sub.kind == "command"
        assert sub.command_name == "compact"
        assert sub.command is not None

    asyncio.run(_run())


def test_precursor_reflects_cursor() -> None:
    async def _run() -> None:
        app = _InputHostApp(FakeRegistry([]))
        async with app.run_test() as pilot:
            app.input.focus()
            await pilot.pause()
            app.input.value = "/comp"
            # Cursor at end after assignment+pause.
            await pilot.pause()
            assert app.input.precursor.startswith("/comp")

    asyncio.run(_run())
