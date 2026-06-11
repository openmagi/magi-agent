"""Textual command-palette providers for the Magi TUI (PR2.1+).

We use Textual 8.2.7's BUILT-IN command palette (``App.COMMANDS`` +
``COMMAND_PALETTE_BINDING``) instead of hand-rolling opencode's
``command-palette.tsx``. Two providers ship here:

* :class:`CommandPaletteProvider` — every TUI slash-command from the injected
  ``CommandRegistry`` (``list_for(CommandSurface(tui=True, headless=False))``).
  Choosing a hit routes through the SAME submission path a typed ``/name`` uses
  (``PromptInput.PromptSubmitted`` → ``on_prompt_input_prompt_submitted``), so a
  command launched from the palette and one typed at the prompt are
  indistinguishable downstream — and both pick up PR2.2's executor.
* :class:`AppActionProvider` — app-level actions (open the model picker, session
  list, help dialog, cancel the turn). These call the public action methods the
  app exposes; PR2.3–2.5 add the dialog openers.

The provider reads the live registry/app off ``self.app`` (the Textual palette
constructs providers with the active screen and exposes ``self.app``). A test
seam ``_app_ref`` lets unit tests drive a provider without opening the palette.
"""

from __future__ import annotations

from collections.abc import Iterable

from textual.command import DiscoveryHit, Hit, Hits, Provider

from magi_agent.cli.contracts import CommandRegistry, CommandSurface
from magi_agent.cli.tui.theme import MAGI_THEMES

__all__ = [
    "CommandPaletteProvider",
    "AppActionProvider",
    "ThemeProvider",
    "tui_command_names",
]

# The TUI surface mask — palette commands are the interactive set.
_TUI_SURFACE = CommandSurface(tui=True, headless=False)


def tui_command_names(registry: CommandRegistry) -> list[str]:
    """Non-empty TUI-surfaced slash-command names (bare, no ``/`` prefix).

    Shared by every TUI surface that lists slash commands (palette discovery,
    the welcome banner, and the help dialog) so the
    ``list_for(CommandSurface(tui=True, headless=False))`` + non-empty-name
    filter lives in one place. The ``/`` prefix is applied at each display site.
    """

    out: list[str] = []
    for command in registry.list_for(_TUI_SURFACE):
        name = getattr(command, "name", "")
        if isinstance(name, str) and name:
            out.append(name)
    return out


def _command_names(app: object) -> list[str]:
    """Slash-command names visible in the TUI, from the injected registry."""

    commands = getattr(app, "_commands", None)
    if commands is None:
        return []
    return tui_command_names(commands)


class CommandPaletteProvider(Provider):
    """List the TUI slash-commands as palette hits."""

    # Tests may set this to drive the provider without an open palette.
    _app_ref: object | None = None

    @property
    def _magi_app(self) -> object:
        return self._app_ref if self._app_ref is not None else self.app

    async def discover(self) -> Hits:
        app = self._magi_app
        for name in _command_names(app):
            label = f"/{name}"
            yield DiscoveryHit(
                label,
                self._make_runner(app, name),
                text=label,
                help="slash command",
            )

    async def search(self, query: str) -> Hits:
        app = self._magi_app
        matcher = self.matcher(query)
        for name in _command_names(app):
            label = f"/{name}"
            score = matcher.match(label)
            if score > 0:
                yield Hit(
                    score,
                    matcher.highlight(label),
                    self._make_runner(app, name),
                    text=label,
                    help="slash command",
                )

    @staticmethod
    def _make_runner(app: object, name: str):
        def _run() -> None:
            # Route exactly like a typed ``/name``: build a Submission and post
            # the SAME message the prompt input posts, so PR2.2's executor and
            # the single-turn invariant apply uniformly.
            submit = getattr(app, "submit_command", None)
            if callable(submit):
                submit(name, "")

        return _run


class AppActionProvider(Provider):
    """App-level actions (open dialogs, cancel turn). PR2.3–2.5 add openers."""

    _app_ref: object | None = None

    @property
    def _magi_app(self) -> object:
        return self._app_ref if self._app_ref is not None else self.app

    def _actions(self, app: object) -> Iterable[tuple[str, str, object]]:
        # (label, help, callback) — only include openers the app actually has,
        # so PR2.1 ships with just "Cancel turn" and later PRs add their entries.
        actions: list[tuple[str, str, object]] = []
        cancel = getattr(app, "action_cancel_turn", None)
        if callable(cancel):
            actions.append(("Cancel turn", "abort the in-flight turn", cancel))
        for attr, label, help_text in (
            ("action_open_model_picker", "Switch model", "pick a model"),
            ("action_open_session_list", "Sessions", "resume a session"),
            ("action_open_help", "Help", "keybindings & commands"),
        ):
            fn = getattr(app, attr, None)
            if callable(fn):
                actions.append((label, help_text, fn))
        return actions

    async def discover(self) -> Hits:
        app = self._magi_app
        for label, help_text, callback in self._actions(app):
            yield DiscoveryHit(label, callback, text=label, help=help_text)

    async def search(self, query: str) -> Hits:
        app = self._magi_app
        matcher = self.matcher(query)
        for label, help_text, callback in self._actions(app):
            score = matcher.match(label)
            if score > 0:
                yield Hit(
                    score,
                    matcher.highlight(label),
                    callback,
                    text=label,
                    help=help_text,
                )


class ThemeProvider(Provider):
    """Palette provider listing the curated themes; selecting one switches live.

    Each ``MAGI_THEMES`` name becomes a ``Theme: <name>`` palette entry whose
    callback calls ``app.select_theme(name)`` — the SAME entrypoint ``ctrl+t``'s
    cycle and any future binding use, so a palette pick and a cycle both set +
    persist the theme identically (PR4.1). Reads the live app off ``self.app``;
    a ``_app_ref`` test seam mirrors the other providers for unit drives.
    """

    _app_ref: object | None = None

    @property
    def _magi_app(self) -> object:
        return self._app_ref if self._app_ref is not None else self.app

    async def discover(self) -> Hits:
        app = self._magi_app
        for name in MAGI_THEMES:
            label = f"Theme: {name}"
            yield DiscoveryHit(
                label,
                _select_theme_runner(app, name),
                text=label,
                help="switch theme",
            )

    async def search(self, query: str) -> Hits:
        app = self._magi_app
        matcher = self.matcher(query)
        for name in MAGI_THEMES:
            label = f"Theme: {name}"
            score = matcher.match(label)
            if score > 0:
                yield Hit(
                    score,
                    matcher.highlight(label),
                    _select_theme_runner(app, name),
                    text=label,
                    help="switch theme",
                )


def _select_theme_runner(app: object, name: str):
    """Bind ``app.select_theme(name)`` as a zero-arg palette callback."""

    def _run() -> None:
        select = getattr(app, "select_theme", None)
        if callable(select):
            select(name)

    return _run
