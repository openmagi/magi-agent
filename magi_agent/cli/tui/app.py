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

from magi_agent.cli.commands.executor import DefaultCommandExecutor
from magi_agent.cli.render.diff import render_diff
from magi_agent.cli.contracts import (
    CommandContext,
    CommandExecutor,
    CommandRegistry,
    ControlRequest,
    EngineDriver,
    EngineResult,
    PermissionDecision,
    PermissionGate,
    PermissionUpdate,
    PromptSink,
    RenderNode,
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
from magi_agent.cli.tui import notify as _notify
from magi_agent.cli.tui.history import DraftStash, InputHistory
from magi_agent.cli.tui.input import PromptInput, Submission
from magi_agent.cli.tui.footer import StatusFooter
from magi_agent.cli.tui.dialogs.help import HelpDialog
from magi_agent.cli.tui.dialogs.model import ModelPickerDialog, model_choices
from magi_agent.cli.tui.dialogs.session import (
    SessionEntry,
    SessionListDialog,
    session_entries,
)
from magi_agent.cli.tui.palette import (
    AppActionProvider,
    CommandPaletteProvider,
    ThemeProvider,
    tui_command_names,
)
from magi_agent.cli.tui.theme import (
    DEFAULT_THEME,
    MAGI_THEMES,
    load_saved_theme,
    register_magi_themes,
    save_theme,
)
from magi_agent.cli.tui.render.markdown import render_markdown
from magi_agent.cli.tui.sidebar import Sidebar
from magi_agent.cli.tui.transcript import (
    DEFAULT_FLUSH_INTERVAL,
    TranscriptController,
)
from magi_agent.cli.tui.widgets.transcript_view import TranscriptView
from magi_agent.cli.tui.widgets.whichkey import WhichKeyOverlay, chord_continuations

__all__ = ["MagiTuiApp", "TextualSink", "ToolUseConfirm"]


def _token_text(payload: dict) -> str:
    """Extract assistant text from a ``token`` payload (mirror headless)."""

    for key in ("delta", "text"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
    return ""


def _usage_tokens(usage: object) -> int:
    """Sum input+output tokens from an EngineResult.usage dict (best-effort).

    ``EngineResult.usage`` is a free-form dict; providers spell token counts
    differently. We sum the input/output split when present, else fall back to a
    pre-summed ``tokens`` / ``total_tokens`` field. Missing/non-numeric -> 0 (the
    footer then shows ``0 tok``, degrading gracefully when usage is unavailable).
    """

    if not isinstance(usage, dict):
        return 0
    split = 0
    for key in ("input_tokens", "output_tokens"):
        value = usage.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            split += int(value)
    if split > 0:
        return split
    for key in ("total_tokens", "tokens"):
        value = usage.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return max(0, int(value))
    return 0


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


def _tool_file_path(tool_input: object) -> str | None:
    """Best-effort file path from a Read/Edit/Write tool input."""

    if isinstance(tool_input, dict):
        for key in ("path", "file_path", "filePath", "file"):
            value = tool_input.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def _todo_contents(tool_input: object) -> list[str] | None:
    """Best-effort todo strings from a TodoWrite tool input.

    ``None`` means "not a TodoWrite-style payload"; an empty list is a valid
    payload and should clear the sidebar's Todo pane.
    """

    if not isinstance(tool_input, dict):
        return None
    todos = tool_input.get("todos")
    if not isinstance(todos, list):
        return None
    out: list[str] = []
    for item in todos:
        if isinstance(item, dict):
            content = item.get("content") or item.get("text")
            if isinstance(content, str) and content:
                out.append(content)
        elif isinstance(item, str) and item:
            out.append(item)
    return out


# Coarse default context-window budget (tokens) retained as a future sidebar
# seam. The current sidebar renders an honest bare token count; a future
# per-model window table can decide when a ratio is accurate enough to surface.
DEFAULT_CONTEXT_WINDOW = 200_000


def _context_limit(_model: str | None) -> int:
    """Coarse context-window budget for a future per-model context pane ratio."""

    return DEFAULT_CONTEXT_WINDOW


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


# Reasoning/thinking inline display (PR4.2). The runtime surfaces model reasoning
# to the TUI as a ``status`` event whose inner payload ``type`` is
# ``thinking_delta`` (engine ``_map_event_kind`` has no ``thinking_delta`` →
# falls through to ``status``; the engine sanitizer only lets it through under
# ``MAGI_STREAM_THINKING``). We render it as a DIM one-line ``● thinking <preview>``
# block — distinct from the teal/blue tool dots and from assistant markdown.
_THINKING_INNER_TYPES = frozenset({"thinking_delta", "thinking"})
_THINKING_PREVIEW_MAX_CHARS = 100
# Dim throughout so the reasoning line reads as quiet annotation, not output.
_THINKING_DOT_STYLE = "dim #7aa2f7"
_THINKING_LABEL_STYLE = "dim bold"
_THINKING_PREVIEW_STYLE = "dim italic"


def _is_reasoning_event(event: RuntimeEvent) -> bool:
    """True for a ``status`` event carrying a reasoning/thinking marker.

    Distinguishes user-relevant reasoning from plumbing ``status`` events
    (runner_policy_*, phase_route_*, turn_end) that stay hidden by default.
    """

    if event.type != "status":
        return False
    payload = event.payload
    if not isinstance(payload, dict):
        return False
    inner = payload.get("type")
    if isinstance(inner, str) and inner in _THINKING_INNER_TYPES:
        return True
    # Belt-and-suspenders: an explicit label/reasoning marker (forward-compatible
    # with a future status-marker shape) also counts.
    if payload.get("reasoning"):
        return True
    return payload.get("label") in {"thinking", "reasoning"}


def _reasoning_text(payload: dict) -> str:
    """Extract reasoning text from a ``thinking_delta``-style payload."""

    for key in ("delta", "text", "detail", "thinking", "reasoning"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _thinking_preview(text: str) -> str:
    """Collapse multi-line reasoning into a single terse preview line."""

    first = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            first = stripped
            break
    if not first:
        first = text.strip()
    if len(first) > _THINKING_PREVIEW_MAX_CHARS:
        first = first[: _THINKING_PREVIEW_MAX_CHARS - 1].rstrip() + "…"
    return first


def _render_thinking_node(text: str) -> RenderNode:
    """Build the DIM one-line ``● thinking  <preview>`` reasoning block.

    The committed/search text mirrors the displayed line for search fidelity
    (what is indexed == what is shown). The whole line is styled dim so it reads
    as quiet annotation, distinct from the teal/blue tool dots and from the
    assistant markdown.
    """

    from rich.text import Text  # noqa: PLC0415

    preview = _thinking_preview(text)
    rich = Text()
    rich.append("● ", style=_THINKING_DOT_STYLE)
    rich.append("thinking", style=_THINKING_LABEL_STYLE)
    search = "● thinking"
    if preview:
        rich.append("  ", style=_THINKING_PREVIEW_STYLE)
        rich.append(preview, style=_THINKING_PREVIEW_STYLE)
        search = f"● thinking  {preview}"
    return RenderNode(rich=rich, text=search)


# Subagent / child-run inline display (PR4.3). The runtime surfaces nested
# subagent (child-run) activity to the TUI as a ``status`` event whose inner
# payload ``type`` is one of ``child_started`` / ``child_progress`` /
# ``child_completed`` / ``child_cancelled`` / ``child_failed`` (engine
# ``_map_event_kind`` has no ``child_*`` member → falls through to ``status``;
# the SSE sanitizer lets them through UNCONDITIONALLY — not behind a flag like
# thinking's ``MAGI_STREAM_THINKING`` — as long as ``taskId`` survives). We render
# them as a DIM INDENTED one-line ``  ⤷ subagent <label>  <status>`` block —
# visually nested under the parent turn, distinct from the flush-left teal/blue
# tool dots and assistant markdown. Lifecycle events for the SAME task coalesce
# into one updating line (started → completed/failed).
_CHILD_INNER_STATUS = {
    "child_started": "running",
    "child_progress": "running",
    "child_completed": "completed",
    "child_cancelled": "cancelled",
    "child_failed": "failed",
}
_SUBAGENT_LABEL_MAX_CHARS = 60
# Dim throughout so the subagent line reads as quiet nested annotation.
_SUBAGENT_INDENT = "  "
_SUBAGENT_MARKER_STYLE = "dim #9ece6a"
_SUBAGENT_LABEL_STYLE = "dim bold"
_SUBAGENT_STATUS_STYLE = "dim italic"


def _is_child_event(event: RuntimeEvent) -> bool:
    """True for a ``status`` event carrying a child/subagent marker.

    Distinguishes user-relevant subagent activity from plumbing ``status``
    events (runner_policy_*, phase_route_*, turn_end) that stay hidden by
    default. Keys off the inner payload ``type`` (a ``child_*`` string),
    mirroring how ``tool`` events carry an inner ``tool_start``/``tool_end``.
    """

    if event.type != "status":
        return False
    payload = event.payload
    if not isinstance(payload, dict):
        return False
    inner = payload.get("type")
    return isinstance(inner, str) and inner in _CHILD_INNER_STATUS


def _child_task_key(payload: dict) -> str:
    """The RAW coalescing key for a subagent line: the untruncated ``taskId``.

    Used as the ``_subagent_handles`` dict key (NOT the display label) so two
    distinct taskIds that share a 59-char prefix don't collide after the label
    truncates at ``_SUBAGENT_LABEL_MAX_CHARS``. Falls back to the raw ``label``,
    then to the literal ``"subagent"`` only when no identifying field is present.
    """

    for key in ("taskId", "label"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return "subagent"


def _child_task_label(payload: dict) -> str:
    """The subagent DISPLAY label: the public ``taskId`` (or a sane fallback),
    truncated to one line. Use ``_child_task_key`` for the coalescing key."""

    label = _child_task_key(payload)
    if len(label) > _SUBAGENT_LABEL_MAX_CHARS:
        label = label[: _SUBAGENT_LABEL_MAX_CHARS - 1].rstrip() + "…"
    return label


def _child_detail(payload: dict) -> str:
    """Short dim suffix surfacing WHY a child line shows its status.

    ``child_progress`` carries ``detail`` (what the child is doing),
    ``child_failed`` carries ``errorMessage`` (the failure cause), and
    ``child_cancelled`` carries ``reason``. Without this they all render
    identically ("running"/"failed") with no cause. Truncated to the same terse
    preview cap as thinking so it stays one-line and dim.
    """

    for key in ("detail", "errorMessage", "reason"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return _thinking_preview(value)
    return ""


def _render_subagent_node(label: str, status: str, detail: str = "") -> RenderNode:
    """Build the DIM INDENTED one-line ``  ⤷ subagent <label>  <status>`` block.

    The committed/search text mirrors the displayed line for search fidelity
    (what is indexed == what is shown). The whole line is styled dim and the
    text is leading-indented so it reads as quiet activity nested under the
    parent turn, distinct from flush-left tool/assistant lines. An optional
    ``detail`` (progress detail / failure reason) is appended as a dim suffix.
    """

    from rich.text import Text  # noqa: PLC0415

    rich = Text()
    rich.append(f"{_SUBAGENT_INDENT}⤷ ", style=_SUBAGENT_MARKER_STYLE)
    rich.append(f"subagent {label}", style=_SUBAGENT_LABEL_STYLE)
    rich.append(f"  {status}", style=_SUBAGENT_STATUS_STYLE)
    search = f"{_SUBAGENT_INDENT}⤷ subagent {label}  {status}"
    if detail:
        rich.append(f"  {detail}", style=_SUBAGENT_STATUS_STYLE)
        search = f"{search}  {detail}"
    return RenderNode(rich=rich, text=search)


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
            # Diff preview (PR3.3): shows what an Edit/Write would change ABOVE
            # the action buttons so the operator approves an informed diff, not a
            # bare tool name. Hidden (display:false) for non-edit tools.
            yield Static("", id="tool-diff-preview", classes="confirm-diff")
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
        # Render the Edit/Write diff preview (hidden for non-edit tools).
        self._render_diff_preview()
        # Focus the first action so Enter/Up/Down work without a click.
        self.query_one("#allow", Button).focus()

    def _render_diff_preview(self) -> None:
        """Render an Edit/Write diff into the preview panel, else hide it."""

        preview = self.query_one("#tool-diff-preview", Static)
        rendered = self._diff_renderable()
        if rendered is None:
            preview.display = False
            return
        preview.update(rendered)
        preview.display = True

    def _diff_renderable(self) -> object | None:
        """Build a Rich diff for an Edit/Write request, else ``None``.

        Only Edit/Write requests carrying ``old_string``/``new_string``
        (or an empty old + ``content`` for Write) produce a preview. Reuses
        ``cli/render/diff.py:render_diff`` (cached, syntax-highlighted). Returns
        ``None`` for any other tool, partial/missing fields, or a no-op edit, so
        the modal stays diff-free in those cases. Never raises (a failed render
        falls back to ``None`` rather than crashing the permission prompt).
        """

        # MultiEdit carries an ``edits: [{old_string, new_string}, ...]`` array
        # rather than top-level fields, so it has no single-diff preview here;
        # rendering that array is a deferred follow-up (intentionally omitted).
        if self._req.tool_name not in ("Edit", "Write"):
            return None
        args = self._req.arguments
        if not isinstance(args, dict):
            return None
        old = args.get("old_string")
        new = args.get("new_string")
        if not isinstance(new, str):
            # Write: empty old -> full content as the "new" (added) side.
            content = args.get("content")
            if isinstance(content, str):
                old, new = "", content
            else:
                return None
        if not isinstance(old, str):
            old = ""
        if old == new:
            return None
        path = args.get("path") or args.get("file_path") or "file"
        file = path if isinstance(path, str) else "file"
        try:
            # 58 = conservative narrow-terminal-safe floor for the modal's
            # usable width (modal ``width: 72`` minus padding/borders).
            return render_diff(old, new, file=file, width=58)
        except Exception:  # pragma: no cover - never fail the modal on a diff
            return None

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
        # Gated focus-aware attention bell (PR3.4): a permission prompt while the
        # terminal is unfocused (and MAGI_TUI_NOTIFY_BELL on) rings, so an
        # away-from-keyboard operator notices a turn is blocked on them. No-op
        # when focused or the gate is off (default). Fired BEFORE the modal so
        # the cue lands as the prompt appears.
        _notify.notify_attention(
            self._app,
            focused=self._app.app_is_focused,
            reason=f"Magi: permission needed for {req.tool_name}",
        )
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
    Screen { background: transparent; }
    #topbar {
        dock: top;
        height: 1;
        padding: 0 1;
        background: transparent;
        color: $text-muted;
    }
    #transcript { height: 1fr; padding: 0 1; background: transparent; }
    #live { height: auto; padding: 0 1; background: transparent; }
    #prompt {
        dock: bottom;
        margin: 1 1;
        padding: 0 1;
        border: round $primary;
        background: transparent;
        height: auto;
    }
    #prompt:focus { border: round $accent; }
    #footer {
        dock: bottom;
        height: 1;
        padding: 0 1;
        background: transparent;
        color: $text-muted;
    }
    #completions {
        dock: bottom;
        height: auto;
        max-height: 10;
        margin: 0 1;
        display: none;
    }
    #completions.visible { display: block; }
    #sidebar {
        dock: left;
        width: 32;
        padding: 0 1;
        background: $panel;
        border-right: solid $primary-darken-2;
        display: none;
    }
    .sidebar-pane { height: auto; padding: 0 0 1 0; }
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
    #tool-diff-preview {
        height: auto;
        max-height: 12;
        margin: 0 0 1 0;
        padding: 0 1;
        background: $panel-darken-1;
    }
    .confirm-diff { width: 1fr; }
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
        # priority=True so this preempts Textual's built-in ctrl+c (which, when
        # an Input/TextArea is focused, is "copy", and otherwise only shows a
        # "press ctrl+q to quit" notice). Without priority the binding never
        # fires from the prompt and Ctrl+C appears dead.
        Binding("ctrl+c", "cancel_turn", "Cancel", priority=True),
        ("ctrl+y", "copy_selection", "Copy"),
        # ctrl+t cycles the curated theme set (PR4.1). priority=True so it
        # preempts any built-in ctrl+t on the focused Input/TextArea; it is NOT
        # in the keybindings defaults, so on_key resolves it UNBOUND and lets it
        # bubble to this App BINDING.
        Binding("ctrl+t", "cycle_theme", "Theme", priority=True),
        # ctrl+b toggles the left sidebar (PR3.2). priority=True so it preempts
        # any built-in ctrl+b on the focused Input/TextArea (some widgets map it
        # to cursor-left); it is NOT in the keybindings defaults (defaults.py),
        # so on_key resolves it UNBOUND and lets it bubble to this App BINDING.
        Binding("ctrl+b", "toggle_sidebar", "Sidebar", priority=True),
        # F1 opens the help dialog. F1 is not in the keybindings defaults
        # (defaults.py) and (unlike "?") never collides with typed prompt text,
        # so it is safe to bind globally while the prompt input is focused.
        ("f1", "open_help", "Help"),
    ]

    # Textual's built-in command palette (PR2.1). Ctrl+P is already the 8.2.7
    # default (verified: ``App.COMMAND_PALETTE_BINDING == "ctrl+p"``); pin it
    # explicitly (OQ2) so a future Textual default change can't silently move it,
    # and so it documents intent. No collision with BINDINGS (ctrl+c / ctrl+y)
    # or the keybindings defaults.
    COMMANDS = {CommandPaletteProvider, AppActionProvider, ThemeProvider}
    COMMAND_PALETTE_BINDING = "ctrl+p"

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
        executor: CommandExecutor | None = None,
        flush_interval: float = DEFAULT_FLUSH_INTERVAL,
        clipboard_reader: Callable[[], dict | None] | None = None,
    ) -> None:
        super().__init__()
        self._engine = engine
        self._gate = gate
        self._commands = commands
        # Injected slash-command executor (PR2.2). Defaults to the builtin
        # DefaultCommandExecutor which maps prompt/local/widget kinds onto the
        # app openers without ever starting a second engine loop.
        self._executor: CommandExecutor = executor or DefaultCommandExecutor()
        # Clipboard image reader (injectable for tests; real default avoids importing
        # clipboard libs at module level).  ``_pending_attachments`` accumulates
        # blocks between Ctrl+V presses and is flushed into the TurnInput on submit.
        from magi_agent.cli.clipboard_image import read_clipboard_image  # noqa: PLC0415
        self._clipboard_reader: Callable[[], dict | None] = clipboard_reader or read_clipboard_image
        self._pending_attachments: list[dict] = []
        # Count of /compact (Compact()) acknowledgements; asserted by tests. Real
        # compaction is gated runtime authority (Stream B/E).
        self.compact_requests = 0
        self._renderers = renderers
        self._runtime = runtime
        self._session_id = session_id
        # Per-session ↑/↓ input history (persisted JSONL under the session root).
        self._history = InputHistory(session_id=session_id)
        # Per-session draft stash (ctrl+s); persisted JSONL alongside history.
        self._drafts = DraftStash(session_id=session_id)
        self._model = model
        self._mode = mode
        # Quiet by default: internal lifecycle/plumbing status events
        # (runner_policy_*, phase_route_decision, turn_end, …) are dropped from
        # the transcript. Set MAGI_TUI_VERBOSE=1 to surface them for debugging.
        import os as _os_verbose  # noqa: PLC0415

        self._verbose = _os_verbose.environ.get("MAGI_TUI_VERBOSE", "") == "1"
        # Session list (PR2.4) seams. ``_session_source`` is an optional test/
        # injection hook ``() -> list[SessionEntry]``; when None the dialog reads
        # the runtime's ``session_lister`` seam via ``session_entries``.
        # ``resumed_session`` records the most recently resumed session ref
        # (asserted by tests); None until a resume happens.
        self._session_source: Callable[[], list[SessionEntry]] | None = None
        self.resumed_session: str | None = None
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
        # Reasoning/thinking inline display (PR4.2): the in-flight dim thinking
        # line's update handle + accumulated reasoning text. Reset per turn so
        # streaming deltas coalesce into ONE updating line, not a new line each.
        self._thinking_handle: object | None = None
        self._thinking_accum: str = ""
        # Subagent/child-run inline display (PR4.3): per-task in-flight handle to
        # the dim indented line so lifecycle events (started → completed/failed)
        # for the SAME taskId coalesce into ONE updating line. Reset per turn.
        self._subagent_handles: dict[str, object] = {}
        # Keybindings: defaults merged with the user's ``keybindings.json`` if one
        # exists under the CLI config root (PR4.4). ``load_keybindings`` never
        # raises — a missing/malformed file or an unknown action degrades to
        # defaults, so the app always has a usable keymap. VIM mode + hot-reload
        # remain DEFERRED to v1.1.
        self._key_bindings: list[ParsedBinding] = load_keybindings(
            self._keybindings_path()
        )[0]
        self._pending: tuple[Keystroke, ...] | None = None
        # which-key chord-hint overlay (PR4.4); mounted in compose, driven by
        # on_key — shown while a chord is pending, hidden on resolve/cancel.
        self._whichkey: WhichKeyOverlay | None = None
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
        self._footer: StatusFooter | None = None
        # Left sidebar (PR3.2): mounted hidden, toggled by ctrl+b. Panes are fed
        # from folded tool/terminal events (todo / recent files / context usage).
        self._sidebar: Sidebar | None = None
        # Focus tracking for the gated attention bell (PR3.4). Assume focused at
        # mount; on_app_blur/on_app_focus keep it current. The bell only fires
        # while UNFOCUSED (and only when MAGI_TUI_NOTIFY_BELL is on — default OFF).
        self.app_is_focused: bool = True
        # monotonic() stamp marking the in-flight turn's start; None when idle.
        # Used by the footer elapsed clock (set in start_turn, cleared in
        # _render_terminal).
        self._turn_started_monotonic: float | None = None
        self._log: RichLog | None = None
        self._view: TranscriptView | None = None
        self._live: Static | None = None
        self._input: PromptInput | None = None
        self._completions: OptionList | None = None
        # Autocomplete overlay state: the currently-shown ranked completions and
        # the highlighted index. Tab/Enter accept the highlight, ↑/↓ navigate.
        self._completion_results: list[Completion] = []
        self._completion_index: int = 0
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

    @staticmethod
    def _keybindings_path() -> str | None:
        """Resolve the user keybindings config path, or ``None`` if absent.

        Lives at ``<session-root>/keybindings.json`` where ``<session-root>`` is
        ``~/.magi`` by default and overridable via ``MAGI_CLI_SESSION_DIR`` — the
        SAME root ``session_log.py`` / ``history.py`` / the theme settings use, so
        a single env isolates the whole CLI config tree in tests. Returns ``None``
        when no file exists so ``load_keybindings(None)`` short-circuits to the
        built-in defaults.
        """

        from magi_agent.cli.session_log import _session_root  # noqa: PLC0415

        path = _session_root() / "keybindings.json"
        return str(path) if path.exists() else None

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
        self._footer = StatusFooter(
            model=self._model, cwd=self._topbar_cwd(), id="footer"
        )
        # Left sidebar (PR3.2): a left-docked sibling, hidden by default (the CSS
        # sets ``display: none``; ctrl+b flips ``display``). Yielded BEFORE the
        # transcript so the left dock column is reserved first and the transcript
        # reflows to the right of it (no extra rule needed — #transcript is 1fr).
        self._sidebar = Sidebar(id="sidebar")
        # which-key overlay (PR4.4): bottom-docked, hidden until a chord pends.
        self._whichkey = WhichKeyOverlay(id="whichkey")
        yield self._topbar
        yield self._sidebar
        yield transcript_widget
        yield self._live
        yield self._completions
        yield self._whichkey
        yield self._input
        # The footer is yielded LAST so its ``dock: bottom`` wins the lower slot
        # over the prompt's (also bottom-docked) — the footer sits BELOW the
        # prompt.
        yield self._footer

    def _topbar_cwd(self) -> str:
        """Home-relative, length-capped cwd (shared by topbar + footer)."""

        import os as _os  # noqa: PLC0415

        home = _os.path.expanduser("~")
        cwd = self._cwd
        if cwd.startswith(home):
            cwd = "~" + cwd[len(home) :]
        if len(cwd) > 48:
            cwd = "…" + cwd[-47:]
        return cwd

    def _topbar_text(self) -> str:
        """The top status bar: app · model · cwd · mode (static identity)."""

        model = self._model or "no model"
        mode = (self._mode or "act").lower()
        return f"● Magi   {model}   {self._topbar_cwd()}   [{mode}]"

    def on_mount(self) -> None:
        assert self._live is not None and (
            self._log is not None or self._view is not None
        )
        # Register the curated theme set (custom magi-dark + Textual built-ins)
        # and restore the last-chosen theme from disk, falling back to the
        # historical default. The flat-look CSS pins region backgrounds to
        # transparent, so a theme only retints accent/text/primary — switching
        # never repaints the Screen/transcript with a solid colour (PR4.1).
        register_magi_themes(self)
        try:
            self.theme = load_saved_theme() or DEFAULT_THEME
        except Exception:  # pragma: no cover - theme always present in textual 8.x
            pass
        if self._view is not None:
            self._controller = TranscriptController(view=self._view, live=self._live)
        else:
            self._controller = TranscriptController(log=self._log, live=self._live)
        self._controller.markdown_live = True
        # Wire the prompt's ↑/↓ recall to this session's history ring.
        if self._input is not None:
            self._input.attach_history(self._history)
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
        # While a turn is in flight, advance the footer elapsed so the live
        # status reads as a running clock (reuses the existing flush timer — no
        # separate timer needed). Once _render_terminal clears the start stamp
        # and flips state off "running", this stops contributing.
        if (
            self._turn_started_monotonic is not None
            and self._footer is not None
            and self._footer.state == "running"
        ):
            # The reactive ``elapsed`` advances every tick (cheap assignment),
            # but ``StatusFooter.watch_elapsed`` only REPAINTS when the
            # whole-second value changes — so this 25Hz tick no longer triggers
            # ~24/25 identical repaints for the 1s-granularity render.
            self._footer.set_elapsed(self._turn_elapsed())

    def _render_welcome(self) -> None:
        """Render the initial TUI state so bare ``magi`` never opens blank."""

        if self._controller is None:
            return
        command_names = tui_command_names(self._commands)
        preview = ", ".join(f"/{name}" for name in command_names[:8])
        extra = len(command_names) - 8
        if preview and extra > 0:
            preview = f"{preview}  (+{extra} more — type / to see all)"
        command_line = preview if preview else "type / for local commands"
        from rich.text import Text  # noqa: PLC0415

        welcome = Text()
        welcome.append("● ", style="bold #7aa2f7")
        welcome.append("Welcome to Magi", style="bold")
        welcome.append("  ·  your local AI agent\n", style="dim")
        welcome.append("Type a task and press ", style="dim")
        welcome.append("Enter", style="#7aa2f7")
        welcome.append(".  ", style="dim")
        welcome.append("Ctrl+C", style="#7aa2f7")
        welcome.append(" cancels a turn (again to quit).\n", style="dim")
        welcome.append("Keys: ", style="dim")
        welcome.append("Shift+Enter", style="#7aa2f7")
        welcome.append(" newline · ", style="dim")
        welcome.append("↑", style="#7aa2f7")
        welcome.append(" history · ", style="dim")
        welcome.append("Ctrl+S", style="#7aa2f7")
        welcome.append(" draft · ", style="dim")
        welcome.append("Ctrl+B", style="#7aa2f7")
        welcome.append(" sidebar · ", style="dim")
        welcome.append("Ctrl+P", style="#7aa2f7")
        welcome.append(" palette · ", style="dim")
        welcome.append("F1", style="#7aa2f7")
        welcome.append(" help\n", style="dim")
        welcome.append("Copy: drag to select · ", style="dim")
        welcome.append("Ctrl+Y", style="#7aa2f7")
        welcome.append(" copy (", style="dim")
        welcome.append("⌥", style="#7aa2f7")
        welcome.append("-drag for native terminal copy)\n", style="dim")
        welcome.append(f"Commands ({len(command_names)}): ", style="dim")
        welcome.append(command_line, style="#9ece6a")
        self._controller.commit_rich(
            welcome,
            text=(
                "Welcome to Magi  "
                "Keys: Shift+Enter newline · ↑ history · Ctrl+S draft · "
                "Ctrl+B sidebar · Ctrl+P palette · F1 help  "
                "Copy: drag to select · Ctrl+Y copy (⌥-drag for native terminal copy)  "
                f"Commands ({len(command_names)}): {command_line}"
            ),
        )

    @property
    def controller(self) -> TranscriptController:
        if self._controller is None:  # pragma: no cover - guarded by on_mount
            raise RuntimeError("controller not ready; app not mounted")
        return self._controller

    # -- clipboard image attach ---------------------------------------------
    @property
    def pending_attachments(self) -> list[dict]:
        """Read-only view of queued image blocks (not yet submitted)."""
        return list(self._pending_attachments)

    def attach_clipboard_image(self) -> None:
        """Read a clipboard image and add it to the pending buffer.

        Calls the injected ``_clipboard_reader`` (real default:
        ``read_clipboard_image``; test default: any callable → dict | None).
        On success appends the block and shows a toast. On None shows a warning
        toast and leaves the buffer untouched.
        """
        try:
            block = self._clipboard_reader()
        except Exception as exc:  # reader shells out (pngpaste/xclip); never crash a turn
            _notify.warning(self, f"Clipboard read failed: {exc}")
            return
        if block is not None:
            self._pending_attachments.append(block)
            _notify.info(self, f"📎 image attached ({len(self._pending_attachments)})")
        else:
            _notify.warning(self, "No image in clipboard")

    def on_prompt_input_attach_image_requested(
        self, event: "PromptInput.AttachImageRequested"
    ) -> None:
        """Handle the Ctrl+V message posted by PromptInput."""
        self.attach_clipboard_image()

    # -- input / submission -------------------------------------------------
    def on_prompt_input_prompt_submitted(
        self, event: PromptInput.PromptSubmitted
    ) -> None:
        self._hide_completions()
        submission = event.submission
        # Record every submitted prompt (commands included) into history so ↑/↓
        # recall them; blank/consecutive-dup entries are filtered by add().
        self._history.add(submission.text)
        if submission.kind == "command":
            self._dispatch_command(submission)
            return
        self.start_turn(submission.text)

    def submit_command(self, name: str, args: str = "") -> None:
        """Submit a slash command exactly as if typed at the prompt.

        Builds the SAME ``Submission`` the prompt input's ``classify_line``
        produces for ``/name args`` (kind/text/command_name/args + the registry
        ``lookup`` result) and routes it through
        ``on_prompt_input_prompt_submitted`` → ``_dispatch_command``. This is the
        single funnel both typed and palette-launched commands use, so PR2.2's
        executor and the single-turn invariant apply uniformly.
        """

        from magi_agent.cli.tui.input import classify_line  # noqa: PLC0415

        line = f"/{name} {args}".rstrip()
        submission = classify_line(line, self._commands)
        self.on_prompt_input_prompt_submitted(
            PromptInput.PromptSubmitted(submission)
        )

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        # Recompute completions for the current pre-cursor slice (debounced via
        # the exclusive worker so a stale async pass is discarded). ``PromptInput``
        # is a ``TextArea`` so it posts ``TextArea.Changed`` (not ``Input.Changed``)
        # on every edit.
        if self._input is None:
            return
        # Source guard: ONLY the prompt buffer drives completions. Other
        # ``TextArea``s (e.g. the ``ToolUseConfirm`` modal's ``#edit-area``)
        # bubble their own ``TextArea.Changed`` here; recomputing off the prompt
        # precursor for those is spurious, so ignore foreign sources.
        if event.text_area is not self._input:
            return
        self._refresh_completions(self._input.precursor)

    def _dispatch_command(self, submission: Submission) -> None:
        """Run a slash command through the injected executor (PR2.2).

        Looks the command up, builds a ``CommandContext`` exposing the app
        openers, and runs the executor in a NON-turn worker (``group="command"``)
        so a command never collides with or cancels the exclusive ``group="turn"``
        worker. ``prompt``-kind commands re-enter ``start_turn`` (the one turn
        loop); ``local``/``widget`` kinds apply their effect without starting an
        engine loop.
        """

        command = submission.command or self._commands.lookup(submission.command_name)
        if command is None:
            self.controller.commit_block(
                f"[command] unknown: /{submission.command_name}"
            )
            return
        args = self._command_args(submission)
        ctx = CommandContext(
            cwd=self._cwd, runtime=self._runtime, session=self._session_id, app=self
        )
        self._run_command(command, args, ctx)

    @staticmethod
    def _command_args(submission: Submission) -> str:
        """Argument tail for a command submission.

        ``classify_line`` already splits ``/name args`` and stores the parsed
        tail on ``Submission.args``, so prefer it. Fall back to re-deriving from
        ``text`` for any submission built without going through ``classify_line``.
        """

        if submission.args:
            return submission.args
        text = (submission.text or "").strip()
        if text.startswith("/"):
            _, _, rest = text.partition(" ")
            return rest.strip()
        return ""

    @work(group="command")
    async def _run_command(
        self, command: object, args: str, ctx: CommandContext
    ) -> None:
        # Separate worker group from "turn" so a command never collides with or
        # cancels the exclusive turn worker. The executor itself never drives a
        # turn loop; a prompt command calls back into start_turn (group="turn").
        try:
            await self._executor.run(command, args, ctx)  # type: ignore[arg-type]
        except Exception as exc:  # commands are first-party but must never die silently
            # ``except Exception`` deliberately excludes ``asyncio.CancelledError``
            # (a ``BaseException`` in modern Python), so cancellation still
            # propagates and only real command failures surface here.
            self.controller.commit_block(f"[command failed: {exc}]")

    # -- app-opener seam (CommandContext.app) ------------------------------
    def status_snapshot(self) -> dict[str, object]:
        """Live session status for the ``/status`` command.

        ``StatusCommand`` reads this (via ``ctx.app``) to render a real
        model/cwd/mode/session/turns/tokens summary instead of the
        slash-control boundary projection (which is the headless fallback).
        """

        from magi_agent import __version__  # noqa: PLC0415

        tokens = 0
        if self._footer is not None:
            try:
                tokens = int(getattr(self._footer, "tokens", 0) or 0)
            except (TypeError, ValueError):
                tokens = 0
        return {
            "version": __version__,
            "model": self._model or "no model",
            "cwd": self._cwd,
            "mode": (self._mode or "act").lower(),
            "session": self._session_id,
            "turns": self._turn_seq,
            "tokens": tokens,
        }

    def commit_text(self, text: str) -> None:
        """Commit a local command's ``Text`` result to the transcript."""

        self.controller.commit_block(text)

    def request_compact(self) -> None:
        """Acknowledge a ``Compact()`` result (real compaction is gated B/E)."""

        self.compact_requests += 1
        self.controller.commit_block("[compact requested]")

    def open_dialog(self, name: str) -> None:
        """Open a named dialog (PR2.3/2.4/2.5 register the real openers)."""

        opener = getattr(self, f"action_open_{name}", None)
        if callable(opener):
            opener()

    # -- model picker (PR2.3) ----------------------------------------------
    def action_open_model_picker(self) -> None:
        """Open the model picker; apply the chosen model on dismiss.

        Surfaced automatically in the command palette (``AppActionProvider``
        gates on ``action_open_model_picker`` existing) and reachable via
        ``open_dialog("model_picker")`` (the ``open_dialog`` name maps to
        ``action_open_<name>``). The plan defers a ``/model`` widget command
        wiring; the action + palette path is the PR2.3 surface.
        """

        dialog = ModelPickerDialog(
            models=model_choices(self._model), current=self._model
        )
        self.push_screen(dialog, self._apply_model)

    def _apply_model(
        self, model: str | None, *, _config_path: object = None
    ) -> None:
        """Apply a model selected in the picker (None on cancel = no-op).

        Updates ``self._model``, persists the choice to ``~/.magi/config.toml``
        (best-effort: a write failure shows a "couldn't save" note rather than
        crashing), and refreshes the topbar.  ``TurnInput`` carries no model
        field, so the engine does not consume the switch mid-session — it takes
        effect on the NEXT launch.  ``_config_path`` is a test-only seam so
        tests never touch the real ``~/.magi/config.toml``.
        """

        if not model:
            return
        # Persist to config (best-effort).
        try:
            from magi_agent.cli.providers import persist_model  # noqa: PLC0415

            persist_model(model, path=_config_path)
            saved = True
        except Exception:
            saved = False
        self._model = model
        if self._topbar is not None:
            self._topbar.update(self._topbar_text())
        if saved:
            self.notify(
                f"Model set to {model} — saved to config, applies next session",
                timeout=2,
            )
        else:
            self.notify(
                f"Model set to {model} (couldn't save to config — applies next session)",
                timeout=3,
            )

    # -- session list (PR2.4) ----------------------------------------------
    def action_open_session_list(self) -> None:
        """Open the session list; resume the chosen session on dismiss.

        Surfaced automatically in the command palette (``AppActionProvider``
        gates on ``action_open_session_list`` existing) and reachable via
        ``open_dialog("session_list")`` (the ``open_dialog`` name maps to
        ``action_open_<name>``). The resumable list comes from the injected
        ``_session_source`` test seam when set, else from the runtime's
        ``session_lister`` seam via ``session_entries``; absent either it is
        empty and the dialog shows its "No prior sessions." placeholder.
        """

        if self._session_source is not None:
            sessions = list(self._session_source())
        else:
            sessions = session_entries(self._runtime)
        self.push_screen(SessionListDialog(sessions=sessions), self._resume_session)

    def _resume_session(self, ref: str | None) -> None:
        """Resume a chosen session (None on cancel = no-op).

        OQ3: **marker-only resume**. We switch the active session id to the
        chosen ref and show a visible ``[resumed session {ref}]`` marker; the
        prior transcript is NOT replayed and NO synthetic engine turn is sent.
        The user's next typed prompt naturally runs under the resumed
        ``_session_id``. Real rehydration (``TurnInput.initial_messages``
        replay) stays a deferred runtime seam.
        """

        if not ref:
            return
        self.resumed_session = ref
        self._session_id = ref
        # Re-bind the per-session history + draft stores to the resumed session.
        # Both were constructed ONCE in __init__ bound to the ORIGINAL session's
        # files (history-<id>.jsonl / drafts-<id>.jsonl); without rebuilding them
        # ↑/↓ recall and ctrl+s drafts would keep reading/writing the OLD
        # session's files — a silent desync. Match __init__'s construction.
        self._history = InputHistory(session_id=ref)
        self._drafts = DraftStash(session_id=ref)
        # Re-point the prompt's ↑/↓ recall at the new history ring (on_mount
        # wired the original; the input must follow the resumed session too).
        if self._input is not None:
            self._input.attach_history(self._history)
        self.controller.commit_block(f"[resumed session {ref}]")

    # -- help (PR2.5) ------------------------------------------------------
    def action_open_help(self) -> None:
        """Open the read-only help dialog (keybindings + command reference).

        Surfaced automatically in the command palette (``AppActionProvider``
        gates on ``action_open_help`` existing), reachable via
        ``open_dialog("help")`` (the ``open_dialog`` name maps to
        ``action_open_<name>``), and bound to ``F1``. The dialog reads the live
        ``BINDINGS`` + ``COMMAND_PALETTE_BINDING`` + the injected command
        registry; escape/enter closes it. Read-only — no behavioral change.
        """

        self.push_screen(HelpDialog.from_app(self))

    # -- theme cycle + picker (PR4.1) --------------------------------------
    def action_cycle_theme(self) -> None:
        """Advance ``App.theme`` to the next curated theme and persist it.

        Bound to ``ctrl+t``. Wraps around ``MAGI_THEMES``; if the current theme
        is outside the curated set (e.g. a built-in set elsewhere) the cycle
        restarts at the first curated name. The flat-look regions stay
        transparent across the switch — only accent/text/primary retint.
        """

        current = self.theme
        try:
            idx = MAGI_THEMES.index(current)
        except ValueError:
            idx = -1
        nxt = MAGI_THEMES[(idx + 1) % len(MAGI_THEMES)]
        self._set_theme(nxt)

    def select_theme(self, name: str) -> None:
        """Set + persist a theme by name (the palette ThemeProvider entrypoint).

        Unknown names are ignored so a stale/forged palette entry can never set
        ``App.theme`` to an unregistered value (which would raise on assignment).
        """

        if name not in MAGI_THEMES:
            return
        self._set_theme(name)

    def _set_theme(self, name: str) -> None:
        """Apply + persist a curated theme and toast the choice (best-effort)."""

        self.theme = name
        save_theme(name)
        try:
            self.notify(f"Theme: {name}", timeout=2)
        except Exception:  # pragma: no cover - notify always available when mounted
            pass

    # -- the ONE engine-driven turn loop -----------------------------------
    def start_turn(self, prompt: str) -> None:
        """Kick off a single engine turn for ``prompt`` in an exclusive worker."""

        import time as _time  # noqa: PLC0415

        self._turn_seq += 1
        turn_id = f"{self._session_id}-turn-{self._turn_seq}"
        cancel = asyncio.Event()
        self._cancel = cancel
        self._active_turn_id = turn_id
        self._turn_active = True
        # Fresh thinking line for this turn (coalesce only within one turn).
        self._thinking_handle = None
        self._thinking_accum = ""
        # Fresh subagent registry for this turn (coalesce only within one turn).
        self._subagent_handles = {}
        self._turn_started_monotonic = _time.monotonic()
        # Clear any dangling chord prefix + which-key overlay: a turn starting
        # mid-chord (e.g. submit fired) must not leave the hint stuck visible.
        self._pending = None
        self._hide_whichkey()
        self.update_footer(state="running")
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
        """Copy the currently selected transcript text to the clipboard.

        Both the empty-selection and clipboard-failure paths now surface a toast
        (``_notify.info`` / ``_notify.warning``) instead of returning silently —
        the operator always gets feedback that Ctrl+Y did something (or why it
        couldn't). Reading the selection itself stays best-effort (a missing
        selection API is the expected "nothing selected" case, not an error).
        """

        text = ""
        try:
            text = self.screen.get_selected_text() or ""  # type: ignore[attr-defined]
        except Exception:
            try:
                text = self.selected_text or ""  # type: ignore[attr-defined]
            except Exception:
                text = ""
        if not text:
            _notify.info(self, "Nothing selected to copy")
            return
        try:
            self.copy_to_clipboard(text)
            _notify.info(self, "Copied selection")
        except Exception as exc:
            _notify.warning(self, f"Copy failed: {exc}")

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
        # Snapshot and clear the pending image buffer atomically before the turn
        # starts so any Ctrl+V press that races the submit lands in the NEXT turn.
        image_blocks = tuple(self._pending_attachments)
        self._pending_attachments.clear()
        turn_input = TurnInput(
            prompt=prompt,
            session_id=self._session_id,
            turn_id=turn_id,
            image_blocks=image_blocks,
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
        except BaseException:
            # The engine RAISED instead of yielding a terminal: the normal
            # ``_render_terminal`` path below never runs, so the footer would
            # stay on "running" and the elapsed clock would tick forever. Reset
            # the footer to a terminal-ish state and stop the clock, then
            # re-raise to preserve the existing error propagation.
            self._reset_footer_on_error()
            raise
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

    def _reset_footer_on_error(self) -> None:
        """Stop the running clock + flip the footer off "running" on a raise.

        Used when the engine generator raises (rather than yielding an error
        terminal): clears the elapsed start stamp so ``_on_flush_tick`` stops
        advancing the clock, and folds an ``error`` state into the footer so the
        user sees the turn ended. No-op before mount.
        """

        self._turn_started_monotonic = None
        self.update_footer(state=Terminal.error.value)

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
        if event.type == "error":
            controller.commit_block(_status_summary(event))
            return
        # Reasoning/thinking is user-relevant (unlike plumbing noise), so it is
        # INTERCEPTED here — BEFORE the quiet-by-default ``status`` drop below —
        # and rendered as a dim one-line block, shown by default. Streaming
        # deltas coalesce into the single in-flight thinking line.
        if _is_reasoning_event(event):
            self._render_thinking(event)
            return
        # Subagent/child-run activity is user-relevant (unlike plumbing noise),
        # so it too is INTERCEPTED here — BEFORE the quiet-by-default ``status``
        # drop below — and rendered as a dim INDENTED one-line block, shown by
        # default. Lifecycle events coalesce per taskId into one updating line.
        if _is_child_event(event):
            self._render_subagent(event)
            return
        # status / artifact / control -> internal diagnostics (routing/policy/
        # turn-lifecycle plumbing). These flooded the chat with lines like
        # ``runner_policy_assembly`` / ``phase_route_decision`` / ``turn_end``, so
        # they are hidden by default and only shown under MAGI_TUI_VERBOSE=1.
        if self._verbose:
            controller.commit_block(_status_summary(event))

    def _render_tool_event(self, event: RuntimeEvent) -> None:
        """Route a TOOL event through the injected per-tool renderer registry."""

        payload = event.payload
        name = _tool_name(payload)
        renderer = self._renderers.get(name)
        inner = _inner_type(payload)
        if inner == "tool_start":
            # Fold this tool_start into the sidebar panes (todo / recent files)
            # BEFORE rendering the call, so the side panes track activity even
            # when the sidebar is currently hidden.
            self._fold_sidebar_tool(name, _tool_input(payload))
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
        """Commit a ``RenderNode`` as a compact one-line block.

        Tools render Claude-Code style: a ``● Name(arg)`` header and a dimmed
        ``└ preview`` — committed as plain finalized blocks (not collapsible
        cards), so a turn's tool activity stays scannable instead of stacking
        large boxes. The displayed/committed text is annotated with the tool name
        when the renderer's output does not already carry it (the fallback
        renderer emits only the raw input/result); the real Edit/Bash/Read
        renderers already embed their name in the header, so they are untouched.
        ``commit_rich`` keeps the displayed text in the snapshot for
        search-fidelity (what is indexed == what is shown).
        """

        rich = getattr(node, "rich", None)
        text = getattr(node, "text", "") or ""
        annotated = self._annotate_tool_text(text, tool_name)
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

    def _render_thinking(self, event: RuntimeEvent) -> None:
        """Render a reasoning event as a dim one-line block (coalesced).

        The first reasoning event in a turn commits a fresh dim ``● thinking``
        line; subsequent deltas UPDATE that same line in place (the accumulated
        reasoning's terse preview) rather than spamming one line per delta. The
        in-flight handle is reset at turn start (``start_turn``).
        """

        payload = event.payload if isinstance(event.payload, dict) else {}
        delta = _reasoning_text(payload)
        # Accumulate so the coalesced preview reflects the latest reasoning.
        # Gate the join on whether there's ALREADY accumulated text (the
        # accumulator), not on the widget handle — appending only makes sense
        # when prior reasoning exists, and this stays correct even if the first
        # delta is empty (no leading-space artifact to mask).
        self._thinking_accum = (
            (self._thinking_accum + " " + delta).strip()
            if self._thinking_accum
            else delta.strip()
        )
        node = _render_thinking_node(self._thinking_accum)
        if self._thinking_handle is None:
            self._thinking_handle = self.controller.commit_coalesced(
                node.rich, text=node.text
            )
        else:
            # Position-freeze: if a tool/assistant block commits BETWEEN two
            # thinking deltas, ``update_coalesced`` keeps patching the ORIGINAL
            # thinking line in place — its text updates while its on-screen
            # position stays ABOVE the later block. This is intended coalescing
            # (one thinking line per turn), NOT the index-drift bug.
            self.controller.update_coalesced(
                self._thinking_handle, node.rich, text=node.text
            )

    def _render_subagent(self, event: RuntimeEvent) -> None:
        """Render a child/subagent event as a dim indented one-line block.

        The first event for a ``taskId`` commits a fresh dim indented
        ``  ⤷ subagent <label>  <status>`` line; subsequent lifecycle events for
        the SAME task UPDATE that one line in place (status started → completed/
        failed) rather than spamming one line per event. Distinct tasks get
        distinct lines. The per-task handle registry is reset at turn start.
        Reuses the generic in-place one-line ``commit_coalesced``/
        ``update_coalesced`` seam (the transcript's coalescing primitive).
        """

        payload = event.payload if isinstance(event.payload, dict) else {}
        inner = payload.get("type")
        # ``_is_child_event`` already proved ``inner`` is a str key in
        # ``_CHILD_INNER_STATUS`` before routing here, so a direct lookup is safe.
        status = _CHILD_INNER_STATUS[inner]
        # Coalesce by the RAW taskId (key) so two distinct taskIds sharing a
        # 59-char prefix don't collide once the DISPLAY label truncates.
        key = _child_task_key(payload)
        label = _child_task_label(payload)
        node = _render_subagent_node(label, status, _child_detail(payload))
        handle = self._subagent_handles.get(key)
        if handle is None:
            self._subagent_handles[key] = self.controller.commit_coalesced(
                node.rich, text=node.text
            )
        else:
            self.controller.update_coalesced(handle, node.rich, text=node.text)

    def _fold_sidebar_tool(self, name: str, tool_input: object) -> None:
        """Route a tool_start into the sidebar panes (todo / recent files).

        TodoWrite replaces the todo pane with the tool's todo list; Read/Edit/
        Write push the touched file onto the MRU recent-files pane. No-op when
        the sidebar is not mounted or the input carries nothing usable.
        """

        if self._sidebar is None:
            return
        if name == "TodoWrite":
            todos = _todo_contents(tool_input)
            if todos is not None:
                self._sidebar.set_todos(todos)
            return
        if name in ("Read", "Edit", "Write", "MultiEdit"):
            path = _tool_file_path(tool_input)
            if path:
                self._sidebar.add_file(path)

    # -- status footer (PR3.1) ---------------------------------------------
    def update_footer(
        self,
        *,
        state: str | None = None,
        tokens: int | None = None,
        elapsed: float | None = None,
    ) -> None:
        """Single seam every fold/turn path uses to refresh the footer.

        No-op before mount (the footer is created in ``compose``); each provided
        field updates the corresponding reactive on ``StatusFooter`` (which
        repaints only itself).
        """

        if self._footer is None:
            return
        if state is not None:
            self._footer.set_state(state)
        if tokens is not None:
            self._footer.set_tokens(tokens)
        if elapsed is not None:
            self._footer.set_elapsed(elapsed)

    def _turn_elapsed(self) -> float:
        """Seconds since the in-flight turn started (0.0 when idle)."""

        import time as _time  # noqa: PLC0415

        if self._turn_started_monotonic is None:
            return 0.0
        return max(0.0, _time.monotonic() - self._turn_started_monotonic)

    def _render_terminal(self, terminal: EngineResult) -> None:
        # Fold the terminal state + token usage + elapsed into the footer FIRST,
        # so it updates for completed AND non-completed turns (the early return
        # below is only for the transcript marker, not the footer).
        tokens = _usage_tokens(terminal.usage)
        self.update_footer(
            state=terminal.terminal.value,
            tokens=tokens,
            elapsed=self._turn_elapsed(),
        )
        # Mirror the turn's token usage into the sidebar context pane. The limit
        # is kept as a future per-model seam, but the sidebar currently renders
        # only a bare token count to avoid a misleading hardcoded ratio.
        if self._sidebar is not None:
            self._sidebar.set_context(
                usage=tokens, limit=_context_limit(self._model)
            )
        # Stop the running clock once the turn is terminal (the flush-tick
        # elapsed advance keys off this being None / state != "running").
        self._turn_started_monotonic = None
        # Gated focus-aware attention bell (PR3.4): ring only when the terminal
        # is unfocused AND MAGI_TUI_NOTIFY_BELL is on (default OFF). Fired here
        # so it covers completed AND non-completed turns (before the early
        # return for the completed case). Fail-open — never crashes the turn.
        _notify.notify_attention(
            self,
            focused=self.app_is_focused,
            reason=f"Magi: turn {terminal.terminal.value}",
        )
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

    # -- sidebar toggle (PR3.2) --------------------------------------------
    def action_toggle_sidebar(self) -> None:
        """Show/hide the left sidebar (ctrl+b)."""

        if self._sidebar is None:
            return
        self._sidebar.display = not self._sidebar.display

    # -- focus tracking for the attention bell (PR3.4) ---------------------
    def on_app_blur(self, _event: object) -> None:
        """Terminal lost focus -> the attention bell may fire on next turn-done.

        Textual posts ``AppBlur`` (handler ``on_app_blur``) when the terminal
        emulator reports the window/tab lost focus. We only track the flag here;
        the gated bell keys off it (and ``MAGI_TUI_NOTIFY_BELL``).
        """

        self.app_is_focused = False

    def on_app_focus(self, _event: object) -> None:
        """Terminal regained focus -> suppress the attention bell.

        Textual posts ``AppFocus`` (handler ``on_app_focus``) when focus returns;
        the operator is now looking, so no bell should fire.
        """

        self.app_is_focused = True

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
            self._hide_whichkey()
            _stop(event)
            self._run_key_action(result.action)
        elif result.kind is ResultKind.CHORD_STARTED:
            self._pending = result.pending
            self._show_whichkey()
            _stop(event)
        else:
            # UNBOUND / NONE / CHORD_CANCELLED: clear any pending, hide the hints
            # and let the event bubble (typing reaches the Input widget).
            self._pending = None
            self._hide_whichkey()

    def _show_whichkey(self) -> None:
        """Render the pending chord's continuations into the overlay."""

        if self._whichkey is None:
            return
        hints = chord_continuations(
            self._pending, self._active_contexts(), self._key_bindings
        )
        self._whichkey.show_hints(hints)

    def _hide_whichkey(self) -> None:
        """Hide the which-key overlay (chord resolved or cancelled)."""

        if self._whichkey is not None:
            self._whichkey.hide_hints()

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
        # NOTE: these CHAT_SUBMIT/CHAT_NEWLINE branches are NOT reached while
        # ``PromptInput`` is focused — its ``_on_key`` calls ``event.stop()`` on
        # Enter/Shift+Enter, so it is the authoritative submission driver. These
        # remain only as a fallback for programmatic / non-focused dispatch.
        elif action == Action.CHAT_SUBMIT.value:
            self._submit_current_input()
        elif action == Action.CHAT_NEWLINE.value:
            if self._input is not None:
                self._input.insert("\n")
        elif action == Action.CHAT_STASH.value:
            self._stash_or_restore_draft()
        # Remaining Action members (AUTOCOMPLETE_* / CONFIRMATION_*) are owned by
        # widgets or land in later PRs.

    def _submit_current_input(self) -> None:
        """Submit the current prompt buffer (classify + route, then clear)."""

        if self._input is None:
            return
        self._input.submit()

    def _stash_or_restore_draft(self) -> None:
        """ctrl+s: stash the current draft, or restore the most recent if empty.

        A non-blank buffer is handed to :class:`DraftStash` (which keeps it only
        if it is at least ``MIN_DRAFT_LEN`` chars). The buffer is cleared ONLY
        when the draft was actually stored — a sub-threshold draft that
        ``save()`` drops leaves the buffer intact so a deliberate ctrl+s never
        silently loses the operator's text. An empty buffer restores the single
        most-recent stashed draft (highest count, then recency) and parks the
        caret at its end. No-op when there is nothing to restore.
        """

        if self._input is None:
            return
        text = self._input.text
        if text.strip():
            if self._drafts.save(text):
                self._input.text = ""
            else:
                # Too short to stash: keep the buffer so it isn't lost.
                self.notify("Draft too short to stash", timeout=2)
            return
        recent = self._drafts.recent(limit=1)
        if recent:
            restored = recent[0]
            self._input.text = restored
            self._input.cursor_location = (
                restored.count("\n"),
                len(restored.split("\n")[-1]),
            )

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
        self._completion_results = results
        self._completion_index = 0
        self._completions.add_options(
            [Option(self._format_option(c), id=str(i)) for i, c in enumerate(results)]
        )
        self._completions.add_class("visible")
        # Highlight the top candidate so Tab/Enter has a default to accept.
        try:
            self._completions.highlighted = 0
        except Exception:  # pragma: no cover - defensive (textual version skew)
            pass

    @staticmethod
    def _format_option(completion: Completion) -> str:
        if completion.ghost and completion.label.endswith(completion.ghost):
            head = completion.label[: -len(completion.ghost)]
            return f"{head}{completion.ghost}"
        return completion.label

    def _hide_completions(self) -> None:
        self._completion_results = []
        self._completion_index = 0
        if self._completions is None:
            return
        self._completions.remove_class("visible")
        self._completions.clear_options()

    # -- completion acceptance (driven by PromptInput key handling) ----------
    def completions_active(self) -> bool:
        """True when the autocomplete overlay is visible with candidates.

        ``PromptInput._on_key`` consults this so Tab/Enter/↑/↓/Esc drive the
        overlay (accept/navigate/dismiss) instead of their normal editor roles
        while completions are showing.
        """

        return bool(self._completion_results) and self._completions is not None and (
            self._completions.has_class("visible")
        )

    def completion_navigate(self, delta: int) -> None:
        """Move the highlighted completion by ``delta`` (wraps)."""

        if not self._completion_results:
            return
        count = len(self._completion_results)
        self._completion_index = (self._completion_index + delta) % count
        if self._completions is not None:
            try:
                self._completions.highlighted = self._completion_index
            except Exception:  # pragma: no cover - defensive
                pass

    def accept_completion(self) -> bool:
        """Substitute the highlighted completion into the prompt and dismiss."""

        if not self._completion_results:
            return False
        index = self._completion_index
        if not 0 <= index < len(self._completion_results):
            index = 0
        value = self._completion_results[index].value
        if self._input is not None:
            self._input.apply_completion(value)
        self._hide_completions()
        return True

    def cancel_completions(self) -> None:
        """Dismiss the overlay without substituting (Esc)."""

        self._hide_completions()

    def on_option_list_option_selected(self, event: object) -> None:
        """Accept a completion clicked with the mouse (Enter goes via the input)."""

        option_list = getattr(event, "option_list", None)
        if option_list is not self._completions or not self._completion_results:
            return
        index = getattr(event, "option_index", None)
        if isinstance(index, int):
            self._completion_index = index
        self.accept_completion()
