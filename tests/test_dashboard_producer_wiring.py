"""Wiring: _build_dashboard_producer_controls is flag-gated and fail-soft."""
from __future__ import annotations

import pytest

from magi_agent.adk_bridge.dashboard_producer_control import DashboardProducerControl
from magi_agent.cli.real_runner import _build_dashboard_producer_controls


class _StubCollector:
    def append_evidence_record_for_turn(self, **_kwargs: object) -> None:  # pragma: no cover
        return None


def test_flag_off_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAGI_DASHBOARD_PACK_AUTHORING_ENABLED", raising=False)
    assert _build_dashboard_producer_controls(_StubCollector()) == []


def test_flag_on_registers_control(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_DASHBOARD_PACK_AUTHORING_ENABLED", "1")
    controls = _build_dashboard_producer_controls(_StubCollector())
    assert len(controls) == 1
    assert isinstance(controls[0], DashboardProducerControl)
