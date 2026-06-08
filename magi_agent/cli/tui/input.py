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

    def __init__(self, *, commands: CommandRegistry, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._commands = commands
        # A single-line-looking prompt by default; grows as the user types.
        self.show_line_numbers = False
        self.soft_wrap = True
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

        if event.key == "enter":
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
        await super()._on_key(event)
