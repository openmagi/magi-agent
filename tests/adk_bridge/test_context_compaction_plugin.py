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

    # U1 pin: the last genuine user-text turn (index 8) is preserved ahead of the
    # widened tail, so contents START with that user turn (never an orphan
    # response). The orphan-widened function_call sits right after the pin.
    first = req.contents[0]
    assert first.role == "user"
    assert first.parts[0].text == "x" * 1600
    assert first.parts[0].function_call is None
    assert not any(
        getattr(p, "function_response", None) is not None for p in (first.parts or [])
    )
    # The widening still included the originating call before its responses.
    assert req.contents[1].parts[0].function_call is not None
    # pin (1) + call (10) + 3 responses == 5 kept (widened from 3, plus the pin).
    assert len(req.contents) == 5


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


# ---------------------------------------------------------------------------
# G4 — deterministic tool-output prune pre-tier, default-OFF
# ---------------------------------------------------------------------------

from magi_agent.adk_bridge.context_compaction import (  # noqa: E402
    _PRUNED_TOOL_OUTPUT_PLACEHOLDER,
)


def _fn_response_content(name: str, *, chars: int, role: str = "user") -> types.Content:
    """One Content carrying a single function_response with a large payload."""
    return types.Content(
        role=role,
        parts=[
            types.Part(
                function_response=types.FunctionResponse(
                    name=name, response={"data": "y" * chars}
                )
            )
        ],
    )


def _prune_plugin(**kw) -> MagiContextCompactionPlugin:  # noqa: ANN003
    defaults: dict = dict(
        token_threshold=1_000_000,  # estimate path never trips by default
        tail_events=2,
        tool_prune_enabled=True,
        prune_protect=40_000,
        prune_minimum=20_000,
    )
    defaults.update(kw)
    return MagiContextCompactionPlugin(**defaults)


def _response_payload(content: types.Content) -> object:
    return content.parts[0].function_response.response


def _is_pruned(content: types.Content) -> bool:
    return _response_payload(content) == _PRUNED_TOOL_OUTPUT_PLACEHOLDER


def test_g4_off_byte_identical_no_prune() -> None:
    # Flag OFF: a request with large OLD function_response payloads is reduced
    # EXACTLY as Phase-1 (estimate path), with NO payload mutation. Compared to a
    # flag-OFF baseline run on identical inputs.
    def _build() -> LlmRequest:
        req = LlmRequest()
        contents = [_fn_response_content("Read", chars=8_000) for _ in range(20)]
        req.contents = contents
        return req

    # Baseline: prune flag OFF, estimate path trips (token_threshold low).
    baseline = MagiContextCompactionPlugin(token_threshold=2_000, tail_events=4)
    req_base = _build()
    _run(baseline.before_model_callback(callback_context=None, llm_request=req_base))

    candidate = MagiContextCompactionPlugin(
        token_threshold=2_000,
        tail_events=4,
        tool_prune_enabled=False,
    )
    req_cand = _build()
    _run(candidate.before_model_callback(callback_context=None, llm_request=req_cand))

    assert len(req_cand.contents) == len(req_base.contents)
    for a, b in zip(req_cand.contents, req_base.contents):
        assert _response_payload(a) == _response_payload(b)
    # No payload was ever cleared (OFF path mutates nothing).
    assert not any(_is_pruned(c) for c in req_cand.contents)


def test_g4_protected_tool_not_pruned() -> None:
    from magi_agent.harness.general_automation.constants import (
        LOAD_GA_RECIPE_TOOL_NAME,
    )

    plugin = _prune_plugin(prune_protect=1, prune_minimum=1)
    req = LlmRequest()
    contents = [_fn_response_content("Read", chars=40_000) for _ in range(3)]
    # A protected tool result in the OLD (prune-eligible) region.
    contents.insert(0, _fn_response_content(LOAD_GA_RECIPE_TOOL_NAME, chars=40_000))
    # Tail (last 2) — protected by count layer; add filler so old region exists.
    contents.append(_content(98, "x" * 10))
    contents.append(_content(99, "x" * 10))
    req.contents = contents

    _run(plugin.before_model_callback(callback_context=None, llm_request=req))

    # The protected tool's payload is intact even though other olds were cleared.
    assert _response_payload(req.contents[0]) == {"data": "y" * 40_000}


def test_g4_recent_tail_not_pruned_count_layer() -> None:
    # Last tail_events Contents' function_responses are never cleared even with a
    # tiny prune_protect (count-layer protection).
    plugin = _prune_plugin(tail_events=3, prune_protect=1, prune_minimum=1)
    req = LlmRequest()
    contents = [_fn_response_content("Read", chars=40_000) for _ in range(6)]
    req.contents = contents

    _run(plugin.before_model_callback(callback_context=None, llm_request=req))

    # Last 3 (count-protected) keep full payloads.
    for c in req.contents[-3:]:
        assert not _is_pruned(c)


def test_g4_recent_tail_not_pruned_token_layer() -> None:
    # prune_protect large enough that the most-recent outputs (beyond the count
    # tail) are token-protected and keep payloads, while older ones are cleared.
    plugin = _prune_plugin(tail_events=1, prune_protect=40_000, prune_minimum=1)
    req = LlmRequest()
    # 8 results each ~big; the most-recent ~40k tokens of output are protected.
    contents = [_fn_response_content("Read", chars=40_000) for _ in range(8)]
    req.contents = contents

    _run(plugin.before_model_callback(callback_context=None, llm_request=req))

    # The newest results stay; the oldest get cleared.
    assert not _is_pruned(req.contents[-1])
    assert _is_pruned(req.contents[0])


