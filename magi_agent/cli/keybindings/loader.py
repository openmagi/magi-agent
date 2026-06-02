"""PR-E4 — load -> merge -> validate the keybindings config (never throws).

:func:`load_keybindings` parses the JSON object-wrapper, merges
``defaults + user_blocks`` (last-wins; ``null`` unbinds), and runs a battery of
NON-fatal validators producing :class:`Warning` records. Any error (missing file,
malformed JSON, bad shape) degrades to defaults-only plus a warning — it NEVER
raises.

Validation (design source 06-input-keybindings-vim.md §A.2):

* unparseable keystrokes;
* unknown context / unknown action;
* ``command:<name>`` format + Chat-only rule;
* **duplicate-key detection by scanning the RAW JSON text** (``json.loads``
  silently drops duplicate object keys — a parsed dict can't express the dupe);
* per-context normalized-duplicate keystroke;
* **reserved-shortcut** check (``NON_REBINDABLE`` = ctrl+c / ctrl+d / ctrl+m;
  ``TERMINAL_RESERVED`` = ctrl+z warn, ctrl+\\ error).

Hot-reload is OUT of scope for PR-E4 (TODO: a watchdog/mtime-poll re-parse +
reactive push lands in a follow-up).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from magi_agent.cli.keybindings.defaults import default_bindings
from magi_agent.cli.keybindings.schema import (
    Context,
    KeystrokeParseError,
    ParsedBinding,
    is_command_action,
    is_known_action,
    parse_chord,
)

__all__ = [
    "Severity",
    "Warning",
    "load_keybindings",
    "NON_REBINDABLE",
    "TERMINAL_RESERVED_WARN",
    "TERMINAL_RESERVED_ERROR",
    "normalize_chord_for_comparison",
]


class Severity(str, Enum):
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class Warning:
    """A non-fatal validation finding. ``message`` is human-readable."""

    severity: Severity
    message: str
    context: str | None = None
    keystroke: str | None = None


# Reserved shortcuts (design §A.2). ctrl+m == Enter.
NON_REBINDABLE: frozenset[str] = frozenset({"ctrl+c", "ctrl+d", "ctrl+m"})
TERMINAL_RESERVED_WARN: frozenset[str] = frozenset({"ctrl+z"})
TERMINAL_RESERVED_ERROR: frozenset[str] = frozenset({"ctrl+\\"})

_VALID_CONTEXTS: dict[str, Context] = {c.value: c for c in Context}


# alt and meta are indistinguishable to the resolver (``keystrokes_equal`` /
# ``_chord_key`` collapse ``alt or meta`` into one modifier). The duplicate-key
# comparison MUST collapse them too, else ``alt+x`` and ``meta+x`` look distinct
# here yet silently collide at resolution time (last-wins, no dup warning).
_ALT_META_CANON = "altmeta"


def normalize_chord_for_comparison(chord_str: str) -> str:
    """Lowercase + sort modifiers WITHIN each chord-step (chords stay ordered).

    Split on whitespace first (chord steps), then ``+`` (modifiers+key); the key
    (last token) is kept last, the preceding modifier tokens are sorted. So
    ``shift+ctrl+k`` == ``ctrl+shift+k`` but a chord's step order is preserved.

    ``alt`` and ``meta`` (and their aliases ``opt``/``option``) collapse to one
    canonical modifier token — consistent with the resolver's alt=meta collapse —
    so ``alt+x`` and ``meta+x`` compare EQUAL for duplicate detection.
    """
    steps: list[str] = []
    for step in chord_str.split():
        parts = step.lower().split("+")
        if len(parts) <= 1:
            steps.append(step.lower())
            continue
        *mods, key = parts
        canon_mods = {
            _ALT_META_CANON if m in ("alt", "opt", "option", "meta") else m
            for m in mods
        }
        steps.append("+".join(sorted(canon_mods) + [key]))
    return " ".join(steps)


# Locate the literal ``"bindings"`` map key in the raw JSON text. A non-greedy
# ``\{(.*?)\}`` regex is WRONG here: it stops at the first ``}`` even when the
# real object end is later (a ``}`` inside a string value, or a nested object).
# We instead do a string-aware brace-depth scan (see ``_scan_raw_duplicate_keys``).
_BINDINGS_KEY_RE = re.compile(r'"bindings"\s*:\s*')


def _find_object_span(raw: str, open_idx: int) -> int:
    """Return the index just past the ``}`` closing the object that opens at
    ``open_idx`` (which must point at a ``{``).

    Tracks brace depth while respecting JSON string literals (braces inside
    ``"..."`` are skipped; ``\\"`` escapes are handled). Returns ``len(raw)`` if
    the object is unterminated (malformed input — caller tolerates it).
    """
    depth = 0
    in_string = False
    escaped = False
    i = open_idx
    n = len(raw)
    while i < n:
        ch = raw[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return i + 1
        i += 1
    return n


def _top_level_keys(obj_body: str) -> list[str]:
    """Return the ordered top-level object keys within ``obj_body`` (the slice
    BETWEEN the outer ``{`` and ``}``), skipping any keys nested inside deeper
    objects/arrays and respecting string literals.
    """
    keys: list[str] = []
    depth = 0
    in_string = False
    escaped = False
    string_start = -1
    i = 0
    n = len(obj_body)
    while i < n:
        ch = obj_body[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
                # A string at depth 0 followed by ``:`` is a top-level key.
                if depth == 0:
                    j = i + 1
                    while j < n and obj_body[j] in " \t\r\n":
                        j += 1
                    if j < n and obj_body[j] == ":":
                        keys.append(_unescape_json_string(obj_body[string_start + 1 : i]))
        else:
            if ch == '"':
                in_string = True
                string_start = i
            elif ch in "{[":
                depth += 1
            elif ch in "}]":
                depth -= 1
        i += 1
    return keys


def _unescape_json_string(s: str) -> str:
    """Best-effort unescape of a JSON string body (keys rarely have escapes)."""
    try:
        return json.loads('"' + s + '"')
    except (json.JSONDecodeError, ValueError):  # pragma: no cover - tolerate junk
        return s


def _scan_raw_duplicate_keys(raw: str) -> list[str]:
    """Return duplicate keystroke-keys found by scanning the RAW JSON text.

    ``json.loads`` silently drops duplicate object keys, so a parsed dict cannot
    express the dupe — we locate each ``"bindings": { ... }`` map object via a
    string-aware brace-depth scan, then look for any top-level key that appears
    more than once within that object. Validation-only: NEVER raises on malformed
    input — returns whatever it can.
    """
    dupes: list[str] = []
    for key_match in _BINDINGS_KEY_RE.finditer(raw):
        open_idx = key_match.end()
        if open_idx >= len(raw) or raw[open_idx] != "{":
            continue  # ``"bindings": [...]`` (the array wrapper) — not a map.
        end = _find_object_span(raw, open_idx)
        body = raw[open_idx + 1 : end - 1] if end > open_idx + 1 else ""
        seen: set[str] = set()
        for key in _top_level_keys(body):
            if key in seen:
                dupes.append(key)
            else:
                seen.add(key)
    return dupes


def load_keybindings(
    path: str | None,
) -> tuple[list[ParsedBinding], list[Warning]]:
    """Load (path | None) -> ``(merged_bindings, warnings)``.

    ``path`` is None or the file is missing -> defaults only, no warnings. Any
    failure degrades to defaults + a warning; never raises.
    """
    defaults = default_bindings()
    if path is None:
        return defaults, []

    file_path = Path(path)
    if not file_path.exists():
        return defaults, []

    try:
        raw = file_path.read_text(encoding="utf-8")
    except OSError as exc:  # pragma: no cover - rare I/O failure
        return defaults, [Warning(Severity.WARNING, f"could not read keybindings: {exc}")]

    warnings: list[Warning] = []

    # raw-text duplicate-key scan (before json.loads drops them).
    for dup in _scan_raw_duplicate_keys(raw):
        warnings.append(
            Warning(Severity.WARNING, f"duplicate key in keybindings JSON: {dup!r}", keystroke=dup)
        )

    try:
        data: Any = json.loads(raw)
    except json.JSONDecodeError as exc:
        warnings.append(Warning(Severity.WARNING, f"malformed keybindings JSON: {exc}"))
        return defaults, warnings

    if not isinstance(data, dict):
        warnings.append(Warning(Severity.WARNING, "keybindings file must be a JSON object"))
        return defaults, warnings

    blocks = data.get("bindings")
    if not isinstance(blocks, list):
        warnings.append(Warning(Severity.WARNING, "keybindings 'bindings' must be an array"))
        return defaults, warnings

    user_bindings: list[ParsedBinding] = []
    for block in blocks:
        if not isinstance(block, dict):
            warnings.append(Warning(Severity.WARNING, "binding block must be an object"))
            continue
        ctx_name = block.get("context")
        ctx = _VALID_CONTEXTS.get(ctx_name) if isinstance(ctx_name, str) else None
        if ctx is None:
            warnings.append(
                Warning(Severity.WARNING, f"unknown context: {ctx_name!r}", context=str(ctx_name))
            )
            continue

        mapping = block.get("bindings")
        if not isinstance(mapping, dict):
            warnings.append(
                Warning(Severity.WARNING, "block 'bindings' must be an object", context=ctx.value)
            )
            continue

        seen_norm: set[str] = set()
        for keystroke_str, action in mapping.items():
            # keystroke parse
            try:
                chord = parse_chord(keystroke_str)
            except KeystrokeParseError as exc:
                warnings.append(
                    Warning(
                        Severity.WARNING,
                        f"unparseable keystroke {keystroke_str!r}: {exc}",
                        context=ctx.value,
                        keystroke=keystroke_str,
                    )
                )
                continue

            # per-context normalized duplicate
            norm = normalize_chord_for_comparison(keystroke_str)
            if norm in seen_norm:
                warnings.append(
                    Warning(
                        Severity.WARNING,
                        f"duplicate keystroke (normalized) in context {ctx.value}: {keystroke_str!r}",
                        context=ctx.value,
                        keystroke=keystroke_str,
                    )
                )
            seen_norm.add(norm)

            # reserved-shortcut check (single-step only)
            warnings.extend(_reserved_warnings(norm, ctx, keystroke_str))

            # action validation (null = unbind, always allowed)
            if action is not None:
                if not isinstance(action, str):
                    warnings.append(
                        Warning(
                            Severity.WARNING,
                            f"action must be a string or null: {action!r}",
                            context=ctx.value,
                            keystroke=keystroke_str,
                        )
                    )
                    continue
                if is_command_action(action) and ctx is not Context.CHAT:
                    warnings.append(
                        Warning(
                            Severity.WARNING,
                            f"command: action only allowed in Chat context: {action!r}",
                            context=ctx.value,
                            keystroke=keystroke_str,
                        )
                    )
                    continue
                if not is_known_action(action):
                    warnings.append(
                        Warning(
                            Severity.WARNING,
                            f"unknown action: {action!r}",
                            context=ctx.value,
                            keystroke=keystroke_str,
                        )
                    )
                    continue

            user_bindings.append(ParsedBinding(chord=chord, action=action, context=ctx))

    # merge: defaults + user (last-wins; null unbinds via the resolver).
    return defaults + user_bindings, warnings


def _reserved_warnings(norm: str, ctx: Context, original: str) -> list[Warning]:
    out: list[Warning] = []
    # only single-step keystrokes can be reserved
    if " " in norm:
        return out
    if norm in NON_REBINDABLE:
        out.append(
            Warning(
                Severity.WARNING,
                f"{original!r} is reserved and cannot be rebound",
                context=ctx.value,
                keystroke=original,
            )
        )
    elif norm in TERMINAL_RESERVED_WARN:
        out.append(
            Warning(
                Severity.WARNING,
                f"{original!r} is terminal-reserved (SIGTSTP); rebinding may not work",
                context=ctx.value,
                keystroke=original,
            )
        )
    elif norm in TERMINAL_RESERVED_ERROR:
        out.append(
            Warning(
                Severity.ERROR,
                f"{original!r} is terminal-reserved (SIGQUIT) and cannot be rebound",
                context=ctx.value,
                keystroke=original,
            )
        )
    return out
