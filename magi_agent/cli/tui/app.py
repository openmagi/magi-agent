"""Interactive Textual App + REPL loop for the Magi CLI (PR-E2).

``MagiTuiApp`` is the interactive surface that drives turns through the SAME
engine generator the headless path uses (``EngineDriver.run_turn_stream``) — it
NEVER writes a second turn loop. It depends ONLY on the contract ABCs/Protocols
(``EngineDriver`` / ``PermissionGate`` / ``CommandRegistry`` /
``ToolRendererRegistry``) plus the PR-E1 transcript building blocks, and accepts
concrete implementations via constructor injection. It must NOT import Stream C's
``cli.permissions`` or Stream D's ``cli.commands`` — Stream F injects those.

Pieces
------
``MagiTuiApp``
    The Textual ``App``. Hosts the PR-E1 ``RichLog`` + live ``Static`` transcript
    regions (composed, not forked), a :class:`~.input.PromptInput`, and an
    autocomplete overlay. On prompt submit it runs ONE engine turn in a worker,
    folding each yielded ``RuntimeEvent`` into the transcript and stopping on the
    terminal ``EngineResult``. A per-turn ``asyncio.Event`` makes the turn
    cancellable from the UI.

``TextualSink``
    A :class:`~magi_agent.cli.contracts.PromptSink` whose ``ask`` pushes
    a :class:`ToolUseConfirm` modal and maps the user's choice to a
    ``PermissionDecision``. Stream C's gate races this sink; Stream F wires the
    real flow.

``ToolUseConfirm``
    The modal confirm screen (allow once / allow+remember / reject / edit).

Event folding
-------------
``token`` events -> ``append_delta`` on the live block. ``status`` / ``tool`` /
``artifact`` / ``control`` / ``error`` events finalize the live block (if any)
then ``commit_block`` a one-line summary (real per-tool rendering is PR-E3 — kept
minimal here). The terminal ``EngineResult`` ends the loop.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Iterable

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, OptionList, RichLog, Static, TextArea
from textual.widgets.option_list import Option

from magi_agent.cli.contracts import (
    CommandRegistry,
    CommandSurface,
    ControlRequest,
    EngineDriver,
    EngineResult,
    PermissionDecision,
    PermissionGate,
    PermissionUpdate,
    PromptSink,
    RuntimeEvent,
    Terminal,
    ToolRendererRegistry,
    TurnInput,
)
from magi_agent.cli.keybindings.loader import load_keybindings
from magi_agent.cli.keybindings.resolver import (
    Result,
    ResultKind,
    keystroke_from_event,
    resolve,
)
from magi_agent.cli.keybindings.schema import Action, Context, Keystroke, ParsedBinding
from magi_agent.cli.tui.autocomplete import (
    AutocompleteRouter,
    Completion,
    CompletionProvider,
)
from magi_agent.cli.tui.input import PromptInput, Submission
from magi_agent.cli.tui.render.markdown import render_markdown
from magi_agent.cli.tui.transcript import (
    DEFAULT_FLUSH_INTERVAL,
    TranscriptController,
)
from magi_agent.cli.tui.widgets.tool_card import ToolCard
from magi_agent.cli.tui.widgets.transcript_view import TranscriptView

__all__ = ["MagiTuiApp", "TextualSink", "ToolUseConfirm"]


def _token_text(payload: dict) -> str:
    """Extract assistant text from a ``token`` payload (mirror headless)."""

    for key in ("delta", "text"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
    return ""


def _stop(event: object) -> None:
    """Call ``event.stop()`` if present (duck-typed; no textual import needed)."""

    stop = getattr(event, "stop", None)
    if callable(stop):
        stop()


def _inner_type(payload: dict) -> str:
    """Inner payload ``type`` (``tool_start``/``tool_progress``/``tool_end``)."""

    inner = payload.get("type")
    return inner if isinstance(inner, str) else ""


def _tool_name(payload: dict) -> str:
    """Tool name from a tool RuntimeEvent payload (mirror headless)."""

    name = payload.get("name")
    return name if isinstance(name, str) and name else "tool"


def _tool_input(payload: dict) -> object:
    """Best-effort tool input for a tool_use block (mirror headless._tool_input)."""

    for key in ("input", "arguments", "input_preview", "inputPreview"):
        if key in payload:
            value = payload[key]
            if isinstance(value, str):
                try:
                    return json.loads(value)
                except (ValueError, TypeError):
                    return value
            return value
    return {}


def _tool_result(payload: dict) -> object:
    """Best-effort tool result for a tool_end block (output preview)."""

    for key in ("output", "output_preview", "outputPreview", "result"):
        if key in payload:
            value = payload[key]
            if isinstance(value, str):
                try:
                    return json.loads(value)
                except (ValueError, TypeError):
                    return value
            return value
    return {}


# tool_end statuses that mean the tool was NOT executed (-> render_rejected).
_REJECTED_STATUSES = {"rejected", "blocked", "denied", "deny", "error"}


def _is_rejected_end(payload: dict) -> bool:
    status = payload.get("status")
    return (isinstance(status, str) and status in _REJECTED_STATUSES) or bool(
        payload.get("interrupted")
    )


def _status_summary(event: RuntimeEvent) -> str:
    """A minimal one-line summary for a non-token event (PR-E3 does real render)."""

    payload = event.payload
    label = payload.get("label") or payload.get("type") or payload.get("phase")
    name = payload.get("name")
    parts = [f"[{event.type}]"]
    if isinstance(name, str) and name:
        parts.append(name)
    if isinstance(label, str) and label:
        parts.append(str(label))
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Modal confirm screen
# ---------------------------------------------------------------------------
class ToolUseConfirm(ModalScreen[PermissionDecision]):
    """Modal asking the operator to approve/deny a tool use.

    All four spec outcomes are reachable through this modal; it dismisses with a
    :class:`PermissionDecision`:

    * ``allow`` -> allow once (``#allow``)
    * ``allow-remember`` -> allow + a remember-rule ``PermissionUpdate``
      (``#allow-remember``)
    * ``allow`` + ``updated_input`` -> the operator edits the tool ``arguments``
      JSON in an inline editor and confirms (``#edit`` -> ``#edit-confirm``)
    * ``deny`` -> reject, optionally carrying ``feedback`` text. Plain reject
      (``#deny``) sends no feedback; "Reject with reason" (``#deny-feedback`` ->
      ``#deny-confirm``) captures a reason and sets ``feedback``.

    The base view shows the action buttons; ``#edit`` and ``#deny-feedback`` swap
    in a small editor sub-view (a ``TextArea`` / ``Input``) and a confirm button
    so the edit-input and reject+feedback flows are real UI paths, not just
    programmatic seams.
    """

    # Each action is reachable by its number (1-5), a mnemonic letter, or by
    # focusing the row (Up/Down) and pressing Enter; Escape rejects. ``show`` is
    # off so the chooser stays uncluttered (the on-screen hint line lists keys).
    BINDINGS = [
        ("escape", "deny", "Reject"),
        Binding("1", "pick('allow')", "Allow", show=False),
        Binding("a", "pick('allow')", "Allow", show=False),
        Binding("2", "pick('allow-remember')", "Allow + remember", show=False),
        Binding("3", "pick('edit')", "Edit", show=False),
        Binding("e", "pick('edit')", "Edit", show=False),
        Binding("4", "pick('deny')", "Reject", show=False),
        Binding("r", "pick('deny')", "Reject", show=False),
        Binding("5", "pick('deny-feedback')", "Reject with reason", show=False),
        Binding("up", "focus_choice(-1)", "Up", show=False),
        Binding("down", "focus_choice(1)", "Down", show=False),
    ]

    # Action rows, in display order: (button id, label). The number shown is the
    # 1-based position, so labels and key bindings stay in lockstep.
    _CHOICES: tuple[tuple[str, str], ...] = (
        ("allow", "Allow once"),
        ("allow-remember", "Allow + remember"),
        ("edit", "Edit input"),
        ("deny", "Reject"),
        ("deny-feedback", "Reject with reason"),
    )

    def __init__(self, req: ControlRequest) -> None:
        super().__init__()
        self._req = req
        # Last inline error surfaced on a failed edit-input parse (test seam).
        self.last_error: str = ""

    def compose(self) -> ComposeResult:
        with Vertical(id="tool-confirm"):
            yield Static(
                f"Allow tool: {self._req.tool_name}\n{self._req.reason}",
                id="tool-confirm-msg",
            )
            with Vertical(id="confirm-actions"):
                for index, (choice_id, label) in enumerate(self._CHOICES, start=1):
                    yield Button(f"{index}. {label}", id=choice_id)
            yield Static(
                "↑/↓ move · Enter or number select · Esc reject",
                id="confirm-hint",
            )
            # Inline edit-input editor (hidden until "Edit input" is pressed).
            with Vertical(id="edit-view", classes="confirm-subview"):
                yield Static("Edit tool arguments (JSON):", id="edit-label")
                yield TextArea(self._arguments_json(), id="edit-area")
                yield Static("", id="edit-error")
                yield Button("Submit edit", id="edit-confirm", variant="success")
            # Inline reject-reason input (hidden until "Reject with reason").
            with Vertical(id="deny-view", classes="confirm-subview"):
                yield Static("Reason for rejection:", id="deny-label")
                yield Input(placeholder="why is this rejected?", id="deny-reason")
                yield Button("Submit reject", id="deny-confirm", variant="error")

    def on_mount(self) -> None:
        # Sub-views start hidden; the action buttons are the default view.
        self.query_one("#edit-view").display = False
        self.query_one("#deny-view").display = False
        # Focus the first action so Enter/Up/Down work without a click.
        self.query_one("#allow", Button).focus()

    def _arguments_json(self) -> str:
        try:
            return json.dumps(dict(self._req.arguments), indent=2, ensure_ascii=False)
        except (TypeError, ValueError):  # pragma: no cover - defensive
            return "{}"

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self._activate(event.button.id or "deny")

    def _activate(self, choice: str) -> None:
        """Route a chosen action (from a click or a key) to its outcome."""

        if choice == "edit":
            self._show_subview("edit")
            return
        if choice == "deny-feedback":
            self._show_subview("deny")
            return
        if choice == "edit-confirm":
            decision = self._decide_edit()
            if decision is None:
                return  # parse error surfaced; keep the modal open
            self.dismiss(decision)
            return
        if choice == "deny-confirm":
            self.dismiss(self._decide_deny_feedback())
            return
        self.dismiss(self._decide(choice))

    def action_pick(self, choice: str) -> None:
        """Keyboard shortcut: select an action row by number/letter.

        No-op while an inline sub-view (edit/reject-reason) is open so digits and
        letters typed into those editors are never hijacked as selections.
        """

        if self._subview_active():
            return
        self._activate(choice)

    def action_focus_choice(self, delta: int) -> None:
        """Move focus between action rows with Up/Down (wrapping)."""

        if self._subview_active():
            return
        ids = [choice_id for choice_id, _ in self._CHOICES]
        focused = self.focused
        current = focused.id if isinstance(focused, Button) else None
        index = ids.index(current) if current in ids else 0
        target = ids[(index + delta) % len(ids)]
        self.query_one(f"#{target}", Button).focus()

    def _subview_active(self) -> bool:
        return bool(
            self.query_one("#edit-view").display
            or self.query_one("#deny-view").display
        )

    def action_deny(self) -> None:
        self.dismiss(PermissionDecision(kind="deny"))

    def _show_subview(self, which: str) -> None:
        # Hide the base action buttons + chooser hint so the sub-view stands alone.
        self.query_one("#confirm-actions").display = False
        self.query_one("#confirm-hint").display = False
        self.query_one("#edit-view").display = which == "edit"
        self.query_one("#deny-view").display = which == "deny"
        if which == "edit":
            self.query_one("#edit-error", Static).update("")
            self.query_one("#edit-area").focus()
        else:
            self.query_one("#deny-reason").focus()

    def _decide_edit(self) -> PermissionDecision | None:
        """Parse the edited arguments JSON into ``updated_input``.

        Returns ``None`` (and surfaces an inline error, leaving the modal open)
        when the text is not a JSON object.
        """

        raw = self.query_one("#edit-area", TextArea).text
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            self._surface_error(f"Invalid JSON: {exc}")
            return None
        if not isinstance(parsed, dict):
            self._surface_error("Arguments must be a JSON object.")
            return None
        self.last_error = ""
        return PermissionDecision(kind="allow", updated_input=parsed)

    def _surface_error(self, message: str) -> None:
        self.last_error = message
        self.query_one("#edit-error", Static).update(message)

    def _decide_deny_feedback(self) -> PermissionDecision:
        feedback = self.query_one("#deny-reason", Input).value.strip()
        return PermissionDecision(kind="deny", feedback=feedback or None)

    def _decide(self, choice: str) -> PermissionDecision:
        if choice == "allow":
            return PermissionDecision(kind="allow")
        if choice == "allow-remember":
            return PermissionDecision(
                kind="allow",
                updates=[
                    PermissionUpdate(
                        tool=self._req.tool_name, matcher="*", decision="allow"
                    )
                ],
            )
        return PermissionDecision(kind="deny")


# ---------------------------------------------------------------------------
# Permission sink
# ---------------------------------------------------------------------------
class TextualSink(PromptSink):
    """A :class:`PromptSink` that raises the TUI confirm modal on ``ask``.

    Stream C's gate races this sink. ``ask`` pushes a :class:`ToolUseConfirm`
    screen and awaits the operator's choice, returning the resulting
    :class:`PermissionDecision`. Cancellation (the gate cancelling a losing sink)
    propagates as ``asyncio.CancelledError`` cleanly.
    """

    def __init__(self, app: "MagiTuiApp") -> None:
        self._app = app

    async def ask(self, req: ControlRequest) -> PermissionDecision:
        # On app teardown the modal can resolve ``None`` (the screen is popped
        # without a decision). The contract is ``-> PermissionDecision``, so fail
        # safe: anything that is not a real decision becomes a deny.
        decision = await self._app.push_screen_wait(ToolUseConfirm(req))
        return decision if isinstance(decision, PermissionDecision) else PermissionDecision(kind="deny")


# ---------------------------------------------------------------------------
# The App
# ---------------------------------------------------------------------------
class MagiTuiApp(App[None]):
    """Interactive REPL driving turns through the injected engine driver."""

    TITLE = "Magi"
    SUB_TITLE = "Local agent"

    # Terminal-native + Textual text selection (drag to select). Default is on;
    # set explicitly so the transcript is always selectable/copyable.
    ALLOW_SELECT = True

    CSS = """
    #topbar {
        dock: top;
        height: 1;
        padding: 0 1;
        background: $primary-darken-2;
        color: $text;
    }
    #transcript { height: 1fr; padding: 0 1; background: $background; }
    #live { height: auto; padding: 0 1; background: $background; }
    #prompt {
        dock: bottom;
        margin: 1 1;
        padding: 0 1;
        border: round $primary;
        background: $surface;
        height: auto;
    }
    #prompt:focus { border: round $accent; }
    #completions {
        dock: bottom;
        height: auto;
        max-height: 10;
        margin: 0 1;
        display: none;
    }
    #completions.visible { display: block; }
    ToolUseConfirm { align: center middle; }
    #tool-confirm {
        width: 72;
        max-width: 90%;
        height: auto;
        padding: 1 2;
        background: $surface;
        border: round $primary;
    }
    #tool-confirm-msg { margin-bottom: 1; color: $text; }
    #confirm-actions { height: auto; }
    #confirm-actions Button {
        width: 100%;
        height: 1;
        min-width: 0;
        border: none;
        margin: 0;
        padding: 0 1;
        background: transparent;
        color: $text;
        content-align: left middle;
        text-align: left;
    }
    #confirm-actions Button:focus {
        background: $primary;
        color: $text;
        text-style: bold;
    }
    #confirm-hint { margin-top: 1; color: $text-muted; }
    .confirm-subview { height: auto; }
    #edit-area { height: 8; }
    #edit-error { color: $error; }
    """

    BINDINGS = [
        ("ctrl+c", "cancel_turn", "Cancel"),
        ("ctrl+y", "copy_selection", "Copy"),
    ]

    def __init__(
        self,
        *,
        engine: EngineDriver,
        gate: PermissionGate,
        commands: CommandRegistry,
        renderers: ToolRendererRegistry,
        runtime: object | None = None,
        session_id: str = "cli-session",
        model: str | None = None,
        mode: str = "act",
        cwd: str | None = None,
        file_provider: CompletionProvider | Callable[[str], Iterable[str]] | None = None,
        channel_provider: (
            CompletionProvider | Callable[[str], Iterable[str]] | None
        ) = None,
        flush_interval: float = DEFAULT_FLUSH_INTERVAL,
    ) -> None:
        super().__init__()
        self._engine = engine
        self._gate = gate
        self._commands = commands
        self._renderers = renderers
        self._runtime = runtime
        self._session_id = session_id
        self._model = model
        self._mode = mode
        import os as _os  # noqa: PLC0415

        self._cwd = cwd if cwd is not None else _os.getcwd()
        # Coalescing cadence for the live-block flush timer (see on_mount).
        self._flush_interval = max(0.0, float(flush_interval))
        # Per-turn cancellation event; recreated each turn.
        self._cancel: asyncio.Event = asyncio.Event()
        self._turn_seq = 0
        # True while an engine turn is in flight; gates Ctrl+C (cancel vs quit).
        self._turn_active = False
        self._active_turn_id: str | None = None
        # Keybindings: defaults-only (no user keybindings.json wired in v1).
        # load_keybindings(None) never raises and returns the built-in keymap.
        # VIM mode + hot-reload are explicitly DEFERRED to v1.1.
        self._key_bindings: list[ParsedBinding] = load_keybindings(None)[0]
        self._pending: tuple[Keystroke, ...] | None = None
        # The permission sink the engine's gate can race (Stream C/F wire it in).
        self.sink: TextualSink = TextualSink(self)
        self._router = AutocompleteRouter(
            commands=commands,
            file_provider=file_provider,
            channel_provider=channel_provider,
        )
        # Wired in compose/on_mount. Exactly ONE of ``_log`` (legacy RichLog) /
        # ``_view`` (new TranscriptView widget list) is populated, selected by
        # ``_legacy_richlog`` (the MAGI_TUI_LEGACY_RICHLOG escape hatch, PR0.3).
        self._topbar: Static | None = None
        self._log: RichLog | None = None
        self._view: TranscriptView | None = None
        self._live: Static | None = None
        self._input: PromptInput | None = None
        self._completions: OptionList | None = None
        self._controller: TranscriptController | None = None
        # Terminal of the most recent turn (asserted by tests).
        self.last_terminal: EngineResult | None = None
        # Last renderable handed to commit_rich. Test-observation seam: Textual's
        # ``RichLog`` doesn't expose the last renderable post-update, so tests
        # read this to assert render parity. Not cruft — keep it.
        self._last_committed_renderable: object | None = None

    @staticmethod
    def _legacy_richlog() -> bool:
        """Whether the legacy RichLog backing is forced (MAGI_TUI_LEGACY_RICHLOG=1)."""

        import os  # noqa: PLC0415

        return os.environ.get("MAGI_TUI_LEGACY_RICHLOG", "") == "1"

    # -- composition --------------------------------------------------------
    def compose(self) -> ComposeResult:
        self._topbar = Static(self._topbar_text(), id="topbar")
        if self._legacy_richlog():
            self._log = RichLog(
                wrap=True, markup=False, auto_scroll=True, id="transcript"
            )
            self._log.can_focus = False
            transcript_widget: RichLog | TranscriptView = self._log
        else:
            self._view = TranscriptView(id="transcript")
            transcript_widget = self._view
        self._live = Static("", id="live")
        self._completions = OptionList(id="completions")
        self._input = PromptInput(commands=self._commands, id="prompt")
        yield self._topbar
        yield transcript_widget
        yield self._live
        yield self._completions
        yield self._input

    def _topbar_text(self) -> str:
        """The top status bar: app · model · cwd · mode."""

        import os as _os  # noqa: PLC0415

        home = _os.path.expanduser("~")
        cwd = self._cwd
        if cwd.startswith(home):
            cwd = "~" + cwd[len(home) :]
        if len(cwd) > 48:
            cwd = "…" + cwd[-47:]
        model = self._model or "no model"
        mode = (self._mode or "act").lower()
        return f"● Magi   {model}   {cwd}   [{mode}]"

    def on_mount(self) -> None:
        assert self._live is not None and (
            self._log is not None or self._view is not None
        )
        try:
            self.theme = "tokyo-night"
        except Exception:  # pragma: no cover - theme always present in textual 8.x
            pass
        if self._view is not None:
            self._controller = TranscriptController(view=self._view, live=self._live)
        else:
            self._controller = TranscriptController(log=self._log, live=self._live)
        self._controller.markdown_live = True
        self._render_welcome()
        # Coalescing flush timer: repaint buffered token deltas on a fixed
        # cadence so a pure token stream renders incrementally (not just at
        # finalize). ``flush`` is a no-op when the buffer is empty, and clears
        # the buffer atomically, so there is no churn or double-flush hazard
        # with the explicit ``flush_now`` calls. App owns the timer; Textual
        # cancels it on teardown.
        self.set_interval(self._flush_interval, self._on_flush_tick)

    def _on_flush_tick(self) -> None:
        if self._controller is not None:
            self._controller.flush()

    def _render_welcome(self) -> None:
        """Render the initial TUI state so bare ``magi`` never opens blank."""

        if self._controller is None:
            return
        command_names = [
            getattr(command, "name", "")
            for command in self._commands.list_for(CommandSurface(tui=True, headless=False))
        ]
        commands = ", ".join(f"/{name}" for name in command_names[:5] if name)
        command_line = (
            commands if commands else "type / for local commands"
        )
        from rich.text import Text  # noqa: PLC0415

        welcome = Text()
        welcome.append("● ", style="bold #7aa2f7")
        welcome.append("Welcome to Magi", style="bold")
        welcome.append("  ·  your local AI agent\n", style="dim")
        welcome.append("Type a task and press ", style="dim")
        welcome.append("Enter", style="#7aa2f7")
        welcome.append(".  ", style="dim")
        welcome.append("Ctrl+C", style="#7aa2f7")
        welcome.append(" cancels a turn.\n", style="dim")
        welcome.append("Commands: ", style="dim")
        welcome.append(command_line, style="#9ece6a")
        self._controller.commit_rich(
            welcome, text=f"Welcome to Magi  Commands: {command_line}"
        )

    @property
    def controller(self) -> TranscriptController:
        if self._controller is None:  # pragma: no cover - guarded by on_mount
            raise RuntimeError("controller not ready; app not mounted")
        return self._controller

    # -- input / submission -------------------------------------------------
    def on_prompt_input_prompt_submitted(
        self, event: PromptInput.PromptSubmitted
    ) -> None:
        self._hide_completions()
        submission = event.submission
        if submission.kind == "command":
            self._dispatch_command(submission)
            return
        self.start_turn(submission.text)

    def on_input_changed(self, event: Input.Changed) -> None:
        # Recompute completions for the current pre-cursor slice (debounced via
        # the exclusive worker so a stale async pass is discarded).
        if self._input is None:
            return
        self._refresh_completions(self._input.precursor)

    def _dispatch_command(self, submission: Submission) -> None:
        """Route a slash command through the registry (execution is Stream D/F).

        PR-E2 wires registry ``lookup`` so the App can find the command; actually
        executing each command KIND (prompt/local/widget) is Stream D's surface.
        We commit a transcript line documenting the dispatch so the wiring is
        observable; widget commands are a documented stub.
        """

        command = submission.command or self._commands.lookup(submission.command_name)
        if command is None:
            self.controller.commit_block(f"[command] unknown: /{submission.command_name}")
            return
        self.controller.commit_block(f"[command] /{submission.command_name}")

    # -- the ONE engine-driven turn loop -----------------------------------
    def start_turn(self, prompt: str) -> None:
        """Kick off a single engine turn for ``prompt`` in an exclusive worker."""

        self._turn_seq += 1
        turn_id = f"{self._session_id}-turn-{self._turn_seq}"
        cancel = asyncio.Event()
        self._cancel = cancel
        self._active_turn_id = turn_id
        self._turn_active = True
        self._echo_user(prompt)
        self._run_turn(prompt, turn_id, cancel)

    def _echo_user(self, prompt: str) -> None:
        """Echo the user's message into the transcript (CC/OpenCode style)."""

        if self._controller is None:
            return
        from rich.text import Text  # noqa: PLC0415

        block = Text()
        block.append("› ", style="bold #7aa2f7")
        block.append(prompt, style="bold")
        self._controller.commit_rich(block, text=f"› {prompt}")

    def action_copy_selection(self) -> None:
        """Copy the currently selected transcript text to the clipboard."""

        text = ""
        try:
            text = self.screen.get_selected_text() or ""  # type: ignore[attr-defined]
        except Exception:
            try:
                text = self.selected_text or ""  # type: ignore[attr-defined]
            except Exception:
                text = ""
        if text:
            try:
                self.copy_to_clipboard(text)
                self.notify("Copied selection", timeout=2)
            except Exception:
                pass

    @work(exclusive=True, group="turn")
    async def _run_turn(
        self, prompt: str, turn_id: str, cancel: asyncio.Event
    ) -> None:
        """Drive ONE ``engine.run_turn_stream`` generator, folding events.

        This is the only turn loop. The terminal ``EngineResult`` (the final
        yielded item) ends it. Cancellation is honored by the engine racing the
        per-turn cancel event.
        """

        controller = self.controller
        controller.begin_live()
        turn_input = TurnInput(
            prompt=prompt, session_id=self._session_id, turn_id=turn_id
        )
        gen = self._engine.run_turn_stream(
            self._runtime, turn_input, cancel=cancel, gate=self._gate
        )
        terminal: EngineResult | None = None
        try:
            async for item in gen:
                if isinstance(item, EngineResult):
                    terminal = item
                    break
                await self._fold_event(item)
        finally:
            await gen.aclose()
            if self._active_turn_id == turn_id:
                self._active_turn_id = None
                self._turn_active = False
        # Finalize the in-flight assistant block as markdown (commits any
        # streamed text). The plain text is preserved in the committed snapshot
        # for search fidelity.
        await controller.flush_now()
        self._finalize_assistant_markdown()
        if terminal is None:
            terminal = EngineResult(terminal=Terminal.error, error="no_terminal")
        self.last_terminal = terminal
        self._render_terminal(terminal)

    async def _fold_event(self, event: RuntimeEvent) -> None:
        """Fold one ``RuntimeEvent`` into the transcript regions."""

        controller = self.controller
        if event.type == "token":
            controller.append_delta(_token_text(event.payload))
            return
        # Non-token: close the in-flight assistant block FIRST (so streamed
        # assistant text is committed before any tool render), then render.
        await controller.flush_now()
        self._finalize_assistant_markdown()
        if event.type == "tool":
            self._render_tool_event(event)
            return
        # status / artifact / control / error -> the minimal one-line summary
        # (only TOOL events get the rich per-tool renderer treatment in F2c).
        controller.commit_block(_status_summary(event))

    def _render_tool_event(self, event: RuntimeEvent) -> None:
        """Route a TOOL event through the injected per-tool renderer registry."""

        payload = event.payload
        name = _tool_name(payload)
        renderer = self._renderers.get(name)
        inner = _inner_type(payload)
        if inner == "tool_start":
            node = renderer.render_call(_tool_input(payload))
        elif inner == "tool_progress":
            node = renderer.render_progress(_tool_result(payload) or payload)
        elif inner == "tool_end":
            if _is_rejected_end(payload):
                node = renderer.render_rejected(_tool_input(payload) or payload)
            else:
                node = renderer.render_result(_tool_result(payload))
        else:  # unknown inner type -> fall back to the one-line summary
            self.controller.commit_block(_status_summary(event))
            return
        self._commit_render_node(node, tool_name=name)

    def _commit_render_node(self, node: object, *, tool_name: str = "") -> None:
        """Commit a ``RenderNode`` as a collapsible ``ToolCard`` (widget backing)
        or a plain finalized block (legacy ``RichLog`` backing).

        The displayed/committed text is annotated with the tool name when the
        renderer's output does not already carry it (the fallback renderer emits
        only the raw input/result). The real Edit/Bash/Read renderers already
        embed their name in the header, so they are left untouched.
        """

        from magi_agent.cli.contracts import RenderNode  # noqa: PLC0415

        rich = getattr(node, "rich", None)
        text = getattr(node, "text", "") or ""
        annotated = self._annotate_tool_text(text, tool_name)
        # Widget-list backing -> collapsible ToolCard (header = annotated text).
        if self._view is not None:
            card = ToolCard.from_render_node(RenderNode(rich=rich, text=annotated))
            self.controller.commit_tool(card, text=annotated)
            return
        # Legacy RichLog backing -> the historical commit_rich/commit_block path.
        # commit_rich keeps the displayed text in the snapshot for
        # search-fidelity (what is indexed == what is shown).
        if rich is not None:
            self.controller.commit_rich(rich, text=annotated)
        else:
            self.controller.commit_block(annotated)

    @staticmethod
    def _annotate_tool_text(text: str, tool_name: str) -> str:
        if not tool_name or tool_name == "tool":
            return text
        if tool_name in text:
            return text
        return f"{tool_name}: {text}" if text else tool_name

    def _render_terminal(self, terminal: EngineResult) -> None:
        if terminal.terminal == Terminal.completed:
            return
        suffix = f": {terminal.error}" if terminal.error else ""
        self.controller.commit_block(f"[turn {terminal.terminal.value}{suffix}]")

    def _finalize_assistant_markdown(self) -> None:
        """Finalize the live assistant block as a markdown renderable.

        Mirrors ``TranscriptController.finalize_live`` but commits the assistant
        text as a Rich ``Markdown`` (via ``commit_rich``) instead of a plain
        string (``commit_block``), so headings/lists/fenced code render. The
        plain ``text=`` snapshot is preserved for search fidelity. An empty live
        block is a no-op (matches ``finalize_live``).
        """

        controller = self.controller
        text = controller.live_text
        controller.discard_live()
        if not text:
            return
        renderable = render_markdown(text)
        self._last_committed_renderable = renderable
        controller.commit_rich(renderable, text=text)

    # -- cancellation -------------------------------------------------------
    def action_cancel_turn(self) -> None:
        """Ctrl+C: cancel an in-flight turn, or quit the app when idle.

        While a turn runs, this signals the per-turn cancel event so the turn
        aborts. When no turn is in flight there is nothing to cancel, so Ctrl+C
        exits the app.
        """

        if self._active_turn_id is not None or self._turn_active:
            self._cancel.set()
        else:
            self.exit()

    # -- keybinding resolution ----------------------------------------------
    def _active_contexts(self) -> list[Context]:
        """Active keybinding contexts for an interactive REPL.

        v1 is a single-surface chat REPL, so the active stack is the Chat input
        context plus Global. (Confirmation/Autocomplete/Select contexts are
        handled by their own screens/widgets; a richer stack lands in v1.1.)
        """

        return [Context.CHAT, Context.GLOBAL]

    def on_key(self, event: object) -> None:
        """Route a key event through keystroke_from_event -> resolve -> action.

        Bound-key handling stops the event so it does not also reach the Input
        widget; UNBOUND / NONE / a cancelled chord let the event bubble so plain
        typing still reaches the prompt. VIM mode is DEFERRED to v1.1.
        """

        ks = keystroke_from_event(event)
        if ks is None:
            return  # not a usable key -> let it propagate (don't swallow typing)
        result: Result = resolve(
            ks, self._active_contexts(), self._key_bindings, self._pending
        )
        if result.kind is ResultKind.MATCH:
            self._pending = None
            _stop(event)
            self._run_key_action(result.action)
        elif result.kind is ResultKind.CHORD_STARTED:
            self._pending = result.pending
            _stop(event)
        else:
            # UNBOUND / NONE / CHORD_CANCELLED: clear any pending and let the
            # event bubble (typing reaches the Input widget).
            self._pending = None

    def _run_key_action(self, action: str | None) -> None:
        """Map a resolved closed-``Action`` value to a concrete v1 REPL behavior.

        Bound actions: ``chat:cancel`` (cancel the in-flight turn),
        ``global:quit`` (exit the app), ``chat:submit`` (submit the prompt),
        ``chat:killAgents`` (also cancels the turn). Actions with no sensible v1
        behavior (newline/stash/autocomplete/confirmation — owned by widgets or
        deferred) are no-ops. VIM + keybindings hot-reload are DEFERRED to v1.1.
        """

        if action is None:
            return
        if action in (Action.CHAT_CANCEL.value, Action.CHAT_KILL_AGENTS.value):
            self.action_cancel_turn()
        elif action == Action.GLOBAL_QUIT.value:
            self.exit()
        elif action == Action.CHAT_SUBMIT.value:
            self._submit_current_input()
        # All other Action members (CHAT_NEWLINE / CHAT_STASH / AUTOCOMPLETE_* /
        # CONFIRMATION_*) are intentionally no-ops in this v1 surface — their
        # behavior is owned by the Input/OptionList/modal widgets or deferred.

    def _submit_current_input(self) -> None:
        """Submit the current prompt-input line (classify + route, then clear)."""

        if self._input is None:
            return
        line = self._input.value
        if not line.strip():
            return
        submission = self._input.classify(line)
        self._input.value = ""
        self._input.post_message(PromptInput.PromptSubmitted(submission))

    # -- autocomplete overlay ----------------------------------------------
    def _refresh_completions(self, precursor: str) -> None:
        # The actual staleness mechanism is ``@work(exclusive=True)`` below: a
        # newer keystroke cancels any still-running compute in the same group.
        self._compute_completions(precursor)

    @work(exclusive=True, group="autocomplete")
    async def _compute_completions(self, precursor: str) -> None:
        request = self._router.route(precursor)
        # NOTE: this ``is_current`` call is effectively always True here —
        # ``route()`` just bumped the router token and nothing awaits between
        # routing and the check. Real staleness protection comes from the
        # ``@work(exclusive=True)`` decorator (it cancels superseded passes); the
        # check remains as a cheap guard should an ``await`` ever be introduced
        # before it.
        if not self._router.is_current(request):
            return
        self._show_completions(request.results)

    def _show_completions(self, results: list[Completion]) -> None:
        if self._completions is None:
            return
        self._completions.clear_options()
        if not results:
            self._hide_completions()
            return
        self._completions.add_options(
            [Option(self._format_option(c), id=str(i)) for i, c in enumerate(results)]
        )
        self._completions.add_class("visible")

    @staticmethod
    def _format_option(completion: Completion) -> str:
        if completion.ghost and completion.label.endswith(completion.ghost):
            head = completion.label[: -len(completion.ghost)]
            return f"{head}{completion.ghost}"
        return completion.label

    def _hide_completions(self) -> None:
        if self._completions is None:
            return
        self._completions.remove_class("visible")
        self._completions.clear_options()
