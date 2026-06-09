from __future__ import annotations

import base64

from magi_agent.shadow.gate5b4c3_image_parts import image_blocks_to_parts


def _block(media_type: str, raw: bytes) -> dict:
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": base64.b64encode(raw).decode("ascii"),
        },
    }


def test_converts_blocks_to_parts_with_decoded_bytes():
    calls: list[tuple[bytes, str]] = []

    def factory(*, data: bytes, mime_type: str):
        calls.append((data, mime_type))
        return ("part", mime_type)

    parts = image_blocks_to_parts(
        [_block("image/png", b"\x89PNG..."), _block("image/jpeg", b"\xff\xd8jpg")],
        part_factory=factory,
    )

    assert parts == [("part", "image/png"), ("part", "image/jpeg")]
    assert calls == [(b"\x89PNG...", "image/png"), (b"\xff\xd8jpg", "image/jpeg")]


def test_skips_malformed_blocks():
    def factory(*, data: bytes, mime_type: str):
        return (data, mime_type)

    parts = image_blocks_to_parts(
        [
            {"type": "text", "text": "nope"},
            {"type": "image"},
            {"type": "image", "source": {"type": "base64"}},
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "!!notb64!!"}},
        ],
        part_factory=factory,
    )

    assert parts == []


def test_empty_returns_empty():
    assert image_blocks_to_parts([], part_factory=lambda *, data, mime_type: None) == []