def test_g4_freed_below_minimum_is_noop() -> None:
    # Old outputs sum to < PRUNE_MINIMUM freed tokens -> contents returned
    # unchanged (same object identity), and downstream Phase-1 still runs.
    plugin = _prune_plugin(prune_minimum=1_000_000, prune_protect=1, tail_events=2)
    req = LlmRequest()
    contents = [_fn_response_content("Read", chars=8_000) for _ in range(10)]
    original = list(contents)
    req.contents = contents

    _run(plugin.before_model_callback(callback_context=None, llm_request=req))

    # Nothing cleared (freed < minimum).
    assert req.contents == original
    assert not any(_is_pruned(c) for c in req.contents)


def test_g4_prune_avoids_tail_drop() -> None:
    # A request that WOULD trip the estimate path before pruning falls under the
    # threshold after the prune frees >= minimum, so _apply_tail_trim is NOT
    # invoked: contents length is unchanged, only old payloads cleared.
    # Build: many large OLD tool outputs. token_threshold sized so that AFTER
    # clearing the old payloads the total estimate is under threshold.
    plugin = _prune_plugin(
        token_threshold=80_000,  # over before prune, under after
        tail_events=2,
        prune_protect=8_000,
        prune_minimum=20_000,
    )
    req = LlmRequest()
    # 10 old big results (~10k tokens each) + 2 tiny tail contents.
    contents = [_fn_response_content("Read", chars=40_000) for _ in range(10)]
    contents.append(_content(98, "x" * 10))
    contents.append(_content(99, "x" * 10))
    req.contents = contents
    n_before = len(contents)

    _run(plugin.before_model_callback(callback_context=None, llm_request=req))

    # Tail-drop NOT invoked: count unchanged.
    assert len(req.contents) == n_before
    # But old payloads were cleared.
    assert any(_is_pruned(c) for c in req.contents[:-2])


def test_g4_prune_commits_but_still_over_budget() -> None:
    # Prune frees >= minimum yet the request remains over threshold -> Phase-1
    # tail-drop still runs on the pruned contents (composition correctness).
    # Large protected-tail TEXT contents keep the estimate over threshold even
    # after the old tool outputs are cleared.
    plugin = _prune_plugin(
        token_threshold=2_000,
        tail_events=4,
        prune_protect=8_000,
        prune_minimum=20_000,
    )
    req = LlmRequest()
    contents = [_fn_response_content("Read", chars=40_000) for _ in range(20)]
    # Append big TEXT tail contents (not function_responses, so never pruned);
    # their estimate alone keeps the request over the 2_000 threshold.
    for i in range(4):
        contents.append(_content(i, "x" * 40_000))
    req.contents = contents

    _run(plugin.before_model_callback(callback_context=None, llm_request=req))

    # Tail-drop ran on the pruned contents: reduced to tail_events.
    assert len(req.contents) == 4
    # And the kept tail is the big text contents (never pruned).
    assert req.contents[-1].parts[0].text == "x" * 40_000


def test_g4_placeholder_shape_and_pairing_intact() -> None:
    plugin = _prune_plugin(
        tail_events=2, prune_protect=8_000, prune_minimum=20_000
    )
    req = LlmRequest()
    contents = [_fn_response_content("Read", chars=40_000) for _ in range(10)]
    req.contents = contents
    n_before = len(contents)
    n_parts_before = [len(c.parts) for c in contents]

    _run(plugin.before_model_callback(callback_context=None, llm_request=req))

    assert len(req.contents) == n_before
    for c, n in zip(req.contents, n_parts_before):
        assert len(c.parts) == n  # part count unchanged (no deletion)
    # A cleared result keeps function_response, same .name, placeholder payload.
    cleared = next(c for c in req.contents if _is_pruned(c))
    fr = cleared.parts[0].function_response
    assert fr is not None
    assert fr.name == "Read"
    assert fr.response == {"pruned": "[old tool output cleared to save context]"}


def test_g4_per_result_minimum_skips_trivial_outputs() -> None:
    # A small old function_response below the per-result minimum is left untouched
    # (avoid clearing trivial outputs), even when other results meet the budget.
    plugin = _prune_plugin(
        tail_events=2, prune_protect=8_000, prune_minimum=20_000
    )
    req = LlmRequest()
    contents = [_fn_response_content("Read", chars=40_000) for _ in range(8)]
    # A trivial old result at index 0.
    contents.insert(0, _fn_response_content("Read", chars=10))
    req.contents = contents

    _run(plugin.before_model_callback(callback_context=None, llm_request=req))

    # The trivial old output is NOT cleared.
    assert _response_payload(req.contents[0]) == {"data": "y" * 10}


def test_g4_fail_open_on_malformed_content() -> None:
    # A malformed part during prune (model_dump_json raises) leaves all contents
    # untouched and the turn proceeds (no raise).
    plugin = _prune_plugin(
        tail_events=2, prune_protect=1, prune_minimum=1, token_threshold=1_000_000
    )

    class _BadResponse:
        name = "Read"

        @property
        def response(self):  # noqa: ANN001
            raise RuntimeError("boom")

    class _BadPart:
        function_response = _BadResponse()
        text = None
        function_call = None

    class _BadContent:
        role = "user"
        parts = [_BadPart()]

    req = LlmRequest()
    good = [_fn_response_content("Read", chars=40_000) for _ in range(5)]
    # Mix a malformed content into the old region.
    req.contents = [_BadContent()] + good  # type: ignore[list-item]

    out = _run(
        plugin.before_model_callback(callback_context=None, llm_request=req)
    )
    assert out is None  # never raises
    # Good function_responses untouched (fail-open leaves contents as-is).
    # Skip the malformed sentinel content (index 0) whose .response raises.
    for c in req.contents[1:]:
        fr = getattr(c.parts[0], "function_response", None)
        assert fr is not None
        assert fr.response == {"data": "y" * 40_000}


