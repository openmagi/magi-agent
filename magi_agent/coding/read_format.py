"""PR6: Read tool quality formatting (pure, IO-free).

Pure helpers that bring magi-agent's FileRead output up to OpenCode read-tool
quality: 1-indexed line numbers, line + byte caps with an "offset=N to continue"
footer, binary-file detection, and "Did you mean?" fuzzy filename suggestions.

This module has NO IO and NO ADK / runtime dependencies. It operates on text
and bytes that callers have already read and (importantly) already redacted via
the appropriate ``_sanitize_text`` / ``_redact`` path. Line numbering and caps
are deliberately applied AFTER redaction so secrets are never re-exposed.
"""
from __future__ import annotations

import difflib

__all__ = [
    "DEFAULT_MAX_BYTES",
    "DEFAULT_MAX_LINES",
    "LINE_NUMBER_GUIDANCE",
    "apply_caps",
    "binary_file_message",
    "did_you_mean",
    "did_you_mean_message",
    "is_binary",
    "number_lines",
]

DEFAULT_MAX_LINES = 2000
DEFAULT_MAX_BYTES = 64 * 1024

# Surfaced to the model so it does not paste the "N: " prefix back into an edit.
LINE_NUMBER_GUIDANCE = (
    "Line numbers are display-only. Do NOT include the 'N: ' prefix when quoting "
    "this content back in an edit's old_string."
)

_PRINTABLE_EXTRA = frozenset({"\t", "\n", "\r", "\f", "\v"})


def number_lines(text: str, offset: int = 1) -> str:
    """Return ``text`` with 1-indexed ``N: line`` prefixes.

    ``offset`` is the 1-indexed line number assigned to the first line of
    ``text`` (used when paging so numbering stays continuous). Values < 1 are
    coerced to 1. A trailing newline does not produce a spurious numbered line.

    Uses ``split("\\n")`` (same strategy as :func:`apply_caps`) so line counts
    stay in sync for CRLF and mixed-EOL content.  A trailing ``\\n`` produces a
    final empty token that is dropped to avoid a spurious numbered blank line.
    """
    start = offset if offset >= 1 else 1
    if text == "":
        return ""
    # split("\n") matches apply_caps; drop a trailing empty token from a
    # trailing newline so we don't produce a spurious blank numbered line.
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]
    numbered = [f"{start + index}: {line}" for index, line in enumerate(lines)]
    return "\n".join(numbered)


def apply_caps(
    text: str,
    *,
    max_lines: int = DEFAULT_MAX_LINES,
    max_bytes: int = DEFAULT_MAX_BYTES,
    offset: int = 1,
) -> tuple[str, bool, int | None]:
    """Cap ``text`` by line count and UTF-8 byte size.

    Returns ``(capped_text, truncated, next_offset)``. When truncated, a footer
    ``(truncated at line N; use offset=N to continue)`` is appended and
    ``next_offset`` is the 1-indexed line to resume from. When not truncated,
    ``next_offset`` is ``None``.

    ``offset`` is the 1-indexed number of the first line in ``text`` so the
    footer reports an absolute, resumable line number.

    **First-line invariant**: even if the very first line exceeds *max_bytes*
    on its own, it is always included in the output (``kept`` will contain at
    least that one line).  This prevents a pathological single-huge-line file
    from returning an empty body.  Callers should not be surprised by a result
    whose byte size exceeds *max_bytes* in that edge case.
    """
    start = offset if offset >= 1 else 1
    max_lines = max(max_lines, 1)
    max_bytes = max(max_bytes, 1)

    lines = text.split("\n")
    truncated = False

    # Line cap.
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        truncated = True

    # Byte cap: keep whole lines whose cumulative UTF-8 size fits.
    kept: list[str] = []
    running = 0
    for line in lines:
        encoded = len(line.encode("utf-8")) + 1  # newline separator estimate
        if kept and running + encoded > max_bytes:
            truncated = True
            break
        kept.append(line)
        running += encoded

    body = "\n".join(kept)
    if not truncated:
        return body, False, None

    next_offset = start + len(kept)
    footer = f"\n(truncated at line {next_offset}; use offset={next_offset} to continue)"
    return body + footer, True, next_offset


def is_binary(sample_bytes: bytes) -> bool:
    """Heuristic: treat as binary if a null byte is present OR the ratio of
    non-printable bytes in the sample exceeds 0.3."""
    if not sample_bytes:
        return False
    if b"\x00" in sample_bytes:
        return True
    text = sample_bytes.decode("utf-8", errors="replace")
    if not text:
        return False
    nonprintable = sum(
        1 for char in text if not (char.isprintable() or char in _PRINTABLE_EXTRA)
    )
    return (nonprintable / len(text)) > 0.3


def did_you_mean(dir_entries: list[str], basename: str, limit: int = 3) -> list[str]:
    """Return up to ``limit`` filenames from ``dir_entries`` similar to
    ``basename`` (case-insensitive), best matches first, no duplicates.

    Uses stdlib ``difflib`` only. Callers MUST pre-filter ``dir_entries`` so
    sealed / secret filenames are never passed in (this function does not know
    the path-policy rules and will faithfully suggest whatever it is given)."""
    if not basename or not dir_entries:
        return []
    lowered_target = basename.casefold()
    lowered_to_original: dict[str, str] = {}
    candidates: list[str] = []
    for entry in dir_entries:
        lowered = entry.casefold()
        if lowered in lowered_to_original:
            continue
        lowered_to_original[lowered] = entry
        candidates.append(lowered)
    matches = difflib.get_close_matches(
        lowered_target, candidates, n=max(limit, 0), cutoff=0.5
    )
    return [lowered_to_original[match] for match in matches]


def binary_file_message(relative_path: str | None = None) -> str:
    """Clear, non-garbled message for binary files."""
    if relative_path:
        return f"Cannot read binary file: {relative_path}"
    return "Cannot read binary file"


def did_you_mean_message(basename: str, suggestions: list[str]) -> str:
    """Format a 'File not found' message with optional 'Did you mean?' list."""
    base = f"File not found: {basename}"
    if not suggestions:
        return base
    listed = ", ".join(suggestions)
    return f"{base}. Did you mean? {listed}"
