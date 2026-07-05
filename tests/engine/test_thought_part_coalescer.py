"""PR-2 (thought-part coalescer, the root fix for Kimi reasoning corruption).

ADK's ``lite_llm.py`` streams reasoning as one ``Part(thought=True)`` PER
TOKEN, then on every subsequent request in a tool loop rebuilds those
per-token parts into ``reasoning_content`` joined with ``"\\n"``
(lite_llm.py:928). Fireworks receives the model's own prior reasoning shredded
one-token-per-line; Kimi mimics the corruption (vertical thinking) and
degrades to ``content_len=0``.

Fix: coalesce ADJACENT unsigned text-only thought parts into ONE part on every
NON-PARTIAL ``LlmResponse`` at the ``EmptyProviderStreamObserverLiteLlm``
wrapper seam. ADK stores the wrapper's output as the session Event, so the
``_NEW_LINE.join`` at lite_llm.py:928 then has a single element and injects
nothing. Signed (Anthropic) thought parts are NEVER merged: their signatures
must survive verbatim.

Hermetic: mocks the parent ``LiteLlm.generate_content_async``; no network.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from google.adk.models.llm_response import LlmResponse
from google.genai import types


def _mk_response(parts: list[types.Part], *, partial: bool | None) -> LlmResponse:
    return LlmResponse(
        content=types.Content(role="model", parts=parts), partial=partial
    )


def _install_parent(
    monkeypatch: pytest.MonkeyPatch, responses: list[LlmResponse]
) -> None:
    async def _gen(*_a: Any, **_kw: Any) -> AsyncIterator[object]:
        for r in responses:
            yield r

    monkeypatch.setattr(
        "google.adk.models.lite_llm.LiteLlm.generate_content_async",
        _gen,
        raising=True,
    )


def _wrapper() -> Any:
    from magi_agent.engine.litellm_empty_observer import (
        EmptyProviderStreamObserverLiteLlm,
    )

    return EmptyProviderStreamObserverLiteLlm(model="fireworks_ai/kimi-k2p6")


@pytest.mark.asyncio
async def test_adjacent_unsigned_thought_parts_merge_on_final(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    final = _mk_response(
        [
            types.Part(text="재", thought=True),
            types.Part(text="무", thought=True),
            types.Part(text="상", thought=True),
            types.Part(text="태", thought=True),
        ],
        partial=False,
    )
    _install_parent(monkeypatch, [final])
    out = [r async for r in _wrapper().generate_content_async()]
    assert len(out) == 1
    parts = out[0].content.parts
    assert len(parts) == 1, "adjacent unsigned thought parts must merge to one"
    assert parts[0].text == "재무상태", "verbatim '' join (NO injected newline)"
    assert parts[0].thought is True
    assert parts[0].thought_signature is None


@pytest.mark.asyncio
async def test_join_is_empty_string_not_newline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fragment that legitimately CONTAINS a real newline is preserved
    exactly; the coalescer never itself injects a separator."""
    final = _mk_response(
        [
            types.Part(text="line1.\n\n", thought=True),
            types.Part(text="line2", thought=True),
        ],
        partial=False,
    )
    _install_parent(monkeypatch, [final])
    out = [r async for r in _wrapper().generate_content_async()]
    assert out[0].content.parts[0].text == "line1.\n\nline2"


@pytest.mark.asyncio
async def test_signed_thought_part_breaks_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A signed (Anthropic) thought part is NEVER merged; it splits the run
    into (unsigned-run, signed, unsigned-run) and keeps its signature."""
    final = _mk_response(
        [
            types.Part(text="a", thought=True),
            types.Part(text="b", thought=True),
            types.Part(text="SIGNED", thought=True, thought_signature=b"sig"),
            types.Part(text="c", thought=True),
            types.Part(text="d", thought=True),
        ],
        partial=False,
    )
    _install_parent(monkeypatch, [final])
    out = [r async for r in _wrapper().generate_content_async()]
    parts = out[0].content.parts
    assert len(parts) == 3
    assert parts[0].text == "ab" and parts[0].thought_signature is None
    assert parts[1].text == "SIGNED" and parts[1].thought_signature == b"sig"
    assert parts[2].text == "cd" and parts[2].thought_signature is None


@pytest.mark.asyncio
async def test_partial_responses_pass_through_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Live thinking deltas (partial=True) must NOT be coalesced: the same
    part objects flow through byte-identically."""
    p1 = types.Part(text="x", thought=True)
    p2 = types.Part(text="y", thought=True)
    partial = _mk_response([p1, p2], partial=True)
    _install_parent(monkeypatch, [partial])
    out = [r async for r in _wrapper().generate_content_async()]
    assert len(out) == 1
    assert out[0].content.parts == [p1, p2]
    assert out[0].content.parts[0] is p1 and out[0].content.parts[1] is p2


