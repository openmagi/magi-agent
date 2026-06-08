"""Tests for the PR0.1 markdown/syntax renderer (cli/tui/render/markdown.py).

Plain pytest — these are pure functions over Rich renderables; no live App.
"""

from __future__ import annotations

from rich.markdown import Markdown as RichMarkdown
from rich.syntax import Syntax

from magi_agent.cli.tui.render import markdown as md


def test_render_markdown_returns_rich_markdown() -> None:
    node = md.render_markdown("# Title\n\nsome **bold** body")
    assert isinstance(node, RichMarkdown)


def test_render_markdown_empty_text_is_safe() -> None:
    # Empty / whitespace must not crash and must still be a renderable.
    node = md.render_markdown("")
    assert isinstance(node, RichMarkdown)


def test_render_markdown_with_fenced_code_block() -> None:
    text = "before\n\n```python\nprint('hi')\n```\n\nafter"
    node = md.render_markdown(text)
    # Rich Markdown owns fenced-code rendering; we just confirm it parsed.
    assert isinstance(node, RichMarkdown)
    assert "print('hi')" in node.markup


def test_highlight_code_returns_syntax() -> None:
    syn = md.highlight_code("print('hi')", lexer="python")
    assert isinstance(syn, Syntax)


def test_highlight_code_unknown_lexer_degrades_to_unhighlighted() -> None:
    # NB: ``Syntax(code, lexer)`` does NOT raise on a bad lexer name — Pygments
    # resolves the lexer lazily, so the ``except`` -> ``Text`` branch in
    # ``highlight_code`` is unreachable for a plain string + bad lexer name.
    # Asserting ``isinstance(out, (Syntax, Text))`` would therefore be a
    # tautology (it can only ever be a Syntax). Instead we assert the REAL
    # degraded behaviour: the returned Syntax's lexer did NOT resolve (``.lexer``
    # is ``None``), so the code renders unhighlighted rather than crashing. This
    # fails if ``highlight_code`` ever stopped degrading gracefully (e.g. started
    # raising, or silently swapped a real lexer in).
    out = md.highlight_code("definitely some text", lexer="definitely-not-a-real-lexer")
    assert isinstance(out, Syntax)
    assert out.lexer is None  # bad lexer name -> no real Pygments lexer resolved

    # Contrast: a real lexer name DOES resolve, proving the assertion above is
    # not vacuously true.
    good = md.highlight_code("print(1)", lexer="python")
    assert isinstance(good, Syntax)
    assert good.lexer is not None
