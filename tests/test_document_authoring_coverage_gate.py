"""Task C — default-OFF document-authoring coverage-blocking gate.

These tests exercise the OPTIONAL BLOCKING layer: the ``document-authoring-coverage``
preset + the field-aware block in ``execute_pre_final_verifier_bus``.

CRITICAL contract (Task B): through the production
``evidence_from_tool_result`` path the canonical ``EvidenceRecord.status``
follows the TOOL status (``"ok"``), so the coverage verdict lives in
``fields["status"]``. The gate MUST key off ``fields["status"]``.
"""

from __future__ import annotations

from magi_agent.config.env import (
    is_document_authoring_coverage_enabled,
    resolve_document_authoring_coverage_mode,
)
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


# -- 3-state mode (14-PR3, C11): off | advisory | block --------------------


def test_coverage_mode_defaults_off() -> None:
    assert resolve_document_authoring_coverage_mode({}) == "off"
    assert resolve_document_authoring_coverage_mode({"MAGI_DOCUMENT_AUTHORING_COVERAGE": ""}) == "off"
    assert (
        resolve_document_authoring_coverage_mode({"MAGI_DOCUMENT_AUTHORING_COVERAGE": "0"}) == "off"
    )


def test_coverage_mode_legacy_truthy_maps_to_block() -> None:
    # Backward compat: the historical boolean truthy values mean hard-block.
    for truthy in ("1", "true", "yes", "on", "TRUE"):
        assert (
            resolve_document_authoring_coverage_mode(
                {"MAGI_DOCUMENT_AUTHORING_COVERAGE": truthy}
            )
            == "block"
        )


def test_coverage_mode_explicit_advisory_and_block() -> None:
    assert (
        resolve_document_authoring_coverage_mode(
            {"MAGI_DOCUMENT_AUTHORING_COVERAGE": "advisory"}
        )
        == "advisory"
    )
    assert (
        resolve_document_authoring_coverage_mode(
            {"MAGI_DOCUMENT_AUTHORING_COVERAGE": " ADVISORY "}
        )
        == "advisory"
    )
    assert (
        resolve_document_authoring_coverage_mode(
            {"MAGI_DOCUMENT_AUTHORING_COVERAGE": "block"}
        )
        == "block"
    )
    # "off" is an explicit, valid mode token too.
    assert (
        resolve_document_authoring_coverage_mode(
            {"MAGI_DOCUMENT_AUTHORING_COVERAGE": "off"}
        )
        == "off"
    )


def test_coverage_mode_unknown_falls_back_to_off() -> None:
    # A typo must fail safe (never silently hard-block).
    assert (
        resolve_document_authoring_coverage_mode(
            {"MAGI_DOCUMENT_AUTHORING_COVERAGE": "bogus"}
        )
        == "off"
    )


def test_engine_document_coverage_blocks_only_in_block_mode() -> None:
    # 14-PR3: the engine treats advisory as record-but-do-not-block.
    from magi_agent.cli.engine import _document_coverage_blocks

    # block mode: failed coverage contributes to the block decision.
    assert _document_coverage_blocks("block", 2) is True
    assert _document_coverage_blocks("block", 0) is False
    # advisory mode: failed coverage is recorded for telemetry but never blocks.
    assert _document_coverage_blocks("advisory", 2) is False
    assert _document_coverage_blocks("advisory", 0) is False
    # off mode: never blocks regardless of count.
    assert _document_coverage_blocks("off", 5) is False


def test_is_enabled_true_for_advisory_and_block() -> None:
    # The verifier bus must still COMPUTE the coverage count in advisory mode
    # (for telemetry); only the engine decides whether it BLOCKS. So the
    # boolean "enabled" helper is true for both advisory and block.
    assert (
        is_document_authoring_coverage_enabled(
            {"MAGI_DOCUMENT_AUTHORING_COVERAGE": "advisory"}
        )
        is True
    )
    assert (
        is_document_authoring_coverage_enabled(
            {"MAGI_DOCUMENT_AUTHORING_COVERAGE": "block"}
        )
        is True
    )


# -- Gate ON edge cases (items 1, 2, 7) ------------------------------------


def test_gate_on_model_dump_raises_runtime_error_does_not_crash() -> None:
    """Item 1: model_dump raising RuntimeError is swallowed; gate stays open."""

    class _BadDumpRecord:
        """Pretends to be a DocumentCoverage record but model_dump raises."""

        type = "DocumentCoverage"
        fields = {"status": "failed"}

        def model_dump(self, **kwargs: object) -> object:
            raise RuntimeError("intentional model_dump failure for test")

    bus = execute_pre_final_verifier_bus(
        required_evidence=(),
        required_validators=(),
        observed_public_refs=(),
        evidence_records=(_BadDumpRecord(),),
        document_coverage_gate_enabled=True,
    )

    # The broken record is safely ignored (no-readable-record ⇒ pass).
    assert bus["decision"] == "pass"
    assert bus["failedDocumentCoverage"] == 0


def test_gate_on_absent_status_key_does_not_block() -> None:
    """Item 2: a DocumentCoverage record with no 'status' in fields ⇒ pass."""
    record = {
        "type": "DocumentCoverage",
        "status": "ok",
        "fields": {
            "totalUnits": 4,
            "coveredUnits": 1,
            # NOTE: no "status" key in fields
        },
    }
    bus = execute_pre_final_verifier_bus(
        required_evidence=(),
        required_validators=(),
        observed_public_refs=(),
        evidence_records=(record,),
        document_coverage_gate_enabled=True,
    )

    assert bus["decision"] == "pass"
    assert bus["failedDocumentCoverage"] == 0


def test_gate_on_two_records_one_pass_one_failed_counts_one() -> None:
    """Item 7: two records (one pass, one failed) → failedDocumentCoverage==1, blocked."""
    bus = execute_pre_final_verifier_bus(
        required_evidence=(),
        required_validators=(),
        observed_public_refs=(),
        evidence_records=(
            _coverage_record("pass"),
            _coverage_record("failed"),
        ),
        document_coverage_gate_enabled=True,
    )

    assert bus["decision"] == "block"
    assert bus["failedDocumentCoverage"] == 1


def test_gate_on_mapping_record_with_no_fields_key_does_not_block() -> None:
    """Item 7: mapping-shaped record with type='DocumentCoverage' and NO 'fields' key ⇒ pass."""
    record = {
        "type": "DocumentCoverage",
        "status": "ok",
        # NOTE: no "fields" key at all
    }
    bus = execute_pre_final_verifier_bus(
        required_evidence=(),
        required_validators=(),
        observed_public_refs=(),
        evidence_records=(record,),
        document_coverage_gate_enabled=True,
    )

    assert bus["decision"] == "pass"
    assert bus["failedDocumentCoverage"] == 0


# -- preset registry -------------------------------------------------------


def test_preset_registered_default_off_with_env_gate() -> None:
    preset = builtin_preset_by_key("document-authoring-coverage")

    assert preset.default_on is False
    assert preset.blocking is True
    assert preset.fail_open is True
    assert preset.env_gates == ("MAGI_DOCUMENT_AUTHORING_COVERAGE",)
    assert preset.verifier_gates == ("document-authoring-coverage",)
    assert str(preset.category) == "output"
