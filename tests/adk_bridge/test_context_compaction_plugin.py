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
from magi_agent.shared.token_estimation import count_text_tokens


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


def test_tail_widens_past_parallel_tool_response_run() -> None:
    # Realistic genai shape for parallel tool calls: one assistant Content emits
    # BOTH calls (A and B), then each tool produces a SEPARATE function_response
    # Content. A naive last-N split can land in the MIDDLE of that response run
    # (keeping response B but not response A and not the originating call); the
    # widening must walk back across the whole response run to the call Content.
    plugin = MagiContextCompactionPlugin(token_threshold=2_000, tail_events=2)
    contents = [_content(i, "x" * 1600) for i in range(15)]
    contents.append(  # index 15 — single assistant Content with parallel calls
        types.Content(
            role="model",
            parts=[
                types.Part(
                    function_call=types.FunctionCall(name="Read", args={"path": "a"})
                ),
                types.Part(
                    function_call=types.FunctionCall(name="Read", args={"path": "b"})
                ),
            ],
        )
    )
    contents.append(  # index 16 — response A
        types.Content(
            role="user",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        name="Read", response={"a": True}
                    )
                )
            ],
        )
    )
    contents.append(  # index 17 — response B (naive split at 18-2=16 lands here-ish)
        types.Content(
            role="user",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        name="Read", response={"b": True}
                    )
                )
            ],
        )
    )
    contents.append(_content(18, "x" * 1600))  # index 18 — assistant text
    req = LlmRequest()
    req.contents = contents  # 19 contents; tail_events=2 -> naive split at 17

    _run(plugin.before_model_callback(callback_context=None, llm_request=req))

    # No kept content may be an orphaned function_response.
    for content in req.contents:
        responses = [
            p for p in (content.parts or [])
            if getattr(p, "function_response", None) is not None
        ]
        if responses:
            # any kept response must be preceded (in the kept window) by its call
            assert req.contents[0].parts[0].function_call is not None
    # Widening walked back across the response run to the originating call Content.
    first = req.contents[0]
    assert first.parts[0].function_call is not None
    # call (15) + respA (16) + respB (17) + text (18) == 4 kept.
    assert len(req.contents) == 4


def test_tail_entirely_function_responses_widens_to_originating_call() -> None:
    # Edge case: the entire tail window is function_responses. Widening must walk
    # back past the whole run until it reaches the originating function_call.
    plugin = MagiContextCompactionPlugin(token_threshold=2_000, tail_events=3)
    contents = [_content(i, "x" * 1600) for i in range(10)]
    contents.append(  # index 10 — the call
        types.Content(
            role="model",
            parts=[
                types.Part(
                    function_call=types.FunctionCall(name="Grep", args={"q": "x"})
                )
            ],
        )
    )
    # indices 11,12,13 — a run of three responses (the whole tail window).
    for _ in range(3):
        contents.append(
            types.Content(
                role="user",
                parts=[
                    types.Part(
                        function_response=types.FunctionResponse(
                            name="Grep", response={"ok": True}
                        )
                    )
                ],
            )
        )
    req = LlmRequest()
    req.contents = contents  # 14 contents; tail_events=3 -> naive split at 11

    _run(plugin.before_model_callback(callback_context=None, llm_request=req))

    # The kept window must START with the function_call, not an orphan response.
    first = req.contents[0]
    assert first.parts[0].function_call is not None
    assert not any(
        getattr(p, "function_response", None) is not None for p in (first.parts or [])
    )
    # call (10) + 3 responses == 4 kept (widened from 3).
    assert len(req.contents) == 4


def test_reused_session_does_not_accumulate_provenance_events() -> None:
    # Item 4: the plugin caches a single session_service+session across calls.
    # The boundary appends a provenance event on every "compacted" decision, so
    # without clearing, session.events would grow unboundedly. Assert the reused
    # session's event log stays bounded across many over-budget calls.
    plugin = MagiContextCompactionPlugin(token_threshold=2_000, tail_events=8)

    async def _drive() -> object:
        for _ in range(25):
            req = _big_request(40)
            await plugin.before_model_callback(
                callback_context=None, llm_request=req
            )
            assert len(req.contents) == 8  # each call actually compacted
        # Inspect the cached session: events must not have grown with call count.
        _service, session, _state = plugin._decision_cache  # type: ignore[misc]
        return session

    session = _run(_drive())
    assert len(session.events) <= 1  # bounded — cleared each call


