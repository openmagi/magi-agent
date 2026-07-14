"""U10: the governed hosted turn path regression gate (P5-M1b updated).

This is the regression gate for the governed hosted path. It drives the governed
path across every blocker surface the stack fixed and asserts parity behavior.
Each scenario is written so it FAILS if its underlying fix were absent (never a
tautological assert).

Design: docs/plans/2026-07-06-hosted-local-turn-engine-convergence-assessment.md
section 7 (Hosted parity test design).

P5-M1b update: the legacy gate5b4c3 boundary
(``Gate5B4C3LiveRunnerBoundary`` / ``_build_user_message_parts``) was deleted.
The governed path is now the ONLY hosted engine. ``MAGI_HOSTED_GOVERNED_TURN_ENABLED``
no longer gates anything; governed is unconditional.

Scenario -> reused harness map:

1. Reused-session continuity (#1364 probe)  -> seed-on-empty serving harness
   (real google.adk Runner + capturing fake BaseLlm + durable SQLite).
2. Restart continuity                        -> same, with a registry + durable
   reset between turns (SQLite file kept) and the legacy identity triple pinned.
3. Tool-loop turn on a reused session        -> real-Runner reused-session tool
   turn (no double-seed). Cross-branch event-parity test retired (P5-M1b).
4. Image parts                               -> governed real-Runner opening
   Content.parts contain the expected text part and inline-data image part.
   Legacy _build_user_message_parts comparison retired (P5-M1b).
5. Continuity fields (miss / hit / busy)     -> serving harness, recording the
   real collector result's session_reused / session_event_count.
6. Timeout                                   -> hanging governed stream + small
   budget -> the shared runner_timeout handler.
7. Transcript records                        -> observability serving harness
   (turn_start / message / turn_end + tool_call / tool_result translation).
8. Live SSE streaming                        -> governed SSE route delivers the
   answer as multiple live text_delta frames (B8), not one blob.
9. output_continuation                       -> serving resolver gated on
   selected_full_toolhost under the same env/profile condition as legacy (U9).

B9 (UNLOCKED in P5-M1b): the no-tool finalizer (U1-U4) is now wired into the
governed path. The strict xfail that locked the gap is removed.

All hermetic: no network (fake BaseLlm), tmp MAGI_STATE_DIR, registry / durable /
transcript-sink resets in fixtures.
"""
from __future__ import annotations

import asyncio
import base64
from typing import Any

import pytest
from fastapi.testclient import TestClient

import magi_agent.transport.gate5b_serving as serving_mod
from magi_agent.app import create_app
from magi_agent.observability.transcript import set_active_transcript_sink
from magi_agent.runtime.hosted_runtime import (
    GATE5B_SHADOW_APP_NAME,
    GATE5B_SHADOW_USER_ID,
)
from magi_agent.runtime.output_continuation import OutputContinuationConfig
from magi_agent.runtime.turn_context import TurnContext
from magi_agent.shadow.hosted_session_substrate import (
    reset_durable_hosted_session_service,
)
from magi_agent.shadow.session_service_registry import (
    reset_default_session_service_registry,
)
from magi_agent.transport.active_turn import ACTIVE_TURNS
from magi_agent.transport.hosted_turn_context import hosted_request_to_turn_context

# --- reused harness helpers (import, do not re-invent) ---------------------
from tests.support.engine_fakes import call_event, response_event, text_event
from tests.support.gate5b4c3_factories import make_shadow_generation_request
from tests.test_chat_routes_hosted_governed_turn import (
    _canary_headers,
    _make_canary_runtime,
)
from tests.test_gate5b_serving_seed_on_empty import (
    _CapturingLlm,
    _RESUME_MARKER,
    _all_texts,
    _drive_turn,
    _valid_agent_hosted_runtime,
)
from tests.test_hosted_shadow_parity import (
    _config_full_toolhost,
    _drive_governed_turn_path,
    _request_full_toolhost,
)
from tests.support.gate5b4c3_fakes import _ManualCalculationTool


