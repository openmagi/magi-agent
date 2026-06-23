"""``_LiteLlmResponseTap`` captures provider-level metadata ADK normally
discards.

This tests the BELOW-ADK layer the empty-observer wrapper relies on for
diagnostic info: when the upstream stream completes with zero yielded
LlmResponse objects, the synthesized error message must include
``raw_finish_reason``, ``raw_model``, usage tokens, and any exception
class — the signals operators need to distinguish auth-fail vs
content-filter vs model_not_found vs rate_limit.

Hermetic: the tap wraps a fake ``LiteLLMClient`` so no network is
required and ADK is only imported for the LiteLLMClient base class.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from magi_agent.cli.litellm_empty_observer import (
    EMPTY_PROVIDER_STREAM_ERROR_CODE,
    EmptyProviderStreamObserverLiteLlm,
    _LiteLlmCallSnapshot,
    _LiteLlmResponseTap,
)


class _FakeUsage:
    def __init__(
        self,
        *,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
    ) -> None:
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = total_tokens


class _FakeChoice:
    def __init__(self, finish_reason: str | None) -> None:
        self.finish_reason = finish_reason


class _FakeChunk:
    def __init__(
        self,
        *,
        model: str | None = None,
        finish_reason: str | None = None,
        usage: _FakeUsage | None = None,
    ) -> None:
        self.model = model
        self.choices = [_FakeChoice(finish_reason)] if finish_reason or model else []
        self.usage = usage


class _FakeStream:
    def __init__(self, chunks: list[_FakeChunk]) -> None:
        self._chunks = iter(chunks)

    def __aiter__(self) -> "_FakeStream":
        return self

    async def __anext__(self) -> _FakeChunk:
        try:
            return next(self._chunks)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class _FakeNonStreamingResponse:
    def __init__(
        self,
        *,
        model: str,
        finish_reason: str | None,
        usage: _FakeUsage | None = None,
    ) -> None:
        self.model = model
        self.choices = [_FakeChoice(finish_reason)]
        self.usage = usage


class _FakeStreamingClient:
    def __init__(self, chunks: list[_FakeChunk]) -> None:
        self._chunks = chunks
        self.calls: list[dict[str, Any]] = []

    async def acompletion(
        self, model: str, messages: Any, tools: Any, **kwargs: Any
    ) -> _FakeStream:
        self.calls.append({"model": model, "messages": messages, "tools": tools, **kwargs})
        return _FakeStream(self._chunks)


class _FakeNonStreamingClient:
    def __init__(self, response: _FakeNonStreamingResponse) -> None:
        self._response = response

    async def acompletion(
        self, model: str, messages: Any, tools: Any, **kwargs: Any
    ) -> _FakeNonStreamingResponse:
        return self._response


class _RaisingClient:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def acompletion(self, *_a: Any, **_kw: Any) -> Any:
        raise self._exc


# ---------------------------------------------------------------------------
# _LiteLlmCallSnapshot
# ---------------------------------------------------------------------------


def test_snapshot_diagnostic_suffix_empty_when_nothing_captured() -> None:
    snap = _LiteLlmCallSnapshot()
    assert snap.diagnostic_suffix() == ""


def test_snapshot_diagnostic_suffix_includes_captured_fields() -> None:
    snap = _LiteLlmCallSnapshot(
        last_finish_reason="content_filter",
        last_model="anthropic/claude-opus-4-8",
        prompt_tokens=42,
        completion_tokens=0,
        total_tokens=42,
        exception_summary=None,
        chunks_seen=3,
    )
    suffix = snap.diagnostic_suffix()
    assert "raw_finish_reason=content_filter" in suffix
    assert "raw_model=anthropic/claude-opus-4-8" in suffix
    assert "usage=prompt:42/completion:0/total:42" in suffix
    assert "chunks_seen=3" in suffix


def test_snapshot_diagnostic_suffix_length_bounded() -> None:
    snap = _LiteLlmCallSnapshot(
        last_finish_reason="x" * 5000, last_model="m", chunks_seen=1
    )
    suffix = snap.diagnostic_suffix()
    assert len(suffix) <= 700  # base " — " + bounded fields


def test_snapshot_reset_clears_state() -> None:
    snap = _LiteLlmCallSnapshot(
        last_finish_reason="stop", last_model="x", chunks_seen=5
    )
    snap.reset()
    assert snap.diagnostic_suffix() == ""


# ---------------------------------------------------------------------------
# _LiteLlmResponseTap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tap_captures_streaming_finish_reason_and_usage() -> None:
    inner = _FakeStreamingClient(
        chunks=[
            _FakeChunk(model="anthropic/claude-opus-4-8"),
            _FakeChunk(finish_reason="content_filter"),
            _FakeChunk(usage=_FakeUsage(prompt_tokens=12, completion_tokens=0, total_tokens=12)),
        ]
    )
    tap = _LiteLlmResponseTap(inner)
    stream = await tap.acompletion(
        model="anthropic/claude-opus-4-8", messages=[], tools=None
    )
    chunks = [chunk async for chunk in stream]
    assert len(chunks) == 3
    assert tap.snapshot.last_finish_reason == "content_filter"
    assert tap.snapshot.last_model == "anthropic/claude-opus-4-8"
    assert tap.snapshot.prompt_tokens == 12
    assert tap.snapshot.completion_tokens == 0
    assert tap.snapshot.chunks_seen == 3


@pytest.mark.asyncio
async def test_tap_captures_non_streaming_response() -> None:
    response = _FakeNonStreamingResponse(
        model="openai/gpt-5.5",
        finish_reason="stop",
        usage=_FakeUsage(prompt_tokens=8, completion_tokens=2, total_tokens=10),
    )
    inner = _FakeNonStreamingClient(response)
    tap = _LiteLlmResponseTap(inner)
    result = await tap.acompletion(model="openai/gpt-5.5", messages=[], tools=None)
    assert result is response
    assert tap.snapshot.last_finish_reason == "stop"
    assert tap.snapshot.last_model == "openai/gpt-5.5"
    assert tap.snapshot.completion_tokens == 2
    assert tap.snapshot.chunks_seen == 1


@pytest.mark.asyncio
async def test_tap_captures_exception_and_re_raises() -> None:
    class _BoomError(RuntimeError):
        pass

    inner = _RaisingClient(_BoomError("anthropic 404: model not found"))
    tap = _LiteLlmResponseTap(inner)
    with pytest.raises(_BoomError, match="model not found"):
        await tap.acompletion(
            model="anthropic/claude-opus-4-8", messages=[], tools=None
        )
    assert tap.snapshot.exception_summary is not None
    assert "_BoomError" in tap.snapshot.exception_summary
    assert "model not found" in tap.snapshot.exception_summary
    assert tap.snapshot.last_model == "anthropic/claude-opus-4-8"


@pytest.mark.asyncio
async def test_tap_reset_per_call() -> None:
    inner_1 = _FakeStreamingClient(
        chunks=[_FakeChunk(model="m1", finish_reason="content_filter")]
    )
    tap = _LiteLlmResponseTap(inner_1)
    stream = await tap.acompletion(model="m1", messages=[], tools=None)
    [_ async for _ in stream]
    assert tap.snapshot.last_finish_reason == "content_filter"

    # Second call must start with a clean snapshot — no stale data from #1.
    tap._inner = _FakeStreamingClient(
        chunks=[_FakeChunk(model="m2", finish_reason="stop")]
    )
    stream = await tap.acompletion(model="m2", messages=[], tools=None)
    [_ async for _ in stream]
    assert tap.snapshot.last_finish_reason == "stop"
    assert tap.snapshot.last_model == "m2"


# ---------------------------------------------------------------------------
# Wrapper folds tap data into empty-completion error message
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_completion_error_message_includes_raw_finish_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ADK silently drops the response, the wrapper's synthesized
    error must include the raw finish_reason the tap captured so the
    operator sees the actual provider signal, not just "empty"."""
    # Fake ADK super().generate_content_async — zero yields.
    async def _empty_super(*_a: Any, **_kw: Any) -> AsyncIterator[object]:
        return
        yield  # pragma: no cover

    monkeypatch.setattr(
        "google.adk.models.lite_llm.LiteLlm.generate_content_async",
        _empty_super,
        raising=True,
    )

    wrapper = EmptyProviderStreamObserverLiteLlm(model="anthropic/claude-opus-4-8")
    # Pre-populate the tap's snapshot to simulate what a real streaming
    # call would have captured before ADK silently dropped it.
    tap = wrapper._response_tap
    assert tap is not None
    tap.snapshot.last_finish_reason = "content_filter"
    tap.snapshot.last_model = "anthropic/claude-opus-4-8"
    tap.snapshot.prompt_tokens = 12
    tap.snapshot.completion_tokens = 0
    tap.snapshot.total_tokens = 12
    tap.snapshot.chunks_seen = 2

    [resp] = [r async for r in wrapper.generate_content_async()]
    assert getattr(resp, "error_code", None) == EMPTY_PROVIDER_STREAM_ERROR_CODE
    msg = str(getattr(resp, "error_message", "") or "")
    assert "anthropic/claude-opus-4-8" in msg
    assert "raw_finish_reason=content_filter" in msg
    assert "usage=prompt:12/completion:0/total:12" in msg
    assert "chunks_seen=2" in msg


