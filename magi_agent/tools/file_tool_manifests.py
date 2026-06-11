"""Manifest declarations for the optional file & multimodal tool suite.

All tools are ``enabled_by_default=False``.  They are registered only when
``MAGI_FILE_TOOLS_ENABLED=true`` and wired into the CLI tool runtime by
``magi_agent.cli.tool_runtime.build_cli_tool_runtime``.

Heavy dependencies (openpyxl / pypdf / python-docx / python-pptx / openai /
yt-dlp / ffmpeg) are guarded by ``try/except ImportError`` inside the
handlers, so environments without the ``[files]``, ``[audio]``, or ``[video]``
extras return a ``status="blocked"`` result rather than failing on import.
"""

from __future__ import annotations

from .catalog import CORE_TOOL_SOURCE
from .manifest import Budget, ToolManifest
from .registry import ToolRegistry


# ---------------------------------------------------------------------------
# Input schemas
# ---------------------------------------------------------------------------

_XLSX_READ_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["path"],
    "properties": {
        "path": {"type": "string"},
        "sheetName": {"type": "string"},
        "maxRows": {"type": "integer", "minimum": 1, "maximum": 10000},
        "maxCols": {"type": "integer", "minimum": 1, "maximum": 200},
        "cellRange": {
            "type": "string",
            "description": (
                "Optional Excel-style range like 'A1:C5' to read a sub-range of the sheet. "
                "Overrides maxRows/maxCols within the range."
            ),
        },
    },
}

_DOCUMENT_READ_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["path"],
    "properties": {
        "path": {"type": "string"},
        "maxChars": {"type": "integer", "minimum": 100, "maximum": 200000},
        "pageRange": {
            "type": "string",
            "description": "e.g. '1-5' or '3'. Applies to PDF only.",
        },
    },
}

_IMAGE_UNDERSTAND_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["path"],
    "properties": {
        "path": {"type": "string"},
        "prompt": {
            "type": "string",
            "description": (
                "Question or instruction for the vision model. "
                "Default: 'Describe this image in detail.'"
            ),
        },
    },
}

_AUDIO_TRANSCRIBE_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "path": {
            "type": "string",
            "description": "Workspace-relative path to an audio file. Mutually exclusive with url.",
        },
        "url": {
            "type": "string",
            "description": (
                "YouTube or direct audio URL to fetch and transcribe. "
                "Mutually exclusive with path. "
                "Requires MAGI_VIDEO_DOWNLOAD_ENABLED=true."
            ),
        },
        "language": {
            "type": "string",
            "description": "ISO 639-1 language code hint (e.g. 'en'). Optional.",
        },
    },
}

_VIDEO_FRAMES_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["source"],
    "properties": {
        "source": {
            "type": "string",
            "description": (
                "YouTube URL (https://youtube.com/...) or workspace-relative path "
                "to a video file (.mp4, .webm, .avi, .mov, .mkv)."
            ),
        },
        "timestamps": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "List of timestamps in HH:MM:SS or MM:SS format "
                "(e.g. ['00:02:00', '00:05:30']). "
                "If omitted, samples 5 evenly-spaced frames."
            ),
            "maxItems": 10,
        },
        "prompt": {
            "type": "string",
            "description": (
                "Question or instruction for the vision model applied to each frame. "
                "Default: 'Describe what is happening in this video frame.'"
            ),
        },
        "includeCaptions": {
            "type": "boolean",
            "description": (
                "When true and source is a YouTube URL, also fetch auto-generated "
                "or manual captions. Default: true."
            ),
        },
    },
}

_MUSIC_NOTATION_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["path"],
    "properties": {
        "path": {
            "type": "string",
            "description": "Workspace-relative path to an image file containing musical notation.",
        },
        "clef": {
            "type": "string",
            "enum": ["treble", "bass", "alto", "tenor", "auto"],
            "description": (
                "Expected clef type. Use 'auto' to let the model detect. Default: 'auto'."
            ),
        },
        "question": {
            "type": "string",
            "description": (
                "Specific question about the notation "
                "(e.g. 'What are the note names from left to right?'). Optional."
            ),
        },
    },
}


# ---------------------------------------------------------------------------
# Manifest declarations
# ---------------------------------------------------------------------------

_DOCUMENT_SEARCH_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["path", "query"],
    "properties": {
        "path": {"type": "string"},
        "query": {
            "type": "string",
            "description": (
                "Search term or phrase to find in the document. "
                "Case-insensitive. Supports footnote references like 'footnote 397'."
            ),
        },
    },
}

