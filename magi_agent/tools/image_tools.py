"""ImageUnderstand tool — describe or Q&A an image file from the workspace.

The handler loads image bytes and calls the session's vision model via the ADK
inline image content API.  When no ``adk_tool_context`` is available (e.g. in
unit tests) the handler returns a placeholder description so tests can verify
the path-safety and schema logic without making live model calls.

No additional package dependency is required beyond ``google-adk`` (already in
core dependencies) and optionally ``anthropic`` for Claude vision.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path

from .context import ToolContext
from .result import ToolResult
from .spreadsheet_tools import (
    _SpreadsheetPolicyError,
    _base_metadata,
    _blocked_result,
    _error_result,
    _resolve_workspace_path,
    _workspace_root,
)

_MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5 MiB
_DEFAULT_PROMPT = "Describe this image in detail."

_MIME_BY_EXT: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}


def image_understand(arguments: Mapping[str, object], context: ToolContext) -> ToolResult:
    """Describe or answer a question about an image file in the workspace.

    The handler reads image bytes from the workspace, injects them as multipart
    ``inline_data`` content into the ADK session model, and returns the model's
    description in ``output["description"]``.

    When ``context.adk_tool_context`` is absent (unit-test mode), returns a
    stub description so tests can verify path/extension/size logic without a
    live model call.
    """
    tool_name = "image_understand"
    path_text = _str_arg(arguments, "path")
    if path_text is None:
        return _blocked_result(tool_name, "path_required")

    try:
        root = _workspace_root(context)
        resolved = _resolve_workspace_path(root, path_text, must_exist=True)
    except _SpreadsheetPolicyError as error:
        return _blocked_result(tool_name, error.reason_code)
    except OSError:
        return _error_result(tool_name, "image_read_failed")

    suffix = Path(resolved.relative).suffix.casefold()
    mime_type = _MIME_BY_EXT.get(suffix)
    if mime_type is None:
        return _blocked_result(
            tool_name,
            "image_extension_not_supported",
            f"Supported extensions: {', '.join(sorted(_MIME_BY_EXT))}",
        )

    try:
        byte_size = resolved.path.stat().st_size
    except OSError:
        return _error_result(tool_name, "image_read_failed")

    if byte_size > _MAX_IMAGE_BYTES:
        return _error_result(tool_name, "image_input_too_large")

    try:
        image_bytes = resolved.path.read_bytes()
    except OSError:
        return _error_result(tool_name, "image_read_failed")

    content_digest = f"sha256:{hashlib.sha256(image_bytes).hexdigest()}"
    prompt = _str_arg(arguments, "prompt") or _DEFAULT_PROMPT

    # If no ADK tool context is present (unit tests / plan mode without model),
    # return a stub result so tests can validate path logic hermetically.
    if context.adk_tool_context is None:
        description = f"[stub] image bytes={byte_size} mime={mime_type} prompt={prompt!r}"
    else:
        description = _call_vision_model(
            image_bytes=image_bytes,
            mime_type=mime_type,
            prompt=prompt,
            adk_tool_context=context.adk_tool_context,
        )

    output: dict[str, object] = {
        "description": description,
        "contentDigest": content_digest,
    }
    return ToolResult(
        status="ok",
        output=output,
        llmOutput=output,
        transcriptOutput={
            "toolName": tool_name,
            "contentDigest": content_digest,
            "byteCount": byte_size,
        },
        metadata={
            **_base_metadata(tool_name, permission_class="read", mutates_workspace=False),
            "contentDigest": content_digest,
            "byteCount": byte_size,
            "mimeType": mime_type,
            "pathRef": resolved.path_ref,
        },
    )


def _call_vision_model(
    *,
    image_bytes: bytes,
    mime_type: str,
    prompt: str,
    adk_tool_context: object,
) -> str:
    """Make a synchronous vision-model call using the ADK inline image API.

    This is a best-effort call; any failure returns a descriptive error string
    rather than raising so the agent can still make progress.
    """
    try:
        # google-adk: types.Part with inline_data is the canonical multipart API.
        from google.genai import types as genai_types  # noqa: PLC0415

        image_part = genai_types.Part(
            inline_data=genai_types.Blob(mime_type=mime_type, data=image_bytes)
        )
        text_part = genai_types.Part(text=prompt)

        # The ADK tool context exposes the session model; attempt a generate call.
        # Depending on ADK version the attribute may differ; we probe defensively.
        model = getattr(adk_tool_context, "model", None) or getattr(
            adk_tool_context, "_model", None
        )
        if model is None:
            return "[vision model not available in this context]"

        response = model.generate_content([image_part, text_part])
        return response.text or "[no description returned]"
    except Exception as exc:  # noqa: BLE001
        return f"[vision call failed: {exc}]"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _str_arg(arguments: Mapping[str, object], name: str) -> str | None:
    value = arguments.get(name)
    if isinstance(value, str):
        return value
    return None


__all__ = ["image_understand"]
