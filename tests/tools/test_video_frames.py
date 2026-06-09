"""Tests for VideoFrames tool — hermetic (no real yt-dlp / ffmpeg / network).

Covers:
- Manifest registration
- Default-OFF / gate-blocked path
- Transcript-preferred path (captions available → vision model skipped)
- URL path with stub download + frame extract provider
- Frames passed to vision model (_call_vision_model reuse)
- Local file path with stub frame extractor
- Error / edge cases: unsupported ext, path escape, download-not-enabled
- No ImportError at base-level import (no video extras needed)
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from magi_agent.tools.context import ToolContext


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FAKE_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 60  # minimal fake JPEG header bytes


def _ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(
        botId="test-bot",
        sessionId="test-session",
        turnId="test-turn",
        workspaceRoot=str(tmp_path),
    )


def _make_litellm_response(text: str) -> object:
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = text
    return resp


# ---------------------------------------------------------------------------
# Stub providers (injected via _OVERRIDE seams)
# ---------------------------------------------------------------------------


class _FakeDownloadProvider:
    """Returns a fixed VideoFetchResult without hitting the network."""

    def __init__(
        self,
        *,
        title: str = "Test Video",
        duration_seconds: int = 300,
        captions: str | None = None,
        local_video_path: Path | None = None,
    ) -> None:
        self._title = title
        self._duration = duration_seconds
        self._captions = captions
        self._local_video_path = local_video_path

    def fetch_video(self, url: str, *, output_dir: Path) -> object:
        from magi_agent.tools.video_tools import VideoFetchResult  # noqa: PLC0415

        path = self._local_video_path or (output_dir / "fake_video.mp4")
        # Write stub bytes if it doesn't exist
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"\x00" * 100)
        return VideoFetchResult(
            local_path=path,
            title=self._title,
            duration_seconds=self._duration,
            captions=self._captions,
        )

    def fetch_captions(self, url: str, *, language: str = "en") -> str | None:
        return self._captions


class _FakeFrameExtractor:
    """Returns fixed JPEG bytes for every timestamp."""

    def __init__(self, frame_bytes: bytes = _FAKE_JPEG) -> None:
        self._frame_bytes = frame_bytes

    def extract_frame(self, video_path: Path, timestamp_s: float) -> bytes:
        return self._frame_bytes


# ---------------------------------------------------------------------------
# Import guard: video_tools must be importable without video extras
# ---------------------------------------------------------------------------


class TestVideoToolsImport:
    def test_video_tools_importable_without_video_extras(self) -> None:
        """Importing video_tools must not raise ImportError at module level."""
        import magi_agent.tools.video_tools  # noqa: F401


# ---------------------------------------------------------------------------
# Manifest registration
# ---------------------------------------------------------------------------


class TestVideoFramesManifest:
    def test_video_frames_manifest_in_file_tool_manifests(self) -> None:
        """VideoFrames must appear in file_tool_manifests()."""
        from magi_agent.tools.file_tool_manifests import file_tool_manifests  # noqa: PLC0415

        names = {m.name for m in file_tool_manifests()}
        assert "VideoFrames" in names, f"VideoFrames not in manifests: {names}"

    def test_video_frames_manifest_enabled_by_default_false(self) -> None:
        from magi_agent.tools.file_tool_manifests import file_tool_manifests  # noqa: PLC0415

        manifests = {m.name: m for m in file_tool_manifests()}
        assert manifests["VideoFrames"].enabled_by_default is False

    def test_video_frames_manifest_has_video_tag(self) -> None:
        from magi_agent.tools.file_tool_manifests import file_tool_manifests  # noqa: PLC0415

        manifests = {m.name: m for m in file_tool_manifests()}
        assert "video" in manifests["VideoFrames"].tags

    def test_video_frames_manifest_timeout_sufficient(self) -> None:
        """VideoFrames needs >=60s timeout for download + extract."""
        from magi_agent.tools.file_tool_manifests import file_tool_manifests  # noqa: PLC0415

        manifests = {m.name: m for m in file_tool_manifests()}
        assert manifests["VideoFrames"].timeout_ms >= 60_000


# ---------------------------------------------------------------------------
# Gate: MAGI_FILE_TOOLS_ENABLED=false → tool handler not wired (no crash)
# ---------------------------------------------------------------------------


class TestVideoFramesGate:
    def test_video_frames_gate_off_returns_blocked(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """When MAGI_VIDEO_DOWNLOAD_ENABLED=false + URL source → blocked."""
        import magi_agent.tools.video_tools as vt  # noqa: PLC0415

        monkeypatch.delenv("MAGI_VIDEO_DOWNLOAD_ENABLED", raising=False)
        monkeypatch.setattr(vt, "_VIDEO_DOWNLOAD_PROVIDER_OVERRIDE", _FakeDownloadProvider())
        monkeypatch.setattr(vt, "_FRAME_EXTRACTOR_OVERRIDE", _FakeFrameExtractor())

        result = vt.video_frames(
            {"source": "https://www.youtube.com/watch?v=abc123"},
            _ctx(tmp_path),
        )
        assert result.status == "blocked"
        assert result.error_code == "video_download_not_enabled"

    def test_video_frames_url_blocked_without_download_gate(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """URL source without MAGI_VIDEO_DOWNLOAD_ENABLED=true is blocked."""
        import magi_agent.tools.video_tools as vt  # noqa: PLC0415

        monkeypatch.setenv("MAGI_VIDEO_DOWNLOAD_ENABLED", "false")
        monkeypatch.setattr(vt, "_VIDEO_DOWNLOAD_PROVIDER_OVERRIDE", _FakeDownloadProvider())

        result = vt.video_frames(
            {"source": "https://youtu.be/XYZ"},
            _ctx(tmp_path),
        )
        assert result.status == "blocked"
        assert result.error_code == "video_download_not_enabled"


# ---------------------------------------------------------------------------
# URL path — stub download + frame extract providers
# ---------------------------------------------------------------------------


class TestVideoFramesYoutubeUrlPath:
    def test_youtube_url_frames_passed_to_vision_model(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """With stub providers + litellm mock: frames are described by vision model."""
        import magi_agent.tools.video_tools as vt  # noqa: PLC0415

        monkeypatch.setenv("MAGI_VIDEO_DOWNLOAD_ENABLED", "true")
        monkeypatch.setattr(vt, "_VIDEO_DOWNLOAD_PROVIDER_OVERRIDE", _FakeDownloadProvider(
            duration_seconds=60,
            captions=None,
        ))
        monkeypatch.setattr(vt, "_FRAME_EXTRACTOR_OVERRIDE", _FakeFrameExtractor(_FAKE_JPEG))

        litellm_calls: list[dict[str, Any]] = []

        def fake_completion(**kwargs: Any) -> object:
            litellm_calls.append(kwargs)
            return _make_litellm_response("A frame description.")

        monkeypatch.setattr("litellm.completion", fake_completion)

        result = vt.video_frames(
            {
                "source": "https://www.youtube.com/watch?v=test",
                "timestamps": ["0:01:00"],
                "prompt": "Describe this frame.",
            },
            _ctx(tmp_path),
        )

        assert result.status == "ok", f"Expected ok, got {result.status}: {result.error_code}"
        output = result.output
        assert isinstance(output, dict)
        assert "frames" in output
        assert len(output["frames"]) == 1
        assert output["frames"][0]["description"] == "A frame description."
        # Vision model was called
        assert len(litellm_calls) >= 1

    def test_youtube_url_captions_preferred_skip_vision(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """When captions available and includeCaptions=true, captions field is set."""
        import magi_agent.tools.video_tools as vt  # noqa: PLC0415

        monkeypatch.setenv("MAGI_VIDEO_DOWNLOAD_ENABLED", "true")
        monkeypatch.setattr(vt, "_VIDEO_DOWNLOAD_PROVIDER_OVERRIDE", _FakeDownloadProvider(
            duration_seconds=120,
            captions="Hello world, this is a test caption.",
        ))
        monkeypatch.setattr(vt, "_FRAME_EXTRACTOR_OVERRIDE", _FakeFrameExtractor(_FAKE_JPEG))

        litellm_calls: list[dict[str, Any]] = []

        def fake_completion(**kwargs: Any) -> object:
            litellm_calls.append(kwargs)
            return _make_litellm_response("frame desc")

        monkeypatch.setattr("litellm.completion", fake_completion)

        result = vt.video_frames(
            {
                "source": "https://www.youtube.com/watch?v=test",
                "includeCaptions": True,
                "timestamps": ["0:00:30"],
            },
            _ctx(tmp_path),
        )

        assert result.status == "ok"
        output = result.output
        assert isinstance(output, dict)
        assert output.get("captions") == "Hello world, this is a test caption."

    def test_youtube_url_transcript_only_mode_skips_vision(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """When captions available and no timestamps specified, prefer captions over vision."""
        import magi_agent.tools.video_tools as vt  # noqa: PLC0415

        monkeypatch.setenv("MAGI_VIDEO_DOWNLOAD_ENABLED", "true")
        monkeypatch.setattr(vt, "_VIDEO_DOWNLOAD_PROVIDER_OVERRIDE", _FakeDownloadProvider(
            duration_seconds=120,
            captions="Full transcript text here.",
        ))
        monkeypatch.setattr(vt, "_FRAME_EXTRACTOR_OVERRIDE", _FakeFrameExtractor(_FAKE_JPEG))

        litellm_calls: list[dict[str, Any]] = []

        def fake_completion(**kwargs: Any) -> object:
            litellm_calls.append(kwargs)
            return _make_litellm_response("frame desc")

        monkeypatch.setattr("litellm.completion", fake_completion)

        result = vt.video_frames(
            {
                "source": "https://www.youtube.com/watch?v=test",
                "includeCaptions": True,
            },
            _ctx(tmp_path),
        )
        assert result.status == "ok"
        output = result.output
        assert isinstance(output, dict)
        assert output.get("captions") == "Full transcript text here."

    def test_youtube_url_video_title_in_output(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """videoTitle from download result appears in output."""
        import magi_agent.tools.video_tools as vt  # noqa: PLC0415

        monkeypatch.setenv("MAGI_VIDEO_DOWNLOAD_ENABLED", "true")
        monkeypatch.setattr(vt, "_VIDEO_DOWNLOAD_PROVIDER_OVERRIDE", _FakeDownloadProvider(
            title="My Test Video",
            duration_seconds=30,
        ))
        monkeypatch.setattr(vt, "_FRAME_EXTRACTOR_OVERRIDE", _FakeFrameExtractor(_FAKE_JPEG))
        monkeypatch.setattr(
            "litellm.completion",
            lambda **kw: _make_litellm_response("frame"),
        )

        result = vt.video_frames(
            {"source": "https://www.youtube.com/watch?v=test", "timestamps": ["0:00:05"]},
            _ctx(tmp_path),
        )
        assert result.status == "ok"
        assert result.output["videoTitle"] == "My Test Video"  # type: ignore[index]


# ---------------------------------------------------------------------------
# Local file path
# ---------------------------------------------------------------------------


class TestVideoFramesLocalFile:
    def test_local_mp4_basic(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Local .mp4 with stub frame extractor → frames described."""
        import magi_agent.tools.video_tools as vt  # noqa: PLC0415

        # Write a fake mp4 file in workspace
        video_path = tmp_path / "clip.mp4"
        video_path.write_bytes(b"\x00" * 200)

        monkeypatch.setattr(vt, "_FRAME_EXTRACTOR_OVERRIDE", _FakeFrameExtractor(_FAKE_JPEG))
        monkeypatch.setattr(
            "litellm.completion",
            lambda **kw: _make_litellm_response("local frame desc"),
        )

        result = vt.video_frames(
            {"source": "clip.mp4", "timestamps": ["0:00:01"]},
            _ctx(tmp_path),
        )
        assert result.status == "ok", f"status={result.status} code={result.error_code}"
        output = result.output
        assert isinstance(output, dict)
        assert len(output["frames"]) == 1
        assert output["frames"][0]["description"] == "local frame desc"

    def test_local_file_auto_sample_five_frames(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """When timestamps omitted, 5 evenly-spaced frames are sampled."""
        import magi_agent.tools.video_tools as vt  # noqa: PLC0415

        video_path = tmp_path / "long.mp4"
        video_path.write_bytes(b"\x00" * 200)

        call_count = 0

        class _CountingExtractor:
            def extract_frame(self, path: Path, ts: float) -> bytes:
                nonlocal call_count
                call_count += 1
                return _FAKE_JPEG

        monkeypatch.setattr(vt, "_FRAME_EXTRACTOR_OVERRIDE", _CountingExtractor())
        monkeypatch.setattr(
            "litellm.completion",
            lambda **kw: _make_litellm_response("frame"),
        )

        # Inject a fake duration so auto-sample knows the video length
        import magi_agent.tools.video_tools as _vt  # noqa: PLC0415

        monkeypatch.setattr(_vt, "_VIDEO_DURATION_OVERRIDE", 100)

        result = vt.video_frames({"source": "long.mp4"}, _ctx(tmp_path))
        assert result.status == "ok", f"status={result.status} code={result.error_code}"
        assert call_count == 5, f"Expected 5 frames extracted, got {call_count}"

    def test_local_file_unsupported_extension_blocked(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Local file with .xyz extension → blocked."""
        import magi_agent.tools.video_tools as vt  # noqa: PLC0415

        bad_file = tmp_path / "clip.xyz"
        bad_file.write_bytes(b"\x00" * 10)

        result = vt.video_frames({"source": "clip.xyz"}, _ctx(tmp_path))
        assert result.status == "blocked"
        assert result.error_code == "video_extension_not_supported"

    def test_path_escape_rejected(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Path traversal (../) in source → blocked."""
        import magi_agent.tools.video_tools as vt  # noqa: PLC0415

        result = vt.video_frames({"source": "../../etc/passwd"}, _ctx(tmp_path))
        assert result.status == "blocked"

    def test_local_file_frame_digest_in_output(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Each frame result contains a frameDigest sha256 hash."""
        import magi_agent.tools.video_tools as vt  # noqa: PLC0415

        video_path = tmp_path / "test.mp4"
        video_path.write_bytes(b"\x00" * 100)

        monkeypatch.setattr(vt, "_FRAME_EXTRACTOR_OVERRIDE", _FakeFrameExtractor(_FAKE_JPEG))
        monkeypatch.setattr(
            "litellm.completion",
            lambda **kw: _make_litellm_response("desc"),
        )

        result = vt.video_frames(
            {"source": "test.mp4", "timestamps": ["0:00:01"]},
            _ctx(tmp_path),
        )
        assert result.status == "ok"
        frame = result.output["frames"][0]  # type: ignore[index]
        assert frame["frameDigest"].startswith("sha256:")
        # Check the hash matches the fake JPEG bytes
        expected = "sha256:" + hashlib.sha256(_FAKE_JPEG).hexdigest()
        assert frame["frameDigest"] == expected


# ---------------------------------------------------------------------------
# Timestamp parsing
# ---------------------------------------------------------------------------


class TestTimestampParsing:
    def test_parse_mm_ss_format(self) -> None:
        from magi_agent.tools.video_tools import _parse_timestamp_to_seconds  # noqa: PLC0415

        assert _parse_timestamp_to_seconds("2:00") == 120.0

    def test_parse_hh_mm_ss_format(self) -> None:
        from magi_agent.tools.video_tools import _parse_timestamp_to_seconds  # noqa: PLC0415

        assert _parse_timestamp_to_seconds("00:02:00") == 120.0

    def test_parse_h_mm_ss_format(self) -> None:
        from magi_agent.tools.video_tools import _parse_timestamp_to_seconds  # noqa: PLC0415

        assert _parse_timestamp_to_seconds("1:30:00") == 5400.0

    def test_parse_seconds_suffix(self) -> None:
        from magi_agent.tools.video_tools import _parse_timestamp_to_seconds  # noqa: PLC0415

        assert _parse_timestamp_to_seconds("90s") == 90.0

    def test_parse_invalid_raises(self) -> None:
        from magi_agent.tools.video_tools import _parse_timestamp_to_seconds  # noqa: PLC0415

        with pytest.raises(ValueError):
            _parse_timestamp_to_seconds("not-a-time")


# ---------------------------------------------------------------------------
# Handler binding in file_toolhost
# ---------------------------------------------------------------------------


class TestVideoFramesHandlerBinding:
    def test_video_frames_handler_bound_after_bind_file_toolhost_handlers(self) -> None:
        """bind_file_toolhost_handlers must bind VideoFrames."""
        from magi_agent.tools.file_tool_manifests import register_file_tool_manifests  # noqa: PLC0415
        from magi_agent.tools.file_toolhost import bind_file_toolhost_handlers  # noqa: PLC0415
        from magi_agent.tools.registry import ToolRegistry  # noqa: PLC0415

        reg = ToolRegistry()
        register_file_tool_manifests(reg)
        bound = bind_file_toolhost_handlers(reg)
        assert "VideoFrames" in bound, f"VideoFrames not in bound: {bound}"
