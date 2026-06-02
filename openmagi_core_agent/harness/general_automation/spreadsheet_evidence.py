from __future__ import annotations

from collections.abc import Mapping, Sequence
from hashlib import sha256
import json
import re
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator


SpreadsheetFormat = Literal["csv"]
SchemaCheckStatus = Literal["present", "missing"]
ReconciliationStatus = Literal["matched", "mismatch"]
DeliveryClaimStatus = Literal["blocked", "claim_allowed"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)
_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{1,180}$")


class SpreadsheetEvidenceAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    adk_artifact_service_attached: Literal[False] = Field(
        default=False,
        alias="adkArtifactServiceAttached",
    )
    workbook_loaded: Literal[False] = Field(default=False, alias="workbookLoaded")
    workspace_mutated: Literal[False] = Field(default=False, alias="workspaceMutated")
    channel_delivery_performed: Literal[False] = Field(
        default=False,
        alias="channelDeliveryPerformed",
    )
    live_tool_attached: Literal[False] = Field(default=False, alias="liveToolAttached")
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set, values
        return cls()

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = update, deep
        return type(self)()


class SpreadsheetReadEvidence(BaseModel):
    model_config = _MODEL_CONFIG

    status: Literal["read_represented"] = "read_represented"
    format: SpreadsheetFormat = "csv"
    source_digest: str = Field(alias="sourceDigest")
    workbook_metadata_ref: str = Field(alias="workbookMetadataRef")
    preview_ref: str = Field(alias="previewRef")
    preview_digest: str = Field(alias="previewDigest")
    row_count: int = Field(alias="rowCount", ge=0)
    column_count: int = Field(alias="columnCount", ge=0)
    preview_row_count: int = Field(alias="previewRowCount", ge=0)
    preview_column_count: int = Field(alias="previewColumnCount", ge=0)
    truncated: bool
    authority_flags: SpreadsheetEvidenceAuthorityFlags = Field(
        default_factory=SpreadsheetEvidenceAuthorityFlags,
        alias="authorityFlags",
    )

    @field_validator("source_digest", "preview_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return _safe_digest(value)

    @field_validator("workbook_metadata_ref", "preview_ref")
    @classmethod
    def _validate_ref(cls, value: str) -> str:
        return _safe_ref(value)

    def public_projection(self) -> dict[str, object]:
        return {
            "status": self.status,
            "format": self.format,
            "sourceDigest": self.source_digest,
            "workbookMetadataRef": self.workbook_metadata_ref,
            "previewRef": self.preview_ref,
            "previewDigest": self.preview_digest,
            "rowCount": self.row_count,
            "columnCount": self.column_count,
            "previewBounds": {
                "rowCount": self.preview_row_count,
                "columnCount": self.preview_column_count,
                "truncated": self.truncated,
            },
            "adkBoundary": {
                "artifactService": "ArtifactService",
                "workbookMetadataRef": self.workbook_metadata_ref,
                "previewRef": self.preview_ref,
            },
            "authorityFlags": self.authority_flags.model_dump(
                by_alias=True,
                mode="json",
            ),
        }


class SpreadsheetSchemaCheckEvidence(BaseModel):
    model_config = _MODEL_CONFIG

    column_digest: str = Field(alias="columnDigest")
    required: Literal[True] = True
    status: SchemaCheckStatus
    check_ref: str = Field(alias="checkRef")

    @field_validator("column_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return _safe_digest(value)

    @field_validator("check_ref")
    @classmethod
    def _validate_ref(cls, value: str) -> str:
        return _safe_ref(value)

    def public_projection(self) -> dict[str, object]:
        return {
            "columnDigest": self.column_digest,
            "required": self.required,
            "status": self.status,
            "checkRef": self.check_ref,
        }


class SpreadsheetFormulaPresenceEvidence(BaseModel):
    model_config = _MODEL_CONFIG

    has_formulas: bool = Field(alias="hasFormulas")
    formula_count: int = Field(alias="formulaCount", ge=0)
    formula_cells_ref: str = Field(alias="formulaCellsRef")

    @field_validator("formula_cells_ref")
    @classmethod
    def _validate_ref(cls, value: str) -> str:
        return _safe_ref(value)

    def public_projection(self) -> dict[str, object]:
        return {
            "hasFormulas": self.has_formulas,
            "formulaCount": self.formula_count,
            "formulaCellsRef": self.formula_cells_ref,
        }


class SpreadsheetReconciliationTotalEvidence(BaseModel):
    model_config = _MODEL_CONFIG

    total_ref: str = Field(alias="totalRef")
    label_digest: str = Field(alias="labelDigest")
    expected_digest: str = Field(alias="expectedDigest")
    actual_digest: str = Field(alias="actualDigest")
    status: ReconciliationStatus

    @field_validator("total_ref")
    @classmethod
    def _validate_ref(cls, value: str) -> str:
        return _safe_ref(value)

    @field_validator("label_digest", "expected_digest", "actual_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return _safe_digest(value)

    def public_projection(self) -> dict[str, object]:
        return {
            "totalRef": self.total_ref,
            "labelDigest": self.label_digest,
            "expectedDigest": self.expected_digest,
            "actualDigest": self.actual_digest,
            "status": self.status,
        }


class SpreadsheetValidationEvidence(BaseModel):
    model_config = _MODEL_CONFIG

    status: Literal["validated"] = "validated"
    row_count: int = Field(alias="rowCount", ge=0)
    column_count: int = Field(alias="columnCount", ge=0)
    validation_evidence_ref: str = Field(alias="validationEvidenceRef")
    schema_checks: tuple[SpreadsheetSchemaCheckEvidence, ...] = Field(alias="schemaChecks")
    formula_presence: SpreadsheetFormulaPresenceEvidence = Field(alias="formulaPresence")
    reconciliation_totals: tuple[SpreadsheetReconciliationTotalEvidence, ...] = Field(
        default=(),
        alias="reconciliationTotals",
    )
    authority_flags: SpreadsheetEvidenceAuthorityFlags = Field(
        default_factory=SpreadsheetEvidenceAuthorityFlags,
        alias="authorityFlags",
    )

    @field_validator("validation_evidence_ref")
    @classmethod
    def _validate_ref(cls, value: str) -> str:
        return _safe_ref(value)

    def public_projection(self) -> dict[str, object]:
        return {
            "status": self.status,
            "rowCount": self.row_count,
            "columnCount": self.column_count,
            "validationEvidenceRef": self.validation_evidence_ref,
            "schemaChecks": [item.public_projection() for item in self.schema_checks],
            "formulaPresence": self.formula_presence.public_projection(),
            "reconciliationTotals": [
                item.public_projection() for item in self.reconciliation_totals
            ],
            "adkBoundary": {
                "artifactService": "ArtifactService",
                "validationEvidenceRef": self.validation_evidence_ref,
            },
            "authorityFlags": self.authority_flags.model_dump(
                by_alias=True,
                mode="json",
            ),
        }


class SpreadsheetWriteEvidence(BaseModel):
    model_config = _MODEL_CONFIG

    status: Literal["artifact_recorded"] = "artifact_recorded"
    artifact_ref: str = Field(alias="artifactRef")
    source_snapshot_ref: str = Field(alias="sourceSnapshotRef")
    content_digest: str = Field(alias="contentDigest")
    row_count: int = Field(alias="rowCount", ge=0)
    column_count: int = Field(alias="columnCount", ge=0)
    delivery_claimed: Literal[False] = Field(default=False, alias="deliveryClaimed")
    authority_flags: SpreadsheetEvidenceAuthorityFlags = Field(
        default_factory=SpreadsheetEvidenceAuthorityFlags,
        alias="authorityFlags",
    )

    @field_validator("artifact_ref", "source_snapshot_ref")
    @classmethod
    def _validate_ref(cls, value: str) -> str:
        return _safe_ref(value)

    @field_validator("content_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        return _safe_digest(value)

    def public_projection(self) -> dict[str, object]:
        return {
            "status": self.status,
            "artifactRef": self.artifact_ref,
            "sourceSnapshotRef": self.source_snapshot_ref,
            "contentDigest": self.content_digest,
            "rowCount": self.row_count,
            "columnCount": self.column_count,
            "deliveryClaimed": self.delivery_claimed,
            "adkBoundary": {
                "artifactService": "ArtifactService",
                "artifactRef": self.artifact_ref,
            },
            "authorityFlags": self.authority_flags.model_dump(
                by_alias=True,
                mode="json",
            ),
        }


class SpreadsheetDeliveryClaimDecision(BaseModel):
    model_config = _MODEL_CONFIG

    status: DeliveryClaimStatus
    artifact_ref: str = Field(alias="artifactRef")
    channel_delivery_receipt_ref: str | None = Field(
        default=None,
        alias="channelDeliveryReceiptRef",
    )
    final_answer_delivery_claim_allowed: bool = Field(
        alias="finalAnswerDeliveryClaimAllowed",
    )
    reason_codes: tuple[str, ...] = Field(alias="reasonCodes")
    authority_flags: SpreadsheetEvidenceAuthorityFlags = Field(
        default_factory=SpreadsheetEvidenceAuthorityFlags,
        alias="authorityFlags",
    )

    @field_validator("artifact_ref", "channel_delivery_receipt_ref")
    @classmethod
    def _validate_ref(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _safe_ref(value)

    def public_projection(self) -> dict[str, object]:
        return {
            "status": self.status,
            "artifactRef": self.artifact_ref,
            "channelDeliveryReceiptRef": self.channel_delivery_receipt_ref,
            "finalAnswerDeliveryClaimAllowed": self.final_answer_delivery_claim_allowed,
            "reasonCodes": self.reason_codes,
            "authorityFlags": self.authority_flags.model_dump(
                by_alias=True,
                mode="json",
            ),
        }


def build_spreadsheet_read_evidence(
    *,
    format: SpreadsheetFormat,
    rows: Sequence[Sequence[object]],
    maxPreviewRows: int = 10,
    maxPreviewCols: int = 8,
) -> SpreadsheetReadEvidence:
    table = _normalize_rows(rows)
    row_count = len(table)
    column_count = max((len(row) for row in table), default=0)
    preview_row_count = min(row_count, max(maxPreviewRows, 0))
    preview_column_count = min(column_count, max(maxPreviewCols, 0))
    preview_rows = tuple(row[:preview_column_count] for row in table[:preview_row_count])
    source_digest = _digest(table)
    preview_digest = _digest(preview_rows)
    workbook_metadata_ref = _artifact_ref(
        "spreadsheet-workbook",
        {
            "format": format,
            "sourceDigest": source_digest,
            "rowCount": row_count,
            "columnCount": column_count,
        },
    )
    preview_ref = _artifact_ref(
        "spreadsheet-preview",
        {
            "sourceDigest": source_digest,
            "previewDigest": preview_digest,
            "previewRowCount": preview_row_count,
            "previewColumnCount": preview_column_count,
        },
    )
    return SpreadsheetReadEvidence(
        format=format,
        sourceDigest=source_digest,
        workbookMetadataRef=workbook_metadata_ref,
        previewRef=preview_ref,
        previewDigest=preview_digest,
        rowCount=row_count,
        columnCount=column_count,
        previewRowCount=preview_row_count,
        previewColumnCount=preview_column_count,
        truncated=row_count > preview_row_count or column_count > preview_column_count,
    )


def build_spreadsheet_validation_evidence(
    *,
    rows: Sequence[Sequence[object]],
    requiredColumns: Sequence[str],
    reconciliationTotals: Mapping[str, Mapping[str, object]] | None = None,
) -> SpreadsheetValidationEvidence:
    table = _normalize_rows(rows)
    row_count = len(table)
    column_count = max((len(row) for row in table), default=0)
    headers = {cell.casefold() for cell in table[0]} if table else set()
    schema_checks = tuple(
        _schema_check(required_column, required_column.casefold() in headers)
        for required_column in requiredColumns
    )
    formula_cells = tuple(
        {"row": row_index, "column": column_index, "formulaDigest": _digest(cell)}
        for row_index, row in enumerate(table)
        for column_index, cell in enumerate(row)
        if cell.lstrip().startswith("=")
    )
    formula_presence = SpreadsheetFormulaPresenceEvidence(
        hasFormulas=bool(formula_cells),
        formulaCount=len(formula_cells),
        formulaCellsRef=_artifact_ref("spreadsheet-formulas", formula_cells),
    )
    totals = tuple(
        _reconciliation_total(label, values)
        for label, values in sorted((reconciliationTotals or {}).items())
    )
    validation_evidence_ref = _artifact_ref(
        "spreadsheet-validation",
        {
            "rowCount": row_count,
            "columnCount": column_count,
            "schemaChecks": [item.public_projection() for item in schema_checks],
            "formulaPresence": formula_presence.public_projection(),
            "reconciliationTotals": [item.public_projection() for item in totals],
        },
    )
    return SpreadsheetValidationEvidence(
        rowCount=row_count,
        columnCount=column_count,
        validationEvidenceRef=validation_evidence_ref,
        schemaChecks=schema_checks,
        formulaPresence=formula_presence,
        reconciliationTotals=totals,
    )


def build_spreadsheet_write_evidence(
    *,
    artifactRef: str,
    sourceSnapshotRef: str,
    contentDigest: str,
    rowCount: int,
    columnCount: int,
) -> SpreadsheetWriteEvidence:
    if not artifactRef:
        raise ValueError("artifactRef is required")
    if not sourceSnapshotRef:
        raise ValueError("sourceSnapshotRef is required")
    return SpreadsheetWriteEvidence(
        artifactRef=artifactRef,
        sourceSnapshotRef=sourceSnapshotRef,
        contentDigest=contentDigest,
        rowCount=rowCount,
        columnCount=columnCount,
    )


def evaluate_spreadsheet_delivery_claim(
    *,
    artifactRef: str,
    channelDeliveryReceiptRef: str | None = None,
) -> SpreadsheetDeliveryClaimDecision:
    if channelDeliveryReceiptRef:
        return SpreadsheetDeliveryClaimDecision(
            status="claim_allowed",
            artifactRef=artifactRef,
            channelDeliveryReceiptRef=channelDeliveryReceiptRef,
            finalAnswerDeliveryClaimAllowed=True,
            reasonCodes=("channel_delivery_receipt_present",),
        )
    return SpreadsheetDeliveryClaimDecision(
        status="blocked",
        artifactRef=artifactRef,
        finalAnswerDeliveryClaimAllowed=False,
        reasonCodes=("channel_delivery_receipt_required",),
    )


def _schema_check(column_name: str, present: bool) -> SpreadsheetSchemaCheckEvidence:
    status: SchemaCheckStatus = "present" if present else "missing"
    column_digest = _digest(column_name)
    return SpreadsheetSchemaCheckEvidence(
        columnDigest=column_digest,
        status=status,
        checkRef=_artifact_ref(
            "spreadsheet-schema-check",
            {"columnDigest": column_digest, "status": status},
        ),
    )


def _reconciliation_total(
    label: str,
    values: Mapping[str, object],
) -> SpreadsheetReconciliationTotalEvidence:
    expected = values.get("expected")
    actual = values.get("actual")
    status: ReconciliationStatus = "matched" if str(expected) == str(actual) else "mismatch"
    label_digest = _digest(label)
    expected_digest = _digest(expected)
    actual_digest = _digest(actual)
    return SpreadsheetReconciliationTotalEvidence(
        totalRef=_artifact_ref(
            "spreadsheet-total",
            {
                "labelDigest": label_digest,
                "expectedDigest": expected_digest,
                "actualDigest": actual_digest,
                "status": status,
            },
        ),
        labelDigest=label_digest,
        expectedDigest=expected_digest,
        actualDigest=actual_digest,
        status=status,
    )


def _normalize_rows(rows: Sequence[Sequence[object]]) -> tuple[tuple[str, ...], ...]:
    return tuple(tuple("" if cell is None else str(cell) for cell in row) for row in rows)


def _artifact_ref(kind: str, material: object) -> str:
    return f"artifact:{kind}:{_digest(material)}"


def _safe_digest(value: str) -> str:
    if not _DIGEST_RE.fullmatch(value):
        raise ValueError("digest must be sha256")
    return value


def _safe_ref(value: str) -> str:
    if not value or not _REF_RE.fullmatch(value):
        raise ValueError("ref must be a safe public reference")
    return value


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=repr,
    ).encode("utf-8")
    return f"sha256:{sha256(encoded).hexdigest()}"


__all__ = [
    "SpreadsheetDeliveryClaimDecision",
    "SpreadsheetEvidenceAuthorityFlags",
    "SpreadsheetFormulaPresenceEvidence",
    "SpreadsheetReadEvidence",
    "SpreadsheetReconciliationTotalEvidence",
    "SpreadsheetSchemaCheckEvidence",
    "SpreadsheetValidationEvidence",
    "SpreadsheetWriteEvidence",
    "build_spreadsheet_read_evidence",
    "build_spreadsheet_validation_evidence",
    "build_spreadsheet_write_evidence",
    "evaluate_spreadsheet_delivery_claim",
]
