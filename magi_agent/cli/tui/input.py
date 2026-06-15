"""Prompt input widget + submission routing for the Magi TUI (PR-E2 / PR1.1).

``PromptInput`` wraps a Textual ``TextArea`` (multiline) and exposes the
pre-cursor text slice (everything left of the caret, across rows) so the
:class:`~.autocomplete.AutocompleteRouter` can compute completions. Enter
submits the whole buffer; Shift+Enter inserts a newline. On submit it
classifies the line:

* a line beginning with ``/`` is a **slash command** -> dispatched via the
  injected :class:`~magi_agent.cli.contracts.CommandRegistry` (looked up
  by name);
* anything else is a **prompt** -> starts an engine turn.

The widget itself only *classifies + emits* a :class:`Submission`; the owning
``App`` (see :mod:`app`) decides how to run the turn or execute the command. The
command-execution machinery (prompt/local/widget kinds) is Stream D's; here we
only wire registry ``lookup`` so the App can route — widget commands are a
documented stub.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from textual import events
from textual.message import Message
from textual.widgets import TextArea

from magi_agent.cli.contracts import Command, CommandRegistry

if TYPE_CHECKING:
    from magi_agent.cli.tui.history import InputHistory

__all__ = ["Submission", "PromptInput", "classify_line"]


@dataclass(frozen=True)
class Submission:
    """A classified input submission.

    ``kind`` is ``"prompt"`` or ``"command"``. For a command, ``command_name`` is
    the slash name WITHOUT the leading ``/`` (e.g. ``"compact"``), ``args`` is the
    remaining text, and ``command`` is the registry lookup result (``None`` if the
    name is unknown). For a prompt, ``text`` is the full prompt line.
    """

    kind: str
    text: str = ""
    command_name: str = ""
    args: str = ""
    command: Command | None = None


def classify_line(line: str, commands: CommandRegistry) -> Submission:
    """Classify a submitted ``line`` into a prompt or a command submission."""

    stripped = line.strip()
    if stripped.startswith("/"):
        body = stripped[1:]
        name, _, args = body.partition(" ")
        command = commands.lookup(name) if name else None
        return Submission(
            kind="command",
            text=line,
            command_name=name,
            args=args.strip(),
            command=command,
        )
    return Submission(kind="prompt", text=line)


class PromptInput(TextArea):
    """The REPL prompt input widget (multiline).

    Backed by ``textual.widgets.TextArea`` so the prompt is multiline: Enter
    submits the whole buffer; Shift+Enter inserts a newline. This widget's
    ``_on_key`` is the AUTHORITATIVE submission driver: it intercepts Enter /
    Shift+Enter at the widget level and calls ``event.stop()`` (``TextArea``
    otherwise consumes Enter as a newline; without ``event.stop()`` the App's
    keybinding resolver would also fire). Because the event is stopped while
    this widget is focused, the App's ``Action.CHAT_SUBMIT`` /
    ``Action.CHAT_NEWLINE`` resolver branches are NOT reached — they remain only
    as a fallback for programmatic / non-focused dispatch.

    Posts a :class:`PromptInput.PromptSubmitted` message (carrying a classified
    :class:`Submission`) when the user submits a non-empty buffer, and clears
    itself. The public surface (``precursor`` / ``classify`` /
    ``PromptSubmitted``) is preserved byte-for-byte so the autocomplete router
    and App routing are untouched.
    """

    class PromptSubmitted(Message):
        """Posted when the user submits a classified line."""

        def __init__(self, submission: Submission) -> None:
            self.submission = submission
            super().__init__()

    class AttachImageRequested(Message):
        """Posted when the user presses Ctrl+V to attach a clipboard image."""

    def __init__(self, *, commands: CommandRegistry, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._commands = commands
        # A single-line-looking prompt by default; grows as the user types.
        self.show_line_numbers = False
        self.soft_wrap = True
        # INVARIANT: ``tab_behavior`` MUST stay the default "focus". Under
        # "indent" the base TextArea swallows Escape (_text_area.py: escape stop
        # is guarded on tab_behavior=="indent"), which would silently break the
        # idle-Esc arm-then-quit path (Esc would never bubble to the app).
        # Per-session ↑/↓ recall ring (wired by the App via attach_history).
        self._history: "InputHistory | None" = None

    def attach_history(self, history: "InputHistory") -> None:
        """Wire a per-session :class:`InputHistory` for ↑/↓ recall."""

        self._history = history

    def _last_row(self) -> int:
        """Row index of the buffer's final line (0-based)."""

        return self.text.count("\n")

    def _set_text(self, text: str) -> None:
        """Replace the buffer with ``text`` and park the caret at its end."""

        self.text = text
        self.cursor_location = (text.count("\n"), len(text.split("\n")[-1]))

    @property
    def precursor(self) -> str:
        """Text left of the caret, across all rows (autocomplete input)."""

        row, col = self.cursor_location
        lines = self.text.split("\n")
        if row >= len(lines):
            return self.text
        before = lines[:row]
        current = lines[row][:col]
        return "\n".join([*before, current])

    def classify(self, line: str) -> Submission:
        """Classify ``line`` against the injected registry (exposed for tests)."""

        return classify_line(line, self._commands)

    def apply_completion(self, value: str) -> None:
        """Replace the active pre-cursor token with ``value`` (+ trailing space).

        The active token is the run of non-space chars ending at the caret (the
        same slice the autocomplete router completes). Everything before the
        token and any text after the caret are preserved; the caret lands just
        after the inserted ``value`` and its trailing space so the user can type
        arguments immediately.
        """

        from magi_agent.cli.tui.autocomplete import precursor_token  # noqa: PLC0415

        precursor = self.precursor
        token = precursor_token(precursor)
        head = precursor[: len(precursor) - len(token)] if token else precursor
        after = self.text[len(precursor):]
        new_head = f"{head}{value} "
        self.text = f"{new_head}{after}"
        self.cursor_location = (new_head.count("\n"), len(new_head.split("\n")[-1]))

    def submit(self) -> None:
        """Classify + emit the current buffer as a submission, then clear.

        No-op on a blank buffer. Called by the widget's own Enter handler
        (``_on_key``, the authoritative driver). The App's
        ``Action.CHAT_SUBMIT`` resolver branch only calls this as a fallback for
        programmatic / non-focused dispatch (it is not reached while this widget
        is focused, since ``_on_key`` stops the event).
        """

        line = self.text
        if not line.strip():
            return
        submission = self.classify(line)
        self.text = ""
        self.post_message(self.PromptSubmitted(submission))

    def _completions_active(self) -> bool:
        """Whether the owning App's autocomplete overlay is currently showing."""

        checker = getattr(self.app, "completions_active", None)
        if not callable(checker):
            return False
        try:
            return bool(checker())
        except Exception:
            return False

    def _call_app(self, method: str, *args: object) -> None:
        """Invoke an overlay method on the owning App if present (duck-typed)."""

        fn = getattr(self.app, method, None)
        if callable(fn):
            fn(*args)

    async def _on_key(self, event: events.Key) -> None:
        """Intercept Enter (submit) / Shift+Enter (newline) / ↑↓ (history).

        ``TextArea._on_key`` (an async handler) maps Enter to a newline insert and
        stops the event, so the App's keybinding resolver never sees it. We take
        submission/newline here and otherwise await the base editor's handler so
        normal printable/edit keys still work.

        ↑/↓ recall history ONLY at the buffer edges (↑ on the first row, ↓ on the
        last row); anywhere mid-buffer the keypress falls through to the base
        editor so multi-line editing still moves the caret up/down a row as
        normal. With no history attached, ↑/↓ are never hijacked.
        """

        # Autocomplete overlay drives Tab/↑/↓/Esc while it is showing, so Tab
        # completes the highlighted skill/command instead of inserting a tab and
        # ↑/↓ navigate the menu instead of recalling history. Enter is left alone
        # (it still submits) so typing a full command + Enter runs it directly.
        # Falls through to normal editing when no overlay is open.
        if self._completions_active():
            if event.key == "tab":
                event.stop()
                event.prevent_default()
                self._call_app("accept_completion")
                return
            if event.key == "down":
                event.stop()
                event.prevent_default()
                self._call_app("completion_navigate", 1)
                return
            if event.key == "up":
                event.stop()
                event.prevent_default()
                self._call_app("completion_navigate", -1)
                return
            if event.key == "escape":
                event.stop()
                event.prevent_default()
                self._call_app("cancel_completions")
                return
        if event.key == "ctrl+v":
            event.stop()
            event.prevent_default()
            self.post_message(self.AttachImageRequested())
            return
        if event.key == "enter":
            # IME / CJK composition note (no defer needed here — analysis only):
            # React-style TUIs (e.g. OpenCode) double-``setTimeout`` before
            # submit to dodge a batching race where a composed Hangul/CJK string
            # hasn't flushed to state when the keydown handler reads it. Textual's
            # input pump is SYNCHRONOUS and ordered: the XTerm parser emits one
            # printable ``Key`` per committed syllable, each handled by the base
            # ``TextArea`` before this Enter handler runs, so ``self.text`` already
            # holds the full committed buffer. The React race does not exist; a
            # submit-defer would be cargo-cult. Two residuals are TERMINAL-owned,
            # not fixable here: (1) an UNCOMMITTED IME composition still in the
            # terminal's overlay was never sent as bytes, so neither Textual nor
            # we ever see it (most IMEs make Enter commit first, then submit on a
            # second Enter — correct, do not override); (2) terminals lacking
            # proper wide-char/grapheme handling. See design gap ``cjk-width-ime``.
            event.stop()
            event.prevent_default()
            self.submit()
            return
        if event.key == "shift+enter":
            event.stop()
            event.prevent_default()
            self.insert("\n")
            return
        if event.key in ("up", "down") and self._history is not None:
            row, _col = self.cursor_location
            if event.key == "up" and row == 0:
                recalled = self._history.prev(self.text)
                if recalled is not None:
                    event.stop()
                    event.prevent_default()
                    self._set_text(recalled)
                    return
            elif event.key == "down" and row == self._last_row():
                recalled = self._history.next()
                if recalled is not None:
                    event.stop()
                    event.prevent_default()
                    self._set_text(recalled)
                    return
        # Ctrl+D is fully owned here so it never bubbles to the app's keybinding
        # resolver, where ctrl+d is mapped to global:quit (a bare, single-press
        # exit — exit-safety regression). On an EMPTY buffer it is a quit gesture
        # routed to the arm-then-quit debounce; on a NON-empty buffer it keeps
        # the conventional delete-right (via the base TextArea action) with NO
        # quit/arm. Stopping the event in both cases is what suppresses the dead
        # global:quit mapping for the focused prompt.
        if event.key == "ctrl+d":
            event.stop()
            event.prevent_default()
            if not self.text.strip():
                # ``_call_app`` does a direct getattr, so name the real method
                # (``action_request_quit``), which also accepts the origin key.
                self._call_app("action_request_quit", "ctrl+d")
            else:
                self.action_delete_right()
            return
        await super()._on_key(event)
