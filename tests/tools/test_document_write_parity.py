"""Hosted DocumentWrite parity tests for OSS first-party document authoring."""

from __future__ import annotations

import json
import subprocess
import shutil
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from magi_agent.plugins.native.documents import document_write
from magi_agent.tools.context import ToolContext

_MARKDOWN = """# Annual Plan

This paragraph includes **bold**, *italic*, and a literal tag: <script>alert("x")</script>.

## Actions

- Ship document parity
- Keep workspace paths safe

| Metric | Value |
| --- | --- |
| Coverage | 100 |
"""

_MIME_BY_FORMAT = {
    "md": "text/markdown",
    "txt": "text/plain",
    "html": "text/html",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
    "hwpx": "application/hwp+zip",
}

_PREVIEW_BY_FORMAT = {
    "md": "inline-markdown",
    "txt": "download-only",
    "html": "inline-html",
    "docx": "download-only",
    "pdf": "download-only",
    "hwpx": "download-only",
}


def _context(workspace_root: Path) -> ToolContext:
    return ToolContext(
        botId="bot-document-parity",
        sessionId="session-document-parity",
        turnId="turn-document-parity",
        workspaceRoot=str(workspace_root),
    )


def _assert_metadata(output: dict[str, object], fmt: str, path: str) -> None:
    assert output["path"] == path
    assert output["pathRef"] == path
    assert output["format"] == fmt
    assert output["mimeType"] == _MIME_BY_FORMAT[fmt]
    assert output["previewKind"] == _PREVIEW_BY_FORMAT[fmt]
    assert isinstance(output["contentDigest"], str)
    assert output["contentDigest"].startswith("sha256:")
    assert isinstance(output["byteCount"], int)
    assert output["byteCount"] > 0
    assert output["localOnly"] is True
    assert isinstance(output["artifactRef"], str)
    assert output["artifactRef"].startswith(f"artifact:{fmt}:")
    assert output["artifactRefs"] == (output["artifactRef"],)