# ---------------------------------------------------------------------------
# Fixtures: reset every process-global piece of session / transcript state.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_all_state() -> Any:
    reset_default_session_service_registry()
    reset_durable_hosted_session_service()
    set_active_transcript_sink(None)
    ACTIVE_TURNS._turns.clear()
    yield
    reset_default_session_service_registry()
    reset_durable_hosted_session_service()
    set_active_transcript_sink(None)
    ACTIVE_TURNS._turns.clear()


# ---------------------------------------------------------------------------
# Shared serving driver: flag-ON governed path, REAL lease + probe + collect,
# real ADK Runner fronting a capturing fake BaseLlm. Only build_hosted_runtime
# and the model builder are spied (to inject the real Runner and capture the
# LLM-visible contents / identity). Mirrors the proven seed-on-empty harness.
# ---------------------------------------------------------------------------


def _governed_serving_env(monkeypatch, tmp_path: Any) -> None:
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    monkeypatch.setenv("MAGI_HOSTED_SESSION_REUSE", "1")
    monkeypatch.setenv("MAGI_HOSTED_SESSION_DB", "1")
    monkeypatch.setenv("MAGI_STATE_DIR", str(tmp_path))


def _install_real_runner_serving_spies(monkeypatch) -> dict[str, list]:
    """Wire the flag-ON serving path onto a real ADK Runner with a capturing
    fake BaseLlm, keeping the lease + seed probe + collector REAL. Records the
    per-turn ``include_history`` verdict, the Runner identity triple, the
    LLM-visible request contents, and the collector's boundary result."""
    records: dict[str, list] = {
        "include_history": [],
        "identity": [],
        "llm": [],
        "result": [],
    }

    def fake_model(**kwargs: object) -> object:
        return _CapturingLlm(records["llm"])

    monkeypatch.setattr(serving_mod, "_gate1a_correlated_model_or_label", fake_model)

    def real_build_hosted_runtime(**kwargs: object) -> object:
        records["identity"].append(
            {"app_name": kwargs.get("app_name"), "user_id": kwargs.get("user_id")}
        )
        return _valid_agent_hosted_runtime(
            model=kwargs["model"],
            session_service=kwargs["session_service"],
            sink=records["llm"],
        )

    monkeypatch.setattr(serving_mod, "build_hosted_runtime", real_build_hosted_runtime)

    real_mapper = hosted_request_to_turn_context

    def spy_mapper(generation: object, **kwargs: object) -> TurnContext:
        records["include_history"].append(kwargs.get("include_history", "<absent>"))
        return real_mapper(generation, **kwargs)

    monkeypatch.setattr(serving_mod, "hosted_request_to_turn_context", spy_mapper)

    real_collect = serving_mod.collect_engine_to_boundary_result

    async def spy_collect(**kwargs: object) -> object:
        result = await real_collect(**kwargs)
        records["result"].append(result)
        return result

    monkeypatch.setattr(serving_mod, "collect_engine_to_boundary_result", spy_collect)
    return records


def _post_msg(runtime: object, *, digest: str, session_id: str, content: str) -> Any:
    return TestClient(create_app(runtime)).post(
        "/v1/chat/completions",
        headers=_canary_headers(digest),
        json={
            "messages": [{"role": "user", "content": content}],
            "sessionId": session_id,
        },
    )


# ===========================================================================
# SCENARIO 1: reused-session continuity across 2 turns (the #1364 probe)
# ===========================================================================


def test_reused_session_recall_appears_exactly_once() -> None:
    """MODEL-VISIBLE tier of the #1364 probe. Turn 1 establishes a fact into a
    shared ADK session; turn 2 recalls it on the SAME session with inline history
    SUPPRESSED (initial_messages empty, which is the U4 seam verdict for a
    populated session). Turn 2's model-visible contents must contain the fact
    EXACTLY ONCE (from persisted session events, not also re-seeded inline) and
    the driver resume-prefix marker must be ABSENT. If U4 did not suppress the
    inline history (the pre-fix unconditional mapping), the model would see the
    fact TWICE and the resume marker would appear -- precisely what the sibling
    ``test_unsuppressed_initial_messages_double_seeds_history`` pins. The seam
    that PRODUCES this suppression verdict is asserted in scenario 2 / scenario 5.
    """
    from google.adk import sessions as adk_sessions

    svc = adk_sessions.InMemorySessionService()
    sink: list = []
    sid = "flip-recall"

    async def scenario() -> None:
        await _drive_turn(
            svc,
            TurnContext(
                prompt="My project codename is BLUEFIN.", session_id=sid, turn_id="t1"
            ),
            sink,
        )
        await _drive_turn(
            svc,
            TurnContext(
                prompt="What is my project codename?",
                session_id=sid,
                turn_id="t2",
                initial_messages=(),
            ),
            sink,
        )

    asyncio.run(scenario())

    turn2_texts = _all_texts(sink[-1])
    joined = "\n".join(turn2_texts)
    assert _RESUME_MARKER not in joined, f"resume prefix must be absent when populated: {turn2_texts}"
    bluefin_hits = sum(t.count("BLUEFIN") for t in turn2_texts)
    assert bluefin_hits == 1, f"fact must appear exactly once, saw {bluefin_hits}: {turn2_texts}"


