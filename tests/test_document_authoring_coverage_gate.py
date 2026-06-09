"""Task C — default-OFF document-authoring coverage-blocking gate.

These tests exercise the OPTIONAL BLOCKING layer: the ``document-authoring-coverage``
preset + the field-aware block in ``execute_pre_final_verifier_bus``.

CRITICAL contract (Task B): through the production
``evidence_from_tool_result`` path the canonical ``EvidenceRecord.status``
follows the TOOL status (``"ok"``), so the coverage verdict lives in
``fields["status"]``. The gate MUST key off ``fields["status"]``.
"""

from __future__ import annotations

from magi_agent.config.env import is_document_authoring_coverage_enabled
from magi_agent.evidence.document_coverage import (
    DocumentCoverageBoundary,
    evidence_record_from_record,
)
from magi_agent.evidence.types import EvidenceRecord, EvidenceSource
from magi_agent.harness.presets import builtin_preset_by_key
from magi_agent.harness.verifier_bus import execute_pre_final_verifier_bus


def _coverage_record(status: str) -> EvidenceRecord:
    """A production-shaped DocumentCoverage record.

    Top-level ``status="ok"`` (mirrors a successful ``docx_write`` tool result),
    coverage verdict carried in ``fields["status"]`` per the Task B contract.
    """
    return EvidenceRecord(
        type="DocumentCoverage",
        status="ok",
        observedAt=1,
        source=EvidenceSource(kind="verifier", verifierName="document_coverage"),
        fields={
            "type": "DocumentCoverage",
            "totalUnits": 4,
            "coveredUnits": 4 if status == "pass" else 1,
            "coverageRatio": 1.0 if status == "pass" else 0.25,
            "threshold": 0.95,
            "missingUnitDigests": (),
            "sourceDigest": "sha256:" + "a" * 64,
            "docDigest": "sha256:" + "b" * 64,
            "status": status,
        },
    )


def _non_document_record() -> EvidenceRecord:
    return EvidenceRecord(
        type="TestRun",
        status="ok",
        observedAt=1,
        source=EvidenceSource(kind="tool_trace"),
        fields={"command": "pytest", "exitCode": 0},
    )


# -- Gate ON ---------------------------------------------------------------


def test_gate_on_blocks_on_failed_coverage() -> None:
    bus = execute_pre_final_verifier_bus(
        required_evidence=(),
        required_validators=(),
        observed_public_refs=(),
        evidence_records=(_coverage_record("failed"),),
        document_coverage_gate_enabled=True,
    )

    assert bus["decision"] == "block"
    assert bus["failedDocumentCoverage"] == 1
    assert any(
        result["verifierId"] == "document-authoring-coverage"
        and result["status"] == "failed"
        for result in bus["results"]
    )


def test_gate_on_passes_on_passing_coverage() -> None:
    bus = execute_pre_final_verifier_bus(
        required_evidence=(),
        required_validators=(),
        observed_public_refs=(),
        evidence_records=(_coverage_record("pass"),),
        document_coverage_gate_enabled=True,
    )

    assert bus["decision"] == "pass"
    assert bus["failedDocumentCoverage"] == 0
    assert not any(
        result["verifierId"] == "document-authoring-coverage" for result in bus["results"]
    )


def test_gate_on_does_not_block_non_document_turn() -> None:
    bus = execute_pre_final_verifier_bus(
        required_evidence=(),
        required_validators=(),
        observed_public_refs=(),
        evidence_records=(_non_document_record(),),
        document_coverage_gate_enabled=True,
    )

    assert bus["decision"] == "pass"
    assert bus["failedDocumentCoverage"] == 0


def test_gate_on_uses_fields_status_not_top_level_status() -> None:
    # Build a real coverage record via the production helper: top-level status is
    # forced to "ok" (tool status) while the FAILED verdict lives in fields.
    coverage = DocumentCoverageBoundary().build_record(
        source_markdown="Kept one\nDropped two\nDropped three\nDropped four",
        doc_text="Kept one",
    )
    assert coverage.status == "failed"
    record = evidence_record_from_record(coverage).model_copy(update={"status": "ok"})
    assert record.status == "ok"
    assert record.fields["status"] == "failed"

    bus = execute_pre_final_verifier_bus(
        required_evidence=(),
        required_validators=(),
        observed_public_refs=(),
        evidence_records=(record,),
        document_coverage_gate_enabled=True,
    )
    assert bus["decision"] == "block"


def test_gate_on_accepts_mapping_records() -> None:
    record = {
        "type": "DocumentCoverage",
        "status": "ok",
        "fields": {"status": "failed"},
    }
    bus = execute_pre_final_verifier_bus(
        required_evidence=(),
        required_validators=(),
        observed_public_refs=(),
        evidence_records=(record,),
        document_coverage_gate_enabled=True,
    )
    assert bus["decision"] == "block"


# -- Gate OFF (default) ----------------------------------------------------


def test_gate_off_is_audit_only_for_failed_coverage() -> None:
    bus = execute_pre_final_verifier_bus(
        required_evidence=(),
        required_validators=(),
        observed_public_refs=(),
        evidence_records=(_coverage_record("failed"),),
    )

    assert bus["decision"] == "pass"
    assert bus["failedDocumentCoverage"] == 0
    assert not any(
        result["verifierId"] == "document-authoring-coverage" for result in bus["results"]
    )


def test_gate_off_does_not_disturb_ref_based_block() -> None:
    # Ref-based block path must be unchanged when the coverage gate is off.
    bus = execute_pre_final_verifier_bus(
        required_evidence=("evidence:doc-write",),
        required_validators=(),
        observed_public_refs=(),
        evidence_records=(_coverage_record("failed"),),
    )
    assert bus["decision"] == "block"
    assert bus["missingEvidence"] == ["evidence:doc-write"]
    assert bus["failedDocumentCoverage"] == 0


# -- env flag --------------------------------------------------------------


def test_env_flag_defaults_off_and_is_strict_truthy() -> None:
    assert is_document_authoring_coverage_enabled({}) is False
    assert is_document_authoring_coverage_enabled({"MAGI_DOCUMENT_AUTHORING_COVERAGE": ""}) is False
    assert (
        is_document_authoring_coverage_enabled({"MAGI_DOCUMENT_AUTHORING_COVERAGE": "0"}) is False
    )
    for truthy in ("1", "true", "yes", "on", "TRUE"):
        assert (
            is_document_authoring_coverage_enabled(
                {"MAGI_DOCUMENT_AUTHORING_COVERAGE": truthy}
            )
            is True
        )


# -- preset registry -------------------------------------------------------


def test_preset_registered_default_off_with_env_gate() -> None:
    preset = builtin_preset_by_key("document-authoring-coverage")

    assert preset.default_on is False
    assert preset.blocking is True
    assert preset.fail_open is True
    assert preset.env_gates == ("MAGI_DOCUMENT_AUTHORING_COVERAGE",)
    assert preset.verifier_gates == ("document-authoring-coverage",)
    assert str(preset.category) == "output"
