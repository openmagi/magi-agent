from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
from typing import Literal, Protocol

from .model import NormalizedSource


@dataclass(frozen=True)
class AgenticDocumentRequest:
    format: Literal["docx", "hwpx"]
    title: str
    path: Path
    source: NormalizedSource
    template: object = None
    reference_path: Path | None = None


@dataclass(frozen=True)
class AgenticDocumentResult:
    turns: int
    tool_call_count: int
    model: str | None = None


class AgenticDocumentWriter(Protocol):
    def __call__(self, request: AgenticDocumentRequest) -> AgenticDocumentResult:
        """Write the requested document to ``request.path`` or raise."""


_writer_factory: Callable[[], AgenticDocumentWriter | None] | None = None


def get_agentic_writer() -> AgenticDocumentWriter | None:
    if _writer_factory is None:
        # I-4: routed through the typed flag registry.
        from magi_agent.config.flags import flag_str  # noqa: PLC0415

        model = (flag_str("MAGI_DOCUMENT_AGENTIC_MODEL") or "").strip()
        if not model:
            return None
        return LiteLLMAgenticDocumentWriter(model=model)
    return _writer_factory()


def set_agentic_writer_factory_for_tests(
    factory: Callable[[], AgenticDocumentWriter | None] | None,
) -> None:
    global _writer_factory
    _writer_factory = factory


class LiteLLMAgenticDocumentWriter:
    """Model-backed DOCX/HWPX authoring path.

    This intentionally stays opt-in through ``MAGI_DOCUMENT_AGENTIC_MODEL`` so
    local OSS installs do not make surprise model calls. The deterministic writer
    remains the fallback when this path raises.
    """

    def __init__(self, *, model: str) -> None:
        self.model = model

    def __call__(self, request: AgenticDocumentRequest) -> AgenticDocumentResult:
        authored_markdown = self._author_markdown(request)
        if request.format == "docx":
            self._write_docx(request.path, authored_markdown)
        else:
            self._write_hwpx(request, authored_markdown)
        return AgenticDocumentResult(turns=1, tool_call_count=0, model=self.model)

    def _author_markdown(self, request: AgenticDocumentRequest) -> str:
        try:
            import litellm  # noqa: PLC0415
        except Exception as error:
            raise RuntimeError("litellm unavailable for agentic document authoring") from error

        # I-4: typed flag registry aliases used for the litellm kwargs below.
        from magi_agent.config.flags import (  # noqa: PLC0415
            flag_int as _flag_int,
            flag_str as _flag_str,
        )

        source = request.source.markdown
        system = (
            "You are Magi Agent's document authoring worker. Rewrite the source "
            "into complete, polished Markdown for the requested output. Preserve "
            "all factual content and do not omit source details. Return only JSON "
            "with a single string field named markdown."
        )
        if request.format == "hwpx":
            system += (
                " The target is HWPX; use clear Korean-compatible headings, "
                "short paragraphs, and list structure where useful."
            )
        response = litellm.completion(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": (
                        f"Title: {request.title}\n"
                        f"Format: {request.format}\n"
                        f"Source Markdown:\n{source}"
                    ),
                },
            ],
            # I-4: routed through the typed flag registry. ``flag_str`` /
            # ``flag_int`` apply the registered defaults when env is empty
            # / unparseable (0.2 / 90), preserving legacy parse semantics.
            temperature=float(
                _flag_str("MAGI_DOCUMENT_AGENTIC_TEMPERATURE") or "0.2"
            ),
            timeout=_flag_int("MAGI_DOCUMENT_AGENTIC_TIMEOUT_S") or 90,
        )
        content = response.choices[0].message.content  # type: ignore[attr-defined]
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("agentic authoring returned empty content")
        markdown = _extract_markdown(content)
        if not markdown.strip():
            raise RuntimeError("agentic authoring returned empty markdown")
        return markdown

    def _write_docx(self, path: Path, markdown: str) -> None:
        try:
            from docx import Document  # noqa: PLC0415
        except ImportError as error:
            raise RuntimeError("python-docx unavailable for agentic DOCX authoring") from error
        from magi_agent.tools.document_write_tools import _render_markdown  # noqa: PLC0415

        document = Document()
        _render_markdown(document, markdown)
        path.parent.mkdir(parents=True, exist_ok=True)
        document.save(str(path))

    def _write_hwpx(self, request: AgenticDocumentRequest, markdown: str) -> None:
        from .hwpx import (  # noqa: PLC0415
            _build_package,
            _pack_hwpx,
            _run_bundled_guards,
            _validate_package,
            run_page_guard,
        )
        from .model import NormalizedSource  # noqa: PLC0415

        authored_source = NormalizedSource(kind="markdown", markdown=markdown)
        template = request.template if isinstance(request.template, str) else "report"
        package = _build_package(
            source=authored_source,
            title=request.title,
            template=template,
        )
        validation = _validate_package(package)
        if validation["status"] != "pass":
            raise RuntimeError("agentic HWPX package validation failed")
        data = _pack_hwpx(package)
        _run_bundled_guards(data, source=authored_source, title=request.title)
        request.path.parent.mkdir(parents=True, exist_ok=True)
        request.path.write_bytes(data)
        if request.reference_path is not None:
            run_page_guard(reference_path=request.reference_path, output_path=request.path)


def _extract_markdown(content: str) -> str:
    stripped = content.strip()
    try:
        parsed = json.loads(stripped)
        markdown = parsed.get("markdown") if isinstance(parsed, dict) else None
        if isinstance(markdown, str):
            return markdown
    except json.JSONDecodeError:
        pass
    fence = re.search(r"```(?:json|markdown|md)?\s*(.*?)```", stripped, re.DOTALL)
    if fence:
        inner = fence.group(1).strip()
        try:
            parsed = json.loads(inner)
            markdown = parsed.get("markdown") if isinstance(parsed, dict) else None
            if isinstance(markdown, str):
                return markdown
        except json.JSONDecodeError:
            return inner
    return stripped
