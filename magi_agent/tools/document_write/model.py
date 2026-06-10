from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from magi_agent.plugins.native._common import safe_child_path, workspace_root
from magi_agent.tools.context import ToolContext
from magi_agent.web_acquisition.policy import redact_public_text

DocumentOutputFormat = Literal["html", "docx", "hwpx", "md", "txt", "pdf"]

SUPPORTED_FORMATS: tuple[DocumentOutputFormat, ...] = (
    "html",
    "docx",
    "hwpx",
    "md",
    "txt",
    "pdf",
)
CANONICAL_OUTPUT_FORMATS: tuple[Literal["html", "pdf", "docx"], ...] = (
    "html",
    "pdf",
    "docx",
)
HWPX_TEMPLATES: tuple[str, ...] = ("base", "gonmun", "report", "minutes")
_MAX_CHARS = 200_000

MIME_TYPES: dict[str, str] = {
    "html": "text/html",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "hwpx": "application/hwp+zip",
    "md": "text/markdown",
    "txt": "text/plain",
    "pdf": "application/pdf",
}
PREVIEW_KINDS: dict[str, str] = {
    "html": "inline-html",
    "md": "inline-markdown",
    "docx": "download-only",
    "hwpx": "download-only",
    "txt": "download-only",
    "pdf": "download-only",
}
EXTENSIONS: dict[str, str] = {
    "html": ".html",
    "docx": ".docx",
    "hwpx": ".hwpx",
    "md": ".md",
    "txt": ".txt",
    "pdf": ".pdf",
}

STRUCTURED_BLOCK_SCHEMA = {
    "type": "object",
    "properties": {
        "type": {"type": "string", "enum": ("heading", "paragraph")},
        "text": {"type": "string"},
        "level": {"type": "number", "enum": (1, 2, 3, 4, 5, 6)},
    },
    "required": ("type", "text"),
    "additionalProperties": False,
}
SOURCE_SCHEMA = {
    "anyOf": (
        {
            "type": "string",
            "description": "Markdown or plain text document content.",
        },
        {
            "type": "object",
            "description": (
                "Markdown/text source object. Provide exactly one of content, "
                "markdown, text, or path."
            ),
            "properties": {
                "kind": {"type": "string", "enum": ("markdown", "text", "plain_text")},
                "type": {"type": "string", "enum": ("markdown", "text", "plain_text")},
                "content": {"type": "string"},
                "markdown": {"type": "string"},
                "text": {"type": "string"},
                "path": {
                    "type": "string",
                    "description": "Workspace-relative markdown/text source path.",
                },
            },
            "additionalProperties": False,
        },
        {
            "type": "object",
            "description": (
                "Structured document source object. Provide blocks inline or "
                "blocksFile as a workspace-relative JSON file."
            ),
            "properties": {
                "kind": {"type": "string", "enum": ("structured",)},
                "type": {"type": "string", "enum": ("structured",)},
                "blocks": {"type": "array", "items": STRUCTURED_BLOCK_SCHEMA},
                "blocksFile": {
                    "type": "string",
                    "description": "Workspace-relative JSON file containing blocks.",
                },
            },
            "additionalProperties": False,
        },
    ),
}
DOCUMENT_WRITE_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "mode": {"type": "string", "enum": ("create", "edit")},
        "format": {"type": "string", "enum": SUPPORTED_FORMATS},
        "renderer": {"type": "string", "enum": ("auto", "default", "canonical_markdown")},
        "outputs": {
            "type": "array",
            "items": {"type": "string", "enum": CANONICAL_OUTPUT_FORMATS},
        },
        "docxMode": {"type": "string", "enum": ("editable", "fixed_layout")},
        "preset": {
            "type": "string",
            "enum": ("memo", "report", "investment_committee", "plain"),
        },
        "page": {
            "type": "object",
            "properties": {
                "size": {"type": "string", "enum": ("A4", "Letter")},
                "margin": {"type": "string"},
            },
            "additionalProperties": False,
        },
        "locale": {"type": "string", "enum": ("en-US", "ko-KR", "ja-JP", "zh-CN", "es-ES")},
        "title": {"type": "string"},
        "filename": {"type": "string"},
        "path": {"type": "string"},
        "template": {
            "anyOf": (
                {"type": "string", "enum": HWPX_TEMPLATES},
                {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ("path",),
                    "additionalProperties": False,
                },
            )
        },
        "source": SOURCE_SCHEMA,
        "content": {"type": "string"},
        "markdown": {"type": "string"},
        "text": {"type": "string"},
    },
    "additionalProperties": False,
}


