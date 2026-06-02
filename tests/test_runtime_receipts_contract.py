from __future__ import annotations

import subprocess
import sys

import pytest
from pydantic import ValidationError

from openmagi_core_agent.evidence.runtime_receipts import (
    CalculationEvidenceReceipt,
    ReceiptAuthorityFlags,
    SourceEvidenceReceipt,
    ToolExecutionReceipt,
)


def test_tool_receipt_requires_digest_fields_and_redaction_status() -> None:
    receipt = ToolExecutionReceipt.model_validate(
        {
            "receiptId": "receipt_1",
            "toolCallId": "tool_1",
            "toolName": "Calculation",
            "toolVersion": "1.0.0",
            "inputDigest": "sha256:" + "a" * 64,
            "outputDigest": "sha256:" + "b" * 64,
            "status": "success",
            "startedAt": "2026-05-21T00:00:00Z",
            "endedAt": "2026-05-21T00:00:01Z",
            "authorityFlags": {
                "readOnly": True,
                "mutationAllowed": False,
                "channelDeliveryAllowed": False,
                "memoryWriteAllowed": False,
            },
            "policyDecisionId": "policy_decision_1",
            "redactionStatus": "redacted",
        }
    )

    public = receipt.public_projection()
    assert public["inputDigest"].startswith("sha256:")
    assert public["outputDigest"].startswith("sha256:")
    assert "raw" not in str(public).lower()


def test_tool_receipt_rejects_raw_secret_values() -> None:
    private_header = "Bearer " + "x" * 12
    with pytest.raises(ValidationError):
        ToolExecutionReceipt.model_validate(
            {
                "receiptId": "receipt_1",
                "toolCallId": "tool_1",
                "toolName": "FileRead",
                "toolVersion": "1.0.0",
                "inputDigest": private_header,
                "outputDigest": "sha256:" + "b" * 64,
                "status": "success",
                "startedAt": "2026-05-21T00:00:00Z",
                "endedAt": "2026-05-21T00:00:01Z",
                "authorityFlags": {
                    "readOnly": True,
                    "mutationAllowed": False,
                    "channelDeliveryAllowed": False,
                    "memoryWriteAllowed": False,
                },
                "policyDecisionId": "policy_decision_1",
                "redactionStatus": "redacted",
            }
        )


def test_source_evidence_requires_snapshot_digest_and_span_ref() -> None:
    source = SourceEvidenceReceipt.model_validate(
        {
            "sourceRef": "source_12",
            "openedAt": "2026-05-21T00:00:00Z",
            "contentDigest": "sha256:" + "c" * 64,
            "snapshotRef": "snapshot_12",
            "spanRef": "source_12_span_4",
            "quoteDigest": "sha256:" + "d" * 64,
        }
    )

    assert source.span_ref == "source_12_span_4"


def test_calculation_evidence_requires_formula_and_result_digest() -> None:
    calc = CalculationEvidenceReceipt.model_validate(
        {
            "calculationRef": "calc_7f31",
            "inputFileDigest": "sha256:" + "e" * 64,
            "rangeRef": "Sales!A1:H3021",
            "formulaDigest": "sha256:" + "f" * 64,
            "resultDigest": "sha256:" + "1" * 64,
        }
    )

    assert calc.range_ref == "Sales!A1:H3021"


def test_authority_flags_cannot_be_forged_by_copy_or_construct() -> None:
    flags = ReceiptAuthorityFlags.model_construct(
        mutation_allowed=True,
        channel_delivery_allowed=True,
        memory_write_allowed=True,
    )
    copied = ReceiptAuthorityFlags().model_copy(
        update={
            "mutation_allowed": True,
            "channel_delivery_allowed": True,
            "memory_write_allowed": True,
        }
    )

    assert flags.model_dump(by_alias=True) == {
        "readOnly": True,
        "mutationAllowed": False,
        "channelDeliveryAllowed": False,
        "memoryWriteAllowed": False,
    }
    assert copied.model_dump(by_alias=True) == flags.model_dump(by_alias=True)