def test_g4_builder_additive_default_off() -> None:
    # build_context_compaction_plugin without the new kwargs keeps prune OFF.
    plugin = build_context_compaction_plugin(
        enabled=True, token_threshold=24_000, tail_events=16
    )
    assert isinstance(plugin, MagiContextCompactionPlugin)
    assert plugin._tool_prune_enabled is False
    assert plugin._prune_protect == 40_000
    assert plugin._prune_minimum == 20_000


def test_g4_builder_forwards_prune_kwargs() -> None:
    plugin = build_context_compaction_plugin(
        enabled=True,
        token_threshold=24_000,
        tail_events=16,
        tool_prune_enabled=True,
        prune_protect=50_000,
        prune_minimum=10_000,
    )
    assert isinstance(plugin, MagiContextCompactionPlugin)
    assert plugin._tool_prune_enabled is True
    assert plugin._prune_protect == 50_000
    assert plugin._prune_minimum == 10_000


def test_g4_rejects_invalid_prune_bounds() -> None:
    with pytest.raises(ValueError):
        MagiContextCompactionPlugin(
            token_threshold=1, tail_events=1, tool_prune_enabled=True, prune_protect=0
        )
    with pytest.raises(ValueError):
        MagiContextCompactionPlugin(
            token_threshold=1, tail_events=1, tool_prune_enabled=True, prune_minimum=0
        )


# ---------------------------------------------------------------------------
# G1 — summary injection + G8 protected-tool-as-text preserve (default-OFF)
# ---------------------------------------------------------------------------

import magi_agent.adk_bridge.context_compaction as _cc  # noqa: E402


class _FakeProviderConfig:
    litellm_model = "anthropic/claude-haiku-4-5"
    api_key = "test-key"


class _FakeLlmResponse:
    def __init__(self, text: str) -> None:
        self.content = types.Content(role="model", parts=[types.Part(text=text)])


class _FakeSummaryModel:
    """Async-generator model mirroring the ADK generate_content_async contract."""

    def __init__(self, text: str = "SUMMARY", *, sleep: float = 0.0, raises: bool = False) -> None:
        self._text = text
        self._sleep = sleep
        self._raises = raises
        self.calls = 0

    async def generate_content_async(self, llm_request, stream=False):  # noqa: ANN001
        self.calls += 1
        self.captured_request = llm_request
        if self._raises:
            raise RuntimeError("boom")
        if self._sleep:
            await asyncio.sleep(self._sleep)
        yield _FakeLlmResponse(self._text)


def _patch_summarizer(
    monkeypatch,
    *,
    provider=_FakeProviderConfig(),
    model=None,
):
    """Patch resolve_provider_config + _build_litellm_for_config in context_compaction.

    The two helpers are function-local imports inside context_compaction, so we
    patch them at their SOURCE modules. Returns the fake model so tests can assert
    call counts / captured prompts.
    """
    import magi_agent.cli.providers as providers_mod
    import magi_agent.cli.readonly_classifier as roc_mod

    fake_model = model if model is not None else _FakeSummaryModel()

    def _resolve():  # noqa: ANN202
        return provider

    def _build(provider_config, *, model_override=None):  # noqa: ANN001, ANN202
        fake_model.last_model_override = model_override
        return fake_model

    monkeypatch.setattr(providers_mod, "resolve_provider_config", _resolve)
    monkeypatch.setattr(roc_mod, "_build_litellm_for_config", _build)
    return fake_model


def _summary_plugin(**kw) -> MagiContextCompactionPlugin:  # noqa: ANN003
    defaults: dict = dict(
        token_threshold=2_000,
        tail_events=16,
        summarize_enabled=True,
    )
    defaults.update(kw)
    return MagiContextCompactionPlugin(**defaults)


