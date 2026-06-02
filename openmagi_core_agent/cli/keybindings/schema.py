"""PR-E4 — keybinding config contract: contexts, actions, keystroke grammar.

This module is the pure, textual-free contract layer for the Magi CLI
keybinding subsystem. It defines:

* :class:`Context` — the fixed ~18-member context enum.
* :class:`Action` — a small *closed* action enum (only the actions whose
  features v1 ships); file actions are validated against this set, plus the
  special ``command:<name>`` form (Chat-only).
* :class:`Keystroke` — a normalized parsed keystroke ``{key, ctrl, alt, shift,
  meta, super}``.
* :class:`ParsedBinding` — ``{chord, action, context}``.
* the keystroke/chord *grammar* parsers (:func:`parse_keystroke` /
  :func:`parse_chord`) with modifier + key aliases.

Grammar (design source 06-input-keybindings-vim.md §A.1):
  ``mod+mod+key`` — modifiers joined by ``+``. A CHORD is multiple keystrokes
  joined by whitespace; the lone string ``" "`` is the space key (NOT a
  separator). Modifier aliases: ``ctrl|control``, ``alt|opt|option``, ``meta``,
  ``cmd|command|super|win``. Key aliases: ``esc``, ``return``/``enter``,
  ``space``, ``up|down|left|right``.

NOTE: vim mode is intentionally NOT modeled here — it is deferred to v1.1
(see package docstring TODO).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

__all__ = [
    "Context",
    "Action",
    "Keystroke",
    "ParsedBinding",
    "MODIFIER_ALIASES",
    "KEY_ALIASES",
    "COMMAND_RE",
    "KeystrokeParseError",
    "parse_keystroke",
    "parse_chord",
    "is_known_action",
    "is_command_action",
]


# ---------------------------------------------------------------------------
# Contexts — fixed enum (18). v1 only USES a small subset (Global/Chat/
# Autocomplete/Confirmation/Select) but the enum is fixed at ~18.
# ---------------------------------------------------------------------------
class Context(str, Enum):
    GLOBAL = "Global"
    CHAT = "Chat"
    AUTOCOMPLETE = "Autocomplete"
    CONFIRMATION = "Confirmation"
    HELP = "Help"
    TRANSCRIPT = "Transcript"
    HISTORY_SEARCH = "HistorySearch"
    TASK = "Task"
    THEME_PICKER = "ThemePicker"
    SETTINGS = "Settings"
    TABS = "Tabs"
    ATTACHMENTS = "Attachments"
    FOOTER = "Footer"
    MESSAGE_SELECTOR = "MessageSelector"
    DIFF_DIALOG = "DiffDialog"
    MODEL_PICKER = "ModelPicker"
    SELECT = "Select"
    PLUGIN = "Plugin"


# ---------------------------------------------------------------------------
# Actions — a small CLOSED enum for the features that exist in v1. Every action
# string in a config file is validated against this set (plus ``command:<name>``).
# ---------------------------------------------------------------------------
class Action(str, Enum):
    GLOBAL_QUIT = "global:quit"
    CHAT_SUBMIT = "chat:submit"
    CHAT_CANCEL = "chat:cancel"  # interrupt
    CHAT_NEWLINE = "chat:newline"
    CHAT_STASH = "chat:stash"
    CHAT_KILL_AGENTS = "chat:killAgents"
    AUTOCOMPLETE_ACCEPT = "autocomplete:accept"
    AUTOCOMPLETE_NEXT = "autocomplete:next"
    AUTOCOMPLETE_PREV = "autocomplete:prev"
    AUTOCOMPLETE_DISMISS = "autocomplete:dismiss"
    CONFIRMATION_ALLOW = "confirmation:allow"
    CONFIRMATION_DENY = "confirmation:deny"


_KNOWN_ACTIONS: frozenset[str] = frozenset(a.value for a in Action)

#: ``command:<name>`` form — legal ONLY in the Chat context.
COMMAND_RE = re.compile(r"^command:[a-zA-Z0-9:\-_]+$")


def is_command_action(action: str) -> bool:
    """True if ``action`` is a syntactically valid ``command:<name>`` string."""
    return bool(COMMAND_RE.match(action))


def is_known_action(action: str) -> bool:
    """True if ``action`` is a known closed-enum action OR a ``command:`` form."""
    return action in _KNOWN_ACTIONS or is_command_action(action)


# ---------------------------------------------------------------------------
# Keystroke grammar
# ---------------------------------------------------------------------------
#: modifier alias -> canonical attribute name
MODIFIER_ALIASES: dict[str, str] = {
    "ctrl": "ctrl",
    "control": "ctrl",
    "alt": "alt",
    "opt": "alt",
    "option": "alt",
    "meta": "meta",
    "shift": "shift",
    "cmd": "super",
    "command": "super",
    "super": "super",
    "win": "super",
}

#: key alias -> canonical key name
KEY_ALIASES: dict[str, str] = {
    "esc": "escape",
    "escape": "escape",
    "return": "enter",
    "enter": "enter",
    "space": "space",
    "up": "up",
    "down": "down",
    "left": "left",
    "right": "right",
}


class KeystrokeParseError(ValueError):
    """Raised by :func:`parse_keystroke` on an unparseable keystroke string."""


@dataclass(frozen=True)
class Keystroke:
    """A normalized single keystroke.

    ``key`` is the lowercased canonical key name (aliases resolved). Modifier
    flags are booleans. ``super`` is kept distinct from ``alt``/``meta`` (kitty
    protocol only); ``alt`` and ``meta`` are collapsed at *equality* time in the
    resolver, NOT here.
    """

    key: str
    ctrl: bool = False
    alt: bool = False
    shift: bool = False
    meta: bool = False
    super: bool = False


@dataclass(frozen=True)
class ParsedBinding:
    """A single binding: a chord (tuple of keystrokes) -> action, in a context.

    ``action`` is a known action / ``command:<name>`` string, or ``None`` to
    UNBIND (shadow) a default.
    """

    chord: tuple[Keystroke, ...]
    action: str | None
    context: Context


def parse_keystroke(text: str) -> Keystroke:
    """Parse one keystroke string ``mod+mod+key`` into a :class:`Keystroke`.

    Raises :class:`KeystrokeParseError` on empty parts or a missing key. The
    lone string ``" "`` (a single space) parses to the space key.
    """
    if text == " ":
        return Keystroke(key="space")
    if not text:
        raise KeystrokeParseError("empty keystroke")

    parts = text.split("+")
    if any(p == "" for p in parts):
        raise KeystrokeParseError(f"empty modifier/key part in {text!r}")

    ctrl = alt = shift = meta = super_ = False
    key: str | None = None
    for i, raw in enumerate(parts):
        token = raw.lower()
        last = i == len(parts) - 1
        if token in MODIFIER_ALIASES and not last:
            attr = MODIFIER_ALIASES[token]
            if attr == "ctrl":
                ctrl = True
            elif attr == "alt":
                alt = True
            elif attr == "shift":
                shift = True
            elif attr == "meta":
                meta = True
            elif attr == "super":
                super_ = True
            continue
        # last part is the key (or a non-modifier token); a token that is BOTH a
        # modifier name and last is treated as the key only if nothing else.
        if last:
            key = KEY_ALIASES.get(token, token)
        else:
            raise KeystrokeParseError(f"unexpected token {raw!r} in {text!r}")

    if not key:
        raise KeystrokeParseError(f"no key in {text!r}")
    return Keystroke(key=key, ctrl=ctrl, alt=alt, shift=shift, meta=meta, super=super_)


def parse_chord(text: str) -> tuple[Keystroke, ...]:
    """Parse a chord string into a tuple of keystrokes.

    Keystrokes are whitespace-separated; the lone string ``" "`` is the space
    key (NOT a separator).
    """
    if text == " ":
        return (Keystroke(key="space"),)
    pieces = text.split()
    if not pieces:
        raise KeystrokeParseError(f"empty chord {text!r}")
    return tuple(parse_keystroke(p) for p in pieces)