# ===========================================================================
# SCENARIO 2: restart continuity (durable SQLite kept, registry reset)
# ===========================================================================


def test_restart_continuity_under_legacy_identity(monkeypatch, tmp_path: Any) -> None:
    """Reset the process session registry AND the durable-service singleton
    between turns while keeping the durable SQLite file (a simulated pod
    restart). Turn 2 must still see turn 1 exactly once, which requires the
    probe/runner to reload the SAME durable rows turn 1 wrote -- i.e. under the
    LEGACY identity triple (app_name / user_id). Without U6 the governed path
    would read/write a DIFFERENT (app_name, user_id, session_id) row, the probe
    would read 0 across the restart, seed both turns (include_history stays
    [True, True]) and lose server-side history at cutover.
    """
    _governed_serving_env(monkeypatch, tmp_path)
    records = _install_real_runner_serving_spies(monkeypatch)
    runtime = _make_canary_runtime(tmp_path)

    r1 = _post_msg(
        runtime, digest="a" * 64, session_id="s-restart",
        content="My project codename is BLUEFIN.",
    )
    assert r1.status_code == 200, r1.json()

    # Simulate a restart: drop the in-process registry AND the durable-service
    # handle, but keep MAGI_STATE_DIR (the SQLite file on disk) untouched.
    reset_default_session_service_registry()
    reset_durable_hosted_session_service()

    r2 = _post_msg(
        runtime, digest="b" * 64, session_id="s-restart",
        content="What is my project codename?",
    )
    assert r2.status_code == 200, r2.json()

    # Surviving the restart, turn 2 still reads the persisted events (verdict
    # False) -- proof the durable file reloaded and was probed under the SAME
    # identity turn 1 wrote. Without U6 the restart turn probes a different row,
    # reads 0, and the verdict would be [True, True] (reseed = the #1364 re-open
    # plus lost server-side history at cutover).
    assert records["include_history"] == [True, False], records["include_history"]

    # The probe and runner used the legacy identity triple on BOTH turns.
    assert len(records["identity"]) == 2
    for ident in records["identity"]:
        assert ident["app_name"] == GATE5B_SHADOW_APP_NAME
        assert ident["user_id"] == GATE5B_SHADOW_USER_ID


# ===========================================================================
# SCENARIO 3: tool-loop turn on a reused session
# ===========================================================================


