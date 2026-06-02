"""Pure-Python diff engine for the Magi CLI TUI.

Pipeline (mirrors the CC ``color-diff`` Rust crate, see
``docs/architecture/claude-code-cli/07-message-diff-display-components.md`` §D):

    line patch  ->  adjacent-pair word diff  ->  syntax highlight  ->  colorize

with a plain, rich-free headless projection (``unified_diff_text``).

Design notes
------------
* **Line patch** uses ``difflib.SequenceMatcher(autojunk=False)`` with
  ``CONTEXT_LINES = 3``. Leading tabs are normalized to spaces for display
  alignment. CC's ``&``/``$`` sentinel-escape is a JS ``String.replace``
  artifact and is intentionally NOT ported.
* **Char-level word diff** pairs each run of deleted lines with the immediately
  following run of added lines 1:1 by adjacency (``min(del, add)``); only paired
  lines get a word diff. Tokenization uses ``TOKEN_RE`` (word | whitespace |
  single char). The ``CHANGE_THRESHOLD = 0.40`` guard falls back to whole-line
  marking when intra-line highlighting would be noise. Word-diff is skipped
  entirely when the diff is ``dim`` (rejected/preview) — inline highlight is too
  loud there.
* **Colorize** builds a single ``rich.text.Text`` for the whole diff: a base
  ``on red``/``on green`` line background over-painted with ``on bright_red``/
  ``on bright_green`` on the changed char ranges, on top of Pygments syntax
  foregrounds. ``rich`` is imported lazily inside the colorize path so the plain
  unified-diff projection works rich-free.
* **Cache** keys the rendered ``Text`` by ``(file, width, theme, dim, old, new)``
  so a ``ctrl+o``-style remount/resize does not re-highlight.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:  # pragma: no cover - typing only
    from rich.text import Text

__all__ = [
    "Range",
    "CONTEXT_LINES",
    "CHANGE_THRESHOLD",
    "TOKEN_RE",
    "DEFAULT_THEME",
    "DiffLine",
    "Hunk",
    "word_ranges",
    "build_hunks",
    "unified_diff_text",
    "render_diff",
    "clear_diff_cache",
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
#: Context lines around a change in a hunk (CC ``CONTEXT_LINES``).
CONTEXT_LINES = 3

#: Above this fraction of a line changing, intra-line highlight is noise so the
#: whole line is marked changed instead (CC ``CHANGE_THRESHOLD = 0.4``).
CHANGE_THRESHOLD = 0.40

#: word | whitespace-run | single char (surrogate-aware via ``re.UNICODE``).
TOKEN_RE = re.compile(r"[^\W_]+|\s+|.", re.UNICODE)

#: Canonical Pygments/Rich theme for syntax highlighting.
DEFAULT_THEME = "monokai"

#: A half-open ``(start, end)`` char range within a single line.
Range = tuple[int, int]

LineKind = Literal["context", "del", "add"]


# ---------------------------------------------------------------------------
# Structured hunk model
# ---------------------------------------------------------------------------
@dataclass
class DiffLine:
    """One displayed line of a hunk plus its intra-line changed char ranges."""

    kind: LineKind
    text: str
    word_ranges: list[Range] = field(default_factory=list)


@dataclass
class Hunk:
    """A contiguous block of context/changed lines."""

    old_start: int
    new_start: int
    lines: list[DiffLine] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Char-level word diff
# ---------------------------------------------------------------------------
def _token_char_spans(tokens: list[str]) -> list[Range]:
    """Half-open char span of each token within the joined string."""

    spans: list[Range] = []
    pos = 0
    for tok in tokens:
        spans.append((pos, pos + len(tok)))
        pos += len(tok)
    return spans


def word_ranges(old: str, new: str) -> tuple[list[Range], list[Range]]:
    """Changed char ranges on the old/new side of a paired line.

    Applies ``CHANGE_THRESHOLD``: if more than 40% of either side changed, the
    whole line is marked changed (a single full-span range), because intra-line
    highlighting would be noise.
    """

    old_tokens = TOKEN_RE.findall(old)
    new_tokens = TOKEN_RE.findall(new)
    old_spans = _token_char_spans(old_tokens)
    new_spans = _token_char_spans(new_tokens)

    sm = difflib.SequenceMatcher(a=old_tokens, b=new_tokens, autojunk=False)
    del_ranges: list[Range] = []
    add_ranges: list[Range] = []
    del_changed = 0
    add_changed = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        if i2 > i1:
            start = old_spans[i1][0]
            end = old_spans[i2 - 1][1]
            del_ranges.append((start, end))
            del_changed += end - start
        if j2 > j1:
            start = new_spans[j1][0]
            end = new_spans[j2 - 1][1]
            add_ranges.append((start, end))
            add_changed += end - start

    old_total = len(old)
    new_total = len(new)
    del_noisy = old_total > 0 and del_changed / old_total > CHANGE_THRESHOLD
    add_noisy = new_total > 0 and add_changed / new_total > CHANGE_THRESHOLD
    if del_noisy or add_noisy:
        return [(0, old_total)], [(0, new_total)]
    return del_ranges, add_ranges


# ---------------------------------------------------------------------------
# Line patch / hunks
# ---------------------------------------------------------------------------
def _normalize_display(line: str) -> str:
    """Convert leading tabs to spaces for display alignment (CC ``diff.ts:139``)."""

    stripped = line.rstrip("\n")
    i = 0
    while i < len(stripped) and stripped[i] == "\t":
        i += 1
    if i:
        return "    " * i + stripped[i:]
    return stripped


def _pair_word_ranges(block: list[DiffLine], *, dim: bool) -> None:
    """Pair adjacent del/add runs 1:1 and populate their ``word_ranges``.

    With ``dim=True`` the word diff is skipped: paired changed lines are marked
    whole-line (full-span range) so the dim preview is not over-loud.
    """

    i = 0
    n = len(block)
    while i < n:
        if block[i].kind != "del":
            i += 1
            continue
        d0 = i
        while i < n and block[i].kind == "del":
            i += 1
        del_run = block[d0:i]
        a0 = i
        while i < n and block[i].kind == "add":
            i += 1
        add_run = block[a0:i]
        for d_line, a_line in zip(del_run, add_run):
            if dim:
                d_line.word_ranges = [(0, len(d_line.text))]
                a_line.word_ranges = [(0, len(a_line.text))]
            else:
                d_ranges, a_ranges = word_ranges(d_line.text, a_line.text)
                d_line.word_ranges = d_ranges
                a_line.word_ranges = a_ranges


def build_hunks(
    old: str, new: str, *, context: int = CONTEXT_LINES, dim: bool = False
) -> list[Hunk]:
    """Build context-windowed hunks from ``old``/``new`` strings.

    Uses ``SequenceMatcher`` opcodes grouped via ``get_grouped_opcodes`` for the
    standard 3-line context window, then pairs adjacent del/add runs for the
    intra-line word diff.
    """

    old_lines = old.splitlines()
    new_lines = new.splitlines()
    sm = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)

    hunks: list[Hunk] = []
    for group in sm.get_grouped_opcodes(context):
        if not group:
            continue
        old_start = group[0][1]
        new_start = group[0][3]
        lines: list[DiffLine] = []
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                for k in range(i1, i2):
                    lines.append(DiffLine("context", _normalize_display(old_lines[k])))
                continue
            for k in range(i1, i2):
                lines.append(DiffLine("del", _normalize_display(old_lines[k])))
            for k in range(j1, j2):
                lines.append(DiffLine("add", _normalize_display(new_lines[k])))
        _pair_word_ranges(lines, dim=dim)
        hunks.append(Hunk(old_start=old_start, new_start=new_start, lines=lines))
    return hunks


# ---------------------------------------------------------------------------
# Headless projection (rich-free)
# ---------------------------------------------------------------------------
def unified_diff_text(old: str, new: str, *, file: str = "file") -> str:
    """Plain unified diff for the headless/search projection (no color).

    Imports nothing from ``rich`` — usable in a rich-free consumer.
    """

    old_lines = old.splitlines(keepends=True)
    new_lines = new.splitlines(keepends=True)
    diff = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=f"a/{file}",
        tofile=f"b/{file}",
        n=CONTEXT_LINES,
    )
    out = "".join(diff)
    if out and not out.endswith("\n"):
        out += "\n"
    return out


# ---------------------------------------------------------------------------
# Colorized render + cache
# ---------------------------------------------------------------------------
# Cache key: (file, width, theme, dim, old, new) -> rich.text.Text.
_RENDER_CACHE: dict[tuple[str, int, str, bool, str, str], "Text"] = {}
_CACHE_MAX = 8


def clear_diff_cache() -> None:
    """Drop all cached rendered diffs (used by tests and on theme change)."""

    _RENDER_CACHE.clear()


def _highlight_line(text: str, *, file: str, theme: str) -> "Text":
    """Syntax-highlight a single line, returning a styled ``rich.text.Text``."""

    from rich.syntax import Syntax
    from rich.text import Text

    if not text:
        return Text("")
    try:
        lexer = Syntax.guess_lexer(file, code=text)
        highlighted = Syntax.highlight(Syntax(text, lexer, theme=theme), text)
        # ``Syntax.highlight`` appends a trailing newline; trim it in place so the
        # per-line ``Text`` stays single-line for our own newline joining.
        highlighted.rstrip()
        return highlighted
    except Exception:  # pragma: no cover - defensive: never fail a render
        return Text(text)


def _render_line(line: DiffLine, *, file: str, theme: str, dim: bool) -> "Text":
    from rich.style import Style
    from rich.text import Text

    base = _highlight_line(line.text, file=file, theme=theme)
    if line.kind == "context":
        prefix = Text("  ")
        if dim:
            base.stylize("dim")
        return prefix + base

    if line.kind == "del":
        marker = "- "
        line_bg = "on red"
        word_bg = "on bright_red"
    else:
        marker = "+ "
        line_bg = "on green"
        word_bg = "on bright_green"

    base.stylize(Style.parse(line_bg))
    for start, end in line.word_ranges:
        base.stylize(Style.parse(word_bg), start, min(end, len(line.text)))
    if dim:
        base.stylize("dim")
    return Text(marker) + base


def render_diff(
    old: str,
    new: str,
    *,
    file: str = "file",
    width: int = 80,
    theme: str = DEFAULT_THEME,
    dim: bool = False,
) -> "Text":
    """Render the whole diff to a single cached ``rich.text.Text``.

    Cached by ``(file, width, theme, dim, old, new)``: the same key returns the
    SAME object; any key change rebuilds. ``rich`` is imported lazily here so the
    plain ``unified_diff_text`` path stays rich-free.
    """

    from rich.text import Text

    key = (file, width, theme, dim, old, new)
    cached = _RENDER_CACHE.get(key)
    if cached is not None:
        return cached

    hunks = build_hunks(old, new, dim=dim)
    out = Text(overflow="fold", no_wrap=False)
    first_line = True
    for hunk in hunks:
        for line in hunk.lines:
            if not first_line:
                out.append("\n")
            first_line = False
            out.append_text(_render_line(line, file=file, theme=theme, dim=dim))

    if len(_RENDER_CACHE) >= _CACHE_MAX:
        _RENDER_CACHE.clear()
    _RENDER_CACHE[key] = out
    return out
