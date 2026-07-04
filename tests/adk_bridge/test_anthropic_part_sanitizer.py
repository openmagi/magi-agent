"""Tests for the native-Anthropic part sanitizer wired into the cache-aware mixin.

Root cause (verified against google-adk 1.33.0 + anthropic 0.116.0): ADK's
``part_to_message_block`` raises ``NotImplementedError`` on any ``genai`` part
whose thinking/text/tool predicates are all falsy. Sonnet 5 (adaptive thinking,
enabled server-side by default) emits signature-only and empty-thinking blocks;
combined with ADK's streaming helper silently dropping ``SignatureDelta``, the
persisted history grows fall-through parts and the NEXT model call within the
same turn crashes at the raise, so the turn finalizes with ``text_len=0``.

The fix is a sanitizer (:mod:`magi_agent.adk_bridge.anthropic_part_sanitizer`)
wired into the cache-aware mixin's message-build seam. It runs BEFORE ADK's
``part_to_message_block`` and drops parts that would otherwise raise, emitting a
structured drop-warning instead. Prompt caching (both seams) is untouched.

Layering:

* RED locks: feed each offending shape (A/B/D/``text=""``) through the mixin
  message-build seam. Before the fix these raise; after, they must not.
* Sanitizer-drop: offending parts dropped with warning, convertible siblings
  preserved in order.
* Thinking-disabled strip: thought-bearing parts removed while thinking is off.
* Golden pass-through: non-thinking history is byte-identical to raw
  ``content_to_message_param`` (proves Opus 4.8 / Sonnet 4.6 / Haiku unaffected).
* Empty-message guard: a Content whose parts all drop is skipped (no empty
  ``content: []`` array reaches the wire).
* Cache preservation: sanitized messages still receive rolling-tail markers.

All model-driven cases need the optional ``anthropic`` package; they
``importorskip`` so environments without it stay green.
"""

from __future__ import annotations

import asyncio
import importlib
from typing import Any

import pytest


def _model_module():
    return importlib.import_module("magi_agent.adk_bridge.anthropic_cache_model")


def _sanitizer_module():
    return importlib.import_module("magi_agent.adk_bridge.anthropic_part_sanitizer")


# ---------------------------------------------------------------------------
# Fake Anthropic client (mirrors tests/test_anthropic_cache_model.py so the
# message-build seam runs without a network or credentials).
# ---------------------------------------------------------------------------


class _FakeMessages:
    def __init__(self, recorder: dict) -> None:
        self._recorder = recorder

    async def create(self, **kwargs: Any):
        self._recorder["create_kwargs"] = kwargs
        from anthropic import types as anthropic_types

        return anthropic_types.Message(
            id="msg_fake",
            type="message",
            role="assistant",
            model="claude-test",
            content=[anthropic_types.TextBlock(type="text", text="ok")],
            stop_reason="end_turn",
            stop_sequence=None,
            usage=anthropic_types.Usage(input_tokens=1, output_tokens=1),
        )


class _FakeAnthropicClient:
    def __init__(self, recorder: dict) -> None:
        self.messages = _FakeMessages(recorder)


def _build_llm_request(contents):
    from google.adk.models.llm_request import LlmRequest
    from google.genai import types as genai_types

    config = genai_types.GenerateContentConfig(system_instruction="sys-prompt")
    return LlmRequest(model="claude-sonnet-5", contents=contents, config=config)


def _content(role: str, parts):
    from google.genai import types as genai_types

    return genai_types.Content(role=role, parts=parts)


def _drive_generate(model, llm_request) -> dict:
    recorder: dict = {}
    object.__setattr__(model, "_anthropic_client", _FakeAnthropicClient(recorder))

    async def _run() -> None:
        async for _ in model.generate_content_async(llm_request, stream=False):
            pass

    asyncio.run(_run())
    return recorder["create_kwargs"]


