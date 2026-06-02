from __future__ import annotations

from pathlib import Path

import pytest

from magi_agent.harness.general_automation.spreadsheet_evidence import (
    build_spreadsheet_read_evidence,
    build_spreadsheet_validation_evidence,
    build_spreadsheet_write_evidence,
    evaluate_spreadsheet_delivery_claim,
)
from magi_agent.recipes.first_party.general_automation.spreadsheet_contracts import (
    get_spreadsheet_operation_contract,
    spreadsheet_contract_catalog,
)


PYTHON_ROOT = Path(__file__).resolve().parents[1]
HARNESS_DIR = PYTHON_ROOT / "magi_agent" / "harness" / "general_automation"
RECIPE_DIR = (
    PYTHON_ROOT
    / "magi_agent"
    / "recipes"
    / "first_party"
    / "general_automation"
)


def _contains_fragment(value: object, fragment: str) -> bool:
    if isinstance(value, str):
        return fragment in value
    if isinstance(value, dict):
        return any(
            _contains_fragment(key, fragment) or _contains_fragment(item, fragment)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_fragment(item, fragment) for item in value)
    return False


def _fragment(*parts: str) -> str:
    return "".join(parts)


def test_spreadsheet_contract_catalog_maps_read_write_preview_without_aliasing() -> None:
    catalog = {contract.operation_id: contract for contract in spreadsheet_contract_catalog()}

    assert set(catalog) >= {
        "spreadsheet.read",
        "spreadsheet.write",
        "spreadsheet.preview",
        "spreadsheet.validate",
        "spreadsheet.reconcile",
        "spreadsheet.xlsx.read",
        "spreadsheet.xlsx.write",
    }
    assert catalog["spreadsheet.read"].tool_name == "CSVRead"
    assert catalog["spreadsheet.read"].permission_class == "read"
    assert catalog["spreadsheet.read"].tool_name != "SpreadsheetWrite"
    assert catalog["spreadsheet.write"].tool_name == "CSVWrite"
    assert catalog["spreadsheet.preview"].tool_name == "SpreadsheetPreview"

    for operation_id in ("spreadsheet.read", "spreadsheet.write", "spreadsheet.preview"):
        public = catalog[operation_id].public_projection()
        assert public["adkTool"]["adkToolType"] == "FunctionTool"
        assert public["adkTool"]["enabledByDefault"] is False
        assert public["adkTool"]["handlerAttached"] is False
        assert public["authorityFlags"] == {
            "adkArtifactServiceAttached": False,
            "workspaceMutated": False,
            "channelDeliveryPerformed": False,
            "liveToolAttached": False,
            "routeAttached": False,
        }


def test_xlsx_contracts_remain_blocked_until_dependency_or_worker_approval() -> None:
    xlsx_read = get_spreadsheet_operation_contract("spreadsheet.xlsx.read")
    xlsx_write = get_spreadsheet_operation_contract("spreadsheet.xlsx.write")

    assert xlsx_read.supported is False
    assert xlsx_write.supported is False
    assert xlsx_read.blocked_reason == "xlsx_dependency_or_worker_approval_required"
    assert xlsx_write.blocked_reason == "xlsx_dependency_or_worker_approval_required"
    assert xlsx_read.public_projection()["adkTool"]["handlerAttached"] is False
    assert xlsx_write.public_projection()["adkTool"]["handlerAttached"] is False


def test_read_evidence_bounds_preview_and_projects_refs_without_raw_rows() -> None:
    rows = (
        ("name", "amount", "note"),
        ("Ada", "10", "Authorization: Bearer fixture-token"),
        ("Grace", "20", "/Users/acme/private/export.csv"),
    )

    evidence = build_spreadsheet_read_evidence(
        format="csv",
        rows=rows,
        maxPreviewRows=2,
        maxPreviewCols=1,
    )
    public = evidence.public_projection()

    assert public["status"] == "read_represented"
    assert public["format"] == "csv"
    assert public["rowCount"] == 3
    assert public["columnCount"] == 3
    assert public["previewBounds"] == {"rowCount": 2, "columnCount": 1, "truncated": True}
    assert public["workbookMetadataRef"].startswith("artifact:spreadsheet-workbook:sha256:")
    assert public["previewRef"].startswith("artifact:spreadsheet-preview:sha256:")
    assert public["previewDigest"].startswith("sha256:")
    assert public["adkBoundary"] == {
        "artifactService": "ArtifactService",
        "workbookMetadataRef": public["workbookMetadataRef"],
        "previewRef": public["previewRef"],
    }
    dumped = str(public)
    assert "Ada" not in dumped
    assert "fixture-token" not in dumped
    assert "/Users/acme" not in dumped


