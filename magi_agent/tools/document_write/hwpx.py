from __future__ import annotations

import html
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from magi_agent.tools.context import ToolContext

from .markdown import markdown_to_plain_text
from .model import (
    HWPX_TEMPLATES,
    DocumentWriteError,
    NormalizedSource,
    write_output_bytes,
)

_RUNTIME_ROOT = Path(__file__).resolve().parent / "hwpx_runtime"
_TEMPLATES_ROOT = _RUNTIME_ROOT / "templates"
_BASE_TEMPLATE = _TEMPLATES_ROOT / "base"
_REQUIRED_ENTRIES = (
    "mimetype",
    "META-INF/manifest.xml",
    "Contents/content.hpf",
    "Contents/header.xml",
    "Contents/section0.xml",
)


def write_hwpx(
    *,
    context: ToolContext,
    source: NormalizedSource,
    path_value: str,
    title: str,
    template: object = "report",
) -> dict[str, object]:
    if isinstance(template, dict):
        raise DocumentWriteError("hwpx_reference_template_requires_agentic_authoring")
    effective_template = str(template or "report")
    if effective_template not in HWPX_TEMPLATES:
        raise DocumentWriteError("unsupported_hwpx_template")
    if not _BASE_TEMPLATE.is_dir():
        raise DocumentWriteError("hwpx_runtime_unavailable")

    package = _build_package(source=source, title=title, template=effective_template)
    validation = _validate_package(package)
    if validation["status"] != "pass":
        raise DocumentWriteError("hwpx_validation_failed")

    data = _pack_hwpx(package)
    validation, content_guard = _run_bundled_guards(data, source=source, title=title)

    return write_output_bytes(
        context=context,
        path_value=path_value,
        default_name="magi-document.hwpx",
        fmt="hwpx",
        data=data,
        extra={"hwpxValidation": validation, "hwpxContentGuard": content_guard},
    )


def _build_package(
    *,
    source: NormalizedSource,
    title: str,
    template: str,
) -> dict[str, bytes]:
    files = _read_base_template()
    overlay_root = _TEMPLATES_ROOT / template
    if template != "base" and overlay_root.is_dir():
        for path in overlay_root.iterdir():
            if path.is_file() and path.suffix == ".xml":
                files[f"Contents/{path.name}"] = path.read_bytes()

    section_template = files.get("Contents/section0.xml")
    if section_template is None:
        raise DocumentWriteError("hwpx_runtime_unavailable")
    files["Contents/section0.xml"] = _render_section_xml(
        section_template.decode("utf-8"),
        source=source,
        title=title,
    ).encode("utf-8")
    return files


def _read_base_template() -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for path in _BASE_TEMPLATE.rglob("*"):
        if path.is_file():
            files[path.relative_to(_BASE_TEMPLATE).as_posix()] = path.read_bytes()
    return files


def _render_section_xml(
    section_template: str,
    *,
    source: NormalizedSource,
    title: str,
) -> str:
    close_tag = "</hs:sec>"
    close_index = section_template.rfind(close_tag)
    if close_index < 0:
        raise DocumentWriteError("hwpx_validation_failed")

    paragraphs = _paragraphs_for_source(source=source, title=title)
    body = "\n".join(
        _paragraph_xml(4_000_000_000 + index, paragraph)
        for index, paragraph in enumerate(paragraphs, start=1)
    )
    return f"{section_template[:close_index]}\n{body}\n{close_tag}\n"


def _paragraphs_for_source(*, source: NormalizedSource, title: str) -> tuple[str, ...]:
    lines: list[str] = []
    if title.strip():
        lines.append(title.strip())
    plain_text = markdown_to_plain_text(source.markdown)
    for raw_line in plain_text.splitlines():
        line = raw_line.strip()
        if line:
            lines.append(line)
    deduped: list[str] = []
    seen: set[str] = set()
    for line in lines:
        if line not in seen:
            deduped.append(line)
            seen.add(line)
    return tuple(deduped)


def _paragraph_xml(paragraph_id: int, text: str) -> str:
    escaped = html.escape(text, quote=True)
    return "\n".join(
        (
            f'  <hp:p id="{paragraph_id}" paraPrIDRef="0" styleIDRef="0" '
            'pageBreak="0" columnBreak="0" merged="0">',
            '    <hp:run charPrIDRef="0">',
            f"      <hp:t>{escaped}</hp:t>",
            "    </hp:run>",
            "  </hp:p>",
        )
    )


