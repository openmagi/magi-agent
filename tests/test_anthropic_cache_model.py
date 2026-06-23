"""Tests for the cache-aware ADK Anthropic model — PR11 (genuine injection).

These tests prove the rolling-tail ``cache_control`` marker reaches the
*outgoing Anthropic request* at build time, not just the pure injector helper.

Two layers:

1. Pure marker logic (``inject_message_tail_cache_control``) — deterministic,
   no ``anthropic`` package required.
2. Request-level injection via the ``CacheAwareClaude`` subclass driving a
   FAKE anthropic client (no network, no API key). Skipped only if the
   optional ``anthropic`` package isn't importable in the test env.
3. Routing seam — a Claude model id selects ``build_cache_aware_claude`` in
   ``gate5b4c3_live_runner_boundary`` (no anthropic import needed; the builder
   is monkeypatched).
"""

from __future__ import annotations

import asyncio
import importlib
from types import SimpleNamespace
from typing import Any

import pytest


def _model_module():
    return importlib.import_module("magi_agent.adk_bridge.anthropic_cache_model")


def _msg(role: str, text: str) -> dict:
    return {"role": role, "content": [{"type": "text", "text": text}]}


def _count_breakpoints(messages: list[dict]) -> int:
    total = 0
    for message in messages:
        content = message.get("content")
        if isinstance(content, list):
            total += sum(
                1
                for block in content
                if isinstance(block, dict) and "cache_control" in block
            )
    return total


def _has_cache_control(message: dict) -> bool:
    content = message.get("content")
    if isinstance(content, list):
        return any(
            isinstance(block, dict) and "cache_control" in block for block in content
        )
    return False


# ---------------------------------------------------------------------------
# Pure marker logic — no anthropic package required
# ---------------------------------------------------------------------------


class TestInjectMessageTailCacheControl:
    def test_marks_last_two_non_system_messages(self) -> None:
        inject = _model_module().inject_message_tail_cache_control
        messages = [
            _msg("user", "first"),
            _msg("assistant", "second"),
            _msg("user", "third"),
            _msg("assistant", "fourth"),
        ]
        marked = inject(messages)
        assert not _has_cache_control(marked[0])
        assert not _has_cache_control(marked[1])
        assert _has_cache_control(marked[2])
        assert _has_cache_control(marked[3])

    def test_marker_is_ephemeral(self) -> None:
        inject = _model_module().inject_message_tail_cache_control
        marked = inject([_msg("user", "only")])
        assert marked[-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}

    def test_does_not_mark_system_messages(self) -> None:
        inject = _model_module().inject_message_tail_cache_control
        messages = [
            {"role": "system", "content": [{"type": "text", "text": "sys"}]},
            _msg("user", "hi"),
            _msg("assistant", "yo"),
        ]
        marked = inject(messages)
        assert not _has_cache_control(marked[0])
        assert _has_cache_control(marked[1])
        assert _has_cache_control(marked[2])

    def test_never_exceeds_two_breakpoints(self) -> None:
        inject = _model_module().inject_message_tail_cache_control
        messages = [_msg("user" if i % 2 == 0 else "assistant", str(i)) for i in range(10)]
        assert _count_breakpoints(inject(messages, tail_size=10)) <= 2

    def test_does_not_mutate_input(self) -> None:
        inject = _model_module().inject_message_tail_cache_control
        messages = [_msg("user", "a"), _msg("assistant", "b")]
        inject(messages)
        assert not _has_cache_control(messages[0])
        assert not _has_cache_control(messages[1])

    def test_string_content_message_is_marked(self) -> None:
        inject = _model_module().inject_message_tail_cache_control
        marked = inject([{"role": "user", "content": "plain string"}])
        assert _has_cache_control(marked[-1])

    def test_empty_returns_empty(self) -> None:
        inject = _model_module().inject_message_tail_cache_control
        assert inject([]) == []


# ---------------------------------------------------------------------------
# Request-level injection via CacheAwareClaude + FAKE anthropic client
# ---------------------------------------------------------------------------


class _FakeStream:
    """Minimal async-iterable standing in for the anthropic streaming response.

    ADK's ``_generate_content_streaming`` re-uses the ``messages`` argument it is
    handed (it does NOT re-derive them from the ``llm_request``), so the
    injected ``messages`` necessarily reach the streaming ``create`` call —
    which is exactly what the streaming test asserts via the captured kwargs.

    We yield one ``message_start`` + one text delta so the helper produces a
    non-empty aggregated response; the event objects are constructed via the
    ``anthropic`` SDK to stay valid for the pinned version.
    """

    def __init__(self) -> None:
        from anthropic import types as anthropic_types

        message = anthropic_types.Message(
            id="msg_fake",
            type="message",
            role="assistant",
            model="claude-test",
            content=[],
            stop_reason="end_turn",
            stop_sequence=None,
            usage=anthropic_types.Usage(input_tokens=1, output_tokens=1),
        )
        self._events = [
            anthropic_types.RawMessageStartEvent(
                type="message_start", message=message
            ),
            anthropic_types.RawContentBlockStartEvent(
                type="content_block_start",
                index=0,
                content_block=anthropic_types.TextBlock(
                    type="text", text="", citations=None
                ),
            ),
            anthropic_types.RawContentBlockDeltaEvent(
                type="content_block_delta",
                index=0,
                delta=anthropic_types.TextDelta(type="text_delta", text="ok"),
            ),
        ]

    def __aiter__(self):
        async def _gen():
            for event in self._events:
                yield event

        return _gen()


