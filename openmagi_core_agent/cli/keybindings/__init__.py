"""PR-E4 — Magi CLI keybinding subsystem (pure, textual-free).

This package is the chord-capable, override-aware keybinding layer for the Magi
CLI TUI. It is intentionally import-clean: NO ``textual`` / ``rich`` /
``google-adk`` import anywhere, so it is importable everywhere and trivially
unit-testable.

Layout
------
``schema``    config contract — :class:`Context`, :class:`Action`,
              :class:`Keystroke`, :class:`ParsedBinding`, the keystroke/chord
              grammar parsers.
``defaults``  the built-in default keymap.
``loader``    load -> merge (last-wins) -> validate (non-fatal warnings).
``resolver``  the pure chord-resolution algorithm + a duck-typed event adapter.

Stream F (NOT this PR) writes the ~10-line ``App.on_key`` adapter that converts
a Textual key event -> :class:`Keystroke` (via :func:`keystroke_from_event`),
calls :func:`resolve`, and dispatches ``self.run_action(action)``.

TODO (deferred to v1.1): a vim NORMAL/INSERT state machine + operator/motion/
text-object composition is intentionally NOT implemented here. It is cleanly
isolable behind an ``editor_mode == "vim"`` flag in a follow-up stream.
"""

from __future__ import annotations

from openmagi_core_agent.cli.keybindings.resolver import (
    Result,
    ResultKind,
    active_context_order,
    build_keystroke,
    keystroke_from_event,
    keystrokes_equal,
    resolve,
)
from openmagi_core_agent.cli.keybindings.schema import (
    Action,
    Context,
    Keystroke,
    KeystrokeParseError,
    ParsedBinding,
    is_command_action,
    is_known_action,
    parse_chord,
    parse_keystroke,
)

__all__ = [
    # schema
    "Action",
    "Context",
    "Keystroke",
    "KeystrokeParseError",
    "ParsedBinding",
    "is_command_action",
    "is_known_action",
    "parse_chord",
    "parse_keystroke",
    # resolver
    "Result",
    "ResultKind",
    "active_context_order",
    "build_keystroke",
    "keystroke_from_event",
    "keystrokes_equal",
    "resolve",
    # loader entry (lazy import to keep this module cheap)
    "load_keybindings",
]


def load_keybindings(path: str | None):  # noqa: ANN201 - thin re-export
    """Re-export of :func:`openmagi_core_agent.cli.keybindings.loader.load_keybindings`."""
    from openmagi_core_agent.cli.keybindings.loader import load_keybindings as _lk

    return _lk(path)
