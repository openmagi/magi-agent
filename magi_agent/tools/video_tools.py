"""VideoFrames tool — extract frames from a video at timestamps and describe them.

Provider seam design:
- ``VideoDownloadProviderPort`` — abstract base for URL-based video/audio download.
  Concrete: ``YtDlpProvider`` (requires ``yt-dlp`` from ``[video]`` extra).
- ``FrameExtractProviderPort`` — abstract base for per-frame JPEG extraction.
  Concrete: ``FfmpegFrameExtractor`` (requires system ``ffmpeg`` in PATH).

Gate environment variables:
- ``MAGI_FILE_TOOLS_ENABLED`` — outer gate (existing); must be true for this
  tool to be registered at all.
- ``MAGI_VIDEO_DOWNLOAD_ENABLED`` — inner gate; must be true for URL-based
  downloads (YouTube etc.). Local workspace file processing only needs the
  outer gate.

Tests inject fake providers via ``_VIDEO_DOWNLOAD_PROVIDER_OVERRIDE`` and
``_FRAME_EXTRACTOR_OVERRIDE`` so no real yt-dlp / ffmpeg calls are made in
the test suite.
"""

from __future__ import annotations

import hashlib
import os
import re
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .context import ToolContext
from .image_tools import _call_vision_model
from .media_egress import MediaEgressBlocked, assert_media_url_allowed
from .result import ToolResult
from .spreadsheet_tools import (
    _SpreadsheetPolicyError,
    _base_metadata,
    _blocked_result,
    _error_result,
    _resolve_workspace_path,
    _workspace_root,
)

_SUPPORTED_VIDEO_EXTENSIONS: frozenset[str] = frozenset(
    {".mp4", ".webm", ".avi", ".mov", ".mkv"}
)
_MAX_VIDEO_BYTES = 500 * 1024 * 1024  # 500 MiB
_DEFAULT_FRAME_COUNT = 5
_MAX_FRAMES = 10
_DEFAULT_PROMPT = "Describe what is happening in this video frame."

# Used by tests to override auto-detected video duration for local files.
_VIDEO_DURATION_OVERRIDE: int | None = None


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class VideoFetchResult:
    """Result from a video/audio download operation."""

    local_path: Path
    title: str
    duration_seconds: int
    captions: str | None  # pre-fetched caption text or None


# ---------------------------------------------------------------------------
# Provider seams
# ---------------------------------------------------------------------------


class VideoDownloadProviderPort(ABC):
    """Abstract seam for YouTube/URL video or audio download."""

    @abstractmethod
    def fetch_video(self, url: str, *, output_dir: Path) -> VideoFetchResult:
        """Download video from *url* into *output_dir* and return metadata."""

    @abstractmethod
    def fetch_captions(self, url: str, *, language: str = "en") -> str | None:
        """Fetch caption/subtitle text for *url*. Returns None if unavailable."""


