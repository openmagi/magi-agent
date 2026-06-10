"""Tests for image-block threading through the CLI engine (Task 2).

Tests
-----
1. ``TurnInput`` defaults ``image_blocks`` to an empty tuple.
2. ``TurnInput`` carries image blocks supplied by the caller.
3. ``MagiEngineDriver._build_opening_parts`` produces a text part followed by
   one image part per block — using fake ``types`` doubles so the test has zero
   ADK / google-genai dependency.
"""

from __future__ import annotations

import base64

from magi_agent.cli.contracts import TurnInput

_PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n").decode("ascii")


# ---------------------------------------------------------------------------
# TurnInput contract tests
# ---------------------------------------------------------------------------


def test_turn_input_has_image_blocks_default_empty():
    assert TurnInput(prompt="hi").image_blocks == ()


def test_turn_input_carries_image_blocks():
    blocks = (
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": _PNG,
            },
        },
    )
    ti = TurnInput(prompt="describe", image_blocks=blocks)
    assert ti.image_blocks == blocks


# ---------------------------------------------------------------------------
# Engine helper: _build_opening_parts
# ---------------------------------------------------------------------------


class _FakePart:
    """Fake ``types.Part`` that mirrors the two call forms used by the engine.

    - ``Part(text=...)``        → ``("text", <text>)``      (constructor)
    - ``Part.from_bytes(...)``  → ``("image", <mime>, <data>)``  (classmethod)
    """

    def __new__(cls, *, text: str):  # type: ignore[misc]
        return ("text", text)

    @staticmethod
    def from_bytes(*, data: bytes, mime_type: str):
        return ("image", mime_type, data)


def _make_fake_types():
    from types import SimpleNamespace

    return SimpleNamespace(Part=_FakePart)


def test_build_opening_parts_text_only():
    from magi_agent.cli.engine import MagiEngineDriver

    result = MagiEngineDriver._build_opening_parts(_make_fake_types(), "hi", ())
    assert result == [("text", "hi")]


def test_build_opening_parts_appends_image_after_text():
    from magi_agent.cli.engine import MagiEngineDriver

    blocks = (
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": _PNG,
            },
        },
    )
    parts = MagiEngineDriver._build_opening_parts(_make_fake_types(), "describe", blocks)
    assert parts[0] == ("text", "describe")
    assert parts[1] == ("image", "image/png", b"\x89PNG\r\n\x1a\n")


def test_build_opening_parts_skips_invalid_block():
    """Non-image and non-base64 blocks must be skipped gracefully."""
    from magi_agent.cli.engine import MagiEngineDriver

    blocks = (
        {"type": "text", "text": "not an image"},
        {
            "type": "image",
            "source": {"type": "url", "url": "https://example.com/img.png"},
        },
    )
    parts = MagiEngineDriver._build_opening_parts(_make_fake_types(), "hello", blocks)
    # Only the text part — both image blocks are malformed/unsupported
    assert parts == [("text", "hello")]


def test_turn_images_reads_from_dict():
    from magi_agent.cli.engine import MagiEngineDriver

    block = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": _PNG}}
    assert MagiEngineDriver._turn_images({"image_blocks": (block,)}) == (block,)


def test_turn_images_dict_absent_returns_empty():
    from magi_agent.cli.engine import MagiEngineDriver

    assert MagiEngineDriver._turn_images({"prompt": "hi"}) == ()
