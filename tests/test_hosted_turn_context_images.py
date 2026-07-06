"""U5 (B1): image inputs threaded through TurnContext on the governed path.

On the flag-ON governed hosted path, image inputs were silently dropped.
The legacy boundary threads ``sanitized_image_blocks`` into ``Part.from_bytes``
image parts, but the governed path built its message from ``ctx.prompt`` (text
only). ``TurnContext`` had no image field, so ``to_turn_input`` emitted none.

This test file covers three tiers:

(a) mapper unit -- ``hosted_request_to_turn_context`` with one image block in
    the generation yields ``ctx.image_blocks`` as a 1-tuple in the exact
    converter-dict shape that ``image_blocks_to_parts`` expects; a generation
    with no image blocks yields an empty tuple.

(b) ``to_turn_input`` shape neutrality -- with ``image_blocks`` present the key
    is emitted in the turn-input dict; with an empty tuple the key is ABSENT
    (preserves the byte-identical shape invariant documented in the method).

(c) end-to-end-ish -- drive the governed path via a capturing fake LLM and
    assert the opening ``Content.parts`` contains one text part followed by one
    inline-data image part, mirroring the assertions in
    ``test_gate5b4c3_live_runner_boundary_image_parts.py`` for the legacy path.
"""
from __future__ import annotations

import asyncio
import base64

import pytest
from google.adk.models import BaseLlm, LlmResponse
from google.genai import types

from magi_agent.runtime.turn_context import TurnContext
from magi_agent.transport.hosted_turn_context import hosted_request_to_turn_context
from tests.support.gate5b4c3_factories import make_shadow_generation_request

# Minimal valid PNG header used as the canonical test image across all tiers.
_PNG_BYTES = b"\x89PNG\r\n\x1a\n"
_PNG_B64 = base64.b64encode(_PNG_BYTES).decode("ascii")

# Expected converter-dict shape for one PNG block.
_EXPECTED_BLOCK: dict[str, object] = {
    "type": "image",
    "source": {
        "type": "base64",
        "media_type": "image/png",
        "data": _PNG_B64,
    },
}


# ---------------------------------------------------------------------------
# (a) mapper unit
# ---------------------------------------------------------------------------


def test_mapper_one_image_block_yields_converter_dict_in_image_blocks() -> None:
    """A generation carrying one sanitized image block maps to ctx.image_blocks
    as a 1-tuple in the exact converter-dict shape image_blocks_to_parts needs."""
    generation = make_shadow_generation_request(
        sanitized_current_turn_text="describe this image",
        sanitized_image_blocks=[{"mediaType": "image/png", "data": _PNG_B64}],
    )
    ctx = hosted_request_to_turn_context(generation)

    assert len(ctx.image_blocks) == 1, f"expected 1 block, got {len(ctx.image_blocks)}"
    block = ctx.image_blocks[0]
    assert block["type"] == "image"
    source = block["source"]
    assert source["type"] == "base64"
    assert source["media_type"] == "image/png"
    assert source["data"] == _PNG_B64


def test_mapper_no_image_blocks_yields_empty_tuple() -> None:
    """A generation without sanitized image blocks maps to an empty tuple."""
    generation = make_shadow_generation_request(
        sanitized_current_turn_text="just text",
    )
    ctx = hosted_request_to_turn_context(generation)
    assert ctx.image_blocks == ()


# ---------------------------------------------------------------------------
# (b) to_turn_input shape neutrality
# ---------------------------------------------------------------------------


def test_to_turn_input_includes_image_blocks_key_when_non_empty() -> None:
    """TurnContext with image_blocks -> turn_input["image_blocks"] is present."""
    ctx = TurnContext(
        prompt="look",
        session_id="s1",
        turn_id="t1",
        image_blocks=(_EXPECTED_BLOCK,),
    )
    ti = ctx.to_turn_input()
    assert "image_blocks" in ti, "image_blocks key must be present when non-empty"
    assert ti["image_blocks"] == [_EXPECTED_BLOCK]


def test_to_turn_input_omits_image_blocks_key_when_empty() -> None:
    """TurnContext with no image_blocks -> turn_input must NOT contain the key.

    This preserves the byte-identical shape invariant: a fresh-session turn
    dict stays identical to the pre-U5 shape so downstream readers that use
    ``dict.get("image_blocks", ())`` or ``getattr`` fallback are unaffected.
    """
    ctx = TurnContext(prompt="look", session_id="s1", turn_id="t1")
    ti = ctx.to_turn_input()
    assert "image_blocks" not in ti, "image_blocks key must be absent when empty"


