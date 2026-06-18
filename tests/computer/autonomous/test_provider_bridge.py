import pytest

from magi_agent.computer.autonomous.provider_bridge import (
    BridgeError,
    build_chat_step,
    build_step_messages,
)


def test_build_step_messages_embeds_image_and_tree() -> None:
    msgs = build_step_messages(
        task="open settings",
        ax_tree='[element_index 1] AXButton "Settings"',
        screenshot_b64="QUJD",
        history=["clicked Back"],
    )
    assert msgs[0]["role"] == "system"
    user = msgs[-1]
    assert user["role"] == "user"
    parts = user["content"]
    text_blob = " ".join(p.get("text", "") for p in parts if p["type"] == "text")
    assert "open settings" in text_blob
    assert "AXButton" in text_blob
    assert "clicked Back" in text_blob
    image_parts = [p for p in parts if p["type"] == "image_url"]
    assert image_parts and image_parts[0]["image_url"]["url"].startswith(
        "data:image/png;base64,QUJD"
    )


def test_build_chat_step_requires_provider() -> None:
    with pytest.raises(BridgeError):
        build_chat_step(None)
