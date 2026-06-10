from __future__ import annotations

import io
import shutil
import subprocess
import tempfile
from pathlib import Path

from magi_agent.tools.context import ToolContext

from .model import DocumentWriteError, NormalizedSource, write_output_bytes

_PDF_TIMEOUT_SECONDS = 60


def write_pdf(
    *,
    context: ToolContext,
    source: NormalizedSource,
    path_value: str,
) -> dict[str, object]:
    converter = shutil.which("libreoffice") or shutil.which("soffice")
    if converter is None:
        raise DocumentWriteError("pdf_converter_unavailable")

    try:
        from docx import Document  # noqa: PLC0415
    except ImportError as error:
        raise DocumentWriteError("document_dependency_not_installed") from error

    from magi_agent.tools.document_write_tools import _render_markdown  # noqa: PLC0415

    with tempfile.TemporaryDirectory(prefix="magi-document-pdf-") as temp_dir:
        temp_root = Path(temp_dir)
        docx_path = temp_root / "intermediate.docx"
        out_dir = temp_root / "pdf"
        out_dir.mkdir(parents=True, exist_ok=True)

        document = Document()
        _render_markdown(document, source.markdown)
        buf = io.BytesIO()
        document.save(buf)
        docx_path.write_bytes(buf.getvalue())

        command = [
            converter,
            "--headless",
            "--convert-to",
            "pdf",
            "--outdir",
            str(out_dir),
            str(docx_path),
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=_PDF_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise DocumentWriteError("document_pdf_conversion_timeout") from error

        if completed.returncode != 0:
            raise DocumentWriteError("document_pdf_conversion_failed")

        pdf_path = out_dir / "intermediate.pdf"
        if not pdf_path.is_file():
            raise DocumentWriteError("document_pdf_conversion_failed")
        data = pdf_path.read_bytes()
        if not data.startswith(b"%PDF-"):
            raise DocumentWriteError("document_pdf_validation_failed")

    return write_output_bytes(
        context=context,
        path_value=path_value,
        default_name="magi-document.pdf",
        fmt="pdf",
        data=data,
    )
