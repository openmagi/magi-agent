from __future__ import annotations

import base64

import pytest

from magi_agent.shadow.gate5b4c3_shadow_generation_contract import (
    Gate5B4C3ShadowGenerationImageBlock,
    Gate5B4C3ShadowGenerationTurn,
)

_PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n").decode("ascii")


def _turn(**overrides):
    base = {
        "turnId": "turn_abc123",
        "turnDigest": "sha256:" + "a" * 64,
        "sanitizedCurrentTurnText": "describe this image",
        "sanitizedInputTextDigest": "sha256:" + "b" * 64,
    }
    base.update(overrides)
    return base


def test_turn_defaults_to_no_image_blocks():
    turn = Gate5B4C3ShadowGenerationTurn.model_validate(_turn())
    assert turn.sanitized_image_blocks == ()


def test_turn_round_trips_image_blocks():
    turn = Gate5B4C3ShadowGenerationTurn.model_validate(
        _turn(sanitizedImageBlocks=[{"mediaType": "image/png", "data": _PNG}])
    )
    assert len(turn.sanitized_image_blocks) == 1
    block = turn.sanitized_image_blocks[0]
    assert isinstance(block, Gate5B4C3ShadowGenerationImageBlock)
    assert block.media_type == "image/png"
    assert block.data == _PNG
    dumped = turn.model_dump(by_alias=True, mode="python")
    assert dumped["sanitizedImageBlocks"][0]["mediaType"] == "image/png"


def test_turn_rejects_unsupported_media_type():
    with pytest.raises(ValueError):
        Gate5B4C3ShadowGenerationTurn.model_validate(
            _turn(sanitizedImageBlocks=[{"mediaType": "image/svg+xml", "data": _PNG}])
        )


def test_turn_rejects_invalid_base64():
    with pytest.raises(ValueError):
        Gate5B4C3ShadowGenerationTurn.model_validate(
            _turn(sanitizedImageBlocks=[{"mediaType": "image/png", "data": "!!notbase64!!"}])
        )
