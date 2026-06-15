from __future__ import annotations

from magi_agent.observability.runtime_sink import (
    combine_sinks,
    get_active_sink,
    set_active_sink,
)


def test_default_is_none():
    set_active_sink(None)
    assert get_active_sink() is None


def test_set_get_roundtrip():
    calls = []

    def my_sink(payload, session_id, turn_id):
        calls.append((payload, session_id, turn_id))

    set_active_sink(my_sink)
    assert get_active_sink() is my_sink
    set_active_sink(None)
    assert get_active_sink() is None


def test_clear_resets_to_none():
    set_active_sink(lambda p, s, t: None)
    set_active_sink(None)
    assert get_active_sink() is None


def test_combine_sinks_fans_out_to_all():
    a_calls = []
    b_calls = []
    combined = combine_sinks(
        [
            lambda e, s, t: a_calls.append((e, s, t)),
            lambda e, s, t: b_calls.append((e, s, t)),
        ]
    )
    combined({"type": "tool_call"}, "s1", "t1")
    assert a_calls == [({"type": "tool_call"}, "s1", "t1")]
    assert b_calls == [({"type": "tool_call"}, "s1", "t1")]


def test_combine_sinks_isolates_failures():
    good_calls = []

    def boom(e, s, t):
        raise RuntimeError("sink down")

    combined = combine_sinks([boom, lambda e, s, t: good_calls.append(e)])
    # A failing sink must not block the others, and must not raise.
    combined({"type": "turn_end"}, "s", "t")
    assert good_calls == [{"type": "turn_end"}]


def test_combine_sinks_filters_none_and_empty():
    assert combine_sinks([]) is None
    assert combine_sinks([None, None]) is None

    seen = []
    combined = combine_sinks([None, lambda e, s, t: seen.append(e), None])
    combined({"type": "x"}, "s", "t")
    assert seen == [{"type": "x"}]


def test_build_headless_runtime_uses_global_sink():
    from magi_agent.cli import wiring
    from magi_agent.observability import runtime_sink

    sink = lambda payload, session_id, turn_id: None  # noqa: E731
    runtime_sink.set_active_sink(sink)
    try:
        hr = wiring.build_headless_runtime(session_id="obs-test")
        assert hr.engine._event_sink is sink
    finally:
        runtime_sink.set_active_sink(None)


def test_build_headless_runtime_fans_out_to_transcript():
    from magi_agent.cli import wiring
    from magi_agent.observability import runtime_sink
    from magi_agent.observability.transcript import set_active_transcript_sink

    obs_calls = []
    tr_calls = []
    runtime_sink.set_active_sink(lambda e, s, t: obs_calls.append(e))
    set_active_transcript_sink(lambda e, s, t: tr_calls.append(e))
    try:
        hr = wiring.build_headless_runtime(session_id="fanout-test")
        hr.engine._event_sink({"type": "tool_call"}, "s", "t")
        assert obs_calls == [{"type": "tool_call"}]
        assert tr_calls == [{"type": "tool_call"}]
    finally:
        runtime_sink.set_active_sink(None)
        set_active_transcript_sink(None)