def test_tool_route_on_reused_session_suppresses_inline_history() -> None:
    """The no-history-duplication half of scenario 3. Reused-session history
    suppression is computed at the serving seam BEFORE the turn and is
    independent of whether the turn uses tools: a full-toolhost (tool-capable)
    generation mapped under the reused verdict (include_history=False) carries NO
    inline history into the driver, so the tool turn cannot double-seed the prior
    context. The same generation under include_history=True (the empty-session
    seed verdict) DOES carry it, proving the verdict -- not the tool route --
    drives the behavior. (A real-ADK tool loop with a fake model plus a real tool
    is not hermetic in this environment: the ADK function-call telemetry path
    breaks under the sandbox opentelemetry stack, and no repo test drives a real
    Runner tool loop. Tool-loop event/result parity is covered in the sibling
    both-branch test above; the reused-session model-visible no-double-seed is
    covered by scenario 1.)"""
    from types import SimpleNamespace

    generation = SimpleNamespace(
        turn=SimpleNamespace(
            sanitized_current_turn_text="run the tool then answer",
            sanitized_recent_history=[
                SimpleNamespace(role="user", sanitized_text="My codename is BLUEFIN."),
                SimpleNamespace(role="assistant", sanitized_text="Understood."),
            ],
            turn_id="turn-tool-1",
        ),
        model_routing=SimpleNamespace(provider_label="google", model_label="gemini-3.5-flash"),
        recipe_profile=SimpleNamespace(tools_policy="selected_full_toolhost"),
        selection=SimpleNamespace(session_key_digest="sha256:" + "a" * 64),
        request_id_digest="sha256:" + "b" * 64,
    )

    # Reused session -> suppress: the tool turn carries no inline history.
    suppressed = hosted_request_to_turn_context(generation, include_history=False)
    assert suppressed.initial_messages == ()

    # Empty session -> seed: the same generation DOES carry the client-echo.
    seeded = hosted_request_to_turn_context(generation, include_history=True)
    assert len(seeded.initial_messages) == 2


# ===========================================================================
# SCENARIO 4: image parts parity between branches
# ===========================================================================

_PNG_BYTES = b"\x89PNG\r\n\x1a\n"
_PNG_B64 = base64.b64encode(_PNG_BYTES).decode("ascii")


def _governed_image_opening_parts() -> list:
    """Governed path's opening Content.parts for the same one image block,
    threaded through TurnContext + a real ADK Runner fronting a capturing model."""
    from google.adk import sessions as adk_sessions

    block = {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": _PNG_B64},
    }
    svc = adk_sessions.InMemorySessionService()
    sink: list = []
    runtime = _valid_agent_hosted_runtime(
        model=_CapturingLlm(sink), session_service=svc, sink=sink
    )
    from magi_agent.runtime.governed_turn import run_governed_turn

    async def run() -> None:
        async for _event in run_governed_turn(
            TurnContext(
                prompt="describe this image",
                session_id="flip-img",
                turn_id="t-img",
                image_blocks=(block,),
            ),
            runtime=runtime,
        ):
            pass

    asyncio.run(run())
    assert sink, "governed model was never called"
    return list(sink[0][0].parts)


def test_image_parts_shapes_equal_across_branches() -> None:
    """Governed path, given one sanitized PNG block, must produce an opening
    message of [text part, inline-data image part] with the expected text,
    mime type, and raw bytes. Without U5 the governed path drops the image
    entirely (text-only), so the governed opening would have no image part.
    The legacy comparison was retired in P5-M1b (legacy boundary deleted)."""
    governed = _governed_image_opening_parts()
    assert len(governed) >= 2, f"governed opening lacks the image part: {governed}"
    assert governed[0].text == "describe this image"
    img = governed[1]
    assert img.inline_data is not None, "second governed part must be inline-data image"
    assert img.inline_data.mime_type == "image/png"
    assert img.inline_data.data == _PNG_BYTES


# ===========================================================================
# SCENARIO 5: continuity fields (miss / hit / busy-overlap)
# ===========================================================================


def test_continuity_fields_miss_then_hit(monkeypatch, tmp_path: Any) -> None:
    """The governed collector result must carry the #1364 continuity verdicts.
    Turn 1 (miss, fresh durable session): session_reused False, event_count 0.
    Turn 2 (hit, reused populated session): session_reused True, event_count > 0
    and seeded_message_count 0 (history suppressed). Without U3 (lease) the reuse
    verdict is always False; without U4/U8 the fields never populate."""
    _governed_serving_env(monkeypatch, tmp_path)
    records = _install_real_runner_serving_spies(monkeypatch)
    runtime = _make_canary_runtime(tmp_path)

    r1 = _post_msg(runtime, digest="a" * 64, session_id="s-fields", content="hello one")
    assert r1.status_code == 200, r1.json()
    r2 = _post_msg(runtime, digest="b" * 64, session_id="s-fields", content="hello two")
    assert r2.status_code == 200, r2.json()

    assert len(records["result"]) == 2
    miss, hit = records["result"]
    assert miss.session_reused is False
    assert miss.session_event_count == 0
    assert hit.session_reused is True
    assert hit.session_event_count > 0
    assert hit.seeded_message_count == 0


