"""PR13: live context-compaction activation tests.

Proves the dormant ``ContextLifecycleBoundary.compact_if_needed`` is now wired
onto the live ADK model loop via a ``before_model_callback`` plugin that mutates
the outgoing ``llm_request.contents`` before the model call.

Two layers of proof:

1. The plugin directly against a real ``LlmRequest`` (over/under threshold,
   tail preservation, orphan-response widening).
2. The plugin driven through a *real* ADK ``Runner``'s ``PluginManager``
   (``run_before_model_callback``) — i.e. the genuine dispatch path the live
   turn engine uses — proving the runner builder attaches it (flag ON) and that
   the mutation survives the real callback boundary, and that flag OFF leaves
   the runner with no compaction plugin and the contents untouched.
"""

from __future__ import annotations

import asyncio

import pytest
from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.invocation_context import InvocationContext
from google.adk.models import LlmRequest
from google.genai import types

from magi_agent.adk_bridge import local_runner
from magi_agent.adk_bridge.context_compaction import (
    CONTEXT_COMPACTION_PLUGIN_NAME,
    MagiContextCompactionPlugin,
    build_context_compaction_plugin,
)


def _content(index: int, text: str) -> types.Content:
    return types.Content(
        role="user" if index % 2 == 0 else "model",
        parts=[types.Part(text=text)],
    )


def _big_request(count: int, *, chars: int = 1600) -> LlmRequest:
    req = LlmRequest()
    req.contents = [_content(i, "x" * chars) for i in range(count)]
    return req


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Layer 1 — plugin against a real LlmRequest
# ---------------------------------------------------------------------------


def test_over_threshold_context_is_compacted_to_recent_tail() -> None:
    plugin = MagiContextCompactionPlugin(token_threshold=2_000, tail_events=16)
    req = _big_request(40)
    original_last = req.contents[-1].parts[0].text
    original_split = req.contents[24].parts[0].text  # 40 - 16 == 24

    result = _run(
        plugin.before_model_callback(callback_context=None, llm_request=req)
    )

    assert result is None  # request proceeds (mutated) to the model
    assert len(req.contents) == 16  # reduced to the tail
    # Recent tail preserved exactly (last event survives unchanged).
    assert req.contents[-1].parts[0].text == original_last
    # The kept window is the LAST 16 — first kept == original index 24.
    assert req.contents[0].parts[0].text == original_split


def test_under_threshold_context_is_untouched() -> None:
    plugin = MagiContextCompactionPlugin(token_threshold=1_000_000, tail_events=16)
    req = _big_request(40)
    before = list(req.contents)

    result = _run(
        plugin.before_model_callback(callback_context=None, llm_request=req)
    )

    assert result is None
    assert req.contents == before  # not reduced
    assert len(req.contents) == 40


def test_contents_at_or_below_tail_are_never_trimmed() -> None:
    # Even with a tiny token threshold, a context that already fits in the tail
    # window must not be reduced (nothing to compact).
    plugin = MagiContextCompactionPlugin(token_threshold=1, tail_events=16)
    req = _big_request(10)
    before = list(req.contents)

    _run(plugin.before_model_callback(callback_context=None, llm_request=req))

    assert req.contents == before
    assert len(req.contents) == 10


def test_tail_widens_to_avoid_orphaned_function_response() -> None:
    plugin = MagiContextCompactionPlugin(token_threshold=2_000, tail_events=4)
    req = LlmRequest()
    # 20 contents (indices 0-19); tail_events=4 -> naive split at index 16.
    # Place the function *call* at 15 and its matching *response* exactly at the
    # split boundary (16). The naive last-4 keep would start on the orphaned
    # response at 16; the plugin must widen backwards to include the call at 15.
    contents = [_content(i, "x" * 1600) for i in range(15)]
    contents.append(  # index 15 — the call
        types.Content(
            role="model",
            parts=[
                types.Part(
                    function_call=types.FunctionCall(name="Read", args={"path": "f"})
                )
            ],
        )
    )
    contents.append(  # index 16 — the orphan response at the naive split
        types.Content(
            role="user",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        name="Read", response={"ok": True}
                    )
                )
            ],
        )
    )
    contents.append(_content(17, "x" * 1600))
    contents.append(_content(18, "x" * 1600))
    contents.append(_content(19, "x" * 1600))
    req.contents = contents

    _run(plugin.before_model_callback(callback_context=None, llm_request=req))

    # First kept content must NOT be an orphaned function response.
    first = req.contents[0]
    assert not any(
        getattr(p, "function_response", None) is not None for p in (first.parts or [])
    )
    # Tail widened from 4 to include the function_call -> 5 kept (call + 4).
    assert len(req.contents) == 5
    # The widened head is the function call.
    assert first.parts[0].function_call is not None
    # Last content preserved.
    assert req.contents[-1].parts[0].text == "x" * 1600