class _FakeMessages:
    def __init__(self, recorder: dict) -> None:
        self._recorder = recorder

    async def create(self, **kwargs: Any):
        # Capture exactly what would be sent to the Anthropic Messages API.
        self._recorder["create_kwargs"] = kwargs
        if kwargs.get("stream"):
            return _FakeStream()
        # Build a minimal anthropic Message-shaped response.
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
    """Stand-in for ``AsyncAnthropic`` / ``AsyncAnthropicVertex``.

    Tests override ``_anthropic_client`` (an ADK ``cached_property``) with this
    fake so NO real credential path is touched: the direct base would otherwise
    construct ``AsyncAnthropic()`` (``ANTHROPIC_API_KEY``) and the Vertex base
    ``AsyncAnthropicVertex`` (``GOOGLE_CLOUD_PROJECT``/``GOOGLE_CLOUD_LOCATION``).
    By injecting the fake we exercise the message-building/injection logic
    without network or env credentials. The base-class *selection* (which client
    WOULD be used) is asserted separately in ``TestBaseClassSelection``.
    """

    def __init__(self, recorder: dict) -> None:
        self.messages = _FakeMessages(recorder)


def _build_llm_request(texts: list[str]):
    """Build a real ADK LlmRequest with one genai Content per text."""
    from google.adk.models.llm_request import LlmRequest
    from google.genai import types as genai_types

    contents = [
        genai_types.Content(role="user", parts=[genai_types.Part.from_text(text=text)])
        for text in texts
    ]
    config = genai_types.GenerateContentConfig(system_instruction="sys-prompt")
    return LlmRequest(model="claude-sonnet-4-6", contents=contents, config=config)


def _run_generate(model, llm_request) -> dict:
    recorder: dict = {}
    fake_client = _FakeAnthropicClient(recorder)
    # Inject the fake client over the cached_property.
    object.__setattr__(model, "_anthropic_client", fake_client)

    async def _drive() -> None:
        async for _ in model.generate_content_async(llm_request, stream=False):
            pass

    asyncio.run(_drive())
    return recorder["create_kwargs"]


class TestRequestLevelInjection:
    def test_flag_on_marks_last_two_messages_in_request(self, monkeypatch) -> None:
        pytest.importorskip("anthropic")
        monkeypatch.setenv("MAGI_MESSAGE_CACHE_ENABLED", "1")
        cls = _model_module().get_cache_aware_claude_class()
        model = cls(model="claude-sonnet-4-6")
        llm_request = _build_llm_request(["m0", "m1", "m2", "m3"])

        create_kwargs = _run_generate(model, llm_request)
        messages = create_kwargs["messages"]

        assert len(messages) == 4
        assert not _has_cache_control(messages[0])
        assert not _has_cache_control(messages[1])
        assert _has_cache_control(messages[2])
        assert _has_cache_control(messages[3])
        assert _count_breakpoints(messages) == 2

    def test_marker_is_ephemeral_in_request(self, monkeypatch) -> None:
        pytest.importorskip("anthropic")
        monkeypatch.setenv("MAGI_MESSAGE_CACHE_ENABLED", "1")
        cls = _model_module().get_cache_aware_claude_class()
        model = cls(model="claude-sonnet-4-6")
        llm_request = _build_llm_request(["only"])

        create_kwargs = _run_generate(model, llm_request)
        block = create_kwargs["messages"][-1]["content"][-1]
        assert block["cache_control"] == {"type": "ephemeral"}

    def test_flag_off_produces_no_cache_control(self, monkeypatch) -> None:
        pytest.importorskip("anthropic")
        monkeypatch.setenv("MAGI_MESSAGE_CACHE_ENABLED", "0")
        cls = _model_module().get_cache_aware_claude_class()
        model = cls(model="claude-sonnet-4-6")
        llm_request = _build_llm_request(["m0", "m1", "m2"])

        create_kwargs = _run_generate(model, llm_request)
        assert _count_breakpoints(create_kwargs["messages"]) == 0

    def test_flag_off_matches_default_adk_messages(self, monkeypatch) -> None:
        """OFF ⇒ request messages identical to the parent Claude conversion."""
        pytest.importorskip("anthropic")
        monkeypatch.setenv("MAGI_MESSAGE_CACHE_ENABLED", "0")
        from google.adk.models.anthropic_llm import content_to_message_param

        cls = _model_module().get_cache_aware_claude_class()
        model = cls(model="claude-sonnet-4-6")
        llm_request = _build_llm_request(["m0", "m1"])

        create_kwargs = _run_generate(model, llm_request)
        expected = [content_to_message_param(c) for c in llm_request.contents]
        assert create_kwargs["messages"] == expected

    def test_real_class_exposes_cache_aware_marker(self, monkeypatch) -> None:
        """The REAL (non-mocked) cache-aware class carries the public marker."""
        pytest.importorskip("anthropic")
        cls = _model_module().get_cache_aware_claude_class("claude-sonnet-4-6")
        assert cls.magi_message_cache_aware is True
        # And the constructed instance inherits it.
        assert cls(model="claude-sonnet-4-6").magi_message_cache_aware is True


