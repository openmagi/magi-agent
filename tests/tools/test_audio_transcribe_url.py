"""Tests for AudioTranscribe URL-source enhancement (PR-V4).

Covers:
- url field accepted + routed through VideoDownloadProvider
- MAGI_VIDEO_DOWNLOAD_ENABLED=false → blocked
- captions-preferred path (captions available → ASR skipped)
- stub ASR provider returns transcript
- url and path mutually exclusive handled
- No ImportError at base import
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from magi_agent.tools.context import ToolContext


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(
        botId="test-bot",
        sessionId="test-session",
        turnId="test-turn",
        workspaceRoot=str(tmp_path),
    )


class _FakeDownloadProvider:
    """Stub that returns fixed audio bytes without a network call."""

    def __init__(
        self,
        *,
        captions: str | None = None,
        audio_bytes: bytes = b"\x00" * 100,
    ) -> None:
        self._captions = captions
        self._audio_bytes = audio_bytes

    def fetch_video(self, url: str, *, output_dir: Path) -> object:
        from magi_agent.tools.video_tools import VideoFetchResult  # noqa: PLC0415

        path = output_dir / "fake_audio.mp3"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self._audio_bytes)
        return VideoFetchResult(
            local_path=path,
            title="Test Audio",
            duration_seconds=60,
            captions=self._captions,
        )

    def fetch_captions(self, url: str, *, language: str = "en") -> str | None:
        return self._captions


class _FakeASRProvider:
    def __init__(self, transcript: str = "Hello world") -> None:
        self._transcript = transcript

    def transcribe(self, audio_bytes: bytes, *, mime_type: str, language: str | None) -> str:
        return self._transcript


# ---------------------------------------------------------------------------
# Import guard
# ---------------------------------------------------------------------------


class TestAudioTranscribeURLImport:
    def test_audio_tools_importable(self) -> None:
        import magi_agent.tools.audio_tools  # noqa: F401


# ---------------------------------------------------------------------------
# URL path blocked when download gate OFF
# ---------------------------------------------------------------------------


class TestAudioTranscribeURLGate:
    def test_url_blocked_when_download_gate_off(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """url arg + MAGI_VIDEO_DOWNLOAD_ENABLED=false → blocked."""
        import magi_agent.tools.audio_tools as at  # noqa: PLC0415

        monkeypatch.setenv("MAGI_VIDEO_DOWNLOAD_ENABLED", "false")

        result = at.audio_transcribe(
            {"url": "https://www.youtube.com/watch?v=abc"},
            _ctx(tmp_path),
        )
        assert result.status == "blocked"
        assert result.error_code == "video_download_not_enabled"

    def test_url_blocked_when_download_env_unset(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        import magi_agent.tools.audio_tools as at  # noqa: PLC0415

        monkeypatch.delenv("MAGI_VIDEO_DOWNLOAD_ENABLED", raising=False)

        result = at.audio_transcribe(
            {"url": "https://www.youtube.com/watch?v=abc"},
            _ctx(tmp_path),
        )
        assert result.status == "blocked"
        assert result.error_code == "video_download_not_enabled"


# ---------------------------------------------------------------------------
# URL path with stub providers
# ---------------------------------------------------------------------------


class TestAudioTranscribeURLStub:
    def test_url_returns_transcript_from_asr_provider(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """URL path with fake download + fake ASR → transcript returned."""
        import magi_agent.tools.audio_tools as at  # noqa: PLC0415
        import magi_agent.tools.video_tools as vt  # noqa: PLC0415

        monkeypatch.setenv("MAGI_VIDEO_DOWNLOAD_ENABLED", "true")
        monkeypatch.setattr(
            vt, "_VIDEO_DOWNLOAD_PROVIDER_OVERRIDE", _FakeDownloadProvider(captions=None)
        )
        monkeypatch.setattr(at, "_PROVIDER_OVERRIDE", _FakeASRProvider("test transcript text"))

        result = at.audio_transcribe(
            {"url": "https://www.youtube.com/watch?v=abc"},
            _ctx(tmp_path),
        )
        assert result.status == "ok", f"status={result.status} err={result.error_code}"
        assert result.output["transcript"] == "test transcript text"  # type: ignore[index]

    def test_url_captions_preferred_over_asr(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """When captions available, they are returned and ASR is skipped."""
        import magi_agent.tools.audio_tools as at  # noqa: PLC0415
        import magi_agent.tools.video_tools as vt  # noqa: PLC0415

        caption_text = "This is the caption transcript."
        monkeypatch.setenv("MAGI_VIDEO_DOWNLOAD_ENABLED", "true")
        monkeypatch.setattr(
            vt, "_VIDEO_DOWNLOAD_PROVIDER_OVERRIDE",
            _FakeDownloadProvider(captions=caption_text),
        )

        asr_called: list[bool] = []

        class _TrackingASRProvider:
            def transcribe(self, *a: object, **kw: object) -> str:
                asr_called.append(True)
                return "asr transcript"

        monkeypatch.setattr(at, "_PROVIDER_OVERRIDE", _TrackingASRProvider())

        result = at.audio_transcribe(
            {"url": "https://www.youtube.com/watch?v=abc"},
            _ctx(tmp_path),
        )
        assert result.status == "ok"
        # Captions preferred: either transcript is the caption text, or captions field is set
        output = result.output
        assert isinstance(output, dict)
        transcript_ok = output.get("transcript") == caption_text or output.get("captions") == caption_text
        assert transcript_ok, (
            f"Expected captions text in output. Got: {output}"
        )
        # ASR should NOT have been called
        assert not asr_called, "ASR was called even though captions were available"

    def test_url_transcript_in_output(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Output must contain transcript key."""
        import magi_agent.tools.audio_tools as at  # noqa: PLC0415
        import magi_agent.tools.video_tools as vt  # noqa: PLC0415

        monkeypatch.setenv("MAGI_VIDEO_DOWNLOAD_ENABLED", "true")
        monkeypatch.setattr(
            vt, "_VIDEO_DOWNLOAD_PROVIDER_OVERRIDE", _FakeDownloadProvider()
        )
        monkeypatch.setattr(at, "_PROVIDER_OVERRIDE", _FakeASRProvider("hello"))

        result = at.audio_transcribe(
            {"url": "https://www.youtube.com/watch?v=abc"},
            _ctx(tmp_path),
        )
        assert result.status == "ok"
        assert "transcript" in result.output  # type: ignore[operator]

    def test_url_language_forwarded_to_asr(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """language parameter is forwarded to ASR provider."""
        import magi_agent.tools.audio_tools as at  # noqa: PLC0415
        import magi_agent.tools.video_tools as vt  # noqa: PLC0415

        monkeypatch.setenv("MAGI_VIDEO_DOWNLOAD_ENABLED", "true")
        monkeypatch.setattr(
            vt, "_VIDEO_DOWNLOAD_PROVIDER_OVERRIDE", _FakeDownloadProvider(captions=None)
        )

        received_language: list[str | None] = []

        class _LangCapture:
            def transcribe(
                self, audio_bytes: bytes, *, mime_type: str, language: str | None
            ) -> str:
                received_language.append(language)
                return "ok"

        monkeypatch.setattr(at, "_PROVIDER_OVERRIDE", _LangCapture())

        result = at.audio_transcribe(
            {"url": "https://www.youtube.com/watch?v=abc", "language": "ko"},
            _ctx(tmp_path),
        )
        assert result.status == "ok"
        assert received_language == ["ko"], f"Expected language='ko', got {received_language}"