def test_receipts_reject_private_refs_and_unsafe_authority_flags() -> None:
    with pytest.raises(ValidationError):
        ReceiptAuthorityFlags.model_validate({"readOnly": True, "memoryWriteAllowed": True})

    with pytest.raises(ValidationError):
        SourceEvidenceReceipt.model_validate(
            {
                "sourceRef": "source_12",
                "openedAt": "2026-05-21T00:00:00Z",
                "contentDigest": "sha256:" + "c" * 64,
                "snapshotRef": "/Users/kevin/private/snapshot",
                "spanRef": "source_12_span_4",
                "quoteDigest": "sha256:" + "d" * 64,
            }
        )

    with pytest.raises(ValidationError):
        SourceEvidenceReceipt.model_validate(
            {
                "sourceRef": "private/customer-token-dump",
                "openedAt": "2026-05-21T00:00:00Z",
                "contentDigest": "sha256:" + "c" * 64,
                "snapshotRef": "snapshot_12",
                "spanRef": "source_12_span_4",
                "quoteDigest": "sha256:" + "d" * 64,
            }
        )

    with pytest.raises(ValidationError):
        SourceEvidenceReceipt.model_validate(
            {
                "sourceRef": "source_12",
                "openedAt": "2026-05-21T00:00:00Z",
                "contentDigest": "sha256:" + "c" * 64,
                "snapshotRef": "secret/customer-password-export",
                "spanRef": "source_12_span_4",
                "quoteDigest": "sha256:" + "d" * 64,
            }
        )

    with pytest.raises(ValidationError):
        SourceEvidenceReceipt.model_validate(
            {
                "sourceRef": "aaaaaaaaaaaa.bbbbbbbbbbbb.cccccccccccc",
                "openedAt": "2026-05-21T00:00:00Z",
                "contentDigest": "sha256:" + "c" * 64,
                "snapshotRef": "snapshot_12",
                "spanRef": "source_12_span_4",
                "quoteDigest": "sha256:" + "d" * 64,
            }
        )


def test_runtime_receipts_import_boundary_is_schema_only() -> None:
    code = (
        "import sys;"
        "import openmagi_core_agent.evidence.runtime_receipts;"
        "print('\\n'.join(sorted(sys.modules)))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        capture_output=True,
        text=True,
    )
    forbidden_fragments = (
        "google.adk",
        "openmagi_core_agent.transport",
        "openmagi_core_agent.tools.dispatcher",
        "openmagi_core_agent.memory",
        "openmagi_core_agent.channels",
        "kubernetes",
        "fastapi",
        "supabase",
    )
    for fragment in forbidden_fragments:
        assert fragment not in completed.stdout


def test_receipt_public_projection_sanitizes_constructed_or_copied_private_values() -> None:
    receipt = ToolExecutionReceipt.model_construct(
        receipt_id="receipt_1",
        tool_call_id="tool_1",
        tool_name="FileRead",
        tool_version="1.0.0",
        input_digest="Authorization: " + "Bearer " + "x" * 12,
        output_digest="sha256:" + "b" * 64,
        status="success",
        started_at="2026-05-21T00:00:00Z",
        ended_at="2026-05-21T00:00:01Z",
        authority_flags=ReceiptAuthorityFlags.model_construct(memory_write_allowed=True),
        policy_decision_id="policy_decision_1",
        redaction_status="redacted",
        source_ref="/Users/kevin/private/snapshot",
        artifact_ref="artifact_1",
    )
    copied = receipt.model_copy(update={"artifact_ref": "secret/customer-password-export"})

    public = receipt.public_projection()
    copied_public = copied.public_projection()

    assert public["inputDigest"] == "sha256:" + "0" * 64
    assert public["sourceRef"] == "redacted_ref"
    assert public["authorityFlags"]["memoryWriteAllowed"] is False
    assert "Bearer" not in str(public)
    assert "/Users/" not in str(public)
    assert copied_public["artifactRef"] == "redacted_ref"


def test_source_and_calculation_public_projection_sanitize_constructed_private_values() -> None:
    source = SourceEvidenceReceipt.model_construct(
        source_ref="aaaaaaaaaaaa.bbbbbbbbbbbb.cccccccccccc",
        opened_at="Authorization: " + "Bearer " + "x" * 12,
        content_digest="not-a-digest",
        snapshot_ref="/Users/kevin/private/snapshot",
        span_ref="source_12_span_4",
        quote_digest="sha256:" + "d" * 64,
    )
    calc = CalculationEvidenceReceipt.model_construct(
        calculation_ref="calc_7f31",
        input_file_digest="Bearer " + "x" * 12,
        range_ref="secret/customer-password-export",
        formula_digest="sha256:" + "f" * 64,
        result_digest="not-a-digest",
    )

    source_public = source.public_projection()
    calc_public = calc.public_projection()

    assert source_public["sourceRef"] == "redacted_ref"
    assert source_public["openedAt"] == "redacted_value"
    assert source_public["contentDigest"] == "sha256:" + "0" * 64
    assert source_public["snapshotRef"] == "redacted_ref"
    assert calc_public["inputFileDigest"] == "sha256:" + "0" * 64
    assert calc_public["rangeRef"] == "redacted_ref"
    assert calc_public["resultDigest"] == "sha256:" + "0" * 64
