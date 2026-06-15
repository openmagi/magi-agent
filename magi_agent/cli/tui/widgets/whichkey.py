"""PR4.4 — which-key chord-hint overlay.

``chord_continuations`` is the pure seam: given a pending chord prefix + the
active contexts + the merged bindings, it returns the
``(next-keystroke-label, action)`` pairs that would continue or complete a chord
from the current prefix (e.g. after ``ctrl+x`` it offers ``ctrl+k →
chat:killAgents``). It is unit-testable without an App.

``WhichKeyOverlay`` is a docked ``Static`` that renders those hints dim at the
bottom of the screen while a chord is pending, and hides itself when the chord
resolves or cancels (the app drives it from ``on_key``).
"""

from __future__ import annotations

from textual.widgets import Static

from magi_agent.cli.keybindings.resolver import keystrokes_equal
from magi_agent.cli.keybindings.schema import Context, Keystroke, ParsedBinding

__all__ = ["chord_continuations", "WhichKeyOverlay"]

# Display-only labels for action ids shown in the which-key overlay. The raw
# ``Action`` enum values (e.g. ``chat:killAgents``) are machine vocab and stay
# byte-for-byte on the binding/seam; this map ONLY humanizes them at render
# time. Unknown ids (incl. ``command:<name>`` forms) fall back to the raw id.
_ACTION_LABELS: dict[str, str] = {
    "chat:killAgents": "Stop agents",
    "chat:stash": "Stash draft",
    "chat:submit": "Send",
    "chat:cancel": "Interrupt",
    "chat:newline": "New line",
    "global:quit": "Quit",
}


def _keystroke_label(ks: Keystroke) -> str:
    """Render a keystroke back to a ``mod+...+key`` label for display."""

    mods: list[str] = []
    if ks.ctrl:
        mods.append("ctrl")
    # alt and meta are collapsed by the resolver; show a single ``alt`` label so
    # the hint matches what the user typed conceptually.
    if ks.alt or ks.meta:
        mods.append("alt")
    if ks.shift:
        mods.append("shift")
    if ks.super:
        mods.append("super")
    return "+".join(mods + [ks.key]) if mods else ks.key


def chord_continuations(
    pending: tuple[Keystroke, ...] | None,
    active_contexts: list[Context],
    bindings: list[ParsedBinding],
) -> list[tuple[str, str]]:
    """Return ``[(next_key_label, action)]`` for chords extending ``pending``.

    Only bindings whose context is active, whose chord is strictly longer than
    ``pending`` and whose first ``len(pending)`` keystrokes match ``pending`` are
    offered. ``null``-action (unbound) entries are skipped. Duplicate next-key
    labels collapse to the first seen. Empty/``None`` ``pending`` -> ``[]``.
    """

    if not pending:
        return []
    active = set(active_contexts)
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for binding in bindings:
        if binding.context not in active or binding.action is None:
            continue
        if len(binding.chord) <= len(pending):
            continue
        prefix = binding.chord[: len(pending)]
        if not all(keystrokes_equal(x, y) for x, y in zip(pending, prefix)):
            continue
        next_ks = binding.chord[len(pending)]
        label = _keystroke_label(next_ks)
        if label in seen:
            continue
        seen.add(label)
        out.append((label, binding.action))
    return out


class WhichKeyOverlay(Static):
    """A bottom-docked dim hint overlay listing a pending chord's continuations."""

    DEFAULT_CSS = """
    WhichKeyOverlay {
        dock: bottom;
        height: auto;
        padding: 0 1;
        color: $text-muted;
        display: none;
    }
    WhichKeyOverlay.visible { display: block; }
    """

    def show_hints(self, hints: list[tuple[str, str]]) -> None:
        """Render ``hints`` and reveal the overlay; empty hints -> hide."""

        if not hints:
            self.hide_hints()
            return
        line = "   ".join(
            f"{key} → {_ACTION_LABELS.get(action, action)}" for key, action in hints
        )
        self.update(f"…  {line}")
        self.add_class("visible")

    def hide_hints(self) -> None:
        """Clear the overlay text and hide it."""

        self.update("")
        self.remove_class("visible")
