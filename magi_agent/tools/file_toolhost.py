"""Handler bindings for the optional file & multimodal tool suite.

Each handler is imported lazily from its implementation module so this module
can be imported even when the optional extras (openpyxl, pypdf, python-docx,
openai) are not installed.  Missing dependencies are surfaced at call-time via
``status="blocked"`` results rather than import-time errors.

Entry point used by the CLI tool runtime::

    from magi_agent.tools.file_toolhost import bind_file_toolhost_handlers
    bind_file_toolhost_handlers(registry)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .registry import ToolRegistry


def bind_file_toolhost_handlers(registry: "ToolRegistry") -> tuple[str, ...]:
    """Bind all four file-tool handlers and enable them via registry policy.

    Returns the names of tools successfully bound.
    """
    from .spreadsheet_tools import xlsx_read  # noqa: PLC0415
    from .document_tools import document_read  # noqa: PLC0415
    from .image_tools import image_understand  # noqa: PLC0415
    from .audio_tools import audio_transcribe  # noqa: PLC0415

    _handlers: dict[str, object] = {
        "XLSXRead": xlsx_read,
        "DocumentRead": document_read,
        "ImageUnderstand": image_understand,
        "AudioTranscribe": audio_transcribe,
    }
    bound: list[str] = []
    for name, handler in _handlers.items():
        registration = registry.resolve_registration(name)
        if registration is None:
            continue
        registry.bind_handler(name, handler, enabled_by_registry_policy=True)  # type: ignore[arg-type]
        bound.append(name)
    return tuple(bound)


__all__ = ["bind_file_toolhost_handlers"]