class TestDocumentWriteTextHtmlParity:
    def test_md_format_returns_hosted_metadata(self, tmp_path: Path) -> None:
        result = document_write(
            {
                "format": "md",
                "content": "# Title\n\nSee /home/user/private.py.",
                "path": "reports/plan.md",
            },
            _context(tmp_path),
        )

        assert result.status == "ok", result.error_code
        _assert_metadata(result.output, "md", "reports/plan.md")
        saved = tmp_path / "reports" / "plan.md"
        assert saved.read_text(encoding="utf-8") == "# Title\n\nSee [redacted-path]"

    def test_txt_reads_source_path_and_strips_markdown(self, tmp_path: Path) -> None:
        (tmp_path / "source.md").write_text(_MARKDOWN, encoding="utf-8")

        result = document_write(
            {
                "format": "txt",
                "source": {"path": "source.md"},
                "path": "plain/source.txt",
            },
            _context(tmp_path),
        )

        assert result.status == "ok", result.error_code
        _assert_metadata(result.output, "txt", "plain/source.txt")
        text = (tmp_path / "plain" / "source.txt").read_text(encoding="utf-8")
        assert "Annual Plan" in text
        assert "Ship document parity" in text
        assert "# Annual Plan" not in text
        assert "| Metric |" not in text

    def test_html_escapes_source_and_uses_inline_preview(self, tmp_path: Path) -> None:
        result = document_write(
            {
                "format": "html",
                "source": {"markdown": _MARKDOWN},
                "path": "public/plan.html",
            },
            _context(tmp_path),
        )

        assert result.status == "ok", result.error_code
        _assert_metadata(result.output, "html", "public/plan.html")
        html = (tmp_path / "public" / "plan.html").read_text(encoding="utf-8")
        assert "<!doctype html>" in html.lower()
        assert "@media print" in html
        assert "<h1>Annual Plan</h1>" in html
        assert "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;" in html
        assert "<script>alert" not in html

    def test_outputs_writes_multiple_formats(self, tmp_path: Path) -> None:
        result = document_write(
            {
                "content": _MARKDOWN,
                "path": "bundle/plan.md",
                "outputs": ["md", "html", "txt"],
            },
            _context(tmp_path),
        )

        assert result.status == "ok", result.error_code
        outputs = result.output["outputs"]
        assert [item["format"] for item in outputs] == ["md", "html", "txt"]
        assert [item["path"] for item in outputs] == [
            "bundle/plan.md",
            "bundle/plan.html",
            "bundle/plan.txt",
        ]
        assert (tmp_path / "bundle" / "plan.md").exists()
        assert (tmp_path / "bundle" / "plan.html").exists()
        assert (tmp_path / "bundle" / "plan.txt").exists()
        assert result.artifact_refs == tuple(item["artifactRef"] for item in outputs)

    def test_source_string_and_filename_suffix_infer_format(self, tmp_path: Path) -> None:
        result = document_write(
            {"source": "# From Source\n\nBody", "filename": "suffix.html"},
            _context(tmp_path),
        )

        assert result.status == "ok", result.error_code
        _assert_metadata(result.output, "html", "suffix.html")
        assert "<h1>From Source</h1>" in (tmp_path / "suffix.html").read_text(
            encoding="utf-8"
        )

    def test_structured_blocks_source_writes_text(self, tmp_path: Path) -> None:
        result = document_write(
            {
                "format": "txt",
                "source": {
                    "kind": "structured",
                    "blocks": [
                        {"type": "heading", "level": 1, "text": "Structured Title"},
                        {"type": "paragraph", "text": "Structured body"},
                    ],
                },
                "filename": "structured.txt",
            },
            _context(tmp_path),
        )

        assert result.status == "ok", result.error_code
        assert (tmp_path / "structured.txt").read_text(encoding="utf-8") == (
            "Structured Title\n\nStructured body\n"
        )

    def test_structured_blocks_file_source_writes_markdown(self, tmp_path: Path) -> None:
        (tmp_path / "blocks.json").write_text(
            json.dumps(
                {
                    "blocks": [
                        {"type": "heading", "level": 2, "text": "Blocks File"},
                        {"type": "paragraph", "text": "From JSON"},
                    ]
                }
            ),
            encoding="utf-8",
        )

        result = document_write(
            {
                "format": "md",
                "source": {"type": "structured", "blocksFile": "blocks.json"},
                "filename": "blocks.md",
            },
            _context(tmp_path),
        )

        assert result.status == "ok", result.error_code
        assert (tmp_path / "blocks.md").read_text(encoding="utf-8") == (
            "## Blocks File\n\nFrom JSON\n"
        )

    def test_source_path_traversal_read_is_blocked(self, tmp_path: Path) -> None:
        result = document_write(
            {
                "format": "html",
                "source": {"path": "../outside.md"},
                "filename": "blocked.html",
            },
            _context(tmp_path),
        )

        assert result.status == "blocked"
        assert result.error_code in {
            "path_traversal_blocked",
            "absolute_path_blocked",
            "file_not_found",
        }

    def test_unsupported_format_blocks(self, tmp_path: Path) -> None:
        result = document_write(
            {"format": "rtf", "content": "body", "filename": "bad.rtf"},
            _context(tmp_path),
        )

        assert result.status == "blocked"
        assert result.error_code == "unsupported_document_format"

    def test_blank_source_is_blocked(self, tmp_path: Path) -> None:
        result = document_write(
            {"format": "md", "content": "", "filename": "blank.md"},
            _context(tmp_path),
        )

        assert result.status == "blocked"
        assert result.error_code == "content_required"

    def test_blank_content_does_not_shadow_markdown(self, tmp_path: Path) -> None:
        result = document_write(
            {
                "format": "md",
                "content": "",
                "markdown": "# Real\n\nBody",
                "filename": "real.md",
            },
            _context(tmp_path),
        )

        assert result.status == "ok", result.error_code
        assert (tmp_path / "real.md").read_text(encoding="utf-8") == "# Real\n\nBody"

    def test_plain_text_source_preserves_markdown_like_literals(
        self, tmp_path: Path
    ) -> None:
        result = document_write(
            {
                "format": "txt",
                "source": {"type": "plain_text", "text": "# not a heading\n- not a list"},
                "filename": "literal.txt",
            },
            _context(tmp_path),
        )

        assert result.status == "ok", result.error_code
        assert (tmp_path / "literal.txt").read_text(encoding="utf-8") == (
            "# not a heading\n- not a list\n"
        )


