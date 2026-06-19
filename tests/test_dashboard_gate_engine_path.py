"""Engine-path regression for the dashboard deny-on-present gate.

This is the test that would have caught the silent-inert wiring bug: the
dashboard producer keys its emitted ``custom:DashboardCheck`` record under the
ADK ``invocation_id`` (``tool_context.invocation_id``), but the engine's
pre-final gate queries ``_collect_evidence`` with the engine's STATIC turn id
(``"cli-turn"``). They never match, so the reconciliation fold in
``MagiEngineDriver._collect_evidence`` is the only bridge.

Before the fix that fold was armed SOLELY behind
``MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED``; with only
``MAGI_DASHBOARD_PACK_AUTHORING_ENABLED=1`` a matching ``block`` check emitted a
``status="failed"`` record the gate never collected -> fail-open leak.

These tests construct the REAL ``MagiEngineDriver`` exactly the way
``magi_agent/cli/tests/test_evidence_turn_id_reconciliation.py`` does and drive
the REAL ``LocalToolEvidenceCollector`` via the REAL producer, then assert the
engine's static-turn-id collect surfaces the dashboard record only when the
dashboard flag arms reconciliation (and NOT when both flags are off).
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from magi_agent.adk_bridge.dashboard_producer_control import DashboardProducerControl
from magi_agent.cli.engine import MagiEngineDriver
from magi_agent.evidence.local_tool_collector import LocalToolEvidenceCollector
from magi_agent.packs.dashboard_authored import (
    DASHBOARD_PACK_DIR_NAME,
    DashboardCheck,
    write_pack,
)

# The ADK invocation id the producer keys under — DIFFERENT from the engine's
# static turn id below, which is the whole point of the bug.
_ADK_INVOCATION_ID = "e-abc-live"
_ENGINE_TURN_ID = "cli-turn"


class _FakeSession:
    def __init__(self, session_id: str) -> None:
        self.id = session_id


class _FakeCtx:
    def __init__(self, *, invocation_id: str, session_id: str) -> None:
        self.invocation_id = invocation_id
        self.session = _FakeSession(session_id)


class _FakeTool:
    def __init__(self, name: str) -> None:
        self.name = name


def _emit_failed_dashboard_record(
    collector: LocalToolEvidenceCollector, tmp_path: Path
) -> None:
    """Run the REAL producer with a matching ``block`` check so it stores a
    ``status="failed"`` record under the ADK invocation id (NOT the engine turn id)."""
    check = DashboardCheck.model_validate(
        {
            "id": "no-ssn",
            "label": "no ssn",
            "scope": "always",
            "enabled": True,
            "trigger": {
                "tool": "web_fetch",
                "match": {"pattern": "ssn", "isRegex": False},
            },
            "action": "block",
        }
    )
    write_pack(tmp_path / DASHBOARD_PACK_DIR_NAME, [check])
    control = DashboardProducerControl(
        collector=collector, search_bases=lambda: [tmp_path]
    )
    asyncio.run(
        control.on_after_tool(
            tool=_FakeTool("web_fetch"),
            args={},
            tool_context=_FakeCtx(
                invocation_id=_ADK_INVOCATION_ID, session_id="s-1"
            ),
            result="contains ssn",
        )
    )


def test_dashboard_flag_arms_engine_path_reconciliation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RED→GREEN: with ONLY the dashboard flag on (source-ledger flag unset), the
    engine's static-turn-id ``_collect_evidence`` must surface the dashboard
    record the producer keyed under the ADK invocation id."""
    monkeypatch.setenv("MAGI_DASHBOARD_PACK_AUTHORING_ENABLED", "1")
    monkeypatch.delenv("MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED", raising=False)

    collector = LocalToolEvidenceCollector()
    _emit_failed_dashboard_record(collector, tmp_path)

    # Sanity: producer keyed under the ADK id, NOT the engine's static turn id.
    assert collector.collect_for_turn(_ADK_INVOCATION_ID) != ()
    assert collector.collect_for_turn(_ENGINE_TURN_ID) == ()

    engine = MagiEngineDriver(
        runner=object(),
        evidence_collector=collector.collect_for_turn,
    )
    engine._note_observed_invocation_id(_ADK_INVOCATION_ID)

    records = engine._collect_evidence(_ENGINE_TURN_ID)
    assert records, "dashboard flag must arm the invocation-id reconciliation fold"
    assert any(
        getattr(r, "type", None) == "custom:DashboardCheck"
        and getattr(r, "status", None) == "failed"
        for r in records
    ), "the failed dashboard record must be visible to the engine's static-turn gate"


def test_both_flags_off_engine_path_byte_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default-OFF guard: with BOTH the dashboard flag and the source-ledger flag
    off, the reconciliation fold is NOT armed, so the engine's static-turn-id
    collect does NOT surface the producer's ADK-keyed record (byte-identical to
    pre-seam behaviour)."""
    # Producer needs the dashboard flag ON to emit at all; emit, then clear both
    # flags so we exercise the COLLECT side with both gates off.
    monkeypatch.setenv("MAGI_DASHBOARD_PACK_AUTHORING_ENABLED", "1")
    collector = LocalToolEvidenceCollector()
    _emit_failed_dashboard_record(collector, tmp_path)
    assert collector.collect_for_turn(_ADK_INVOCATION_ID) != ()

    monkeypatch.delenv("MAGI_DASHBOARD_PACK_AUTHORING_ENABLED", raising=False)
    monkeypatch.delenv("MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED", raising=False)

    engine = MagiEngineDriver(
        runner=object(),
        evidence_collector=collector.collect_for_turn,
    )
    engine._note_observed_invocation_id(_ADK_INVOCATION_ID)

    # Both flags off => fold not armed => gate sees only its own turn id (empty).
    assert engine._collect_evidence(_ENGINE_TURN_ID) == ()
