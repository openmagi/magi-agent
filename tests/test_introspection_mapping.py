"""Tests for the shared tool-call normalization (introspection/mapping.py).

The mapping layer guarantees a consistent OUTPUT (same ``ToolCallView`` shape +
canonical status vocabulary) across the two distinct tool_call producers:
  - EvidenceLedger evidence_records (mid-turn PULL seam, projection.py)
  - gate5b ``Gate5BFullToolReceipt`` (egress PUSH seam, transport/chat.py)
"""

from __future__ import annotations

import typing

from magi_agent.evidence.types import EvidenceRecord, EvidenceStatus
from magi_agent.gates.gate5b_full_toolhost import (
    Gate5BFullToolOutcomeStatus,
    Gate5BFullToolReceipt,
)
from magi_agent.introspection.mapping import (
    normalize_tool_status,
    tool_call_from_evidence_record,
    tool_call_from_gate5b_receipt,
)
from magi_agent.introspection.projection import ToolCallView

_CANONICAL = {"ok", "error", "blocked", "duplicate", "unknown"}


def _record(*, name: str, status: str) -> EvidenceRecord:
    return EvidenceRecord.model_validate(
        {
            "type": "TestRun",
            "status": status,
            "observedAt": 1_779_000_001,
            "source": {"kind": "tool_trace", "toolName": name},
            "fields": {},
        }
    )


def _receipt(*, name: str, status: str) -> Gate5BFullToolReceipt:
    return Gate5BFullToolReceipt(
        requestDigest="d" * 64,
        toolCallDigest="e" * 64,
        toolName=name,
        status=status,
        boundedOutputDigest="a" * 64,
        outputByteCount=3,
    )


# ---------------------------------------------------------------------------
# Status normalization is TOTAL over both producers' vocabularies.
# ---------------------------------------------------------------------------


def test_every_gate5b_status_maps_to_canonical_value() -> None:
    members = typing.get_args(Gate5BFullToolOutcomeStatus)
    assert members  # guard: the Literal actually exposes members
    for raw in members:
        mapped = normalize_tool_status(raw)
        assert mapped in _CANONICAL, (raw, mapped)


def test_every_evidence_status_maps_to_canonical_value() -> None:
    members = typing.get_args(EvidenceStatus)
    assert members
    for raw in members:
        mapped = normalize_tool_status(raw)
        assert mapped in _CANONICAL, (raw, mapped)


def test_full_status_mapping_table() -> None:
    assert normalize_tool_status("ok") == "ok"
    assert normalize_tool_status("failed") == "error"  # EvidenceStatus
    assert normalize_tool_status("unknown") == "unknown"
    assert normalize_tool_status("error") == "error"  # gate5b
    assert normalize_tool_status("blocked") == "blocked"
    assert normalize_tool_status("duplicate") == "duplicate"


def test_unmapped_status_collapses_to_unknown() -> None:
    assert normalize_tool_status("totally-made-up") == "unknown"
    assert normalize_tool_status("") == "unknown"


# ---------------------------------------------------------------------------
# tool_call_from_evidence_record — matches prior projection behavior.
# ---------------------------------------------------------------------------


def test_evidence_helper_builds_expected_view() -> None:
    view = tool_call_from_evidence_record(
        _record(name="Grep", status="ok").model_dump(by_alias=True),
        "turn-1",
    )
    assert isinstance(view, ToolCallView)
    assert view.name == "Grep"
    assert view.status == "ok"
    assert view.turn_id == "turn-1"


def test_evidence_helper_normalizes_failed_to_error() -> None:
    view = tool_call_from_evidence_record(
        _record(name="Bash", status="failed").model_dump(by_alias=True),
        "turn-2",
    )
    assert view is not None
    assert view.status == "error"


def test_evidence_helper_returns_none_without_tool_name() -> None:
    assert tool_call_from_evidence_record({"status": "ok"}, "turn-1") is None
    assert (
        tool_call_from_evidence_record({"source": {"kind": "tool_trace"}}, "turn-1")
        is None
    )
    assert (
        tool_call_from_evidence_record(
            {"source": {"toolName": ""}, "status": "ok"}, "turn-1"
        )
        is None
    )


def test_evidence_helper_missing_status_is_unknown() -> None:
    view = tool_call_from_evidence_record(
        {"source": {"toolName": "Grep"}}, "turn-1"
    )
    assert view is not None
    assert view.status == "unknown"


# ---------------------------------------------------------------------------
# tool_call_from_gate5b_receipt — matches prior egress behavior.
# ---------------------------------------------------------------------------


def test_gate5b_helper_builds_expected_view() -> None:
    view = tool_call_from_gate5b_receipt(
        _receipt(name="Grep", status="ok"), "live-egress-turn"
    )
    assert isinstance(view, ToolCallView)
    assert view.name == "Grep"
    assert view.status == "ok"
    assert view.turn_id == "live-egress-turn"


def test_gate5b_helper_normalizes_each_outcome() -> None:
    assert (
        tool_call_from_gate5b_receipt(_receipt(name="t", status="error"), "x").status
        == "error"
    )
    assert (
        tool_call_from_gate5b_receipt(_receipt(name="t", status="blocked"), "x").status
        == "blocked"
    )
    assert (
        tool_call_from_gate5b_receipt(
            _receipt(name="t", status="duplicate"), "x"
        ).status
        == "duplicate"
    )


# ---------------------------------------------------------------------------
# The two seams report the SAME normalized status for equivalent outcomes.
# ---------------------------------------------------------------------------


def test_success_is_ok_from_either_source() -> None:
    from_evidence = tool_call_from_evidence_record(
        _record(name="Grep", status="ok").model_dump(by_alias=True), "t"
    )
    from_receipt = tool_call_from_gate5b_receipt(
        _receipt(name="Grep", status="ok"), "t"
    )
    assert from_evidence is not None
    assert from_evidence.status == from_receipt.status == "ok"


def test_failure_is_error_from_either_source() -> None:
    # EvidenceStatus "failed" and gate5b "error" both denote a failed tool call;
    # they must normalize to the SAME canonical token.
    from_evidence = tool_call_from_evidence_record(
        _record(name="Bash", status="failed").model_dump(by_alias=True), "t"
    )
    from_receipt = tool_call_from_gate5b_receipt(
        _receipt(name="Bash", status="error"), "t"
    )
    assert from_evidence is not None
    assert from_evidence.status == from_receipt.status == "error"