# ---------------------------------------------------------------------------
# Streaming path — injection must reach the stream=True branch
# ---------------------------------------------------------------------------


class TestStreamingInjection:
    def test_stream_on_marks_messages_in_streaming_request(self, monkeypatch) -> None:
        """stream=True still injects the rolling-tail markers into the request.

        ADK's ``_generate_content_streaming`` receives the ``messages`` list our
        override built (and injected), so the marker reaches the streaming
        create call. We drive the full async generator against a fake streaming
        client and assert on the captured create kwargs.
        """
        pytest.importorskip("anthropic")
        monkeypatch.setenv("MAGI_MESSAGE_CACHE_ENABLED", "1")
        cls = _model_module().get_cache_aware_claude_class("claude-sonnet-4-6")
        model = cls(model="claude-sonnet-4-6")
        llm_request = _build_llm_request(["m0", "m1", "m2", "m3"])

        recorder: dict = {}
        fake_client = _FakeAnthropicClient(recorder)
        object.__setattr__(model, "_anthropic_client", fake_client)

        async def _drive() -> None:
            async for _ in model.generate_content_async(llm_request, stream=True):
                pass

        asyncio.run(_drive())
        create_kwargs = recorder["create_kwargs"]
        assert create_kwargs["stream"] is True
        messages = create_kwargs["messages"]
        assert len(messages) == 4
        assert not _has_cache_control(messages[0])
        assert not _has_cache_control(messages[1])
        assert _has_cache_control(messages[2])
        assert _has_cache_control(messages[3])
        assert _count_breakpoints(messages) == 2

    def test_stream_off_produces_no_cache_control(self, monkeypatch) -> None:
        pytest.importorskip("anthropic")
        monkeypatch.setenv("MAGI_MESSAGE_CACHE_ENABLED", "0")
        cls = _model_module().get_cache_aware_claude_class("claude-sonnet-4-6")
        model = cls(model="claude-sonnet-4-6")
        llm_request = _build_llm_request(["m0", "m1", "m2"])

        recorder: dict = {}
        object.__setattr__(model, "_anthropic_client", _FakeAnthropicClient(recorder))

        async def _drive() -> None:
            async for _ in model.generate_content_async(llm_request, stream=True):
                pass

        asyncio.run(_drive())
        assert recorder["create_kwargs"]["stream"] is True
        assert _count_breakpoints(recorder["create_kwargs"]["messages"]) == 0


# ---------------------------------------------------------------------------
# Base-class selection — direct Anthropic (default) vs Vertex
# ---------------------------------------------------------------------------