_ARCHIVE_EXTRACT_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["path"],
    "properties": {
        "path": {"type": "string"},
        "readEntry": {
            "type": "string",
            "description": (
                "Inner file path within the archive to read (e.g. 'data.xml'). "
                "When omitted, only the entry listing is returned."
            ),
        },
    },
}

_XLSX_INFO_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["path"],
    "properties": {
        "path": {"type": "string"},
    },
}

_FILE_TOOL_MANIFESTS: tuple[ToolManifest, ...] = (
    ToolManifest(
        name="XLSXRead",
        description="Read an XLSX spreadsheet from the workspace, returning structured rows.",
        kind="core",
        source=CORE_TOOL_SOURCE,
        permission="read",
        inputSchema=_XLSX_READ_SCHEMA,
        availableInModes=("plan", "act"),
        tags=("workspace", "file", "spreadsheet", "read", "multimodal-file"),
        parallelSafety="readonly",
        mutatesWorkspace=False,
        dangerous=False,
        timeoutMs=30_000,
        budget=Budget(max_calls_per_turn=5, max_parallel=1, outputChars=32_000),
        enabled_by_default=False,
        opt_out=True,
    ),
    ToolManifest(
        name="DocumentRead",
        description=(
            "Extract text from a document file in the workspace. "
            "Supports: PDF, DOCX, PPTX, XML, CSV, TXT, MD, RST."
        ),
        kind="core",
        source=CORE_TOOL_SOURCE,
        permission="read",
        inputSchema=_DOCUMENT_READ_SCHEMA,
        availableInModes=("plan", "act"),
        tags=("workspace", "file", "document", "read", "multimodal-file"),
        parallelSafety="readonly",
        mutatesWorkspace=False,
        dangerous=False,
        timeoutMs=60_000,
        budget=Budget(max_calls_per_turn=5, max_parallel=1, outputChars=64_000),
        enabled_by_default=False,
        opt_out=True,
    ),
    ToolManifest(
        name="ImageUnderstand",
        description="Describe or answer a question about an image file in the workspace.",
        kind="core",
        source=CORE_TOOL_SOURCE,
        permission="read",
        inputSchema=_IMAGE_UNDERSTAND_SCHEMA,
        availableInModes=("act",),
        tags=("workspace", "file", "image", "read", "multimodal-file", "vision"),
        parallelSafety="readonly",
        mutatesWorkspace=False,
        dangerous=False,
        timeoutMs=60_000,
        costClass="medium",
        latencyClass="interactive",
        budget=Budget(max_calls_per_turn=4, max_parallel=1),
        enabled_by_default=False,
        opt_out=True,
    ),
    ToolManifest(
        name="AudioTranscribe",
        description=(
            "Transcribe an audio file in the workspace to text via ASR. "
            "Also accepts a YouTube or direct audio URL when "
            "MAGI_VIDEO_DOWNLOAD_ENABLED=true."
        ),
        kind="core",
        source=CORE_TOOL_SOURCE,
        permission="read",
        inputSchema=_AUDIO_TRANSCRIBE_SCHEMA,
        availableInModes=("act",),
        tags=("workspace", "file", "audio", "read", "multimodal-file", "asr"),
        parallelSafety="readonly",
        mutatesWorkspace=False,
        dangerous=False,
        timeoutMs=120_000,
        costClass="metered",
        latencyClass="background",
        adkToolType="LongRunningFunctionTool",
        shouldDefer=True,
        budget=Budget(max_calls_per_turn=2, max_parallel=1),
        enabled_by_default=False,
        opt_out=True,
    ),
    ToolManifest(
        name="VideoFrames",
        description=(
            "Extract frames from a video at specific timestamps and describe their content. "
            "Accepts a YouTube URL or a workspace-local video file path. "
            "Also fetches available subtitles/captions when present. "
            "URL sources require MAGI_VIDEO_DOWNLOAD_ENABLED=true."
        ),
        kind="core",
        source=CORE_TOOL_SOURCE,
        permission="read",
        inputSchema=_VIDEO_FRAMES_SCHEMA,
        availableInModes=("act",),
        tags=("video", "multimodal", "read", "url", "multimodal-file"),
        parallelSafety="readonly",
        mutatesWorkspace=False,
        dangerous=False,
        timeoutMs=300_000,
        costClass="metered",
        latencyClass="background",
        adkToolType="LongRunningFunctionTool",
        shouldDefer=True,
        budget=Budget(max_calls_per_turn=2, max_parallel=1),
        enabled_by_default=False,
        opt_out=True,
    ),
    ToolManifest(
        name="MusicNotation",
        description=(
            "Read musical notation from an image file (staff, clef, notes, rests). "
            "Returns a structured description of the notes and their values. "
            "Supports treble clef, bass clef, and common time signatures."
        ),
        kind="core",
        source=CORE_TOOL_SOURCE,
        permission="read",
        inputSchema=_MUSIC_NOTATION_SCHEMA,
        availableInModes=("act",),
        tags=("image", "music", "notation", "read", "multimodal-file"),
        parallelSafety="readonly",
        mutatesWorkspace=False,
        dangerous=False,
        timeoutMs=60_000,
        costClass="medium",
        latencyClass="interactive",
        budget=Budget(max_calls_per_turn=3, max_parallel=1),
        enabled_by_default=False,
        opt_out=True,
    ),
    # -----------------------------------------------------------------------
    # File-tools v2 additions
    # -----------------------------------------------------------------------
    ToolManifest(
        name="DocumentSearch",
        description=(
            "Search within a PDF document for a term or phrase (case-insensitive). "
            "Returns matching page numbers and surrounding snippets. "
            "Useful for finding footnotes, page counts for topics, and in-document references."
        ),
        kind="core",
        source=CORE_TOOL_SOURCE,
        permission="read",
        inputSchema=_DOCUMENT_SEARCH_SCHEMA,
        availableInModes=("plan", "act"),
        tags=("workspace", "file", "document", "search", "read", "multimodal-file"),
        parallelSafety="readonly",
        mutatesWorkspace=False,
        dangerous=False,
        timeoutMs=60_000,
        budget=Budget(max_calls_per_turn=10, max_parallel=2, outputChars=64_000),
        enabled_by_default=False,
        opt_out=True,
    ),
    ToolManifest(
        name="ArchiveExtract",
        description=(
            "Inspect a .zip archive in the workspace: list its entries and optionally read "
            "a named inner file (e.g. an XML, CSV, or TXT file inside the zip). "
            "Path-traversal entry names are rejected."
        ),
        kind="core",
        source=CORE_TOOL_SOURCE,
        permission="read",
        inputSchema=_ARCHIVE_EXTRACT_SCHEMA,
        availableInModes=("plan", "act"),
        tags=("workspace", "file", "archive", "zip", "read", "multimodal-file"),
        parallelSafety="readonly",
        mutatesWorkspace=False,
        dangerous=False,
        timeoutMs=30_000,
        budget=Budget(max_calls_per_turn=5, max_parallel=1, outputChars=64_000),
        enabled_by_default=False,
        opt_out=True,
    ),
    ToolManifest(
        name="XLSXInfo",
        description=(
            "Return structural metadata about an XLSX workbook: sheet names, row counts, "
            "column counts, and first-row header previews. Use before XLSXRead to identify "
            "which sheet and range to query."
        ),
        kind="core",
        source=CORE_TOOL_SOURCE,
        permission="read",
        inputSchema=_XLSX_INFO_SCHEMA,
        availableInModes=("plan", "act"),
        tags=("workspace", "file", "spreadsheet", "read", "multimodal-file"),
        parallelSafety="readonly",
        mutatesWorkspace=False,
        dangerous=False,
        timeoutMs=30_000,
        budget=Budget(max_calls_per_turn=5, max_parallel=1, outputChars=16_000),
        enabled_by_default=False,
        opt_out=True,
    ),
)


def file_tool_manifests() -> tuple[ToolManifest, ...]:
    """Return copies of all four file-tool manifests."""
    return tuple(m.model_copy(deep=True) for m in _FILE_TOOL_MANIFESTS)


def register_file_tool_manifests(registry: ToolRegistry) -> tuple[ToolManifest, ...]:
    """Register the file-tool manifests into *registry*.

    All manifests are registered with ``enabled_by_default=False``; the caller
    must call ``bind_file_toolhost_handlers`` to bind handlers and enable them
    via registry policy.
    """
    manifests = file_tool_manifests()
    for manifest in manifests:
        registry.register(manifest.model_copy(deep=True))
    return manifests


__all__ = [
    "file_tool_manifests",
    "register_file_tool_manifests",
]