def test_g1_off_byte_identical_pure_drop_no_llm_call(monkeypatch) -> None:  # noqa: ANN001
    # Flag OFF (default ctor): over-threshold request reduces to the pure-drop
    # tail, byte-identical to today, and the summary model is NEVER built.
    def _fail_build(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("summary model must not be built when flag is OFF")

    import magi_agent.cli.readonly_classifier as roc_mod

    monkeypatch.setattr(roc_mod, "_build_litellm_for_config", _fail_build)

    plugin = MagiContextCompactionPlugin(token_threshold=2_000, tail_events=16)
    req = _big_request(40)
    original_last = req.contents[-1].parts[0].text
    original_split = req.contents[24].parts[0].text  # 40 - 16 == 24

    _run(plugin.before_model_callback(callback_context=None, llm_request=req))

    assert len(req.contents) == 16
    assert req.contents[-1].parts[0].text == original_last
    assert req.contents[0].parts[0].text == original_split


def test_g1_summary_injected_happy_path(monkeypatch) -> None:  # noqa: ANN001
    fake = _patch_summarizer(monkeypatch, model=_FakeSummaryModel("SUMMARY"))
    plugin = _summary_plugin()
    req = _big_request(40)
    original_last = req.contents[-1].parts[0].text
    original_split = req.contents[24].parts[0].text

    _run(plugin.before_model_callback(callback_context=None, llm_request=req))

    # Head: summary content + kept tail (no protected tools -> len == 1 + 16).
    assert len(req.contents) == 1 + 16
    assert req.contents[0].role == "user"
    assert req.contents[0].parts[0].text.startswith(
        "[Previous conversation summary]\n\nSUMMARY"
    )
    # Kept tail byte-identical to the pure-drop tail.
    tail = req.contents[-16:]
    assert tail[-1].parts[0].text == original_last
    assert tail[0].parts[0].text == original_split
    assert fake.calls == 1


def test_g1_fail_open_no_provider(monkeypatch) -> None:  # noqa: ANN001
    _patch_summarizer(monkeypatch, provider=None)
    plugin = _summary_plugin()
    req = _big_request(40)
    original_split = req.contents[24].parts[0].text

    _run(plugin.before_model_callback(callback_context=None, llm_request=req))

    # Pure tail-drop: no summary head.
    assert len(req.contents) == 16
    assert req.contents[0].parts[0].text == original_split


def test_g1_fail_open_timeout(monkeypatch) -> None:  # noqa: ANN001
    _patch_summarizer(monkeypatch, model=_FakeSummaryModel(sleep=1.0))
    plugin = _summary_plugin(summary_timeout=0.01)
    req = _big_request(40)
    original_split = req.contents[24].parts[0].text

    _run(plugin.before_model_callback(callback_context=None, llm_request=req))

    assert len(req.contents) == 16
    assert req.contents[0].parts[0].text == original_split


def test_g1_fail_open_generate_error(monkeypatch) -> None:  # noqa: ANN001
    _patch_summarizer(monkeypatch, model=_FakeSummaryModel(raises=True))
    plugin = _summary_plugin()
    req = _big_request(40)
    original_split = req.contents[24].parts[0].text

    out = _run(
        plugin.before_model_callback(callback_context=None, llm_request=req)
    )
    assert out is None
    assert len(req.contents) == 16
    assert req.contents[0].parts[0].text == original_split


def test_g8_protected_tool_preserved_as_text(monkeypatch) -> None:  # noqa: ANN001
    from magi_agent.harness.general_automation.constants import (
        LOAD_GA_RECIPE_TOOL_NAME,
    )

    _patch_summarizer(monkeypatch, model=_FakeSummaryModel("S"))
    plugin = _summary_plugin(tail_events=4)
    req = LlmRequest()
    # Dropped region: a protected tool result + filler; tail = last 4 text.
    contents = [_content(i, "x" * 1600) for i in range(10)]
    contents.insert(0, _fn_response_content(LOAD_GA_RECIPE_TOOL_NAME, chars=4_000))
    req.contents = contents

    _run(plugin.before_model_callback(callback_context=None, llm_request=req))

    # [summary, protected_text, *tail(4)].
    assert len(req.contents) == 1 + 1 + 4
    summary_c, protected_c = req.contents[0], req.contents[1]
    assert summary_c.parts[0].text.startswith("[Previous conversation summary]")
    assert protected_c.role == "user"
    assert protected_c.parts[0].text.startswith(
        f"[Preserved tool output: {LOAD_GA_RECIPE_TOOL_NAME}]"
    )
    # NO function_response part anywhere in the injected head (protocol-valid).
    for c in (summary_c, protected_c):
        for p in c.parts or []:
            assert getattr(p, "function_response", None) is None


def test_g8_non_protected_not_preserved(monkeypatch) -> None:  # noqa: ANN001
    _patch_summarizer(monkeypatch, model=_FakeSummaryModel("S"))
    plugin = _summary_plugin(tail_events=4)
    req = LlmRequest()
    contents = [_content(i, "x" * 1600) for i in range(10)]
    # A non-protected function_response in the dropped region.
    contents.insert(0, _fn_response_content("Bash", chars=4_000))
    req.contents = contents

    _run(plugin.before_model_callback(callback_context=None, llm_request=req))

    # Only summary head + tail (no preserved-text Content for Bash).
    assert len(req.contents) == 1 + 4
    assert not any(
        (c.parts and c.parts[0].text or "").startswith("[Preserved tool output:")
        for c in req.contents
    )


def test_g1_summarizer_not_called_on_no_op_turn(monkeypatch) -> None:  # noqa: ANN001
    # Under-threshold (no compaction) must NOT resolve/build the summary model.
    import magi_agent.cli.providers as providers_mod
    import magi_agent.cli.readonly_classifier as roc_mod

    def _fail_resolve():  # noqa: ANN202
        raise AssertionError("resolve_provider_config must not run on a no-op turn")

    def _fail_build(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("_build_litellm_for_config must not run on a no-op turn")

    monkeypatch.setattr(providers_mod, "resolve_provider_config", _fail_resolve)
    monkeypatch.setattr(roc_mod, "_build_litellm_for_config", _fail_build)

    plugin = _summary_plugin(token_threshold=1_000_000)
    req = _big_request(40)
    before = list(req.contents)

    _run(plugin.before_model_callback(callback_context=None, llm_request=req))

    assert req.contents == before  # untouched (no compaction)


def test_g1_protocol_no_orphan_after_injection(monkeypatch) -> None:  # noqa: ANN001
    # After ON-summary injection on a request whose naive tail begins with an
    # orphaned function_response, the FIRST content has no function_response part
    # and the kept tail still begins on a non-orphan boundary.
    _patch_summarizer(monkeypatch, model=_FakeSummaryModel("S"))
    plugin = MagiContextCompactionPlugin(
        token_threshold=2_000, tail_events=4, summarize_enabled=True
    )
    req = LlmRequest()
    contents = [_content(i, "x" * 1600) for i in range(15)]
    contents.append(  # index 15 — the call
        types.Content(
            role="model",
            parts=[types.Part(function_call=types.FunctionCall(name="Read", args={}))],
        )
    )
    contents.append(  # index 16 — the response (naive split lands here)
        types.Content(
            role="user",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(name="Read", response={"ok": 1})
                )
            ],
        )
    )
    contents.extend(_content(i, "x" * 1600) for i in range(17, 20))
    req.contents = contents

    _run(plugin.before_model_callback(callback_context=None, llm_request=req))

    # First content is the summary head (no function_response part).
    first = req.contents[0]
    assert not any(
        getattr(p, "function_response", None) is not None for p in (first.parts or [])
    )
    # The originating call survived in the kept tail (orphan-widened before head).
    assert any(
        getattr(p, "function_call", None) is not None
        for c in req.contents
        for p in (c.parts or [])
    )


