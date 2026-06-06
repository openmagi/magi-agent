"""Manifest declarations for the optional file & multimodal tool suite.

All four tools are ``enabled_by_default=False``.  They are registered only
when ``MAGI_FILE_TOOLS_ENABLED=true`` and wired into the CLI tool runtime by
``magi_agent.cli.tool_runtime.build_cli_tool_runtime``.

Heavy dependencies (openpyxl / pypdf / python-docx / openai) are guarded by
``try/except ImportError`` inside the handlers, so environments without the
``[files]`` or ``[audio]`` extras return a ``status="blocked"`` result rather
than failing on import.
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
    "required": ["path"],
    "properties": {
        "path": {"type": "string"},
        "language": {
            "type": "string",
            "description": "ISO 639-1 language code hint (e.g. 'en'). Optional.",
        },
    },
}


# ---------------------------------------------------------------------------
# Manifest declarations
# ---------------------------------------------------------------------------

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
        description="Extract text from a PDF or DOCX file in the workspace as markdown.",
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
        description="Transcribe an audio file in the workspace to text via ASR.",
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