def test_continuity_fields_busy_overlap_reports_not_reused(
    monkeypatch, tmp_path: Any
) -> None:
    """While one turn holds the single-flight lease for a session key, an
    overlapping same-key served turn gets the busy-fallback and the continuity
    verdict threaded into its result must be session_reused False (never a stale
    True). The overlapping turn's busy-fallback is a fresh in-memory service from
    the canary's fake primitives, so the driver is faked here; what is asserted is
    the ``session_reused`` verdict the serving branch feeds into the REAL
    collector (the exact value that becomes ``result.session_reused``). The exact
    lease key is captured by wrapping the REAL acquire helper. The sibling
    miss/hit test proves the same second turn is True WITHOUT the hold, so False
    here is the busy-fallback, not a fresh miss.
    """
    import magi_agent.runtime.session_ownership as ownership_mod
    from magi_agent.cli.contracts import EngineResult, Terminal

    _governed_serving_env(monkeypatch, tmp_path)

    acquire_kwargs: list = []
    reused_into_collect: list = []
    real_acquire = ownership_mod.acquire_hosted_session_lease

    def spy_acquire(**kwargs: object):  # noqa: ANN202
        lease = real_acquire(**kwargs)
        acquire_kwargs.append(dict(kwargs))
        return lease

    def fake_build(**kwargs: object) -> object:
        from types import SimpleNamespace

        return SimpleNamespace()

    async def _text_gen():  # noqa: ANN202
        yield {"type": "text_delta", "delta": "ok"}
        yield EngineResult(terminal=Terminal.completed, session_id="s", turn_id="t")

    def fake_governed_turn(ctx: object, *, runtime: object, cancel: object = None):  # noqa: ANN201
        return _text_gen()

    real_collect = serving_mod.collect_engine_to_boundary_result

    async def spy_collect(**kwargs: object) -> object:
        reused_into_collect.append(kwargs.get("session_reused"))
        return await real_collect(**kwargs)

    monkeypatch.setattr(
        "magi_agent.transport.gate5b_serving.acquire_hosted_session_lease", spy_acquire
    )
    monkeypatch.setattr("magi_agent.transport.gate5b_serving.build_hosted_runtime", fake_build)
    monkeypatch.setattr("magi_agent.transport.gate5b_serving.run_governed_turn", fake_governed_turn)
    monkeypatch.setattr(
        "magi_agent.transport.gate5b_serving.collect_engine_to_boundary_result", spy_collect
    )

    runtime = _make_canary_runtime(tmp_path)

    # Turn 1 establishes a reusable durable entry (miss -> release seeded).
    r1 = _post_msg(runtime, digest="a" * 64, session_id="s-busy", content="first")
    assert r1.status_code == 200, r1.json()
    assert reused_into_collect[0] is False
    assert acquire_kwargs, "acquire helper was never called on the served turn"

    # Hold the lease on the EXACT captured key so the next served turn overlaps.
    held = real_acquire(**acquire_kwargs[0])
    assert held is not None and held.reused is True
    try:
        r2 = _post_msg(runtime, digest="b" * 64, session_id="s-busy", content="second")
        assert r2.status_code == 200, r2.json()
    finally:
        held.release(seeded=True)

    # The overlapping turn's verdict fed into the result is the busy-fallback.
    assert reused_into_collect[-1] is False


# ===========================================================================
# SCENARIO 6: timeout -> the shared runner_timeout family (parity with legacy)
# ===========================================================================


