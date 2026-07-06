"""U8 (B4) tests: continuity fields on the collector + transcript records on the
governed hosted serving path.

Part A -- continuity fields:
    collect_engine_to_boundary_result gains three optional kwargs
    (session_reused / session_event_count / seeded_message_count) that flow onto
    the returned Gate5B4C3LiveRunnerBoundaryResult. Omitting them keeps the
    result byte-identical (False / 0 / 0).

Part B -- transcript records:
    Under the flag-ON governed path, a registered transcript sink receives the
    same record family the legacy boundary emits: turn_start (with the three
    continuity fields), message + turn_end on completion, and tool_call /
    tool_result records translated from the engine-native public tool events
    (decision D4: preserve the legacy record TYPES via a translation shim in the
    composed driver sink).
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from magi_agent.app import create_app
from magi_agent.cli.contracts import EngineResult, Terminal
from magi_agent.observability.transcript import (
    governed_transcript_event_sink,
    public_tool_event_to_transcript_record,
    set_active_transcript_sink,
)
from magi_agent.shadow.gate5b4c3_live_runner_boundary import (
    Gate5B4C3LiveRunnerBoundaryResult,
)
from magi_agent.transport.hosted_engine_result import collect_engine_to_boundary_result

# Reuse the collector fixtures (payload builder, config, diagnostic, fake gen).
from tests.test_hosted_engine_result import (
    _config,
    _diagnostic,
    _fake_gen,
    _ok_terminal,
    _request,
    _text_delta,
)

# Reuse the fully-wired canary runtime + headers from the flip route tests.
from tests.test_chat_routes_hosted_governed_turn import (
    _FakeSessionService,
    _canary_headers,
    _CANARY_BODY,
    _make_boundary_result,
)
from tests import test_chat_routes_hosted_governed_turn as _flip


# ---------------------------------------------------------------------------
# Part A: continuity fields on the collector
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_collector_sets_continuity_fields_when_passed() -> None:
    """Passing the three kwargs sets the three result fields."""
    generation = _request()
    diag = _diagnostic(generation)
    result = await collect_engine_to_boundary_result(
        generation=generation,
        config=_config(),
        diagnostic=diag,
        event_stream=_fake_gen([_text_delta("hi")], _ok_terminal()),
        started_at_monotonic=0.0,
        session_reused=True,
        session_event_count=7,
        seeded_message_count=3,
    )
    assert result.session_reused is True
    assert result.session_event_count == 7
    assert result.seeded_message_count == 3


@pytest.mark.asyncio
async def test_collector_continuity_fields_default_to_false_zero_zero() -> None:
    """Omitting the kwargs keeps the result byte-identical (False / 0 / 0)."""
    generation = _request()
    diag = _diagnostic(generation)
    result = await collect_engine_to_boundary_result(
        generation=generation,
        config=_config(),
        diagnostic=diag,
        event_stream=_fake_gen([_text_delta("hi")], _ok_terminal()),
        started_at_monotonic=0.0,
    )
    assert result.session_reused is False
    assert result.session_event_count == 0
    assert result.seeded_message_count == 0


# ---------------------------------------------------------------------------
# Translator unit (D4 shim): public tool event -> legacy record TYPE
# ---------------------------------------------------------------------------


def test_public_tool_start_translates_to_tool_call_record() -> None:
    record = public_tool_event_to_transcript_record(
        {"type": "tool_start", "id": "tu_1", "name": "Bash", "input_preview": '{"path":"x"}'}
    )
    assert record is not None
    assert record["type"] == "tool_call"
    assert record["tool_name"] == "Bash"
    assert record["call_id"] == "tu_1"
    # D4: full args are not on the public event; only a bounded preview is.
    assert record["args_preview"] == '{"path":"x"}'


def test_public_tool_end_translates_to_tool_result_record() -> None:
    record = public_tool_event_to_transcript_record(
        {"type": "tool_end", "id": "tu_1", "status": "ok", "output_preview": "result:sha"}
    )
    assert record is not None
    assert record["type"] == "tool_result"
    assert record["call_id"] == "tu_1"
    assert record["status"] == "ok"
    assert record["output_preview"] == "result:sha"


def test_non_tool_public_event_translates_to_none() -> None:
    assert public_tool_event_to_transcript_record({"type": "text_delta", "delta": "hi"}) is None
    assert public_tool_event_to_transcript_record({"type": "turn_phase"}) is None
    assert public_tool_event_to_transcript_record("not-a-mapping") is None  # type: ignore[arg-type]


def test_composed_sink_emits_translated_records_and_forwards_inner() -> None:
    captured: list[tuple] = []
    set_active_transcript_sink(lambda e, s, t: captured.append((e, s, t)))
    # A 3-arg inner sink (combine_sinks members are called with 3 args) proves the
    # composed sink forwards the ORIGINAL public event alongside the translation.
    forwarded: list[tuple] = []
    try:
        composed = governed_transcript_event_sink(
            lambda event, s, t: forwarded.append((event, s, t))
        )
        assert composed is not None
        composed({"type": "tool_start", "id": "tu_9", "name": "Grep"}, "sess", "turn")
        composed({"type": "tool_end", "id": "tu_9", "status": "error"}, "sess", "turn")
        types = [e["type"] for (e, _s, _t) in captured]
        assert types == ["tool_call", "tool_result"]
        assert captured[0][1] == "sess" and captured[0][2] == "turn"
        # The original public events are forwarded to the inner sink unchanged.
        assert [ev["type"] for (ev, _s, _t) in forwarded] == ["tool_start", "tool_end"]
    finally:
        set_active_transcript_sink(None)


def test_composed_sink_preserves_one_arg_public_sink_contract() -> None:
    """The hosted public sink is a 1-arg ``(event)`` callable. combine_sinks
    calls members with 3 args and guards each, so a 1-arg inner sink is dropped
    fail-open -- byte-identical to the pre-U8 driver behavior (the driver already
    called the 1-arg public sink with 3 args). The transcript records still flow;
    this unit does not change the public-event path."""
    captured: list[tuple] = []
    set_active_transcript_sink(lambda e, s, t: captured.append((e, s, t)))
    one_arg_calls: list[object] = []
    try:
        composed = governed_transcript_event_sink(
            lambda event: one_arg_calls.append(event)
        )
        composed({"type": "tool_start", "id": "tu_1", "name": "Bash"}, "sess", "turn")
        # transcript record emitted (U8 addition) ...
        assert [e["type"] for (e, _s, _t) in captured] == ["tool_call"]
        # ... while the 1-arg public sink is dropped fail-open (unchanged path).
        assert one_arg_calls == []
    finally:
        set_active_transcript_sink(None)


def test_composed_sink_is_identity_when_no_transcript_sink() -> None:
    """When no transcript sink is registered, the inner sink is returned
    unchanged so the driver event_sink path stays byte-identical."""
    set_active_transcript_sink(None)
    inner = object()
    assert governed_transcript_event_sink(inner) is inner


# ---------------------------------------------------------------------------
# Part B: serving-level transcript records on the governed path
# ---------------------------------------------------------------------------


class _FakeLease:
    def __init__(self, *, reused: bool, service: object) -> None:
        self.reused = reused
        self.service = service
        self.released: list[bool] = []

    def release(self, *, seeded: bool) -> None:
        self.released.append(seeded)


def _register_capturing_transcript_sink() -> list[tuple]:
    captured: list[tuple] = []
    set_active_transcript_sink(lambda e, s, t: captured.append((dict(e), s, t)))
    return captured


def _drive_flag_on_governed_turn(
    monkeypatch, tmp_path: Any, *, capture_sink: dict | None = None
) -> None:
    """Drive one flag-ON governed turn through the real serving path with the
    ownership seam faked to a REUSED, populated session. run_governed_turn is
    faked to a text_delta + terminal stream; collect is REAL so message /
    turn_end read a genuine boundary result."""
    monkeypatch.setenv("CORE_AGENT_PYTHON_CHAT_ROUTE", "on")
    monkeypatch.setenv("MAGI_HOSTED_GOVERNED_TURN_ENABLED", "1")

    lease = _FakeLease(reused=True, service=_FakeSessionService())

    def fake_acquire(**kwargs: object) -> _FakeLease:
        return lease

    async def fake_probe(*args: object, **kwargs: object) -> int:
        return 4

    def fake_build_hosted_runtime(**kwargs: object) -> object:
        if capture_sink is not None:
            capture_sink["public_event_sink"] = kwargs.get("public_event_sink")
        return SimpleNamespace()

    async def _gen():  # noqa: ANN202
        yield {"type": "text_delta", "delta": "hi"}
        yield EngineResult(terminal=Terminal.completed, session_id="s", turn_id="t")

    def fake_governed_turn(ctx: object, *, runtime: object, cancel: object = None):  # noqa: ANN201
        return _gen()

    monkeypatch.setattr("magi_agent.transport.gate5b_serving.acquire_hosted_session_lease", fake_acquire)
    monkeypatch.setattr("magi_agent.transport.gate5b_serving.probe_session_event_count", fake_probe)
    monkeypatch.setattr("magi_agent.transport.gate5b_serving.build_hosted_runtime", fake_build_hosted_runtime)
    monkeypatch.setattr("magi_agent.transport.gate5b_serving.run_governed_turn", fake_governed_turn)

    runtime = _flip._make_canary_runtime(tmp_path)
    response = TestClient(create_app(runtime)).post(
        "/v1/chat/completions",
        headers=_canary_headers("b" * 64),
        json={**_CANARY_BODY, "sessionId": "sess-obs"},
    )
    assert response.status_code == 200, response.json()


def test_flag_on_reused_session_emits_turn_start_message_turn_end(monkeypatch, tmp_path: Any) -> None:
    captured = _register_capturing_transcript_sink()
    try:
        _drive_flag_on_governed_turn(monkeypatch, tmp_path)
    finally:
        set_active_transcript_sink(None)

    by_type = {e["type"]: e for (e, _s, _t) in captured}
    assert "turn_start" in by_type, [e["type"] for (e, _s, _t) in captured]
    turn_start = by_type["turn_start"]
    assert turn_start["session_reused"] is True
    assert turn_start["session_event_count"] == 4
    assert turn_start["seeded_message_count"] == 0

    assert "message" in by_type
    assert by_type["message"]["role"] == "assistant"
    assert by_type["message"]["content"] == "hi"

    assert "turn_end" in by_type
    assert by_type["turn_end"]["terminal"] == "completed"

    # ordering: turn_start precedes message precedes turn_end
    order = [e["type"] for (e, _s, _t) in captured if e["type"] in {"turn_start", "message", "turn_end"}]
    assert order == ["turn_start", "message", "turn_end"]


def test_flag_on_tool_events_reach_transcript_as_legacy_record_types(monkeypatch, tmp_path: Any) -> None:
    """D4: the composed public_event_sink wired into build_hosted_runtime
    translates engine-native tool_start / tool_end public events into the legacy
    tool_call / tool_result transcript record TYPES."""
    captured = _register_capturing_transcript_sink()
    capture_sink: dict = {}
    try:
        _drive_flag_on_governed_turn(monkeypatch, tmp_path, capture_sink=capture_sink)
        sink = capture_sink.get("public_event_sink")
        assert sink is not None, "build_hosted_runtime must receive a composed public_event_sink"
        # Drive the composed sink with the wire-shape tool events the driver emits.
        sink({"type": "tool_start", "id": "tu_1", "name": "Bash", "input_preview": "{}"}, "sess", "turn")
        sink({"type": "tool_end", "id": "tu_1", "status": "ok", "output_preview": "result:x"}, "sess", "turn")
    finally:
        set_active_transcript_sink(None)

    tool_records = [e for (e, _s, _t) in captured if e["type"] in {"tool_call", "tool_result"}]
    types = [e["type"] for e in tool_records]
    assert types == ["tool_call", "tool_result"]
    assert tool_records[0]["tool_name"] == "Bash"
    assert tool_records[0]["call_id"] == "tu_1"
    assert tool_records[1]["call_id"] == "tu_1"
    assert tool_records[1]["status"] == "ok"