def test_g1_transcript_bounded(monkeypatch) -> None:  # noqa: ANN001
    fake = _patch_summarizer(monkeypatch, model=_FakeSummaryModel("S"))
    plugin = _summary_plugin(tail_events=2)
    req = LlmRequest()
    # Put the tool parts FIRST (within the cap) so their descriptors are rendered,
    # then append a huge tail of text that overflows the transcript cap.
    contents: list = [
        _fn_response_content("Bash", chars=200),
        types.Content(
            role="model",
            parts=[types.Part(function_call=types.FunctionCall(name="Read"))],
        ),
    ]
    contents += [_content(i, "z" * 5_000) for i in range(20)]
    contents.append(_content(98, "x" * 10))
    contents.append(_content(99, "x" * 10))
    req.contents = contents

    _run(plugin.before_model_callback(callback_context=None, llm_request=req))

    # The prompt passed to the fake model contains the SUMMARY_PROMPT-formatted
    # transcript; its length must be bounded by the transcript cap (+ slack for
    # the surrounding prompt template + truncation marker).
    captured = fake.captured_request
    prompt_text = "".join(
        p.text or "" for c in captured.contents for p in (c.parts or [])
    )
    assert len(prompt_text) <= _cc._SUMMARY_TRANSCRIPT_MAX_CHARS + 2_000
    assert "…[older context truncated]" in prompt_text
    assert "[tool_call Read]" in prompt_text
    assert "[tool_result Bash]" in prompt_text


def test_g1_builder_additive_default_off() -> None:
    plugin = build_context_compaction_plugin(
        enabled=True, token_threshold=24_000, tail_events=16
    )
    assert isinstance(plugin, MagiContextCompactionPlugin)
    assert plugin._summarize_enabled is False


def test_g1_builder_forwards_summary_kwargs() -> None:
    plugin = build_context_compaction_plugin(
        enabled=True,
        token_threshold=24_000,
        tail_events=16,
        summarize_enabled=True,
        summary_model="anthropic/claude-haiku-4-5",
        summary_timeout=12.5,
    )
    assert isinstance(plugin, MagiContextCompactionPlugin)
    assert plugin._summarize_enabled is True
    assert plugin._summary_model_override == "anthropic/claude-haiku-4-5"
    assert plugin._summary_timeout == 12.5


# ---------------------------------------------------------------------------
# G5 — anchored/incremental summary + G6 — circuit breaker (default-OFF)
# ---------------------------------------------------------------------------

from magi_agent.adk_bridge.context_compaction import (  # noqa: E402
    ANCHOR_SUMMARY_STATE_KEY,
    SUMMARY_FAILURE_COUNT_STATE_KEY,
)


class _FailingSummaryModel:
    """Async-generator model that yields empty text (-> summary == '')."""

    def __init__(self) -> None:
        self.calls = 0

    async def generate_content_async(self, llm_request, stream=False):  # noqa: ANN001
        self.calls += 1
        self.captured_request = llm_request
        yield _FakeLlmResponse("")


def _captured_prompt(fake) -> str:  # noqa: ANN001
    cap = fake.captured_request
    return "".join(p.text or "" for c in cap.contents for p in (c.parts or []))


def _anchored_plugin(**kw) -> MagiContextCompactionPlugin:  # noqa: ANN003
    defaults: dict = dict(
        token_threshold=2_000,
        tail_events=16,
        summarize_enabled=True,
        anchored_summary_enabled=True,
    )
    defaults.update(kw)
    return MagiContextCompactionPlugin(**defaults)


def test_g5_off_no_state_touched() -> None:
    # anchored OFF + summarize OFF: state dict remains EMPTY after a tail-drop.
    plugin = MagiContextCompactionPlugin(token_threshold=2_000, tail_events=16)
    ctx = _CbCtx()
    req = _big_request(40)
    _run(plugin.before_model_callback(callback_context=ctx, llm_request=req))
    assert ANCHOR_SUMMARY_STATE_KEY not in ctx.state
    assert SUMMARY_FAILURE_COUNT_STATE_KEY not in ctx.state
    assert len(req.contents) == 16


def test_g5_anchored_off_summarize_on_plain_prompt(monkeypatch) -> None:  # noqa: ANN001
    # summarize ON but anchored OFF: the prompt is the plain Phase-3 SUMMARY_PROMPT
    # (no <previous_summary> wording), even across turns.
    fake = _patch_summarizer(monkeypatch, model=_FakeSummaryModel("S1"))
    plugin = _summary_plugin()  # anchored OFF
    ctx = _CbCtx()
    _run(
        plugin.before_model_callback(
            callback_context=ctx, llm_request=_big_request(40)
        )
    )
    _run(
        plugin.before_model_callback(
            callback_context=ctx, llm_request=_big_request(40)
        )
    )
    prompt = _captured_prompt(fake)
    assert "<previous_summary>" not in prompt
    assert "anchored summary" not in prompt


def test_g5_anchor_persisted_then_fed(monkeypatch) -> None:  # noqa: ANN001
    fake = _patch_summarizer(monkeypatch, model=_FakeSummaryModel("S1"))
    plugin = _anchored_plugin()
    ctx = _CbCtx()
    # Turn 1: produces summary 'S1', stored marker-stripped, failures reset to 0.
    _run(
        plugin.before_model_callback(
            callback_context=ctx, llm_request=_big_request(40)
        )
    )
    assert ctx.state.get(ANCHOR_SUMMARY_STATE_KEY) == "S1"
    assert ctx.state.get(SUMMARY_FAILURE_COUNT_STATE_KEY) == 0
    # Turn 2: prior anchor 'S1' fed into the anchored prompt.
    _run(
        plugin.before_model_callback(
            callback_context=ctx, llm_request=_big_request(40)
        )
    )
    prompt = _captured_prompt(fake)
    assert "<previous_summary>\nS1" in prompt
    assert "anchored summary" in prompt


