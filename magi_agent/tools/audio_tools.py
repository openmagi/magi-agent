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
    # I-4: routed through the typed flag registry.
    from magi_agent.config.flags import flag_str  # noqa: PLC0415

    provider_name = (flag_str("MAGI_ASR_PROVIDER") or "openai_whisper").strip()
    if provider_name == "openai_whisper":
        return OpenAIWhisperProvider()
    return None


# ---------------------------------------------------------------------------
# Public handler
# ---------------------------------------------------------------------------


def audio_transcribe(arguments: Mapping[str, object], context: ToolContext) -> ToolResult:
    """Transcribe an audio file (or YouTube URL) via the configured ASR provider.

    Accepts either:
    - ``path``: workspace-relative path to a local audio file.
    - ``url``: YouTube or direct audio URL (requires ``MAGI_VIDEO_DOWNLOAD_ENABLED=true``).

    Selects the ASR provider from ``MAGI_ASR_PROVIDER`` env var (default:
    ``openai_whisper``).  Returns ``status="blocked"`` when no provider is
    configured or when the provider dependency is missing.

    Caption-preferred path: when ``url`` is given and the download provider
    returns captions, they are returned directly without an ASR call (cheaper).
    """
    tool_name = "audio_transcribe"

    url_text = _str_arg(arguments, "url")
    if url_text is not None:
        return _audio_transcribe_url(url_text, arguments, context, tool_name)

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
# URL path handler (YouTube / direct audio URL)
# ---------------------------------------------------------------------------


def _audio_transcribe_url(
    url: str,
    arguments: Mapping[str, object],
    context: ToolContext,
    tool_name: str,
) -> ToolResult:
    """Handle the ``url`` argument path: download audio, prefer captions, else ASR."""
    # Gate check
    if not _is_video_download_enabled():
        return _blocked_result(tool_name, "video_download_not_enabled")

    # SSRF preflight: static string-level validation of the user-supplied URL
    # only (no DNS/redirect resolution). Runs AFTER the gate and BEFORE the
    # download provider/try-block so a block maps to status='blocked'.
    from .media_egress import (  # noqa: PLC0415
        MediaEgressBlocked,
        assert_media_url_allowed,
    )

    try:
        assert_media_url_allowed(url)
    except MediaEgressBlocked as exc:
        return _blocked_result(tool_name, "media_url_egress_blocked", exc.reason_code)

    # Import video_tools lazily to avoid circular dependency at module level
    try:
        from .video_tools import (  # noqa: PLC0415
            VideoDownloadProviderPort,
            _VIDEO_DOWNLOAD_PROVIDER_OVERRIDE,
            _get_download_provider,
        )
    except ImportError as exc:
        return _blocked_result(
            tool_name,
            "audio_url_provider_not_configured",
            f"video_tools not available: {exc}",
        )

    import tempfile  # noqa: PLC0415

    downloader = _get_download_provider()
    language = _str_arg(arguments, "language")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        try:
            fetch_result = downloader.fetch_video(url, output_dir=tmp_path)
        except RuntimeError as exc:
            return ToolResult(
                status="error",
                errorCode="audio_url_download_failed",
                errorMessage=str(exc),
                retryable=True,
                metadata=_base_metadata(tool_name, permission_class="net", mutates_workspace=False),
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(
                status="error",
                errorCode="audio_url_download_failed",
                errorMessage=str(exc),
                retryable=True,
                metadata=_base_metadata(tool_name, permission_class="net", mutates_workspace=False),
            )

        # Caption-preferred path: return captions without ASR
        if fetch_result.captions:
            output: dict[str, object] = {
                "transcript": fetch_result.captions,
                "captions": fetch_result.captions,
                "captionSource": "auto-generated",
            }
            return ToolResult(
                status="ok",
                output=output,
                llmOutput=output,
                transcriptOutput={
                    "toolName": tool_name,
                    "charCount": len(fetch_result.captions),
                    "source": "captions",
                },
                metadata=_base_metadata(tool_name, permission_class="net", mutates_workspace=False),
            )

        # Read downloaded audio file
        local_path = fetch_result.local_path
        if not local_path.exists():
            return _error_result(tool_name, "audio_url_download_failed")

        try:
            audio_bytes = local_path.read_bytes()
        except OSError:
            return _error_result(tool_name, "audio_read_failed")

        # Determine MIME from extension
        suffix = local_path.suffix.casefold()
        mime_type = _MIME_BY_EXT.get(suffix, "audio/mpeg")
        content_digest = f"sha256:{hashlib.sha256(audio_bytes).hexdigest()}"

        provider = _get_provider()
        if provider is None:
            return _blocked_result(tool_name, "audio_asr_provider_not_configured")

        try:
            raw_transcript = provider.transcribe(
                audio_bytes, mime_type=mime_type, language=language
            )
        except RuntimeError as exc:
            if "not installed" in str(exc):
                return _blocked_result(tool_name, "audio_asr_provider_not_configured")
            return ToolResult(
                status="error",
                errorCode="asr_provider_error",
                errorMessage=str(exc),
                retryable=True,
                metadata=_base_metadata(tool_name, permission_class="net", mutates_workspace=False),
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(
                status="error",
                errorCode="asr_provider_error",
                errorMessage=str(exc),
                retryable=True,
                metadata=_base_metadata(tool_name, permission_class="net", mutates_workspace=False),
            )

        transcript, redacted = _sanitize_text(raw_transcript)
        out: dict[str, object] = {
            "transcript": transcript,
            "contentDigest": content_digest,
        }
        if redacted:
            out["redacted"] = True

        return ToolResult(
            status="ok",
            output=out,
            llmOutput=out,
            transcriptOutput={
                "toolName": tool_name,
                "charCount": len(transcript),
                "contentDigest": content_digest,
                "source": "asr",
            },
            metadata=_base_metadata(tool_name, permission_class="net", mutates_workspace=False),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_video_download_enabled() -> bool:
    # I-4: routed through the typed flag registry.
    from magi_agent.config.flags import flag_bool  # noqa: PLC0415

    return flag_bool("MAGI_VIDEO_DOWNLOAD_ENABLED")


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
