"""MusicNotation tool — read musical notation from an image via vision model.

Builds on top of ``image_tools._call_vision_model`` with clef-specialised
prompts that include staff-position mnemonics (G-B-D-F-A for bass clef,
E-G-B-D-F for treble clef).  No extra dependencies are required beyond the
core runtime; heavy OMR libraries (audiveris, music21) are future provider-
seam stubs only.

Gate: ``MAGI_FILE_TOOLS_ENABLED=true`` (same outer gate as other file tools;
registered by ``register_file_tool_manifests`` / ``bind_file_toolhost_handlers``).
``enabled_by_default=False`` — operator must opt in.

Provider seam:
- ``MusicNotationProviderPort`` — abstract base for OMR recognition.
- ``VisionModelMusicProvider`` — default: uses ``_call_vision_model`` from
  ``image_tools`` with specialised prompts.
- ``AudiverisProvider`` — stub for future Java-based OMR (not implemented).

Tests use ``monkeypatch`` on ``litellm.completion`` so no live model calls
are required.
"""

from __future__ import annotations

import hashlib
import re
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .context import ToolContext
from .image_tools import _MAX_IMAGE_BYTES, _MIME_BY_EXT, _call_vision_model
from .result import ToolResult
from .spreadsheet_tools import (
    _SpreadsheetPolicyError,
    _base_metadata,
    _blocked_result,
    _error_result,
    _resolve_workspace_path,
    _workspace_root,
)

# ---------------------------------------------------------------------------
# Clef-specialised prompt templates
# ---------------------------------------------------------------------------

_MUSIC_PROMPTS: dict[str, str] = {
    "auto": (
        "This image contains musical notation. "
        "Please identify: (1) the clef type (treble, bass, alto, tenor), "
        "(2) the key signature (number of sharps/flats and the resulting key), "
        "(3) the time signature, "
        "(4) list all notes from left to right with their letter names "
        "(using C, D, E, F, G, A, B) and indicate any accidentals (sharp ♯, flat ♭, natural ♮). "
        "If this is a bass clef, remember: lines from bottom are G, B, D, F, A; "
        "spaces from bottom are A, C, E, G. "
        "If this is a treble clef, remember: lines from bottom are E, G, B, D, F; "
        "spaces from bottom are F, A, C, E."
    ),
    "bass": (
        "This image contains musical notation in BASS CLEF. "
        "Bass clef lines from bottom to top: G, B, D, F, A. "
        "Bass clef spaces from bottom to top: A, C, E, G. "
        "Please list all notes from left to right with their letter names "
        "and any accidentals (sharp ♯, flat ♭, natural ♮). "
        "Also identify the key signature and time signature."
    ),
    "treble": (
        "This image contains musical notation in TREBLE CLEF. "
        "Treble clef lines from bottom to top: E, G, B, D, F. "
        "Treble clef spaces from bottom to top: F, A, C, E. "
        "Please list all notes from left to right with their letter names "
        "and any accidentals (sharp ♯, flat ♭, natural ♮). "
        "Also identify the key signature and time signature."
    ),
    "alto": (
        "This image contains musical notation in ALTO CLEF (C clef on the middle line). "
        "Alto clef lines from bottom to top: F, A, C, E, G. "
        "Please list all notes from left to right with their letter names "
        "and any accidentals. "
        "Also identify the key signature and time signature."
    ),
    "tenor": (
        "This image contains musical notation in TENOR CLEF (C clef on the second-from-top line). "
        "Tenor clef lines from bottom to top: D, F, A, C, E. "
        "Please list all notes from left to right with their letter names "
        "and any accidentals. "
        "Also identify the key signature and time signature."
    ),
}

_SUPPORTED_CLEFS = frozenset({"treble", "bass", "alto", "tenor", "auto"})


# ---------------------------------------------------------------------------
# Provider seam
# ---------------------------------------------------------------------------


@dataclass
class MusicNotationResult:
    """Structured output from an OMR recognition pass."""

    notes: str
    clef_detected: str
    time_signature: str
    key_signature: str


class MusicNotationProviderPort(ABC):
    """Abstract seam for Optical Music Recognition (OMR)."""

    @abstractmethod
    def recognize(self, image_bytes: bytes, *, mime_type: str, clef: str) -> MusicNotationResult:
        """Recognise notation in *image_bytes* and return structured result."""


class VisionModelMusicProvider(MusicNotationProviderPort):
    """Default provider: delegates to vision LLM with clef-specialised prompts."""

    def recognize(
        self,
        image_bytes: bytes,
        *,
        mime_type: str,
        clef: str,
        question: str | None = None,
        adk_tool_context: object = None,
    ) -> MusicNotationResult:
        prompt = _MUSIC_PROMPTS.get(clef, _MUSIC_PROMPTS["auto"])
        if question:
            prompt = f"{prompt}\n\nAdditional question: {question}"

        raw = _call_vision_model(
            image_bytes=image_bytes,
            mime_type=mime_type,
            prompt=prompt,
            adk_tool_context=adk_tool_context,
        )
        parsed = _parse_music_response(raw, clef)
        return MusicNotationResult(
            notes=raw,
            clef_detected=parsed["clefDetected"],
            time_signature=parsed["timeSignature"],
            key_signature=parsed["keySignature"],
        )