class DocumentWriteError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class StructuredBlock:
    type: str
    text: str
    level: int = 1


@dataclass(frozen=True)
class NormalizedSource:
    kind: Literal["markdown", "text", "structured"]
    markdown: str
    blocks: tuple[StructuredBlock, ...] = ()


@dataclass(frozen=True)
class OutputRequest:
    format: DocumentOutputFormat
    path_value: str


def normalize_format(value: object) -> DocumentOutputFormat:
    fmt = str(value or "").strip().lower()
    if fmt not in SUPPORTED_FORMATS:
        raise DocumentWriteError("unsupported_document_format")
    return fmt  # type: ignore[return-value]


def infer_format(arguments: dict[str, object]) -> DocumentOutputFormat:
    value = arguments.get("format")
    if isinstance(value, str) and value.strip():
        return normalize_format(value)
    path_value = arguments.get("path") or arguments.get("filename")
    if isinstance(path_value, str):
        suffix = Path(path_value).suffix.lower().removeprefix(".")
        if suffix in SUPPORTED_FORMATS:
            return normalize_format(suffix)
    return "md"


def normalize_source(
    arguments: dict[str, object],
    context: ToolContext,
) -> NormalizedSource:
    top_level = _first_string(arguments, ("content", "markdown", "text"))
    if top_level is not None:
        return NormalizedSource(kind="markdown", markdown=_redact(top_level))

    source = arguments.get("source")
    if isinstance(source, str):
        if not source.strip():
            raise DocumentWriteError("content_required")
        return NormalizedSource(kind="markdown", markdown=_redact(source))
    if not isinstance(source, dict):
        raise DocumentWriteError("content_required")

    source_type = str(source.get("kind") or source.get("type") or "").strip().lower()
    if source_type == "structured" or "blocks" in source or "blocksFile" in source:
        blocks = _structured_blocks_from_source(source, context)
        return NormalizedSource(
            kind="structured",
            markdown=structured_blocks_to_markdown(blocks),
            blocks=blocks,
        )

    if isinstance(source.get("path"), str) and source["path"].strip():
        path = _safe_read_path(context, source["path"])
        try:
            content = _redact(path.read_text(encoding="utf-8"))
        except OSError as error:
            raise DocumentWriteError("file_read_failed") from error
        if not content.strip():
            raise DocumentWriteError("content_required")
        return NormalizedSource(
            kind="text" if source_type in {"text", "plain_text"} else "markdown",
            markdown=content,
        )

    value = _first_string(source, ("content", "markdown", "text"))
    if value is None:
        raise DocumentWriteError("content_required")
    return NormalizedSource(
        kind="text" if source_type in {"text", "plain_text"} else "markdown",
        markdown=_redact(value),
    )


def normalize_output_requests(
    arguments: dict[str, object],
    primary_format: DocumentOutputFormat,
) -> tuple[OutputRequest, ...]:
    base_path = str(
        arguments.get("path")
        or arguments.get("filename")
        or f"magi-document{EXTENSIONS[primary_format]}"
    )
    outputs = arguments.get("outputs")
    if isinstance(outputs, (list, tuple)) and outputs:
        requests: list[OutputRequest] = []
        for raw_format in outputs:
            fmt = normalize_format(raw_format)
            requests.append(OutputRequest(format=fmt, path_value=replace_suffix(base_path, fmt)))
        return tuple(requests)
    return (OutputRequest(format=primary_format, path_value=base_path),)


