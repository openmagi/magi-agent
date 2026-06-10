"""ImageUnderstand tool — describe or Q&A an image file from the workspace.

The handler loads image bytes and dispatches a vision-model call via litellm
using the configured provider (``~/.magi/config.toml`` or env vars).  litellm
is already a core runtime dependency so no extra package is required.

The previous implementation tried to probe the ADK ``ToolContext`` for a
``.model`` / ``._model`` attribute that does not exist on
``google.adk.agents.context.Context``, causing every call to silently return
the stub string ``"[vision model not available in this context]"``.  This
module replaces that approach with a direct ``litellm.completion`` call so
vision actually works.

Structured extraction mode
--------------------------
Pass ``mode="structured"`` (or ``"extract"``) to ask the vision model to
return *exact* data — numbers, table rows, coordinates, labels — in a clean
machine-parseable form rather than prose.  This is critical for computation
tasks (e.g. GAIA-style questions like "area of the green polygon", "average of
the red numbers in the table") where prose descriptions cause mis-computation.

Optionally pass ``verify=True`` to fire a second vision call that asks the
model to confirm the extracted values against the original image.  This is
disabled by default to control cost.

Valid values for ``mode``:
- ``"prose"`` (default, or omit) — unchanged prose description behaviour.
- ``"structured"`` / ``"extract"`` — structured extraction.

Any other value returns a ``blocked`` result with ``invalid_mode`` reason.
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

# Prompt injected for structured extraction — demands exact data, no prose.
_STRUCTURED_EXTRACTION_PROMPT = (
    "Extract all visible data from this image exactly as it appears. "
    "Transcribe every number, label, table cell, coordinate, and measurement verbatim — "
    "do not summarize, do not paraphrase, do not omit any value. "
    "If there is a table, output each row and column. "
    "If there are numbers or measurements, list them all. "
    "If there are polygon vertices or coordinates, list every point. "
    "Return the result as structured data (JSON-style or clearly delimited), NOT as prose."
)

# Prompt for the optional verify pass — asks the model to confirm extracted values.
_VERIFY_PROMPT_TEMPLATE = (
    "Here is the structured data that was extracted from the image:\n\n{extracted}\n\n"
    "Please verify that every value listed above matches what you can see in the image. "
    "Correct any misread digit, label, or coordinate, and confirm or revise the output."
)

_VALID_MODES: frozenset[str] = frozenset({"prose", "structured", "extract"})

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

    Parameters
    ----------
    arguments:
        ``path``   — workspace-relative path to the image file (required).
        ``prompt`` — user question or instruction (used in prose mode only).
        ``mode``   — extraction mode:
                     * ``"prose"`` (default) — returns a natural-language description.
                     * ``"structured"`` / ``"extract"`` — returns exact data (numbers,
                       table cells, coordinates, labels) in a structured/JSON form.
        ``verify`` — when ``True`` and ``mode="structured"``, fires a second vision
                     call to confirm the extracted values against the image.
                     Disabled by default.

    The handler reads image bytes from the workspace, injects them as multipart
    ``inline_data`` content into the ADK session model, and returns the model's
    description in ``output["description"]`` (prose mode) or
    ``output["extracted_data"]`` (structured mode).
    """
    tool_name = "image_understand"
    path_text = _str_arg(arguments, "path")
    if path_text is None:
        return _blocked_result(tool_name, "path_required")

    # Validate mode early so we don't do expensive I/O for an invalid request.
    mode_raw = _str_arg(arguments, "mode") or "prose"
    if mode_raw not in _VALID_MODES:
        return _blocked_result(
            tool_name,
            "invalid_mode",
            f"mode must be one of: {', '.join(sorted(_VALID_MODES))}; got {mode_raw!r}",
        )
    is_structured = mode_raw in {"structured", "extract"}

    verify_flag = arguments.get("verify")
    do_verify = bool(verify_flag) if verify_flag is not None else False

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

    if is_structured:
        # Structured extraction: ignore user prompt, use the structured prompt.
        extraction_prompt = _STRUCTURED_EXTRACTION_PROMPT
        extracted_data = _call_vision_model(
            image_bytes=image_bytes,
            mime_type=mime_type,
            prompt=extraction_prompt,
            adk_tool_context=context.adk_tool_context,
        )

        verify_output: str | None = None
        if do_verify:
            verify_prompt = _VERIFY_PROMPT_TEMPLATE.format(extracted=extracted_data)
            verify_output = _call_vision_model(
                image_bytes=image_bytes,
                mime_type=mime_type,
                prompt=verify_prompt,
                adk_tool_context=context.adk_tool_context,
            )

        output: dict[str, object] = {
            "extracted_data": extracted_data,
            "mode": "structured",
            "contentDigest": content_digest,
        }
        if verify_output is not None:
            output["verifyOutput"] = verify_output

        return ToolResult(
            status="ok",
            output=output,
            llmOutput=output,
            transcriptOutput={
                "toolName": tool_name,
                "contentDigest": content_digest,
                "byteCount": byte_size,
                "mode": "structured",
            },
            metadata={
                **_base_metadata(tool_name, permission_class="read", mutates_workspace=False),
                "contentDigest": content_digest,
                "byteCount": byte_size,
                "mimeType": mime_type,
                "pathRef": resolved.path_ref,
                "mode": "structured",
            },
        )

    # Default prose mode — unchanged behaviour.
    prompt = _str_arg(arguments, "prompt") or _DEFAULT_PROMPT

    description = _call_vision_model(
        image_bytes=image_bytes,
        mime_type=mime_type,
        prompt=prompt,
        adk_tool_context=context.adk_tool_context,
    )

    prose_output: dict[str, object] = {
        "description": description,
        "contentDigest": content_digest,
    }
    return ToolResult(
        status="ok",
        output=prose_output,
        llmOutput=prose_output,
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
    adk_tool_context: object,  # retained for API compatibility, unused after fix
) -> str:
    """Make a vision-model call via litellm using the configured provider.

    The ``adk_tool_context`` parameter is kept for call-site compatibility but
    is no longer used.  The previous implementation probed
    ``adk_tool_context.model`` which does not exist on
    ``google.adk.agents.context.Context``, causing every call to silently
    return ``"[vision model not available in this context]"``.  This
    implementation uses ``litellm.completion`` directly, reading provider
    credentials from :func:`~magi_agent.cli.providers.resolve_provider_config`.

    Falls back to a descriptive error string on any failure so the agent can
    still make progress rather than crashing.
    """
    try:
        return _call_vision_model_via_litellm(
            image_bytes=image_bytes,
            mime_type=mime_type,
            prompt=prompt,
        )
    except Exception as exc:  # noqa: BLE001
        return f"[vision call failed: {exc}]"