def test_to_turn_input_image_blocks_list_matches_tuple_contents() -> None:
    """The emitted list must mirror the tuple contents element-by-element."""
    block_a = dict(_EXPECTED_BLOCK)
    block_b = {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/jpeg", "data": _PNG_B64},
    }
    ctx = TurnContext(
        prompt="multi",
        session_id="s2",
        turn_id="t2",
        image_blocks=(block_a, block_b),
    )
    ti = ctx.to_turn_input()
    assert ti["image_blocks"] == [block_a, block_b]


# ---------------------------------------------------------------------------
# (c) end-to-end-ish: governed path emits image part in opening Content
# ---------------------------------------------------------------------------


class _CapturingLlm(BaseLlm):
    """Records each call's request contents; returns a canned model reply."""

    def __init__(self, sink: list) -> None:
        super().__init__(model="fake")
        self._sink = sink

    async def generate_content_async(  # noqa: ANN201
        self,
        llm_request: object,
        stream: bool = False,
    ):
        contents = list(getattr(llm_request, "contents", None) or [])
        self._sink.append(contents)
        yield LlmResponse(
            content=types.Content(role="model", parts=[types.Part(text="ok")])
        )


def _drive_governed(image_blocks: tuple) -> list:
    """Drive one governed turn and return the captured LLM request contents."""
    from google.adk import sessions as adk_sessions

    from magi_agent.adk_bridge.wire_profile import HOSTED_PROFILE
    from magi_agent.engine.driver import MagiEngineDriver
    from magi_agent.runtime.governed_turn import run_governed_turn
    from magi_agent.runtime.hosted_runtime import (
        GATE5B_SHADOW_APP_NAME,
        GATE5B_SHADOW_USER_ID,
        HostedRuntime,
        _HOSTED_NOOP_GATE,
    )
    from magi_agent.shadow.gate5b4c3_live_runner_boundary import (
        load_gate5b4c3_live_adk_primitives,
    )

    sink: list = []
    svc = adk_sessions.InMemorySessionService()
    primitives = load_gate5b4c3_live_adk_primitives()
    model = _CapturingLlm(sink)
    agent = primitives.Agent(
        name="openmagi_gate5b4c3_shadow_generation_agent",
        description="test hosted image agent",
        model=model,
        instruction="you are a test agent",
        tools=[],
        generate_content_config=primitives.GenerateContentConfig(),
    )
    runner = primitives.Runner(
        app_name=GATE5B_SHADOW_APP_NAME,
        agent=agent,
        session_service=svc,
        auto_create_session=True,
    )
    engine = MagiEngineDriver(
        runner=runner,
        wire_profile=HOSTED_PROFILE,
        user_id=GATE5B_SHADOW_USER_ID,
    )
    runtime = HostedRuntime(engine=engine, gate=_HOSTED_NOOP_GATE)

    ctx = TurnContext(
        prompt="describe this image",
        session_id="u5-img-test-session",
        turn_id="t-img-1",
        image_blocks=image_blocks,
    )

    async def run() -> None:
        async for _ in run_governed_turn(ctx, runtime=runtime):
            pass

    asyncio.run(run())
    return sink


def test_governed_turn_opening_parts_include_image_part() -> None:
    """With image_blocks threaded through TurnContext, the opening Content.parts
    must include a text part followed by an inline-data image part, mirroring
    what the legacy _build_user_message_parts produces."""
    captured = _drive_governed((_EXPECTED_BLOCK,))
    assert captured, "LLM was never called -- no captures recorded"

    opening_contents = captured[0]
    assert opening_contents, "no Content objects in first LLM call"
    user_content = opening_contents[0]
    parts = list(user_content.parts)
    assert len(parts) >= 2, (
        f"Expected at least 2 parts (text + image), got {len(parts)}: {parts}"
    )

    # First part: text with the prompt.
    assert parts[0].text == "describe this image"

    # Second part: inline-data image.
    img_part = parts[1]
    assert img_part.inline_data is not None, "second part must be an inline-data image"
    assert img_part.inline_data.mime_type == "image/png"
    assert img_part.inline_data.data == _PNG_BYTES


def test_governed_turn_text_only_opening_has_no_image_parts() -> None:
    """Without image_blocks, the opening Content must contain no inline-data parts."""
    captured = _drive_governed(())
    assert captured, "LLM was never called -- no captures recorded"

    opening_contents = captured[0]
    user_content = opening_contents[0]
    parts = list(user_content.parts)
    image_parts = [p for p in parts if getattr(p, "inline_data", None)]
    assert not image_parts, (
        f"Expected no image parts for text-only turn, got {image_parts}"
    )
