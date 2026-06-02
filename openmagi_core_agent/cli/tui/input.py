"""Prompt input widget + submission routing for the Magi TUI (PR-E2).

``PromptInput`` wraps a Textual ``Input`` and exposes the pre-cursor text slice
(everything left of the caret) so the :class:`~.autocomplete.AutocompleteRouter`
can compute completions. On submit it classifies the line:

* a line beginning with ``/`` is a **slash command** -> dispatched via the
  injected :class:`~openmagi_core_agent.cli.contracts.CommandRegistry` (looked up
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

from textual.message import Message
from textual.widgets import Input

from openmagi_core_agent.cli.contracts import Command, CommandRegistry

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


class PromptInput(Input):
    """The REPL prompt input widget.

    Posts a :class:`PromptInput.PromptSubmitted` message (carrying a classified
    :class:`Submission`) when the user submits a non-empty line, and clears
    itself. The owning App handles the message.

    Note: we deliberately do NOT shadow ``Input.Submitted`` (Textual's built-in
    message, posted with positional ``(self, value, ...)`` args) — overriding it
    would break the base widget's own dispatch.
    """

    class PromptSubmitted(Message):
        """Posted when the user submits a classified line."""

        def __init__(self, submission: Submission) -> None:
            self.submission = submission
            super().__init__()

    def __init__(self, *, commands: CommandRegistry, **kwargs: object) -> None:
        super().__init__(placeholder="Message Magi…  (/ for commands)", **kwargs)  # type: ignore[arg-type]
        self._commands = commands

    @property
    def precursor(self) -> str:
        """The text slice left of the caret — fed to the autocomplete router."""

        return self.value[: self.cursor_position]

    def classify(self, line: str) -> Submission:
        """Classify ``line`` against the injected registry (exposed for tests)."""

        return classify_line(line, self._commands)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        # Textual fires Input.Submitted on Enter; swallow it and re-post our own
        # classified message so the App has a single submission surface.
        event.stop()
        line = event.value
        if not line.strip():
            return
        submission = self.classify(line)
        self.value = ""
        self.post_message(self.PromptSubmitted(submission))