def test_non_text_part_estimate_uses_json_basis() -> None:
    # Item 3: function_call/response parts are estimated from model_dump_json(),
    # aligning the non-text basis with the json.dumps basis used by
    # estimate_message_tokens (rather than the pydantic str(part) repr).
    from magi_agent.adk_bridge.context_compaction import _content_token_estimate

    content = types.Content(
        role="model",
        parts=[
            types.Part(
                function_call=types.FunctionCall(
                    name="Read", args={"path": "some/long/path/to/a/file.py"}
                )
            )
        ],
    )
    part = content.parts[0]
    json_form = part.model_dump_json()
    repr_form = str(part)
    # The two serialisations differ — the fix deliberately uses the JSON one.
    assert json_form != repr_form

    estimate = _content_token_estimate(content)
    # Estimate is driven by the JSON form (+ the role token), NOT the repr form.
    expected = count_text_tokens(json_form) + count_text_tokens("model")
    assert estimate == expected
    assert estimate != count_text_tokens(repr_form) + count_text_tokens("model")


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

    # After PR2 (control-plane), the compaction plugin is wrapped inside the
    # ControlPlanePlugin as a _CompactionLoopControl adapter. The top-level plugin
    # name is CONTROL_PLANE_PLUGIN_NAME; the compaction logic is still reachable
    # via the plane's before_model fan-out.
    from magi_agent.adk_bridge.control_plane import (
        CONTROL_PLANE_PLUGIN_NAME,
        _CompactionLoopControl,
    )
    plane_plugin = next(p for p in pm.plugins if p.name == CONTROL_PLANE_PLUGIN_NAME)
    assert any(
        isinstance(c, _CompactionLoopControl) for c in plane_plugin._p._controls
    ), "compaction control not found in plane"

    async def _drive() -> LlmRequest:
        cc = await _callback_context(bundle)
        req = _big_request(40)
        # The genuine ADK dispatch path the live turn engine calls.
        out = await pm.run_before_model_callback(callback_context=cc, llm_request=req)
        assert out is None  # no short-circuit; request proceeds (mutated)
        return req

    req = _run(_drive())
    assert len(req.contents) == 16  # compacted before the model call


def test_runner_explicit_flag_off_attaches_no_compaction_plugin_and_leaves_contents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CORE_AGENT_PYTHON_LOCAL_ADK_RUNNER", "1")
    monkeypatch.setenv("MAGI_CONTEXT_COMPACTION_ENABLED", "0")

    bundle = local_runner.build_local_adk_runner()
    pm = bundle.runner.plugin_manager

    # After PR2: the compaction LoopControl is not registered when flag is OFF.
    from magi_agent.adk_bridge.control_plane import (
        CONTROL_PLANE_PLUGIN_NAME,
        _CompactionLoopControl,
    )
    plane_plugin = next(p for p in pm.plugins if p.name == CONTROL_PLANE_PLUGIN_NAME)
    assert not any(
        isinstance(c, _CompactionLoopControl) for c in plane_plugin._p._controls
    ), "compaction control should not be in plane when flag is off"

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


# ---------------------------------------------------------------------------
# G2 — real-token accounting (%-of-window threshold), default-OFF
# ---------------------------------------------------------------------------


class _State:
    """Minimal CallbackContext.state stand-in (dict-backed get/set)."""

    def __init__(self) -> None:
        self._d: dict = {}

    def get(self, key, default=None):  # noqa: ANN001
        return self._d.get(key, default)

    def __setitem__(self, key, value) -> None:  # noqa: ANN001
        self._d[key] = value

    def __getitem__(self, key):  # noqa: ANN001
        return self._d[key]

    def __contains__(self, key) -> bool:  # noqa: ANN001
        return key in self._d


class _CbCtx:
    def __init__(self) -> None:
        self.state = _State()


def _g2_plugin(**kw) -> MagiContextCompactionPlugin:  # noqa: ANN003
    defaults: dict = dict(
        token_threshold=24_000,
        tail_events=16,
        real_tokens_enabled=True,
        real_tokens_pct=0.75,
        output_reserve=8_000,
    )
    defaults.update(kw)
    return MagiContextCompactionPlugin(**defaults)


def _model_request(count: int, *, chars: int, model: str | None) -> LlmRequest:
    req = LlmRequest()
    req.contents = [_content(i, "x" * chars) for i in range(count)]
    if model is not None:
        req.model = model
    return req


def test_after_model_stashes_real_prompt_tokens_on_state() -> None:
    plugin = _g2_plugin()
    ctx = _CbCtx()

    class _Resp:
        usage_metadata = type("U", (), {"prompt_token_count": 120_000})()

    _run(plugin.after_model_callback(callback_context=ctx, llm_response=_Resp()))

    from magi_agent.adk_bridge.context_compaction import (
        REAL_PROMPT_TOKENS_STATE_KEY,
    )

    assert ctx.state.get(REAL_PROMPT_TOKENS_STATE_KEY) == 120_000


def test_flag_off_after_model_is_noop() -> None:
    # The after-model capture writes NOTHING to state when the real-token path
    # is OFF, so the state key is absent.
    plugin = MagiContextCompactionPlugin(
        token_threshold=24_000, tail_events=16, real_tokens_enabled=False
    )
    ctx = _CbCtx()

    class _Resp:
        usage_metadata = type("U", (), {"prompt_token_count": 120_000})()

    _run(plugin.after_model_callback(callback_context=ctx, llm_response=_Resp()))

    from magi_agent.adk_bridge.context_compaction import (
        REAL_PROMPT_TOKENS_STATE_KEY,
    )

    assert REAL_PROMPT_TOKENS_STATE_KEY not in ctx.state


