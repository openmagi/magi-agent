from __future__ import annotations

import pytest

from magi_agent.runtime.manual_compaction_context import (
    MAGI_COMPACTION_MANUAL_ENABLED_ENV,
    consume_manual_compaction,
    manual_compaction_enabled,
    request_manual_compaction,
    reset_manual_compaction,
)


@pytest.fixture(autouse=True)
def _isolate_signal():
    # Process-global state leaks across tests; reset before AND after each test.
    reset_manual_compaction()
    yield
    reset_manual_compaction()


def test_request_then_consume_is_true_exactly_once() -> None:
    request_manual_compaction()
    assert consume_manual_compaction() is True
    # One-shot: a second consume with no new request returns False.
    assert consume_manual_compaction() is False


def test_consume_without_request_is_false() -> None:
    assert consume_manual_compaction() is False


def test_multiple_requests_collapse_to_one_true() -> None:
    request_manual_compaction()
    request_manual_compaction()
    request_manual_compaction()
    assert consume_manual_compaction() is True
    assert consume_manual_compaction() is False


def test_reset_clears_pending_request() -> None:
    request_manual_compaction()
    reset_manual_compaction()
    assert consume_manual_compaction() is False


def test_enabled_false_when_unset(monkeypatch) -> None:
    monkeypatch.delenv(MAGI_COMPACTION_MANUAL_ENABLED_ENV, raising=False)
    assert manual_compaction_enabled() is False


def test_enabled_false_for_garbage(monkeypatch) -> None:
    monkeypatch.setenv(MAGI_COMPACTION_MANUAL_ENABLED_ENV, "nope")
    assert manual_compaction_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", " On "])
def test_enabled_true_for_truthy(monkeypatch, value: str) -> None:
    monkeypatch.setenv(MAGI_COMPACTION_MANUAL_ENABLED_ENV, value)
    assert manual_compaction_enabled() is True
