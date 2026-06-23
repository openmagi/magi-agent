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

This wrapper sits ABOVE ADK's LiteLlm. When the upstream stream
finishes WITHOUT yielding a single LlmResponse, we synthesize one with
``error_code`` set to ``EMPTY_PROVIDER_STREAM`` and the model name in
the message. The in-loop classifier
(:func:`magi_agent.runtime.child_runner_live._classify_child_event_error`)
already detects ``LlmResponse.error_code`` events and raises
``_ChildLlmTurnError`` — so the typed failure surfaces a real,
actionable reason instead of the generic empty-response slug.

ADK is a hard dep of magi-agent (LiteLlm import at module load is fine);
the subclass is built at module load. Tests monkeypatch
``LiteLlm.generate_content_async`` BEFORE constructing an instance —
Python's method resolution looks at MRO at call time, so the
monkeypatched parent method is what ``super().generate_content_async``
actually invokes. The earlier lazy-loading dance (``__getattr__``) was
over-engineered and tripped ruff F822.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from google.adk.models.lite_llm import LiteLlm
from google.adk.models.llm_response import LlmResponse

#: Wire-stable error code consumed by ``_classify_child_event_error``.
#: Lowercased + slugified there into ``child_llm_empty_provider_stream``.
EMPTY_PROVIDER_STREAM_ERROR_CODE = "EMPTY_PROVIDER_STREAM"


class EmptyProviderStreamObserverLiteLlm(LiteLlm):
    """ADK LiteLlm that surfaces silent-empty completions as error events.

    The override is the minimal possible change: count what the parent
    yields; if the count stays at zero across the whole stream, emit
    ONE synthetic LlmResponse carrying ``error_code`` so the existing
    child-runner classifier sees it as an actionable failure with the
    actual model name in the message.
    """

    async def generate_content_async(
        self, *args: Any, **kwargs: Any
    ) -> AsyncIterator[object]:
        yielded = 0
        async for resp in super().generate_content_async(*args, **kwargs):
            yielded += 1
            yield resp
        if yielded == 0:
            yield LlmResponse(
                error_code=EMPTY_PROVIDER_STREAM_ERROR_CODE,
                error_message=(
                    "provider returned empty completion "
                    f"(model={getattr(self, 'model', 'unknown')}) — "
                    "ADK dropped the finish_reason because no text or "
                    "reasoning chunks were produced; this is usually a "
                    "model_id rejection, content_filter, or 0-token "
                    "response from the provider"
                ),
            )


__all__ = [
    "EMPTY_PROVIDER_STREAM_ERROR_CODE",
    "EmptyProviderStreamObserverLiteLlm",
]
