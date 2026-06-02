"""PR-E4 — the built-in default keymap (a list of :class:`ParsedBinding`).

Minimal but real: a handful per active context, including at least one CHORD
default (``ctrl+x ctrl+k``) and the reserved-but-special keys (``ctrl+c`` /
``ctrl+d``) which are present here *so the resolver can find them* but get
special handling and cannot be rebound (validated in :mod:`.loader`).

Design source: 06-input-keybindings-vim.md §2.2 / §A (Magi v1 lean subset:
Global / Chat / Autocomplete / Confirmation / Select).
"""

from __future__ import annotations

from magi_agent.cli.keybindings.schema import (
    Action,
    Context,
    ParsedBinding,
    parse_chord,
)

__all__ = ["default_bindings", "DEFAULT_SPEC"]


#: (context, keystroke/chord string, action) — the canonical default keymap.
DEFAULT_SPEC: tuple[tuple[Context, str, str], ...] = (
    # Global: reserved-but-special interrupt/exit + quit.
    (Context.GLOBAL, "ctrl+c", Action.CHAT_CANCEL.value),
    (Context.GLOBAL, "ctrl+d", Action.GLOBAL_QUIT.value),
    (Context.GLOBAL, "ctrl+q", Action.GLOBAL_QUIT.value),
    # Chat: submit/cancel/newline + stash + a CHORD for killAgents.
    (Context.CHAT, "enter", Action.CHAT_SUBMIT.value),
    (Context.CHAT, "escape", Action.CHAT_CANCEL.value),
    (Context.CHAT, "shift+enter", Action.CHAT_NEWLINE.value),
    (Context.CHAT, "ctrl+s", Action.CHAT_STASH.value),
    (Context.CHAT, "ctrl+x ctrl+k", Action.CHAT_KILL_AGENTS.value),
    # Autocomplete: accept / navigate / dismiss.
    (Context.AUTOCOMPLETE, "tab", Action.AUTOCOMPLETE_ACCEPT.value),
    (Context.AUTOCOMPLETE, "down", Action.AUTOCOMPLETE_NEXT.value),
    (Context.AUTOCOMPLETE, "up", Action.AUTOCOMPLETE_PREV.value),
    (Context.AUTOCOMPLETE, "escape", Action.AUTOCOMPLETE_DISMISS.value),
    # Confirmation modal.
    (Context.CONFIRMATION, "y", Action.CONFIRMATION_ALLOW.value),
    (Context.CONFIRMATION, "n", Action.CONFIRMATION_DENY.value),
)


def default_bindings() -> list[ParsedBinding]:
    """Return a fresh list of the default :class:`ParsedBinding` objects."""
    return [
        ParsedBinding(chord=parse_chord(chord), action=action, context=context)
        for context, chord, action in DEFAULT_SPEC
    ]
