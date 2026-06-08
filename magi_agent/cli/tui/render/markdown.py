"""Markdown + fenced-code syntax rendering for the TUI (PR0.1).

``render_markdown(text)`` returns a Rich renderable (``rich.markdown.Markdown``)
that the finalized assistant block (``commit_rich``) and the coalesced live block
(``TranscriptController.flush``) both route through. Rich's ``Markdown`` owns
fenced-code highlighting natively, so the live/finalized paths share one parser.

``highlight_code(code, *, lexer)`` is the standalone syntax helper used where a
bare code block (not embedded markdown) needs highlighting; it degrades to plain
``Text`` on an unknown lexer rather than raising — a render must never crash.

The default Pygments/Rich theme mirrors the diff engine
(``cli/render/diff.py``'s ``DEFAULT_THEME = "monokai"``) so code looks identical
in assistant text and in diffs.
"""

from __future__ import annotations

from rich.markdown import Markdown
from rich.syntax import Syntax
from rich.text import Text

__all__ = ["render_markdown", "highlight_code", "CODE_THEME"]

#: Pygments/Rich theme for fenced code + standalone highlight. Matches
#: ``cli/render/diff.py``'s ``DEFAULT_THEME`` so code is visually consistent.
CODE_THEME = "monokai"


def render_markdown(text: str) -> Markdown:
    """Render assistant text as a Rich ``Markdown`` renderable.

    Fenced code blocks are highlighted by Rich's own Markdown renderer using
    ``CODE_THEME``. The empty string is rendered as an empty ``Markdown`` (safe
    for the live block before any delta has arrived).
    """

    return Markdown(text or "", code_theme=CODE_THEME)


def highlight_code(code: str, *, lexer: str) -> Syntax | Text:
    """Syntax-highlight a standalone code block.

    Returns a ``rich.syntax.Syntax`` on success; falls back to plain ``Text`` if
    Pygments cannot resolve ``lexer`` (a render path must never raise).
    """

    if not code:
        return Text("")
    try:
        return Syntax(code, lexer, theme=CODE_THEME, word_wrap=True)
    except Exception:  # pragma: no cover - defensive: never fail a render
        return Text(code)
