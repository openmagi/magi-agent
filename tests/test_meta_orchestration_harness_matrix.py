from __future__ import annotations

import json
from pathlib import Path
from typing import Any


PYTHON_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PYTHON_ROOT.parents[2]
MATRIX_PATH = (
    PYTHON_ROOT
    / "tests"
    / "fixtures"
    / "meta_orchestration_harness"
    / "matrix.json"
)
DOC_PATH = REPO_ROOT / "docs/notes/2026-05-26-meta-orchestration-harness-gap-audit.md"

REQUIRED_ROW_IDS = (
    "parent_plan_contract",
    "child_role_registry",
    "child_tool_grants",
    "child_context_budget",
    "child_completion_contract",
    "runtime_issued_child_evidence_envelope",
    "raw_child_transcript_rejection",
    "parent_accept_retry_reject_verdict",
    "bounded_retry",
    "final_assembly_from_accepted_evidence_only",
    "verifier_chain_selection",
    "before_commit_projection_gate",
    "public_projection_redaction",
    "default_off_authority_flags",
    "adk_primitive_ownership",
)
ALLOWED_OWNING_LAYERS = {
    "ADK substrate",
    "OpenMagi first-party harness",
    "OpenMagi adapter",
    "OpenMagi verifier/projection",
    "Tests/docs only",
}
ALLOWED_STATUSES = {
    "covered_by_adk",
    "openmagi_owned_required",
    "adapter_owned_example",
    "audit_only",
}
DOMAIN_ROLE_TERMS = ("research", "coding", "backoffice")
LIVE_ACTIVATION_TERMS = (
    "live model",
    "live child",
    "live tool",
    "web activation",
    "browser activation",
    "channel activation",
    "memory-write activation",
    "workspace-write activation",
)
ADK_OWNED_PRIMITIVES = {
    "Agent",
    "Runner",
    "FunctionTool",
    "LongRunningFunctionTool",
    "callback",
    "SessionService",
    "MemoryService",
    "ArtifactService",
    "Evaluation",
}
OPENMAGI_OWNED_SURFACES = {
    "evidence envelopes",
    "ledger/receipts",
    "verifier verdicts",
    "projection",
    "approval/control",
    "privacy/redaction",
    "rollout gates",
}
AUTHORITY_FLAGS = {
    "orchestrationEnabled",
    "childDispatchEnabled",
    "toolGrantEnabled",
    "memoryWriteEnabled",
    "workspaceWriteEnabled",
    "publicProjectionEnabled",
}


def _load_matrix() -> dict[str, Any]:
    return json.loads(MATRIX_PATH.read_text(encoding="utf-8"))


def _rows() -> list[dict[str, Any]]:
    rows = _load_matrix()["rows"]
    assert isinstance(rows, list)
    return rows


def _all_strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        strings: list[str] = []
        for nested in value.values():
            strings.extend(_all_strings(nested))
        return strings
    if isinstance(value, list):
        strings = []
        for nested in value:
            strings.extend(_all_strings(nested))
        return strings
    return []


def test_required_meta_orchestration_rows_exist_exactly_once() -> None:
    row_ids = [row["id"] for row in _rows()]

    assert row_ids == list(REQUIRED_ROW_IDS)
    assert len(row_ids) == len(set(row_ids))


def test_every_row_has_owning_layer_status_and_activation_gate() -> None:
    for row in _rows():
        assert row["owningLayer"] in ALLOWED_OWNING_LAYERS
        assert row["status"] in ALLOWED_STATUSES
        assert isinstance(row["activationGate"], str)
        assert row["activationGate"].strip()


def test_domain_specific_role_examples_are_adapter_owned_not_core_owned() -> None:
    examples = [
        (row["id"], example)
        for row in _rows()
        for example in row.get("roleExamples", [])
        if any(term in example["id"] for term in DOMAIN_ROLE_TERMS)
    ]

    assert examples
    for row_id, example in examples:
        assert example["owner"] == "adapter", (row_id, example)
        assert example["coreRequirement"] is False, (row_id, example)


def test_no_matrix_row_requires_live_activation() -> None:
    for row in _rows():
        assert row["requiresLiveActivation"] is False, row["id"]
        assert row["trafficAttached"] is False, row["id"]
        assert row["defaultOff"] is True, row["id"]
        row_text = " ".join(_all_strings(row)).lower()
        assert not any(term in row_text for term in LIVE_ACTIVATION_TERMS), row["id"]


def test_adk_and_openmagi_primitive_ownership_is_explicit() -> None:
    adk_owned: set[str] = set()
    openmagi_owned: set[str] = set()
    for row in _rows():
        ownership = row["primitiveOwnership"]
        row_adk_owned = set(ownership["adkOwns"])
        row_openmagi_owned = set(ownership["openMagiOwns"])

        assert row_adk_owned <= ADK_OWNED_PRIMITIVES, row["id"]
        assert row_openmagi_owned <= OPENMAGI_OWNED_SURFACES, row["id"]
        assert row_adk_owned.isdisjoint(row_openmagi_owned), row["id"]
        if row["status"] == "openmagi_owned_required":
            assert row_openmagi_owned, row["id"]

        adk_owned.update(ownership["adkOwns"])
        openmagi_owned.update(ownership["openMagiOwns"])

    assert ADK_OWNED_PRIMITIVES <= adk_owned
    assert OPENMAGI_OWNED_SURFACES <= openmagi_owned


def test_default_off_authority_flags_are_explicitly_false() -> None:
    row_by_id = {row["id"]: row for row in _rows()}
    flags = row_by_id["default_off_authority_flags"]["authorityFlags"]

    assert set(flags) == AUTHORITY_FLAGS
    assert set(flags.values()) == {False}


def test_gap_audit_note_covers_the_fixture_rows_and_boundary() -> None:
    note = DOC_PATH.read_text(encoding="utf-8")

    assert "first-party harness metadata" in note
    assert "not a generic core meta-agent rewrite" in note
    assert "No live model, child, tool, web, browser, channel, memory-write, or workspace-write activation" in note
    for row_id in REQUIRED_ROW_IDS:
        assert row_id in note
