from __future__ import annotations

from magi_agent.tools.context import ToolContext

from .markdown import render_markdown_html
from .model import NormalizedSource, write_output_bytes


def write_html(
    *,
    context: ToolContext,
    source: NormalizedSource,
    path_value: str,
    title: str | None = None,
) -> dict[str, object]:
    html = render_markdown_html(source.markdown, title=title)
    return write_output_bytes(
        context=context,
        path_value=path_value,
        default_name="magi-document.html",
        fmt="html",
        data=html.encode("utf-8"),
    )
