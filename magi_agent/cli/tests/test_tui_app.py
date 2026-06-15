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
from magi_agent.cli.keybindings.schema import Action
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
        # Phase 3 sidebar toggle advertised in the welcome banner too.
        assert "Ctrl+B" in joined
        # Phase 2 doors advertised too: command palette + help.
        assert "Ctrl+P" in joined
        assert "F1" in joined
        # Exit-safety: the banner must teach the REAL double-press gesture and
        # mention Esc; it must NOT claim a single Ctrl+C is "again to quit".
        assert "twice to quit" in joined
        assert "Esc" in joined
        assert "again to quit" not in joined
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

            # Idle -> first press arms (no exit), second within-window quits.
            app._turn_active = False
            app.action_cancel_turn()
            assert exits == []
            assert app._quit_armed_at is not None
            app.action_cancel_turn()
            assert exits == [True]
            await pilot.pause()

    asyncio.run(_run())


def test_cancel_turn_carries_escape_key_via_run_key_action() -> None:
    async def _run() -> None:
        engine = FakeEngineDriver()
        app = _make_app(engine)
        async with app.run_test() as pilot:
            exits: list[bool] = []
            app.exit = lambda *a, **k: exits.append(True)  # type: ignore[method-assign]
            app._turn_active = False
            app._input.text = "draft"
            # Esc resolves to CHAT_CANCEL; the resolver must mark _cancel_key
            # "escape" so the idle branch clears the buffer before arming.
            app._run_key_action(Action.CHAT_CANCEL.value)
            assert app._input.text == ""
            assert exits == []
            await pilot.pause()

    asyncio.run(_run())


def test_idle_esc_clears_nonempty_input_before_arming() -> None:
    async def _run() -> None:
        engine = FakeEngineDriver()
        app = _make_app(engine)
        async with app.run_test() as pilot:
            exits: list[bool] = []
            app.exit = lambda *a, **k: exits.append(True)  # type: ignore[method-assign]
            app._turn_active = False
            app._input.text = "draft"
            app._cancel_key = "escape"
            app.action_cancel_turn()
            assert app._input.text == ""
            assert exits == []
            assert app._quit_armed_at is None
            await pilot.pause()

    asyncio.run(_run())


def test_idle_ctrl_c_does_not_clear_nonempty_input() -> None:
    async def _run() -> None:
        engine = FakeEngineDriver()
        app = _make_app(engine)
        async with app.run_test() as pilot:
            exits: list[bool] = []
            app.exit = lambda *a, **k: exits.append(True)  # type: ignore[method-assign]
            app._turn_active = False
            app._input.text = "draft"
            app._cancel_key = None  # Ctrl+C priority-Binding path
            app.action_cancel_turn()
            assert app._input.text == "draft"
            assert app._quit_armed_at is not None
            assert exits == []
            await pilot.pause()

    asyncio.run(_run())


def test_kill_agents_idle_does_not_arm_quit() -> None:
    async def _run() -> None:
        engine = FakeEngineDriver()
        app = _make_app(engine)
        async with app.run_test() as pilot:
            exits: list[bool] = []
            app.exit = lambda *a, **k: exits.append(True)  # type: ignore[method-assign]
            # Idle kill-agents chord (ctrl+x ctrl+k) is cancel-only: a no-op
            # when idle, never arms a quit.
            app._turn_active = False
            app._run_key_action(Action.CHAT_KILL_AGENTS.value)
            assert exits == []
            assert app._quit_armed_at is None
            await pilot.pause()

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 5b-quit-safety (Pilot). Real key dispatch — direct action_* calls false-green
# the routing (they bypass Textual's Ctrl+Q preemption / TextArea Ctrl+D consume).
# ---------------------------------------------------------------------------
def test_idle_esc_arms_then_quits_via_pilot() -> None:
    async def _run() -> None:
        engine = FakeEngineDriver()
        app = _make_app(engine)
        async with app.run_test() as pilot:
            app._input.focus()
            await pilot.pause()
            exits: list[bool] = []
            app.exit = lambda *a, **k: exits.append(True)  # type: ignore[method-assign]
            app._turn_active = False
            # Real Esc -> on_key -> resolve -> CHAT_CANCEL -> action_cancel_turn
            # -> _arm_or_quit. Depends on PromptInput keeping tab_behavior="focus"
            # so Escape bubbles (TextArea swallows it under "indent").
            await pilot.press("escape")
            assert exits == []
            assert app._quit_armed_at is not None
            await pilot.press("escape")
            assert exits == [True]

    asyncio.run(_run())


def test_idle_ctrl_c_arms_then_quits_via_pilot() -> None:
    async def _run() -> None:
        engine = FakeEngineDriver()
        app = _make_app(engine)
        async with app.run_test() as pilot:
            app._input.focus()
            await pilot.pause()
            exits: list[bool] = []
            app.exit = lambda *a, **k: exits.append(True)  # type: ignore[method-assign]
            app._turn_active = False
            # Real Ctrl+C goes through the priority=True Binding (app BINDINGS)
            # -> action_cancel_turn with _cancel_key unset -> "ctrl+c" (no clear).
            await pilot.press("ctrl+c")
            assert exits == []
            assert app._quit_armed_at is not None
            await pilot.press("ctrl+c")
            assert exits == [True]

    asyncio.run(_run())


def test_ctrl_q_binding_is_priority() -> None:
    from textual.binding import Binding

    ctrl_q = [
        b
        for b in MagiTuiApp.BINDINGS
        if isinstance(b, Binding) and b.key == "ctrl+q"
    ]
    assert ctrl_q, "ctrl+q must be a Binding to preempt Textual's built-in quit"
    assert len(ctrl_q) == 1
    assert ctrl_q[0].priority is True
    assert ctrl_q[0].action == "request_quit"


def test_idle_ctrl_q_routes_to_arming_not_textual_quit_via_pilot() -> None:
    async def _run() -> None:
        engine = FakeEngineDriver()
        app = _make_app(engine)
        async with app.run_test() as pilot:
            app._input.focus()
            await pilot.pause()
            exits: list[bool] = []
            app.exit = lambda *a, **k: exits.append(True)  # type: ignore[method-assign]
            app._turn_active = False
            # Magi's priority=True ctrl+q must preempt Textual's built-in
            # ctrl+q->quit; the first press arms instead of exiting.
            await pilot.press("ctrl+q")
            assert exits == []
            assert app._quit_armed_at is not None
            await pilot.press("ctrl+q")
            assert exits == [True]

    asyncio.run(_run())