class YtDlpProvider(VideoDownloadProviderPort):
    """Concrete provider using yt-dlp (requires ``[video]`` extra)."""

    def fetch_video(self, url: str, *, output_dir: Path) -> VideoFetchResult:
        try:
            import yt_dlp  # noqa: PLC0415
        except ImportError as exc:
            raise RuntimeError("yt_dlp package not installed") from exc

        output_dir.mkdir(parents=True, exist_ok=True)
        ydl_opts: dict[str, object] = {
            "outtmpl": str(output_dir / "%(title)s.%(ext)s"),
            "quiet": True,
            "no_warnings": True,
        }
        captions: str | None = None
        try:
            captions = self.fetch_captions(url)
        except Exception:  # noqa: BLE001
            pass

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title: str = info.get("title", "Unknown") if isinstance(info, dict) else "Unknown"
            duration: int = int(info.get("duration", 0)) if isinstance(info, dict) else 0
            ext: str = info.get("ext", "mp4") if isinstance(info, dict) else "mp4"
            # Find the downloaded file
            safe_title = ydl.prepare_filename(info) if info else str(output_dir / f"video.{ext}")
            local_path = Path(safe_title)
            if not local_path.exists():
                # Fallback: find first video file in output_dir
                matches = list(output_dir.glob("*"))
                local_path = matches[0] if matches else local_path

        return VideoFetchResult(
            local_path=local_path,
            title=title,
            duration_seconds=duration,
            captions=captions,
        )

    def fetch_captions(self, url: str, *, language: str = "en") -> str | None:
        try:
            import yt_dlp  # noqa: PLC0415
        except ImportError:
            return None

        import tempfile  # noqa: PLC0415

        with tempfile.TemporaryDirectory() as tmpdir:
            ydl_opts: dict[str, object] = {
                "skip_download": True,
                "writeautomaticsub": True,
                "writesubtitles": True,
                "subtitleslangs": [language],
                "outtmpl": str(Path(tmpdir) / "%(title)s.%(ext)s"),
                "quiet": True,
                "no_warnings": True,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                try:
                    ydl.download([url])
                except Exception:  # noqa: BLE001
                    return None
            # Find .vtt or .srt file
            for suffix in (".vtt", ".srt", ".ttml"):
                for f in Path(tmpdir).glob(f"*.{suffix.lstrip('.')}"):
                    raw = f.read_text(encoding="utf-8", errors="replace")
                    return _strip_vtt_tags(raw)
        return None


class FrameExtractProviderPort(ABC):
    """Abstract seam for per-frame JPEG extraction from a video file."""

    @abstractmethod
    def extract_frame(self, video_path: Path, timestamp_s: float) -> bytes:
        """Extract the frame at *timestamp_s* seconds and return JPEG bytes."""


class FfmpegFrameExtractor(FrameExtractProviderPort):
    """Concrete frame extractor using the ``ffmpeg`` system CLI."""

    def extract_frame(self, video_path: Path, timestamp_s: float) -> bytes:
        import io  # noqa: PLC0415
        import subprocess  # noqa: PLC0415

        cmd = [
            "ffmpeg",
            "-ss", str(timestamp_s),
            "-i", str(video_path),
            "-frames:v", "1",
            "-vf", "scale=iw*min(1\\,1280/iw):ih*min(1\\,720/ih):flags=lanczos",
            "-q:v", "3",
            "-f", "image2",
            "pipe:1",
            "-loglevel", "error",
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=30)  # noqa: S603
        except FileNotFoundError as exc:
            raise RuntimeError("ffmpeg not found in PATH") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("ffmpeg timed out") from exc
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg failed: {result.stderr.decode()[:200]}")
        return result.stdout


def _get_local_video_duration(video_path: Path) -> int:
    """Best-effort duration probe using ffprobe; returns 0 on failure."""
    if _VIDEO_DURATION_OVERRIDE is not None:
        return _VIDEO_DURATION_OVERRIDE

    import subprocess  # noqa: PLC0415

    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(video_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)  # noqa: S603
        if result.returncode == 0:
            return int(float(result.stdout.strip()))
    except Exception:  # noqa: BLE001
        pass
    return 60  # fallback: assume 60s for auto-sampling


# ---------------------------------------------------------------------------
# Provider override seams (used by tests to inject mocks)
# ---------------------------------------------------------------------------

_VIDEO_DOWNLOAD_PROVIDER_OVERRIDE: VideoDownloadProviderPort | None = None
_FRAME_EXTRACTOR_OVERRIDE: FrameExtractProviderPort | None = None


def _get_download_provider() -> VideoDownloadProviderPort:
    if _VIDEO_DOWNLOAD_PROVIDER_OVERRIDE is not None:
        return _VIDEO_DOWNLOAD_PROVIDER_OVERRIDE
    return YtDlpProvider()


def _get_frame_extractor() -> FrameExtractProviderPort:
    if _FRAME_EXTRACTOR_OVERRIDE is not None:
        return _FRAME_EXTRACTOR_OVERRIDE
    return FfmpegFrameExtractor()


# ---------------------------------------------------------------------------
# Timestamp parsing
# ---------------------------------------------------------------------------


def _parse_timestamp_to_seconds(ts: str) -> float:
    """Parse a timestamp string into seconds.

    Supports:
    - ``"MM:SS"`` → seconds
    - ``"HH:MM:SS"`` → seconds
    - ``"Xs"`` (e.g. ``"90s"``) → seconds
    """
    ts = ts.strip()
    # "Xs" format
    if re.fullmatch(r"\d+(\.\d+)?s", ts):
        return float(ts[:-1])
    # Colon-separated
    parts = ts.split(":")
    if len(parts) == 2:
        m = int(parts[0])
        s = float(parts[1])
        return m * 60 + s
    if len(parts) == 3:
        h = int(parts[0])
        m = int(parts[1])
        s = float(parts[2])
        return h * 3600 + m * 60 + s
    raise ValueError(f"Cannot parse timestamp: {ts!r}")


# ---------------------------------------------------------------------------
# Caption VTT stripping
# ---------------------------------------------------------------------------


def _strip_vtt_tags(vtt_text: str) -> str:
    """Remove VTT timing lines and XML tags, returning plain text."""
    lines: list[str] = []
    for line in vtt_text.splitlines():
        line = line.strip()
        # Skip WEBVTT header, NOTE lines, timing lines
        if line.startswith("WEBVTT") or line.startswith("NOTE") or "-->" in line:
            continue
        # Remove XML/HTML tags
        line = re.sub(r"<[^>]+>", "", line)
        if line:
            lines.append(line)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public handler
# ---------------------------------------------------------------------------


def video_frames(arguments: Mapping[str, object], context: ToolContext) -> ToolResult:
    """Extract frames from a video at timestamps and describe them via vision model.

    Accepts a YouTube URL or a workspace-local video file path.
    Falls back to captions/transcript when available (no vision call needed).

    When ``MAGI_VIDEO_DOWNLOAD_ENABLED`` is not set to a true value, URL-based
    sources return ``status="blocked"`` with ``errorCode="video_download_not_enabled"``.
    """
    tool_name = "video_frames"
    source = _str_arg(arguments, "source")
    if source is None:
        return _blocked_result(tool_name, "source_required")

    is_url = source.startswith("http://") or source.startswith("https://")

    # ------------------------------------------------------------------
    # URL path
    # ------------------------------------------------------------------
    if is_url:
        download_enabled = _is_video_download_enabled()
        if not download_enabled:
            return _blocked_result(tool_name, "video_download_not_enabled")

        # SSRF preflight: static string-level validation of the user-supplied
        # URL only (no DNS/redirect resolution). Placed BEFORE the download
        # try-block so a block maps to status='blocked' rather than the
        # retryable download-failure handler. fetch_captions is invoked
        # internally by fetch_video on the SAME url, so one guard suffices.
        try:
            assert_media_url_allowed(source)
        except MediaEgressBlocked as exc:
            return _blocked_result(tool_name, "media_url_egress_blocked", exc.reason_code)

        import tempfile  # noqa: PLC0415

        downloader = _get_download_provider()
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            try:
                fetch_result = downloader.fetch_video(source, output_dir=tmp_path)
            except RuntimeError as exc:
                return ToolResult(
                    status="error",
                    errorCode="video_download_failed",
                    errorMessage=str(exc),
                    retryable=True,
                    metadata=_base_metadata(tool_name, permission_class="net", mutates_workspace=False),
                )
            except Exception as exc:  # noqa: BLE001
                return ToolResult(
                    status="error",
                    errorCode="video_download_failed",
                    errorMessage=str(exc),
                    retryable=True,
                    metadata=_base_metadata(tool_name, permission_class="net", mutates_workspace=False),
                )

            include_captions = _bool_arg(arguments, "includeCaptions", default=True)
            captions: str | None = fetch_result.captions if include_captions else None

            timestamps_raw = _list_arg(arguments, "timestamps")

            # If captions available and no explicit timestamps → caption-only mode
            if captions and not timestamps_raw:
                output: dict[str, object] = {
                    "frames": [],
                    "captions": captions,
                    "videoTitle": fetch_result.title,
                    "videoDurationSeconds": fetch_result.duration_seconds,
                    "captionSource": "auto-generated",
                }
                return ToolResult(
                    status="ok",
                    output=output,
                    llmOutput=output,
                    transcriptOutput={"toolName": tool_name},
                    metadata=_base_metadata(tool_name, permission_class="net", mutates_workspace=False),
                )

            # Extract frames
            frames = _extract_and_describe_frames(
                video_path=fetch_result.local_path,
                duration_seconds=fetch_result.duration_seconds,
                timestamps_raw=timestamps_raw,
                prompt=_str_arg(arguments, "prompt") or _DEFAULT_PROMPT,
                tool_name=tool_name,
                adk_tool_context=context.adk_tool_context,
            )

            output = {
                "frames": frames,
                "captions": captions,
                "videoTitle": fetch_result.title,
                "videoDurationSeconds": fetch_result.duration_seconds,
                "captionSource": "auto-generated" if captions else "none",
            }
            return ToolResult(
                status="ok",
                output=output,
                llmOutput=output,
                transcriptOutput={"toolName": tool_name, "frameCount": len(frames)},
                metadata=_base_metadata(tool_name, permission_class="net", mutates_workspace=False),
            )

    # ------------------------------------------------------------------
    # Local file path
    # ------------------------------------------------------------------
    try:
        root = _workspace_root(context)
        resolved = _resolve_workspace_path(root, source, must_exist=True)
    except _SpreadsheetPolicyError as error:
        return _blocked_result(tool_name, error.reason_code)
    except OSError:
        return _error_result(tool_name, "video_read_failed")

    suffix = Path(resolved.relative).suffix.casefold()
    if suffix not in _SUPPORTED_VIDEO_EXTENSIONS:
        return _blocked_result(
            tool_name,
            "video_extension_not_supported",
            f"Supported extensions: {', '.join(sorted(_SUPPORTED_VIDEO_EXTENSIONS))}",
        )

    try:
        byte_size = resolved.path.stat().st_size
    except OSError:
        return _error_result(tool_name, "video_read_failed")

    if byte_size > _MAX_VIDEO_BYTES:
        return _error_result(tool_name, "video_file_too_large")

    duration_s = _get_local_video_duration(resolved.path)
    timestamps_raw = _list_arg(arguments, "timestamps")
    frames = _extract_and_describe_frames(
        video_path=resolved.path,
        duration_seconds=duration_s,
        timestamps_raw=timestamps_raw,
        prompt=_str_arg(arguments, "prompt") or _DEFAULT_PROMPT,
        tool_name=tool_name,
        adk_tool_context=context.adk_tool_context,
    )

    output = {
        "frames": frames,
        "captions": None,
        "videoTitle": None,
        "videoDurationSeconds": duration_s,
        "captionSource": "none",
    }
    return ToolResult(
        status="ok",
        output=output,
        llmOutput=output,
        transcriptOutput={
            "toolName": tool_name,
            "frameCount": len(frames),
            "pathRef": resolved.path_ref,
        },
        metadata={
            **_base_metadata(tool_name, permission_class="read", mutates_workspace=False),
            "pathRef": resolved.path_ref,
            "byteCount": byte_size,
        },
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_and_describe_frames(
    *,
    video_path: Path,
    duration_seconds: int,
    timestamps_raw: list[str] | None,
    prompt: str,
    tool_name: str,
    adk_tool_context: object,
) -> list[dict[str, object]]:
    """Extract frames at given timestamps and call vision model on each."""
    extractor = _get_frame_extractor()

    if timestamps_raw:
        try:
            timestamp_seconds = [_parse_timestamp_to_seconds(ts) for ts in timestamps_raw[:_MAX_FRAMES]]
        except ValueError as exc:
            return [{"error": str(exc), "errorCode": "timestamp_parse_error"}]
    else:
        # Auto-sample 5 evenly-spaced frames
        n = _DEFAULT_FRAME_COUNT
        dur = max(duration_seconds, n)
        step = dur / (n + 1)
        timestamp_seconds = [step * (i + 1) for i in range(n)]

    frames: list[dict[str, object]] = []
    for ts in timestamp_seconds:
        ts_str = _seconds_to_hms(ts)
        try:
            frame_bytes = extractor.extract_frame(video_path, ts)
        except RuntimeError as exc:
            frames.append({
                "timestamp": ts_str,
                "description": f"[frame extraction failed: {exc}]",
                "frameDigest": None,
            })
            continue

        digest = f"sha256:{hashlib.sha256(frame_bytes).hexdigest()}"
        description = _call_vision_model(
            image_bytes=frame_bytes,
            mime_type="image/jpeg",
            prompt=prompt,
            adk_tool_context=adk_tool_context,
        )
        frames.append({
            "timestamp": ts_str,
            "description": description,
            "frameDigest": digest,
        })

    return frames


def _seconds_to_hms(seconds: float) -> str:
    h = int(seconds) // 3600
    m = (int(seconds) % 3600) // 60
    s = int(seconds) % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def _is_video_download_enabled() -> bool:
    val = os.environ.get("MAGI_VIDEO_DOWNLOAD_ENABLED", "").strip().lower()
    return val in ("1", "true", "yes", "on")


def _str_arg(arguments: Mapping[str, object], name: str) -> str | None:
    value = arguments.get(name)
    if isinstance(value, str):
        return value
    return None


def _bool_arg(arguments: Mapping[str, object], name: str, *, default: bool = False) -> bool:
    value = arguments.get(name)
    if isinstance(value, bool):
        return value
    return default


def _list_arg(arguments: Mapping[str, object], name: str) -> list[str] | None:
    value = arguments.get(name)
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return None


__all__ = [
    "VideoDownloadProviderPort",
    "VideoFetchResult",
    "YtDlpProvider",
    "FrameExtractProviderPort",
    "FfmpegFrameExtractor",
    "video_frames",
    "_parse_timestamp_to_seconds",
    "_VIDEO_DOWNLOAD_PROVIDER_OVERRIDE",
    "_FRAME_EXTRACTOR_OVERRIDE",
    "_VIDEO_DURATION_OVERRIDE",
]