def test_real_tokens_above_pct_threshold_compacts() -> None:
    # window 150_000, reserve 8_000, pct 0.75 -> threshold 106_500.
    # 120_000 real prompt tokens > threshold -> trim, even though the
    # char-estimate of these few small contents is UNDER the fixed 24k.
    plugin = _g2_plugin()
    ctx = _CbCtx()

    class _Resp:
        usage_metadata = type("U", (), {"prompt_token_count": 120_000})()

    _run(plugin.after_model_callback(callback_context=ctx, llm_response=_Resp()))

    # Small contents (tiny char-estimate) but many of them.
    req = _model_request(40, chars=10, model="claude-sonnet-4-6")
    out = _run(
        plugin.before_model_callback(callback_context=ctx, llm_request=req)
    )
    assert out is None
    assert len(req.contents) == 16  # real-token signal forced the tail-trim


def test_real_tokens_below_pct_threshold_no_compact() -> None:
    # 50_000 < 106_500 -> contents untouched even with a large content count.
    plugin = _g2_plugin()
    ctx = _CbCtx()

    class _Resp:
        usage_metadata = type("U", (), {"prompt_token_count": 50_000})()

    _run(plugin.after_model_callback(callback_context=ctx, llm_response=_Resp()))

    req = _model_request(40, chars=10, model="claude-sonnet-4-6")
    before = list(req.contents)
    out = _run(
        plugin.before_model_callback(callback_context=ctx, llm_request=req)
    )
    assert out is None
    assert req.contents == before
    assert len(req.contents) == 40


def test_fail_open_when_tokens_missing() -> None:
    # Flag ON but no usage_metadata ever stashed -> falls back to the estimate +
    # fixed-threshold path; identical to flag-OFF for the same request.
    plugin = _g2_plugin(token_threshold=2_000)
    ctx = _CbCtx()  # state never populated (no after_model call)

    req = _model_request(40, chars=1600, model="claude-sonnet-4-6")
    original_last = req.contents[-1].parts[0].text
    out = _run(
        plugin.before_model_callback(callback_context=ctx, llm_request=req)
    )
    assert out is None
    # estimate path with token_threshold=2000 over-budget -> tail-trim to 16.
    assert len(req.contents) == 16
    assert req.contents[-1].parts[0].text == original_last


def test_unknown_model_uses_default_window() -> None:
    # Unknown model id -> window resolves to the 150_000 default; threshold
    # computed from it (106_500). 120_000 > threshold -> compact, no crash.
    plugin = _g2_plugin()
    ctx = _CbCtx()

    class _Resp:
        usage_metadata = type("U", (), {"prompt_token_count": 120_000})()

    _run(plugin.after_model_callback(callback_context=ctx, llm_response=_Resp()))

    req = _model_request(40, chars=10, model="something/unknown")
    out = _run(
        plugin.before_model_callback(callback_context=ctx, llm_request=req)
    )
    assert out is None
    assert len(req.contents) == 16


def test_zero_or_negative_effective_window_falls_back() -> None:
    # output_reserve >= window -> (window - reserve) < 1 -> fall back to the
    # fixed token_threshold (fail-open; no ZeroDivision / negative threshold).
    plugin = _g2_plugin(token_threshold=2_000, output_reserve=200_000)
    ctx = _CbCtx()

    class _Resp:
        usage_metadata = type("U", (), {"prompt_token_count": 120_000})()

    _run(plugin.after_model_callback(callback_context=ctx, llm_response=_Resp()))

    # Big char contents so the FIXED 2_000 estimate path breaches and trims.
    req = _model_request(40, chars=1600, model="claude-sonnet-4-6")
    out = _run(
        plugin.before_model_callback(callback_context=ctx, llm_request=req)
    )
    assert out is None
    assert len(req.contents) == 16


def test_flag_off_is_byte_identical() -> None:
    # With the real-token path OFF, a 40-content over-fixed-threshold request
    # trims to exactly tail_events via the estimate path — same shape as
    # test_over_threshold_context_is_compacted_to_recent_tail.
    plugin = MagiContextCompactionPlugin(
        token_threshold=2_000, tail_events=16, real_tokens_enabled=False
    )
    ctx = _CbCtx()

    # Even if a value somehow lands on state, the OFF guard must ignore it.
    from magi_agent.adk_bridge.context_compaction import (
        REAL_PROMPT_TOKENS_STATE_KEY,
    )

    ctx.state[REAL_PROMPT_TOKENS_STATE_KEY] = 50_000  # would be UNDER threshold

    req = _big_request(40)
    original_last = req.contents[-1].parts[0].text
    original_split = req.contents[24].parts[0].text

    out = _run(
        plugin.before_model_callback(callback_context=ctx, llm_request=req)
    )
    assert out is None
    assert len(req.contents) == 16
    assert req.contents[-1].parts[0].text == original_last
    assert req.contents[0].parts[0].text == original_split


def test_real_tokens_default_constructor_is_off() -> None:
    # Constructing without the new kwargs keeps the real-token path OFF, so the
    # legacy two-arg construction stays byte-identical.
    plugin = MagiContextCompactionPlugin(token_threshold=24_000, tail_events=16)
    assert plugin._real_tokens_enabled is False
