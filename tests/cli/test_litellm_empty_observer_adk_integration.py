"""ADK upgrade safety net for ``EmptyProviderStreamObserverLiteLlm`` (PR #880).

The unit tests in ``test_litellm_empty_finish_reason_observer.py`` monkeypatch
``LiteLlm.generate_content_async`` — they prove the OVERRIDE logic works in
isolation but cannot catch ADK signature/structure changes that would break
our subclass at runtime (e.g. ``generate_content_async`` becoming non-async,
``LlmResponse`` renaming ``error_code`` to ``errorCode``, the LiteLlm base
class moving).

This integration suite uses the REAL ADK installation (ADK 1.33.0 today; 2.3.0
upstream HEAD has the same silent-drop pattern) without making network calls.
It catches the failure modes that would otherwise ship silently:

  * The subclass class-builds cleanly against real ADK.
  * An instance constructs with the real LiteLlm.__init__ contract.
  * The override drives a real LiteLlm.llm_client.acompletion call through
    a fake llm_client whose stream yields zero events — verifying the
    silent-drop pattern still exists in the installed ADK AND that our
    wrapper still synthesizes the error LlmResponse.
  * The synthesized LlmResponse uses fields ADK's downstream Runner can read
    (``error_code`` / ``error_message``) — proving the wrapper's output is
    well-formed for the ADK contract, not just our internal classifier.

When ADK changes any of these, these tests fail FIRST instead of an empty
turn showing up in production.
"""
from __future__ import annotations

import inspect

import pytest


def test_observer_subclass_imports_against_real_adk() -> None:
    # Imports both ADK and the wrapper. Trivially proves the inheritance
    # chain works with the installed ADK version. Fails on ADK module
    # rename / class removal / restructure.
    from google.adk.models.lite_llm import LiteLlm
    from google.adk.models.llm_response import LlmResponse  # noqa: F401

    from magi_agent.cli.litellm_empty_observer import (
        EmptyProviderStreamObserverLiteLlm,
    )

    # The wrapper MUST be a strict subclass of the real ADK LiteLlm,
    # not a shim that bypasses ADK's normalization layers.
    assert issubclass(EmptyProviderStreamObserverLiteLlm, LiteLlm)


def test_observer_override_signature_matches_adk_parent() -> None:
    from google.adk.models.lite_llm import LiteLlm

    from magi_agent.cli.litellm_empty_observer import (
        EmptyProviderStreamObserverLiteLlm,
    )

    parent_sig = inspect.signature(LiteLlm.generate_content_async)
    child_sig = inspect.signature(
        EmptyProviderStreamObserverLiteLlm.generate_content_async
    )
    # The override accepts ``*args, **kwargs`` for forward-compat with new
    # ADK kwargs (audio modality, streaming flags, etc.). What we ASSERT
    # here is that the override still PRODUCES an async generator the way
    # the parent does — i.e. it's an async-generator function. If ADK
    # changes the protocol (returns Awaitable instead of AsyncIterator),
    # this trips.
    assert inspect.isasyncgenfunction(LiteLlm.generate_content_async), (
        "ADK parent generate_content_async is no longer an async generator — "
        "the wrapper's `async for ... yield` shape must be revisited."
    )
    assert inspect.isasyncgenfunction(
        EmptyProviderStreamObserverLiteLlm.generate_content_async
    ), "Observer override drift"
    # Sanity check that the parameter count is compatible. We don't pin the
    # exact names because ADK's internal names can change; the *args/**kwargs
    # forwarding tolerates additions.
    assert "self" in parent_sig.parameters
    assert "self" in child_sig.parameters


def test_observer_constructs_with_real_litellm_init_contract() -> None:
    # Real ADK LiteLlm has a non-trivial __init__ (model, api_base, api_key,
    # api_version, ...). The wrapper must accept the production call shape.
    # No completion is performed — just construction.
    from magi_agent.cli.litellm_empty_observer import (
        EmptyProviderStreamObserverLiteLlm,
    )

    inst = EmptyProviderStreamObserverLiteLlm(model="anthropic/claude-sonnet-4-6")
    assert getattr(inst, "model", None) == "anthropic/claude-sonnet-4-6"


@pytest.mark.asyncio
async def test_observer_synthesizes_empty_via_real_adk_path() -> None:
    """Drive an end-to-end empty stream through the real ADK code paths.

    The fake ``llm_client`` returns an async iterator that yields zero
    completion chunks — the exact production shape that triggers the
    silent-drop in ADK's ``generate_content_async``. The wrapper must
    detect zero yields and synthesize the error LlmResponse.

    This is the regression guard that would catch ADK silently fixing
    the upstream drop (`yielded > 0` skip works as expected → wrapper
    no-ops) AND would catch ADK changing the stream-shape contract in
    a way that breaks our override.
    """
    from google.adk.models.lite_llm import LlmRequest
    from google.genai import types as genai_types

    from magi_agent.cli.litellm_empty_observer import (
        EMPTY_PROVIDER_STREAM_ERROR_CODE,
        EmptyProviderStreamObserverLiteLlm,
    )

    class _EmptyClient:
        async def acompletion(self, **_kwargs: object):
            async def _gen():
                if False:
                    yield  # pragma: no cover — empty generator marker.

            return _gen()

    wrapper = EmptyProviderStreamObserverLiteLlm(model="anthropic/test-empty")
    # Swap the inner llm_client; we don't mock the wrapper itself so this
    # exercises the real ADK code path between our override and acompletion.
    wrapper.llm_client = _EmptyClient()  # type: ignore[assignment]

    # Build a minimal LlmRequest the real ADK contract accepts.
    request = LlmRequest(
        model="anthropic/test-empty",
        contents=[
            genai_types.Content(
                role="user",
                parts=[genai_types.Part(text="ping")],
            )
        ],
    )

    yielded = []
    async for resp in wrapper.generate_content_async(request, stream=True):
        yielded.append(resp)

    # The integration assertion: even after the FULL real-ADK stream
    # processing inside super().generate_content_async, an empty inner
    # client produces zero LlmResponse — and our wrapper appends exactly
    # one synthetic error response with the actual model name.
    assert len(yielded) == 1, (
        f"expected one synthesized error response, got {len(yielded)}: {yielded}"
    )
    [resp] = yielded
    assert getattr(resp, "error_code", None) == EMPTY_PROVIDER_STREAM_ERROR_CODE
    msg = str(getattr(resp, "error_message", "") or "")
    assert "anthropic/test-empty" in msg, msg


# NOTE: a real-ADK text-stream pass-through test was attempted here but ADK
# requires litellm's internal ``ModelResponseStream`` / ``ModelResponse``
# instances (lite_llm.py:1544 isinstance gate) — constructing real instances
# without making a network call requires importing litellm internals. The
# pass-through behavior is covered by
# ``test_litellm_empty_finish_reason_observer.py::test_wrapper_passes_through_non_empty_streams``
# (monkeypatched) instead; this integration file owns the EMPTY-path
# regression guard, which is what would silently regress on an ADK upgrade.