def test_idle_ctrl_d_empty_buffer_arms_via_pilot() -> None:
    async def _run() -> None:
        engine = FakeEngineDriver()
        app = _make_app(engine)
        async with app.run_test() as pilot:
            app._input.focus()
            await pilot.pause()
            app._input.text = ""
            exits: list[bool] = []
            app.exit = lambda *a, **k: exits.append(True)  # type: ignore[method-assign]
            app._turn_active = False
            # Empty-buffer Ctrl+D is intercepted in PromptInput._on_key before
            # the base TextArea delete-right and calls up to request_quit.
            await pilot.press("ctrl+d")
            assert exits == []
            assert app._quit_armed_at is not None
            await pilot.press("ctrl+d")
            assert exits == [True]

    asyncio.run(_run())


def test_ctrl_d_nonempty_buffer_deletes_not_quits_via_pilot() -> None:
    async def _run() -> None:
        engine = FakeEngineDriver()
        app = _make_app(engine)
        async with app.run_test() as pilot:
            app._input.focus()
            await pilot.pause()
            app._input.text = "abc"
            app._input.move_cursor((0, 0))
            exits: list[bool] = []
            app.exit = lambda *a, **k: exits.append(True)  # type: ignore[method-assign]
            app._turn_active = False
            # Non-empty buffer: Ctrl+D stays forward-delete (no quit, no arm).
            await pilot.press("ctrl+d")
            assert app._input.text == "bc"
            assert exits == []
            assert app._quit_armed_at is None

    asyncio.run(_run())


def test_cross_key_arming_confirms_quit_via_pilot() -> None:
    async def _run() -> None:
        engine = FakeEngineDriver()
        app = _make_app(engine)
        async with app.run_test() as pilot:
            app._input.focus()
            await pilot.pause()
            app._input.text = ""  # empty so Esc arms (not clear-first)
            exits: list[bool] = []
            app.exit = lambda *a, **k: exits.append(True)  # type: ignore[method-assign]
            app._turn_active = False
            # Cross-key: Esc arms, then Ctrl+C within the window confirms quit
            # (shared _quit_armed_at). Proven through real dispatch.
            await pilot.press("escape")
            assert exits == []
            assert app._quit_armed_at is not None
            await pilot.press("ctrl+c")
            assert exits == [True]

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 5b-quit-safety. Idle Esc/Ctrl+C/Ctrl+Q/Ctrl+D are a taught double-press, not
# a bare quit. See docs/plans/2026-06-14-magi-tui-ux/01-exit-cancel-safety.md.
# ---------------------------------------------------------------------------
def test_quit_armed_state_initialized_none() -> None:
    async def _run() -> None:
        engine = FakeEngineDriver()
        app = _make_app(engine)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app._quit_armed_at is None

    asyncio.run(_run())


def test_arm_or_quit_first_press_arms_and_toasts() -> None:
    async def _run() -> None:
        engine = FakeEngineDriver()
        app = _make_app(engine)
        async with app.run_test() as pilot:
            exits: list[bool] = []
            app.exit = lambda *a, **k: exits.append(True)  # type: ignore[method-assign]
            toasts: list[str] = []
            import magi_agent.cli.tui.app as _appmod

            monkey = _appmod._notify.info
            _appmod._notify.info = lambda _app, msg, **k: toasts.append(msg)  # type: ignore[attr-defined]
            try:
                app._turn_active = False
                app._arm_or_quit(key="ctrl+c")
            finally:
                _appmod._notify.info = monkey  # type: ignore[attr-defined]
            assert exits == []
            assert app._quit_armed_at is not None
            assert toasts == ["Press again to quit"]
            await pilot.pause()

    asyncio.run(_run())


def test_arm_or_quit_second_press_within_window_quits() -> None:
    async def _run() -> None:
        engine = FakeEngineDriver()
        app = _make_app(engine)
        async with app.run_test() as pilot:
            exits: list[bool] = []
            app.exit = lambda *a, **k: exits.append(True)  # type: ignore[method-assign]
            app._turn_active = False
            app._arm_or_quit(key="ctrl+c")
            assert exits == []
            app._arm_or_quit(key="ctrl+c")
            assert exits == [True]
            await pilot.pause()

    asyncio.run(_run())


def test_arm_or_quit_disarms_after_window() -> None:
    async def _run() -> None:
        import time as _time

        engine = FakeEngineDriver()
        app = _make_app(engine)
        async with app.run_test() as pilot:
            exits: list[bool] = []
            app.exit = lambda *a, **k: exits.append(True)  # type: ignore[method-assign]
            app._turn_active = False
            app._arm_or_quit(key="ctrl+c")
            assert app._quit_armed_at is not None
            # Backdate the arm beyond the window -> the next press re-arms.
            app._quit_armed_at = _time.monotonic() - (MagiTuiApp.QUIT_WINDOW_S + 1)
            app._arm_or_quit(key="ctrl+c")
            assert exits == []
            assert app._quit_armed_at is not None
            await pilot.pause()

    asyncio.run(_run())


def test_arm_or_quit_window_constant() -> None:
    assert MagiTuiApp.QUIT_WINDOW_S == 1.5
    assert MagiTuiApp.INSTANT_QUIT_ENV == "MAGI_TUI_INSTANT_QUIT"


