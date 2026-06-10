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
# Fakes for driving the real MagiTuiApp keybinding path
# ---------------------------------------------------------------------------
from magi_agent.cli.tui.app import MagiTuiApp  # noqa: E402


class _FakeEngine:
    async def run_turn_stream(self, *a, **k):  # pragma: no cover - not driven here
        if False:
            yield None
        return


class _FakeGate:
    async def evaluate(self, *a, **k):  # pragma: no cover
        return None


class _FakeRenderers:
    def get(self, name):  # pragma: no cover
        return None


def _make_app(registry: FakeRegistry) -> MagiTuiApp:
    return MagiTuiApp(
        engine=_FakeEngine(),
        gate=_FakeGate(),
        commands=registry,
        renderers=_FakeRenderers(),
    )


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
            app.input.text = "hello world"
            app.input.cursor_location = (0, len("hello world"))
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
            app.input.text = "/compact now"
            app.input.cursor_location = (0, len("/compact now"))
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
            app.input.text = "/comp"
            app.input.cursor_location = (0, len("/comp"))
            # Cursor at end after assignment+pause.
            await pilot.pause()
            assert app.input.precursor.startswith("/comp")

    asyncio.run(_run())


def test_precursor_spans_multiple_lines() -> None:
    async def _run() -> None:
        app = _InputHostApp(FakeRegistry([]))
        async with app.run_test() as pilot:
            app.input.focus()
            await pilot.pause()
            app.input.text = "first line\nsecond /comp"
            # Caret at end of row 1, col == len("second /comp").
            app.input.cursor_location = (1, len("second /comp"))
            await pilot.pause()
            assert app.input.precursor == "first line\nsecond /comp"

    asyncio.run(_run())


def test_precursor_mid_line_on_second_row() -> None:
    async def _run() -> None:
        app = _InputHostApp(FakeRegistry([]))
        async with app.run_test() as pilot:
            app.input.focus()
            await pilot.pause()
            app.input.text = "alpha\nbeta gamma"
            app.input.cursor_location = (1, 4)  # after "beta"
            await pilot.pause()
            assert app.input.precursor == "alpha\nbeta"

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Multiline submit / newline behavior (real MagiTuiApp keybinding path)
# ---------------------------------------------------------------------------
def test_shift_enter_inserts_newline_not_submit() -> None:
    async def _run() -> None:
        app = _make_app(FakeRegistry(["compact"]))
        async with app.run_test() as pilot:
            app._input.focus()
            await pilot.pause()
            app._input.text = "line one"
            app._input.cursor_location = (0, len("line one"))
            await pilot.press("shift+enter")
            await pilot.pause()
            assert "\n" in app._input.text
            assert app.last_terminal is None  # did NOT submit

    asyncio.run(_run())


def test_enter_submits_whole_multiline_buffer() -> None:
    async def _run() -> None:
        app = _make_app(FakeRegistry(["compact"]))
        async with app.run_test() as pilot:
            app._input.focus()
            await pilot.pause()
            app._input.text = "first\nsecond"
            app._input.cursor_location = (1, len("second"))
            await pilot.press("enter")
            await pilot.pause()
            # buffer cleared on submit
            assert app._input.text == ""

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Autocomplete routes off the multiline precursor (regression guard)
# ---------------------------------------------------------------------------
from magi_agent.cli.tui.autocomplete import AutocompleteRouter  # noqa: E402


def test_router_routes_off_multiline_precursor() -> None:
    async def _run() -> None:
        app = _InputHostApp(FakeRegistry(["compact", "clear"]))
        async with app.run_test() as pilot:
            app.input.focus()
            await pilot.pause()
            app.input.text = "do the thing\n/comp"
            app.input.cursor_location = (1, len("/comp"))
            await pilot.pause()
            router = AutocompleteRouter(commands=app._command_registry)
            request = router.route(app.input.precursor)
            assert request.trigger == "/"
            assert any(c.value == "/compact" for c in request.results)

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# ↑/↓ history recall at buffer edges (PR1.2 t5)
# ---------------------------------------------------------------------------
from magi_agent.cli.tui.history import InputHistory  # noqa: E402


def test_up_on_first_row_recalls_history() -> None:
    async def _run() -> None:
        app = _InputHostApp(FakeRegistry([]))
        async with app.run_test() as pilot:
            app.input.focus()
            history = InputHistory(session_id="s", path=None)
            history.add("earlier prompt")
            app.input.attach_history(history)
            await pilot.pause()
            app.input.text = ""
            app.input.cursor_location = (0, 0)
            await pilot.press("up")
            await pilot.pause()
            assert app.input.text == "earlier prompt"

    asyncio.run(_run())


def test_up_mid_multiline_moves_caret_not_history() -> None:
    async def _run() -> None:
        app = _InputHostApp(FakeRegistry([]))
        async with app.run_test() as pilot:
            app.input.focus()
            history = InputHistory(session_id="s", path=None)
            history.add("earlier prompt")
            app.input.attach_history(history)
            await pilot.pause()
            app.input.text = "row0\nrow1"
            app.input.cursor_location = (1, 2)  # on second row
            await pilot.press("up")
            await pilot.pause()
            # caret moved up a row; text untouched
            assert app.input.text == "row0\nrow1"
            assert app.input.cursor_location[0] == 0

    asyncio.run(_run())


def test_down_on_last_row_walks_history_forward() -> None:
    async def _run() -> None:
        app = _InputHostApp(FakeRegistry([]))
        async with app.run_test() as pilot:
            app.input.focus()
            history = InputHistory(session_id="s", path=None)
            history.add("older")
            history.add("newer")
            app.input.attach_history(history)
            await pilot.pause()
            app.input.text = ""
            app.input.cursor_location = (0, 0)
            # walk back twice -> "older"
            await pilot.press("up")
            await pilot.pause()
            await pilot.press("up")
            await pilot.pause()
            assert app.input.text == "older"
            # down on the (single) last row walks forward -> "newer"
            await pilot.press("down")
            await pilot.pause()
            assert app.input.text == "newer"

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Ctrl+V posts AttachImageRequested (Task 3)
# ---------------------------------------------------------------------------


class _AttachHostApp(App[None]):
    """Minimal host that captures AttachImageRequested messages."""

    def __init__(self, registry: FakeRegistry) -> None:
        super().__init__()
        self._command_registry = registry
        self.attach_requests: list[PromptInput.AttachImageRequested] = []
        self.input: PromptInput | None = None

    def compose(self) -> ComposeResult:
        self.input = PromptInput(commands=self._command_registry, id="prompt")
        yield self.input

    def on_prompt_input_attach_image_requested(
        self, event: "PromptInput.AttachImageRequested"
    ) -> None:
        self.attach_requests.append(event)


def test_ctrl_v_posts_attach_image_requested() -> None:
    """AttachImageRequested is a Message subclass on PromptInput."""
    from textual.message import Message

    from magi_agent.cli.tui.input import PromptInput

    assert hasattr(PromptInput, "AttachImageRequested")
    assert issubclass(PromptInput.AttachImageRequested, Message)


def test_ctrl_v_behavioral_posts_attach_image_requested() -> None:
    """Pressing ctrl+v posts exactly one AttachImageRequested and stops the event."""

    async def _run() -> None:
        app = _AttachHostApp(FakeRegistry([]))
        async with app.run_test() as pilot:
            app.input.focus()
            await pilot.pause()
            app.input.text = ""
            await pilot.press("ctrl+v")
            await pilot.pause()
        assert len(app.attach_requests) == 1
        # Buffer must remain empty (default paste must not fire)
        assert app.input.text == ""

    asyncio.run(_run())
