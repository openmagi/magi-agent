"""Sanitizer for native-Anthropic part conversion on the cache-aware ADK path.

Why this module exists
----------------------
ADK 1.33.0's ``google.adk.models.anthropic_llm.part_to_message_block`` converts
a ``genai`` ``Part`` into an Anthropic content block, and ``raise``\\ s
``NotImplementedError`` for any part whose thinking/text/tool/media predicates
are all falsy (line 265 in ADK 1.33.0). Verified against the installed libs
(google-adk 1.33.0, anthropic 0.116.0), the following shapes fall through and
raise:

* ``Part(thought_signature=b"...")`` with ``thought`` unset (signature-only),
* ``Part(text="", thought=True)`` with no signature (empty thinking),
* ``Part()`` fully empty, and any ``text=""``-only part.

Claude Sonnet 5 uses adaptive thinking enabled server-side by default, so it
emits thinking / signature-bearing blocks even though magi never client-enables
thinking (``thinking_config`` is never set on the ADK native path). Compounding
this, ADK's streaming helper silently drops ``anthropic.types.SignatureDelta``,
so a streamed thinking block is aggregated as ``Part(text=<thinking>,
thought=True)`` with NO signature. A signature-only interleaved thinking block
therefore becomes ``Part(text="", thought=True)`` (the empty-thinking shape). On
the NEXT model call within the same turn (tool-use continuation),
``content_to_message_param`` over history hits the raise and the turn dies with
no final text.

The fix runs BEFORE ADK's ``part_to_message_block`` inside the cache-aware
mixin's message-build seam (``magi_agent.adk_bridge.anthropic_cache_model``,
``generate_content_async``). It never mutates the process-global ADK function
(monkeypatching is rejected: process-wide, affects other consumers, hard to
test); it only sanitizes the parts magi itself is about to convert, on both the
non-stream and stream branches, for both the local and hosted surfaces (both
construct ``CacheAwareClaude``).

Sanitation policy (per part, evaluated before delegating to ADK)
----------------------------------------------------------------
1. Thinking DISABLED (magi today, always): every thought-bearing part
   (``part.thought`` truthy or ``part.thought_signature`` set) is DROPPED. With
   thinking off there is no preservation requirement, signatures are already
   unrecoverable (streaming ``SignatureDelta`` drop), and this also avoids
   sending a ``ThinkingBlockParam(signature="")`` whose API acceptance is
   unknown.
2. Any remaining part that would fall through ADK's raise (empty part,
   ``text=""``-only part) is DROPPED. One structured WARNING is emitted per
   request carrying a counter and the union of field names present on dropped
   parts; the sanitizer never raises.
3. Every convertible part is delegated to ADK's ``part_to_message_block``
   UNCHANGED, so output is byte-identical to the raw ADK path for
   text/tool_use/tool_result/image/pdf histories (Opus 4.8 / Sonnet 4.6 / Haiku
   are unaffected).
4. Empty-message guard: if all parts of a ``Content`` were dropped, the whole
   message is skipped (Anthropic rejects an empty ``content`` array).
5. Thinking ENABLED (future, once streaming ``SignatureDelta`` capture lands):
   signature-bearing thought parts round-trip faithfully via ADK
   (``thought and text`` -> thinking block, ``thought and signature`` ->
   redacted-thinking block); signature-less thought parts are still dropped.

Prompt caching is untouched: this module only decides WHICH parts reach ADK's
converter. The rolling-tail and system-prefix cache seams run in the same order
as before, outside this module.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from google.genai import types as genai_types

_LOGGER = logging.getLogger("magi.adk.anthropic_sanitizer")

# Field names on a ``genai`` Part that indicate convertible (non-thinking)
# content. Used only for the drop-warning field summary; conversion itself is
# always delegated to ADK.
_PART_CONTENT_FIELDS = (
    "text",
    "function_call",
    "function_response",
    "inline_data",
    "executable_code",
    "code_execution_result",
    "thought",
    "thought_signature",
)


def _is_thought_bearing(part: Any) -> bool:
    """True when the part carries reasoning (thinking text or a signature)."""
    return bool(getattr(part, "thought", None)) or bool(
        getattr(part, "thought_signature", None)
    )


def _would_fall_through(part: Any) -> bool:
    """True when ADK's ``part_to_message_block`` would raise on this part.

    Mirrors the predicate chain in ADK 1.33.0 (thinking-with-text,
    thinking-with-signature, text, function_call, function_response, image, pdf,
    executable_code, code_execution_result). A part convertible by ADK returns
    False; a fall-through part (all predicates falsy) returns True.
    """
    thought = getattr(part, "thought", None)
    text = getattr(part, "text", None)
    signature = getattr(part, "thought_signature", None)

    if thought and text:
        return False
    if thought and signature:
        return False
    if text:
        return False
    if getattr(part, "function_call", None):
        return False
    if getattr(part, "function_response", None):
        return False
    inline = getattr(part, "inline_data", None)
    if inline is not None and getattr(inline, "mime_type", None):
        # image / pdf are handled by ADK's _is_image_part / _is_pdf_part; treat
        # any inline mime as convertible so we never drop attachments.
        return False
    if getattr(part, "executable_code", None):
        return False
    if getattr(part, "code_execution_result", None):
        return False
    return True


def _dropped_field_summary(part: Any) -> str:
    """Comma-joined names of populated fields on a dropped part (for warnings)."""
    present = [
        name for name in _PART_CONTENT_FIELDS if getattr(part, name, None)
    ]
    return ",".join(present) if present else "none"


def _sanitize_parts(
    parts: list[Any],
    *,
    thinking_enabled: bool,
    dropped: list[str],
) -> list[Any]:
    """Return the subset of *parts* that should reach ADK's converter.

    Records a field-summary string in *dropped* for each removed part so the
    caller can emit a single aggregate warning per request.
    """
    kept: list[Any] = []
    for part in parts:
        if not thinking_enabled and _is_thought_bearing(part):
            # Policy 1: strip reasoning entirely while thinking is disabled.
            dropped.append(_dropped_field_summary(part))
            continue
        if _would_fall_through(part):
            # Policy 2: drop anything ADK would raise on.
            dropped.append(_dropped_field_summary(part))
            continue
        kept.append(part)
    return kept


def safe_contents_to_message_params(
    contents: list["genai_types.Content"] | None,
    *,
    thinking_enabled: bool = False,
) -> list[dict[str, Any]]:
    """Sanitize *contents* then convert to Anthropic ``MessageParam`` dicts.

    Drop-in replacement for
    ``[content_to_message_param(c) for c in contents]`` used by the cache-aware
    mixin. Convertible parts are delegated to ADK's ``content_to_message_param``
    unchanged (byte-identical output); fall-through / thinking parts are dropped
    per the module policy; Contents whose parts all drop are skipped.

    ``thinking_enabled`` should reflect whether the outgoing request enables
    Anthropic thinking (magi today: always False on the ADK native path, since
    ``thinking_config`` is never set). When False, thought-bearing parts are
    stripped; when True they are left to ADK for faithful round-trip.
    """
    from google.adk.models.anthropic_llm import (  # noqa: PLC0415
        content_to_message_param,
    )
    from google.genai import types as genai_types  # noqa: PLC0415

    messages: list[dict[str, Any]] = []
    dropped: list[str] = []

    for content in contents or []:
        original_parts = list(content.parts or [])
        kept = _sanitize_parts(
            original_parts,
            thinking_enabled=thinking_enabled,
            dropped=dropped,
        )
        if not kept:
            # Empty-message guard: skip a Content whose parts all dropped so we
            # never send an empty ``content`` array (Anthropic rejects it).
            continue
        if len(kept) == len(original_parts):
            # Fast path: nothing dropped -> byte-identical to the raw ADK call.
            messages.append(content_to_message_param(content))
            continue
        sanitized = genai_types.Content(role=content.role, parts=kept)
        messages.append(content_to_message_param(sanitized))

    if dropped:
        _LOGGER.warning(
            "anthropic_part_sanitizer dropped_parts=%d thinking_enabled=%s "
            "shapes=%s",
            len(dropped),
            thinking_enabled,
            ";".join(dropped),
        )

    return messages