@pytest.mark.asyncio
async def test_governed_timeout_yields_runner_timeout_family(
    monkeypatch, tmp_path: Any
) -> None:
    """A hung governed stream plus a small python_runner_timeout_ms budget must
    produce a 504 whose body is status='timeout' / reason='runner_timeout' --
    the SAME shape the legacy path yields, via the shared TimeoutError handler in
    gate5b_serving that serves both branches. Without U7 the governed collector
    never raises TimeoutError and the request hangs unbounded."""
    import time

    import httpx

    import tests.test_hosted_engine_result_timeout as _timeout

    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")

    async def _hanging_gen():  # noqa: ANN202
        event = asyncio.Event()
        while True:
            await event.wait()  # never set
            yield None

    def fake_governed_turn(ctx: object, *, runtime: object, cancel: object = None):  # noqa: ANN201
        return _hanging_gen()

    monkeypatch.setattr(
        "magi_agent.transport.gate5b_serving.run_governed_turn", fake_governed_turn
    )
    # collect_engine_to_boundary_result is NOT mocked: the real U7 timeout runs.

    runtime = _timeout._make_canary_runtime_with_small_timeout(
        tmp_path, python_runner_timeout_ms=100
    )
    app = create_app(runtime)

    start = time.monotonic()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            timeout=10.0,
        ) as client:
            response = await asyncio.wait_for(
                client.post(
                    "/v1/chat/completions",
                    headers=_timeout._canary_headers("c" * 64),
                    json=_timeout._CANARY_BODY,
                ),
                timeout=5.0,  # outer guard: prevents an infinite hang if U7 regressed
            )
    except (asyncio.TimeoutError, TimeoutError):
        pytest.fail(
            "governed request never returned: the 100ms runner timeout did not fire "
            "(U7 collector timeout missing?)"
        )

    assert response.status_code == 504, response.text
    body = response.json()
    assert body.get("status") == "timeout", body
    assert body.get("reason") == "runner_timeout", body
    assert time.monotonic() - start < 2.0


# ===========================================================================
# SCENARIO 7: transcript records on the governed path
# ===========================================================================


def test_governed_path_emits_turn_start_message_turn_end(monkeypatch, tmp_path: Any) -> None:
    """A registered transcript sink must receive the legacy record family from
    the governed path: turn_start (with the three continuity fields), message,
    turn_end -- in order. Reuses the U8 observability serving harness (reused,
    populated session). Without U8 the governed driver only gets the SSE sink and
    the transcript feed goes dark under the flip."""
    from tests.test_gate5b_serving_observability import (
        _drive_flag_on_governed_turn,
        _register_capturing_transcript_sink,
    )

    captured = _register_capturing_transcript_sink()
    try:
        _drive_flag_on_governed_turn(monkeypatch, tmp_path)
    finally:
        set_active_transcript_sink(None)

    by_type = {e["type"]: e for (e, _s, _t) in captured}
    assert "turn_start" in by_type, [e["type"] for (e, _s, _t) in captured]
    assert by_type["turn_start"]["session_reused"] is True
    assert by_type["turn_start"]["session_event_count"] == 4
    assert by_type["message"]["role"] == "assistant"
    assert by_type["turn_end"]["terminal"] == "completed"
    order = [
        e["type"]
        for (e, _s, _t) in captured
        if e["type"] in {"turn_start", "message", "turn_end"}
    ]
    assert order == ["turn_start", "message", "turn_end"]


def test_governed_tool_events_reach_transcript_as_legacy_record_types(
    monkeypatch, tmp_path: Any
) -> None:
    """D4: the composed public_event_sink wired into build_hosted_runtime on the
    governed path translates engine-native tool_start / tool_end into the legacy
    tool_call / tool_result transcript record TYPES."""
    from tests.test_gate5b_serving_observability import (
        _drive_flag_on_governed_turn,
        _register_capturing_transcript_sink,
    )

    captured = _register_capturing_transcript_sink()
    capture_sink: dict = {}
    try:
        _drive_flag_on_governed_turn(monkeypatch, tmp_path, capture_sink=capture_sink)
        sink = capture_sink.get("public_event_sink")
        assert sink is not None, "build_hosted_runtime must receive a composed public_event_sink"
        sink({"type": "tool_start", "id": "tu_1", "name": "Bash", "input_preview": "{}"}, "sess", "turn")
        sink({"type": "tool_end", "id": "tu_1", "status": "ok", "output_preview": "result:x"}, "sess", "turn")
    finally:
        set_active_transcript_sink(None)

    tool_records = [e for (e, _s, _t) in captured if e["type"] in {"tool_call", "tool_result"}]
    assert [e["type"] for e in tool_records] == ["tool_call", "tool_result"]
    assert tool_records[0]["tool_name"] == "Bash"
    assert tool_records[0]["call_id"] == "tu_1"
    assert tool_records[1]["status"] == "ok"


