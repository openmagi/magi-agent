from __future__ import annotations

import importlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError
from pydantic import BaseModel

from openmagi_core_agent.security.compliance import (
    ComplianceAuthorityFlags,
    ComplianceReportRef,
    PolicyKernelDecisionRecord,
    RollbackFallbackDiagnosticRef,
    build_compliance_report_ref,
    record_policy_kernel_decision,
)


PYTHON_ROOT = Path(__file__).parents[1]
MODULE_PATH = PYTHON_ROOT / "openmagi_core_agent" / "security" / "compliance.py"


def _digest(character: str) -> str:
    return "sha256:" + character * 64


def test_policy_kernel_decision_record_is_digest_only_and_default_off() -> None:
    report = build_compliance_report_ref(
        reportRef="compliance:daily-contract-0001",
        policySnapshotDigest=_digest("1"),
        evidenceLedgerDigest=_digest("2"),
        generatedAt=datetime(2026, 5, 26, 8, 0, tzinfo=UTC),
        metadata={"suiteRef": "security-compliance-suite"},
    )
    fallback = RollbackFallbackDiagnosticRef(
        diagnosticRef="fallback:typescript-restoration-0001",
        diagnosticDigest=_digest("3"),
        routeRef="route:python-default-off",
        reasonCodes=("python_route_disabled", "typescript_restoration_available"),
    )
    record = record_policy_kernel_decision(
        decisionId="decision:policy-kernel-0001",
        decisionKind="policy",
        subjectRef="recipe:product-admin-readonly",
        policySnapshotDigest=_digest("4"),
        kernelSnapshotDigest=_digest("5"),
        evidenceDigest=_digest("6"),
        reasonCodes=("default_off_contract",),
        fallbackDiagnostic=fallback,
        complianceReport=report,
        metadata={"opsRef": "ops:trace-contract"},
    )

    payload = record.public_projection()
    encoded = json.dumps(payload, sort_keys=True).lower()

    assert record.decision_digest.startswith("sha256:")
    assert payload["schemaVersion"] == "openmagi.security.compliance.public.v1"
    assert payload["decisionId"] == "decision:policy-kernel-0001"
    assert payload["decisionKind"] == "policy"
    assert payload["decisionDigest"] == record.decision_digest
    assert payload["policySnapshotDigest"] == _digest("4")
    assert payload["kernelSnapshotDigest"] == _digest("5")
    assert payload["evidenceDigest"] == _digest("6")
    assert payload["fallbackDiagnostic"]["diagnosticDigest"] == _digest("3")
    assert payload["complianceReport"]["reportDigest"] == report.report_digest
    assert set(payload["authorityFlags"].values()) == {False}
    for forbidden in (
        "raw prompt",
        "raw output",
        "hidden reasoning",
        "authorization",
        "cookie",
        "session key",
        "credential",
        "token",
        "/users/",
        "/private/",
        ".env",
    ):
        assert forbidden not in encoded