def test_validation_evidence_records_schema_formula_and_reconciliation_refs() -> None:
    rows = (
        ("name", "amount", "total"),
        ("Ada", "10", "=B2*2"),
        ("Grace", "20", "40"),
    )

    evidence = build_spreadsheet_validation_evidence(
        rows=rows,
        requiredColumns=("name", "amount", "missing"),
        reconciliationTotals={
            "amount": {"expected": "30.00", "actual": "30.00"},
            "tax": {"expected": "3.00", "actual": "4.00"},
        },
    )
    public = evidence.public_projection()

    assert public["status"] == "validated"
    assert public["rowCount"] == 3
    assert public["columnCount"] == 3
    assert [item["status"] for item in public["schemaChecks"]] == [
        "present",
        "present",
        "missing",
    ]
    assert all(item["columnDigest"].startswith("sha256:") for item in public["schemaChecks"])
    assert public["formulaPresence"]["hasFormulas"] is True
    assert public["formulaPresence"]["formulaCount"] == 1
    assert public["formulaPresence"]["formulaCellsRef"].startswith(
        "artifact:spreadsheet-formulas:sha256:"
    )
    assert [item["status"] for item in public["reconciliationTotals"]] == ["matched", "mismatch"]
    assert all(
        item["totalRef"].startswith("artifact:spreadsheet-total:sha256:")
        for item in public["reconciliationTotals"]
    )
    dumped = str(public)
    assert "amount" not in dumped
    assert "30.00" not in dumped
    assert "=B2" not in dumped


def test_write_evidence_requires_snapshot_artifact_refs_and_delivery_receipt() -> None:
    with pytest.raises(ValueError, match="artifactRef"):
        build_spreadsheet_write_evidence(
            artifactRef="",
            sourceSnapshotRef="snapshot:workspace:sha256:"
            "0000000000000000000000000000000000000000000000000000000000000000",
            contentDigest="sha256:1111111111111111111111111111111111111111111111111111111111111111",
            rowCount=2,
            columnCount=2,
        )
    with pytest.raises(ValueError, match="sourceSnapshotRef"):
        build_spreadsheet_write_evidence(
            artifactRef="artifact:spreadsheet:sha256:"
            "2222222222222222222222222222222222222222222222222222222222222222",
            sourceSnapshotRef="",
            contentDigest="sha256:1111111111111111111111111111111111111111111111111111111111111111",
            rowCount=2,
            columnCount=2,
        )

    write = build_spreadsheet_write_evidence(
        artifactRef="artifact:spreadsheet:sha256:"
        "2222222222222222222222222222222222222222222222222222222222222222",
        sourceSnapshotRef="snapshot:workspace:sha256:"
        "0000000000000000000000000000000000000000000000000000000000000000",
        contentDigest="sha256:1111111111111111111111111111111111111111111111111111111111111111",
        rowCount=2,
        columnCount=2,
    )
    blocked_claim = evaluate_spreadsheet_delivery_claim(artifactRef=write.artifact_ref)
    allowed_claim = evaluate_spreadsheet_delivery_claim(
        artifactRef=write.artifact_ref,
        channelDeliveryReceiptRef="receipt:channel:sha256:"
        "3333333333333333333333333333333333333333333333333333333333333333",
    )

    assert write.public_projection()["deliveryClaimed"] is False
    assert write.public_projection()["adkBoundary"] == {
        "artifactService": "ArtifactService",
        "artifactRef": write.artifact_ref,
    }
    assert blocked_claim.public_projection()["status"] == "blocked"
    assert blocked_claim.public_projection()["finalAnswerDeliveryClaimAllowed"] is False
    assert allowed_claim.public_projection()["status"] == "claim_allowed"
    assert allowed_claim.public_projection()["finalAnswerDeliveryClaimAllowed"] is True


def test_spreadsheet_contract_modules_do_not_touch_core_or_live_services() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            HARNESS_DIR / "spreadsheet_evidence.py",
            RECIPE_DIR / "spreadsheet_contracts.py",
        )
    )

    forbidden_fragments = (
        "magi_agent.adk_bridge",
        "magi_agent.runtime",
        "magi_agent.tools.dispatcher",
        "magi_agent.tools.registry",
        "magi_agent.tools.permission",
        "magi_agent.tools.result",
        "google.adk.runners",
        "ArtifactService(",
        _fragment("sub", "process"),
        "requests",
        "httpx",
        ".write_text(",
        ".read_text(",
        "open(",
    )
    for fragment in forbidden_fragments:
        assert fragment not in source
