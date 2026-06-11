"""Help dialog for the Magi TUI (PR2.5).

A read-only ``ModalScreen`` rendering a keybinding + command reference. The
content is built by :func:`build_help_sections` from two already-resolved
inputs (so the formatting is unit-testable without an App):

* ``bindings`` — ``(key, description)`` pairs (the app's ``BINDINGS`` plus the
  command-palette key the caller chooses to surface).
* ``commands`` — slash-command names from the ``CommandRegistry`` (rendered
  ``/name``).

The app constructs the inputs from its live ``BINDINGS``, the
``COMMAND_PALETTE_BINDING``, and the shared ``tui_command_names(registry)``
helper (PR2.1) — see :meth:`HelpDialog.from_app`.

Unlike the model/session dialogs this is NOT an ``OptionList`` modal — it is a
scrollable ``Static`` reference — so it does not share the ``OptionListModal``
base; it owns its own escape/enter -> close skeleton.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

__all__ = ["HelpDialog", "build_help_sections", "PROMPT_KEYS", "ENV_HELP"]


# Phase-1 prompt-input keys. These live on ``PromptInput`` (its ``_on_key``),
# NOT on ``App.BINDINGS``, so the BINDINGS-driven help reference would otherwise
# omit them. Surfaced here as a static (key, description) reference so ↑/↓
# recall, Shift+Enter newline, Ctrl+S drafts, and Enter submit are discoverable.
PROMPT_KEYS: tuple[tuple[str, str], ...] = (
    ("Enter", "Submit prompt"),
    ("Shift+Enter", "Insert newline"),
    ("↑ / ↓", "History recall"),
    ("Ctrl+S", "Stash / restore draft"),
)


# Opt-in environment toggles. These are not keybindings or commands, so they are
# otherwise invisible (live only in code comments). Surface them here so a user
# can discover the gated features.
ENV_HELP: tuple[tuple[str, str], ...] = (
    (
        "MAGI_TUI_NOTIFY_BELL=1",
        "Ring the terminal bell on turn-done / permission when unfocused",
    ),
    (
        "MAGI_STREAM_THINKING=1",
        "show the agent's reasoning as a dim one-line trace",
    ),
)


def build_help_sections(
    *,
    bindings: list[tuple[str, str]],
    commands: list[str],
    prompt_keys: list[tuple[str, str]] | None = None,
    env: list[tuple[str, str]] | None = None,
) -> list[tuple[str, list[str]]]:
    """Return ``[(section_title, lines)]`` for the help reference.

    Pure formatting — no App, no Textual widgets — so it is unit-testable.
    Empty sections (no keys / no commands / no prompt keys / no env) are dropped.
    ``prompt_keys`` are the Phase-1 ``PromptInput`` keys (not in ``BINDINGS``);
    they render as a dedicated "Prompt" section. ``env`` are opt-in environment
    toggles (not keys/commands); they render as an "Environment" section.
    """

    key_lines = [f"  {key:<12} {desc}" for key, desc in bindings if key]
    prompt_lines = [
        f"  {key:<12} {desc}" for key, desc in (prompt_keys or []) if key
    ]
    command_lines = [f"  /{name}" for name in commands if name]
    env_lines = [f"  {name}\n    {desc}" for name, desc in (env or []) if name]
    sections: list[tuple[str, list[str]]] = []
    if prompt_lines:
        sections.append(("Prompt", prompt_lines))
    if key_lines:
        sections.append(("Keybindings", key_lines))
    if command_lines:
        sections.append(("Commands", command_lines))
    if env_lines:
        sections.append(("Environment", env_lines))
    return sections


def _render(sections: list[tuple[str, list[str]]]) -> str:
    chunks: list[str] = []
    for title, lines in sections:
        chunks.append(title)
        chunks.extend(lines)
        chunks.append("")
    return "\n".join(chunks).rstrip()


class HelpDialog(ModalScreen[None]):
    """Show keybindings + commands; dismiss on escape/enter (read-only)."""

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("enter", "close", "Close"),
    ]

    CSS = """
    HelpDialog { align: center middle; }
    #help-dialog {
        width: 72;
        height: auto;
        max-height: 80%;
        padding: 1 2;
        background: $panel;
        border: thick $accent;
    }
    #help-body { height: auto; }
    """

    def __init__(
        self,
        *,
        bindings: list[tuple[str, str]],
        commands: list[str],
        prompt_keys: list[tuple[str, str]] | None = None,
        env: list[tuple[str, str]] | None = None,
    ) -> None:
        super().__init__()
        self._sections = build_help_sections(
            bindings=bindings,
            commands=commands,
            prompt_keys=list(prompt_keys) if prompt_keys is not None
            else list(PROMPT_KEYS),
            env=list(env) if env is not None else list(ENV_HELP),
        )

    @classmethod
    def from_app(cls, app: object) -> "HelpDialog":
        """Build a HelpDialog from a live ``MagiTuiApp``.

        Pulls ``BINDINGS`` (key, description), the command-palette key
        (``COMMAND_PALETTE_BINDING``), and the TUI slash-command names from the
        injected registry via the shared ``tui_command_names`` helper. Keeps the
        App seam thin; the formatting lives in :func:`build_help_sections`.
        """

        from magi_agent.cli.tui.palette import tui_command_names  # noqa: PLC0415

        binding_pairs: list[tuple[str, str]] = []
        for entry in getattr(app, "BINDINGS", []) or []:
            key, desc = _binding_key_desc(entry)
            if key:
                binding_pairs.append((key, desc))
        palette_key = getattr(app, "COMMAND_PALETTE_BINDING", "ctrl+p")
        if isinstance(palette_key, str) and palette_key:
            binding_pairs.append((palette_key, "Command palette"))

        names: list[str] = []
        registry = getattr(app, "_commands", None)
        if registry is not None:
            names = tui_command_names(registry)
        return cls(bindings=binding_pairs, commands=names)

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="help-dialog"):
            yield Static(_render(self._sections), id="help-body")

    def action_close(self) -> None:
        self.dismiss(None)


def _normalize_keys(key: str) -> str:
    """Render a (possibly comma-separated) key string for the help column.

    Textual allows multi-key bindings like ``"ctrl+c,escape"``. Split on the
    comma, strip each part, and join with ``" / "`` so the help reads
    ``ctrl+c / escape`` instead of the raw, overflow-prone ``ctrl+c,escape``.
    """

    parts = [part.strip() for part in key.split(",")]
    return " / ".join(part for part in parts if part)


def _binding_key_desc(entry: object) -> tuple[str, str]:
    """Best-effort ``(key, description)`` from a Textual ``BINDINGS`` entry.

    Entries may be a ``Binding`` instance or a ``(keys, action, description?)``
    tuple. Returns ``("", "")`` when no key can be derived. A ``Binding`` with
    ``show=False`` is treated as hidden and also returns ``("", "")`` so the
    caller (``from_app`` / ``build_help_sections``, which both drop empty-key
    entries) skips it. Tuple-form entries have no ``show`` field, so they are
    always rendered. Multi-key strings are normalized via ``_normalize_keys``.
    """

    if isinstance(entry, Binding):
        if not getattr(entry, "show", True):
            return ("", "")
        return (
            _normalize_keys(str(entry.key)),
            str(entry.description or entry.action),
        )
    if isinstance(entry, tuple) and entry:
        key = _normalize_keys(str(entry[0]))
        desc = str(entry[2]) if len(entry) >= 3 else (
            str(entry[1]) if len(entry) >= 2 else ""
        )
        return (key, desc)
    return ("", "")