class TestDocumentWriteDocxParity:
    def test_docx_keeps_existing_real_writer_and_adds_metadata(
        self, tmp_path: Path
    ) -> None:
        result = document_write(
            {
                "format": "docx",
                "source": {"content": _MARKDOWN},
                "path": "docs/plan.docx",
            },
            _context(tmp_path),
        )

        assert result.status == "ok", result.error_code
        _assert_metadata(result.output, "docx", "docs/plan.docx")
        saved = tmp_path / "docs" / "plan.docx"
        assert saved.exists()
        assert saved.read_bytes().startswith(b"PK")
        assert result.output["coverage"]["type"] == "DocumentCoverage"

    def test_docx_agentic_writer_success_records_metadata(
        self, tmp_path: Path
    ) -> None:
        from docx import Document

        from magi_agent.tools.document_write import agentic

        def writer(
            request: agentic.AgenticDocumentRequest,
        ) -> agentic.AgenticDocumentResult:
            doc = Document()
            doc.add_paragraph("Agentic document")
            doc.save(str(request.path))
            return agentic.AgenticDocumentResult(
                turns=3,
                tool_call_count=2,
                model="fake-model",
            )

        agentic.set_agentic_writer_factory_for_tests(lambda: writer)
        try:
            result = document_write(
                {
                    "format": "docx",
                    "source": {"content": _MARKDOWN},
                    "path": "docs/agentic.docx",
                },
                _context(tmp_path),
            )
        finally:
            agentic.set_agentic_writer_factory_for_tests(None)

        assert result.status == "ok", result.error_code
        _assert_metadata(result.output, "docx", "docs/agentic.docx")
        assert result.output["documentWriteMode"] == "agentic"
        assert result.output["agenticTurns"] == 3
        assert result.output["agenticToolCallCount"] == 2
        assert result.output["agenticModel"] == "fake-model"

    def test_docx_agentic_writer_failure_falls_back_to_fast_writer(
        self, tmp_path: Path
    ) -> None:
        from magi_agent.tools.document_write import agentic

        def writer(
            _request: agentic.AgenticDocumentRequest,
        ) -> agentic.AgenticDocumentResult:
            raise RuntimeError("authoring failed")

        agentic.set_agentic_writer_factory_for_tests(lambda: writer)
        try:
            result = document_write(
                {
                    "format": "docx",
                    "source": {"content": _MARKDOWN},
                    "path": "docs/fallback.docx",
                },
                _context(tmp_path),
            )
        finally:
            agentic.set_agentic_writer_factory_for_tests(None)

        assert result.status == "ok", result.error_code
        _assert_metadata(result.output, "docx", "docs/fallback.docx")
        assert result.output["documentWriteMode"] == "fast_fallback"
        assert "authoring failed" in result.output["agenticError"]

    def test_agentic_writer_can_be_configured_from_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from magi_agent.tools.document_write import agentic

        monkeypatch.setenv("MAGI_DOCUMENT_AGENTIC_MODEL", "openai/fake-document-model")
        agentic.set_agentic_writer_factory_for_tests(None)

        writer = agentic.get_agentic_writer()

        assert isinstance(writer, agentic.LiteLLMAgenticDocumentWriter)


