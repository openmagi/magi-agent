import asyncio

from magi_agent.channels.workflow_orchestrator import (
    WorkflowOrchestratorResult,
    resolve_confirmation,
    route_inbound,
    start_research,
)
from magi_agent.channels.workflow_confirm_store import InMemoryPendingConfirmationStore


class _Classifier:
    def __init__(self, kind: str) -> None:
        self._kind = kind

    def classify(self, message_text: str) -> str:
        return self._kind


_RATES = dict(per_child_token_estimate=8000, model_microcents_per_1k=120)


def test_channel_off_returns_normal_llm(monkeypatch):
    monkeypatch.delenv("MAGI_CHANNEL_WORKFLOWS_ENABLED", raising=False)
    store = InMemoryPendingConfirmationStore()
    out = route_inbound("compare X vs Y", session_id="s", classifier=_Classifier("complex_synthesis"), store=store, **_RATES)
    assert out.outcome == "normal_llm"


def test_ineligible_returns_normal_llm(monkeypatch):
    monkeypatch.setenv("MAGI_CHANNEL_WORKFLOWS_ENABLED", "1")
    store = InMemoryPendingConfirmationStore()
    out = route_inbound("hi", session_id="s", classifier=_Classifier("general"), store=store, **_RATES)
    assert out.outcome == "normal_llm"
    assert store.pop("s") is None


def test_eligible_awaits_confirmation_and_stores(monkeypatch):
    monkeypatch.setenv("MAGI_CHANNEL_WORKFLOWS_ENABLED", "1")
    store = InMemoryPendingConfirmationStore()
    out = route_inbound("compare X vs Y", session_id="s", classifier=_Classifier("source_sensitive_research"), store=store, **_RATES)
    assert out.outcome == "awaiting_confirmation"
    assert out.message  # confirm prompt present
    # still pending in the store
    assert store.pop("s") is not None


def test_start_research_awaits_confirmation(monkeypatch):
    monkeypatch.setenv("MAGI_CHANNEL_WORKFLOWS_ENABLED", "1")
    store = InMemoryPendingConfirmationStore()
    out = start_research("q", session_id="s", store=store, **_RATES)
    assert out.outcome == "awaiting_confirmation"
    assert store.pop("s") is not None


def test_resolve_no_pending(monkeypatch):
    store = InMemoryPendingConfirmationStore()
    out = asyncio.run(resolve_confirmation("예", session_id="s", store=store))
    assert out.outcome == "not_pending"


def test_resolve_decline(monkeypatch):
    monkeypatch.setenv("MAGI_CHANNEL_WORKFLOWS_ENABLED", "1")
    store = InMemoryPendingConfirmationStore()
    start_research("q", session_id="s", store=store, **_RATES)
    out = asyncio.run(resolve_confirmation("아니", session_id="s", store=store))
    assert out.outcome == "declined"
    # pending consumed
    assert store.pop("s") is None


def test_resolve_affirm_but_executor_disabled_does_not_execute(monkeypatch):
    monkeypatch.setenv("MAGI_CHANNEL_WORKFLOWS_ENABLED", "1")
    monkeypatch.delenv("MAGI_WORKFLOW_EXECUTOR_ENABLED", raising=False)
    store = InMemoryPendingConfirmationStore()
    start_research("q", session_id="s", store=store, **_RATES)
    out = asyncio.run(resolve_confirmation("예", session_id="s", store=store))
    # executor disabled -> route not taken -> declined, never executed
    assert out.outcome == "declined"


def test_resolve_affirm_with_executor_enabled_executes(monkeypatch):
    monkeypatch.setenv("MAGI_CHANNEL_WORKFLOWS_ENABLED", "1")
    monkeypatch.setenv("MAGI_WORKFLOW_EXECUTOR_ENABLED", "1")
    store = InMemoryPendingConfirmationStore()
    start_research("q", session_id="s", store=store, **_RATES)
    out = asyncio.run(resolve_confirmation("예", session_id="s", store=store))
    assert out.outcome == "executed"
    assert isinstance(out.executor_status, str) and out.executor_status


def test_model_construct_disabled():
    import pytest
    with pytest.raises(TypeError):
        WorkflowOrchestratorResult.model_construct()
