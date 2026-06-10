"""Read an image from the OS clipboard for CLI/TUI image attach.

Terminals cannot deliver binary clipboard data on paste, so we shell out to an
OS clipboard tool. The command runner is injected for testability; the real
default shells out via subprocess. Returns a sanitized Anthropic-style image
block (validated/capped by message_builder) or None on any failure.
"""

from __future__ import annotations

import base64
import subprocess
import sys
from collections.abc import Callable, Sequence

from magi_agent.runtime.message_builder import _collect_image_blocks

CommandRunner = Callable[[Sequence[str]], "bytes | None"]

# (magic, media_type) pairs for supported image formats.
_IMAGE_MAGIC: list[tuple[bytes, str]] = [
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"RIFF", "image/webp"),  # RIFF....WEBP — checked further below
]


def _detect_media_type(raw: bytes) -> str | None:
    for magic, media_type in _IMAGE_MAGIC:
        if raw[:len(magic)] == magic:
            if media_type == "image/webp" and raw[8:12] != b"WEBP":
                continue
            return media_type
    return None


def clipboard_commands(platform: str) -> list[tuple[str, ...]]:
    """Ordered OS clipboard image-read commands (stdout = raw image bytes)."""
    if platform == "darwin":
        return [("pngpaste", "-")]
    if platform.startswith("linux"):
        return [
            ("wl-paste", "--type", "image/png"),
            ("xclip", "-selection", "clipboard", "-t", "image/png", "-o"),
        ]
    return []


def _default_runner(cmd: Sequence[str]) -> bytes | None:
    try:
        result = subprocess.run(
            list(cmd), capture_output=True, timeout=5, check=False
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0 or not result.stdout:
        return None
    return result.stdout


def read_clipboard_image(
    *,
    runner: CommandRunner | None = None,
    platform: str | None = None,
) -> dict[str, object] | None:
    """Return one sanitized image block from the clipboard, or None."""
    run = runner or _default_runner
    plat = platform if platform is not None else sys.platform
    for cmd in clipboard_commands(plat):
        raw = run(cmd)
        if not raw:
            continue
        media_type = _detect_media_type(raw)
        if not media_type:
            continue
        block = {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": base64.b64encode(raw).decode("ascii"),
            },
        }
        sanitized = _collect_image_blocks({"imageBlocks": [block]}, {})
        if sanitized:
            return sanitized[0]
    return None