def test_g5_prior_summary_not_rerendered(monkeypatch) -> None:  # noqa: ANN001
    # A dropped prefix that itself contains an injected '[Previous conversation
    # summary]\n\nOLD' Content: with anchored ON, OLD is used as the anchor and
    # NOT re-rendered as raw transcript (excluded from <new_history>).
    fake = _patch_summarizer(monkeypatch, model=_FakeSummaryModel("NEW"))
    plugin = _anchored_plugin(tail_events=4)
    req = LlmRequest()
    contents = [
        types.Content(
            role="user",
            parts=[types.Part(text="[Previous conversation summary]\n\nOLD")],
        )
    ]
    contents += [_content(i + 1, "x" * 1600) for i in range(10)]
    req.contents = contents
    _run(plugin.before_model_callback(callback_context=None, llm_request=req))
    prompt = _captured_prompt(fake)
    # The OLD anchor body is fed as the previous-summary anchor...
    assert "<previous_summary>\nOLD" in prompt
    # ...but NOT re-rendered into the raw <new_history> transcript.
    history = prompt.split("<new_history>", 1)[1]
    assert "OLD" not in history
    assert "[Previous conversation summary]" not in history


def test_g6_breaker_trips_after_max(monkeypatch) -> None:  # noqa: ANN001
    fake = _patch_summarizer(monkeypatch, model=_FailingSummaryModel())
    plugin = _anchored_plugin(summary_max_failures=2)
    ctx = _CbCtx()
    # turn1 -> failures=1 (model called), turn2 -> failures=2 (model called).
    _run(plugin.before_model_callback(callback_context=ctx, llm_request=_big_request(40)))
    assert ctx.state.get(SUMMARY_FAILURE_COUNT_STATE_KEY) == 1
    _run(plugin.before_model_callback(callback_context=ctx, llm_request=_big_request(40)))
    assert ctx.state.get(SUMMARY_FAILURE_COUNT_STATE_KEY) == 2
    assert fake.calls == 2
    # turn3 -> short-circuits: model NOT called, pure tail-drop.
    req3 = _big_request(40)
    original_split = req3.contents[24].parts[0].text
    _run(plugin.before_model_callback(callback_context=ctx, llm_request=req3))
    assert fake.calls == 2
    assert len(req3.contents) == 16
    assert req3.contents[0].parts[0].text == original_split


def test_g6_breaker_resets_on_success(monkeypatch) -> None:  # noqa: ANN001
    # Custom model: fail twice, then succeed, then fail again.
    class _Seq:
        def __init__(self) -> None:
            self.calls = 0
            self._texts = ["", "", "OK", ""]

        async def generate_content_async(self, llm_request, stream=False):  # noqa: ANN001
            self.captured_request = llm_request
            text = self._texts[self.calls]
            self.calls += 1
            yield _FakeLlmResponse(text)

    fake = _Seq()
    _patch_summarizer(monkeypatch, model=fake)
    plugin = _anchored_plugin(summary_max_failures=3)
    ctx = _CbCtx()
    _run(plugin.before_model_callback(callback_context=ctx, llm_request=_big_request(40)))
    assert ctx.state.get(SUMMARY_FAILURE_COUNT_STATE_KEY) == 1
    _run(plugin.before_model_callback(callback_context=ctx, llm_request=_big_request(40)))
    assert ctx.state.get(SUMMARY_FAILURE_COUNT_STATE_KEY) == 2
    # Success resets counter to 0 and stores the anchor.
    _run(plugin.before_model_callback(callback_context=ctx, llm_request=_big_request(40)))
    assert ctx.state.get(SUMMARY_FAILURE_COUNT_STATE_KEY) == 0
    assert ctx.state.get(ANCHOR_SUMMARY_STATE_KEY) == "OK"
    # Later failure starts counting from 1 again.
    _run(plugin.before_model_callback(callback_context=ctx, llm_request=_big_request(40)))
    assert ctx.state.get(SUMMARY_FAILURE_COUNT_STATE_KEY) == 1


def test_g6_breaker_disabled_when_max_zero(monkeypatch) -> None:  # noqa: ANN001
    fake = _patch_summarizer(monkeypatch, model=_FailingSummaryModel())
    plugin = _anchored_plugin(summary_max_failures=0)
    ctx = _CbCtx()
    # Many consecutive failures never short-circuit (model called every turn).
    for _ in range(5):
        _run(
            plugin.before_model_callback(
                callback_context=ctx, llm_request=_big_request(40)
            )
        )
    assert fake.calls == 5


def test_g5_fail_open_no_provider(monkeypatch) -> None:  # noqa: ANN001
    # anchored ON but no provider -> pure tail-drop, never raises; state breaker
    # increments (a failed attempt).
    _patch_summarizer(monkeypatch, provider=None)
    plugin = _anchored_plugin()
    ctx = _CbCtx()
    req = _big_request(40)
    original_split = req.contents[24].parts[0].text
    out = _run(
        plugin.before_model_callback(callback_context=ctx, llm_request=req)
    )
    assert out is None
    assert len(req.contents) == 16
    assert req.contents[0].parts[0].text == original_split


def test_g5_builder_forwards_anchor_kwargs() -> None:
    plugin = build_context_compaction_plugin(
        enabled=True,
        token_threshold=24_000,
        tail_events=16,
        summarize_enabled=True,
        anchored_summary_enabled=True,
        summary_max_failures=5,
    )
    assert isinstance(plugin, MagiContextCompactionPlugin)
    assert plugin._anchored_enabled is True
    assert plugin._summary_breaker_max == 5


def test_g5_builder_default_off() -> None:
    plugin = build_context_compaction_plugin(
        enabled=True, token_threshold=24_000, tail_events=16
    )
    assert plugin._anchored_enabled is False
    assert plugin._summary_breaker_max == 3