def test_plugin_fails_open_on_unexpected_error() -> None:
    plugin = MagiContextCompactionPlugin(token_threshold=2_000, tail_events=16)

    class _Boom:
        @property
        def contents(self):  # noqa: ANN001
            raise RuntimeError("boom")

    # Must not raise into the model loop.
    result = _run(
        plugin.before_model_callback(callback_context=None, llm_request=_Boom())
    )
    assert result is None


# ---------------------------------------------------------------------------
# Layer 2 — driven through a real ADK Runner PluginManager
# ---------------------------------------------------------------------------


async def _callback_context(bundle) -> CallbackContext:  # noqa: ANN001
    session = await bundle.session_service.create_session(
        app_name="magi-agent-local", user_id="u", session_id="s"
    )
    ic = InvocationContext(
        session_service=bundle.session_service,
        invocation_id="inv-pr13",
        agent=bundle.agent,
        session=session,
        plugin_manager=bundle.runner.plugin_manager,
    )
    return CallbackContext(ic)


def test_runner_attaches_plugin_and_compacts_via_real_plugin_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CORE_AGENT_PYTHON_LOCAL_ADK_RUNNER", "1")
    monkeypatch.setenv("MAGI_CONTEXT_COMPACTION_ENABLED", "1")
    monkeypatch.setenv("MAGI_COMPACTION_TOKEN_THRESHOLD", "2000")
    monkeypatch.setenv("MAGI_COMPACTION_TAIL_EVENTS", "16")

    bundle = local_runner.build_local_adk_runner()
    pm = bundle.runner.plugin_manager
    assert CONTEXT_COMPACTION_PLUGIN_NAME in {p.name for p in pm.plugins}

    async def _drive() -> LlmRequest:
        cc = await _callback_context(bundle)
        req = _big_request(40)
        # The genuine ADK dispatch path the live turn engine calls.
        out = await pm.run_before_model_callback(callback_context=cc, llm_request=req)
        assert out is None  # no short-circuit; request proceeds (mutated)
        return req

    req = _run(_drive())
    assert len(req.contents) == 16  # compacted before the model call


def test_runner_flag_off_attaches_no_compaction_plugin_and_leaves_contents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CORE_AGENT_PYTHON_LOCAL_ADK_RUNNER", "1")
    monkeypatch.delenv("MAGI_CONTEXT_COMPACTION_ENABLED", raising=False)

    bundle = local_runner.build_local_adk_runner()
    pm = bundle.runner.plugin_manager
    assert CONTEXT_COMPACTION_PLUGIN_NAME not in {p.name for p in pm.plugins}

    async def _drive() -> LlmRequest:
        cc = await _callback_context(bundle)
        req = _big_request(40)
        out = await pm.run_before_model_callback(callback_context=cc, llm_request=req)
        assert out is None
        return req

    req = _run(_drive())
    assert len(req.contents) == 40  # untouched — zero regression


def test_build_context_compaction_plugin_disabled_returns_none() -> None:
    assert (
        build_context_compaction_plugin(
            enabled=False, token_threshold=24_000, tail_events=16
        )
        is None
    )


def test_build_context_compaction_plugin_enabled_returns_plugin() -> None:
    plugin = build_context_compaction_plugin(
        enabled=True, token_threshold=24_000, tail_events=16
    )
    assert isinstance(plugin, MagiContextCompactionPlugin)
    assert plugin.token_threshold == 24_000
    assert plugin.tail_events == 16
