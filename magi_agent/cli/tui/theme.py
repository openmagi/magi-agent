"""PR4.1 — curated theme set + registration + persistence for the Magi TUI.

``register_magi_themes(app)`` registers one custom ``magi-dark`` theme (the
built-ins ``tokyo-night``/``nord``/``gruvbox``/``dracula``/``catppuccin-mocha``/
``monokai`` ship with Textual 8.2.7 and need no registration). ``MAGI_THEMES`` is
the ordered cycle list the ``ctrl+t`` action and the palette picker iterate.

The last-chosen theme persists to ``<session-root>/tui/settings.json`` where
``<session-root>`` is ``~/.magi`` by default and is overridable via
``MAGI_CLI_SESSION_DIR`` (the SAME root ``history.py`` / ``session_log.py`` use).
We intentionally reuse that root rather than introducing a new ``MAGI_HOME`` env:
there is one config root for the CLI, and tests already isolate it via that env.

The Magi flat-look (Kevin's PR #353) hardcodes ``background: transparent`` on the
Screen + transcript/live/prompt regions, so a theme only retints
accent/text/primary — switching ``App.theme`` never repaints those regions with a
solid colour. This module does NOT touch region backgrounds.
"""

from __future__ import annotations

import json
from pathlib import Path

from textual.app import App
from textual.theme import Theme

from magi_agent.cli.session_log import _session_root

__all__ = [
    "MAGI_THEMES",
    "MAGI_DARK",
    "DEFAULT_THEME",
    "register_magi_themes",
    "settings_path",
    "load_saved_theme",
    "save_theme",
]


#: The historical default (set bare in PR2.1's ``on_mount``); first in the cycle.
DEFAULT_THEME = "tokyo-night"


#: Ordered curated theme cycle. ``tokyo-night`` first (historical default),
#: ``magi-dark`` last. All but ``magi-dark`` are Textual 8.2.7 built-ins
#: (verified present: ``tokyo-night``/``nord``/``gruvbox``/``dracula``/
#: ``catppuccin-mocha``/``monokai``).
MAGI_THEMES: tuple[str, ...] = (
    "tokyo-night",
    "nord",
    "gruvbox",
    "dracula",
    "catppuccin-mocha",
    "monokai",
    "magi-dark",
)


#: The one custom theme — a magi-branded dark theme (blue/green accents matching
#: the welcome banner's #7aa2f7 / #9ece6a in app.py). ``$background``/``$surface``
#: /``$panel`` are still set so widgets that DO opt into a solid bg (the footer,
#: sidebar, modals) get a coherent palette; the flat regions ignore them because
#: their CSS pins ``background: transparent``.
MAGI_DARK: Theme = Theme(
    name="magi-dark",
    primary="#7aa2f7",
    secondary="#9ece6a",
    accent="#bb9af7",
    warning="#e0af68",
    error="#f7768e",
    success="#9ece6a",
    foreground="#c0caf5",
    background="#1a1b26",
    surface="#24283b",
    panel="#414868",
    dark=True,
)


def register_magi_themes(app: App) -> tuple[str, ...]:
    """Register the custom ``magi-dark`` theme on ``app``; return ``MAGI_THEMES``.

    Idempotent: re-registering an already-registered name is harmless (Textual
    overwrites the entry). The built-in names are left to Textual — they are
    always in ``available_themes``.
    """

    app.register_theme(MAGI_DARK)
    return MAGI_THEMES


def settings_path() -> Path:
    """Path to the small TUI settings file (theme choice etc.).

    Lives under ``<session-root>/tui/settings.json`` (``MAGI_CLI_SESSION_DIR``
    override), alongside the per-session history/draft JSONL files.
    """

    return _session_root() / "tui" / "settings.json"


def load_saved_theme() -> str | None:
    """Return the persisted theme name, or ``None`` (missing/corrupt/unknown).

    A persisted name no longer in ``MAGI_THEMES`` is treated as absent so the app
    falls back to the default rather than crashing on an unregistered theme.
    """

    path = settings_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    theme = data.get("theme") if isinstance(data, dict) else None
    return theme if isinstance(theme, str) and theme in MAGI_THEMES else None


def save_theme(name: str) -> None:
    """Persist ``name`` to the settings file (best-effort; never raises).

    Merges into any existing settings dict so unrelated keys survive. A write
    failure is swallowed — a theme choice not persisting is a cosmetic loss, not
    a reason to crash the UI.
    """

    path = settings_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        existing: dict[str, object] = {}
        if path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    existing = loaded
            except (OSError, json.JSONDecodeError, ValueError):
                existing = {}
        existing["theme"] = name
        path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    except OSError:
        pass
