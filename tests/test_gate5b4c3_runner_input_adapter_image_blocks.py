from __future__ import annotations

import base64

from magi_agent.shadow.gate5b4c3_runner_input_adapter import (
    Gate5B4C3RunnerInput,
    build_gate5b4c3_runner_input,
)
from tests.support.gate5b4c3_factories import make_shadow_generation_request

_PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n").decode("ascii")


def test_runner_input_carries_image_blocks() -> None:
    request = make_shadow_generation_request(
        sanitized_current_turn_text="describe this image",
        sanitized_image_blocks=[{"mediaType": "image/png", "data": _PNG}],
    )
    result = build_gate5b4c3_runner_input(request)
    assert result.status == "accepted"
    assert result.runner_input is not None
    assert len(result.runner_input.sanitized_image_blocks) == 1
    assert result.runner_input.sanitized_image_blocks[0].media_type == "image/png"


def test_runner_input_defaults_to_no_image_blocks() -> None:
    request = make_shadow_generation_request(sanitized_current_turn_text="hi")
    result = build_gate5b4c3_runner_input(request)
    assert result.status == "accepted"
    assert result.runner_input is not None
    assert result.runner_input.sanitized_image_blocks == ()
