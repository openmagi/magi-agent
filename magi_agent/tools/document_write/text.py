from __future__ import annotations

from magi_agent.tools.context import ToolContext

from .markdown import markdown_to_plain_text
from .model import NormalizedSource, write_output_bytes


def write_markdown(
    *,
    context: ToolContext,
    source: NormalizedSource,
    path_value: str,
) -> dict[str, object]:
    data = source.markdown.encode("utf-8")
    return write_output_bytes(
        context=context,
        path_value=path_value,
        default_name="magi-document.md",
        fmt="md",
        data=data,
    )


def write_plain_text(
    *,
    context: ToolContext,
    source: NormalizedSource,
    path_value: str,
) -> dict[str, object]:
    text = source.markdown if source.kind == "text" else markdown_to_plain_text(source.markdown)
    if text and not text.endswith("\n"):
        text = f"{text}\n"
    return write_output_bytes(
        context=context,
        path_value=path_value,
        default_name="magi-document.txt",
        fmt="txt",
        data=text.encode("utf-8"),
    )
