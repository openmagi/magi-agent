"""AudioTranscribe tool — transcribe audio files in the workspace via ASR.

Provider seam design: ``AudioTranscribeProviderPort`` is the abstract base;
``OpenAIWhisperProvider`` is the default concrete provider (requires the
``openai>=1.0`` package installed via ``uv sync --extra audio``).

The handler selects a provider via ``MAGI_ASR_PROVIDER`` env var:
  - ``openai_whisper`` (default) → ``OpenAIWhisperProvider``

When no provider is configured or the required dependency is missing the handler
returns ``status="blocked"`` with ``errorCode="audio_asr_provider_not_configured"``.

Tests use a mock provider injected via ``_PROVIDER_OVERRIDE`` so no live ASR
API call is required in the test suite.
"""

from __future__ import annotations

import hashlib
import os
from abc import ABC, abstractmethod
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
    _sanitize_text,
    _workspace_root,
)

_MAX_AUDIO_BYTES = 25 * 1024 * 1024  # 25 MiB (Whisper API limit)

_MIME_BY_EXT: dict[str, str] = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".m4a": "audio/mp4",
    ".flac": "audio/flac",
}

# ---------------------------------------------------------------------------
# Provider seam
# ---------------------------------------------------------------------------


class AudioTranscribeProviderPort(ABC):
    """Abstract provider seam for ASR transcription."""

    @abstractmethod
    def transcribe(
        self,
        audio_bytes: bytes,
        *,
        mime_type: str,
        language: str | None,
    ) -> str:
        """Transcribe *audio_bytes* and return the transcript as a string."""


class OpenAIWhisperProvider(AudioTranscribeProviderPort):
    """Concrete provider: OpenAI Whisper API (``openai>=1.0`` required)."""

    def transcribe(
        self,
        audio_bytes: bytes,
        *,
        mime_type: str,
        language: str | None,
    ) -> str:
        try:
            from openai import OpenAI  # noqa: PLC0415
        except ImportError as exc:
            raise RuntimeError("openai package not installed") from exc

        import io  # noqa: PLC0415

        # Determine file extension from mime_type for openai client
        ext_map = {
            "audio/mpeg": ".mp3",
            "audio/wav": ".wav",
            "audio/ogg": ".ogg",
            "audio/mp4": ".m4a",
            "audio/flac": ".flac",
        }
        ext = ext_map.get(mime_type, ".mp3")
        audio_file = io.BytesIO(audio_bytes)
        audio_file.name = f"audio{ext}"

        client = OpenAI()
        kwargs: dict[str, object] = {"model": "whisper-1", "file": audio_file}
        if language:
            kwargs["language"] = language
        response = client.audio.transcriptions.create(**kwargs)
        return str(response.text)


# ---------------------------------------------------------------------------
# Provider override seam (used by tests to inject a mock)
# ---------------------------------------------------------------------------

_PROVIDER_OVERRIDE: AudioTranscribeProviderPort | None = None


def _get_provider() -> AudioTranscribeProviderPort | None:
    if _PROVIDER_OVERRIDE is not None:
        return _PROVIDER_OVERRIDE
    provider_name = os.environ.get("MAGI_ASR_PROVIDER", "openai_whisper").strip()
    if provider_name == "openai_whisper":
        return OpenAIWhisperProvider()
    return None


# ---------------------------------------------------------------------------
# Public handler
# ---------------------------------------------------------------------------


def audio_transcribe(arguments: Mapping[str, object], context: ToolContext) -> ToolResult:
    """Transcribe an audio file from the workspace via the configured ASR provider.

    Selects the provider from ``MAGI_ASR_PROVIDER`` env var (default:
    ``openai_whisper``).  Returns ``status="blocked"`` when no provider is
    configured or when the provider dependency is missing.
    """
    tool_name = "audio_transcribe"
    path_text = _str_arg(arguments, "path")
    if path_text is None:
        return _blocked_result(tool_name, "path_required")

    try:
        root = _workspace_root(context)
        resolved = _resolve_workspace_path(root, path_text, must_exist=True)
    except _SpreadsheetPolicyError as error:
        return _blocked_result(tool_name, error.reason_code)
    except OSError:
        return _error_result(tool_name, "audio_read_failed")

    suffix = Path(resolved.relative).suffix.casefold()
    mime_type = _MIME_BY_EXT.get(suffix)
    if mime_type is None:
        return _blocked_result(
            tool_name,
            "audio_extension_not_supported",
            f"Supported extensions: {', '.join(sorted(_MIME_BY_EXT))}",
        )

    try:
        byte_size = resolved.path.stat().st_size
    except OSError:
        return _error_result(tool_name, "audio_read_failed")

    if byte_size > _MAX_AUDIO_BYTES:
        return _error_result(tool_name, "audio_input_too_large")

    try:
        audio_bytes = resolved.path.read_bytes()
    except OSError:
        return _error_result(tool_name, "audio_read_failed")

    content_digest = f"sha256:{hashlib.sha256(audio_bytes).hexdigest()}"
    language = _str_arg(arguments, "language")

    provider = _get_provider()
    if provider is None:
        return _blocked_result(tool_name, "audio_asr_provider_not_configured")

    try:
        raw_transcript = provider.transcribe(
            audio_bytes, mime_type=mime_type, language=language
        )
    except RuntimeError as exc:
        # Provider dependency missing (e.g. openai not installed)
        if "not installed" in str(exc):
            return _blocked_result(tool_name, "audio_asr_provider_not_configured")
        return ToolResult(
            status="error",
            errorCode="asr_provider_error",
            errorMessage=str(exc),
            retryable=True,
            metadata={
                **_base_metadata(tool_name, permission_class="read", mutates_workspace=False),
                "reason": "asr_provider_error",
            },
        )
    except Exception as exc:  # noqa: BLE001
        return ToolResult(
            status="error",
            errorCode="asr_provider_error",
            errorMessage=str(exc),
            retryable=True,
            metadata={
                **_base_metadata(tool_name, permission_class="read", mutates_workspace=False),
                "reason": "asr_provider_error",
            },
        )

    transcript, redacted = _sanitize_text(raw_transcript)

    output: dict[str, object] = {
        "transcript": transcript,
        "contentDigest": content_digest,
    }
    if redacted:
        output["redacted"] = True

    return ToolResult(
        status="ok",
        output=output,
        llmOutput=output,
        transcriptOutput={
            "toolName": tool_name,
            "charCount": len(transcript),
            "contentDigest": content_digest,
        },
        metadata={
            **_base_metadata(tool_name, permission_class="read", mutates_workspace=False),
            "contentDigest": content_digest,
            "byteCount": byte_size,
            "mimeType": mime_type,
            "pathRef": resolved.path_ref,
            "redactionStatus": "redacted" if redacted else "no_redaction_needed",
            "networkAllowed": True,  # override: ASR is a network call
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
    "AudioTranscribeProviderPort",
    "OpenAIWhisperProvider",
    "audio_transcribe",
]
