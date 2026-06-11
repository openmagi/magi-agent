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
    """Bind all file-tool handlers and enable them via registry policy.

    Returns the names of tools successfully bound.
    """
    from .spreadsheet_tools import xlsx_read, xlsx_info  # noqa: PLC0415
    from .document_tools import document_read, document_search  # noqa: PLC0415
    from .archive_tools import archive_extract  # noqa: PLC0415
    from .image_tools import image_understand  # noqa: PLC0415
    from .audio_tools import audio_transcribe  # noqa: PLC0415
    from .video_tools import video_frames  # noqa: PLC0415
    from .music_tools import music_notation  # noqa: PLC0415

    _handlers: dict[str, object] = {
        "XLSXRead": xlsx_read,
        "XLSXInfo": xlsx_info,
        "DocumentRead": document_read,
        "DocumentSearch": document_search,
        "ArchiveExtract": archive_extract,
        "ImageUnderstand": image_understand,
        "AudioTranscribe": audio_transcribe,
        "VideoFrames": video_frames,
        "MusicNotation": music_notation,
    }

    # Strict default-OFF inner gate (MAGI_DOCUMENT_QA_ENABLED). The bind loop
    # below already skips unregistered names, so this is doubly safe: when the
    # flag is off the manifest is never registered AND the handler is never
    # offered for binding.
    from magi_agent.config.env import document_qa_enabled  # noqa: PLC0415

    if document_qa_enabled():
        from .document_qa_tools import document_qa  # noqa: PLC0415

        _handlers["DocumentQA"] = document_qa
    bound: list[str] = []
    for name, handler in _handlers.items():
        registration = registry.resolve_registration(name)
        if registration is None:
            continue
        registry.bind_handler(name, handler, enabled_by_registry_policy=True)  # type: ignore[arg-type]
        bound.append(name)
    return tuple(bound)


__all__ = ["bind_file_toolhost_handlers"]
