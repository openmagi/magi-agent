from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Literal

BlockKind = Literal["heading", "paragraph", "list_item", "code", "table"]


@dataclass(frozen=True)
class MarkdownBlock:
    kind: BlockKind
    text: str = ""
    level: int = 0
    rows: tuple[tuple[str, ...], ...] = ()
    ordered: bool = False


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_UNORDERED_RE = re.compile(r"^[-*+]\s+(.*)$")
_ORDERED_RE = re.compile(r"^\d+[.)]\s+(.*)$")
_FENCE_RE = re.compile(r"^\s*```")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)+\|?\s*$")
_INLINE_MARKUP_RE = re.compile(r"(\*\*|__|\*|_|`|~~)")
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^)]+\)")
_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")


def parse_markdown(source: str) -> tuple[MarkdownBlock, ...]:
    lines = source.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks: list[MarkdownBlock] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if not stripped:
            index += 1
            continue

        if _FENCE_RE.match(line):
            index += 1
            code_lines: list[str] = []
            while index < len(lines) and not _FENCE_RE.match(lines[index]):
                code_lines.append(lines[index])
                index += 1
            if index < len(lines):
                index += 1
            blocks.append(MarkdownBlock(kind="code", text="\n".join(code_lines)))
            continue

        heading = _HEADING_RE.match(stripped)
        if heading:
            blocks.append(
                MarkdownBlock(
                    kind="heading",
                    text=_strip_inline(heading.group(2).strip()),
                    level=len(heading.group(1)),
                )
            )
            index += 1
            continue

        if "|" in line and index + 1 < len(lines) and _TABLE_SEP_RE.match(lines[index + 1]):
            rows: list[tuple[str, ...]] = [_split_table_row(line)]
            index += 2
            while index < len(lines) and "|" in lines[index] and lines[index].strip():
                rows.append(_split_table_row(lines[index]))
                index += 1
            blocks.append(MarkdownBlock(kind="table", rows=tuple(rows)))
            continue

        unordered = _UNORDERED_RE.match(stripped)
        if unordered:
            blocks.append(MarkdownBlock(kind="list_item", text=_strip_inline(unordered.group(1))))
            index += 1
            continue

        ordered = _ORDERED_RE.match(stripped)
        if ordered:
            blocks.append(
                MarkdownBlock(kind="list_item", text=_strip_inline(ordered.group(1)), ordered=True)
            )
            index += 1
            continue

        if stripped.startswith(">"):
            quote_lines: list[str] = []
            while index < len(lines) and lines[index].strip().startswith(">"):
                quote_lines.append(lines[index].strip().lstrip(">").strip())
                index += 1
            blocks.append(
                MarkdownBlock(kind="paragraph", text=_strip_inline(" ".join(quote_lines)))
            )
            continue

        para_lines: list[str] = []
        while index < len(lines):
            candidate = lines[index]
            candidate_stripped = candidate.strip()
            if not candidate_stripped:
                break
            if _is_structural_line(candidate, candidate_stripped, lines, index):
                break
            para_lines.append(candidate_stripped)
            index += 1
        blocks.append(
            MarkdownBlock(kind="paragraph", text=_strip_inline(" ".join(para_lines)))
        )
    return tuple(blocks)


def markdown_to_plain_text(source: str) -> str:
    parts: list[str] = []
    for block in parse_markdown(source):
        if block.kind in {"heading", "paragraph", "list_item", "code"}:
            if block.text:
                parts.append(block.text)
        elif block.kind == "table":
            for row in block.rows:
                parts.append(" ".join(cell for cell in row if cell))
    return "\n\n".join(parts) + ("\n" if parts else "")


def render_markdown_html(source: str, *, title: str | None = None) -> str:
    escaped_title = html.escape(title or _first_heading(source) or "Document")
    body = "\n".join(_render_block_html(block) for block in parse_markdown(source))
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escaped_title}</title>
  <style>
    :root {{ color-scheme: light; }}
    body {{
      margin: 40px auto;
      max-width: 860px;
      padding: 0 24px;
      color: #111827;
      font: 16px/1.65 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    h1, h2, h3, h4, h5, h6 {{ line-height: 1.25; margin: 1.4em 0 0.45em; }}
    p {{ margin: 0 0 1em; }}
    code, pre {{ font-family: "SFMono-Regular", Consolas, monospace; }}
    pre {{ background: #f3f4f6; border-radius: 6px; padding: 12px; overflow-x: auto; }}
    table {{ width: 100%; border-collapse: collapse; margin: 1em 0; }}
    th, td {{ border: 1px solid #d1d5db; padding: 6px 8px; text-align: left; }}
    th {{ background: #f9fafb; }}
    @media print {{
      body {{ margin: 18mm; max-width: none; padding: 0; }}
      a {{ color: inherit; text-decoration: none; }}
    }}
  </style>
</head>
<body>
{body}
</body>
</html>
"""


def _render_block_html(block: MarkdownBlock) -> str:
    if block.kind == "heading":
        level = min(6, max(1, block.level))
        return f"<h{level}>{html.escape(block.text, quote=True)}</h{level}>"
    if block.kind == "paragraph":
        return f"<p>{html.escape(block.text, quote=True)}</p>"
    if block.kind == "list_item":
        return f"<ul><li>{html.escape(block.text, quote=True)}</li></ul>"
    if block.kind == "code":
        return f"<pre><code>{html.escape(block.text, quote=True)}</code></pre>"
    if block.kind == "table":
        return _render_table_html(block.rows)
    return ""


def _render_table_html(rows: tuple[tuple[str, ...], ...]) -> str:
    if not rows:
        return ""
    header, *body = rows
    header_html = "".join(f"<th>{html.escape(cell, quote=True)}</th>" for cell in header)
    body_html = "\n".join(
        "<tr>" + "".join(f"<td>{html.escape(cell, quote=True)}</td>" for cell in row) + "</tr>"
        for row in body
    )
    return f"<table><thead><tr>{header_html}</tr></thead><tbody>{body_html}</tbody></table>"


def _split_table_row(line: str) -> tuple[str, ...]:
    trimmed = line.strip()
    if trimmed.startswith("|"):
        trimmed = trimmed[1:]
    if trimmed.endswith("|"):
        trimmed = trimmed[:-1]
    return tuple(_strip_inline(cell.strip()) for cell in trimmed.split("|"))


def _strip_inline(text: str) -> str:
    text = _IMAGE_RE.sub(r"\1", text)
    text = _LINK_RE.sub(r"\1", text)
    return _INLINE_MARKUP_RE.sub("", text)


def _first_heading(source: str) -> str | None:
    for line in source.splitlines():
        match = _HEADING_RE.match(line.strip())
        if match:
            return _strip_inline(match.group(2).strip())
    return None


def _is_structural_line(
    line: str,
    stripped: str,
    lines: list[str],
    index: int,
) -> bool:
    return (
        _FENCE_RE.match(line) is not None
        or _HEADING_RE.match(stripped) is not None
        or _UNORDERED_RE.match(stripped) is not None
        or _ORDERED_RE.match(stripped) is not None
        or stripped.startswith(">")
        or ("|" in line and index + 1 < len(lines) and _TABLE_SEP_RE.match(lines[index + 1]) is not None)
    )
