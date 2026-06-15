"""Regression tests for root cause 2: FileRead's sourceProjection reaching the
collector on the LIVE path.

Live-instrumented finding: the CLI FileRead does NOT execute through
``LocalReadOnlyToolHost`` (which attaches ``metadata['sourceProjection']``).
It runs through ``Gate5BFullToolHost`` and the result is rebuilt by
``core_toolhost._tool_result_from_outcome``, which only carries
``gate5bFullToolhostReceipt`` — never a ``sourceProjection``. So a real
FileRead reached ``record_tool_result`` with ``hasSourceProj=False`` and the
source-ledger gate had no SourceInspection record to match.

The fix synthesizes a ``sourceProjection`` for read-only source tools in
``_tool_result_from_outcome`` ONLY when the default-OFF
``MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED`` flag is on, so a real FileRead now
carries the metadata the collector's projector reads. Flag OFF stays
byte-identical.
"""

from __future__ import annotations

import pytest

from magi_agent.evidence.local_tool_collector import (
    _projected_source_inspection_records,
)
from magi_agent.tools.core_toolhost import _tool_result_from_outcome


def _make_filread_outcome():
    from magi_agent.gates.gate5b_full_toolhost import (
        Gate5BFullToolOutcome,
        Gate5BFullToolReceipt,
    )

    receipt = Gate5BFullToolReceipt.model_validate(
        {
            "requestDigest": "sha256:" + "a" * 64,
            "toolCallDigest": "sha256:" + "b" * 64,
            "toolName": "FileRead",
            "status": "ok",
            "boundedOutputDigest": "sha256:" + "c" * 64,
            "outputByteCount": 42,
        }
    )
    return Gate5BFullToolOutcome.model_validate(
        {
            "status": "ok",
            "reason": "ok",
            "receipt": receipt,
            "outputPreview": {"path": "README.md", "content": "# hi"},
            "handlerCalled": True,
        }
    )


def test_gate5b_fileread_outcome_carries_source_projection_when_flag_on(
    monkeypatch,
) -> None:
    """RED→GREEN: with the source-ledger gate flag ON a Gate5B FileRead outcome
    must carry a ``sourceProjection`` that yields a SourceInspection record."""
    monkeypatch.setenv("MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED", "1")
    result = _tool_result_from_outcome(_make_filread_outcome())

    assert "sourceProjection" in result.metadata, (
        "live FileRead must carry sourceProjection metadata when the gate is on"
    )
    projected = _projected_source_inspection_records(result.metadata)
    assert projected, "expected a SourceInspection projected record"
    assert all(
        getattr(rec, "type", None) == "SourceInspection" for rec in projected
    )


def test_gate5b_fileread_outcome_no_source_projection_when_flag_off(
    monkeypatch,
) -> None:
    """Regression guard: flag OFF (default) -> no sourceProjection key, so the
    metadata shape is byte-identical to main."""
    monkeypatch.delenv("MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED", raising=False)
    result = _tool_result_from_outcome(_make_filread_outcome())
    assert "sourceProjection" not in result.metadata


def test_gate5b_nonsource_tool_no_source_projection_when_flag_on(
    monkeypatch,
) -> None:
    """A non-source tool (e.g. Bash) must NOT get a synthesized sourceProjection
    even with the flag on — only read-only source tools inspect sources."""
    monkeypatch.setenv("MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED", "1")
    from magi_agent.gates.gate5b_full_toolhost import (
        Gate5BFullToolOutcome,
        Gate5BFullToolReceipt,
    )

    receipt = Gate5BFullToolReceipt.model_validate(
        {
            "requestDigest": "sha256:" + "a" * 64,
            "toolCallDigest": "sha256:" + "b" * 64,
            "toolName": "Bash",
            "status": "ok",
            "boundedOutputDigest": "sha256:" + "c" * 64,
            "outputByteCount": 10,
        }
    )
    outcome = Gate5BFullToolOutcome.model_validate(
        {
            "status": "ok",
            "reason": "ok",
            "receipt": receipt,
            "outputPreview": {"stdout": "ok"},
            "handlerCalled": True,
        }
    )
    result = _tool_result_from_outcome(outcome)
    assert "sourceProjection" not in result.metadata


def test_collector_projects_source_inspection_from_fileread_shaped_result(
    monkeypatch,
) -> None:
    """Task-2 acceptance: a FileRead-shaped result (with the synthesized
    sourceProjection) yields a SourceInspection projected record through
    ``record_tool_result`` end-to-end."""
    monkeypatch.setenv("MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED", "1")
    from magi_agent.evidence.local_tool_collector import LocalToolEvidenceCollector

    result = _tool_result_from_outcome(_make_filread_outcome())
    collector = LocalToolEvidenceCollector()
    records = collector.record_tool_result(
        session_id="s",
        turn_id="t",
        tool_call_id="c",
        tool_name="FileRead",
        result=result,
    )
    source_records = [
        rec for rec in records if getattr(rec, "type", None) == "SourceInspection"
    ]
    assert source_records, "record_tool_result must project a SourceInspection record"
