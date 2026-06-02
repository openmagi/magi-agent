from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from magi_agent.transport.tool_preview import sanitize_tool_preview


SlopSeverity = Literal["info", "warn"]
SlopCleanerMode = Literal["off", "audit", "active"]
SlopCleanerSseEventType = Literal["slop_cleaner_report"]

SLOP_CLEANER_SSE_EVENT_TYPE: SlopCleanerSseEventType = "slop_cleaner_report"
SLOP_CLEANER_TRAFFIC_ATTACHED = False
SLOP_CLEANER_EXECUTION_ATTACHED = False


class SlopCleanerFinding(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

    finding_id: str = Field(alias="findingId")
    pattern_id: str = Field(alias="patternId")
    path: str
    line: int | None = None
    severity: SlopSeverity
    raw_preview: str | None = Field(default=None, alias="rawPreview")

    @field_validator("finding_id", "pattern_id", "path")
    @classmethod
    def _reject_empty_required_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("slop cleaner finding identifiers and path must be non-empty")
        return value

    @field_validator("line")
    @classmethod
    def _validate_line(cls, value: int | None) -> int | None:
        if value is not None and value <= 0:
            raise ValueError("line must be positive when present")
        return value


class SlopCleanerPublicFinding(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

    finding_id: str = Field(alias="findingId")
    pattern_id: str = Field(alias="patternId")
    path: str
    line: int | None = None
    severity: SlopSeverity
    public_preview: str | None = Field(default=None, alias="publicPreview")
    traffic_attached: bool = Field(default=False, alias="trafficAttached")
    execution_attached: bool = Field(default=False, alias="executionAttached")

    @field_validator("finding_id", "pattern_id", "path")
    @classmethod
    def _reject_empty_required_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("slop cleaner public finding identifiers and path must be non-empty")
        return value

    @field_validator("line")
    @classmethod
    def _validate_line(cls, value: int | None) -> int | None:
        if value is not None and value <= 0:
            raise ValueError("line must be positive when present")
        return value

    @field_validator("public_preview")
    @classmethod
    def _sanitize_public_preview(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return sanitize_tool_preview(value)

    @field_validator("traffic_attached", "execution_attached")
    @classmethod
    def _reject_attached_flags(cls, value: bool) -> bool:
        if value:
            raise ValueError("slop cleaner public contract is traffic-free and execution-free")
        return value


class SlopCleanerPublicReport(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

    event_type: SlopCleanerSseEventType = Field(
        default=SLOP_CLEANER_SSE_EVENT_TYPE,
        alias="eventType",
    )
    report_id: str = Field(alias="reportId")
    mode: SlopCleanerMode
    scanned_files: int = Field(alias="scannedFiles")
    findings: tuple[SlopCleanerPublicFinding, ...]
    changed_files: tuple[str, ...] = Field(default=(), alias="changedFiles")
    requires_reverify: bool = Field(alias="requiresReverify")
    report_preview: str | None = Field(default=None, alias="reportPreview")
    artifact_refs: tuple[str, ...] = Field(default=(), alias="artifactRefs")
    traffic_attached: bool = Field(default=False, alias="trafficAttached")
    execution_attached: bool = Field(default=False, alias="executionAttached")

    @field_validator("report_id")
    @classmethod
    def _reject_empty_report_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("reportId must be non-empty")
        return value

    @field_validator("scanned_files")
    @classmethod
    def _validate_scanned_files(cls, value: int) -> int:
        if value < 0:
            raise ValueError("scannedFiles must be non-negative")
        return value

    @field_validator("changed_files", "artifact_refs")
    @classmethod
    def _reject_empty_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() for item in value):
            raise ValueError("changedFiles and artifactRefs entries must be non-empty")
        return value

    @field_validator("report_preview")
    @classmethod
    def _sanitize_report_preview(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return sanitize_tool_preview(value)

    @field_validator("traffic_attached", "execution_attached")
    @classmethod
    def _reject_attached_flags(cls, value: bool) -> bool:
        if value:
            raise ValueError("slop cleaner public report is traffic-free and execution-free")
        return value


def project_slop_cleaner_public_report(
    *,
    report_id: str,
    mode: SlopCleanerMode,
    scanned_files: int,
    findings: Iterable[SlopCleanerFinding],
    requires_reverify: bool,
    changed_files: Iterable[str] = (),
    report_preview: str | None = None,
    artifact_refs: Iterable[str] = (),
) -> SlopCleanerPublicReport:
    public_findings = tuple(
        SlopCleanerPublicFinding(
            finding_id=finding.finding_id,
            pattern_id=finding.pattern_id,
            path=finding.path,
            line=finding.line,
            severity=finding.severity,
            public_preview=finding.raw_preview,
            traffic_attached=False,
            execution_attached=False,
        )
        for finding in findings
    )

    return SlopCleanerPublicReport(
        report_id=report_id,
        mode=mode,
        scanned_files=scanned_files,
        findings=public_findings,
        changed_files=tuple(changed_files),
        requires_reverify=requires_reverify,
        report_preview=report_preview,
        artifact_refs=tuple(artifact_refs),
        traffic_attached=False,
        execution_attached=False,
    )


def slop_cleaner_sse_event(report: SlopCleanerPublicReport) -> dict[str, object]:
    validated_report = SlopCleanerPublicReport.model_validate(
        report.model_dump(by_alias=True, warnings="none")
    )
    return {
        "type": SLOP_CLEANER_SSE_EVENT_TYPE,
        "report": validated_report.model_dump(by_alias=True, exclude_none=True),
        "trafficAttached": False,
        "executionAttached": False,
    }