def replace_suffix(path_value: str, fmt: str) -> str:
    suffix = EXTENSIONS[fmt]
    path = Path(path_value)
    if path.suffix:
        return path.with_suffix(suffix).as_posix()
    return f"{path.as_posix()}{suffix}"


def output_metadata(
    *,
    context: ToolContext,
    path: Path,
    fmt: str,
    data: bytes,
    extra: dict[str, object] | None = None,
) -> dict[str, object]:
    relative = path.relative_to(workspace_root(context)).as_posix()
    content_digest = "sha256:" + hashlib.sha256(data).hexdigest()
    short_digest = content_digest.removeprefix("sha256:")[:16]
    artifact_ref = f"artifact:{fmt}:{short_digest}"
    output: dict[str, object] = {
        "path": relative,
        "pathRef": relative,
        "contentDigest": content_digest,
        "byteCount": len(data),
        "format": fmt,
        "mimeType": MIME_TYPES[fmt],
        "previewKind": PREVIEW_KINDS[fmt],
        "localOnly": True,
        "artifactRef": artifact_ref,
        "artifactRefs": (artifact_ref,),
    }
    if extra:
        output.update(extra)
    return output


def write_output_bytes(
    *,
    context: ToolContext,
    path_value: object,
    default_name: str,
    fmt: str,
    data: bytes,
    extra: dict[str, object] | None = None,
) -> dict[str, object]:
    try:
        path = safe_child_path(context, path_value, default_name=default_name, mutating=True)
    except ValueError as error:
        raise DocumentWriteError(str(error)) from error
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return output_metadata(context=context, path=path, fmt=fmt, data=data, extra=extra)


def structured_blocks_to_markdown(blocks: tuple[StructuredBlock, ...]) -> str:
    parts: list[str] = []
    for block in blocks:
        text = _redact(block.text)
        if block.type == "heading":
            level = min(6, max(1, block.level))
            parts.append(f"{'#' * level} {text}")
        else:
            parts.append(text)
    return "\n\n".join(parts) + ("\n" if parts else "")


def _structured_blocks_from_source(
    source: dict[str, object],
    context: ToolContext,
) -> tuple[StructuredBlock, ...]:
    raw_blocks = source.get("blocks")
    if raw_blocks is None and isinstance(source.get("blocksFile"), str):
        path = _safe_read_path(context, source["blocksFile"])
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise DocumentWriteError("structured_blocks_file_invalid") from error
        raw_blocks = loaded.get("blocks") if isinstance(loaded, dict) else loaded
    if not isinstance(raw_blocks, list):
        raise DocumentWriteError("structured_blocks_required")

    blocks: list[StructuredBlock] = []
    for item in raw_blocks:
        if not isinstance(item, dict):
            raise DocumentWriteError("structured_block_invalid")
        block_type = str(item.get("type") or "paragraph")
        text = item.get("text")
        if block_type not in {"heading", "paragraph"} or not isinstance(text, str):
            raise DocumentWriteError("structured_block_invalid")
        level_value = item.get("level", 1)
        level = int(level_value) if isinstance(level_value, int | float) else 1
        blocks.append(StructuredBlock(type=block_type, text=text, level=level))
    return tuple(blocks)


def _safe_read_path(context: ToolContext, path_value: object) -> Path:
    try:
        path = safe_child_path(
            context,
            path_value,
            default_name="source.md",
            mutating=False,
        )
    except ValueError as error:
        raise DocumentWriteError(str(error)) from error
    if not path.is_file():
        raise DocumentWriteError("file_not_found")
    return path


def _first_string(mapping: dict[str, object], fields: tuple[str, ...]) -> str | None:
    for field in fields:
        value = mapping.get(field)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _redact(value: str) -> str:
    return redact_public_text(value, max_chars=_MAX_CHARS)
