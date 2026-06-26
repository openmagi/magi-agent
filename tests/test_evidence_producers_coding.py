"""Producer coverage for coding evidence types (EditMatch / CodeDiagnostics /
CommitCheckpoint).

These types had receipts built by the runtime but never lifted into typed
``EvidenceRecord``s reaching the durable ledger. This module pins:

1. List-form ``metadata["evidence"]`` declaration lift (so a single tool call
   can emit multiple typed records — an edit produces BOTH an EditMatch and a
   CodeDiagnostics receipt).
2. ``core_toolhost._tool_result_from_outcome`` surfaces the edit-match /
   code-diagnostics receipts as ``metadata["evidence"]`` declarations.
3. The native ``commit_checkpoint`` tool declares a CommitCheckpoint record.
4. Honesty: each producer's emitted fields are a superset of its SHACL hint.
"""

from __future__ import annotations

import json

import pytest

from magi_agent.evidence.extraction import (
    evidence_from_tool_result,
    evidence_records_from_tool_result,
)
from magi_agent.tools.result import ToolResult


# --- Part A: list-form declaration lift -------------------------------------


def _decl(type_name: str, fields: dict) -> dict:
    return {
        "type": type_name,
        "fields": fields,
        "source": {"kind": "tool_trace", "toolName": "FileEdit"},
    }


def test_list_form_lifts_multiple_records():
    result = ToolResult(
        status="ok",
        output="ok",
        metadata={
            "toolName": "FileEdit",
            "evidence": [
                _decl("EditMatch", {"tier": "exact", "confidence": 1.0}),
                _decl("CodeDiagnostics", {"checker": "pyright", "errorCount": 0}),
            ],
        },
    )
    records = evidence_records_from_tool_result(result, tool_name="FileEdit")
    assert [r.type for r in records] == ["EditMatch", "CodeDiagnostics"]
    assert records[0].fields["tier"] == "exact"
    assert records[1].fields["checker"] == "pyright"


def test_single_mapping_still_lifts_one_record():
    result = ToolResult(
        status="ok",
        output="ok",
        metadata={"toolName": "FileEdit", "evidence": _decl("EditMatch", {"tier": "exact"})},
    )
    records = evidence_records_from_tool_result(result, tool_name="FileEdit")
    assert len(records) == 1
    assert records[0].type == "EditMatch"


def test_evidence_from_tool_result_backcompat_returns_first():
    # The single-record helper still returns the first declaration (back-compat).
    result = ToolResult(
        status="ok",
        output="ok",
        metadata={
            "toolName": "FileEdit",
            "evidence": [
                _decl("EditMatch", {"tier": "exact"}),
                _decl("CodeDiagnostics", {"checker": "pyright"}),
            ],
        },
    )
    one = evidence_from_tool_result(result, tool_name="FileEdit")
    assert one is not None
    assert one.type == "EditMatch"


def test_external_ack_declaration_skipped_in_list():
    result = ToolResult(
        status="ok",
        output="ok",
        metadata={
            "toolName": "FileEdit",
            "evidence": [
                {
                    "type": "TelegramDeliveryAck",
                    "fields": {},
                    "source": {"kind": "external_ack"},
                },
                _decl("EditMatch", {"tier": "exact"}),
            ],
        },
    )
    records = evidence_records_from_tool_result(result, tool_name="FileEdit")
    assert [r.type for r in records] == ["EditMatch"]


def test_no_evidence_key_lifts_nothing():
    result = ToolResult(status="ok", output="ok", metadata={"toolName": "FileEdit"})
    assert evidence_records_from_tool_result(result, tool_name="FileEdit") == []


# --- Part B: core_toolhost outcome -> evidence declarations ------------------


class _FakeReceipt:
    def __init__(self, tool_name: str) -> None:
        self.tool_name = tool_name

    def model_dump(self, **_kwargs):
        return {"toolName": self.tool_name}


class _FakeProjection:
    def __init__(self, projection: dict) -> None:
        self._projection = projection

    def public_projection(self) -> dict:
        return dict(self._projection)


class _FakeOutcome:
    def __init__(
        self,
        *,
        tool_name: str = "FileEdit",
        status: str = "ok",
        edit_match_receipt=None,
        code_diagnostics_receipt=None,
        coding_mutation_receipt=None,
    ) -> None:
        self.receipt = _FakeReceipt(tool_name)
        self.status = status
        self.reason = "ok"
        self.output_preview = "ok"
        self.edit_match_receipt = edit_match_receipt
        self.code_diagnostics_receipt = code_diagnostics_receipt
        self.coding_mutation_receipt = coding_mutation_receipt