def test_g6_ctor_rejects_negative_max_failures() -> None:
    with pytest.raises(ValueError):
        MagiContextCompactionPlugin(
            token_threshold=1, tail_events=1, summary_max_failures=-1
        )


# ---------------------------------------------------------------------------
# G7 — manual /compact force-compaction (default-OFF)
# ---------------------------------------------------------------------------


@pytest.fixture()
def _reset_manual_signal():
    from magi_agent.runtime.manual_compaction_context import (
        reset_manual_compaction,
    )

    reset_manual_compaction()
    yield
    reset_manual_compaction()


def test_manual_force_compacts_under_threshold(_reset_manual_signal) -> None:
    from magi_agent.runtime.manual_compaction_context import (
        request_manual_compaction,
    )

    # Huge threshold: the automatic path would NOT compact (under threshold).
    plugin = MagiContextCompactionPlugin(
        token_threshold=1_000_000, tail_events=16, manual_enabled=True
    )
    req = _big_request(40)
    original_last = req.contents[-1].parts[0].text

    request_manual_compaction()
    result = _run(
        plugin.before_model_callback(callback_context=None, llm_request=req)
    )

    assert result is None
    assert len(req.contents) == 16  # forced tail-drop despite under threshold
    assert req.contents[-1].parts[0].text == original_last


def test_manual_force_is_one_shot(_reset_manual_signal) -> None:
    from magi_agent.runtime.manual_compaction_context import (
        request_manual_compaction,
    )

    plugin = MagiContextCompactionPlugin(
        token_threshold=1_000_000, tail_events=16, manual_enabled=True
    )
    request_manual_compaction()

    req1 = _big_request(40)
    _run(plugin.before_model_callback(callback_context=None, llm_request=req1))
    assert len(req1.contents) == 16  # forced

    # No new request -> the next call takes the normal threshold path (untouched).
    req2 = _big_request(40)
    before = list(req2.contents)
    _run(plugin.before_model_callback(callback_context=None, llm_request=req2))
    assert req2.contents == before
    assert len(req2.contents) == 40


def test_manual_enabled_no_signal_is_automatic(_reset_manual_signal) -> None:
    # manual_enabled=True but NO pending request: byte-identical to automatic
    # under-threshold behaviour (contents untouched).
    plugin = MagiContextCompactionPlugin(
        token_threshold=1_000_000, tail_events=16, manual_enabled=True
    )
    req = _big_request(40)
    before = list(req.contents)
    _run(plugin.before_model_callback(callback_context=None, llm_request=req))
    assert req.contents == before
    assert len(req.contents) == 40


def test_manual_flag_off_never_consumes_signal(_reset_manual_signal) -> None:
    from magi_agent.runtime.manual_compaction_context import (
        consume_manual_compaction,
        request_manual_compaction,
    )

    # Flag OFF (default): even with a pending request, the plugin never consumes
    # it and under-threshold contents stay untouched (Phase-4 byte-identical).
    plugin = MagiContextCompactionPlugin(
        token_threshold=1_000_000, tail_events=16
    )
    request_manual_compaction()
    req = _big_request(40)
    before = list(req.contents)
    _run(plugin.before_model_callback(callback_context=None, llm_request=req))

    assert req.contents == before
    assert len(req.contents) == 40
    # The pending request was NOT consumed by the OFF plugin.
    assert consume_manual_compaction() is True


def test_manual_force_tiny_context_is_noop_and_preserves_signal(
    _reset_manual_signal,
) -> None:
    from magi_agent.runtime.manual_compaction_context import (
        consume_manual_compaction,
        request_manual_compaction,
    )

    # contents <= tail_events: forced /compact is a safe no-op AND the one-shot is
    # NOT consumed (the early return fires before consume()).
    plugin = MagiContextCompactionPlugin(
        token_threshold=1, tail_events=16, manual_enabled=True
    )
    request_manual_compaction()
    req = _big_request(10)
    before = list(req.contents)
    _run(plugin.before_model_callback(callback_context=None, llm_request=req))

    assert req.contents == before
    assert len(req.contents) == 10
    # The pending request survived (not burned on a tiny context).
    assert consume_manual_compaction() is True


def test_build_plugin_manual_enabled_defaults_false() -> None:
    plugin = build_context_compaction_plugin(
        enabled=True, token_threshold=2_000, tail_events=16
    )
    assert plugin is not None
    assert plugin._manual_enabled is False


def test_build_plugin_manual_enabled_forwarded() -> None:
    plugin = build_context_compaction_plugin(
        enabled=True, token_threshold=2_000, tail_events=16, manual_enabled=True
    )
    assert plugin is not None
    assert plugin._manual_enabled is True


# ---------------------------------------------------------------------------
# U1: pin the active user task across a tail-drop
# ---------------------------------------------------------------------------


def _user_text(index: int, text: str) -> types.Content:
    """A genuine user text turn (no function parts)."""
    return types.Content(role="user", parts=[types.Part(text=text)])


def _model_text(index: int, text: str) -> types.Content:
    return types.Content(role="model", parts=[types.Part(text=text)])


def _pin_fn_response(name: str) -> types.Content:
    return types.Content(
        role="user",
        parts=[
            types.Part(
                function_response=types.FunctionResponse(
                    name=name, response={"ok": True}
                )
            )
        ],
    )


def test_pin_last_user_task_survives_tail_drop_summarize_off() -> None:
    plugin = MagiContextCompactionPlugin(token_threshold=2_000, tail_events=4)
    req = LlmRequest()
    # The active user task is the FIRST content and would be dropped by a naive
    # last-4 tail keep. It must survive, prepended ahead of the kept tail.
    task = _user_text(0, "TASK: analyze the six uploaded documents")
    contents = [task]
    contents += [_model_text(i, "x" * 1600) for i in range(1, 30)]
    req.contents = contents

    _run(plugin.before_model_callback(callback_context=None, llm_request=req))

    # The pinned user task is present and is the FIRST content.
    assert req.contents[0].parts[0].text == "TASK: analyze the six uploaded documents"
    # The recent tail is preserved (last content unchanged).
    assert req.contents[-1].parts[0].text == "x" * 1600
    # Pinned task + tail (kept window is tail_events; pin adds one ahead).
    assert len(req.contents) == plugin.tail_events + 1