@pytest.mark.parametrize(
    "metadata",
    (
        {"rawPrompt": "prompt-ref"},
        {"rawOutput": "output-ref"},
        {"authHeader": "header-ref"},
        {"cookie": "cookie-ref"},
        {"sessionKey": "session-ref"},
        {"privatePath": "path-ref"},
        {"toolOutputDigest": _digest("7")},
    ),
)
def test_policy_kernel_decision_rejects_raw_or_private_metadata(metadata: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        PolicyKernelDecisionRecord(
            decisionId="decision:unsafe",
            decisionKind="kernel",
            subjectRef="kernel:projection",
            policySnapshotDigest=_digest("1"),
            kernelSnapshotDigest=_digest("2"),
            evidenceDigest=_digest("3"),
            reasonCodes=("unsafe_metadata",),
            metadata=metadata,
        )


@pytest.mark.parametrize(
    "unsafe_value",
    (
        "/Users/kevin/.env",
        "authorization: bearer token",
        "raw prompt text",
        "hidden reasoning",
        "cookie: sid=abc",
        "sk-" + "a" * 32,
    ),
)
def test_compliance_validation_errors_do_not_echo_raw_inputs(unsafe_value: str) -> None:
    with pytest.raises(ValidationError) as exc_info:
        RollbackFallbackDiagnosticRef(
            diagnosticRef=unsafe_value,
            diagnosticDigest=_digest("1"),
            routeRef="route:default-off",
            reasonCodes=("blocked",),
        )

    encoded_error = json.dumps(exc_info.value.errors(), default=str).lower()
    assert unsafe_value.lower() not in str(exc_info.value).lower()
    assert unsafe_value.lower() not in encoded_error


def test_authority_flags_cannot_be_forged_or_copied_to_live() -> None:
    flags = ComplianceAuthorityFlags(
        publicRouteAttached=True,
        productionWrite=True,
        userVisibleOutputAllowed=True,
        modelCalled=True,
        toolHostDispatched=True,
        networkCallAllowed=True,
    )

    assert flags.public_projection() == {
        "publicRouteAttached": False,
        "productionWrite": False,
        "userVisibleOutputAllowed": False,
        "modelCalled": False,
        "toolHostDispatched": False,
        "networkCallAllowed": False,
        "rawPayloadAttached": False,
    }
    with pytest.raises(ValueError, match="model_copy update"):
        flags.model_copy(update={"productionWrite": True})
    with pytest.raises(ValueError, match="model_construct"):
        ComplianceAuthorityFlags.model_construct(publicRouteAttached=True)


def test_compliance_report_ref_requires_digest_refs_not_raw_report() -> None:
    with pytest.raises(ValidationError):
        ComplianceReportRef(
            reportRef="compliance:report-0001",
            reportDigest="raw report body",
            policySnapshotDigest=_digest("1"),
            evidenceLedgerDigest=_digest("2"),
            generatedAt=datetime(2026, 5, 26, 8, 0, tzinfo=UTC),
        )


def test_forged_nested_compliance_refs_do_not_project_raw_material() -> None:
    forged_diagnostic = BaseModel.model_construct.__func__(
        RollbackFallbackDiagnosticRef,
        diagnostic_ref="/Users/kevin/.env",
        diagnostic_digest="raw prompt",
        route_ref="route:default-off",
        reason_codes=("blocked",),
        metadata={},
    )
    record = PolicyKernelDecisionRecord(
        decisionId="decision:policy-kernel-0001",
        decisionKind="policy",
        subjectRef="recipe:product-admin-readonly",
        policySnapshotDigest=_digest("1"),
        kernelSnapshotDigest=_digest("2"),
        evidenceDigest=_digest("3"),
        reasonCodes=("default_off_contract",),
        fallbackDiagnostic=forged_diagnostic,
    )

    with pytest.raises(ValueError):
        record.public_projection()

    forged_report = BaseModel.model_construct.__func__(
        ComplianceReportRef,
        report_ref="compliance:report-0001",
        report_digest="/Users/kevin/.env",
        policy_snapshot_digest=_digest("1"),
        evidence_ledger_digest=_digest("2"),
        generated_at=datetime(2026, 5, 26, 8, 0, tzinfo=UTC),
        metadata={},
    )
    record_with_report = PolicyKernelDecisionRecord(
        decisionId="decision:policy-kernel-0002",
        decisionKind="policy",
        subjectRef="recipe:product-admin-readonly",
        policySnapshotDigest=_digest("1"),
        kernelSnapshotDigest=_digest("2"),
        evidenceDigest=_digest("3"),
        reasonCodes=("default_off_contract",),
        complianceReport=forged_report,
    )

    with pytest.raises(ValueError):
        record_with_report.public_projection()


def test_compliance_contract_import_boundary_has_no_live_runtime_imports() -> None:
    module = importlib.import_module("openmagi_core_agent.security.compliance")

    assert not hasattr(module, "Runner")
    assert not hasattr(module, "ToolHost")

    script = (
        "import sys\n"
        "import openmagi_core_agent.security.compliance\n"
        "forbidden=('google.adk','kubernetes','supabase','stripe','httpx','requests')\n"
        "loaded=[name for name in forbidden if name in sys.modules]\n"
        "raise SystemExit(1 if loaded else 0)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=PYTHON_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_compliance_module_does_not_add_public_routes_or_mutations() -> None:
    text = MODULE_PATH.read_text()

    assert "@app." not in text
    assert "FastAPI" not in text
    assert "requests." not in text
    assert "httpx." not in text
    assert "subprocess" not in text
