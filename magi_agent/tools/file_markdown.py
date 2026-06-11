"""Unified file→markdown conversion entry point (delegation-only).

One function — :func:`convert_file_to_markdown` — routes a workspace file to
the *existing* format handlers by extension and normalizes their
``ToolResult``s into a single :class:`MarkdownConversion`:

- ``.pdf/.docx/.pptx/.xml/.csv/.txt/.md/.rst`` → :func:`~magi_agent.tools.document_tools.document_read`
- ``.xlsx`` → :func:`~magi_agent.tools.spreadsheet_tools.xlsx_read` rendered as a markdown table
- ``.zip``  → :func:`~magi_agent.tools.archive_tools.archive_extract` entry listing (never inner content)

No new parsers are introduced.  All existing workspace-path policy
(``_resolve_workspace_path``), byte gates, sanitization (``_sanitize_text``),
and dependency-missing ``blocked`` results are inherited from the delegates.

Also hosts :func:`truncate_head_tail`, a local head+tail truncation helper
(keep the first ~60% and last ~40% of the budget with a middle marker), so
document tails — totals rows, appendices, conclusions — survive capping.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .context import ToolContext
from .result import ToolResult

#: Extensions the unified converter (and therefore DocumentQA) accepts.
QA_SUPPORTED_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".pdf",
        ".docx",
        ".pptx",
        ".xml",
        ".csv",
        ".txt",
        ".md",
        ".rst",
        ".xlsx",
        ".zip",
    }
)

_DOCUMENT_READ_EXTENSIONS = frozenset(
    {".pdf", ".docx", ".pptx", ".xml", ".csv", ".txt", ".md", ".rst"}
)

_DEFAULT_MAX_CHARS = 40_000
_MIN_MAX_CHARS = 100
_MAX_MAX_CHARS = 200_000

# Generous bounds for the spreadsheet delegate — the schema maxima of XLSXRead.
_XLSX_MAX_ROWS = 10_000
_XLSX_MAX_COLS = 200

_TRUNCATION_HEAD_RATIO = 0.6


@dataclass(frozen=True)
class MarkdownConversion:
    """Normalized result of a file→markdown conversion."""

    status: Literal["ok", "blocked", "error"]
    markdown: str
    truncated: bool
    source_tool: str
    content_digest: str | None
    error_code: str | None


def truncate_head_tail(text: str, max_chars: int) -> tuple[str, bool]:
    """Cap *text* at *max_chars*, keeping head AND tail around a middle marker.

    The head receives ~60% of the budget and the tail ~40%, joined by a
    ``[... <n> chars truncated (middle) ...]`` marker, so trailing content
    (totals rows, appendices, conclusions) survives — unlike head-only cuts.

    Local copy by design: the PRs in this series are independent off ``main``
    and a shared ``tools/truncation.py`` helper is not on ``main`` yet.  If a
    shared helper lands first, delegate to it in a post-merge follow-up.
    """
    if max_chars <= 0:
        return ("", len(text) > 0)
    if len(text) <= max_chars:
        return (text, False)

    # Size the marker with the worst-case count so the final result is always
    # within budget even after the real (smaller-or-equal) count is formatted.
    marker_template = "\n\n[... {count} chars truncated (middle) ...]\n\n"
    worst_case_marker = marker_template.format(count=len(text))
    budget = max_chars - len(worst_case_marker)
    if budget <= 0:
        return (text[:max_chars], True)

    head_chars = max(int(budget * _TRUNCATION_HEAD_RATIO), 1)
    tail_chars = max(budget - head_chars, 1)
    omitted = len(text) - head_chars - tail_chars
    marker = marker_template.format(count=omitted)
    result = text[:head_chars] + marker + text[len(text) - tail_chars :]
    return (result, True)


def convert_file_to_markdown(
    path: str,
    context: ToolContext,
    *,
    max_chars: int = _DEFAULT_MAX_CHARS,
) -> MarkdownConversion:
    """Convert a workspace file to markdown via the existing format handlers.

    Routing is by ``Path(path).suffix.casefold()``; unsupported extensions
    return ``status="blocked"`` with ``error_code="document_extension_not_supported"``.
    Delegate ``blocked``/``error`` results pass through with their ``errorCode``.
    """
    max_chars = min(max(max_chars, _MIN_MAX_CHARS), _MAX_MAX_CHARS)
    suffix = Path(path).suffix.casefold()

    if suffix in _DOCUMENT_READ_EXTENSIONS:
        return _convert_via_document_read(path, context, max_chars)
    if suffix == ".xlsx":
        return _convert_via_xlsx_read(path, context, max_chars)
    if suffix == ".zip":
        return _convert_via_archive_extract(path, context, max_chars)

    return MarkdownConversion(
        status="blocked",
        markdown="",
        truncated=False,
        source_tool="",
        content_digest=None,
        error_code="document_extension_not_supported",
    )


# ---------------------------------------------------------------------------
# Delegates
# ---------------------------------------------------------------------------


def _convert_via_document_read(
    path: str, context: ToolContext, max_chars: int
) -> MarkdownConversion:
    from .document_tools import document_read  # noqa: PLC0415

    result = document_read({"path": path, "maxChars": max_chars}, context)
    failure = _failure_from(result, "document_read")
    if failure is not None:
        return failure

    output = _output_mapping(result)
    text = output.get("text")
    markdown = text if isinstance(text, str) else ""
    return MarkdownConversion(
        status="ok",
        markdown=markdown,
        truncated=bool(output.get("truncated")),
        source_tool="document_read",
        content_digest=_digest_from(output),
        error_code=None,
    )


def _convert_via_xlsx_read(
    path: str, context: ToolContext, max_chars: int
) -> MarkdownConversion:
    from .spreadsheet_tools import _markdown_table, xlsx_read  # noqa: PLC0415

    result = xlsx_read(
        {"path": path, "maxRows": _XLSX_MAX_ROWS, "maxCols": _XLSX_MAX_COLS},
        context,
    )
    failure = _failure_from(result, "xlsx_read")
    if failure is not None:
        return failure

    output = _output_mapping(result)
    rows_raw = output.get("rows")
    rows: list[list[str]] = []
    if isinstance(rows_raw, list):
        for row in rows_raw:
            if isinstance(row, (list, tuple)):
                rows.append([str(cell) for cell in row])

    heading = f"# {Path(path).name}"
    markdown = f"{heading}\n\n{_markdown_table(rows)}" if rows else heading
    markdown, locally_truncated = truncate_head_tail(markdown, max_chars)
    return MarkdownConversion(
        status="ok",
        markdown=markdown,
        truncated=bool(output.get("truncated")) or locally_truncated,
        source_tool="xlsx_read",
        content_digest=_digest_from(output),
        error_code=None,
    )


def _convert_via_archive_extract(
    path: str, context: ToolContext, max_chars: int
) -> MarkdownConversion:
    from .archive_tools import archive_extract  # noqa: PLC0415
    from .spreadsheet_tools import _markdown_table  # noqa: PLC0415

    result = archive_extract({"path": path}, context)
    failure = _failure_from(result, "archive_extract")
    if failure is not None:
        return failure

    output = _output_mapping(result)
    entries_raw = output.get("entries")
    table_rows: list[list[str]] = [["name", "size"]]
    if isinstance(entries_raw, list):
        for entry in entries_raw:
            if isinstance(entry, dict):
                table_rows.append(
                    [str(entry.get("name", "")), str(entry.get("size", ""))]
                )

    heading = f"# {Path(path).name} — archive entry listing"
    note = (
        "Entry listing only — inner file content is NOT included. "
        "To inspect an inner file, call ArchiveExtract with readEntry, "
        "or extract it to the workspace and run DocumentQA on the extracted file."
    )
    markdown = f"{heading}\n\n{_markdown_table(table_rows)}\n\n{note}"
    markdown, locally_truncated = truncate_head_tail(markdown, max_chars)
    return MarkdownConversion(
        status="ok",
        markdown=markdown,
        truncated=locally_truncated,
        source_tool="archive_extract",
        content_digest=None,
        error_code=None,
    )


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------


def _failure_from(result: ToolResult, source_tool: str) -> MarkdownConversion | None:
    if result.status == "ok":
        return None
    status: Literal["blocked", "error"] = (
        "blocked" if result.status == "blocked" else "error"
    )
    return MarkdownConversion(
        status=status,
        markdown="",
        truncated=False,
        source_tool=source_tool,
        content_digest=None,
        error_code=result.error_code,
    )


def _output_mapping(result: ToolResult) -> dict[str, object]:
    output = result.output
    if isinstance(output, dict):
        return output
    return {}


def _digest_from(output: dict[str, object]) -> str | None:
    digest = output.get("contentDigest")
    if isinstance(digest, str) and digest:
        return digest
    return None


__all__ = [
    "QA_SUPPORTED_EXTENSIONS",
    "MarkdownConversion",
    "convert_file_to_markdown",
    "truncate_head_tail",
]
