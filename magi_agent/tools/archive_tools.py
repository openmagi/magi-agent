"""ArchiveExtract tool — extract and inspect .zip archives in the workspace.

Stdlib only (``zipfile`` module) — no extra dependencies required.

Security:
- Only ``.zip`` extension accepted.
- All paths are validated to stay within the workspace (no traversal).
- Inner entry names are sanitised: any entry whose resolved path would escape
  the archive's logical root is rejected with ``archive_entry_traversal_denied``.
- Entry content is capped at ``_MAX_ENTRY_CHARS`` characters to bound LLM output.
"""

from __future__ import annotations

import posixpath
import zipfile
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
    _workspace_root,
)

_MAX_ARCHIVE_BYTES = 50 * 1024 * 1024  # 50 MiB
_MAX_ENTRY_CHARS = 200_000
_MAX_ENTRIES_LISTED = 500


def archive_extract(
    arguments: Mapping[str, object], context: ToolContext
) -> ToolResult:
    """Inspect a .zip archive and optionally read a named inner file.

    Parameters
    ----------
    path:
        Workspace-relative path to a ``.zip`` file.
    readEntry:
        Optional inner file name to read (e.g. ``"data.xml"`` or
        ``"subdir/notes.txt"``).  When omitted the tool returns only the
        list of entries.

    Output
    ------
    - ``entries``: list of ``{name: str, size: int}`` for every member.
    - ``entryCount``: number of entries.
    - ``entryContent``: text content of the named entry (only when
      ``readEntry`` is provided and the entry is found).
    - ``truncated``: ``True`` when the entry content was capped.
    """
    tool_name = "ArchiveExtract"

    path_text = _str_arg(arguments, "path")
    if path_text is None:
        return _blocked_result(tool_name, "path_required")

    try:
        root = _workspace_root(context)
        resolved = _resolve_workspace_path(root, path_text, must_exist=True)
    except _SpreadsheetPolicyError as error:
        return _blocked_result(tool_name, error.reason_code)
    except OSError:
        return _error_result(tool_name, "archive_read_failed")

    suffix = Path(resolved.relative).suffix.casefold()
    if suffix != ".zip":
        return _blocked_result(
            tool_name,
            "archive_extension_not_supported",
            "Only .zip archives are supported.",
        )

    try:
        byte_size = resolved.path.stat().st_size
    except OSError:
        return _error_result(tool_name, "archive_read_failed")

    if byte_size > _MAX_ARCHIVE_BYTES:
        return _error_result(tool_name, "archive_input_too_large")

    read_entry = _str_arg(arguments, "readEntry")

    # Validate readEntry for traversal before opening the zip
    if read_entry is not None:
        if _is_traversal_entry(read_entry):
            return _blocked_result(tool_name, "archive_entry_traversal_denied")

    try:
        with zipfile.ZipFile(resolved.path, "r") as zf:
            all_names = zf.namelist()
            entries: list[dict[str, object]] = []
            for info in zf.infolist()[:_MAX_ENTRIES_LISTED]:
                entries.append({"name": info.filename, "size": info.file_size})

            entry_content: str | None = None
            truncated = False

            if read_entry is not None:
                # Check all names for the requested entry
                if read_entry not in all_names:
                    return ToolResult(
                        status="error",
                        errorCode="archive_entry_not_found",
                        errorMessage=f"Entry {read_entry!r} not found in archive.",
                        metadata=_base_metadata(
                            tool_name, permission_class="read", mutates_workspace=False
                        ),
                    )
                raw_bytes = zf.read(read_entry)
                try:
                    text = raw_bytes.decode("utf-8", errors="replace")
                except Exception:  # noqa: BLE001
                    text = raw_bytes.decode("latin-1", errors="replace")

                if len(text) > _MAX_ENTRY_CHARS:
                    text = text[:_MAX_ENTRY_CHARS]
                    truncated = True
                entry_content = text

    except zipfile.BadZipFile:
        return _error_result(tool_name, "archive_corrupt")
    except Exception:  # noqa: BLE001
        return _error_result(tool_name, "archive_read_failed")

    output: dict[str, object] = {
        "entries": entries,
        "entryCount": len(all_names),
    }
    if entry_content is not None:
        output["entryContent"] = entry_content
        output["truncated"] = truncated

    return ToolResult(
        status="ok",
        output=output,
        llmOutput=output,
        transcriptOutput={
            "toolName": tool_name,
            "entryCount": len(all_names),
        },
        metadata={
            **_base_metadata(tool_name, permission_class="read", mutates_workspace=False),
            "byteCount": byte_size,
            "entryCount": len(all_names),
            "pathRef": resolved.path_ref,
        },
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _str_arg(arguments: Mapping[str, object], name: str) -> str | None:
    value = arguments.get(name)
    if isinstance(value, str):
        return value
    return None


def _is_traversal_entry(name: str) -> bool:
    """Return True if the entry name contains path-traversal sequences."""
    # Normalise forward slashes
    cleaned = name.replace("\\", "/")
    # posixpath.normpath collapses .. and extra slashes
    normalised = posixpath.normpath(cleaned)
    # Reject absolute paths, paths starting with .., or those that resolved to ..
    return (
        normalised.startswith("/")
        or normalised == ".."
        or normalised.startswith("../")
        or ".." in normalised.split("/")
    )


__all__ = ["archive_extract"]