def test_pin_last_user_task_survives_tail_drop_summarize_on() -> None:
    # summarize ON but the summary generation fails -> falls through to pure
    # tail-drop; the pin must still preserve the active user task ahead of tail.
    plugin = MagiContextCompactionPlugin(
        token_threshold=2_000, tail_events=4, summarize_enabled=True
    )
    req = LlmRequest()
    task = _user_text(0, "TASK: build the quarterly report")
    contents = [task]
    contents += [_model_text(i, "x" * 1600) for i in range(1, 30)]
    req.contents = contents

    _run(plugin.before_model_callback(callback_context=None, llm_request=req))

    texts = [
        c.parts[0].text
        for c in req.contents
        if c.parts and getattr(c.parts[0], "text", None)
    ]
    # The exact active user task survives verbatim (head + [pinned_user] + tail).
    assert "TASK: build the quarterly report" in texts
    # The pinned task appears before the "xxxx" tail bodies.
    task_pos = texts.index("TASK: build the quarterly report")
    tail_pos = texts.index("x" * 1600)
    assert task_pos < tail_pos


def test_pin_not_applied_when_last_user_text_is_in_tail() -> None:
    # When the last real user-text content is already inside the kept tail, no
    # extra content is prepended (no double-inject).
    plugin = MagiContextCompactionPlugin(token_threshold=2_000, tail_events=6)
    req = LlmRequest()
    contents = [_model_text(i, "x" * 1600) for i in range(20)]
    # Put a genuine user turn well inside the last-6 window (index 18).
    contents[18] = _user_text(18, "USER: latest instruction")
    req.contents = contents

    _run(plugin.before_model_callback(callback_context=None, llm_request=req))

    # No pin prepended: kept window is exactly tail_events.
    assert len(req.contents) == plugin.tail_events
    texts = [c.parts[0].text for c in req.contents]
    assert "USER: latest instruction" in texts


def test_pin_ignores_function_response_carrier_user_role() -> None:
    # A function_response is carried on a role=="user" Content but is NOT a
    # genuine user message; the pin must skip it and preserve the real user text.
    plugin = MagiContextCompactionPlugin(token_threshold=2_000, tail_events=4)
    req = LlmRequest()
    task = _user_text(0, "TASK: the real user instruction")
    contents = [task]
    # A tool cycle: model text then a function_response carrier (role user).
    for i in range(1, 30):
        if i % 2 == 0:
            contents.append(_pin_fn_response("Read"))
        else:
            contents.append(_model_text(i, "x" * 1600))
    req.contents = contents

    _run(plugin.before_model_callback(callback_context=None, llm_request=req))

    assert req.contents[0].parts[0].text == "TASK: the real user instruction"


def test_pin_preserves_huge_user_task_without_size_condition() -> None:
    plugin = MagiContextCompactionPlugin(token_threshold=2_000, tail_events=4)
    req = LlmRequest()
    huge = _user_text(0, "TASK: " + ("y" * 200_000))
    contents = [huge]
    contents += [_model_text(i, "x" * 1600) for i in range(1, 30)]
    req.contents = contents

    _run(plugin.before_model_callback(callback_context=None, llm_request=req))

    assert req.contents[0].parts[0].text == "TASK: " + ("y" * 200_000)


# ---------------------------------------------------------------------------
# U2: window-aware default threshold (env var unset)
# ---------------------------------------------------------------------------


def test_window_aware_default_does_not_compact_above_24k() -> None:
    # token_threshold NOT explicit -> effective threshold derives from the model
    # window (150_000 default). A context that exceeds 24k estimate but sits under
    # the effective window-aware threshold must NOT compact.
    plugin = MagiContextCompactionPlugin(
        token_threshold=24_000, tail_events=16, token_threshold_explicit=False
    )
    req = LlmRequest()
    # ~50k estimated tokens (100 * ~501): above 24k, far below effective
    # ~106_500 for the 150_000 default window. Content count (100) stays under
    # the event-count threshold (128) so the token threshold is isolated.
    req.contents = [_content(i, "x" * 4000) for i in range(100)]
    est_before = len(req.contents)

    _run(plugin.before_model_callback(callback_context=None, llm_request=req))

    assert len(req.contents) == est_before  # untouched


def test_explicit_threshold_24k_still_compacts_at_24k() -> None:
    # env var explicitly set (explicit=True) -> 24k is authoritative; a context
    # over 24k compacts even though the window-aware value would be higher.
    plugin = MagiContextCompactionPlugin(
        token_threshold=24_000, tail_events=16, token_threshold_explicit=True
    )
    req = LlmRequest()
    # ~50k estimated tokens, 100 contents (under the 128 event-count threshold).
    req.contents = [_content(i, "x" * 4000) for i in range(100)]

    _run(plugin.before_model_callback(callback_context=None, llm_request=req))

    assert len(req.contents) < 100  # compacted


def test_window_aware_default_compacts_when_over_effective() -> None:
    plugin = MagiContextCompactionPlugin(
        token_threshold=24_000, tail_events=16, token_threshold_explicit=False
    )
    req = LlmRequest()
    # ~120k estimated tokens (120 * ~1001): above the effective ~106_500
    # threshold -> must compact. 120 contents stays under the 128 event-count
    # threshold so the token threshold is the sole trigger.
    req.contents = [_content(i, "x" * 8000) for i in range(120)]

    _run(plugin.before_model_callback(callback_context=None, llm_request=req))

    assert len(req.contents) < 120