def _fresh_model(monkeypatch):
    # Message cache ON so we can also assert the rolling-tail markers still land.
    monkeypatch.setenv("MAGI_MESSAGE_CACHE_ENABLED", "1")
    cls = _model_module().get_cache_aware_claude_class("claude-sonnet-5")
    return cls(model="claude-sonnet-5")


# ---------------------------------------------------------------------------
# Offending part shapes (verified to raise in ADK 1.33.0 part_to_message_block).
# ---------------------------------------------------------------------------


def _offending_parts():
    from google.genai import types as genai_types

    return {
        "A_signature_only": genai_types.Part(thought_signature=b"sig"),
        "B_empty_thinking_no_sig": genai_types.Part(text="", thought=True),
        "D_fully_empty": genai_types.Part(),
        "plain_empty_text": genai_types.Part(text=""),
    }


# ---------------------------------------------------------------------------
# RED: before the sanitizer these raise through the mixin seam.
# ---------------------------------------------------------------------------


class TestRawAdkRaisesOnOffendingShapes:
    """Pins the repro: raw ADK conversion raises on every offending shape."""

    @pytest.mark.parametrize("shape", list(_offending_parts()))
    def test_raw_part_to_message_block_raises(self, shape: str) -> None:
        pytest.importorskip("anthropic")
        from google.adk.models.anthropic_llm import part_to_message_block

        part = _offending_parts()[shape]
        with pytest.raises(NotImplementedError):
            part_to_message_block(part)


class TestMixinSeamSurvivesOffendingShapes:
    """After the fix, the same shapes flow through the mixin without raising."""

    @pytest.mark.parametrize("shape", list(_offending_parts()))
    def test_mixin_does_not_raise(self, monkeypatch, shape: str) -> None:
        pytest.importorskip("anthropic")
        model = _fresh_model(monkeypatch)
        part = _offending_parts()[shape]
        # Bracket the offending part with a convertible user turn so the request
        # is never fully empty.
        contents = [
            _content("user", [_text_part("hello")]),
            _content("model", [part]),
        ]
        llm_request = _build_llm_request(contents)
        # Must not raise NotImplementedError anymore.
        _drive_generate(model, llm_request)


def _text_part(text: str):
    from google.genai import types as genai_types

    return genai_types.Part.from_text(text=text)


# ---------------------------------------------------------------------------
# Sanitizer drop behavior: offending parts dropped, siblings preserved.
# ---------------------------------------------------------------------------


class TestSanitizerDropAndPreserve:
    def test_offending_part_dropped_convertible_sibling_kept(
        self, monkeypatch
    ) -> None:
        pytest.importorskip("anthropic")
        from google.genai import types as genai_types

        model = _fresh_model(monkeypatch)
        # A model turn with a fall-through thinking part followed by real text.
        contents = [
            _content("user", [_text_part("prompt")]),
            _content(
                "model",
                [
                    genai_types.Part(text="", thought=True),  # Shape B, dropped
                    _text_part("actual answer"),  # kept, in order
                ],
            ),
        ]
        create_kwargs = _drive_generate(model, _build_llm_request(contents))
        messages = create_kwargs["messages"]
        assert len(messages) == 2
        model_msg = messages[1]
        blocks = model_msg["content"]
        text_blocks = [b for b in blocks if b.get("type") == "text"]
        assert len(text_blocks) == 1
        assert text_blocks[0]["text"] == "actual answer"
        # No thinking / redacted_thinking block leaked through.
        assert all(
            b.get("type") not in ("thinking", "redacted_thinking") for b in blocks
        )

    def test_drop_emits_structured_warning(self, monkeypatch, caplog) -> None:
        pytest.importorskip("anthropic")
        model = _fresh_model(monkeypatch)
        contents = [
            _content("user", [_text_part("prompt")]),
            _content("model", [_text_part("answer")]),
            _content("user", [_offending_parts()["A_signature_only"]]),
        ]
        # Bracket with a convertible tail so the offending user Content is not the
        # only message; but that Content's single part drops -> the message is
        # skipped and a warning is emitted.
        contents.append(_content("user", [_text_part("keep going")]))
        with caplog.at_level("WARNING"):
            _drive_generate(model, _build_llm_request(contents))
        warnings = [
            r for r in caplog.records if "sanitiz" in r.getMessage().lower()
            or "dropped" in r.getMessage().lower()
        ]
        assert warnings, "expected a structured drop-warning"


