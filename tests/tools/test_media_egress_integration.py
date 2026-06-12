"""Integration tests: SSRF preflight wired into video_frames + audio url path.

Verifies:
- A blocked (private/metadata/credentialed) URL returns status='blocked'
  errorCode='media_url_egress_blocked' (NOT the retryable download_failed path).
- The preflight runs ONLY after the download gate; on the gated-off path the
  pre-existing 'video_download_not_enabled' behavior is byte-identical.
- A public URL still reaches the (stubbed) download provider.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from magi_agent.tools.context import ToolContext


def _ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(
        botId="test-bot",
        sessionId="test-session",
        turnId="test-turn",
        workspaceRoot=str(tmp_path),
    )


class _FakeDownloadProvider:
    def __init__(self) -> None:
        self.called_with: list[str] = []

    def fetch_video(self, url: str, *, output_dir: Path) -> object:
        from magi_agent.tools.video_tools import VideoFetchResult  # noqa: PLC0415

        self.called_with.append(url)
        return VideoFetchResult(
            local_path=output_dir / "v.mp4",
            title="T",
            duration_seconds=10,
            captions="hello transcript",
        )

    def fetch_captions(self, url: str, *, language: str = "en") -> str | None:
        return "hello transcript"


# ---------------------------------------------------------------------------
# video_frames
# ---------------------------------------------------------------------------


class TestVideoFramesEgressPreflight:
    def test_private_url_blocked_with_egress_code(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        import magi_agent.tools.video_tools as vt  # noqa: PLC0415

        monkeypatch.setenv("MAGI_VIDEO_DOWNLOAD_ENABLED", "true")
        fake = _FakeDownloadProvider()
        monkeypatch.setattr(vt, "_VIDEO_DOWNLOAD_PROVIDER_OVERRIDE", fake)

        result = vt.video_frames(
            {"source": "http://metadata.google.internal/latest/meta-data/"},
            _ctx(tmp_path),
        )

        assert result.status == "blocked"
        assert result.error_code == "media_url_egress_blocked"
        assert "metadata_endpoint_blocked" in (result.error_message or "")
        # Download provider must NOT have been reached.
        assert fake.called_with == []

    def test_gate_off_takes_precedence_over_preflight(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Gate-off path is byte-identical: preflight does not run / change behavior."""
        import magi_agent.tools.video_tools as vt  # noqa: PLC0415

        monkeypatch.delenv("MAGI_VIDEO_DOWNLOAD_ENABLED", raising=False)
        fake = _FakeDownloadProvider()
        monkeypatch.setattr(vt, "_VIDEO_DOWNLOAD_PROVIDER_OVERRIDE", fake)

        # Even a private URL returns the pre-existing gate code, not egress code.
        result = vt.video_frames(
            {"source": "http://169.254.169.254/x.mp4"}, _ctx(tmp_path)
        )
        assert result.status == "blocked"
        assert result.error_code == "video_download_not_enabled"
        assert fake.called_with == []

    def test_public_url_reaches_download_provider(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        import magi_agent.tools.video_tools as vt  # noqa: PLC0415

        monkeypatch.setenv("MAGI_VIDEO_DOWNLOAD_ENABLED", "true")
        fake = _FakeDownloadProvider()
        monkeypatch.setattr(vt, "_VIDEO_DOWNLOAD_PROVIDER_OVERRIDE", fake)

        result = vt.video_frames(
            {"source": "https://www.youtube.com/watch?v=abc"}, _ctx(tmp_path)
        )
        assert result.status == "ok"
        assert fake.called_with == ["https://www.youtube.com/watch?v=abc"]


# ---------------------------------------------------------------------------
# audio url path
# ---------------------------------------------------------------------------


class TestAudioUrlEgressPreflight:
    def test_private_url_blocked_with_egress_code(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        import magi_agent.tools.audio_tools as at  # noqa: PLC0415
        import magi_agent.tools.video_tools as vt  # noqa: PLC0415

        monkeypatch.setenv("MAGI_VIDEO_DOWNLOAD_ENABLED", "true")
        fake = _FakeDownloadProvider()
        monkeypatch.setattr(vt, "_VIDEO_DOWNLOAD_PROVIDER_OVERRIDE", fake)

        result = at._audio_transcribe_url(
            "http://192.168.0.10/audio.mp3", {}, _ctx(tmp_path), "audio_transcribe"
        )
        assert result.status == "blocked"
        assert result.error_code == "media_url_egress_blocked"
        assert "private_network_blocked" in (result.error_message or "")
        assert fake.called_with == []

    def test_gate_off_takes_precedence(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        import magi_agent.tools.audio_tools as at  # noqa: PLC0415

        monkeypatch.delenv("MAGI_VIDEO_DOWNLOAD_ENABLED", raising=False)
        result = at._audio_transcribe_url(
            "http://192.168.0.10/audio.mp3", {}, _ctx(tmp_path), "audio_transcribe"
        )
        assert result.status == "blocked"
        assert result.error_code == "video_download_not_enabled"

    def test_public_url_reaches_download_provider(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        import magi_agent.tools.audio_tools as at  # noqa: PLC0415
        import magi_agent.tools.video_tools as vt  # noqa: PLC0415

        monkeypatch.setenv("MAGI_VIDEO_DOWNLOAD_ENABLED", "true")
        fake = _FakeDownloadProvider()
        monkeypatch.setattr(vt, "_VIDEO_DOWNLOAD_PROVIDER_OVERRIDE", fake)

        result = at._audio_transcribe_url(
            "https://www.youtube.com/watch?v=abc", {}, _ctx(tmp_path), "audio_transcribe"
        )
        assert result.status == "ok"
        assert fake.called_with == ["https://www.youtube.com/watch?v=abc"]
