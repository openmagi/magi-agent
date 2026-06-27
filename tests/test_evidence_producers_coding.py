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
        output_preview: object = "ok",
        edit_match_receipt=None,
        code_diagnostics_receipt=None,
        coding_mutation_receipt=None,
        extra_evidence_declarations: tuple = (),
    ) -> None:
        self.receipt = _FakeReceipt(tool_name)
        self.status = status
        self.reason = "ok"
        self.output_preview = output_preview
        self.edit_match_receipt = edit_match_receipt
        self.code_diagnostics_receipt = code_diagnostics_receipt
        self.coding_mutation_receipt = coding_mutation_receipt
        self.extra_evidence_declarations = extra_evidence_declarations


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


# --- Part B2: GitDiff live producer (derived from output_preview) -----------


def _git_output(*paths: str) -> dict:
    return {
        "isGitRepo": True,
        "status": [f" M {p}" for p in paths],
        "numstat": [{"path": p, "added": 1, "deleted": 0} for p in paths],
    }


def test_git_diff_outcome_emits_evidence_declaration():
    from magi_agent.tools.core_toolhost import _tool_result_from_outcome

    outcome = _FakeOutcome(tool_name="GitDiff", output_preview=_git_output("a.py", "b.py"))
    tr = _tool_result_from_outcome(outcome)
    decls = _evidence(tr.metadata)
    git = [d for d in decls if d["type"] == "GitDiff"]
    assert len(git) == 1
    assert tuple(git[0]["fields"]["changedFiles"]) == ("a.py", "b.py")
    assert git[0]["fields"]["fileCount"] == 2


def test_git_diff_clean_repo_emits_empty_changed_files():
    from magi_agent.tools.core_toolhost import _tool_result_from_outcome

    outcome = _FakeOutcome(tool_name="GitDiff", output_preview=_git_output())
    tr = _tool_result_from_outcome(outcome)
    git = [d for d in _evidence(tr.metadata) if d["type"] == "GitDiff"]
    assert len(git) == 1
    assert tuple(git[0]["fields"]["changedFiles"]) == ()
    assert git[0]["fields"]["fileCount"] == 0


def test_git_diff_non_repo_no_evidence():
    from magi_agent.tools.core_toolhost import _tool_result_from_outcome

    outcome = _FakeOutcome(
        tool_name="GitDiff",
        output_preview={"isGitRepo": False, "status": [], "numstat": []},
    )
    tr = _tool_result_from_outcome(outcome)
    assert "evidence" not in tr.metadata


def test_git_diff_truncated_output_no_evidence():
    # Degraded output ({truncated, digest}) must not crash or fabricate.
    from magi_agent.tools.core_toolhost import _tool_result_from_outcome

    outcome = _FakeOutcome(
        tool_name="GitDiff",
        output_preview={"truncated": True, "digest": "sha256:x"},
    )
    tr = _tool_result_from_outcome(outcome)
    assert "evidence" not in tr.metadata


def test_git_diff_reaches_durable_ledger(tmp_path, monkeypatch):
    from magi_agent.evidence.local_tool_collector import LocalToolEvidenceCollector
    from magi_agent.tools.core_toolhost import _tool_result_from_outcome

    monkeypatch.setenv("MAGI_EVIDENCE_LEDGER_DIR", str(tmp_path))
    outcome = _FakeOutcome(tool_name="GitDiff", output_preview=_git_output("x.py"))
    tr = _tool_result_from_outcome(outcome)
    collector = LocalToolEvidenceCollector()
    collector.record_tool_result(
        session_id="sess-g",
        turn_id="turn-g",
        tool_call_id="call-g",
        tool_name="GitDiff",
        result=tr,
    )
    ledger = tmp_path / "sess-g.jsonl"
    assert ledger.exists()
    types = set()
    for ln in ledger.read_text().splitlines():
        if not ln.strip():
            continue
        rec = json.loads(ln).get("record")
        if isinstance(rec, dict):
            types.add(rec.get("type"))
    assert "GitDiff" in types


# --- Part B3: TestRun live producer (command threaded from gate5b dispatch) -


def test_extra_evidence_declarations_appended_to_metadata():
    from magi_agent.tools.core_toolhost import _tool_result_from_outcome

    decl = {
        "type": "TestRun",
        "fields": {"command": "pytest -q", "exitCode": 0},
        "source": {"kind": "tool_trace", "toolName": "TestRun"},
    }
    outcome = _FakeOutcome(tool_name="TestRun", extra_evidence_declarations=(decl,))
    tr = _tool_result_from_outcome(outcome)
    decls = _evidence(tr.metadata)
    test_run = [d for d in decls if d["type"] == "TestRun"]
    assert len(test_run) == 1
    assert test_run[0]["fields"]["command"] == "pytest -q"
    assert test_run[0]["fields"]["exitCode"] == 0


