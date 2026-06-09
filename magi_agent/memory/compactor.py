"""Deterministic, IO-free memory compactor (gap-closer B2).

Append-only memory files (``MEMORY.md`` / ``USER.md``) grow without bound.
This module consolidates such a file's text *deterministically* — no LLM, no
network, no clock — so the result is reproducible and safe to gate behind a
default-off flag in :mod:`magi_agent.memory.adapters.local_file_writable`.

Algorithm
---------
1. Split the text into whole entries using the SAME line-based delimiter the
   :class:`LocalFileMemoryProvider` uses when appending: each entry is a
   single non-blank line (the provider writes ``\\n- [{kind}] {body}\\n``).
   Blank lines are structural padding and are dropped from the working set but
   never counted as facts.
2. **Dedup pass** — remove exact-duplicate entries, preserving the first
   occurrence and original order. Duplicates carry no new information, so this
   is always loss-free.
3. **Oldest-drop pass** — if the deduped text still exceeds ``max_bytes``
   (UTF-8), drop entries from the FRONT (oldest, since appends go to the end)
   until the result fits. The newest facts are always preferred.

Drop-safety
-----------
Mirrors the regen-types drop-safety philosophy: nothing is removed silently.
:class:`CompactionResult` reports ``dropped_count`` (duplicates + oldest
entries removed) and ``dropped_entries`` (the verbatim lines removed), so the
caller/tests can assert exactly which facts were sacrificed and that this only
happens when the file genuinely cannot fit.

The returned text is ALWAYS ``<= max_bytes`` measured in UTF-8 bytes, and
entries are never split mid-line or mid-codepoint.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CompactionResult:
    """Outcome of a deterministic consolidation pass.

    Attributes:
        text: The consolidated text (always ``<= max_bytes`` UTF-8 bytes).
        kept_count: Number of distinct entries retained.
        dropped_count: Number of entries removed (duplicates + oldest drops).
        dropped_entries: Verbatim lines that were removed (drop-safety audit).
        was_compacted: True iff the text changed (anything removed).
    """

    text: str
    kept_count: int
    dropped_count: int
    dropped_entries: tuple[str, ...] = field(default=())
    was_compacted: bool = False


def consolidate(text: str, *, max_bytes: int) -> CompactionResult:
    """Deterministically consolidate ``text`` to fit ``max_bytes`` (UTF-8).

    Removes exact duplicates first (loss-free), then drops the oldest entries
    (front of file) until the result fits. Always returns text whose UTF-8 byte
    length is ``<= max_bytes`` and never splits an entry.

    Args:
        text: The full memory-file text (append-only, newest entries last).
        max_bytes: Hard UTF-8 byte cap for the returned text. Must be ``>= 0``.

    Returns:
        A :class:`CompactionResult` describing the consolidated text and what,
        if anything, was removed.
    """
    if max_bytes < 0:
        raise ValueError("max_bytes must be non-negative")

    original_entries = _split_entries(text)
    total = len(original_entries)

    # If the input already fits and has no duplicates, return it unchanged so
    # the gate-off / under-threshold path is a perfect no-op.
    deduped, dup_dropped = _dedup_preserve_order(original_entries)

    fits = _encoded_len(text) <= max_bytes
    if fits and not dup_dropped:
        return CompactionResult(
            text=text,
            kept_count=total,
            dropped_count=0,
            dropped_entries=(),
            was_compacted=False,
        )

    # Oldest-drop pass: keep dropping from the front until the rendered text
    # fits the cap. ``deduped`` is newest-last, so index 0 is the oldest.
    kept = list(deduped)
    oldest_dropped: list[str] = []
    while kept and _encoded_len(_render(kept)) > max_bytes:
        oldest_dropped.append(kept.pop(0))

    rendered = _render(kept)
    # Guard: if even an empty render somehow exceeded the cap (cap == 0 with a
    # trailing newline), fall back to the empty string.
    if _encoded_len(rendered) > max_bytes:
        kept = []
        rendered = ""

    dropped_entries = tuple(dup_dropped) + tuple(oldest_dropped)
    dropped_count = len(dropped_entries)
    return CompactionResult(
        text=rendered,
        kept_count=len(kept),
        dropped_count=dropped_count,
        dropped_entries=dropped_entries,
        was_compacted=dropped_count > 0 or rendered != text,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _split_entries(text: str) -> list[str]:
    """Split into whole entries by the provider's line-based delimiter.

    The provider appends ``\\n- [{kind}] {body}\\n``; every meaningful entry is
    therefore a single non-blank line. Blank lines are structural and excluded.
    """
    return [line for line in text.splitlines() if line.strip()]


def _dedup_preserve_order(entries: list[str]) -> tuple[list[str], list[str]]:
    """Remove exact duplicates, preserving first occurrence + order.

    Returns ``(unique_entries, dropped_duplicates)``.
    """
    seen: set[str] = set()
    unique: list[str] = []
    dropped: list[str] = []
    for entry in entries:
        if entry in seen:
            dropped.append(entry)
            continue
        seen.add(entry)
        unique.append(entry)
    return unique, dropped


def _render(entries: list[str]) -> str:
    """Render entries back to file text matching the provider's append format.

    The provider appends ``\\n- [{kind}] {body}\\n``, so every entry is
    preceded by a ``\\n``.  Re-joining with a leading ``\\n`` preserves that
    structure so the provider's substring-based USER.md dedup check
    (``if entry in existing``) still works after compaction.
    """
    if not entries:
        return ""
    return "\n" + "\n".join(entries) + "\n"


def _encoded_len(text: str) -> int:
    return len(text.encode("utf-8"))
