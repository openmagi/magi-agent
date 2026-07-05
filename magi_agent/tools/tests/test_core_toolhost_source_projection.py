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


def test_src_1_id_collision_exists_on_multiple_fileread_calls_legacy_flag_on(
    monkeypatch,
) -> None:
    """RED: _synthesized_source_projection hardcodes src_1 on every call.

    Two FileRead outcomes both get src_1 in their sourceProjection when
    only the LEGACY flag (MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED) is on.
    This demonstrates the collision that the citation-path fix addresses.
    """
    monkeypatch.setenv("MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED", "1")
    monkeypatch.delenv("MAGI_SOURCE_CITATION_ENABLED", raising=False)
    result1 = _tool_result_from_outcome(_make_filread_outcome())
    result2 = _tool_result_from_outcome(_make_filread_outcome())
    id1 = result1.metadata["sourceProjection"]["sources"][0]["sourceId"]
    id2 = result2.metadata["sourceProjection"]["sources"][0]["sourceId"]
    # Both hardcoded to src_1 -- this is the known collision
    assert id1 == "src_1"
    assert id2 == "src_1"
    assert id1 == id2, "collision: two FileRead calls get the same src_1"


def test_citation_capture_produces_unique_ids_for_multiple_fileread_calls(
    monkeypatch,
) -> None:
    """GREEN: with MAGI_SOURCE_CITATION_ENABLED ON and distinct file paths,
    two FileRead calls in the same session get unique source ids via the
    session registry (not via _synthesized_source_projection).
    """
    monkeypatch.setenv("MAGI_SOURCE_CITATION_ENABLED", "1")
    monkeypatch.delenv("MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED", raising=False)
    from magi_agent.evidence.local_tool_collector import LocalToolEvidenceCollector
    from magi_agent.tools.result import ToolResult

    collector = LocalToolEvidenceCollector()
    result = ToolResult(status="ok", output={"content": "hello"}, metadata={})

    records1 = collector.record_tool_result(
        session_id="sess-1",
        turn_id="turn-1",
        tool_call_id="c1",
        tool_name="FileRead",
        result=result,
        arguments={"path": "/workspace/README.md"},
    )
    records2 = collector.record_tool_result(
        session_id="sess-1",
        turn_id="turn-1",
        tool_call_id="c2",
        tool_name="FileRead",
        result=result,
        arguments={"path": "/workspace/src/main.py"},
    )

    # Each call should produce at least one citation evidence record
    citation1 = [
        r for r in records1
        if getattr(r, "producing_rule_id", None) == "source_citation.capture"
    ]
    citation2 = [
        r for r in records2
        if getattr(r, "producing_rule_id", None) == "source_citation.capture"
    ]
    assert citation1, "first FileRead must produce a citation record"
    assert citation2, "second FileRead must produce a citation record"

    # Extract source ids from the evidence records
    def _get_source_id(rec: object) -> str | None:
        fields = getattr(rec, "fields", None)
        get_fn = getattr(fields, "get", None)
        if callable(get_fn):
            sid = get_fn("sourceId")
            if isinstance(sid, str):
                return sid
            sids = get_fn("sourceIds")
            if isinstance(sids, (list, tuple)) and sids:
                return str(sids[0])
        return None

    id1 = _get_source_id(citation1[0])
    id2 = _get_source_id(citation2[0])
    assert id1 is not None, "first citation record must have a sourceId"
    assert id2 is not None, "second citation record must have a sourceId"
    assert id1 != id2, f"expected unique ids, got {id1!r} and {id2!r}"
