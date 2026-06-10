from __future__ import annotations

import base64

from magi_agent.cli.clipboard_image import clipboard_commands, read_clipboard_image

_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


def test_reads_png_via_runner_returns_sanitized_block():
    def runner(cmd):
        return _PNG_BYTES

    block = read_clipboard_image(runner=runner, platform="darwin")
    assert block is not None
    assert block["source"]["media_type"] == "image/png"
    assert base64.b64decode(block["source"]["data"]) == _PNG_BYTES


def test_returns_none_when_no_tool_succeeds():
    def runner(cmd):
        return None

    assert read_clipboard_image(runner=runner, platform="darwin") is None
    assert read_clipboard_image(runner=runner, platform="linux") is None


def test_returns_none_on_non_image_bytes():
    def runner(cmd):
        return b"not an image"

    assert read_clipboard_image(runner=runner, platform="darwin") is None


def test_returns_none_on_oversized_image():
    # >5 MB PNG payload: _collect_image_blocks drops it, so we get None.
    big = b"\x89PNG\r\n\x1a\n" + b"\x00" * (6 * 1024 * 1024)

    def runner(cmd):
        return big

    assert read_clipboard_image(runner=runner, platform="darwin") is None


def test_platform_command_selection():
    assert any("pngpaste" in c[0] for c in clipboard_commands("darwin"))
    for linux_platform in ("linux", "linux2"):  # sys.platform is "linux2" on many systems
        linux_cmds = clipboard_commands(linux_platform)
        assert any("wl-paste" in c[0] for c in linux_cmds)
        assert any("xclip" in c[0] for c in linux_cmds)
    assert clipboard_commands("win32") == []


import pytest


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (b"\xff\xd8\xff\xe0" + b"\x00" * 32, "image/jpeg"),
        (b"GIF89a" + b"\x00" * 32, "image/gif"),
        (b"GIF87a" + b"\x00" * 32, "image/gif"),
        (b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 16, "image/webp"),
    ],
)
def test_detects_media_type_from_magic_bytes(raw, expected):
    block = read_clipboard_image(runner=lambda cmd: raw, platform="darwin")
    assert block is not None
    assert block["source"]["media_type"] == expected


def test_riff_non_webp_is_not_treated_as_image():
    # RIFF container that is NOT WebP (e.g. WAVE audio) must be rejected.
    raw = b"RIFF" + b"\x00\x00\x00\x00" + b"WAVE" + b"\x00" * 16
    assert read_clipboard_image(runner=lambda cmd: raw, platform="darwin") is None
