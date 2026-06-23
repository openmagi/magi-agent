"""ADK's ``LiteLlm.generate_content_async`` silently DROPS the provider
finish_reason when the completion produces zero text + zero tool calls
(``google/adk/models/lite_llm.py:2418-2445`` only finalizes a text
response when ``(text or reasoning_parts)`` is truthy). The downstream
``Runner.run_async`` then sees zero events — which is exactly the 100ms
anthropic / 83ms gemini empty-summary symptom Kevin chased for days.

PRs #854 + #876 close the silent path AFTER ADK by detecting "child ran
with nothing to show" and surfacing as ``child_llm_empty_response``.
That stops the chaos but the WHY is still buried (provider returned
``content_filter`` / unknown_model_id / 0-token completion / ...).

This observer wraps the LiteLlm instance and, when the inner generator
finishes WITHOUT yielding a single LlmResponse, synthesizes one with
``error_code`` set so the existing in-loop classifier (PR #827,
``_classify_child_event_error``) picks it up. The result: a typed
``child_llm_empty_provider_stream`` failure surfaces the actual model
+ provider that returned nothing — first time, with no extra round-trip.

Hermetic: mocks LiteLlm's parent ``generate_content_async`` so no
litellm / network access is required.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest


@pytest.mark.asyncio
async def test_wrapper_synthesizes_empty_provider_stream_on_zero_yields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from magi_agent.cli.litellm_empty_observer import (
        EmptyProviderStreamObserverLiteLlm,
        EMPTY_PROVIDER_STREAM_ERROR_CODE,
    )

    # Replace the parent's generate_content_async with a generator that
    # yields ZERO events — the exact silent-drop ADK shape.
    async def _empty_generator(*_a: Any, **_kw: Any) -> AsyncIterator[object]:
        return
        yield  # pragma: no cover

    monkeypatch.setattr(
        "google.adk.models.lite_llm.LiteLlm.generate_content_async",
        _empty_generator,
        raising=True,
    )

    wrapper = EmptyProviderStreamObserverLiteLlm(model="anthropic/claude-opus-4-8")
    yielded = [r async for r in wrapper.generate_content_async()]
    assert len(yielded) == 1, "must synthesize exactly one error response"
    [resp] = yielded
    assert getattr(resp, "error_code", None) == EMPTY_PROVIDER_STREAM_ERROR_CODE
    msg = str(getattr(resp, "error_message", "") or "")
    # The synthesized message must surface the model so the operator can act
    # without going back to the raw transcript.
    assert "anthropic/claude-opus-4-8" in msg, msg


@pytest.mark.asyncio
async def test_wrapper_passes_through_non_empty_streams(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from magi_agent.cli.litellm_empty_observer import (
        EmptyProviderStreamObserverLiteLlm,
    )

    class _FakeResponse:
        def __init__(self, text: str) -> None:
            self.text = text

    async def _real_stream(*_a: Any, **_kw: Any) -> AsyncIterator[object]:
        yield _FakeResponse("hello")
        yield _FakeResponse("world")

    monkeypatch.setattr(
        "google.adk.models.lite_llm.LiteLlm.generate_content_async",
        _real_stream,
        raising=True,
    )

    wrapper = EmptyProviderStreamObserverLiteLlm(model="openai/gpt-5.5")
    yielded = [r async for r in wrapper.generate_content_async()]
    assert len(yielded) == 2
    assert getattr(yielded[0], "text", None) == "hello"
    assert getattr(yielded[1], "text", None) == "world"
    # No synthesized error appended.
    for r in yielded:
        assert getattr(r, "error_code", None) is None


@pytest.mark.asyncio
async def test_wrapper_does_not_double_emit_when_parent_already_errored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # ADK CAN emit an LlmResponse with error_code already set (the
    # text-or-reasoning-and-non-stop finish-reason path); our wrapper must
    # NOT then ALSO synthesize a duplicate. Only inject when the upstream
    # stream produced literally zero LlmResponse objects.
    from magi_agent.cli.litellm_empty_observer import (
        EmptyProviderStreamObserverLiteLlm,
    )

    class _ErrorResp:
        error_code = "SAFETY"

    async def _one_error(*_a: Any, **_kw: Any) -> AsyncIterator[object]:
        yield _ErrorResp()

    monkeypatch.setattr(
        "google.adk.models.lite_llm.LiteLlm.generate_content_async",
        _one_error,
        raising=True,
    )

    wrapper = EmptyProviderStreamObserverLiteLlm(model="anthropic/claude-opus-4-8")
    yielded = [r async for r in wrapper.generate_content_async()]
    assert len(yielded) == 1
    assert getattr(yielded[0], "error_code", None) == "SAFETY"


@pytest.mark.asyncio
async def test_wrapper_synthesizes_even_when_parent_raises_stop_iteration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Some test doubles return immediately. Make sure normal generator
    # termination (no events, no exception) still triggers the synthesis.
    from magi_agent.cli.litellm_empty_observer import (
        EmptyProviderStreamObserverLiteLlm,
    )

    async def _immediate_return(*_a: Any, **_kw: Any) -> AsyncIterator[object]:
        if False:
            yield  # pragma: no cover — empty generator marker.

    monkeypatch.setattr(
        "google.adk.models.lite_llm.LiteLlm.generate_content_async",
        _immediate_return,
        raising=True,
    )

    wrapper = EmptyProviderStreamObserverLiteLlm(model="google/gemini-3.5-flash")
    yielded = [r async for r in wrapper.generate_content_async()]
    assert len(yielded) == 1
    assert "google/gemini-3.5-flash" in str(
        getattr(yielded[0], "error_message", "") or ""
    )