class AudiverisProvider(MusicNotationProviderPort):
    """Stub: future Java-based Audiveris OMR provider (not implemented)."""

    def recognize(self, image_bytes: bytes, *, mime_type: str, clef: str) -> MusicNotationResult:
        raise NotImplementedError("AudiverisProvider is not yet implemented")


# ---------------------------------------------------------------------------
# Response parser
# ---------------------------------------------------------------------------


def _parse_music_response(text: str, clef_hint: str) -> dict[str, str]:
    """Extract clef, time signature, and key signature from model response text.

    Falls back to ``clef_hint`` for clefDetected when the model response does
    not explicitly mention a clef name.  All fields fall back to ``"unknown"``.
    """
    result: dict[str, str] = {
        "clefDetected": "unknown",
        "timeSignature": "unknown",
        "keySignature": "unknown",
    }

    # Clef detection: prefer explicit mention in text
    if re.search(r"\bbass\b", text, re.I):
        result["clefDetected"] = "bass"
    elif re.search(r"\btreble\b", text, re.I):
        result["clefDetected"] = "treble"
    elif re.search(r"\balto\b", text, re.I):
        result["clefDetected"] = "alto"
    elif re.search(r"\btenor\b", text, re.I):
        result["clefDetected"] = "tenor"
    elif clef_hint and clef_hint != "auto":
        # Use the hint if the model didn't say explicitly
        result["clefDetected"] = clef_hint

    # Time signature: "4/4", "3/4", "6/8" etc.
    m = re.search(r"\b(\d+/\d+)\b", text)
    if m:
        result["timeSignature"] = m.group(1)

    # Key signature: "C major", "G major", "D minor", "F# minor" etc.
    m = re.search(r"\b([A-G][♯♭#b]?\s*(?:major|minor))\b", text, re.I)
    if m:
        result["keySignature"] = m.group(1).strip()

    return result


# ---------------------------------------------------------------------------
# Public handler
# ---------------------------------------------------------------------------


def music_notation(arguments: Mapping[str, object], context: ToolContext) -> ToolResult:
    """Read musical notation from an image file using a vision model.

    Applies clef-specialised prompts that include staff-position mnemonics
    to improve accuracy on GAIA-style music questions.

    Returns structured fields: ``notes``, ``clefDetected``, ``timeSignature``,
    ``keySignature``, ``contentDigest``.
    """
    tool_name = "music_notation"
    path_text = _str_arg(arguments, "path")
    if path_text is None:
        return _blocked_result(tool_name, "path_required")

    try:
        root = _workspace_root(context)
        resolved = _resolve_workspace_path(root, path_text, must_exist=True)
    except _SpreadsheetPolicyError as error:
        return _blocked_result(tool_name, error.reason_code)
    except OSError:
        return _error_result(tool_name, "music_image_read_failed")

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
        return _error_result(tool_name, "music_image_read_failed")

    if byte_size > _MAX_IMAGE_BYTES:
        return _error_result(tool_name, "image_input_too_large")

    try:
        image_bytes = resolved.path.read_bytes()
    except OSError:
        return _error_result(tool_name, "music_image_read_failed")

    content_digest = f"sha256:{hashlib.sha256(image_bytes).hexdigest()}"

    clef = _str_arg(arguments, "clef") or "auto"
    if clef not in _SUPPORTED_CLEFS:
        clef = "auto"
    question = _str_arg(arguments, "question")

    provider = VisionModelMusicProvider()
    try:
        music_result = provider.recognize(
            image_bytes,
            mime_type=mime_type,
            clef=clef,
            question=question,
            adk_tool_context=context.adk_tool_context,
        )
    except Exception as exc:  # noqa: BLE001
        return ToolResult(
            status="error",
            errorCode="music_recognition_failed",
            errorMessage=str(exc),
            retryable=True,
            metadata=_base_metadata(tool_name, permission_class="read", mutates_workspace=False),
        )

    output: dict[str, object] = {
        "notes": music_result.notes,
        "clefDetected": music_result.clef_detected,
        "timeSignature": music_result.time_signature,
        "keySignature": music_result.key_signature,
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _str_arg(arguments: Mapping[str, object], name: str) -> str | None:
    value = arguments.get(name)
    if isinstance(value, str):
        return value
    return None


__all__ = [
    "MusicNotationProviderPort",
    "MusicNotationResult",
    "VisionModelMusicProvider",
    "AudiverisProvider",
    "music_notation",
    "_parse_music_response",
]