def _evidence(metadata) -> list:
    ev = metadata.get("evidence")
    if ev is None:
        return []
    return ev if isinstance(ev, list) else [ev]


def test_edit_match_receipt_becomes_evidence_declaration():
    from magi_agent.tools.core_toolhost import _tool_result_from_outcome

    proj = {"tier": "exact", "tierIndex": 0, "confidence": 1.0}
    outcome = _FakeOutcome(edit_match_receipt=_FakeProjection(proj))
    tr = _tool_result_from_outcome(outcome)
    decls = _evidence(tr.metadata)
    assert any(d["type"] == "EditMatch" and d["fields"] == proj for d in decls)


def test_code_diagnostics_receipt_becomes_evidence_declaration():
    from magi_agent.tools.core_toolhost import _tool_result_from_outcome

    proj = {"checker": "pyright", "errorCount": 0, "fileDigest": "sha256:x"}
    outcome = _FakeOutcome(code_diagnostics_receipt=_FakeProjection(proj))
    tr = _tool_result_from_outcome(outcome)
    decls = _evidence(tr.metadata)
    assert any(d["type"] == "CodeDiagnostics" and d["fields"] == proj for d in decls)


def test_both_receipts_emit_two_declarations():
    from magi_agent.tools.core_toolhost import _tool_result_from_outcome

    outcome = _FakeOutcome(
        edit_match_receipt=_FakeProjection({"tier": "exact"}),
        code_diagnostics_receipt=_FakeProjection({"checker": "pyright"}),
    )
    tr = _tool_result_from_outcome(outcome)
    types = {d["type"] for d in _evidence(tr.metadata)}
    assert {"EditMatch", "CodeDiagnostics"} <= types


def test_no_coding_receipts_no_evidence_key():
    from magi_agent.tools.core_toolhost import _tool_result_from_outcome

    outcome = _FakeOutcome()
    tr = _tool_result_from_outcome(outcome)
    assert "evidence" not in tr.metadata


# --- Part C: CommitCheckpoint native plugin ---------------------------------


def test_commit_checkpoint_declares_evidence(tmp_path):
    from magi_agent.plugins.native.coding import commit_checkpoint
    from magi_agent.tools.context import ToolContext

    ctx = ToolContext(bot_id="test-bot", workspace_root=str(tmp_path))
    result = commit_checkpoint({"label": "ckpt-1"}, ctx)
    decl = result.metadata.get("evidence")
    assert isinstance(decl, dict)
    assert decl["type"] == "CommitCheckpoint"
    assert set(decl["fields"]) >= {"checkpointDigest", "pathRef"}


# --- Part D: honesty — emitted fields superset SHACL hint -------------------


@pytest.mark.parametrize(
    "type_name, emitted",
    [
        ("EditMatch", {"type", "tier", "tierIndex", "confidence", "ambiguous", "fileDigest", "spanDigest"}),
        ("CodeDiagnostics", {"type", "checker", "fileDigest", "errorCount", "capped", "diagnosticsDigest", "entries"}),
        ("CommitCheckpoint", {"checkpointDigest", "pathRef"}),
    ],
)
def test_shacl_hint_is_subset_of_emitted_fields(type_name, emitted):
    from magi_agent.customize.shacl_compiler import _BUILTIN_FIELD_HINTS

    hint = set(_BUILTIN_FIELD_HINTS.get(type_name, []))
    assert hint, f"{type_name} should have a non-empty SHACL hint once produced"
    assert hint <= emitted


# --- Part E: end-to-end through the collector -> durable ledger --------------


def test_coding_receipts_reach_durable_ledger(tmp_path, monkeypatch):
    from magi_agent.evidence.local_tool_collector import LocalToolEvidenceCollector

    monkeypatch.setenv("MAGI_EVIDENCE_LEDGER_DIR", str(tmp_path))
    collector = LocalToolEvidenceCollector()
    result = ToolResult(
        status="ok",
        output="ok",
        metadata={
            "toolName": "FileEdit",
            "evidence": [
                _decl("EditMatch", {"tier": "exact", "confidence": 1.0}),
                _decl("CodeDiagnostics", {"checker": "pyright", "errorCount": 0}),
            ],
        },
    )
    collector.record_tool_result(
        session_id="sess-1",
        turn_id="turn-1",
        tool_call_id="call-1",
        tool_name="FileEdit",
        result=result,
    )
    ledger = tmp_path / "sess-1.jsonl"
    assert ledger.exists()
    types = {
        json.loads(ln)["record"]["type"]
        for ln in ledger.read_text().splitlines()
        if ln.strip()
    }
    assert {"EditMatch", "CodeDiagnostics"} <= types