# ---------------------------------------------------------------------------
# Thinking-disabled strip.
# ---------------------------------------------------------------------------


class TestThinkingDisabledStrip:
    def test_reasoning_part_stripped_no_signature_block(self, monkeypatch) -> None:
        pytest.importorskip("anthropic")
        from google.genai import types as genai_types

        model = _fresh_model(monkeypatch)
        contents = [
            _content("user", [_text_part("prompt")]),
            _content(
                "model",
                [
                    genai_types.Part(text="reasoning", thought=True),
                    _text_part("final"),
                ],
            ),
        ]
        create_kwargs = _drive_generate(model, _build_llm_request(contents))
        model_msg = create_kwargs["messages"][1]
        blocks = model_msg["content"]
        # Thinking-disabled -> reasoning part stripped, no thinking block and no
        # signature="" ThinkingBlockParam.
        assert all(b.get("type") == "text" for b in blocks)
        assert not any("signature" in b for b in blocks)
        assert [b["text"] for b in blocks if b["type"] == "text"] == ["final"]


# ---------------------------------------------------------------------------
# Thinking-enabled round-trip (rule 5, faithful when thinking is on).
# ---------------------------------------------------------------------------


class TestThinkingEnabledRoundTrip:
    def test_thinking_part_with_text_and_signature_round_trips(self) -> None:
        pytest.importorskip("anthropic")
        from google.genai import types as genai_types

        sanitize = _sanitizer_module().safe_contents_to_message_params
        contents = [
            _content("user", [_text_part("q")]),
            _content(
                "model",
                [
                    genai_types.Part(
                        text="reasoning",
                        thought=True,
                        thought_signature=b"sig",
                    )
                ],
            ),
        ]
        out = sanitize(contents, thinking_enabled=True)
        # Thinking enabled -> the thought part is preserved and ADK converts it
        # to a thinking block with the signature carried through.
        model_msg = out[1]
        block = model_msg["content"][0]
        assert block["type"] == "thinking"
        assert block["thinking"] == "reasoning"
        assert block["signature"] == "sig"

    def test_signature_only_thought_round_trips_as_redacted(self) -> None:
        pytest.importorskip("anthropic")
        from google.genai import types as genai_types

        sanitize = _sanitizer_module().safe_contents_to_message_params
        contents = [
            _content("user", [_text_part("q")]),
            _content(
                "model",
                [genai_types.Part(thought=True, thought_signature=b"sig")],
            ),
        ]
        out = sanitize(contents, thinking_enabled=True)
        block = out[1]["content"][0]
        assert block["type"] == "redacted_thinking"
        assert block["data"] == "sig"

    def test_signature_less_thought_still_dropped_when_enabled(self) -> None:
        """Even with thinking on, a signature-less empty-thinking part drops.

        It has no signature to preserve and would fall through ADK's raise.
        """
        pytest.importorskip("anthropic")
        from google.genai import types as genai_types

        sanitize = _sanitizer_module().safe_contents_to_message_params
        contents = [
            _content("user", [_text_part("q")]),
            _content(
                "model",
                [
                    genai_types.Part(text="", thought=True),  # drops
                    _text_part("answer"),  # kept
                ],
            ),
        ]
        out = sanitize(contents, thinking_enabled=True)
        blocks = out[1]["content"]
        assert len(blocks) == 1
        assert blocks[0]["type"] == "text"
        assert blocks[0]["text"] == "answer"


