from __future__ import annotations

from pathlib import Path

from magi_agent.plugins.native._common import blocked_result, digest, safe_child_path
from magi_agent.tools.context import ToolContext
from magi_agent.tools.result import ToolResult

from . import agentic
from .canonical import write_canonical_markdown
from .html import write_html
from .hwpx import write_hwpx
from .model import (
    DocumentWriteError,
    NormalizedSource,
    OutputRequest,
    infer_format,
    normalize_output_requests,
    normalize_source,
    output_metadata,
)
from .pdf import write_pdf
from .text import write_markdown, write_plain_text


def document_write(arguments: dict[str, object], context: ToolContext) -> ToolResult:
    try:
        primary_format = infer_format(arguments)
        source = normalize_source(arguments, context)
        if str(arguments.get("renderer") or "").strip().lower() == "canonical_markdown":
            return write_canonical_markdown(
                arguments=arguments,
                context=context,
                source=source,
            )

        requests = normalize_output_requests(arguments, primary_format)
        outputs = [_write_one(arguments, context, source, request) for request in requests]
        if len(outputs) == 1:
            return _ok(outputs[0], artifact_refs=tuple(outputs[0]["artifactRefs"]))

        artifact_refs = tuple(str(output["artifactRef"]) for output in outputs)
        aggregate: dict[str, object] = {
            "outputs": tuple(outputs),
            "artifactRefs": artifact_refs,
            "localOnly": True,
        }
        return _ok(aggregate, artifact_refs=artifact_refs)
    except DocumentWriteError as error:
        return blocked_result("DocumentWrite", error.reason)


def _write_one(
    arguments: dict[str, object],
    context: ToolContext,
    source: NormalizedSource,
    request: OutputRequest,
) -> dict[str, object]:
    title = str(arguments.get("title") or _title_from_source(source) or "Document")
    if request.format == "md":
        return write_markdown(context=context, source=source, path_value=request.path_value)
    if request.format == "txt":
        return write_plain_text(context=context, source=source, path_value=request.path_value)
    if request.format == "html":
        return write_html(
            context=context,
            source=source,
            path_value=request.path_value,
            title=title,
        )
    if request.format == "docx":
        return _write_docx(
            arguments=arguments,
            context=context,
            source=source,
            path_value=request.path_value,
            title=title,
        )
    if request.format == "pdf":
        return write_pdf(context=context, source=source, path_value=request.path_value)
    if request.format == "hwpx":
        return _write_hwpx(
            arguments=arguments,
            context=context,
            source=source,
            path_value=request.path_value,
            title=title,
        )
    raise DocumentWriteError("unsupported_document_format")


def _write_docx(
    *,
    arguments: dict[str, object],
    context: ToolContext,
    source: NormalizedSource,
    path_value: str,
    title: str,
) -> dict[str, object]:
    writer = agentic.get_agentic_writer()
    fallback_extra: dict[str, object] = {"documentWriteMode": "fast"}
    if writer is not None:
        try:
            path = safe_child_path(
                context,
                path_value,
                default_name="magi-document.docx",
                mutating=True,
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            result = writer(
                agentic.AgenticDocumentRequest(
                    format="docx",
                    title=title,
                    path=path,
                    source=source,
                    template=arguments.get("template"),
                )
            )
            data = path.read_bytes()
            if not data.startswith(b"PK"):
                raise ValueError("agentic DOCX output failed package validation")
            return output_metadata(
                context=context,
                path=path,
                fmt="docx",
                data=data,
                extra={
                    "documentWriteMode": "agentic",
                    "agenticTurns": result.turns,
                    "agenticToolCallCount": result.tool_call_count,
                    **({"agenticModel": result.model} if result.model else {}),
                },
            )
        except Exception as error:  # noqa: BLE001 - hosted parity: fallback.
            fallback_extra = {
                "documentWriteMode": "fast_fallback",
                "agenticError": str(error)[:240],
            }

    from magi_agent.tools.document_write_tools import docx_write  # noqa: PLC0415

    result = docx_write({"content": source.markdown, "path": path_value}, context)
    if result.status != "ok":
        raise DocumentWriteError(result.error_code or "document_write_failed")
    output = dict(result.output) if isinstance(result.output, dict) else {}
    output.update(fallback_extra)
    return output


def _write_hwpx(
    *,
    arguments: dict[str, object],
    context: ToolContext,
    source: NormalizedSource,
    path_value: str,
    title: str,
) -> dict[str, object]:
    template = arguments.get("template") or "report"
    writer = agentic.get_agentic_writer()
    if isinstance(template, dict) and writer is None:
        raise DocumentWriteError("hwpx_reference_template_requires_agentic_authoring")
    reference_path = _reference_template_path(context, template)
    if writer is not None:
        try:
            path = safe_child_path(
                context,
                path_value,
                default_name="magi-document.hwpx",
                mutating=True,
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            result = writer(
                agentic.AgenticDocumentRequest(
                    format="hwpx",
                    title=title,
                    path=path,
                    source=source,
                    template=template,
                    reference_path=reference_path,
                )
            )
            data = path.read_bytes()
            if not data.startswith(b"PK"):
                raise ValueError("agentic HWPX output failed package validation")
            return output_metadata(
                context=context,
                path=path,
                fmt="hwpx",
                data=data,
                extra={
                    "documentWriteMode": "agentic",
                    "agenticTurns": result.turns,
                    "agenticToolCallCount": result.tool_call_count,
                    **({"agenticModel": result.model} if result.model else {}),
                },
            )
        except Exception as error:
            if isinstance(template, dict):
                raise DocumentWriteError("hwpx_reference_template_authoring_failed") from error

    return write_hwpx(
        context=context,
        source=source,
        path_value=path_value,
        title=title,
        template=template,
    )


def _ok(output: dict[str, object], *, artifact_refs: tuple[str, ...]) -> ToolResult:
    output_digest = digest(output)
    return ToolResult(
        status="ok",
        output=output,
        llmOutput=output,
        transcriptOutput={"toolName": "DocumentWrite", "outputDigest": output_digest},
        artifactRefs=artifact_refs,
        metadata={
            "toolName": "DocumentWrite",
            "handler": "first_party_native_local",
            "outputDigest": output_digest,
        },
    )


def _title_from_source(source: NormalizedSource) -> str | None:
    for line in source.markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return None


def _reference_template_path(context: ToolContext, template: object) -> Path | None:
    if not isinstance(template, dict):
        return None
    path_value = template.get("path")
    if not isinstance(path_value, str) or not path_value.strip():
        raise DocumentWriteError("hwpx_reference_template_path_required")
    try:
        path = safe_child_path(
            context,
            path_value,
            default_name="reference.hwpx",
            mutating=False,
        )
    except ValueError as error:
        raise DocumentWriteError(str(error)) from error
    if not path.is_file():
        raise DocumentWriteError("file_not_found")
    return path
