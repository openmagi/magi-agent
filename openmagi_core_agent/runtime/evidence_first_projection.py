from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


CoverageLabel = Literal["none", "partial", "sufficient", "complete"]
ConfidenceLabel = Literal["low", "medium", "high", "insufficient_evidence"]


_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_PUBLIC_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{1,180}$")
_PRIVATE_REF_RE = re.compile(
    r"(?:"
    r"/Users(?:/|\b)|/home(?:/|\b)|/workspace(?:/|\b)|/data/bots(?:/|\b)|"
    r"/var/lib/kubelet(?:/|\b)|Bearer\s+|github_pat_|gh[opusr]_|"
    r"xox[a-z]-|AKIA|AIza|sk-|authorization|cookie|credential|secret|"
    r"token|password|api[_-]?key|private[_-]?key"
    r")",
    re.IGNORECASE,
)
_SAFE_REASON_RE = re.compile(r"^[a-z0-9_.:-]{1,120}$")


class EvidenceFirstProjectionRequest(BaseModel):
    model_config = _MODEL_CONFIG

    opened_source_refs: tuple[str, ...] = Field(default=(), alias="openedSourceRefs")
    opened_file_refs: tuple[str, ...] = Field(default=(), alias="openedFileRefs")
    opened_page_refs: tuple[str, ...] = Field(default=(), alias="openedPageRefs")
    tool_evidence_refs: tuple[str, ...] = Field(default=(), alias="toolEvidenceRefs")
    test_evidence_refs: tuple[str, ...] = Field(default=(), alias="testEvidenceRefs")
    calculation_evidence_refs: tuple[str, ...] = Field(
        default=(),
        alias="calculationEvidenceRefs",
    )
    validator_refs: tuple[str, ...] = Field(default=(), alias="validatorRefs")
    validator_statuses: dict[str, str] = Field(default_factory=dict, alias="validatorStatuses")
    approval_refs: tuple[str, ...] = Field(default=(), alias="approvalRefs")
    coverage: CoverageLabel = "none"
    confidence_label: ConfidenceLabel = Field(default="low", alias="confidenceLabel")
    uncertainty_reason: str | None = Field(default=None, alias="uncertaintyReason")
    hidden_reasoning: str | None = Field(default=None, alias="hiddenReasoning")
    raw_tool_logs: str | None = Field(default=None, alias="rawToolLogs")
    raw_child_transcript: str | None = Field(default=None, alias="rawChildTranscript")
    raw_browser_snapshot: str | None = Field(default=None, alias="rawBrowserSnapshot")

    @field_validator(
        "opened_source_refs",
        "opened_file_refs",
        "opened_page_refs",
        "tool_evidence_refs",
        "test_evidence_refs",
        "calculation_evidence_refs",
        "validator_refs",
        "approval_refs",
    )
    @classmethod
    def _sanitize_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _safe_refs(value)


class EvidenceFirstPublicProgress(BaseModel):
    model_config = _MODEL_CONFIG

    opened_source_refs: tuple[str, ...] = Field(default=(), alias="openedSourceRefs")
    opened_file_refs: tuple[str, ...] = Field(default=(), alias="openedFileRefs")
    opened_page_refs: tuple[str, ...] = Field(default=(), alias="openedPageRefs")
    tool_evidence_refs: tuple[str, ...] = Field(default=(), alias="toolEvidenceRefs")
    test_evidence_refs: tuple[str, ...] = Field(default=(), alias="testEvidenceRefs")
    calculation_evidence_refs: tuple[str, ...] = Field(
        default=(),
        alias="calculationEvidenceRefs",
    )
    validator_refs: tuple[str, ...] = Field(default=(), alias="validatorRefs")
    validator_statuses: dict[str, str] = Field(default_factory=dict, alias="validatorStatuses")
    approval_refs: tuple[str, ...] = Field(default=(), alias="approvalRefs")
    coverage: CoverageLabel = "none"
    confidence_label: ConfidenceLabel = Field(default="low", alias="confidenceLabel")
    uncertainty_reason: str | None = Field(default=None, alias="uncertaintyReason")

    def public_projection(self) -> dict[str, object]:
        payload = self.model_dump(by_alias=True, mode="python")
        return {
            key: list(value) if isinstance(value, tuple) else value
            for key, value in payload.items()
            if value not in (None, (), {})
        }


class EvidenceFirstProjection:
    def project(self, request: EvidenceFirstProjectionRequest) -> EvidenceFirstPublicProgress:
        return EvidenceFirstPublicProgress(
            openedSourceRefs=request.opened_source_refs,
            openedFileRefs=request.opened_file_refs,
            openedPageRefs=request.opened_page_refs,
            toolEvidenceRefs=request.tool_evidence_refs,
            testEvidenceRefs=request.test_evidence_refs,
            calculationEvidenceRefs=request.calculation_evidence_refs,
            validatorRefs=request.validator_refs,
            validatorStatuses=_safe_statuses(request.validator_statuses),
            approvalRefs=request.approval_refs,
            coverage=request.coverage,
            confidenceLabel=request.confidence_label,
            uncertaintyReason=_safe_reason(request.uncertainty_reason),
        )


def _safe_refs(values: tuple[str, ...]) -> tuple[str, ...]:
    refs: list[str] = []
    for value in values:
        text = str(value).strip()
        if _PRIVATE_REF_RE.search(text) or _PUBLIC_REF_RE.fullmatch(text) is None:
            continue
        refs.append(text)
    return tuple(dict.fromkeys(refs))


def _safe_statuses(values: dict[str, str]) -> dict[str, str]:
    allowed = {"passed", "failed", "repair_required", "blocked", "skipped"}
    safe: dict[str, str] = {}
    for key, value in sorted(values.items()):
        if key in _safe_refs((key,)) and value in allowed:
            safe[key] = value
    return safe


def _safe_reason(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip().replace("-", "_")
    if _PRIVATE_REF_RE.search(text) or _SAFE_REASON_RE.fullmatch(text) is None:
        return None
    return text


__all__ = [
    "ConfidenceLabel",
    "CoverageLabel",
    "EvidenceFirstProjection",
    "EvidenceFirstProjectionRequest",
    "EvidenceFirstPublicProgress",
]
