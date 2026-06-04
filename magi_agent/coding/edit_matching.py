"""
edit_matching.py — 9-stage fuzzy-match cascade for FileEdit.

Ported faithfully from OpenCode tool/edit.ts (source comments credit cline +
gemini-cli).  No ADK, Pydantic, or I/O dependencies.  Pure functions only.

Public API
----------
replace(content, old, new, replace_all=False) -> str
    Tries the 9 matchers in order, stops at the first that yields a usable
    (unique, when replace_all=False) match.  Raises NoMatchError or
    MultipleMatchesError as appropriate.

NoMatchError    — raised when no matcher can locate `old` in `content`.
MultipleMatchesError — raised when a match was found but it appears more than
                       once and replace_all=False.

levenshtein(a, b) -> int
    Classic DP edit-distance.

detect_line_ending(text) -> "\\r\\n" | "\\n"
    Majority-vote on the line endings present in `text`.
"""
from __future__ import annotations

import re


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class NoMatchError(Exception):
    """old_text was not found in content by any matcher."""


class MultipleMatchesError(Exception):
    """old_text was found in multiple locations; provide more context."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BOM = "﻿"


def levenshtein(a: str, b: str) -> int:
    """Classic DP Levenshtein distance — no third-party deps."""
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if la == 0:
        return lb
    if lb == 0:
        return la
    # Use two rolling rows
    prev = list(range(lb + 1))
    curr = [0] * (lb + 1)
    for i in range(1, la + 1):
        curr[0] = i
        for j in range(1, lb + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            curr[j] = min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost)
        prev, curr = curr, prev
    return prev[lb]


def detect_line_ending(text: str) -> str:
    """Return '\\r\\n' if CRLF count >= pure-LF count (and CRLF > 0), else '\\n'."""
    crlf = text.count("\r\n")
    lf = text.count("\n") - crlf  # pure LF only
    return "\r\n" if crlf >= lf and crlf > 0 else "\n"


def _strip_bom(text: str) -> tuple[str, bool]:
    """Return (text_without_bom, had_bom)."""
    if text.startswith(_BOM):
        return text[1:], True
    return text, False


def _apply_line_ending(text: str, ending: str) -> str:
    """Normalise all line endings in *text* to *ending*."""
    # Collapse any existing CRLF to LF first, then switch.
    normalised = text.replace("\r\n", "\n")
    if ending == "\r\n":
        return normalised.replace("\n", "\r\n")
    return normalised


# ---------------------------------------------------------------------------
# Internal: uniqueness guard used by replace()
# ---------------------------------------------------------------------------


def _is_unique(content: str, candidate: str) -> bool:
    """True iff candidate appears exactly once in content."""
    idx = content.find(candidate)
    if idx == -1:
        return False
    return content.find(candidate, idx + 1) == -1


# ---------------------------------------------------------------------------
# Matcher generators
# Each yields candidate substrings of *content* that are semantically
# equivalent to *find*.  The caller does the single-occurrence check and
# the actual replacement.
# ---------------------------------------------------------------------------


def _simple(content: str, find: str):
    """Matcher 1: exact substring."""
    if find in content:
        yield find


def _line_trimmed(content: str, find: str):
    """Matcher 2: compare line-by-line after .strip()."""
    find_lines = find.splitlines(keepends=True)
    # Drop a trailing empty line in find (model artefact)
    while find_lines and find_lines[-1].strip() == "":
        find_lines.pop()
    if not find_lines:
        return
    n = len(find_lines)
    content_lines = content.splitlines(keepends=True)
    for i in range(len(content_lines) - n + 1):
        window = content_lines[i : i + n]
        if all(
            w.rstrip("\r\n").strip() == f.rstrip("\r\n").strip()
            for w, f in zip(window, find_lines)
        ):
            candidate = "".join(window)
            if candidate in content:
                yield candidate


def _block_anchor(content: str, find: str):
    """Matcher 3: first/last line anchors + Levenshtein similarity for middle."""
    find_lines = find.splitlines(keepends=True)
    while find_lines and find_lines[-1].strip() == "":
        find_lines.pop()
    if len(find_lines) < 3:
        return
    first_anchor = find_lines[0].rstrip("\r\n").strip()
    last_anchor = find_lines[-1].rstrip("\r\n").strip()
    find_middle = [l.rstrip("\r\n").strip() for l in find_lines[1:-1]]
    content_lines = content.splitlines(keepends=True)
    n = len(find_lines)

    # Collect candidate (start, end) pairs where anchors match
    candidates: list[tuple[int, int]] = []
    for i, cl in enumerate(content_lines):
        if cl.rstrip("\r\n").strip() != first_anchor:
            continue
        # Look for last anchor at position i + n - 1 first, then scan forward
        for j in range(i + n - 1, len(content_lines)):
            if content_lines[j].rstrip("\r\n").strip() == last_anchor:
                if j >= i + 2:  # at least 3 lines
                    candidates.append((i, j))
                break  # take first j per i

    if not candidates:
        return

    if len(candidates) == 1:
        i, j = candidates[0]
        candidate = "".join(content_lines[i : j + 1])
        if candidate in content:
            yield candidate
        return

    # Multiple candidates: pick the one with best avg middle similarity
    def _middle_similarity(i: int, j: int) -> float:
        content_mid = [content_lines[k].rstrip("\r\n").strip() for k in range(i + 1, j)]
        if not content_mid or not find_middle:
            return 0.0
        n_pairs = min(len(content_mid), len(find_middle))
        scores = []
        for c_l, f_l in zip(content_mid[:n_pairs], find_middle[:n_pairs]):
            max_len = max(len(c_l), len(f_l), 1)
            scores.append(1.0 - levenshtein(c_l, f_l) / max_len)
        return sum(scores) / len(scores)

    best_score = -1.0
    best = None
    for i, j in candidates:
        score = _middle_similarity(i, j)
        if score > best_score:
            best_score = score
            best = (i, j)

    if best is not None and best_score >= 0.3:
        i, j = best
        yield "".join(content_lines[i : j + 1])


def _whitespace_normalized(content: str, find: str):
    """Matcher 4: collapse whitespace runs then compare."""

    def _norm(s: str) -> str:
        return re.sub(r"\s+", " ", s).strip()

    find_lines = find.splitlines(keepends=True)
    # Multi-line
    if len(find_lines) > 1:
        n = len(find_lines)
        content_lines = content.splitlines(keepends=True)
        norm_find = _norm("".join(find_lines))
        for i in range(len(content_lines) - n + 1):
            window = content_lines[i : i + n]
            if _norm("".join(window)) == norm_find:
                candidate = "".join(window)
                if candidate in content:
                    yield candidate
        return

    # Single-line
    norm_find = _norm(find.rstrip("\r\n"))
    for line in content.splitlines(keepends=True):
        stripped = line.rstrip("\r\n")
        if _norm(stripped) == norm_find:
            if line in content:
                yield line
            continue
        # Try regex match within line — only when it spans the COMPLETE line
        # content (m.start()==0 and m.end()==len(stripped)) to prevent a
        # short old_text token from matching a partial substring anywhere.
        words = re.split(r"\s+", find.strip())
        if words:
            pattern = r"\s+".join(re.escape(w) for w in words if w)
            m = re.search(pattern, stripped)
            if m and m.start() == 0 and m.end() == len(stripped):
                candidate = stripped[m.start() : m.end()]
                if candidate in content:
                    yield candidate


def _indentation_flexible(content: str, find: str):
    """Matcher 5: remove common leading indentation then compare."""

    def _min_indent(lines: list[str]) -> int:
        non_empty = [l for l in lines if l.strip()]
        if not non_empty:
            return 0
        return min(len(l) - len(l.lstrip()) for l in non_empty)

    def _remove_indent(lines: list[str], n: int) -> list[str]:
        return [l[n:] if len(l) >= n else l for l in lines]

    find_lines = find.splitlines(keepends=True)
    n = len(find_lines)
    if n == 0:
        return
    find_indent = _min_indent([l.rstrip("\r\n") for l in find_lines])
    find_stripped = _remove_indent([l.rstrip("\r\n") for l in find_lines], find_indent)

    content_lines = content.splitlines(keepends=True)
    for i in range(len(content_lines) - n + 1):
        window = content_lines[i : i + n]
        window_raw = [l.rstrip("\r\n") for l in window]
        w_indent = _min_indent(window_raw)
        window_stripped = _remove_indent(window_raw, w_indent)
        if window_stripped == find_stripped:
            candidate = "".join(window)
            if candidate in content:
                yield candidate


def _escape_normalized(content: str, find: str):
    """Matcher 6: unescape common escape sequences in find."""
    _ESCAPES = {
        "\\n": "\n",
        "\\t": "\t",
        "\\r": "\r",
        "\\'": "'",
        '\\"': '"',
        "\\`": "`",
        "\\\\": "\\",
        "\\$": "$",
    }

    def _unescape(s: str) -> str:
        result = s
        for esc, char in _ESCAPES.items():
            result = result.replace(esc, char)
        return result

    unescaped_find = _unescape(find)
    if unescaped_find == find:
        return  # nothing to unescape; don't duplicate simple matcher

    if unescaped_find in content:
        yield unescaped_find
        return

    # Try sliding window: compare raw content window to unescaped find_lines.
    # We must NOT unescape the content side — files may contain literal
    # two-character sequences like backslash-n which would be destroyed.
    find_lines = unescaped_find.splitlines(keepends=True)
    if not find_lines:
        return
    n = len(find_lines)
    content_lines = content.splitlines(keepends=True)
    for i in range(len(content_lines) - n + 1):
        window = content_lines[i : i + n]
        if window == find_lines:
            candidate = "".join(window)
            if candidate in content:
                yield candidate


def _trimmed_boundary(content: str, find: str):
    """Matcher 7: strip surrounding whitespace from find block."""
    if find.strip() == find:
        return  # nothing to trim; avoid duplicating simple matcher

    stripped_find = find.strip()
    if not stripped_find:
        return

    if stripped_find in content:
        yield stripped_find
        return

    # Try window where window.strip() == stripped_find
    find_lines = find.strip().splitlines(keepends=True)
    n = len(find_lines)
    content_lines = content.splitlines(keepends=True)
    for i in range(len(content_lines) - n + 1):
        window = content_lines[i : i + n]
        if "".join(window).strip() == stripped_find:
            candidate = "".join(window)
            if candidate in content:
                yield candidate


def _context_aware(content: str, find: str):
    """Matcher 8: first/last anchors + ≥50% of middle non-empty lines match."""
    find_lines = find.splitlines(keepends=True)
    while find_lines and find_lines[-1].strip() == "":
        find_lines.pop()
    if len(find_lines) < 3:
        return
    first_anchor = find_lines[0].rstrip("\r\n").strip()
    last_anchor = find_lines[-1].rstrip("\r\n").strip()
    find_middle = [l.rstrip("\r\n").strip() for l in find_lines[1:-1]]
    find_middle_nonempty = [l for l in find_middle if l]
    if not find_middle_nonempty:
        return

    content_lines = content.splitlines(keepends=True)
    n = len(find_lines)

    for i in range(len(content_lines) - n + 1):
        if content_lines[i].rstrip("\r\n").strip() != first_anchor:
            continue
        if content_lines[i + n - 1].rstrip("\r\n").strip() != last_anchor:
            continue
        # Check ≥50% of middle non-empty lines match
        content_mid = [content_lines[i + k].rstrip("\r\n").strip() for k in range(1, n - 1)]
        content_mid_nonempty = [l for l in content_mid if l]
        if not content_mid_nonempty:
            continue
        matches = sum(
            1 for cl, fl in zip(content_mid_nonempty, find_middle_nonempty) if cl == fl
        )
        total = max(len(content_mid_nonempty), len(find_middle_nonempty))
        if total == 0 or matches / total >= 0.5:
            candidate = "".join(content_lines[i : i + n])
            if candidate in content:
                yield candidate
                return  # first occurrence only


def _multi_occurrence(content: str, find: str):
    """Matcher 9: yield every exact occurrence (for replace_all).

    This is the replace_all fallback used when exact substring matching was not
    sufficient on its own (i.e. it could not establish uniqueness for single
    replacement but is fine to apply to all occurrences for replace_all=True).
    """
    if find in content:
        yield find


# ---------------------------------------------------------------------------
# Cascade order
# ---------------------------------------------------------------------------

_MATCHERS = [
    _simple,
    _line_trimmed,
    _block_anchor,
    _whitespace_normalized,
    _indentation_flexible,
    _escape_normalized,
    _trimmed_boundary,
    _context_aware,
    _multi_occurrence,
]


# ---------------------------------------------------------------------------
# Public: replace()
# ---------------------------------------------------------------------------


def replace(content: str, old: str, new: str, replace_all: bool = False) -> str:
    """
    Replace *old* with *new* in *content* using the 9-matcher cascade.

    Raises
    ------
    ValueError          if old is empty, or old == new (no-op).
    NoMatchError        if no matcher can locate old in content.
    MultipleMatchesError if a match was found but it is ambiguous
                         (appears >1 time) and replace_all=False.
    """
    if not old:
        raise ValueError("empty old_text")
    if old == new:
        raise ValueError("no changes: old and new are identical")

    # Detect and strip BOM from content; normalise line endings for matching.
    raw_ending = detect_line_ending(content)
    content_body, had_bom = _strip_bom(content)

    # Normalise old and new to the file's line ending for matching.
    old_norm = _apply_line_ending(old, raw_ending)
    new_norm = _apply_line_ending(new, raw_ending)

    # If normalisation made old == new, it's a no-op.
    if old_norm == new_norm:
        raise ValueError("no changes: old and new are identical after line-ending normalisation")

    _found_but_ambiguous = False

    for matcher in _MATCHERS:
        for candidate in matcher(content_body, old_norm):
            if not candidate:
                continue
            if replace_all:
                # replace_all: replace every occurrence
                result_body = content_body.replace(candidate, new_norm)
                result = (_BOM if had_bom else "") + result_body
                return result
            # Single replacement: verify uniqueness
            idx = content_body.find(candidate)
            if idx == -1:
                continue
            if not _is_unique(content_body, candidate):
                # Ambiguous — note it, but try other candidates/matchers
                _found_but_ambiguous = True
                continue
            # Unique match — perform replacement
            result_body = content_body[:idx] + new_norm + content_body[idx + len(candidate):]
            result = (_BOM if had_bom else "") + result_body
            return result

    if _found_but_ambiguous:
        raise MultipleMatchesError(
            "Found multiple matches for the provided old_text. "
            "Provide more surrounding context to make it unique."
        )
    raise NoMatchError(
        "old_text not found in file content. "
        "The text may have changed or the match is too imprecise."
    )
