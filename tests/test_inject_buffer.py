"""Shared inject-buffer used by the chat consumer and background-task sink."""

from magi_agent.missions.work_queue import inject_buffer as ib


def test_enqueue_returns_size():
    ib.reset_for_tests()
    assert ib.enqueue("s1", "alpha") == 1
    assert ib.enqueue("s1", "beta") == 2


def test_drain_returns_pending_and_clears():
    ib.reset_for_tests()
    ib.enqueue("s1", "alpha")
    ib.enqueue("s1", "beta")
    assert ib.drain("s1") == ["alpha", "beta"]
    assert ib.drain("s1") == []                 # idempotent — empty after drain


def test_drain_unknown_session_is_empty():
    ib.reset_for_tests()
    assert ib.drain("nobody") == []


def test_sessions_are_isolated():
    ib.reset_for_tests()
    ib.enqueue("a", "x")
    ib.enqueue("b", "y")
    assert ib.drain("a") == ["x"]
    assert ib.drain("b") == ["y"]


def test_enqueue_blank_text_is_noop():
    ib.reset_for_tests()
    assert ib.enqueue("s1", "   ") == 0
    assert ib.drain("s1") == []