class TestBaseClassSelection:
    """ADK's LLMRegistry resolves ``claude-*`` to the Vertex ``Claude`` whose
    ``_anthropic_client`` is ``AsyncAnthropicVertex`` (needs GOOGLE_CLOUD_*).
    Magi deploys against the DIRECT Anthropic API (``GOOGLE_GENAI_USE_VERTEXAI``
    is false in the Gate 5B live-smoke config), so the cache-aware factory must
    default to the direct ``AnthropicLlm`` base unless a Vertex signal is
    present — otherwise the inherited client is wrong and fails at first call.
    """

    def test_default_base_is_direct_anthropic(self, monkeypatch) -> None:
        pytest.importorskip("anthropic")
        from google.adk.models.anthropic_llm import AnthropicLlm, Claude

        monkeypatch.delenv("GOOGLE_GENAI_USE_VERTEXAI", raising=False)
        monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
        monkeypatch.delenv("GOOGLE_CLOUD_LOCATION", raising=False)

        cls = _model_module().get_cache_aware_claude_class("claude-sonnet-4-6")
        assert issubclass(cls, AnthropicLlm)
        # Direct base ⇒ NOT the Vertex subclass.
        assert not issubclass(cls, Claude)

    def test_vertex_base_chosen_for_vertex_resource_model_id(self, monkeypatch) -> None:
        pytest.importorskip("anthropic")
        from google.adk.models.anthropic_llm import Claude

        monkeypatch.delenv("GOOGLE_GENAI_USE_VERTEXAI", raising=False)
        model_id = (
            "projects/p/locations/us-east5/publishers/anthropic/models/claude-x"
        )
        cls = _model_module().get_cache_aware_claude_class(model_id)
        assert issubclass(cls, Claude)

    def test_vertex_base_chosen_when_use_vertexai_truthy(self, monkeypatch) -> None:
        pytest.importorskip("anthropic")
        from google.adk.models.anthropic_llm import Claude

        monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
        cls = _model_module().get_cache_aware_claude_class("claude-sonnet-4-6")
        assert issubclass(cls, Claude)

    def test_vertex_base_chosen_when_project_and_location_set(self, monkeypatch) -> None:
        pytest.importorskip("anthropic")
        from google.adk.models.anthropic_llm import Claude

        monkeypatch.delenv("GOOGLE_GENAI_USE_VERTEXAI", raising=False)
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "proj")
        monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-east5")
        cls = _model_module().get_cache_aware_claude_class("claude-sonnet-4-6")
        assert issubclass(cls, Claude)

    def test_direct_base_when_only_project_set(self, monkeypatch) -> None:
        pytest.importorskip("anthropic")
        from google.adk.models.anthropic_llm import AnthropicLlm, Claude

        monkeypatch.delenv("GOOGLE_GENAI_USE_VERTEXAI", raising=False)
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "proj")
        monkeypatch.delenv("GOOGLE_CLOUD_LOCATION", raising=False)
        cls = _model_module().get_cache_aware_claude_class("claude-sonnet-4-6")
        assert issubclass(cls, AnthropicLlm) and not issubclass(cls, Claude)


# ---------------------------------------------------------------------------
# Routing seam — Claude model id selects the cache-aware subclass
# ---------------------------------------------------------------------------


class TestRoutingSeam:
    def test_is_anthropic_route_detection(self) -> None:
        boundary = importlib.import_module(
            "magi_agent.shadow.gate5b4c3_live_runner_boundary"
        )
        assert boundary._is_anthropic_route("anthropic", "")
        assert boundary._is_anthropic_route("", "claude-sonnet-4-6")
        assert boundary._is_anthropic_route("", "claude-3-5-haiku")
        assert boundary._is_anthropic_route("", "anthropic/claude-x")
        assert not boundary._is_anthropic_route("google", "gemini-3.5-flash")
        assert not boundary._is_anthropic_route("openai", "gpt-5")

    def test_claude_model_routes_to_cache_aware_builder(self, monkeypatch) -> None:
        # E-7: the shadow boundary delegates to ``runtime/model_factory``
        # which imports ``build_cache_aware_claude`` at module scope —
        # patch THAT bound reference (the legacy ``cache_model.*`` patch
        # doesn't reach the factory's call site).
        import magi_agent.runtime.model_factory as model_factory

        sentinel = SimpleNamespace(magi_message_cache_aware=True, model="claude-sonnet-4-6")
        captured: dict = {}

        def _fake_build(model: str):
            captured["model"] = model
            return sentinel

        monkeypatch.setattr(model_factory, "build_cache_aware_claude", _fake_build)

        boundary = importlib.import_module(
            "magi_agent.shadow.gate5b4c3_live_runner_boundary"
        )
        result = boundary._gate1a_correlated_model_or_label(
            "anthropic", "claude-sonnet-4-6", None, None
        )
        assert result is sentinel
        assert captured["model"] == "claude-sonnet-4-6"

    def test_missing_optional_anthropic_dependency_falls_back_to_label(
        self, monkeypatch
    ) -> None:
        # E-7: patch the factory's bound reference (see comment above).
        import magi_agent.runtime.model_factory as model_factory

        def _missing_build(model: str):
            raise ModuleNotFoundError("No module named 'anthropic'", name="anthropic")

        monkeypatch.setattr(model_factory, "build_cache_aware_claude", _missing_build)

        boundary = importlib.import_module(
            "magi_agent.shadow.gate5b4c3_live_runner_boundary"
        )
        result = boundary._gate1a_correlated_model_or_label(
            "anthropic", "claude-sonnet-4-6", None, None
        )
        assert result == "claude-sonnet-4-6"

    def test_gemini_route_unchanged(self) -> None:
        boundary = importlib.import_module(
            "magi_agent.shadow.gate5b4c3_live_runner_boundary"
        )
        # No correlation context ⇒ falls through to the bare label string.
        result = boundary._gate1a_correlated_model_or_label(
            "google", "gemini-3.5-flash", None, None
        )
        assert result == "gemini-3.5-flash"