@pytest.mark.asyncio
async def test_non_thought_and_function_call_parts_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text_part = types.Part(text="answer", thought=False)
    fc = types.Part(function_call=types.FunctionCall(name="Bash", args={"cmd": "ls"}))
    thought_a = types.Part(text="th1", thought=True)
    thought_b = types.Part(text="th2", thought=True)
    final = _mk_response([thought_a, thought_b, text_part, fc], partial=False)
    _install_parent(monkeypatch, [final])
    out = [r async for r in _wrapper().generate_content_async()]
    parts = out[0].content.parts
    # thought run merges to one; text + function_call untouched, order preserved.
    assert len(parts) == 3
    assert parts[0].text == "th1th2" and parts[0].thought is True
    assert parts[1] is text_part
    assert parts[2] is fc


@pytest.mark.asyncio
async def test_zero_yield_synthesis_still_fires(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: the coalescer must not disturb the EMPTY_PROVIDER_STREAM
    synthesis when the parent yields nothing."""
    from magi_agent.engine.litellm_empty_observer import (
        EMPTY_PROVIDER_STREAM_ERROR_CODE,
    )

    async def _empty(*_a: Any, **_kw: Any) -> AsyncIterator[object]:
        return
        yield  # pragma: no cover

    monkeypatch.setattr(
        "google.adk.models.lite_llm.LiteLlm.generate_content_async",
        _empty,
        raising=True,
    )
    out = [r async for r in _wrapper().generate_content_async()]
    assert len(out) == 1
    assert out[0].error_code == EMPTY_PROVIDER_STREAM_ERROR_CODE


@pytest.mark.asyncio
async def test_single_thought_part_not_needlessly_rebuilt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A final with a single thought part (nothing to merge) passes through
    with the same part object (no churn)."""
    only = types.Part(text="solo", thought=True)
    final = _mk_response([only], partial=False)
    _install_parent(monkeypatch, [final])
    out = [r async for r in _wrapper().generate_content_async()]
    assert out[0].content.parts[0] is only


@pytest.mark.asyncio
async def test_request_rebuild_has_no_injected_newline_after_coalesce() -> None:
    """Characterization (ADK behavior): once a step is stored as a single
    coalesced thought part, ADK's ``_content_to_message_param`` rebuilds
    ``reasoning_content`` with NO injected newline (the 928 join is a no-op on
    one fragment). Guards against an ADK bump reintroducing shredding.
    """
    import inspect

    from google.adk.models import lite_llm as adk_lite_llm

    fn = getattr(adk_lite_llm, "_content_to_message_param", None)
    if fn is None:  # pragma: no cover - ADK internal renamed
        pytest.skip("_content_to_message_param not present in this ADK build")
    content = types.Content(
        role="model",
        parts=[types.Part(text="재무상태 요약", thought=True)],
    )
    message = fn(content)
    if inspect.isawaitable(message):
        message = await message
    if isinstance(message, list):  # provider may return a list of messages
        message = message[0] if message else None
    reasoning = None
    if isinstance(message, dict):
        reasoning = message.get("reasoning_content")
    elif message is not None:
        reasoning = getattr(message, "reasoning_content", None)
    if reasoning is None:  # pragma: no cover - provider shape differs
        pytest.skip("this ADK build did not surface reasoning_content")
    assert "\n" not in reasoning or reasoning == "재무상태 요약", (
        "coalesced single thought part must not gain an injected newline"
    )