# ===========================================================================
# SCENARIO 8: live SSE streaming (B8)
# ===========================================================================


def test_governed_sse_streams_multiple_live_text_deltas(monkeypatch, tmp_path: Any) -> None:
    """The flag-ON governed SSE route must deliver the answer as MULTIPLE live
    text_delta frames (one per model chunk), not a single end-of-turn blob.
    Without B8 the collector drains the stream opaquely and the only frame the
    client sees is the single content-blob fallback."""
    from tests.test_streaming_chat_route import (
        _auth_headers,
        _data_lines,
        _make_app,
        _selected_runtime,
    )
    from tests.test_streaming_chat_route_governed_live import _wire_governed_real_runner

    monkeypatch.setenv("MAGI_STREAMING_CHAT", "1")
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    monkeypatch.setenv(
        "CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_WORKSPACE_ROOT", str(tmp_path)
    )
    _wire_governed_real_runner(monkeypatch)

    runtime = _selected_runtime(tmp_path, full_toolhost=True)
    client = TestClient(_make_app(runtime=runtime))
    response = client.post(
        "/v1/chat/stream",
        headers=_auth_headers(),
        json={
            "sessionId": "s-flip-sse",
            "turnId": "t-flip-sse",
            "messages": [{"role": "user", "content": "What is the project codename?"}],
        },
    )
    assert response.status_code == 200, response.text
    payloads = _data_lines(response.text)
    text_deltas = [p for p in payloads if p.get("type") == "text_delta"]
    joined = "".join(str(p.get("delta", "")) for p in text_deltas)
    assert "MULTIVERSE." in joined, response.text
    assert len(text_deltas) >= 2, f"SSE must stream live frames, saw {len(text_deltas)}: {text_deltas}"
    assert payloads[-1]["type"] == "turn_result"
    assert payloads[-1]["terminal"] == "completed"


# ===========================================================================
# SCENARIO 9: output_continuation wiring (U9)
# ===========================================================================


def _generation_with_tools_policy(tools_policy: str) -> object:
    from types import SimpleNamespace

    return SimpleNamespace(recipe_profile=SimpleNamespace(tools_policy=tools_policy))


def test_output_continuation_gated_on_full_toolhost_like_legacy(monkeypatch) -> None:
    """The governed serving resolver activates output continuation for
    selected_full_toolhost under the profile default-ON (flag unset), and returns
    None for non-full-toolhost routes and under the safe profile / explicit
    disable -- the same condition the legacy boundary applies. Without U9 the
    governed driver drops truncated-output continuation on long answers."""
    resolve = serving_mod._resolve_output_continuation_config

    monkeypatch.delenv("MAGI_OUTPUT_CONTINUATION_ENABLED", raising=False)
    monkeypatch.delenv("MAGI_RUNTIME_PROFILE", raising=False)
    on = resolve(_generation_with_tools_policy("selected_full_toolhost"))
    assert isinstance(on, OutputContinuationConfig) and on.enabled is True

    assert resolve(_generation_with_tools_policy("shadow_readonly")) is None
    assert resolve(_generation_with_tools_policy("disabled")) is None

    monkeypatch.setenv("MAGI_OUTPUT_CONTINUATION_ENABLED", "0")
    assert resolve(_generation_with_tools_policy("selected_full_toolhost")) is None

    # Safe profile flips the default OFF (flag unset so the profile default wins).
    monkeypatch.delenv("MAGI_OUTPUT_CONTINUATION_ENABLED", raising=False)
    monkeypatch.setenv("MAGI_RUNTIME_PROFILE", "safe")
    assert resolve(_generation_with_tools_policy("selected_full_toolhost")) is None