@pytest.mark.asyncio
async def test_empty_completion_message_omits_diagnostic_suffix_when_nothing_captured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tap snapshot empty (e.g. ADK never invoked acompletion at all) →
    no diagnostic suffix appended; the base message still surfaces."""

    async def _empty_super(*_a: Any, **_kw: Any) -> AsyncIterator[object]:
        return
        yield  # pragma: no cover

    monkeypatch.setattr(
        "google.adk.models.lite_llm.LiteLlm.generate_content_async",
        _empty_super,
        raising=True,
    )

    wrapper = EmptyProviderStreamObserverLiteLlm(model="anthropic/test")
    [resp] = [r async for r in wrapper.generate_content_async()]
    msg = str(getattr(resp, "error_message", "") or "")
    # Base message present.
    assert "anthropic/test" in msg
    # Diagnostic suffix is the empty string when nothing captured.
    assert " — raw_finish_reason" not in msg
    assert " — provider_exception" not in msg


@pytest.mark.asyncio
async def test_success_path_does_not_synthesize_or_modify_yields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression guard for the byte-identical success-path claim."""

    class _RealResp:
        text = "hello"

    async def _stream(*_a: Any, **_kw: Any) -> AsyncIterator[object]:
        yield _RealResp()
        yield _RealResp()

    monkeypatch.setattr(
        "google.adk.models.lite_llm.LiteLlm.generate_content_async",
        _stream,
        raising=True,
    )

    wrapper = EmptyProviderStreamObserverLiteLlm(model="openai/gpt-5.5")
    yielded = [r async for r in wrapper.generate_content_async()]
    assert len(yielded) == 2
    for r in yielded:
        assert getattr(r, "error_code", None) is None
