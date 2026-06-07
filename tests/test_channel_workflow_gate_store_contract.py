import pytest

from magi_agent.channels.workflow_gate import (
    channel_workflows_enabled,
    executor_enabled,
)
from magi_agent.channels.workflow_confirm_store import (
    InMemoryPendingConfirmationStore,
)
from magi_agent.channels.research_command import prepare_research_command


def _pending():
    return prepare_research_command(
        query="compare X vs Y",
        per_child_token_estimate=8000,
        model_microcents_per_1k=120,
    )


def test_gates_default_off(monkeypatch):
    monkeypatch.delenv("MAGI_WORKFLOW_EXECUTOR_ENABLED", raising=False)
    monkeypatch.delenv("MAGI_CHANNEL_WORKFLOWS_ENABLED", raising=False)
    assert executor_enabled() is False
    assert channel_workflows_enabled() is False


@pytest.mark.parametrize("val", ["1", "true", "YES", "on"])
def test_gates_truthy_values(monkeypatch, val):
    monkeypatch.setenv("MAGI_WORKFLOW_EXECUTOR_ENABLED", val)
    monkeypatch.setenv("MAGI_CHANNEL_WORKFLOWS_ENABLED", val)
    assert executor_enabled() is True
    assert channel_workflows_enabled() is True


def test_gates_falsy_value(monkeypatch):
    monkeypatch.setenv("MAGI_WORKFLOW_EXECUTOR_ENABLED", "0")
    assert executor_enabled() is False


def test_store_put_pop_roundtrip():
    store = InMemoryPendingConfirmationStore()
    p = _pending()
    store.put("sess-1", p)
    got = store.pop("sess-1")
    assert got is p
    # pop removes it
    assert store.pop("sess-1") is None


def test_store_pop_absent_returns_none():
    store = InMemoryPendingConfirmationStore()
    assert store.pop("nope") is None


def test_store_clear():
    store = InMemoryPendingConfirmationStore()
    store.put("s", _pending())
    store.clear("s")
    assert store.pop("s") is None
