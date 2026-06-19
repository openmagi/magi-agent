"""End-to-end: DashboardProducerControl emit -> collector -> verifier-bus block.

This is the #1 silent-inert risk for the dashboard deny-on-present feature: the
producer must emit a record under the SAME (session, turn) key that the engine's
``_collect_evidence(turn_id)`` reads, and that record must drive the verifier-bus
gate to ``"block"``. Uses the REAL ``LocalToolEvidenceCollector`` and REAL
``execute_pre_final_verifier_bus`` (no fakes on the wiring path).
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from magi_agent.adk_bridge.dashboard_producer_control import DashboardProducerControl
from magi_agent.evidence.local_tool_collector import LocalToolEvidenceCollector
from magi_agent.harness.verifier_bus import execute_pre_final_verifier_bus
from magi_agent.packs.dashboard_authored import (
    DASHBOARD_PACK_DIR_NAME,
    DashboardCheck,
    write_pack,
)


class FakeSession:
    def __init__(self, session_id: str) -> None:
        self.id = session_id


class FakeCtx:
    def __init__(self, *, invocation_id: str, session_id: str) -> None:
        self.invocation_id = invocation_id
        self.session = FakeSession(session_id)


class FakeTool:
    def __init__(self, name: str) -> None:
        self.name = name


def test_producer_to_gate_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAGI_DASHBOARD_PACK_AUTHORING_ENABLED", "1")
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

    collector = LocalToolEvidenceCollector()
    control = DashboardProducerControl(
        collector=collector, search_bases=lambda: [tmp_path]
    )

    out = asyncio.run(
        control.on_after_tool(
            tool=FakeTool("web_fetch"),
            args={},
            tool_context=FakeCtx(invocation_id="inv-1", session_id="s-1"),
            result="contains ssn",
        )
    )
    assert out is None

    collected = collector.collect_for_turn("inv-1")
    assert len(collected) == 1
    assert collected[0].status == "failed"
    assert collected[0].type == "custom:DashboardCheck"

    result = execute_pre_final_verifier_bus(
        required_evidence=(),
        required_validators=(),
        observed_public_refs=(),
        evidence_records=collected,
        dashboard_gate_enabled=True,
    )
    assert result["decision"] == "block"
    assert result["failedDashboardChecks"] == 1


def test_producer_non_matching_result_no_false_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Negative path: a NON-matching after-tool result emits no record, so the
    collector is empty for the turn AND the verifier-bus gate passes (no false
    block) even with the dashboard gate enabled."""
    monkeypatch.setenv("MAGI_DASHBOARD_PACK_AUTHORING_ENABLED", "1")
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

    collector = LocalToolEvidenceCollector()
    control = DashboardProducerControl(
        collector=collector, search_bases=lambda: [tmp_path]
    )

    out = asyncio.run(
        control.on_after_tool(
            tool=FakeTool("web_fetch"),
            args={},
            tool_context=FakeCtx(invocation_id="inv-2", session_id="s-2"),
            result="totally clean content",
        )
    )
    assert out is None

    collected = collector.collect_for_turn("inv-2")
    assert collected == ()

    result = execute_pre_final_verifier_bus(
        required_evidence=(),
        required_validators=(),
        observed_public_refs=(),
        evidence_records=collected,
        dashboard_gate_enabled=True,
    )
    assert result["decision"] == "pass"
    assert result["failedDashboardChecks"] == 0
