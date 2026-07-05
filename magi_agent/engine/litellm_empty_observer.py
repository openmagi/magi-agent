"""Wrapper for ADK's ``LiteLlm`` that surfaces silent-empty completions.

ADK ``google/adk/models/lite_llm.py:2418-2445`` only finalizes an
``LlmResponse`` when ``(text or reasoning_parts)`` is truthy. When the
provider returns 200 OK with a finish_reason but zero text + zero tool
calls — anthropic's ``content_filter`` / ``unknown_model_id`` / Gemini's
empty-completion shape / a 0-token response — ADK silently drops the
finish_reason and the downstream ``Runner.run_async`` sees ZERO events.

That silent drop is the root cause of the anthropic / google 100-200ms
empty-summary symptom Kevin chased across 0.1.62 → 0.1.74. PRs #854 and
#876 catch the empty AFTER the fact (good — agent no longer chaos), but
the WHY (which finish_reason did the provider return for which model)
stayed buried in the lost LlmResponse.

This wrapper sits ABOVE ADK's LiteLlm AND taps the ``llm_client.acompletion``
call BELOW ADK. The tap captures the raw provider response metadata
(finish_reason, model, usage, exception class) that ADK silently
discards, and the wrapper folds it into the synthesized error message
so the operator sees the actual provider signal — not just \"empty\".
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from google.adk.models.lite_llm import LiteLLMClient, LiteLlm
from google.adk.models.llm_response import LlmResponse

#: Wire-stable error code consumed by ``_classify_child_event_error``.
#: Lowercased + slugified there into ``child_llm_empty_provider_stream``.
EMPTY_PROVIDER_STREAM_ERROR_CODE = "EMPTY_PROVIDER_STREAM"


def _is_mergeable_thought_part(part: Any) -> bool:
    """A thought part that may be merged with its neighbours.

    ``True`` only for UNSIGNED, text-only reasoning fragments: ``thought`` is
    truthy, it carries ``text`` (not ``None``), and it has NO signature and NO
    non-text payload. Anthropic's final thinking part carries a
    ``thought_signature`` (lite_llm.py:431-433, anthropic_llm.py:620-622) which
    MUST survive verbatim, so a signed part is never mergeable. Parts carrying
    inline_data / function_call / function_response are structural, not
    reasoning text, and are likewise excluded.
    """
    if not getattr(part, "thought", False):
        return False
    if getattr(part, "text", None) is None:
        return False
    if getattr(part, "thought_signature", None) is not None:
        return False
    if getattr(part, "inline_data", None) is not None:
        return False
    if getattr(part, "function_call", None) is not None:
        return False
    if getattr(part, "function_response", None) is not None:
        return False
    return True


def _coalesce_unsigned_thought_parts(parts: list[Any]) -> tuple[list[Any], bool]:
    """Merge each ADJACENT run of mergeable (unsigned, text-only) thought parts
    into a SINGLE ``Part(text="".join(texts), thought=True)``.

    ADK streams Kimi/OpenAI reasoning one ``Part(thought=True)`` per token; the
    request rebuild joins those per-token parts with a newline
    (lite_llm.py:928), shredding the model's own history one-token-per-line.
    Collapsing each adjacent run to one part means that join has a single
    element and injects nothing. The join here is ``""`` (verbatim
    concatenation) so a fragment that legitimately contains a real newline is
    preserved exactly and NO separator is introduced.

    Signed thought parts, non-thought parts, and structural parts (inline_data
    / function_call / function_response) break the run and pass through
    untouched, order preserved. Returns ``(new_parts, changed)`` where
    ``changed`` is ``False`` when nothing merged (so the caller can leave the
    response object byte-identical).
    """
    from google.genai import types  # noqa: PLC0415 (keep module import cold)

    merged: list[Any] = []
    changed = False
    run: list[Any] = []

    def _flush_run() -> None:
        nonlocal changed
        if not run:
            return
        if len(run) == 1:
            merged.append(run[0])
        else:
            merged.append(
                types.Part(text="".join(p.text for p in run), thought=True)
            )
            changed = True
        run.clear()

    for part in parts:
        if _is_mergeable_thought_part(part):
            run.append(part)
            continue
        _flush_run()
        merged.append(part)
    _flush_run()
    return merged, changed


def _coalesce_response_thought_parts(resp: Any) -> None:
    """In-place coalesce the thought parts of a NON-PARTIAL ``LlmResponse``.

    Partial responses (live thinking deltas) are left untouched by the caller;
    this only runs on the aggregated, non-partial responses ADK stores as
    session events. Mutates ``resp.content.parts`` in place (via slice
    assignment on the existing list) so no pydantic field is reassigned and the
    surrounding response object stays otherwise identical. Fail-soft: any
    structural surprise leaves the response unchanged.
    """
    try:
        content = getattr(resp, "content", None)
        parts = getattr(content, "parts", None)
        if not parts:
            return
        merged, changed = _coalesce_unsigned_thought_parts(list(parts))
        if changed:
            parts[:] = merged
    except Exception:  # noqa: BLE001 (never break the stream on coalescing)
        return


#: Hard cap on diagnostic text we attach to the error message — provider
#: errors / model ids can balloon if not bounded.
_DIAG_MAX_CHARS = 600


@dataclass
class _LiteLlmCallSnapshot:
    """The provider-level signal ADK normally throws away.

    Populated by :class:`_LiteLlmResponseTap` as it forwards
    ``acompletion`` calls; read by
    :class:`EmptyProviderStreamObserverLiteLlm` when the upstream stream
    produces zero ``LlmResponse`` events so the synthesized error
    message can name the ACTUAL reason (finish_reason from the provider,
    completion tokens that came back as zero, an exception class from
    litellm itself, ...).
    """

    last_finish_reason: str | None = None
    last_model: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    exception_summary: str | None = None
    chunks_seen: int = 0

    def reset(self) -> None:
        self.last_finish_reason = None
        self.last_model = None
        self.prompt_tokens = None
        self.completion_tokens = None
        self.total_tokens = None
        self.exception_summary = None
        self.chunks_seen = 0

    def diagnostic_suffix(self) -> str:
        """Human-readable provider signal, length-bounded + safe for
        inclusion in error messages.

        Returns the empty string when nothing was captured — fail-soft
        so the wrapper's base message stays useful even if the tap
        couldn't read anything off litellm's response shape.
        """
        parts: list[str] = []
        if self.last_finish_reason:
            parts.append(f"raw_finish_reason={self.last_finish_reason}")
        if self.last_model and self.last_model != "unknown":
            parts.append(f"raw_model={self.last_model}")
        if self.completion_tokens is not None:
            parts.append(
                f"usage=prompt:{self.prompt_tokens}/completion:{self.completion_tokens}"
                f"/total:{self.total_tokens}"
            )
        if self.chunks_seen > 0:
            parts.append(f"chunks_seen={self.chunks_seen}")
        if self.exception_summary:
            parts.append(f"provider_exception={self.exception_summary}")
        if not parts:
            return ""
        joined = " ".join(parts)
        if len(joined) > _DIAG_MAX_CHARS:
            joined = joined[: _DIAG_MAX_CHARS - 1] + "…"
        return " — " + joined


class _LiteLlmResponseTap(LiteLLMClient):
    """LiteLLMClient wrapper that captures provider-level response metadata.

    Subclasses the real ``LiteLLMClient`` so it satisfies ADK's pydantic
    typing on the ``llm_client`` field (isinstance check passes). Forwards
    ``acompletion`` to an inner client and snapshots:

      * ``last_finish_reason`` from streaming chunks (or single response)
      * ``last_model`` — the model id the provider actually returned
        (LiteLLM can rewrite this if the request fell through to a
        fallback)
      * ``prompt_tokens`` / ``completion_tokens`` / ``total_tokens``
        from the usage chunk
      * ``exception_summary`` — class + truncated message if litellm
        itself raised (e.g. AuthenticationError, RateLimitError,
        BadRequestError with the provider's actual error body)

    Never re-raises mid-iteration; exceptions ARE propagated up so ADK's
    own error path runs, but the summary is snapshotted on the way out.
    """

    snapshot: _LiteLlmCallSnapshot

    def __init__(self, inner: LiteLLMClient | None = None) -> None:
        # NOTE: LiteLLMClient subclasses don't share state with the
        # parent ``llm_client`` field by default — pydantic treats them
        # as opaque values. We attach our snapshot via object.__setattr__
        # so pydantic's no-extra-fields guard doesn't complain.
        super().__init__()
        object.__setattr__(self, "_inner", inner or LiteLLMClient())
        object.__setattr__(self, "snapshot", _LiteLlmCallSnapshot())

    async def acompletion(self, model, messages, tools, **kwargs):  # type: ignore[override]
        snap: _LiteLlmCallSnapshot = self.snapshot
        snap.reset()
        snap.last_model = model
        try:
            result = await self._inner.acompletion(  # type: ignore[attr-defined]
                model=model, messages=messages, tools=tools, **kwargs
            )
        except Exception as exc:  # noqa: BLE001 — re-raise after capturing.
            snap.exception_summary = f"{type(exc).__name__}: {str(exc)[:200]}"
            raise
        if hasattr(result, "__aiter__"):
            # Streaming response — wrap so each chunk updates the snapshot.
            return _CapturingStreamWrapper(result, snap)
        # Non-streaming response — snapshot the single completion.
        _capture_non_streaming(snap, result)
        return result


class _CapturingStreamWrapper:
    """Async-iterator passthrough that snapshots each chunk's metadata."""

    def __init__(self, inner: Any, snapshot: _LiteLlmCallSnapshot) -> None:
        self._inner = inner
        self._snapshot = snapshot

    def __aiter__(self) -> "_CapturingStreamWrapper":
        return self

    async def __anext__(self) -> Any:
        chunk = await self._inner.__anext__()
        _capture_streaming_chunk(self._snapshot, chunk)
        return chunk


def _capture_non_streaming(snap: _LiteLlmCallSnapshot, response: Any) -> None:
    """Snapshot the relevant fields off a non-streaming ``ModelResponse``."""
    try:
        choices = getattr(response, "choices", None) or []
        if choices:
            choice = choices[0]
            finish = getattr(choice, "finish_reason", None)
            if finish:
                snap.last_finish_reason = str(finish)
    except Exception:  # noqa: BLE001 — best-effort capture.
        pass
    try:
        model = getattr(response, "model", None)
        if model:
            snap.last_model = str(model)
    except Exception:  # noqa: BLE001
        pass
    try:
        usage = getattr(response, "usage", None)
        if usage is not None:
            snap.prompt_tokens = getattr(usage, "prompt_tokens", None)
            snap.completion_tokens = getattr(usage, "completion_tokens", None)
            snap.total_tokens = getattr(usage, "total_tokens", None)
    except Exception:  # noqa: BLE001
        pass
    snap.chunks_seen = 1


def _capture_streaming_chunk(snap: _LiteLlmCallSnapshot, chunk: Any) -> None:
    """Snapshot per-chunk metadata; called for every streamed item."""
    snap.chunks_seen += 1
    try:
        model = getattr(chunk, "model", None)
        if model:
            snap.last_model = str(model)
    except Exception:  # noqa: BLE001
        pass
    try:
        choices = getattr(chunk, "choices", None) or []
        if choices:
            finish = getattr(choices[0], "finish_reason", None)
            if finish:
                snap.last_finish_reason = str(finish)
    except Exception:  # noqa: BLE001
        pass
    try:
        usage = getattr(chunk, "usage", None)
        if usage is not None:
            # ``X or fallback`` would coerce a real 0 (zero completion
            # tokens — exactly the empty-completion signal) to the prior
            # value; check ``is not None`` so 0 propagates correctly.
            pt = getattr(usage, "prompt_tokens", None)
            if pt is not None:
                snap.prompt_tokens = pt
            ct = getattr(usage, "completion_tokens", None)
            if ct is not None:
                snap.completion_tokens = ct
            tt = getattr(usage, "total_tokens", None)
            if tt is not None:
                snap.total_tokens = tt
    except Exception:  # noqa: BLE001
        pass


class EmptyProviderStreamObserverLiteLlm(LiteLlm):
    """ADK LiteLlm that surfaces silent-empty completions as error events.

    Two layers of intercept:

      * BELOW ADK — replaces ``llm_client`` with :class:`_LiteLlmResponseTap`
        so each ``acompletion`` call's raw provider metadata is captured.
      * ABOVE ADK — overrides ``generate_content_async`` to count yields.
        When ADK produces zero LlmResponse objects (the silent-drop path),
        the wrapper synthesizes ONE error LlmResponse whose
        ``error_message`` folds in the captured raw metadata (finish_reason,
        usage tokens, exception class) so the operator sees the actual
        provider signal — not just \"empty\".

    Success path is byte-identical: ``yielded > 0`` skips the synthesis,
    the tap still captures (cheap), but the wrapper does not change
    behavior.
    """

    def __init__(self, **data: Any) -> None:
        # Wrap the configured llm_client with our tap BEFORE pydantic
        # init so the field validation sees a LiteLLMClient subclass.
        inner = data.pop("llm_client", None)
        if not isinstance(inner, _LiteLlmResponseTap):
            inner = _LiteLlmResponseTap(inner)
        data["llm_client"] = inner
        super().__init__(**data)

    @property
    def _response_tap(self) -> _LiteLlmResponseTap | None:
        """Access the tap if it was successfully installed."""
        client = self.llm_client
        return client if isinstance(client, _LiteLlmResponseTap) else None

    async def generate_content_async(
        self, *args: Any, **kwargs: Any
    ) -> AsyncIterator[object]:
        tap = self._response_tap
        yielded = 0
        async for resp in super().generate_content_async(*args, **kwargs):
            yielded += 1
            # Root fix: on the NON-PARTIAL aggregate (the shape ADK stores as
            # the session event and later rebuilds requests from), collapse each
            # adjacent run of unsigned per-token thought parts into one part so
            # ADK's request-rebuild newline-join (lite_llm.py:928) becomes a
            # no-op. Live partial thinking deltas stream through verbatim.
            if not getattr(resp, "partial", False):
                _coalesce_response_thought_parts(resp)
            yield resp
        if yielded == 0:
            base_msg = (
                "provider returned empty completion "
                f"(model={getattr(self, 'model', 'unknown')}) — "
                "ADK dropped the finish_reason because no text or "
                "reasoning chunks were produced; this is usually a "
                "model_id rejection, content_filter, or 0-token "
                "response from the provider"
            )
            diagnostic_suffix = (
                tap.snapshot.diagnostic_suffix() if tap is not None else ""
            )
            yield LlmResponse(
                error_code=EMPTY_PROVIDER_STREAM_ERROR_CODE,
                error_message=base_msg + diagnostic_suffix,
            )


__all__ = [
    "EMPTY_PROVIDER_STREAM_ERROR_CODE",
    "EmptyProviderStreamObserverLiteLlm",
    "_LiteLlmCallSnapshot",
    "_LiteLlmResponseTap",
    "_coalesce_unsigned_thought_parts",
    "_is_mergeable_thought_part",
]