def test_instant_quit_env_bypasses_arming(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_TUI_INSTANT_QUIT", "1")

    async def _run() -> None:
        engine = FakeEngineDriver()
        app = _make_app(engine)
        async with app.run_test() as pilot:
            exits: list[bool] = []
            app.exit = lambda *a, **k: exits.append(True)  # type: ignore[method-assign]
            app._turn_active = False
            app._arm_or_quit(key="ctrl+c")
            assert exits == [True]
            assert app._quit_armed_at is None
            await pilot.pause()

    asyncio.run(_run())


def test_instant_quit_env_non_truthy_does_not_bypass(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_TUI_INSTANT_QUIT", "0")

    async def _run() -> None:
        engine = FakeEngineDriver()
        app = _make_app(engine)
        async with app.run_test() as pilot:
            exits: list[bool] = []
            app.exit = lambda *a, **k: exits.append(True)  # type: ignore[method-assign]
            app._turn_active = False
            app._arm_or_quit(key="ctrl+c")
            assert exits == []
            assert app._quit_armed_at is not None
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
# busy-input-queue (gap: busy-input-queue) — default-OFF, surface-only FIFO that
# buffers a prompt submitted while a turn runs and drains it after the turn ends.
# ---------------------------------------------------------------------------
class _BlockingFirstDriver:
    """First turn yields one token then blocks forever; later turns complete.

    A function-local style helper (the pinned ``ReplacementTurnDriver`` is NOT
    importable). ``calls`` counts ``run_turn_stream`` entries; ``turn_inputs``
    records each ``TurnInput`` so attachment/image assertions can inspect them.
    """

    def __init__(self) -> None:
        self.calls = 0
        self.turn_inputs: list[object] = []

    async def run_turn_stream(self, runtime, turn_input, *, cancel, gate=None):
        _ = runtime, gate
        self.calls += 1
        self.turn_inputs.append(turn_input)
        if self.calls == 1:
            yield RuntimeEvent(
                type="token",
                payload={"delta": "first"},
                turn_id=turn_input.turn_id,
            )
            await asyncio.Event().wait()
            return
        yield RuntimeEvent(
            type="token",
            payload={"delta": "drained"},
            turn_id=turn_input.turn_id,
        )
        yield EngineResult(terminal=Terminal.completed, turn_id=turn_input.turn_id)


def test_queue_buffer_inits_empty_and_flag_defaults_off(monkeypatch) -> None:
    async def _run() -> None:
        monkeypatch.delenv("MAGI_TUI_QUEUE", raising=False)
        app = _make_app(FakeEngineDriver())
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app._prompt_queue == []
            assert app._queue_enabled is False

    asyncio.run(_run())


def test_queue_flag_on_when_env_set(monkeypatch) -> None:
    async def _run() -> None:
        monkeypatch.setenv("MAGI_TUI_QUEUE", "1")
        app = _make_app(FakeEngineDriver())
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app._queue_enabled is True

    asyncio.run(_run())


def test_start_or_enqueue_enqueues_when_busy(monkeypatch) -> None:
    async def _run() -> None:
        monkeypatch.setenv("MAGI_TUI_QUEUE", "1")
        engine = _BlockingFirstDriver()
        app = _make_app(engine, flush_interval=999)
        async with app.run_test() as pilot:
            app.start_turn("first")
            await pilot.pause()
            assert app._turn_active is True
            app.start_or_enqueue_turn("second")
            # First turn NOT replaced; second parked in the queue (text-only
            # snapshot in Task 3 — empty attachment tuple).
            assert engine.calls == 1
            assert app._prompt_queue == [("second", ())]
            assert app._turn_active is True
            snapshot = app.controller.committed_blocks_snapshot()
            assert any(
                b.startswith("⏳ queued:") and "second" in b for b in snapshot
            )

    asyncio.run(_run())


def test_start_or_enqueue_starts_when_idle(monkeypatch) -> None:
    async def _run() -> None:
        monkeypatch.setenv("MAGI_TUI_QUEUE", "1")
        engine = FakeEngineDriver(tokens=["x"])
        app = _make_app(engine)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.start_or_enqueue_turn("solo")
            await app.workers.wait_for_complete()
            await pilot.pause()
            # Idle path is just start_turn: the turn ran to a terminal and
            # nothing was queued.
            assert app.last_terminal is not None
            assert app._prompt_queue == []
            snapshot = app.controller.committed_blocks_snapshot()
            assert any("› solo" in b for b in snapshot)

    asyncio.run(_run())


def _submit_via_funnel(app, text: str) -> None:
    """Drive the REAL typed-submission funnel for ``text``."""

    from magi_agent.cli.tui.input import PromptInput, classify_line  # noqa: PLC0415

    submission = classify_line(text, app._commands)
    app.on_prompt_input_prompt_submitted(PromptInput.PromptSubmitted(submission))


def test_submit_while_busy_enqueues_via_funnel(monkeypatch) -> None:
    async def _run() -> None:
        monkeypatch.setenv("MAGI_TUI_QUEUE", "1")
        engine = _BlockingFirstDriver()
        app = _make_app(engine, flush_interval=999)
        async with app.run_test() as pilot:
            app.start_turn("first")
            await pilot.pause()
            _submit_via_funnel(app, "second")
            assert engine.calls == 1  # first NOT replaced
            assert app._prompt_queue == [("second", ())]
            snapshot = app.controller.committed_blocks_snapshot()
            assert any(
                b.startswith("⏳ queued:") and "second" in b for b in snapshot
            )

    asyncio.run(_run())


class _DrainDriver:
    """First turn completes normally; records each TurnInput.

    Unlike ``_BlockingFirstDriver`` the first turn does NOT block — it yields
    one token then a terminal, so the queue can drain after it finishes. A small
    ``first_started`` event lets a test enqueue while the first turn is still
    observably the active one (before its terminal lands).
    """

    def __init__(self) -> None:
        self.calls = 0
        self.turn_inputs: list[object] = []
        self.first_started = asyncio.Event()
        self._release_first = asyncio.Event()

    async def run_turn_stream(self, runtime, turn_input, *, cancel, gate=None):
        _ = runtime, gate
        self.calls += 1
        self.turn_inputs.append(turn_input)
        if self.calls == 1:
            self.first_started.set()
            yield RuntimeEvent(
                type="token",
                payload={"delta": "first"},
                turn_id=turn_input.turn_id,
            )
            # Hold until the test has enqueued "second", then complete.
            await self._release_first.wait()
            yield EngineResult(terminal=Terminal.completed, turn_id=turn_input.turn_id)
            return
        yield RuntimeEvent(
            type="token",
            payload={"delta": "drained"},
            turn_id=turn_input.turn_id,
        )
        yield EngineResult(terminal=Terminal.completed, turn_id=turn_input.turn_id)


def test_queue_drains_after_turn_completes(monkeypatch) -> None:
    async def _run() -> None:
        monkeypatch.setenv("MAGI_TUI_QUEUE", "1")
        engine = _DrainDriver()
        app = _make_app(engine, flush_interval=999)
        async with app.run_test() as pilot:
            app.start_turn("first")
            await asyncio.wait_for(engine.first_started.wait(), timeout=2)
            await pilot.pause()
            app.start_or_enqueue_turn("second")
            assert app._prompt_queue == [("second", ())]
            # Let the first turn complete; the drain is scheduled from finally
            # via call_after_refresh, so pump the loop after completion.
            engine._release_first.set()
            await app.workers.wait_for_complete()
            await pilot.pause()
            await pilot.pause()
            assert engine.calls == 2
            assert app._prompt_queue == []
            assert app._turn_active is False
            assert app._footer.queued == 0
            snapshot = app.controller.committed_blocks_snapshot()
            assert any("› second" in b for b in snapshot)

    asyncio.run(_run())


def test_queue_drains_after_engine_raises(monkeypatch) -> None:
    async def _run() -> None:
        from textual.worker import WorkerFailed

        monkeypatch.setenv("MAGI_TUI_QUEUE", "1")

        class _RaiseThenOkDriver:
            def __init__(self) -> None:
                self.calls = 0
                self.first_started = asyncio.Event()
                self._release_first = asyncio.Event()

            async def run_turn_stream(self, runtime, turn_input, *, cancel, gate=None):
                _ = runtime, gate
                self.calls += 1
                if self.calls == 1:
                    self.first_started.set()
                    yield RuntimeEvent(
                        type="token",
                        payload={"delta": "first"},
                        turn_id=turn_input.turn_id,
                    )
                    await self._release_first.wait()
                    raise RuntimeError("boom")
                yield RuntimeEvent(
                    type="token",
                    payload={"delta": "drained"},
                    turn_id=turn_input.turn_id,
                )
                yield EngineResult(
                    terminal=Terminal.completed, turn_id=turn_input.turn_id
                )

        engine = _RaiseThenOkDriver()
        captured: dict[str, object] = {}
        app = _make_app(engine, flush_interval=999)
        try:
            async with app.run_test() as pilot:
                app.start_turn("first")
                await asyncio.wait_for(engine.first_started.wait(), timeout=2)
                await pilot.pause()
                app.start_or_enqueue_turn("second")
                # Release the first turn so it RAISES; the finally-drain must
                # still pick up "second" despite the raise path.
                engine._release_first.set()
                try:
                    await app.workers.wait_for_complete()
                except WorkerFailed:
                    pass
                await pilot.pause()
                await pilot.pause()
                captured["calls"] = engine.calls
                captured["queue"] = list(app._prompt_queue)
        except (WorkerFailed, RuntimeError):
            pass
        # The queued "second" turn drained and ran despite the engine raise.
        assert captured.get("calls") == 2
        assert captured.get("queue") == []

    asyncio.run(_run())


def test_submit_with_attachment_while_busy_preserves_image(monkeypatch) -> None:
    async def _run() -> None:
        monkeypatch.setenv("MAGI_TUI_QUEUE", "1")
        engine = _DrainDriver()
        app = _make_app(engine, flush_interval=999)
        block = {"type": "image", "source": {"data": "fake"}}
        async with app.run_test() as pilot:
            app.start_turn("first")
            await asyncio.wait_for(engine.first_started.wait(), timeout=2)
            await pilot.pause()
            # User attached an image (Ctrl+V) then submitted while busy.
            app._pending_attachments.append(block)
            app.start_or_enqueue_turn("p")
            # The queue captured the attachment snapshot; the shared buffer is
            # cleared so an unrelated turn can't consume the image.
            assert app._prompt_queue == [("p", (block,))]
            assert app._pending_attachments == []
            engine._release_first.set()
            await app.workers.wait_for_complete()
            await pilot.pause()
            await pilot.pause()
            # The drained turn carried the snapshotted image into its TurnInput.
            assert engine.calls == 2
            drained_input = engine.turn_inputs[-1]
            assert block in tuple(drained_input.image_blocks)

    asyncio.run(_run())


def test_queue_cleared_on_resume(monkeypatch) -> None:
    async def _run() -> None:
        monkeypatch.setenv("MAGI_TUI_QUEUE", "1")
        engine = _BlockingFirstDriver()
        app = _make_app(engine, flush_interval=999)
        async with app.run_test() as pilot:
            app.start_turn("first")
            await pilot.pause()
            app.start_or_enqueue_turn("second")
            assert len(app._prompt_queue) == 1
            assert app._footer.queued == 1
            # A queue is turn-local intent for the OLD session; resume drops it.
            app._resume_session("other-session")
            assert app._prompt_queue == []
            assert app._footer.queued == 0

    asyncio.run(_run())


def test_prompt_command_while_busy_enqueues(monkeypatch) -> None:
    async def _run() -> None:
        from magi_agent.cli.contracts import (  # noqa: PLC0415
            ContentBlock,
            PromptCommand,
        )

        monkeypatch.setenv("MAGI_TUI_QUEUE", "1")

        class _EchoPrompt(PromptCommand):
            async def build_prompt(self, args, ctx):  # type: ignore[override]
                return [ContentBlock(type="text", text=f"expanded:{args}")]

        class _PromptRegistry:
            def __init__(self) -> None:
                self._cmd = _EchoPrompt(name="ask", surface=TUI)

            def lookup(self, name: str):
                return self._cmd if name == "ask" else None

            def list_for(self, surface):
                _ = surface
                return [self._cmd]

        engine = _BlockingFirstDriver()
        app = _make_app(engine, commands=_PromptRegistry(), flush_interval=999)
        async with app.run_test() as pilot:
            app.start_turn("first")
            await pilot.pause()
            # Prompt-command submitted while busy must ENQUEUE, not replace.
            # NOTE: the first turn worker blocks forever, so we cannot
            # ``wait_for_complete()`` (it waits on ALL workers). The command runs
            # in the separate group="command" worker; pump the loop so it drains
            # build_prompt -> the admission seam -> enqueue.
            app.submit_command("ask", "now")
            for _ in range(5):
                await pilot.pause()
            assert engine.calls == 1  # first turn NOT replaced
            assert len(app._prompt_queue) == 1
            assert app._prompt_queue[0][0] == "expanded:now"

    asyncio.run(_run())


def test_submit_while_busy_disabled_restores_replace(monkeypatch) -> None:
    async def _run() -> None:
        monkeypatch.delenv("MAGI_TUI_QUEUE", raising=False)

        class _ReplaceDriver:
            def __init__(self) -> None:
                self.calls = 0
                self.second_entered = asyncio.Event()

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
                await asyncio.Event().wait()

        engine = _ReplaceDriver()
        app = _make_app(engine, flush_interval=999)
        async with app.run_test() as pilot:
            app.start_turn("first")
            await pilot.pause()
            _submit_via_funnel(app, "second")
            # Flag OFF == today: the second REPLACES the first (exclusive worker).
            await asyncio.wait_for(engine.second_entered.wait(), timeout=2)
            assert app._prompt_queue == []
            assert app._turn_active is True

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


def test_tab_accepts_highlighted_completion() -> None:
    """Tab substitutes the highlighted completion (+ trailing space) and dismisses
    the overlay — so a long skill name is completed instead of hand-typed."""

    async def _run() -> None:
        engine = FakeEngineDriver()
        registry = FakeRegistry(["compact", "reset", "status"])
        app = _make_app(engine, commands=registry)
        async with app.run_test() as pilot:
            app._input.focus()
            await pilot.pause()
            await pilot.press("slash")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert app.completions_active()
            await pilot.press("tab")
            await pilot.pause()
        # Top candidate (lexicographic on empty fragment) is "/compact".
        assert app._input.text == "/compact "
        assert not app._completions.has_class("visible")

    asyncio.run(_run())


def test_arrow_navigates_then_tab_accepts() -> None:
    """↓ moves the highlight while the overlay is open; Tab then accepts it."""

    async def _run() -> None:
        engine = FakeEngineDriver()
        registry = FakeRegistry(["compact", "reset", "status"])
        app = _make_app(engine, commands=registry)
        async with app.run_test() as pilot:
            app._input.focus()
            await pilot.pause()
            await pilot.press("slash")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert app._completion_index == 0
            await pilot.press("down")
            await pilot.pause()
            assert app._completion_index == 1
            await pilot.press("tab")
            await pilot.pause()
        assert app._input.text == "/reset "

    asyncio.run(_run())


def test_escape_dismisses_completions_without_substituting() -> None:
    async def _run() -> None:
        engine = FakeEngineDriver()
        registry = FakeRegistry(["compact", "reset", "status"])
        app = _make_app(engine, commands=registry)
        async with app.run_test() as pilot:
            app._input.focus()
            await pilot.pause()
            await pilot.press("slash")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert app.completions_active()
            await pilot.press("escape")
            await pilot.pause()
            assert not app._completions.has_class("visible")
            # Text is untouched (no substitution on Esc).
            assert app._input.text == "/"

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


# ---------------------------------------------------------------------------
# PR3.1 — StatusFooter wiring
# ---------------------------------------------------------------------------
def test_footer_reflects_turn_state_and_token_usage() -> None:
    async def _run() -> None:
        from magi_agent.cli.tui.footer import StatusFooter

        class _UsageEngine(FakeEngineDriver):
            async def run_turn_stream(self, runtime, turn_input, *, cancel, gate=None):
                turn_id = getattr(turn_input, "turn_id", "t")
                yield RuntimeEvent(type="token", payload={"delta": "hi"}, turn_id=turn_id)
                yield EngineResult(
                    terminal=Terminal.completed,
                    usage={"input_tokens": 100, "output_tokens": 23},
                    turn_id=turn_id,
                )

        app = _make_app(_UsageEngine())
        async with app.run_test() as pilot:
            footer = app.query_one("#footer", StatusFooter)
            # Idle before any turn.
            assert "idle" in footer.status_text()
            app.start_turn("hi")
            await app.workers.wait_for_complete()
            await pilot.pause()
            text = footer.status_text()
        assert "completed" in text
        # 100 + 23 = 123 tokens summed from the terminal usage dict.
        assert "123 tok" in text

    asyncio.run(_run())


def test_footer_shows_queued_badge_during_turn(monkeypatch) -> None:
    async def _run() -> None:
        from magi_agent.cli.tui.footer import StatusFooter

        monkeypatch.setenv("MAGI_TUI_QUEUE", "1")
        engine = _BlockingFirstDriver()
        app = _make_app(engine, flush_interval=999)
        async with app.run_test() as pilot:
            footer = app.query_one("#footer", StatusFooter)
            app.start_turn("first")
            await pilot.pause()
            app.start_or_enqueue_turn("second")
            await pilot.pause()
            assert app._footer.queued == 1
            assert " · 1 queued" in footer.status_text()

    asyncio.run(_run())


def test_footer_elapsed_ticks_during_turn() -> None:
    async def _run() -> None:
        import asyncio as _asyncio

        class _SlowEngine(FakeEngineDriver):
            async def run_turn_stream(self, runtime, turn_input, *, cancel, gate=None):
                turn_id = getattr(turn_input, "turn_id", "t")
                # Stream slowly so the footer tick fires at least once mid-turn.
                for tok in ("a", "b", "c"):
                    await _asyncio.sleep(0.02)
                    yield RuntimeEvent(type="token", payload={"delta": tok}, turn_id=turn_id)
                yield EngineResult(terminal=Terminal.completed, turn_id=turn_id)

        app = _make_app(_SlowEngine(), flush_interval=0.01)
        async with app.run_test() as pilot:
            app.start_turn("go")
            await pilot.pause(0.015)
            first = app._footer.elapsed
            await pilot.pause(0.03)
            second = app._footer.elapsed
            await app.workers.wait_for_complete()
            await pilot.pause()
        assert second > first

    asyncio.run(_run())


def test_footer_resets_when_turn_worker_raises() -> None:
    """If the engine RAISES (vs yielding an error terminal), the footer must not
    get stuck on "running" and the elapsed clock must stop.

    Without the ``_run_turn`` error cleanup the exception propagates past
    ``_render_terminal`` (which never runs), so the footer stays on "running"
    and ``_turn_started_monotonic`` stays set — this test asserts the opposite.
    """

    async def _run() -> None:
        from magi_agent.cli.tui.footer import StatusFooter

        class _RaisingEngine(FakeEngineDriver):
            async def run_turn_stream(self, runtime, turn_input, *, cancel, gate=None):
                # Async generator that raises before yielding any item — the
                # engine never produces a terminal EngineResult.
                if False:  # pragma: no cover - makes this an async generator
                    yield None
                raise RuntimeError("engine boom")

        from textual.worker import WorkerFailed

        captured: dict[str, object] = {}
        app = _make_app(_RaisingEngine())
        try:
            async with app.run_test() as pilot:
                footer = app.query_one("#footer", StatusFooter)
                app.start_turn("go")
                # The turn worker RAISES (propagation is preserved per the fix):
                # ``wait_for_complete`` re-raises it as ``WorkerFailed``, and the
                # ``run_test`` context re-raises the app panic on exit. We swallow
                # only those here — the point is the footer was cleaned up DESPITE
                # the raise, sampled BEFORE the context tears down.
                try:
                    await app.workers.wait_for_complete()
                except WorkerFailed:
                    pass
                await pilot.pause()
                captured["state"] = footer.state
                captured["started"] = app._turn_started_monotonic
        except (WorkerFailed, RuntimeError):
            pass
        # Footer must NOT be stuck on "running" and the elapsed clock stopped.
        assert captured.get("state") != "running"
        assert captured.get("state") is not None  # the turn actually ran
        assert captured.get("started") is None

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# PR3.2 — Sidebar: mount hidden, ctrl+b toggles visibility
# ---------------------------------------------------------------------------
def test_ctrl_b_toggles_sidebar_visibility() -> None:
    async def _run() -> None:
        app = _make_app(FakeEngineDriver())
        async with app.run_test() as pilot:
            sidebar = app.query_one("#sidebar")
            # Hidden on mount.
            assert sidebar.display is False
            await pilot.press("ctrl+b")
            await pilot.pause()
            assert sidebar.display is True
            await pilot.press("ctrl+b")
            await pilot.pause()
            assert sidebar.display is False

    asyncio.run(_run())


def test_ctrl_b_binding_present_and_no_collision() -> None:
    """ctrl+b is a real App BINDING and collides with nothing else.

    It must not duplicate ctrl+c / ctrl+y / f1 (the other App BINDINGS) nor any
    keybindings-default keystroke (defaults.py) — those route through the
    resolver before BINDINGS, so a collision would silently shadow the toggle.
    """

    from textual.binding import Binding

    from magi_agent.cli.keybindings.defaults import DEFAULT_SPEC

    def _key(binding: object) -> str:
        if isinstance(binding, Binding):
            return binding.key
        return binding[0]  # bare ("key", action, desc) tuple

    keys = [_key(b) for b in MagiTuiApp.BINDINGS]
    assert keys.count("ctrl+b") == 1, "ctrl+b must be bound exactly once"
    # No clash with the keybindings-default keystrokes (which win in on_key).
    default_keys = {chord for _ctx, chord, _action in DEFAULT_SPEC}
    assert "ctrl+b" not in default_keys


def test_sidebar_panes_fed_from_tool_and_terminal_events() -> None:
    async def _run() -> None:
        class _ToolEngine(FakeEngineDriver):
            async def run_turn_stream(self, runtime, turn_input, *, cancel, gate=None):
                turn_id = getattr(turn_input, "turn_id", "t")
                yield RuntimeEvent(
                    type="tool",
                    payload={
                        "type": "tool_start",
                        "name": "TodoWrite",
                        "input": {
                            "todos": [
                                {"content": "step one"},
                                {"content": "step two"},
                            ]
                        },
                    },
                    turn_id=turn_id,
                )
                yield RuntimeEvent(
                    type="tool",
                    payload={
                        "type": "tool_start",
                        "name": "Read",
                        "input": {"path": "lib/x.py"},
                    },
                    turn_id=turn_id,
                )
                yield EngineResult(
                    terminal=Terminal.completed,
                    usage={"input_tokens": 500, "output_tokens": 40},
                    turn_id=turn_id,
                )

        app = _make_app(_ToolEngine())
        async with app.run_test() as pilot:
            app.start_turn("do work")
            await app.workers.wait_for_complete()
            await pilot.pause()
            sidebar = app.query_one("#sidebar")
            text = sidebar.panes_text()
        assert "step one" in text
        assert "step two" in text
        assert "x.py" in text  # recent-files pane shows the shortened basename
        assert "540 tokens" in text  # honest bare token count (no false ratio)
        assert "200,000" not in text  # NOT a ratio against a hardcoded budget

    asyncio.run(_run())


def test_sidebar_todowrite_empty_list_clears_todos() -> None:
    async def _run() -> None:
        class _ToolEngine(FakeEngineDriver):
            async def run_turn_stream(self, runtime, turn_input, *, cancel, gate=None):
                turn_id = getattr(turn_input, "turn_id", "t")
                yield RuntimeEvent(
                    type="tool",
                    payload={
                        "type": "tool_start",
                        "name": "TodoWrite",
                        "input": {"todos": [{"content": "step one"}]},
                    },
                    turn_id=turn_id,
                )
                yield RuntimeEvent(
                    type="tool",
                    payload={
                        "type": "tool_start",
                        "name": "TodoWrite",
                        "input": {"todos": []},
                    },
                    turn_id=turn_id,
                )
                yield EngineResult(terminal=Terminal.completed, turn_id=turn_id)

        app = _make_app(_ToolEngine())
        async with app.run_test() as pilot:
            app.start_turn("clear todos")
            await app.workers.wait_for_complete()
            await pilot.pause()
            sidebar = app.query_one("#sidebar")
            text = sidebar.panes_text()
        assert "step one" not in text
        assert "Todo\n  (none)" in text

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# PR3.3 — Permission-modal diff preview for Edit/Write + toasts on copy failure
# ---------------------------------------------------------------------------
def test_perm_modal_shows_diff_preview_for_edit() -> None:
    async def _run() -> None:
        class _EditAskEngine(FakeEngineDriver):
            async def run_turn_stream(self, runtime, turn_input, *, cancel, gate=None):
                turn_id = getattr(turn_input, "turn_id", "t")
                req = ControlRequest(
                    requestId="req-1",
                    turnId=turn_id,
                    toolName="Edit",
                    arguments={
                        "path": "x.py",
                        "old_string": "a = 1\nb = 2\n",
                        "new_string": "a = 1\nb = 3\n",
                    },
                    reason="edit a file",
                )
                self.gate_decision = await gate.check(req)
                yield EngineResult(terminal=Terminal.completed, turn_id=turn_id)

        app = _make_app(_EditAskEngine())
        async with app.run_test() as pilot:
            app._gate = SinkGate(app.sink)
            app.start_turn("edit it")
            await pilot.pause()
            await pilot.pause()
            assert isinstance(app.screen, ToolUseConfirm)
            # The diff-preview panel exists and is visible (only for Edit/Write).
            preview = app.screen.query_one("#tool-diff-preview")
            assert preview.display is True
            await pilot.click("#allow")
            await app.workers.wait_for_complete()
            await pilot.pause()
        assert app.last_terminal.terminal == Terminal.completed

    asyncio.run(_run())


def test_perm_modal_no_diff_preview_for_non_edit() -> None:
    async def _run() -> None:
        engine = FakeEngineDriver(tokens=["working"], ask_tool="Bash")
        app = _make_app(engine)
        async with app.run_test() as pilot:
            app._gate = SinkGate(app.sink)
            app.start_turn("run something")
            await pilot.pause()
            await pilot.pause()
            assert isinstance(app.screen, ToolUseConfirm)
            preview = app.screen.query_one("#tool-diff-preview")
            assert preview.display is False  # Bash has no old/new -> hidden
            await pilot.click("#deny")
            await app.workers.wait_for_complete()
            await pilot.pause()

    asyncio.run(_run())


def test_perm_modal_shows_diff_preview_for_write_content() -> None:
    async def _run() -> None:
        class _WriteAskEngine(FakeEngineDriver):
            async def run_turn_stream(self, runtime, turn_input, *, cancel, gate=None):
                turn_id = getattr(turn_input, "turn_id", "t")
                req = ControlRequest(
                    requestId="req-w",
                    turnId=turn_id,
                    toolName="Write",
                    arguments={"path": "new.py", "content": "print('hi')\n"},
                    reason="write a file",
                )
                self.gate_decision = await gate.check(req)
                yield EngineResult(terminal=Terminal.completed, turn_id=turn_id)

        app = _make_app(_WriteAskEngine())
        async with app.run_test() as pilot:
            app._gate = SinkGate(app.sink)
            app.start_turn("write it")
            await pilot.pause()
            await pilot.pause()
            assert isinstance(app.screen, ToolUseConfirm)
            preview = app.screen.query_one("#tool-diff-preview")
            assert preview.display is True  # empty -> content renders as added lines
            await pilot.click("#deny")
            await app.workers.wait_for_complete()
            await pilot.pause()

    asyncio.run(_run())


def test_copy_selection_failure_surfaces_toast() -> None:
    async def _run() -> None:
        app = _make_app(FakeEngineDriver())
        captured: list[tuple[str, str]] = []

        async with app.run_test() as pilot:
            await pilot.pause()

            def _boom(text):
                raise RuntimeError("clipboard unavailable")

            def _capture(message, *, severity="information", timeout=None):
                captured.append((message, severity))

            app.copy_to_clipboard = _boom  # type: ignore[method-assign]
            app.notify = _capture  # type: ignore[method-assign]
            # Force a non-empty selection path.
            app.screen.get_selected_text = lambda: "some text"  # type: ignore[attr-defined]
            app.action_copy_selection()
            await pilot.pause()
        assert any(sev == "warning" for _msg, sev in captured)

    asyncio.run(_run())


def test_copy_selection_empty_surfaces_info_toast() -> None:
    async def _run() -> None:
        app = _make_app(FakeEngineDriver())
        captured: list[tuple[str, str]] = []

        async with app.run_test() as pilot:
            await pilot.pause()

            def _capture(message, *, severity="information", timeout=None):
                captured.append((message, severity))

            app.notify = _capture  # type: ignore[method-assign]
            # Nothing selected -> info toast, not a silent return.
            app.screen.get_selected_text = lambda: ""  # type: ignore[attr-defined]
            app.action_copy_selection()
            await pilot.pause()
        assert any(sev == "information" for _msg, sev in captured)

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# PR3.4 — focus-aware attention bell on turn-done / permission-needed.
# ---------------------------------------------------------------------------
def test_app_tracks_focus_on_blur_and_focus() -> None:
    async def _run() -> None:
        app = _make_app(FakeEngineDriver())
        async with app.run_test() as pilot:
            await pilot.pause()
            # Default: focused after mount.
            assert app.app_is_focused is True
            app.on_app_blur(None)
            assert app.app_is_focused is False
            app.on_app_focus(None)
            assert app.app_is_focused is True

    asyncio.run(_run())


def test_turn_done_rings_bell_when_unfocused_and_enabled(monkeypatch) -> None:
    async def _run() -> None:
        from magi_agent.cli.tui import notify as _notify

        monkeypatch.setenv(_notify.BELL_ENV, "1")
        app = _make_app(FakeEngineDriver())
        rings: list[int] = []
        async with app.run_test() as pilot:
            await pilot.pause()
            app.bell = lambda: rings.append(1)  # type: ignore[method-assign]
            app.on_app_blur(None)  # go unfocused
            app.start_turn("hi")
            await app.workers.wait_for_complete()
            await pilot.pause()
        assert rings, "expected a bell on turn-done while unfocused + enabled"

    asyncio.run(_run())


def test_turn_done_no_bell_when_focused_even_if_enabled(monkeypatch) -> None:
    async def _run() -> None:
        from magi_agent.cli.tui import notify as _notify

        monkeypatch.setenv(_notify.BELL_ENV, "1")
        app = _make_app(FakeEngineDriver())
        rings: list[int] = []
        async with app.run_test() as pilot:
            await pilot.pause()
            app.bell = lambda: rings.append(1)  # type: ignore[method-assign]
            # Focused (default) -> no bell even though the gate is ON.
            app.start_turn("hi")
            await app.workers.wait_for_complete()
            await pilot.pause()
        assert not rings, "no bell should fire while the terminal is focused"

    asyncio.run(_run())


def test_turn_done_no_bell_when_env_unset_default_off(monkeypatch) -> None:
    async def _run() -> None:
        from magi_agent.cli.tui import notify as _notify

        # Default OFF: with the env unset, NO bell ever — even while unfocused.
        monkeypatch.delenv(_notify.BELL_ENV, raising=False)
        app = _make_app(FakeEngineDriver())
        rings: list[int] = []
        async with app.run_test() as pilot:
            await pilot.pause()
            app.bell = lambda: rings.append(1)  # type: ignore[method-assign]
            app.on_app_blur(None)  # unfocused, but gate is OFF
            app.start_turn("hi")
            await app.workers.wait_for_complete()
            await pilot.pause()
        assert not rings, "default-OFF gate must never ring the bell"

    asyncio.run(_run())


def test_permission_needed_rings_bell_when_unfocused_and_enabled(monkeypatch) -> None:
    async def _run() -> None:
        from magi_agent.cli.tui import notify as _notify

        monkeypatch.setenv(_notify.BELL_ENV, "1")
        engine = FakeEngineDriver(tokens=["working"], ask_tool="Bash")
        app = _make_app(engine)
        rings: list[int] = []
        async with app.run_test() as pilot:
            app._gate = SinkGate(app.sink)
            await pilot.pause()
            app.bell = lambda: rings.append(1)  # type: ignore[method-assign]
            app.on_app_blur(None)  # go unfocused
            app.start_turn("do it")
            await pilot.pause()
            await pilot.pause()
            # The permission modal is up; the bell fired before it was shown.
            assert isinstance(app.screen, ToolUseConfirm)
            await pilot.click("#deny")
            await app.workers.wait_for_complete()
            await pilot.pause()
        assert rings, "expected a bell when a permission modal opened while unfocused"

    asyncio.run(_run())


def test_footer_below_prompt_no_overlap() -> None:
    """Geometry guard: the docked footer must sit strictly below the prompt.

    The footer-below-prompt layout relies on the prompt's margin/auto height to
    reflow. Asserting the real widget regions catches a future CSS change that
    would silently overlap the footer and the prompt.
    """

    async def _run() -> None:
        engine = FakeEngineDriver()
        app = _make_app(engine)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            footer = app.query_one("#footer")
            prompt = app.query_one("#prompt")
            assert footer.region.height >= 1
            assert prompt.region.height >= 1
            # Footer strictly below the prompt — no vertical overlap.
            assert footer.region.y >= prompt.region.y + prompt.region.height

    asyncio.run(_run())


def test_footer_elapsed_resets_across_two_turns() -> None:
    """The elapsed clock is re-based each turn (it must NOT accumulate).

    ``start_turn`` re-stamps ``_turn_started_monotonic`` and ``_render_terminal``
    clears it. So each turn's elapsed is measured from that turn's own start; a
    second turn cannot carry the first turn's time.
    """

    async def _run() -> None:
        engine = FakeEngineDriver(tokens=["a", "b"])
        app = _make_app(engine)
        async with app.run_test() as pilot:
            await pilot.pause()

            # Turn 1: stamped while running, cleared after terminal.
            app.start_turn("first")
            assert app._turn_started_monotonic is not None
            stamp1 = app._turn_started_monotonic
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert app._turn_started_monotonic is None  # clock stopped/reset

            # Turn 2: a FRESH stamp (strictly later monotonic), not turn 1's.
            app.start_turn("second")
            assert app._turn_started_monotonic is not None
            assert app._turn_started_monotonic >= stamp1  # re-based, monotonic
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert app._turn_started_monotonic is None

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# PR4.4 — App loads ~/.magi/keybindings.json (overridable via MAGI_CLI_SESSION_DIR)
# ---------------------------------------------------------------------------
def test_app_loads_user_keybindings_json(tmp_path, monkeypatch) -> None:
    """A user keybindings.json under the session root is merged over defaults."""

    import json

    async def _run() -> None:
        monkeypatch.setenv("MAGI_CLI_SESSION_DIR", str(tmp_path))
        (tmp_path / "keybindings.json").write_text(
            json.dumps(
                {
                    "bindings": [
                        {"context": "Chat", "bindings": {"ctrl+s": "chat:cancel"}}
                    ]
                }
            ),
            encoding="utf-8",
        )
        engine = FakeEngineDriver()
        app = _make_app(engine)
        async with app.run_test() as pilot:
            await pilot.pause()
        # The user binding (ctrl+s -> chat:cancel) was merged after defaults.
        actions = [
            b.action
            for b in app._key_bindings
            if any(k.key == "s" and k.ctrl for k in b.chord)
        ]
        assert "chat:cancel" in actions

    asyncio.run(_run())


def test_app_keybindings_default_when_no_user_file(tmp_path, monkeypatch) -> None:
    """No user file -> defaults only (ctrl+s stays chat:stash)."""

    async def _run() -> None:
        monkeypatch.setenv("MAGI_CLI_SESSION_DIR", str(tmp_path))
        engine = FakeEngineDriver()
        app = _make_app(engine)
        async with app.run_test() as pilot:
            await pilot.pause()
        actions = [
            b.action
            for b in app._key_bindings
            if any(k.key == "s" and k.ctrl for k in b.chord)
        ]
        assert actions == ["chat:stash"]

    asyncio.run(_run())


def test_app_malformed_keybindings_falls_back(tmp_path, monkeypatch) -> None:
    """A malformed keybindings.json degrades to defaults; the app still mounts."""

    async def _run() -> None:
        monkeypatch.setenv("MAGI_CLI_SESSION_DIR", str(tmp_path))
        (tmp_path / "keybindings.json").write_text("{ not json", encoding="utf-8")
        engine = FakeEngineDriver()
        app = _make_app(engine)
        async with app.run_test() as pilot:
            await pilot.pause()
        # Defaults survived (ctrl+s -> chat:stash) and nothing crashed.
        actions = [
            b.action
            for b in app._key_bindings
            if any(k.key == "s" and k.ctrl for k in b.chord)
        ]
        assert "chat:stash" in actions


    asyncio.run(_run())


def test_app_keybindings_unknown_action_skipped(tmp_path, monkeypatch) -> None:
    """An unknown action in the user file is skipped (graceful), not crashing."""

    import json

    async def _run() -> None:
        monkeypatch.setenv("MAGI_CLI_SESSION_DIR", str(tmp_path))
        (tmp_path / "keybindings.json").write_text(
            json.dumps(
                {
                    "bindings": [
                        {
                            "context": "Chat",
                            "bindings": {
                                "ctrl+s": "chat:cancel",
                                "ctrl+r": "chat:bogusAction",
                            },
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        engine = FakeEngineDriver()
        app = _make_app(engine)
        async with app.run_test() as pilot:
            await pilot.pause()
        # The valid override merged; the bogus one was dropped (no ctrl+r binding).
        bound_keys = {
            (k.key, k.ctrl)
            for b in app._key_bindings
            for k in b.chord
        }
        assert ("s", True) in bound_keys
        assert ("r", True) not in bound_keys

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Tool rendering: tool_end inherits the tool_start name; unknown tools get a
# named header instead of a bare dot (CC-style detail parity)
# ---------------------------------------------------------------------------
def test_tool_end_uses_tool_start_name_for_renderer_dispatch() -> None:
    async def _run() -> None:
        from magi_agent.cli.tui.tool_render import build_tool_renderers

        class _ToolEngine(FakeEngineDriver):
            async def run_turn_stream(self, runtime, turn_input, *, cancel, gate=None):
                turn_id = getattr(turn_input, "turn_id", "t")
                yield RuntimeEvent(
                    type="tool",
                    payload={
                        "type": "tool_start",
                        "id": "call-1",
                        "name": "Bash",
                        "input_preview": '{"command": "ls -la"}',
                    },
                    turn_id=turn_id,
                )
                # tool_end carries NO name (sanitized public payload) — the app
                # must resolve it from the tool_start id.
                yield RuntimeEvent(
                    type="tool",
                    payload={
                        "type": "tool_end",
                        "id": "call-1",
                        "status": "ok",
                        "output_preview": '{"output": {"stdout": "total 0"}}',
                    },
                    turn_id=turn_id,
                )
                yield EngineResult(terminal=Terminal.completed, turn_id=turn_id)

        app = MagiTuiApp(
            engine=_ToolEngine(),
            gate=AllowGate(),
            commands=FakeRegistry(["compact"]),
            renderers=build_tool_renderers(),
        )
        async with app.run_test() as pilot:
            app.start_turn("run ls")
            await app.workers.wait_for_complete()
            await pilot.pause()
            blocks = app.controller.committed_blocks_snapshot()
        joined = "\n".join(blocks)
        # Call header renders through the real BashRenderer ("$ <command>") and
        # the result preview resolves through the SAME renderer via the
        # remembered tool_start name (not the anonymous "tool" fallback).
        assert "$ ls -la" in joined
        assert "total 0" in joined
        assert "tool:" not in joined

    asyncio.run(_run())


def test_unknown_tool_renders_named_header_with_arg() -> None:
    async def _run() -> None:
        from magi_agent.cli.tui.tool_render import build_tool_renderers

        class _ToolEngine(FakeEngineDriver):
            async def run_turn_stream(self, runtime, turn_input, *, cancel, gate=None):
                turn_id = getattr(turn_input, "turn_id", "t")
                yield RuntimeEvent(
                    type="tool",
                    payload={
                        "type": "tool_start",
                        "id": "spawn-1",
                        "name": "SpawnAgent",
                        "input_preview": '{"prompt": "calc 1+1", "persona": "general"}',
                    },
                    turn_id=turn_id,
                )
                yield EngineResult(terminal=Terminal.completed, turn_id=turn_id)

        app = MagiTuiApp(
            engine=_ToolEngine(),
            gate=AllowGate(),
            commands=FakeRegistry(["compact"]),
            renderers=build_tool_renderers(),
        )
        async with app.run_test() as pilot:
            app.start_turn("spawn one")
            await app.workers.wait_for_complete()
            await pilot.pause()
            blocks = app.controller.committed_blocks_snapshot()
        joined = "\n".join(blocks)
        assert "SpawnAgent" in joined
        assert "calc 1+1" in joined

    asyncio.run(_run())