def test_test_run_evidence_declaration_builds_redacted_command():
    from magi_agent.gates.gate5b_full_toolhost import (
        _test_run_evidence_declarations,
    )

    decls = _test_run_evidence_declarations(
        "TestRun",
        {"command": "pytest -q --token=AKIAIOSFODNN7EXAMPLE"},
        {"exitCode": 0, "stdout": "ok"},
    )
    assert len(decls) == 1
    fields = decls[0]["fields"]
    assert fields["exitCode"] == 0
    assert "AKIAIOSFODNN7EXAMPLE" not in fields["command"]


def test_test_run_evidence_declaration_carries_nonzero_exit():
    from magi_agent.gates.gate5b_full_toolhost import (
        _test_run_evidence_declarations,
    )

    decls = _test_run_evidence_declarations(
        "TestRun", {"command": "pytest"}, {"exitCode": 1}
    )
    assert decls[0]["fields"]["exitCode"] == 1


def test_test_run_evidence_declaration_skips_non_testrun():
    from magi_agent.gates.gate5b_full_toolhost import (
        _test_run_evidence_declarations,
    )

    assert _test_run_evidence_declarations("Bash", {"command": "ls"}, {"exitCode": 0}) == ()


def test_test_run_reaches_durable_ledger(tmp_path, monkeypatch):
    from magi_agent.evidence.local_tool_collector import LocalToolEvidenceCollector
    from magi_agent.tools.core_toolhost import _tool_result_from_outcome

    monkeypatch.setenv("MAGI_EVIDENCE_LEDGER_DIR", str(tmp_path))
    decl = {
        "type": "TestRun",
        "fields": {"command": "pytest -q", "exitCode": 0},
        "source": {"kind": "tool_trace", "toolName": "TestRun"},
    }
    outcome = _FakeOutcome(tool_name="TestRun", extra_evidence_declarations=(decl,))
    tr = _tool_result_from_outcome(outcome)
    collector = LocalToolEvidenceCollector()
    collector.record_tool_result(
        session_id="sess-t",
        turn_id="turn-t",
        tool_call_id="call-t",
        tool_name="TestRun",
        result=tr,
    )
    ledger = tmp_path / "sess-t.jsonl"
    assert ledger.exists()
    types = set()
    for ln in ledger.read_text().splitlines():
        if not ln.strip():
            continue
        rec = json.loads(ln).get("record")
        if isinstance(rec, dict):
            types.add(rec.get("type"))
    assert "TestRun" in types


# --- Part B4: Calculation live producer -------------------------------------


def test_calculation_evidence_declaration_built():
    from magi_agent.gates.gate5b_full_toolhost import (
        _calculation_evidence_declarations,
    )

    decls = _calculation_evidence_declarations(
        "Calculation", {"expression": "2+2"}, {"value": 4}
    )
    assert len(decls) == 1
    fields = decls[0]["fields"]
    assert decls[0]["type"] == "Calculation"
    assert fields["value"] == 4
    assert fields["expression"] == "2+2"
    assert fields["resultDigest"].startswith("sha256:")
    assert "4" in [str(n) for n in fields["observedNumbers"]]


def test_calculation_skips_non_calculation():
    from magi_agent.gates.gate5b_full_toolhost import (
        _calculation_evidence_declarations,
    )

    assert _calculation_evidence_declarations("Bash", {}, {"value": 1}) == ()


def test_calculation_reaches_durable_ledger(tmp_path, monkeypatch):
    from magi_agent.evidence.local_tool_collector import LocalToolEvidenceCollector
    from magi_agent.gates.gate5b_full_toolhost import (
        _calculation_evidence_declarations,
    )
    from magi_agent.tools.core_toolhost import _tool_result_from_outcome

    monkeypatch.setenv("MAGI_EVIDENCE_LEDGER_DIR", str(tmp_path))
    decls = _calculation_evidence_declarations(
        "Calculation", {"expression": "6*7"}, {"value": 42}
    )
    outcome = _FakeOutcome(tool_name="Calculation", extra_evidence_declarations=decls)
    tr = _tool_result_from_outcome(outcome)
    collector = LocalToolEvidenceCollector()
    collector.record_tool_result(
        session_id="sess-c",
        turn_id="turn-c",
        tool_call_id="call-c",
        tool_name="Calculation",
        result=tr,
    )
    ledger = tmp_path / "sess-c.jsonl"
    assert ledger.exists()
    types = set()
    for ln in ledger.read_text().splitlines():
        if not ln.strip():
            continue
        rec = json.loads(ln).get("record")
        if isinstance(rec, dict):
            types.add(rec.get("type"))
    assert "Calculation" in types


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
        ("GitDiff", {"changedFiles", "fileCount", "digest"}),
        ("Calculation", {"expression", "value", "resultDigest", "observedNumbers"}),
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
    types = set()
    for ln in ledger.read_text().splitlines():
        if not ln.strip():
            continue
        rec = json.loads(ln).get("record")
        if isinstance(rec, dict):
            types.add(rec.get("type"))
    assert {"EditMatch", "CodeDiagnostics"} <= types