def _call_vision_model_via_litellm(
    *,
    image_bytes: bytes,
    mime_type: str,
    prompt: str,
) -> str:
    """Internal litellm-based vision call — called by :func:`_call_vision_model`.

    Resolves the provider config (api_key + model) from the magi config file or
    environment variables, then issues a ``litellm.completion`` call with the
    image base64-encoded as an ``image_url`` message part.
    """
    import base64  # noqa: PLC0415

    import litellm  # noqa: PLC0415

    from magi_agent.cli.providers import resolve_provider_config  # noqa: PLC0415

    provider_cfg = resolve_provider_config()
    if provider_cfg is not None:
        model_id = provider_cfg.litellm_model
        api_key: str | None = provider_cfg.api_key
    else:
        # Fallback: try env-based auto-detect without a config file.
        # If still nothing, litellm will raise an auth error which the caller
        # wraps into a graceful "[vision call failed: ...]" string.
        model_id = "anthropic/claude-sonnet-4-6"
        api_key = None

    b64 = base64.b64encode(image_bytes).decode()
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime_type};base64,{b64}"},
                },
                {"type": "text", "text": prompt},
            ],
        }
    ]
    resp = litellm.completion(
        model=model_id,
        messages=messages,
        api_key=api_key,
        timeout=60,
        max_tokens=2048,
    )
    return (resp.choices[0].message.content or "").strip() or "[no description returned]"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _str_arg(arguments: Mapping[str, object], name: str) -> str | None:
    value = arguments.get(name)
    if isinstance(value, str):
        return value
    return None


__all__ = ["image_understand"]
