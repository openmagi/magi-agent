from __future__ import annotations

import base64

import pytest

from magi_agent.shadow.gate5b4c3_shadow_generation_contract import (
    Gate5B4C3ShadowGenerationImageBlock,
    Gate5B4C3ShadowGenerationTurn,
    _reject_unsafe_value,
)

_PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n").decode("ascii")

# A base64 data string that itself matches the AIza... unsafe-token pattern.
# 'AIza' + 'A'*40 is valid base64 (all chars are in the b64 alphabet) but
# triggers _UNSAFE_TEXT_RE. Before the fix, this causes a false rejection.
_UNSAFE_LOOKING_B64 = "AIza" + "A" * 40


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


# ---------------------------------------------------------------------------
# Critical bug fix: base64 image data must be exempt from the unsafe-text scan
# ---------------------------------------------------------------------------


def test_image_block_with_unsafe_looking_b64_data_validates_at_block_level():
    """An image whose base64 data matches a token-like pattern must not be rejected.

    Before the fix, _reject_unsafe_value fires on the data string itself and
    raises 'forbidden private material' even though the payload is legitimate.
    """
    # Confirm the raw data string really does trigger the scanner (proves the
    # test is meaningful: without the exemption it would fail).
    with pytest.raises(ValueError, match="forbidden private material"):
        _reject_unsafe_value(_UNSAFE_LOOKING_B64)

    # After the fix, wrapping the same string in an image-block mapping skips
    # the data scan and validates successfully.
    block = Gate5B4C3ShadowGenerationImageBlock.model_validate(
        {"mediaType": "image/png", "data": _UNSAFE_LOOKING_B64}
    )
    assert block.data == _UNSAFE_LOOKING_B64


def test_image_block_with_unsafe_looking_b64_data_validates_nested_in_turn():
    """The exemption propagates when the block is nested inside a Turn (proves
    that the parent-level _reject_private_material recursion is also exempted).
    """
    turn = Gate5B4C3ShadowGenerationTurn.model_validate(
        _turn(
            sanitizedImageBlocks=[
                {"mediaType": "image/jpeg", "data": _UNSAFE_LOOKING_B64}
            ]
        )
    )
    assert len(turn.sanitized_image_blocks) == 1
    assert turn.sanitized_image_blocks[0].data == _UNSAFE_LOOKING_B64


def test_unsafe_string_in_non_image_field_is_still_rejected():
    """The exemption must NOT weaken the scanner for ordinary text fields.

    A turn whose sanitizedCurrentTurnText contains an unsafe token-like string
    must still raise ValueError.
    """
    unsafe_text = "AIzaSy" + "B" * 30  # matches AIza[A-Za-z0-9_-]{20,}
    with pytest.raises(ValueError):
        Gate5B4C3ShadowGenerationTurn.model_validate(
            _turn(sanitizedCurrentTurnText=unsafe_text)
        )


def test_non_image_media_type_mapping_data_field_is_still_scanned():
    """A mapping with a non-supported mediaType is NOT treated as an image block.

    Its 'data' field must still be scanned — the exemption is narrowly keyed
    on a supported image mediaType value.
    """
    # text/plain is not a supported image media type → data is scanned → raises
    with pytest.raises(ValueError, match="forbidden private material"):
        _reject_unsafe_value(
            {"mediaType": "text/plain", "data": "AIza" + "A" * 40}
        )

    # image/png IS supported → data is exempt → no raise
    _reject_unsafe_value(
        {"mediaType": "image/png", "data": _UNSAFE_LOOKING_B64}
    )


# ---------------------------------------------------------------------------
# Minor validations: empty bytes and >5 MiB
# ---------------------------------------------------------------------------


def test_image_block_rejects_empty_data():
    with pytest.raises(ValueError):
        Gate5B4C3ShadowGenerationImageBlock.model_validate(
            {"mediaType": "image/png", "data": ""}
        )


def test_image_block_rejects_oversized_data():
    oversized = base64.b64encode(b"\x00" * (5 * 1024 * 1024 + 1)).decode()
    with pytest.raises(ValueError):
        Gate5B4C3ShadowGenerationImageBlock.model_validate(
            {"mediaType": "image/png", "data": oversized}
        )


# ---------------------------------------------------------------------------
# M1 — media_type normalization to lowercase
# ---------------------------------------------------------------------------


def test_image_block_normalizes_media_type_to_lowercase():
    """A block built with mixed-case mediaType must store the lowercase form."""
    block = Gate5B4C3ShadowGenerationImageBlock.model_validate(
        {"mediaType": "Image/PNG", "data": _PNG}
    )
    assert block.media_type == "image/png"


def test_image_block_already_lowercase_media_type_unchanged():
    """Canonical lowercase mediaType is accepted and stored without change."""
    block = Gate5B4C3ShadowGenerationImageBlock.model_validate(
        {"mediaType": "image/jpeg", "data": _PNG}
    )
    assert block.media_type == "image/jpeg"
