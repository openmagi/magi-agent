from __future__ import annotations

import hashlib
import json
from pathlib import Path

from magi_agent.plugins.native._common import digest
from magi_agent.tools.context import ToolContext
from magi_agent.tools.result import ToolResult

from .html import write_html
from .model import (
    CANONICAL_OUTPUT_FORMATS,
    DocumentWriteError,
    NormalizedSource,
    output_metadata,
    replace_suffix,
    write_output_bytes,
)


def write_canonical_markdown(
    *,
    arguments: dict[str, object],
    context: ToolContext,
    source: NormalizedSource,
) -> ToolResult:
    outputs = _canonical_outputs(arguments)
    docx_mode = str(arguments.get("docxMode") or "editable")
    if "pdf" in outputs or docx_mode == "fixed_layout":
        raise DocumentWriteError("canonical_markdown_renderer_unavailable")

    filename = str(arguments.get("filename") or arguments.get("path") or "document.md")
    filename_base = _strip_suffix(filename)
    title = str(arguments.get("title") or Path(filename_base).name or "Document")
    qa = _qa(source.markdown)

    output_items: list[dict[str, object]] = []
    artifact_refs: list[str] = []
    for fmt in outputs:
        workspace_path = f"outputs/{replace_suffix(filename_base, fmt)}"
        if fmt == "html":
            item = write_html(
                context=context,
                source=source,
                path_value=workspace_path,
                title=title,
            )
        elif fmt == "docx":
            item = _write_editable_docx(
                context=context,
                source=source,
                path_value=workspace_path,
            )
        else:
            raise DocumentWriteError("canonical_markdown_renderer_unavailable")
        output_items.append(item)
        artifact_refs.append(str(item["artifactRef"]))

    qa_path_value = f"outputs/{filename_base}.export-qa.json"
    _write_qa(context=context, path_value=qa_path_value, qa=qa)

    primary = _primary_output(output_items, str(arguments.get("format") or outputs[0]))
    output: dict[str, object] = {
        **primary,
        "outputs": tuple(output_items),
        "documentWriteMode": "canonical_markdown",
        "canonicalMarkdownQa": qa,
        "canonicalMarkdownOutputs": tuple(outputs),
        "artifactRefs": tuple(artifact_refs),
        "localOnly": True,
    }
    output_digest = digest(output)
    return ToolResult(
        status="ok",
        output=output,
        llmOutput=output,
        transcriptOutput={"toolName": "DocumentWrite", "outputDigest": output_digest},
        artifactRefs=tuple(artifact_refs),
        metadata={
            "toolName": "DocumentWrite",
            "handler": "first_party_native_local",
            "outputDigest": output_digest,
            "documentWriteMode": "canonical_markdown",
            "canonicalMarkdownQa": qa,
            "canonicalMarkdownOutputs": tuple(outputs),
        },
    )


def _canonical_outputs(arguments: dict[str, object]) -> tuple[str, ...]:
    raw_outputs = arguments.get("outputs")
    if isinstance(raw_outputs, (list, tuple)) and raw_outputs:
        outputs = tuple(str(item).strip().lower() for item in raw_outputs)
    else:
        outputs = (str(arguments.get("format") or "html").strip().lower(),)
    for fmt in outputs:
        if fmt not in CANONICAL_OUTPUT_FORMATS:
            raise DocumentWriteError("unsupported_document_format")
    return outputs


def _write_editable_docx(
    *,
    context: ToolContext,
    source: NormalizedSource,
    path_value: str,
) -> dict[str, object]:
    from magi_agent.plugins.native._common import safe_child_path  # noqa: PLC0415
    from magi_agent.tools.document_write_tools import docx_write  # noqa: PLC0415

    result = docx_write({"content": source.markdown, "path": path_value}, context)
    if result.status != "ok":
        raise DocumentWriteError(result.error_code or "document_write_failed")
    path = safe_child_path(context, path_value, default_name="magi-document.docx", mutating=False)
    data = path.read_bytes()
    extra = {"coverage": result.output.get("coverage")} if isinstance(result.output, dict) else {}
    return output_metadata(context=context, path=path, fmt="docx", data=data, extra=extra)


def _write_qa(
    *,
    context: ToolContext,
    path_value: str,
    qa: dict[str, object],
) -> None:
    write_output_bytes(
        context=context,
        path_value=path_value,
        default_name="document.export-qa.json",
        fmt="txt",
        data=(json.dumps(qa, sort_keys=True, indent=2) + "\n").encode("utf-8"),
    )


def _qa(source_markdown: str) -> dict[str, object]:
    return {
        "status": "passed",
        "sourceHash": "sha256:" + hashlib.sha256(source_markdown.encode("utf-8")).hexdigest(),
        "rendererVersion": "magi-agent-canonical-markdown/1",
        "warnings": (),
    }


def _strip_suffix(filename: str) -> str:
    path = Path(filename)
    return path.with_suffix("").as_posix() if path.suffix else path.as_posix()


def _primary_output(outputs: list[dict[str, object]], fmt: str) -> dict[str, object]:
    for item in outputs:
        if item.get("format") == fmt:
            return dict(item)
    return dict(outputs[0])
