"""PR-E4 — the pure chord-resolution algorithm + a duck-typed event adapter.

This is the reimplementable core (design source 06-input-keybindings-vim.md
§A.3-A.4). :func:`resolve` is a pure function over a normalized
:class:`~openmagi_core_agent.cli.keybindings.schema.Keystroke`, the active
contexts, the merged binding list, and the pending chord (or ``None``).

Quirks ported verbatim (§A.4):

* **alt = meta collapse** — legacy terminals can't distinguish Alt and Meta, so
  equality treats ``(a.alt or a.meta) == (b.alt or b.meta)`` as ONE modifier.
  Only ``super`` (kitty protocol) is distinct.
* **escape-sets-meta** — many terminals report Escape with ``meta=True``; we
  force ``meta=False`` when the resolved key is ``escape`` so bare-``escape``
  bindings (and chord cancellation) still match.

Stream F wiring (NOT done here): ``App.on_key`` converts a Textual key event to
a :class:`Keystroke` via :func:`keystroke_from_event`, calls :func:`resolve`,
then dispatches ``self.run_action(result.action)`` on MATCH. ``event.stop()`` on
MATCH / CHORD_STARTED / UNBOUND; let NONE bubble so Textual's own bindings fire.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Iterable, Sequence

from openmagi_core_agent.cli.keybindings.schema import (
    KEY_ALIASES,
    Context,
    Keystroke,
    ParsedBinding,
)

__all__ = [
    "ResultKind",
    "Result",
    "build_keystroke",
    "keystrokes_equal",
    "resolve",
    "active_context_order",
    "keystroke_from_event",
]


class ResultKind(Enum):
    """The five terminal outcomes of a single resolution step."""

    MATCH = auto()
    UNBOUND = auto()
    NONE = auto()
    CHORD_STARTED = auto()
    CHORD_CANCELLED = auto()


@dataclass(frozen=True)
class Result:
    """Resolution result. ``action`` is set for MATCH; ``pending`` for CHORD_STARTED."""

    kind: ResultKind
    action: str | None = None
    pending: tuple[Keystroke, ...] | None = None


def build_keystroke(ks: Keystroke) -> Keystroke:
    """Apply the escape-sets-meta quirk: force ``meta=False`` when key=='escape'."""
    if ks.key == "escape" and ks.meta:
        return Keystroke(
            key=ks.key,
            ctrl=ks.ctrl,
            alt=ks.alt,
            shift=ks.shift,
            meta=False,
            super=ks.super,
        )
    return ks


def keystrokes_equal(a: Keystroke, b: Keystroke) -> bool:
    """Keystroke equality with the alt=meta collapse (§A.4).

    ``alt`` and ``meta`` are treated as one modifier; ``super`` stays distinct.
    """
    return (
        a.key == b.key
        and a.ctrl == b.ctrl
        and a.shift == b.shift
        and (a.alt or a.meta) == (b.alt or b.meta)
        and a.super == b.super
    )


def _chord_equal(test: Sequence[Keystroke], chord: Sequence[Keystroke]) -> bool:
    if len(test) != len(chord):
        return False
    return all(keystrokes_equal(x, y) for x, y in zip(test, chord))


def _is_strict_prefix(test: Sequence[Keystroke], chord: Sequence[Keystroke]) -> bool:
    if len(chord) <= len(test):
        return False
    return all(keystrokes_equal(x, y) for x, y in zip(test, chord[: len(test)]))


def _chord_key(chord: Sequence[Keystroke]) -> tuple[Any, ...]:
    """A normalized, alt=meta-collapsed hashable identity for a chord."""
    out: list[tuple[Any, ...]] = []
    for k in chord:
        out.append((k.key, k.ctrl, k.shift, bool(k.alt or k.meta), k.super))
    return tuple(out)


def active_context_order(
    screen_contexts: Iterable[Context],
    this_context: Context | None = None,
) -> list[Context]:
    """Build the priority order ``[...screen, this, Global]``, de-duplicated."""
    order: list[Context] = []
    for c in screen_contexts:
        if c not in order:
            order.append(c)
    if this_context is not None and this_context not in order:
        order.append(this_context)
    if Context.GLOBAL not in order:
        order.append(Context.GLOBAL)
    return order


def resolve(
    keystroke: Keystroke,
    active_contexts: Sequence[Context],
    bindings: Sequence[ParsedBinding],
    pending: tuple[Keystroke, ...] | None,
) -> Result:
    """Resolve one keystroke to a :class:`Result` (pure; no I/O).

    Algorithm (design §A.3): ESC cancels a pending chord; build the keystroke
    (escape-meta quirk); form the test chord; filter bindings to active
    contexts; check whether the test is a strict prefix of any *non-null* longer
    chord (grouped by chord-identity so a null override shadows its default) and
    prefer that (chord_started); otherwise take the LAST exact match
    (null -> unbound, else match); otherwise cancel a pending chord or NONE.
    """
    # 1. ESC mid-chord -> cancel.
    if keystroke.key == "escape" and pending is not None:
        return Result(ResultKind.CHORD_CANCELLED)

    # 2. build keystroke (escape-meta quirk; always returns a Keystroke).
    cur = build_keystroke(keystroke)

    # 3. test chord.
    test: tuple[Keystroke, ...] = (*pending, cur) if pending else (cur,)

    # 4. filter to active contexts.
    active = set(active_contexts)
    ctx_bindings = [b for b in bindings if b.context in active]

    # 5. prefix check grouped by chord-identity (last-wins so null shadows).
    winners: dict[tuple[Any, ...], str | None] = {}
    for b in ctx_bindings:
        if _is_strict_prefix(test, b.chord):
            winners[_chord_key(b.chord)] = b.action
    if any(action is not None for action in winners.values()):
        return Result(ResultKind.CHORD_STARTED, pending=test)

    # 6. exact match — LAST wins.
    exact: ParsedBinding | None = None
    for b in ctx_bindings:
        if _chord_equal(test, b.chord):
            exact = b
    if exact is not None:
        if exact.action is None:
            return Result(ResultKind.UNBOUND)
        return Result(ResultKind.MATCH, action=exact.action)

    # 7. nothing.
    return Result(ResultKind.CHORD_CANCELLED if pending else ResultKind.NONE)


# ---------------------------------------------------------------------------
# Duck-typed event adapter (NO ``import textual``)
# ---------------------------------------------------------------------------
_EVENT_MODIFIER_PREFIXES = ("ctrl", "alt", "shift", "meta", "super")


def keystroke_from_event(event: Any) -> Keystroke | None:
    """Convert a Textual-like key event into a :class:`Keystroke` by DUCK-TYPING.

    Reads ``event.key`` (Textual's canonical name, e.g. ``"ctrl+s"``,
    ``"escape"``, ``"x"``) and falls back to ``event.character`` for a literal
    typed character. NO ``import textual`` — works on any object exposing those
    attributes. Returns ``None`` when no usable key can be derived.

    Stream F uses this in ``App.on_key`` to feed :func:`resolve`.
    """
    raw = getattr(event, "key", None)
    if not raw:
        char = getattr(event, "character", None)
        if char:
            raw = char
        else:
            return None
    raw = str(raw)

    parts = raw.split("+")
    ctrl = alt = shift = meta = super_ = False
    key = raw
    if len(parts) > 1 and all(
        p.lower() in _EVENT_MODIFIER_PREFIXES for p in parts[:-1]
    ):
        for p in parts[:-1]:
            pl = p.lower()
            if pl == "ctrl":
                ctrl = True
            elif pl == "alt":
                alt = True
            elif pl == "shift":
                shift = True
            elif pl == "meta":
                meta = True
            elif pl == "super":
                super_ = True
        key = parts[-1]
    key = key.lower()
    key = KEY_ALIASES.get(key, key)
    return Keystroke(key=key, ctrl=ctrl, alt=alt, shift=shift, meta=meta, super=super_)
