"""Tests for the PR0.1 markdown/syntax renderer (cli/tui/render/markdown.py).

Plain pytest — these are pure functions over Rich renderables; no live App.
"""

from __future__ import annotations

from rich.markdown import Markdown as RichMarkdown
from rich.syntax import Syntax
from rich.text import Text

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


def test_highlight_code_unknown_lexer_falls_back_to_text() -> None:
    # An unknown lexer must never raise; it degrades to plain Text.
    out = md.highlight_code("¯\\_(ツ)_/¯", lexer="not-a-real-lexer")
    assert isinstance(out, (Syntax, Text))
