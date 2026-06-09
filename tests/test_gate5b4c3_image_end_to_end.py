from __future__ import annotations

import base64
from types import SimpleNamespace

from magi_agent.shadow.gate5b4c3_live_runner_boundary import _build_user_message_parts
from magi_agent.shadow.gate5b4c3_runner_input_adapter import (
    Gate5B4C3RunnerInput,
    build_gate5b4c3_runner_input,
)
from tests.support.gate5b4c3_factories import make_shadow_generation_request

_PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n").decode("ascii")


def _fake_primitives():
    part = SimpleNamespace(
        from_text=lambda *, text: ("text", text),
        from_bytes=lambda *, data, mime_type: ("image", mime_type, data),
    )
    return SimpleNamespace(Part=part)


def _runner_input_from(result):
    # Gate5B4C3RunnerInputAdapterResult.runner_input is already a
    # Gate5B4C3RunnerInput instance — return it directly.
    # (model_validate on a frozen model with extra="forbid" fails when
    # Pydantic serialises the instance using Python field names rather
    # than aliases, producing "extra inputs are not permitted" errors.)
    runner_input = result.runner_input
    assert isinstance(runner_input, Gate5B4C3RunnerInput)
    return runner_input


def test_image_flows_request_to_opening_parts():
    request = make_shadow_generation_request(
        sanitized_current_turn_text="describe this image",
        sanitized_image_blocks=[{"mediaType": "image/png", "data": _PNG}],
    )
    result = build_gate5b4c3_runner_input(request)
    assert result.status == "accepted"
    runner_input = _runner_input_from(result)
    parts = _build_user_message_parts(runner_input, primitives=_fake_primitives())
    assert ("image", "image/png", b"\x89PNG\r\n\x1a\n") in parts


def test_text_only_request_yields_single_text_part():
    request = make_shadow_generation_request(sanitized_current_turn_text="just text")
    result = build_gate5b4c3_runner_input(request)
    assert result.status == "accepted"
    runner_input = _runner_input_from(result)
    parts = _build_user_message_parts(runner_input, primitives=_fake_primitives())
    # No history → _runner_message_text returns the bare input string unchanged.
    # Exactly one text part with the verbatim user input is expected.
    assert parts == [("text", "just text")]