class TestDocumentWritePdfParity:
    def test_pdf_blocks_when_converter_is_unavailable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(shutil, "which", lambda _name: None)

        result = document_write(
            {"format": "pdf", "content": _MARKDOWN, "path": "docs/plan.pdf"},
            _context(tmp_path),
        )

        assert result.status == "blocked"
        assert result.error_code == "pdf_converter_unavailable"
        assert not (tmp_path / "docs" / "plan.pdf").exists()

    def test_pdf_converter_nonzero_exit_blocks(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from magi_agent.tools.document_write import pdf

        monkeypatch.setattr(pdf.shutil, "which", lambda _name: "/usr/bin/soffice")
        monkeypatch.setattr(
            pdf.subprocess,
            "run",
            lambda *args, **kwargs: SimpleNamespace(returncode=1, stderr="boom"),
        )

        result = document_write(
            {"format": "pdf", "content": _MARKDOWN, "path": "docs/plan.pdf"},
            _context(tmp_path),
        )

        assert result.status == "blocked"
        assert result.error_code == "document_pdf_conversion_failed"

    def test_pdf_converter_timeout_blocks(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from magi_agent.tools.document_write import pdf

        def raise_timeout(*_args: object, **_kwargs: object) -> object:
            raise subprocess.TimeoutExpired(cmd="soffice", timeout=1)

        monkeypatch.setattr(pdf.shutil, "which", lambda _name: "/usr/bin/soffice")
        monkeypatch.setattr(pdf.subprocess, "run", raise_timeout)

        result = document_write(
            {"format": "pdf", "content": _MARKDOWN, "path": "docs/plan.pdf"},
            _context(tmp_path),
        )

        assert result.status == "blocked"
        assert result.error_code == "document_pdf_conversion_timeout"

    def test_pdf_converter_invalid_output_blocks(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from magi_agent.tools.document_write import pdf

        def fake_run(args: list[str], **_kwargs: object) -> object:
            output_dir = Path(args[args.index("--outdir") + 1])
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "intermediate.pdf").write_bytes(b"not a pdf")
            return SimpleNamespace(returncode=0, stderr="")

        monkeypatch.setattr(pdf.shutil, "which", lambda _name: "/usr/bin/soffice")
        monkeypatch.setattr(pdf.subprocess, "run", fake_run)

        result = document_write(
            {"format": "pdf", "content": _MARKDOWN, "path": "docs/plan.pdf"},
            _context(tmp_path),
        )

        assert result.status == "blocked"
        assert result.error_code == "document_pdf_validation_failed"

    def test_pdf_writes_valid_pdf_with_fake_converter(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from magi_agent.tools.document_write import pdf

        def fake_run(args: list[str], **_kwargs: object) -> object:
            output_dir = Path(args[args.index("--outdir") + 1])
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "intermediate.pdf").write_bytes(b"%PDF-1.7\nfake\n")
            return SimpleNamespace(returncode=0, stderr="")

        monkeypatch.setattr(pdf.shutil, "which", lambda _name: "/usr/bin/soffice")
        monkeypatch.setattr(pdf.subprocess, "run", fake_run)

        result = document_write(
            {"format": "pdf", "content": _MARKDOWN, "path": "docs/plan.pdf"},
            _context(tmp_path),
        )

        assert result.status == "ok", result.error_code
        _assert_metadata(result.output, "pdf", "docs/plan.pdf")
        assert (tmp_path / "docs" / "plan.pdf").read_bytes().startswith(b"%PDF-")


class TestDocumentWriteCanonicalParity:
    def test_canonical_markdown_outputs_html_and_editable_docx(
        self, tmp_path: Path
    ) -> None:
        result = document_write(
            {
                "renderer": "canonical_markdown",
                "format": "docx",
                "outputs": ["html", "docx"],
                "docxMode": "editable",
                "preset": "report",
                "locale": "ko-KR",
                "title": "Annual Plan",
                "filename": "exports/annual.docx",
                "source": {"kind": "markdown", "content": _MARKDOWN},
            },
            _context(tmp_path),
        )

        assert result.status == "ok", result.error_code
        assert result.output["documentWriteMode"] == "canonical_markdown"
        assert result.output["canonicalMarkdownOutputs"] == ("html", "docx")
        assert result.output["canonicalMarkdownQa"]["status"] == "passed"
        outputs = result.output["outputs"]
        assert [item["format"] for item in outputs] == ["html", "docx"]
        assert (tmp_path / "outputs" / "exports" / "annual.html").exists()
        assert (tmp_path / "outputs" / "exports" / "annual.docx").exists()
        assert (tmp_path / "outputs" / "exports" / "annual.export-qa.json").exists()

    def test_canonical_markdown_pdf_requires_renderer(self, tmp_path: Path) -> None:
        result = document_write(
            {
                "renderer": "canonical_markdown",
                "format": "pdf",
                "outputs": ["pdf"],
                "title": "Annual Plan",
                "filename": "exports/annual.pdf",
                "source": _MARKDOWN,
            },
            _context(tmp_path),
        )

        assert result.status == "blocked"
        assert result.error_code == "canonical_markdown_renderer_unavailable"

    def test_canonical_fixed_layout_docx_requires_renderer(
        self, tmp_path: Path
    ) -> None:
        result = document_write(
            {
                "renderer": "canonical_markdown",
                "format": "docx",
                "outputs": ["docx"],
                "docxMode": "fixed_layout",
                "title": "Annual Plan",
                "filename": "exports/annual.docx",
                "source": _MARKDOWN,
            },
            _context(tmp_path),
        )

        assert result.status == "blocked"
        assert result.error_code == "canonical_markdown_renderer_unavailable"


class TestDocumentWriteHwpxParity:
    @pytest.mark.parametrize("template", ["base", "gonmun", "report", "minutes"])
    def test_hwpx_writes_valid_package(self, tmp_path: Path, template: str) -> None:
        result = document_write(
            {
                "format": "hwpx",
                "template": template,
                "title": "Annual Plan",
                "content": _MARKDOWN,
                "path": f"docs/plan-{template}.hwpx",
            },
            _context(tmp_path),
        )

        assert result.status == "ok", result.error_code
        _assert_metadata(result.output, "hwpx", f"docs/plan-{template}.hwpx")
        saved = tmp_path / "docs" / f"plan-{template}.hwpx"
        assert saved.read_bytes().startswith(b"PK")
        with zipfile.ZipFile(saved) as archive:
            names = set(archive.namelist())
            assert {
                "mimetype",
                "META-INF/manifest.xml",
                "Contents/header.xml",
                "Contents/section0.xml",
                "version.xml",
            }.issubset(names)
            section = archive.read("Contents/section0.xml").decode("utf-8")
        assert "Annual Plan" in section
        assert "Ship document parity" in section
        assert "<script>" not in section
        assert "&lt;script&gt;" in section
        assert result.output["hwpxValidation"]["status"] == "pass"
        assert result.output["hwpxValidation"]["validator"] == "bundled"
        assert result.output["hwpxContentGuard"]["status"] == "pass"
        assert result.output["hwpxContentGuard"]["validator"] == "bundled"

    def test_hwpx_reference_template_mutation_blocks_clearly(
        self, tmp_path: Path
    ) -> None:
        template = tmp_path / "template.hwpx"
        template.write_bytes(b"PK\x03\x04not-a-real-template")

        result = document_write(
            {
                "format": "hwpx",
                "content": _MARKDOWN,
                "template": {"path": "template.hwpx"},
                "path": "docs/from-template.hwpx",
            },
            _context(tmp_path),
        )

        assert result.status == "blocked"
        assert result.error_code == "hwpx_reference_template_requires_agentic_authoring"
