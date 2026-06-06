"""DocumentRead tool — extract text from PDF or DOCX files in the workspace.

Requires optional extras:
  - PDF:  ``pypdf>=4.0``   (``uv sync --extra files``)
  - DOCX: ``python-docx>=1.1`` (``uv sync --extra files``)

When the relevant package is not installed the handler returns
``status="blocked"`` with ``errorCode="document_dependency_not_installed"``.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path

from .context import ToolContext
from .result import ToolResult
from .spreadsheet_tools import (
    _SpreadsheetPolicyError,
    _base_metadata,
    _blocked_result,
    _error_result,
    _resolve_workspace_path,
    _sanitize_text,
    _workspace_root,
    _markdown_table,
)

_MAX_DOCUMENT_BYTES = 20 * 1024 * 1024  # 20 MiB
_DEFAULT_MAX_CHARS = 40_000
_MAX_CHARS = 200_000

_SUPPORTED_EXTENSIONS = frozenset({".pdf", ".docx"})


# ---------------------------------------------------------------------------
# Public handler
# ---------------------------------------------------------------------------


def document_read(arguments: Mapping[str, object], context: ToolContext) -> ToolResult:
    """Extract text from a PDF or DOCX file in the workspace.

    PDF pages are joined with ``\\n\\n---\\n\\n``; DOCX paragraphs and tables
    are rendered as plain text / markdown.  Output is capped at *maxChars*
    (default 40 000) and run through ``_sanitize_text`` before returning.
    """
    tool_name = "document_read"
    path_text = _str_arg(arguments, "path")
    if path_text is None:
        return _blocked_result(tool_name, "path_required")

    try:
        root = _workspace_root(context)
        resolved = _resolve_workspace_path(root, path_text, must_exist=True)
    except _SpreadsheetPolicyError as error:
        return _blocked_result(tool_name, error.reason_code)
    except OSError:
        return _error_result(tool_name, "document_read_failed")

    suffix = Path(resolved.relative).suffix.casefold()
    if suffix not in _SUPPORTED_EXTENSIONS:
        return _blocked_result(
            tool_name,
            "document_extension_not_supported",
            f"Supported extensions: {', '.join(sorted(_SUPPORTED_EXTENSIONS))}",
        )

    try:
        byte_size = resolved.path.stat().st_size
    except OSError:
        return _error_result(tool_name, "document_read_failed")

    if byte_size > _MAX_DOCUMENT_BYTES:
        return _error_result(tool_name, "document_input_too_large")

    max_chars_raw = arguments.get("maxChars")
    max_chars = _DEFAULT_MAX_CHARS
    if isinstance(max_chars_raw, int) and not isinstance(max_chars_raw, bool):
        max_chars = min(max(max_chars_raw, 100), _MAX_CHARS)

    if suffix == ".pdf":
        return _read_pdf(resolved, arguments, tool_name, max_chars, byte_size)
    return _read_docx(resolved, tool_name, max_chars, byte_size)


# ---------------------------------------------------------------------------
# PDF reader
# ---------------------------------------------------------------------------


def _read_pdf(
    resolved: object,
    arguments: Mapping[str, object],
    tool_name: str,
    max_chars: int,
    byte_size: int,
) -> ToolResult:
    from .spreadsheet_tools import _ResolvedPath  # noqa: PLC0415

    assert isinstance(resolved, _ResolvedPath)

    try:
        from pypdf import PdfReader  # noqa: PLC0415
    except ImportError:
        return _blocked_result(tool_name, "document_dependency_not_installed")

    page_range_str = _str_arg(arguments, "pageRange")

    try:
        reader = PdfReader(str(resolved.path))
        total_pages = len(reader.pages)
        start_page, end_page = _parse_page_range(page_range_str, total_pages)

        parts: list[str] = []
        for page_idx in range(start_page, end_page):
            text = reader.pages[page_idx].extract_text() or ""
            parts.append(text)
        raw_text = "\n\n---\n\n".join(parts)
    except Exception:  # noqa: BLE001
        return _error_result(tool_name, "pdf_read_error")

    return _finish_document_result(
        tool_name,
        raw_text,
        page_count=total_pages,
        byte_size=byte_size,
        max_chars=max_chars,
        path_ref=resolved.path_ref,
        content_digest=_digest_path(resolved.path),
    )


# ---------------------------------------------------------------------------
# DOCX reader
# ---------------------------------------------------------------------------


def _read_docx(
    resolved: object,
    tool_name: str,
    max_chars: int,
    byte_size: int,
) -> ToolResult:
    from .spreadsheet_tools import _ResolvedPath  # noqa: PLC0415

    assert isinstance(resolved, _ResolvedPath)

    try:
        from docx import Document  # noqa: PLC0415
    except ImportError:
        return _blocked_result(tool_name, "document_dependency_not_installed")

    try:
        doc = Document(str(resolved.path))
        parts: list[str] = []

        # Iterate body elements in order (paragraphs + tables interleaved).
        for element in doc.element.body:
            tag = element.tag.split("}")[-1] if "}" in element.tag else element.tag
            if tag == "p":
                # Paragraph
                from docx.oxml.ns import qn  # noqa: PLC0415

                text_nodes = element.findall(f".//{qn('w:t')}")
                para_text = "".join(t.text or "" for t in text_nodes)
                if para_text.strip():
                    parts.append(para_text)
            elif tag == "tbl":
                # Table — find rows/cells and render as markdown
                from docx.oxml.ns import qn as _qn  # noqa: PLC0415

                rows: list[list[str]] = []
                for tr in element.findall(f".//{_qn('w:tr')}"):
                    row_cells: list[str] = []
                    for tc in tr.findall(f".//{_qn('w:tc')}"):
                        cell_texts = tc.findall(f".//{_qn('w:t')}")
                        row_cells.append("".join(t.text or "" for t in cell_texts))
                    if row_cells:
                        rows.append(row_cells)
                if rows:
                    # Pad rows to uniform width
                    width = max(len(r) for r in rows)
                    padded = [r + [""] * (width - len(r)) for r in rows]
                    parts.append(_markdown_table(padded))

        raw_text = "\n\n".join(parts)
    except Exception:  # noqa: BLE001
        return _error_result(tool_name, "docx_read_error")

    return _finish_document_result(
        tool_name,
        raw_text,
        page_count=None,
        byte_size=byte_size,
        max_chars=max_chars,
        path_ref=resolved.path_ref,
        content_digest=_digest_path(resolved.path),
    )


# ---------------------------------------------------------------------------
# Shared finish
# ---------------------------------------------------------------------------


def _finish_document_result(
    tool_name: str,
    raw_text: str,
    *,
    page_count: int | None,
    byte_size: int,
    max_chars: int,
    path_ref: str,
    content_digest: str,
) -> ToolResult:
    sanitized, redacted = _sanitize_text(raw_text)
    truncated = len(sanitized) > max_chars
    if truncated:
        sanitized = sanitized[:max_chars]

    output: dict[str, object] = {
        "text": sanitized,
        "truncated": truncated,
        "contentDigest": content_digest,
    }
    if page_count is not None:
        output["pageCount"] = page_count
    if redacted:
        output["redacted"] = True

    return ToolResult(
        status="ok",
        output=output,
        llmOutput=output,
        transcriptOutput={
            "toolName": tool_name,
            "charCount": len(sanitized),
            "truncated": truncated,
            "contentDigest": content_digest,
        },
        metadata={
            **_base_metadata(tool_name, permission_class="read", mutates_workspace=False),
            "contentDigest": content_digest,
            "byteCount": byte_size,
            "charCount": len(sanitized),
            "truncated": truncated,
            "pathRef": path_ref,
            "redactionStatus": "redacted" if redacted else "no_redaction_needed",
        },
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_page_range(page_range_str: str | None, total_pages: int) -> tuple[int, int]:
    """Parse a ``pageRange`` string like ``'1-5'`` or ``'3'`` to 0-based indices.

    Returns ``(start, end)`` suitable for ``range(start, end)``.
    """
    if not page_range_str:
        return 0, total_pages
    page_range_str = page_range_str.strip()
    m_range = re.fullmatch(r"(\d+)-(\d+)", page_range_str)
    if m_range:
        start = max(int(m_range.group(1)) - 1, 0)
        end = min(int(m_range.group(2)), total_pages)
        return start, end
    m_single = re.fullmatch(r"(\d+)", page_range_str)
    if m_single:
        page = max(int(m_single.group(1)) - 1, 0)
        return page, min(page + 1, total_pages)
    return 0, total_pages


def _str_arg(arguments: Mapping[str, object], name: str) -> str | None:
    value = arguments.get(name)
    if isinstance(value, str):
        return value
    return None


def _digest_path(path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return f"sha256:{digest}"


__all__ = ["document_read"]
