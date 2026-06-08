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
# 6. Slash command submission routes through the registry lookup
# ---------------------------------------------------------------------------
def test_slash_command_dispatch_via_registry() -> None:
    async def _run() -> None:
        engine = FakeEngineDriver()
        registry = FakeRegistry(["compact"])
        app = _make_app(engine, commands=registry)
        async with app.run_test() as pilot:
            app._input.focus()
            await pilot.pause()
            app._input.value = "/compact"
            await pilot.press("enter")
            await pilot.pause()
        blocks = app.controller.committed_blocks_snapshot()
        assert any("/compact" in b for b in blocks)
        # No engine turn was run for a command.
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


def test_tool_event_commits_collapsible_card(monkeypatch) -> None:
    async def _run() -> None:
        from magi_agent.cli.tui.tool_render import build_tool_renderers
        from magi_agent.cli.tui.widgets.tool_card import ToolCard

        # This asserts the widget-list (default) backing, so pin it even when a
        # full-suite run sets MAGI_TUI_LEGACY_RICHLOG in the environment
        # (the legacy path is covered by test_tool_event_legacy_richlog_no_card).
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
            # The Bash tool_end rendered as a collapsed ToolCard in the view.
            cards = app.query(ToolCard)
            assert len(cards) >= 1
            assert all(card.collapsed for card in cards)
            # Search fidelity: the tool text is still in the committed snapshot.
            joined = "\n".join(app.controller.committed_blocks_snapshot())
            assert "Bash" in joined

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