def test_output_continuation_verdict_forwarded_on_serving_path(monkeypatch, tmp_path: Any) -> None:
    """On the real flag-ON serving path the call site forwards EXACTLY the
    resolver's verdict into build_hosted_runtime (the wire that reaches the
    engine driver's _output_continuation)."""
    _governed_serving_env(monkeypatch, tmp_path)
    monkeypatch.delenv("MAGI_OUTPUT_CONTINUATION_ENABLED", raising=False)
    monkeypatch.delenv("MAGI_RUNTIME_PROFILE", raising=False)

    llm_sink: list = []
    monkeypatch.setattr(
        serving_mod, "_gate1a_correlated_model_or_label", lambda **_kw: _CapturingLlm(llm_sink)
    )

    captured: list = []

    def capturing_build(**kwargs: object) -> object:
        captured.append(kwargs.get("output_continuation"))
        return _valid_agent_hosted_runtime(
            model=kwargs["model"], session_service=kwargs["session_service"], sink=llm_sink
        )

    verdicts: list = []
    real_resolver = serving_mod._resolve_output_continuation_config

    def spy_resolver(generation: object) -> object:
        verdict = real_resolver(generation)
        verdicts.append(verdict)
        return verdict

    monkeypatch.setattr(serving_mod, "build_hosted_runtime", capturing_build)
    monkeypatch.setattr(serving_mod, "_resolve_output_continuation_config", spy_resolver)

    runtime = _make_canary_runtime(tmp_path)
    resp = _post_msg(runtime, digest="c" * 64, session_id="s-oc", content="hello")
    assert resp.status_code == 200, resp.json()
    assert captured and verdicts, "serving path did not consult the resolver / builder"
    assert captured[0] is verdicts[0]


# ===========================================================================
# B9 (LOCKED GAP): governed path lacks the legacy no-tool finalizer.
#
# The legacy boundary runs a tool-less finalizer when a selected_full_toolhost
# turn ends with no output_text
# (shadow/gate5b4c3_live_runner_boundary.py:1222-1234, 1144-1156), always-on and
# not env-gated, so an empty-text tool-only turn still surfaces a finalizer
# answer. The governed path has NO equivalent: the same turn surfaces as
# runner_output_missing (an empty answer) under the flip. Replicating it needs a
# governed-native finalizer (a fresh no-tools model pass), so it is a
# feature-sized follow-up deliberately NOT fixed in this stack.
#
# The strict xfail below drives a governed selected_full_toolhost turn that ends
# with no visible text and asserts the PARITY behavior legacy gives (a completed
# turn with a finalizer-produced answer). It XFAILs today; strict=True turns it
# into a hard failure the moment B9 is fixed without removing the marker, forcing
# the tracking to stay honest.
# ===========================================================================


def test_b9_governed_tool_only_turn_finalizes_like_legacy() -> None:
    """B9 UNLOCKED. A governed selected_full_toolhost turn whose model calls a
    tool and then emits NO final text still completes with a non-empty answer,
    at parity with the legacy no-tool finalizer. The first runner pass is tool
    only (no text); the driver runs one tool-less finalizer pass (second run)
    that produces the answer. Before the finalizer (U1-U4) this surfaced
    runner_output_missing; the strict xfail that locked the gap is now removed."""
    from magi_agent.runtime.no_tool_finalizer import NoToolFinalizerConfig

    request = _request_full_toolhost()
    config = _config_full_toolhost()

    # Per-call script: call 1 is tool-only (no text); call 2 (the finalizer
    # pass) yields the final answer.
    class _SequenceRunner:
        def __init__(self) -> None:
            self._script = [
                [
                    call_event("Calculation", {"expression": "1 + 1"}, "calculation-call-001"),
                    response_event(
                        "Calculation",
                        {"status": "ok", "reason": "tool_completed", "outputPreview": {"value": 2}},
                        "calculation-call-001",
                    ),
                ],
                [text_event("The result is 2.", partial=True)],
            ]
            self._i = 0
            self.agent = None

        async def run_async(self, **_kwargs: object):
            events = self._script[min(self._i, len(self._script) - 1)]
            self._i += 1
            for event in events:
                yield event

    _events, result = asyncio.run(
        _drive_governed_turn_path(
            request,
            config,
            engine_runner=_SequenceRunner(),
            adk_tools=(_ManualCalculationTool,),
            no_tool_finalizer=NoToolFinalizerConfig(),
        )
    )

    # Parity target (legacy no-tool finalizer): a completed turn with an answer.
    assert result.status == "completed"
    assert result.output_text_internal
    assert "2" in result.output_text_internal
