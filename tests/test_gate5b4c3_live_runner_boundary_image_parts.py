from __future__ import annotations

import base64
from types import SimpleNamespace

from magi_agent.shadow.gate5b4c3_live_runner_boundary import _build_user_message_parts

_PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n").decode("ascii")


def _fake_primitives():
    part = SimpleNamespace(
        from_text=lambda *, text: ("text", text),
        from_bytes=lambda *, data, mime_type: ("image", mime_type, data),
    )
    return SimpleNamespace(Part=part)


def test_text_only_input_yields_single_text_part():
    runner_input = SimpleNamespace(
        sanitized_user_input="hello",
        sanitized_recent_history=(),
        sanitized_image_blocks=(),
    )
    parts = _build_user_message_parts(runner_input, primitives=_fake_primitives())
    assert parts == [("text", "hello")]


def test_image_input_appends_image_parts_after_text():
    runner_input = SimpleNamespace(
        sanitized_user_input="describe this",
        sanitized_recent_history=(),
        sanitized_image_blocks=(SimpleNamespace(media_type="image/png", data=_PNG),),
    )
    parts = _build_user_message_parts(runner_input, primitives=_fake_primitives())
    assert parts[0] == ("text", "describe this")
    assert parts[1] == ("image", "image/png", b"\x89PNG\r\n\x1a\n")