def _validate_package(files: dict[str, bytes]) -> dict[str, object]:
    missing = [entry for entry in _REQUIRED_ENTRIES if entry not in files]
    if missing:
        return {"status": "fail", "missing": tuple(missing)}
    mimetype = files["mimetype"].decode("utf-8").strip()
    if mimetype != "application/hwp+zip":
        return {"status": "fail", "reason": "bad_mimetype"}
    malformed: list[str] = []
    for name, data in files.items():
        if name.endswith((".xml", ".hpf")):
            try:
                ElementTree.fromstring(data)
            except ElementTree.ParseError:
                malformed.append(name)
    if malformed:
        return {"status": "fail", "malformed": tuple(malformed)}
    return {"status": "pass", "requiredEntries": _REQUIRED_ENTRIES}


def _pack_hwpx(files: dict[str, bytes]) -> bytes:
    import io

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            zipfile.ZipInfo("mimetype"),
            files["mimetype"],
            compress_type=zipfile.ZIP_STORED,
        )
        for name in sorted(files):
            if name == "mimetype":
                continue
            archive.writestr(name, files[name], compress_type=zipfile.ZIP_DEFLATED)
    return buffer.getvalue()


def _run_bundled_guards(
    data: bytes,
    *,
    source: NormalizedSource,
    title: str,
) -> tuple[dict[str, object], dict[str, object]]:
    validate_script = _RUNTIME_ROOT / "scripts" / "validate.py"
    content_guard_script = _RUNTIME_ROOT / "scripts" / "content_guard.py"
    if not validate_script.is_file() or not content_guard_script.is_file():
        raise DocumentWriteError("hwpx_runtime_unavailable")

    with tempfile.TemporaryDirectory(prefix="magi-hwpx-guard-") as temp_dir:
        temp_root = Path(temp_dir)
        output_path = temp_root / "output.hwpx"
        source_path = temp_root / "source.md"
        output_path.write_bytes(data)
        source_path.write_text(source.markdown, encoding="utf-8")

        validation = _run_guard_script(
            [str(validate_script), str(output_path)],
            failure_reason="hwpx_validation_failed",
        )
        content = _run_guard_script(
            [
                str(content_guard_script),
                "--source",
                str(source_path),
                "--output",
                str(output_path),
                "--title",
                title,
            ],
            failure_reason="hwpx_content_guard_failed",
        )

    return (
        {"status": "pass", "validator": "bundled", "stdout": validation[:400]},
        {"status": "pass", "validator": "bundled", "stdout": content[:400]},
    )


def _run_guard_script(command: list[str], *, failure_reason: str) -> str:
    try:
        completed = subprocess.run(
            [sys.executable, *command],
            cwd=_RUNTIME_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise DocumentWriteError(failure_reason) from error
    if completed.returncode != 0:
        raise DocumentWriteError(failure_reason)
    return "\n".join(part for part in (completed.stdout, completed.stderr) if part).strip()


def run_page_guard(*, reference_path: Path, output_path: Path) -> dict[str, object]:
    page_guard_script = _RUNTIME_ROOT / "scripts" / "page_guard.py"
    if not page_guard_script.is_file():
        raise DocumentWriteError("hwpx_runtime_unavailable")
    stdout = _run_guard_script(
        [
            str(page_guard_script),
            "--reference",
            str(reference_path),
            "--output",
            str(output_path),
        ],
        failure_reason="hwpx_page_guard_failed",
    )
    return {"status": "pass", "validator": "bundled", "stdout": stdout[:400]}


def _content_guard(
    data: bytes,
    *,
    source: NormalizedSource,
    title: str,
) -> dict[str, object]:
    with zipfile.ZipFile(__import__("io").BytesIO(data)) as archive:
        section = archive.read("Contents/section0.xml").decode("utf-8")
    output_text = html.unescape(" ".join(re.findall(r"<hp:t[^>]*>(.*?)</hp:t>", section)))
    output_norm = _normalize(output_text)
    markers = [_normalize(line) for line in _paragraphs_for_source(source=source, title=title)]
    markers = [marker for marker in markers if len(marker) >= 4]
    matched = [marker for marker in markers if marker in output_norm]
    required = min(len(markers), 2) if len(markers) <= 4 else min(len(markers), 4)
    if len(matched) < required:
        return {
            "status": "fail",
            "matchedMarkers": len(matched),
            "requiredMarkers": required,
        }
    return {
        "status": "pass",
        "matchedMarkers": len(matched),
        "requiredMarkers": required,
    }


def _normalize(text: str) -> str:
    return re.sub(r"[\s#*_`~>|+\-:.,;!?;()\[\]{}\"']+", "", text.lower())
