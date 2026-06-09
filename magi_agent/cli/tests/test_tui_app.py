"""Tests for the PR-E2 Textual App + REPL loop + TextualSink.

Style: this package has no ``pytest-asyncio``; async tests are SYNC functions
driving the coroutine via ``asyncio.run`` with a nested ``async def _run`` that
uses Textual's ``App.run_test()`` harness. The engine is ALWAYS mocked — no model
is ever hit.

The mock ``FakeEngineDriver`` yields a couple of ``RuntimeEvent``s then the
terminal ``EngineResult`` as its FINAL yielded item (the contract convention).
"""

from __future__ import annotations

import asyncio

from magi_agent.cli.contracts import (
    Command,
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
from magi_agent.cli.tui.app import MagiTuiApp, TextualSink, ToolUseConfirm

TUI = CommandSurface(tui=True, headless=False)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
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


class FakeEngineDriver:
    """Yields scripted RuntimeEvents then a terminal EngineResult.

    Optionally calls the injected ``gate`` (so the modal flow is exercised) and
    honors the ``cancel`` event (so the cancel path is exercised).
    """

    def __init__(
        self,
        *,
        tokens: list[str] | None = None,
        terminal: Terminal = Terminal.completed,
        ask_tool: str | None = None,
    ) -> None:
        self._tokens = tokens if tokens is not None else ["Hello", " world"]
        self._terminal = terminal
        self._ask_tool = ask_tool
        self.gate_decision: PermissionDecision | None = None
        self.cancelled = False

    async def run_turn_stream(self, runtime, turn_input, *, cancel, gate=None):
        turn_id = getattr(turn_input, "turn_id", "t")
        for tok in self._tokens:
            if cancel.is_set():
                self.cancelled = True
                yield EngineResult(
                    terminal=Terminal.aborted, error="cancelled", turn_id=turn_id
                )
                return
            yield RuntimeEvent(
                type="token", payload={"delta": tok}, turn_id=turn_id
            )
        if self._ask_tool is not None and gate is not None:
            req = ControlRequest(
                requestId="req-1",
                turnId=turn_id,
                toolName=self._ask_tool,
                arguments={"path": "x"},
                reason="needs approval",
            )
            self.gate_decision = await gate.check(req)
            yield RuntimeEvent(
                type="tool",
                payload={"type": "tool_end", "name": self._ask_tool},
                turn_id=turn_id,
            )
        yield EngineResult(terminal=self._terminal, turn_id=turn_id)


class AllowGate(PermissionGate):
    async def check(self, req: ControlRequest) -> PermissionDecision:
        _ = req
        return PermissionDecision(kind="allow")


class SinkGate(PermissionGate):
    """A gate whose ``check`` delegates straight to a sink (modal end-to-end)."""

    def __init__(self, sink) -> None:
        self._sink = sink

    async def check(self, req: ControlRequest) -> PermissionDecision:
        return await self._sink.ask(req)


def _make_app(engine, gate=None, commands=None, flush_interval=None) -> MagiTuiApp:
    kwargs = {} if flush_interval is None else {"flush_interval": flush_interval}
    return MagiTuiApp(
        engine=engine,
        gate=gate if gate is not None else AllowGate(),
        commands=commands if commands is not None else FakeRegistry(["compact"]),
        renderers=ToolRendererRegistry(),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# 0. Bare TUI should not open as a blank transcript
# ---------------------------------------------------------------------------
def test_tui_mount_renders_welcome_state() -> None:
    async def _run() -> None:
        engine = FakeEngineDriver()
        app = _make_app(engine)
        async with app.run_test() as pilot:
            await pilot.pause()
        blocks = app.controller.committed_blocks_snapshot()
        joined = "\n".join(blocks)
        assert "Welcome to Magi" in joined
        assert "/compact" in joined
        # Phase 1 keys advertised in the welcome banner (discoverability).
        assert "Shift+Enter" in joined
        assert "history" in joined
        assert "Ctrl+S" in joined
        # Phase 2 doors advertised too: command palette + help.
        assert "Ctrl+P" in joined
        assert "F1" in joined
        assert app.last_terminal is None

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 1. Happy turn: prompt -> transcript updates from the mocked engine stream
# ---------------------------------------------------------------------------
def test_prompt_drives_engine_and_updates_transcript() -> None:
    async def _run() -> None:
        engine = FakeEngineDriver(tokens=["Hello", " world"])
        app = _make_app(engine)
        async with app.run_test() as pilot:
            app.start_turn("hi")
            await app.workers.wait_for_complete()
            await pilot.pause()
        assert app.last_terminal is not None
        assert app.last_terminal.terminal == Terminal.completed
        # The streamed assistant text was committed as one finalized block.
        blocks = app.controller.committed_blocks_snapshot()
        assert any("Hello world" in b for b in blocks)

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 1a. The finalized assistant block is committed as a Rich Markdown renderable
#     (PR0.1) while the search-fidelity snapshot keeps the plain text.
# ---------------------------------------------------------------------------
def test_finalized_assistant_block_is_markdown_renderable() -> None:
    async def _run() -> None:
        engine = FakeEngineDriver(tokens=["# Heading\n\n", "body **bold**"])
        app = _make_app(engine)
        async with app.run_test() as pilot:
            app.start_turn("hi")
            await app.workers.wait_for_complete()
            await pilot.pause()
        # The committed snapshot keeps the plain text (search fidelity).
        blocks = app.controller.committed_blocks_snapshot()
        assert any("# Heading" in b and "body **bold**" in b for b in blocks)
        # The last committed renderable is a Rich Markdown, not a plain str.
        from rich.markdown import Markdown as RichMarkdown

        assert isinstance(app._last_committed_renderable, RichMarkdown)

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 1b. The coalescing flush timer repaints buffered token deltas WITHOUT an
#     explicit flush_now() — proves token streams render incrementally.
# ---------------------------------------------------------------------------
def test_flush_timer_repaints_buffered_deltas_without_explicit_flush() -> None:
    async def _run() -> None:
        engine = FakeEngineDriver()
        # A small interval so the test can advance time deterministically.
        app = _make_app(engine, flush_interval=0.01)
        async with app.run_test() as pilot:
            controller = app.controller
            controller.begin_live()
            controller.append_delta("streamed text")
            # No flush_now(): only the interval timer can render this.
            assert controller.live_render_count == 0
            # Advance past the interval so the timer fires at least once.
            await pilot.pause(0.05)
        assert controller.live_render_count >= 1

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 2. Tool ask raises the modal; APPROVE resolves the turn
# ---------------------------------------------------------------------------
def test_tool_ask_raises_modal_and_approve_resolves() -> None:
    async def _run() -> None:
        engine = FakeEngineDriver(tokens=["working"], ask_tool="Bash")
        app = _make_app(engine, gate=None)  # placeholder; replaced below
        # Use a gate that delegates to the app's TextualSink so the modal shows.
        async with app.run_test() as pilot:
            app._gate = SinkGate(app.sink)
            app.start_turn("run something")
            # Let the turn reach the ask and push the modal.
            await pilot.pause()
            await pilot.pause()
            # The modal should now be on the screen stack.
            assert isinstance(app.screen, ToolUseConfirm)
            await pilot.click("#allow")
            await app.workers.wait_for_complete()
            await pilot.pause()
        assert engine.gate_decision is not None
        assert engine.gate_decision.kind == "allow"
        assert app.last_terminal.terminal == Terminal.completed

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 3. Modal REJECT maps to a deny decision
# ---------------------------------------------------------------------------
def test_tool_ask_reject_maps_to_deny() -> None:
    async def _run() -> None:
        engine = FakeEngineDriver(tokens=["working"], ask_tool="Bash")
        app = _make_app(engine)
        async with app.run_test() as pilot:
            app._gate = SinkGate(app.sink)
            app.start_turn("run something")
            await pilot.pause()
            await pilot.pause()
            assert isinstance(app.screen, ToolUseConfirm)
            await pilot.click("#deny")
            await app.workers.wait_for_complete()
            await pilot.pause()
        assert engine.gate_decision is not None
        assert engine.gate_decision.kind == "deny"

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 4. Allow + remember produces a remember-rule update
# ---------------------------------------------------------------------------
def test_tool_ask_allow_remember_produces_update() -> None:
    async def _run() -> None:
        engine = FakeEngineDriver(tokens=["working"], ask_tool="Bash")
        app = _make_app(engine)
        async with app.run_test() as pilot:
            app._gate = SinkGate(app.sink)
            app.start_turn("run something")
            await pilot.pause()
            await pilot.pause()
            assert isinstance(app.screen, ToolUseConfirm)
            await pilot.click("#allow-remember")
            await app.workers.wait_for_complete()
            await pilot.pause()
        decision = engine.gate_decision
        assert decision is not None
        assert decision.kind == "allow"
        assert len(decision.updates) == 1
        assert decision.updates[0].tool == "Bash"
        assert decision.updates[0].decision == "allow"

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 5. Cancel interrupts a turn -> aborted terminal
# ---------------------------------------------------------------------------
def test_cancel_interrupts_turn() -> None:
    async def _run() -> None:
        # A long token stream; we set cancel before it drains.
        engine = FakeEngineDriver(tokens=[f"t{i}" for i in range(100)])
        app = _make_app(engine)
        async with app.run_test() as pilot:
            app.start_turn("long task")
            # Immediately request cancel; the engine races the shared event.
            app.action_cancel_turn()
            await app.workers.wait_for_complete()
            await pilot.pause()
        assert engine.cancelled is True
        assert app.last_terminal is not None
        assert app.last_terminal.terminal == Terminal.aborted

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 5b. Ctrl+C: cancels an in-flight turn, quits the app when idle
# ---------------------------------------------------------------------------
def test_ctrl_c_cancels_when_running_quits_when_idle() -> None:
    async def _run() -> None:
        engine = FakeEngineDriver(tokens=["t"])
        app = _make_app(engine)
        async with app.run_test() as pilot:
            exits: list[bool] = []
            app.exit = lambda *a, **k: exits.append(True)  # type: ignore[method-assign]

            # Running turn -> cancel (no exit).
            app._turn_active = True
            app._cancel = asyncio.Event()
            app.action_cancel_turn()
            assert app._cancel.is_set()
            assert exits == []

            # Idle -> exit.
            app._turn_active = False
            app.action_cancel_turn()
            assert exits == [True]
            await pilot.pause()

    asyncio.run(_run())


def test_ctrl_c_cancels_replacement_turn_after_stale_worker_finishes() -> None:
    async def _run() -> None:
        class ReplacementTurnDriver:
            def __init__(self) -> None:
                self.calls = 0
                self.second_entered = asyncio.Event()
                self.second_cancelled = False

            async def run_turn_stream(self, runtime, turn_input, *, cancel, gate=None):
                _ = runtime, gate
                self.calls += 1
                if self.calls == 1:
                    yield RuntimeEvent(
                        type="token",
                        payload={"delta": "first"},
                        turn_id=turn_input.turn_id,
                    )
                    await asyncio.Event().wait()
                    return

                self.second_entered.set()
                yield RuntimeEvent(
                    type="token",
                    payload={"delta": "second"},
                    turn_id=turn_input.turn_id,
                )
                await cancel.wait()
                self.second_cancelled = True
                yield EngineResult(
                    terminal=Terminal.aborted,
                    error="cancelled",
                    turn_id=turn_input.turn_id,
                )

        engine = ReplacementTurnDriver()
        app = _make_app(engine, flush_interval=999)
        async with app.run_test() as pilot:
            exits: list[bool] = []
            app.exit = lambda *a, **k: exits.append(True)  # type: ignore[method-assign]

            app.start_turn("first")
            await pilot.pause()
            app.start_turn("second")
            await asyncio.wait_for(engine.second_entered.wait(), timeout=2)
            await pilot.pause()

            assert app._turn_active is True
            app.action_cancel_turn()
            assert exits == []
            await app.workers.wait_for_complete()
            await pilot.pause()

        assert engine.second_cancelled is True
        assert app.last_terminal is not None
        assert app.last_terminal.terminal == Terminal.aborted

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 5b-ii. Ctrl+C binding is priority (preempts Textual's built-in ctrl+c)
# ---------------------------------------------------------------------------
def test_ctrl_c_binding_is_priority() -> None:
    from textual.binding import Binding

    ctrl_c = [
        b
        for b in MagiTuiApp.BINDINGS
        if isinstance(b, Binding) and b.key == "ctrl+c"
    ]
    assert ctrl_c, "ctrl+c must be a Binding (not a bare tuple) to set priority"
    assert ctrl_c[0].priority is True
    assert ctrl_c[0].action == "cancel_turn"


# ---------------------------------------------------------------------------
# 5c. Modal selection by keyboard (number key) resolves the turn
# ---------------------------------------------------------------------------
def test_tool_ask_keyboard_number_selects_allow() -> None:
    async def _run() -> None:
        engine = FakeEngineDriver(tokens=["working"], ask_tool="Bash")
        app = _make_app(engine)
        async with app.run_test() as pilot:
            app._gate = SinkGate(app.sink)
            app.start_turn("run something")
            await pilot.pause()
            await pilot.pause()
            assert isinstance(app.screen, ToolUseConfirm)
            # First action row is focused on mount (Enter works without a click).
            assert app.focused is app.screen.query_one("#allow")
            # "1" selects "Allow once" via the keyboard binding.
            await pilot.press("1")
            await app.workers.wait_for_complete()
            await pilot.pause()
        assert engine.gate_decision is not None
        assert engine.gate_decision.kind == "allow"

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 5d. The change handler is source-guarded: typing into the modal's #edit-area
#     TextArea must NOT recompute prompt completions (its TextArea.Changed
#     bubbles to the App but is for a foreign source, not the prompt buffer).
# ---------------------------------------------------------------------------
def test_modal_edit_area_change_does_not_refresh_prompt_completions() -> None:
    async def _run() -> None:
        engine = FakeEngineDriver(tokens=["working"], ask_tool="Bash")
        app = _make_app(engine)
        async with app.run_test() as pilot:
            app._gate = SinkGate(app.sink)
            app.start_turn("run something")
            await pilot.pause()
            await pilot.pause()
            assert isinstance(app.screen, ToolUseConfirm)
            # Open the edit sub-view so #edit-area is focused.
            await pilot.click("#edit")
            await pilot.pause()

            # Spy on the completion recompute: it must NOT fire for edits made to
            # the modal's #edit-area (only the prompt buffer drives completions).
            calls: list[str] = []
            app._refresh_completions = lambda precursor: calls.append(precursor)  # type: ignore[method-assign]

            editor = app.screen.query_one("#edit-area")
            editor.focus()
            await pilot.pause()
            # Type into the modal editor -> posts TextArea.Changed for #edit-area.
            await pilot.press("x")
            await pilot.pause()

            assert calls == [], (
                "typing into the modal #edit-area must not recompute prompt "
                f"completions; got {calls!r}"
            )
            # Completion overlay stays hidden too.
            assert app._completions is not None
            assert not app._completions.has_class("visible")
            # Resolve the awaiting turn cleanly.
            await pilot.press("escape")
            await app.workers.wait_for_complete()
            await pilot.pause()

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 5e. Autocomplete fires through the live TextArea event path (Pilot)
#     Typing "/" into the real prompt must surface the completion overlay,
#     exercising on_text_area_changed -> _refresh_completions end-to-end. This
#     proves PR1.1's Input.Changed -> TextArea.Changed migration kept the
#     autocomplete wiring intact (we do NOT call the router directly).
# ---------------------------------------------------------------------------
def test_typing_slash_shows_completions_via_textarea_event() -> None:
    async def _run() -> None:
        engine = FakeEngineDriver()
        registry = FakeRegistry(["compact", "reset", "status"])
        app = _make_app(engine, commands=registry)
        async with app.run_test() as pilot:
            app._input.focus()
            await pilot.pause()
            # Type a literal "/" -> posts TextArea.Changed for the prompt buffer.
            await pilot.press("slash")
            # The completion compute runs in an exclusive worker; let it finish.
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert app._completions is not None
            # The overlay is shown (visible class) with the registry commands.
            assert app._completions.has_class("visible"), (
                "typing '/' must surface the completion overlay via the live "
                "TextArea.Changed path"
            )
            assert app._completions.option_count >= 1

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 6. Slash command submission routes through the registry lookup
# ---------------------------------------------------------------------------
def test_slash_command_dispatch_via_registry() -> None:
    async def _run() -> None:
        engine = FakeEngineDriver()
        registry = FakeRegistry(["compact"])
        # Spy on the registry lookup so we prove dispatch actually consulted it.
        looked_up: list[str] = []
        original_lookup = registry.lookup

        def _spy_lookup(name: str):
            looked_up.append(name)
            return original_lookup(name)

        registry.lookup = _spy_lookup  # type: ignore[method-assign]

        app = _make_app(engine, commands=registry)
        async with app.run_test() as pilot:
            app._input.focus()
            await pilot.pause()
            # PR1.1: PromptInput is a TextArea (no ``.value``); set ``.text`` and
            # park the caret at the end, then drive the REAL submit via Enter.
            app._input.text = "/compact"
            app._input.cursor_location = (0, len("/compact"))
            await pilot.press("enter")
            await pilot.pause()
        # REAL dispatch evidence (PR2.2): _dispatch_command now runs the command
        # through the injected CommandExecutor instead of echoing "[command]
        # /compact". A bare LocalCommand (FakeRegistry) has no ``call`` override,
        # so it returns Skip() and nothing is committed for it — the assertion is
        # therefore on the registry lookup + the absence of an engine turn, not
        # on a committed echo line. The dispatch happened without crashing.
        # And the registry lookup path was actually hit by the command name.
        assert "compact" in looked_up
        # No engine turn was run for a command (local Skip()).
        assert app.last_terminal is None

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 7. TextualSink.ask raises the modal and returns the chosen decision
# ---------------------------------------------------------------------------
def test_textual_sink_ask_end_to_end() -> None:
    async def _run() -> None:
        engine = FakeEngineDriver()
        app = _make_app(engine)
        result: dict[str, PermissionDecision] = {}

        async def _ask() -> None:
            req = ControlRequest(
                requestId="r",
                turnId="t",
                toolName="Bash",
                arguments={},
                reason="why",
            )
            result["decision"] = await app.sink.ask(req)

        async with app.run_test() as pilot:
            worker = app.run_worker(_ask(), exclusive=False)
            await pilot.pause()
            await pilot.pause()
            assert isinstance(app.screen, ToolUseConfirm)
            await pilot.click("#allow")
            await worker.wait()
            await pilot.pause()
        assert result["decision"].kind == "allow"

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 7b. TextualSink.ask fails safe to deny when the modal resolves None (teardown)
# ---------------------------------------------------------------------------
def test_textual_sink_ask_none_resolution_falls_back_to_deny() -> None:
    async def _run() -> None:
        engine = FakeEngineDriver()
        app = _make_app(engine)

        async with app.run_test() as pilot:
            await pilot.pause()

            # Simulate the screen resolving with no decision (e.g. app teardown
            # pops the modal without dismissing a PermissionDecision).
            async def _resolve_none(_screen):
                return None

            app.push_screen_wait = _resolve_none  # type: ignore[method-assign]
            req = ControlRequest(
                requestId="r",
                turnId="t",
                toolName="Bash",
                arguments={},
                reason="why",
            )
            decision = await app.sink.ask(req)
        assert isinstance(decision, PermissionDecision)
        assert decision.kind == "deny"

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 8. Edit input: editing the tool arguments yields allow + updated_input dict
# ---------------------------------------------------------------------------
def test_tool_ask_edit_input_yields_updated_input() -> None:
    async def _run() -> None:
        engine = FakeEngineDriver(tokens=["working"], ask_tool="Bash")
        app = _make_app(engine)
        async with app.run_test() as pilot:
            app._gate = SinkGate(app.sink)
            app.start_turn("run something")
            await pilot.pause()
            await pilot.pause()
            assert isinstance(app.screen, ToolUseConfirm)
            # Open the edit view, replace the arguments JSON, then confirm.
            await pilot.click("#edit")
            await pilot.pause()
            editor = app.screen.query_one("#edit-area")
            editor.text = '{"path": "edited.txt", "extra": 1}'
            await pilot.click("#edit-confirm")
            await app.workers.wait_for_complete()
            await pilot.pause()
        decision = engine.gate_decision
        assert decision is not None
        assert decision.kind == "allow"
        assert decision.updated_input == {"path": "edited.txt", "extra": 1}

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 9. Reject with reason: deny path can carry feedback text
# ---------------------------------------------------------------------------
def test_tool_ask_reject_with_feedback_sets_feedback() -> None:
    async def _run() -> None:
        engine = FakeEngineDriver(tokens=["working"], ask_tool="Bash")
        app = _make_app(engine)
        async with app.run_test() as pilot:
            app._gate = SinkGate(app.sink)
            app.start_turn("run something")
            await pilot.pause()
            await pilot.pause()
            assert isinstance(app.screen, ToolUseConfirm)
            # Open the reject-reason view, type a reason, then confirm.
            await pilot.click("#deny-feedback")
            await pilot.pause()
            reason = app.screen.query_one("#deny-reason")
            reason.value = "not allowed here"
            await pilot.click("#deny-confirm")
            await app.workers.wait_for_complete()
            await pilot.pause()
        decision = engine.gate_decision
        assert decision is not None
        assert decision.kind == "deny"
        assert decision.feedback == "not allowed here"

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 10. Edit input with invalid JSON surfaces an error and does NOT dismiss
# ---------------------------------------------------------------------------
def test_tool_ask_edit_input_invalid_json_keeps_modal() -> None:
    async def _run() -> None:
        engine = FakeEngineDriver(tokens=["working"], ask_tool="Bash")
        app = _make_app(engine)
        async with app.run_test() as pilot:
            app._gate = SinkGate(app.sink)
            app.start_turn("run something")
            await pilot.pause()
            await pilot.pause()
            assert isinstance(app.screen, ToolUseConfirm)
            await pilot.click("#edit")
            await pilot.pause()
            modal = app.screen
            editor = modal.query_one("#edit-area")
            editor.text = "{not valid json"
            await pilot.click("#edit-confirm")
            await pilot.pause()
            # Modal stays up (no dismiss) because the edit could not be parsed,
            # and an inline error is surfaced.
            assert app.screen is modal
            assert isinstance(app.screen, ToolUseConfirm)
            assert "Invalid JSON" in modal.last_error
            # Plain reject (escape binding) so the awaiting turn resolves cleanly.
            await pilot.press("escape")
            await app.workers.wait_for_complete()
            await pilot.pause()
        decision = engine.gate_decision
        assert decision is not None
        assert decision.kind == "deny"

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 8. PR0.3: by default the finalized region is a mounted TranscriptView; the
#    MAGI_TUI_LEGACY_RICHLOG=1 escape hatch restores the RichLog backing. The
#    welcome + happy-turn behaviour is identical on both backings.
# ---------------------------------------------------------------------------
def test_app_uses_transcript_view_by_default(monkeypatch) -> None:
    async def _run() -> None:
        from magi_agent.cli.tui.widgets.transcript_view import TranscriptView

        # Assert the DEFAULT (flag-unset) behaviour regardless of an ambient
        # MAGI_TUI_LEGACY_RICHLOG in the environment (e.g. a full-suite run with
        # the escape hatch exported) — the legacy path has its own test.
        monkeypatch.delenv("MAGI_TUI_LEGACY_RICHLOG", raising=False)
        engine = FakeEngineDriver()
        app = _make_app(engine)
        async with app.run_test() as pilot:
            await pilot.pause()
            # New default: the finalized region is a mounted TranscriptView.
            assert len(app.query(TranscriptView)) == 1
            # Welcome still rendered through the widget-list backing.
            joined = "\n".join(app.controller.committed_blocks_snapshot())
            assert "Welcome to Magi" in joined

    asyncio.run(_run())


def test_app_legacy_richlog_flag_restores_richlog(monkeypatch) -> None:
    async def _run() -> None:
        from textual.widgets import RichLog

        from magi_agent.cli.tui.widgets.transcript_view import TranscriptView

        monkeypatch.setenv("MAGI_TUI_LEGACY_RICHLOG", "1")
        engine = FakeEngineDriver()
        app = _make_app(engine)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert len(app.query(RichLog)) == 1
            assert len(app.query(TranscriptView)) == 0

    asyncio.run(_run())


def test_tool_event_commits_one_line_block(monkeypatch) -> None:
    """Tool events render as compact one-line blocks, never collapsible cards.

    The old large ``▶`` ``ToolCard`` boxes flooded the transcript; tools now
    render Claude-Code style (``● Name(arg)`` + dimmed ``└ preview``) committed
    inline, so no ``ToolCard`` widget is ever mounted on either backing.
    """

    async def _run() -> None:
        from magi_agent.cli.tui.tool_render import build_tool_renderers
        from magi_agent.cli.tui.widgets.tool_card import ToolCard

        monkeypatch.delenv("MAGI_TUI_LEGACY_RICHLOG", raising=False)
        engine = FakeEngineDriver(tokens=["ok"], ask_tool="Bash")
        app = MagiTuiApp(
            engine=engine,
            gate=AllowGate(),
            commands=FakeRegistry(["compact"]),
            renderers=build_tool_renderers(),
        )
        async with app.run_test() as pilot:
            app.start_turn("run ls")
            await app.workers.wait_for_complete()
            await pilot.pause()
            # No collapsible cards: tool output is committed inline.
            assert len(app.query(ToolCard)) == 0
            # Search fidelity: the tool text is still in the committed snapshot.
            joined = "\n".join(app.controller.committed_blocks_snapshot())
            assert "Bash" in joined

    asyncio.run(_run())


def test_internal_status_events_hidden_by_default(monkeypatch) -> None:
    """Internal lifecycle/plumbing status events are dropped from the transcript
    unless MAGI_TUI_VERBOSE=1 — they used to flood chat with bare lines like
    ``runner_policy_assembly`` / ``phase_route_decision`` / ``turn_end``."""

    class _StatusEngine:
        async def run_turn_stream(self, runtime, turn_input, *, cancel, gate=None):
            turn_id = getattr(turn_input, "turn_id", "t")
            yield RuntimeEvent(
                type="status",
                payload={"type": "runner_policy_assembly", "turnId": turn_id},
                turn_id=turn_id,
            )
            yield RuntimeEvent(
                type="token", payload={"delta": "hi"}, turn_id=turn_id
            )
            yield EngineResult(terminal=Terminal.completed, turn_id=turn_id)

    async def _run(verbose: bool) -> str:
        from magi_agent.cli.tui.tool_render import build_tool_renderers

        monkeypatch.delenv("MAGI_TUI_LEGACY_RICHLOG", raising=False)
        if verbose:
            monkeypatch.setenv("MAGI_TUI_VERBOSE", "1")
        else:
            monkeypatch.delenv("MAGI_TUI_VERBOSE", raising=False)
        app = MagiTuiApp(
            engine=_StatusEngine(),
            gate=AllowGate(),
            commands=FakeRegistry(["compact"]),
            renderers=build_tool_renderers(),
        )
        async with app.run_test() as pilot:
            app.start_turn("hi")
            await app.workers.wait_for_complete()
            await pilot.pause()
            return "\n".join(app.controller.committed_blocks_snapshot())

    quiet = asyncio.run(_run(verbose=False))
    assert "runner_policy_assembly" not in quiet
    assert "hi" in quiet  # real assistant text still renders

    loud = asyncio.run(_run(verbose=True))
    assert "runner_policy_assembly" in loud


def test_assistant_text_committed_before_tool_card_in_one_turn(monkeypatch) -> None:
    """Finalize-before-tool ordering (Phase 0 review).

    Within ONE turn, ``app._fold_event`` must flush + finalize the in-flight
    assistant markdown BEFORE the tool card mounts, so streamed assistant text
    appears ABOVE the tool output in the transcript. We assert the committed
    snapshot index of the assistant-text block is strictly LESS than the tool
    header block's index. (Backing-agnostic; default widget backing here.)
    """

    async def _run() -> None:
        from magi_agent.cli.tui.tool_render import build_tool_renderers

        monkeypatch.delenv("MAGI_TUI_LEGACY_RICHLOG", raising=False)
        # Stream assistant tokens, THEN emit a Bash tool event in the same turn.
        engine = FakeEngineDriver(tokens=["assistant says hi"], ask_tool="Bash")
        app = MagiTuiApp(
            engine=engine,
            gate=AllowGate(),
            commands=FakeRegistry(["compact"]),
            renderers=build_tool_renderers(),
        )
        async with app.run_test() as pilot:
            app.start_turn("run ls")
            await app.workers.wait_for_complete()
            await pilot.pause()
            blocks = app.controller.committed_blocks_snapshot()

        assistant_idx = next(
            i for i, b in enumerate(blocks) if "assistant says hi" in b
        )
        tool_idx = next(i for i, b in enumerate(blocks) if "Bash" in b)
        assert assistant_idx < tool_idx, (
            f"assistant text (idx {assistant_idx}) must be committed before the "
            f"tool card (idx {tool_idx}); blocks={blocks!r}"
        )

    asyncio.run(_run())


def test_tool_event_legacy_richlog_no_card(monkeypatch) -> None:
    """Under MAGI_TUI_LEGACY_RICHLOG=1 there is no widget backing, so tool
    output routes through commit_rich/commit_block (no Collapsible mounted)."""

    async def _run() -> None:
        from magi_agent.cli.tui.tool_render import build_tool_renderers
        from magi_agent.cli.tui.widgets.tool_card import ToolCard

        monkeypatch.setenv("MAGI_TUI_LEGACY_RICHLOG", "1")
        engine = FakeEngineDriver(tokens=["ok"], ask_tool="Bash")
        app = MagiTuiApp(
            engine=engine,
            gate=AllowGate(),
            commands=FakeRegistry(["compact"]),
            renderers=build_tool_renderers(),
        )
        async with app.run_test() as pilot:
            app.start_turn("run ls")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert len(app.query(ToolCard)) == 0
            joined = "\n".join(app.controller.committed_blocks_snapshot())
            assert "Bash" in joined

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# PR2.2 — slash-command execution through the injected CommandExecutor
# ---------------------------------------------------------------------------
def test_dispatch_prompt_command_starts_turn() -> None:
    async def _run() -> None:
        from magi_agent.cli.contracts import (
            CommandSurface as CS,
            ContentBlock,
            PromptCommand,
        )

        TUI2 = CS(tui=True, headless=False)

        class Greet(PromptCommand):
            async def build_prompt(self, args, ctx):  # type: ignore[override]
                return [ContentBlock(type="text", text="do the thing")]

        class Reg:
            def lookup(self, name):
                return Greet(name="greet", surface=TUI2) if name == "greet" else None

            def list_for(self, surface):
                return [Greet(name="greet", surface=TUI2)]

        engine = FakeEngineDriver(tokens=["x"])
        app = _make_app(engine, commands=Reg())
        async with app.run_test() as pilot:
            app.submit_command("greet", "")
            await app.workers.wait_for_complete()
            await pilot.pause()
        # A prompt command re-entered the ONE turn loop.
        assert app.last_terminal is not None
        assert app.last_terminal.terminal == Terminal.completed
        blocks = app.controller.committed_blocks_snapshot()
        assert any("do the thing" in b for b in blocks)

    asyncio.run(_run())


def test_dispatch_local_compact_requests_compact() -> None:
    async def _run() -> None:
        from magi_agent.cli.commands.builtins import CompactCommand, BUILTIN_BOTH

        class Reg:
            def lookup(self, name):
                return (
                    CompactCommand(name="compact", surface=BUILTIN_BOTH)
                    if name == "compact"
                    else None
                )

            def list_for(self, surface):
                return [CompactCommand(name="compact", surface=BUILTIN_BOTH)]

        engine = FakeEngineDriver()
        app = _make_app(engine, commands=Reg())
        async with app.run_test() as pilot:
            app.submit_command("compact", "")
            await app.workers.wait_for_complete()
            await pilot.pause()
        assert app.compact_requests >= 1
        assert app.last_terminal is None  # local command runs no engine turn

    asyncio.run(_run())


def test_dispatch_unknown_command_warns() -> None:
    async def _run() -> None:
        engine = FakeEngineDriver()
        app = _make_app(engine, commands=FakeRegistry(["compact"]))
        async with app.run_test() as pilot:
            app.submit_command("nope", "")
            await pilot.pause()
        blocks = app.controller.committed_blocks_snapshot()
        assert any("unknown" in b and "nope" in b for b in blocks)

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# PR2.3 — model picker dialog opener + apply
# ---------------------------------------------------------------------------
def test_open_model_picker_applies_selection() -> None:
    async def _run() -> None:
        from magi_agent.cli.tui.dialogs.model import ModelPickerDialog

        engine = FakeEngineDriver()
        app = _make_app(engine)
        app._model = "claude-sonnet-4-6"
        async with app.run_test() as pilot:
            app.action_open_model_picker()
            await pilot.pause()
            assert isinstance(app.screen, ModelPickerDialog)
            app.screen.dismiss("gpt-5.5")
            await pilot.pause()
            await pilot.pause()
        assert app._model == "gpt-5.5"
        assert "gpt-5.5" in app._topbar_text()

    asyncio.run(_run())


def test_open_model_picker_cancel_keeps_model() -> None:
    async def _run() -> None:
        from magi_agent.cli.tui.dialogs.model import ModelPickerDialog

        engine = FakeEngineDriver()
        app = _make_app(engine)
        app._model = "claude-sonnet-4-6"
        async with app.run_test() as pilot:
            app.action_open_model_picker()
            await pilot.pause()
            assert isinstance(app.screen, ModelPickerDialog)
            app.screen.dismiss(None)
            await pilot.pause()
            await pilot.pause()
        assert app._model == "claude-sonnet-4-6"

    asyncio.run(_run())


def test_open_dialog_model_picker_opens_dialog() -> None:
    async def _run() -> None:
        from magi_agent.cli.tui.dialogs.model import ModelPickerDialog

        engine = FakeEngineDriver()
        app = _make_app(engine)
        async with app.run_test() as pilot:
            app.open_dialog("model_picker")
            await pilot.pause()
            assert isinstance(app.screen, ModelPickerDialog)

    asyncio.run(_run())


def test_open_model_picker_surfaces_in_palette_actions() -> None:
    async def _run() -> None:
        from magi_agent.cli.tui.palette import AppActionProvider

        engine = FakeEngineDriver()
        app = _make_app(engine)
        async with app.run_test() as pilot:
            await pilot.pause()
            provider = AppActionProvider(app.screen)
            provider._app_ref = app
            hits = [h async for h in provider.discover()]
        labels = [getattr(h, "text", "") or "" for h in hits]
        assert "Switch model" in labels

    asyncio.run(_run())


def test_apply_model_without_topbar_does_not_crash() -> None:
    # _apply_model is reachable before the topbar is wired (e.g. programmatic
    # apply pre-mount). It must not crash and must still update self._model.
    engine = FakeEngineDriver()
    app = _make_app(engine)
    assert app._topbar is None  # not yet composed/mounted
    app._apply_model("gpt-5.5")
    assert app._model == "gpt-5.5"


def test_ctrl_p_opens_command_palette() -> None:
    async def _run() -> None:
        from textual.command import CommandPalette

        engine = FakeEngineDriver()
        app = _make_app(engine)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("ctrl+p")
            await pilot.pause()
            # The command palette screen is actually pushed onto the stack.
            assert any(
                isinstance(s, CommandPalette) for s in app.screen_stack
            )

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Session list dialog (PR2.4) — resume = marker-only, NO synthetic turn (OQ3)
# ---------------------------------------------------------------------------
def test_open_session_list_resume_is_marker_only_no_turn() -> None:
    async def _run() -> None:
        from magi_agent.cli.tui.dialogs.session import (
            SessionEntry,
            SessionListDialog,
        )

        engine = FakeEngineDriver(tokens=["resumed"])
        app = _make_app(engine)
        # Inject a couple of resumable sessions directly (controller seam).
        app._session_source = lambda: [
            SessionEntry(ref="s-9", label="earlier work", updated="2026-06-06")
        ]
        async with app.run_test() as pilot:
            app.action_open_session_list()
            await pilot.pause()
            assert isinstance(app.screen, SessionListDialog)
            app.screen.dismiss("s-9")
            await app.workers.wait_for_complete()
            await pilot.pause()
        # Resume switched the active session id and recorded the resumed ref.
        assert app.resumed_session == "s-9"
        assert app._session_id == "s-9"
        # Marker-only: the visible "[resumed session s-9]" block is committed,
        # but NO synthetic engine turn was started by the resume itself.
        blocks = app.controller.committed_blocks_snapshot()
        assert any("resumed session s-9" in b for b in blocks)
        assert app.last_terminal is None  # resume sent no engine turn
        # The redundant/confusing synthetic "Resume session s-9." prompt is
        # NOT echoed — the user's NEXT prompt runs under the resumed id.
        assert not any("Resume session s-9" in b for b in blocks)

    asyncio.run(_run())


def test_resume_rebinds_history_and_drafts_to_new_session() -> None:
    async def _run() -> None:
        from magi_agent.cli.tui.dialogs.session import (
            SessionEntry,
            SessionListDialog,
        )

        engine = FakeEngineDriver()
        app = _make_app(engine, commands=FakeRegistry([]))
        app._session_source = lambda: [
            SessionEntry(ref="s-99", label="earlier work")
        ]
        async with app.run_test() as pilot:
            await pilot.pause()
            old_history = app._history
            old_drafts = app._drafts
            # Seed the OLD session's history so we can prove the recall reads the
            # RESUMED session's ring (which is empty) after the re-bind.
            old_history.add("old-session-prompt")

            app.action_open_session_list()
            await pilot.pause()
            assert isinstance(app.screen, SessionListDialog)
            app.screen.dismiss("s-99")
            await pilot.pause()
            await pilot.pause()

            # New history/drafts objects, bound to the resumed session id.
            assert app._history is not old_history
            assert app._drafts is not old_drafts
            assert app._history._session_id == "s-99"
            assert app._drafts._session_id == "s-99"
            assert "s-99" in str(app._history._path)
            assert "s-99" in str(app._drafts._path)

            # ↑-recall after resume reads the RESUMED session's history (empty),
            # NOT the old session's "old-session-prompt".
            recalled = app._history.prev("")
            assert recalled != "old-session-prompt"
            assert recalled is None
            # The prompt input's recall ring follows the resumed session too.
            assert app._input is not None
            assert app._input._history is app._history

    asyncio.run(_run())


def test_open_session_list_cancel_keeps_session() -> None:
    async def _run() -> None:
        from magi_agent.cli.tui.dialogs.session import (
            SessionEntry,
            SessionListDialog,
        )

        engine = FakeEngineDriver()
        app = _make_app(engine)
        app._session_source = lambda: [
            SessionEntry(ref="s-9", label="earlier work")
        ]
        original = app._session_id
        async with app.run_test() as pilot:
            app.action_open_session_list()
            await pilot.pause()
            assert isinstance(app.screen, SessionListDialog)
            app.screen.dismiss(None)
            await pilot.pause()
            await pilot.pause()
        assert app.resumed_session is None
        assert app._session_id == original
        assert app.last_terminal is None  # no turn ran on cancel

    asyncio.run(_run())


def test_open_session_list_empty_when_no_source() -> None:
    async def _run() -> None:
        from magi_agent.cli.tui.dialogs.session import SessionListDialog
        from textual.widgets import OptionList

        engine = FakeEngineDriver()
        app = _make_app(engine)  # no runtime, no _session_source -> empty
        async with app.run_test() as pilot:
            app.action_open_session_list()
            await pilot.pause()
            assert isinstance(app.screen, SessionListDialog)
            assert app.screen.query_one(OptionList).option_count == 0

    asyncio.run(_run())


def test_open_dialog_session_list_opens_dialog() -> None:
    async def _run() -> None:
        from magi_agent.cli.tui.dialogs.session import SessionListDialog

        engine = FakeEngineDriver()
        app = _make_app(engine)
        async with app.run_test() as pilot:
            app.open_dialog("session_list")
            await pilot.pause()
            assert isinstance(app.screen, SessionListDialog)

    asyncio.run(_run())


def test_open_session_list_surfaces_in_palette_actions() -> None:
    async def _run() -> None:
        from magi_agent.cli.tui.palette import AppActionProvider

        engine = FakeEngineDriver()
        app = _make_app(engine)
        async with app.run_test() as pilot:
            await pilot.pause()
            provider = AppActionProvider(app.screen)
            provider._app_ref = app
            hits = [h async for h in provider.discover()]
        labels = [getattr(h, "text", "") or "" for h in hits]
        assert "Sessions" in labels

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Help dialog (PR2.5) — read-only keybinding + command reference
# ---------------------------------------------------------------------------
def test_open_help_shows_help_dialog() -> None:
    async def _run() -> None:
        from magi_agent.cli.tui.dialogs.help import HelpDialog

        engine = FakeEngineDriver()
        app = _make_app(engine, commands=FakeRegistry(["compact", "status"]))
        async with app.run_test() as pilot:
            app.action_open_help()
            await pilot.pause()
            assert isinstance(app.screen, HelpDialog)
            body = app.screen.query_one("#help-body")
            # Textual 8.2.7: Static has no .renderable — use .render().
            rendered = str(body.render())
            assert "/compact" in rendered
            assert "/status" in rendered
            assert "ctrl+p" in rendered  # COMMAND_PALETTE_BINDING surfaced
            # Phase-1 prompt keys surface via the default PROMPT_KEYS section.
            assert "Shift+Enter" in rendered
            assert "Ctrl+S" in rendered
            assert "History recall" in rendered
            await pilot.press("escape")
            await pilot.pause()
            # Escape dismissed the modal: no HelpDialog left on the stack.
            assert not any(
                isinstance(s, HelpDialog) for s in app.screen_stack
            )

    asyncio.run(_run())


def test_open_dialog_help_opens_dialog() -> None:
    async def _run() -> None:
        from magi_agent.cli.tui.dialogs.help import HelpDialog

        engine = FakeEngineDriver()
        app = _make_app(engine)
        async with app.run_test() as pilot:
            app.open_dialog("help")
            await pilot.pause()
            assert isinstance(app.screen, HelpDialog)

    asyncio.run(_run())


def test_open_help_surfaces_in_palette_actions() -> None:
    async def _run() -> None:
        from magi_agent.cli.tui.palette import AppActionProvider

        engine = FakeEngineDriver()
        app = _make_app(engine)
        async with app.run_test() as pilot:
            await pilot.pause()
            provider = AppActionProvider(app.screen)
            provider._app_ref = app
            hits = [h async for h in provider.discover()]
        labels = [getattr(h, "text", "") or "" for h in hits]
        assert "Help" in labels

    asyncio.run(_run())


def test_f1_opens_help_dialog() -> None:
    async def _run() -> None:
        from magi_agent.cli.tui.dialogs.help import HelpDialog

        engine = FakeEngineDriver()
        app = _make_app(engine)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("f1")
            await pilot.pause()
            assert isinstance(app.screen, HelpDialog)

    asyncio.run(_run())
