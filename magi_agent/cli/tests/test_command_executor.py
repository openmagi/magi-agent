"""Tests for the PR2.2 ``CommandExecutor`` contract + default executor.

Fully fake — no App, no model. Drives ``DefaultCommandExecutor.run`` against a
``RecordingApp`` that captures the app-facing effects each command KIND drives:

* ``PromptCommand`` -> ``ctx.app.start_turn(expanded_prompt)`` (re-enters the ONE
  turn loop; NEVER a second engine loop).
* ``LocalCommand`` -> ``Text`` commits text, ``Compact`` requests compaction,
  ``Skip`` is a no-op.
* ``WidgetCommand`` -> opens a dialog via ``ctx.app`` (no turn started).
"""

from __future__ import annotations

import asyncio

from magi_agent.cli.contracts import (
    CommandContext,
    CommandExecutor,
    CommandSurface,
    Compact,
    ContentBlock,
    LocalCommand,
    LocalResult,
    PromptCommand,
    Text,
    WidgetCommand,
)

TUI = CommandSurface(tui=True, headless=False)


class RecordingApp:
    """Captures the app-facing effects the executor drives."""

    def __init__(self) -> None:
        self.turns: list[str] = []
        self.texts: list[str] = []
        self.compacted = 0
        self.opened: list[str] = []

    # CommandContext app-opener seam:
    def start_turn(self, prompt: str) -> None:
        self.turns.append(prompt)

    def commit_text(self, text: str) -> None:
        self.texts.append(text)

    def request_compact(self) -> None:
        self.compacted += 1

    def open_dialog(self, name: str) -> None:
        self.opened.append(name)


def _ctx(app: RecordingApp) -> CommandContext:
    return CommandContext(cwd="/tmp", app=app)


class EchoPrompt(PromptCommand):
    async def build_prompt(self, args, ctx) -> list[ContentBlock]:  # type: ignore[override]
        return [ContentBlock(type="text", text=f"expanded:{args}")]


class SayLocal(LocalCommand):
    async def call(self, args, ctx) -> LocalResult:  # type: ignore[override]
        return Text(text=f"local:{args}")


class CompactLocal(LocalCommand):
    async def call(self, args, ctx) -> LocalResult:  # type: ignore[override]
        return Compact()


class PickerWidget(WidgetCommand):
    async def call(self, on_done, ctx, args):  # type: ignore[override]
        ctx.app.open_dialog(self.name)
        return None


def _default_executor() -> CommandExecutor:
    from magi_agent.cli.commands.executor import DefaultCommandExecutor

    return DefaultCommandExecutor()


def test_prompt_command_reenters_start_turn() -> None:
    async def _run() -> None:
        app = RecordingApp()
        ex = _default_executor()
        await ex.run(EchoPrompt(name="say", surface=TUI), "hi", _ctx(app))
        assert app.turns == ["expanded:hi"]
        assert app.compacted == 0  # no second engine loop

    asyncio.run(_run())


class EnqueueRecordingApp(RecordingApp):
    """A host app that ALSO exposes the busy-aware admission seam."""

    def __init__(self) -> None:
        super().__init__()
        self.enqueued: list[str] = []

    def start_or_enqueue_turn(self, prompt: str) -> None:
        self.enqueued.append(prompt)


def test_prompt_command_prefers_start_or_enqueue_when_present() -> None:
    async def _run() -> None:
        app = EnqueueRecordingApp()
        ex = _default_executor()
        await ex.run(EchoPrompt(name="say", surface=TUI), "hi", _ctx(app))
        # When the host exposes the seam, the executor routes through it (so a
        # prompt-command queues while busy) and does NOT call start_turn.
        assert app.enqueued == ["expanded:hi"]
        assert app.turns == []

    asyncio.run(_run())


def test_local_text_command_commits_text() -> None:
    async def _run() -> None:
        app = RecordingApp()
        ex = _default_executor()
        await ex.run(SayLocal(name="say", surface=TUI), "yo", _ctx(app))
        assert app.texts == ["local:yo"]

    asyncio.run(_run())


def test_local_compact_command_requests_compact() -> None:
    async def _run() -> None:
        app = RecordingApp()
        ex = _default_executor()
        await ex.run(CompactLocal(name="compact", surface=TUI), "", _ctx(app))
        assert app.compacted == 1

    asyncio.run(_run())


def test_widget_command_opens_dialog() -> None:
    async def _run() -> None:
        app = RecordingApp()
        ex = _default_executor()
        await ex.run(PickerWidget(name="model", surface=TUI), "", _ctx(app))
        assert app.opened == ["model"]

    asyncio.run(_run())


def test_executor_is_protocol() -> None:
    assert isinstance(_default_executor(), CommandExecutor)