# ---------------------------------------------------------------------------
# Golden pass-through: proves Opus 4.8 / Sonnet 4.6 / Haiku byte-identical.
# ---------------------------------------------------------------------------


class TestGoldenPassThrough:
    def test_non_thinking_history_byte_identical(self, monkeypatch) -> None:
        pytest.importorskip("anthropic")
        from google.adk.models.anthropic_llm import content_to_message_param
        from google.genai import types as genai_types

        # A realistic non-thinking history: user text, model text + tool_use,
        # user tool_result.
        tool_call = genai_types.Part.from_function_call(
            name="Glob", args={"pattern": "*.py"}
        )
        tool_call.function_call.id = "call_1"
        tool_result = genai_types.Part.from_function_response(
            name="Glob", response={"result": "a.py\nb.py"}
        )
        tool_result.function_response.id = "call_1"
        contents = [
            _content("user", [_text_part("list files")]),
            _content("model", [_text_part("running"), tool_call]),
            _content("user", [tool_result]),
        ]

        sanitize = _sanitizer_module().safe_contents_to_message_params
        got = sanitize(contents, thinking_enabled=False)
        expected = [content_to_message_param(c) for c in contents]
        assert got == expected


# ---------------------------------------------------------------------------
# Empty-message guard.
# ---------------------------------------------------------------------------


class TestEmptyMessageGuard:
    def test_all_dropped_content_is_skipped(self, monkeypatch) -> None:
        pytest.importorskip("anthropic")
        from google.genai import types as genai_types

        model = _fresh_model(monkeypatch)
        contents = [
            _content("user", [_text_part("prompt")]),
            _content(
                "model",
                [
                    genai_types.Part(text="", thought=True),  # drops
                    genai_types.Part(thought_signature=b"x"),  # drops
                ],
            ),
            _content("user", [_text_part("continue")]),
        ]
        create_kwargs = _drive_generate(model, _build_llm_request(contents))
        messages = create_kwargs["messages"]
        # The all-dropped model Content is skipped entirely: only the two user
        # messages remain, and no message has an empty content array.
        assert len(messages) == 2
        for msg in messages:
            assert msg["content"], "no message may have an empty content array"

    def test_sanitizer_skips_empty_content_directly(self) -> None:
        pytest.importorskip("anthropic")
        from google.genai import types as genai_types

        sanitize = _sanitizer_module().safe_contents_to_message_params
        contents = [
            _content("user", [_text_part("hi")]),
            _content("model", [genai_types.Part()]),  # fully empty -> drop -> skip
        ]
        out = sanitize(contents, thinking_enabled=False)
        assert len(out) == 1
        assert out[0]["role"] == "user"


# ---------------------------------------------------------------------------
# Cache preservation: rolling-tail markers survive sanitation.
# ---------------------------------------------------------------------------


def _count_breakpoints(messages: list[dict]) -> int:
    total = 0
    for message in messages:
        content = message.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("cache_control"):
                    total += 1
    return total


class TestCachePreservationAfterSanitation:
    def test_sanitized_messages_still_get_tail_markers(self, monkeypatch) -> None:
        pytest.importorskip("anthropic")
        from google.genai import types as genai_types

        model = _fresh_model(monkeypatch)
        contents = [
            _content("user", [_text_part("m0")]),
            _content(
                "model",
                [genai_types.Part(text="", thought=True), _text_part("m1")],
            ),
            _content("user", [_text_part("m2")]),
        ]
        create_kwargs = _drive_generate(model, _build_llm_request(contents))
        messages = create_kwargs["messages"]
        # Sanitation kept all three messages (the model msg still has "m1");
        # the rolling tail marks the last two.
        assert len(messages) == 3
        assert _count_breakpoints(messages) == 2
        last_block = messages[-1]["content"][-1]
        assert last_block["cache_control"] == {"type": "ephemeral"}
