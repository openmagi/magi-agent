from __future__ import annotations

from magi_agent.observability.runtime_sink import (
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
