from __future__ import annotations

import base64

from magi_agent.transport.chat import _extract_last_user_image_blocks

_PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n").decode("ascii")


def test_extracts_native_anthropic_image_block():
    payload = {"messages": [{"role": "user", "content": [
        {"type": "text", "text": "what is this"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": _PNG}},
    ]}]}
    blocks = _extract_last_user_image_blocks(payload)
    assert len(blocks) == 1
    assert blocks[0]["source"]["media_type"] == "image/png"
    assert blocks[0]["source"]["data"] == _PNG


def test_extracts_openai_data_url_image_block():
    payload = {"messages": [{"role": "user", "content": [
        {"type": "text", "text": "what is this"},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{_PNG}"}},
    ]}]}
    blocks = _extract_last_user_image_blocks(payload)
    assert len(blocks) == 1
    assert blocks[0]["source"]["media_type"] == "image/png"
    assert blocks[0]["source"]["data"] == _PNG


def test_drops_unsupported_and_text_only():
    payload = {"messages": [{"role": "user", "content": [
        {"type": "text", "text": "hi"},
        {"type": "image_url", "image_url": {"url": "data:image/svg+xml;base64," + _PNG}},
    ]}]}
    assert _extract_last_user_image_blocks(payload) == []


def test_string_content_yields_no_images():
    payload = {"messages": [{"role": "user", "content": "plain text"}]}
    assert _extract_last_user_image_blocks(payload) == []
