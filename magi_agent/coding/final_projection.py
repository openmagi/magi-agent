"""PR7: Governed Coding Final Projection.

Ensures final coding responses only include verified claims backed by
evidence (mutation receipts, diff evidence, test runs). Unverified
"done" / "fixed" / "all tests pass" claims are downgraded or blocked.

All models are default-off with productionWorkspaceMutationAllowed=False.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)

_SHA256_DIGEST_RE = re.compile(r"^sha256:[a-fA-F0-9]{64}$")
_RECEIPT_REF_RE = re.compile(r"^(?:receipt:)?sha256:[a-fA-F0-9]{64}$")
_DIFF_EVIDENCE_REF_RE = re.compile(r"^diff:[A-Za-z0-9][A-Za-z0-9_.:-]{0,160}$")
_TEST_EVIDENCE_REF_RE = re.compile(r"^test:[A-Za-z0-9][A-Za-z0-9_.:-]{0,160}$")
_TEST_SUITE_REF_RE = re.compile(r"^test:[A-Za-z0-9][A-Za-z0-9_.:-]{0,160}$")

_VALID_OPERATIONS = frozenset({"created", "modified", "deleted"})
_VALID_TEST_STATUSES = frozenset({"pass", "failed"})
_VALID_GAP_TYPES = frozenset({
    "missing_test_evidence",
    "failed_test_evidence",
    "incomplete_scope",
    "missing_rollback_receipt",
    "missing_diff_evidence",
    "stale_read_evidence",
})

# Patterns for unsupported completion claims
_UNSUPPORTED_CLAIM_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\ball\s+tests?\s+pass(?:ing|ed)?\b", re.IGNORECASE),
    re.compile(r"\beverything\s+is\s+done\b", re.IGNORECASE),
    re.compile(r"\bi\s+fixed\b", re.IGNORECASE),
    re.compile(r"\bimplementation\s+is\s+complete\b", re.IGNORECASE),
    re.compile(r"\bapplied\s+successfully\b", re.IGNORECASE),
    re.compile(r"\btests?\s+are\s+passing\b", re.IGNORECASE),
    re.compile(r"\bdone!?\s", re.IGNORECASE),
    re.compile(r"\bfixed\s+and\s+(?:verified|confirmed|tested)\b", re.IGNORECASE),
    re.compile(r"\ball\s+(?:changes?\s+)?(?:have\s+been\s+)?(?:applied|completed|done|finished)\b", re.IGNORECASE),
    re.compile(r"\ball\s+good\b", re.IGNORECASE),
)

# Production path patterns that must not appear in public projections
_PRODUCTION_PATH_RE = re.compile(
    r"(?:/data/bots|/workspace|/var/lib/kubelet|/Users|/home|/root|/private|/mnt)"
    r"(?:/[^\s\"',}]+)*",
    re.IGNORECASE,
)
_PRIVATE_PATH_RE = re.compile(
    r"(?:[A-Za-z]:[\\/][^,\s\"']+|\\\\[^\\\s\"']+\\[^,\s\"']+)",
    re.IGNORECASE,
)
_AUTH_PUBLIC_TEXT_RE = re.compile(
    r"(?:"
    r"Authorization\s*:|Cookie\s*:|Set-Cookie\s*:|"
    r"Cookie\s+[A-Za-z0-9_.-]+\s*=\s*[^,\s}{\n]{4,}|"
    r"Bearer\s+[A-Za-z0-9._~+/=-]{6,}|"
    r"sk-(?:live|test)?[-_A-Za-z0-9]{8,}|"
    r"(?:api[\s_-]*key|access[\s_-]*token|auth[\s_-]*token|session[\s_-]*key|"
    r"connector[\s_-]*token|refresh[\s_-]*token|id[\s_-]*token|private[\s_-]*key)"
    r"\s+(?:is\s+)?(?=[A-Za-z0-9._~+/=-]{4,}\b)"
    r"(?=[A-Za-z0-9._~+/=-]*\d)[A-Za-z0-9._~+/=-]{4,}|"
    r"(?:access|auth|connector|session|refresh|id|api|private)[\s_-]+"
    r"(?:token|key)\s+(?:is\s+)?[A-Za-z0-9._~+/=-]{8,}|"
    r"\b(?:token|password|secret|cookie)\s+(?:is\s+)?"
    r"(?=[A-Za-z0-9._~+/=-]{4,}\b)(?=[A-Za-z0-9._~+/=-]*\d)"
    r"[A-Za-z0-9._~+/=-]{4,}|"
    r"\b(?:token|password|secret|cookie)\s+(?:is\s+)?[A-Za-z0-9._~+/=-]{8,}|"
    r"callback\s+code\s+[A-Za-z0-9._~+/=-]{6,}|"
    r"[A-Za-z0-9_.-]*(?:token|cookie)\s*=|"
    r"(?:[A-Za-z0-9_.-]*(?:token|cookie)|code|session|state)"
    r"\s*=\s*[^,\s}{\n]{4,}|"
    r"(?:api[_-]?key|access[_-]?token|auth[_-]?token|session[_-]?key|"
    r"connector[_-]?token|secret|password|credential|private[_-]?key)"
    r"\s*[:=]\s*[^,\s}{\n]{4,}|"
    r"(?:[?#&]|%(?:25)*(?:3f|23|26))"
    r"[^\s\"'<>)]*(?:auth|authorization|callback|code|cookie|session|state|token)"
    r"[^\s\"'<>)]*"
    r")",
    re.IGNORECASE,
)
_PRIVATE_PUBLIC_TEXT_RE = re.compile(
    r"\b(?:"
    r"raw[\s_-]+(?:source|tool|prompt|output|result|response|transcript|child|log)"
    r"(?:[\s_-]+(?:source|tool|prompt|input|output|result|response|"
    r"transcript|child|log|body|content|text|html|snapshot))*|"
    r"child[\s_-]+(?:evidence|output|result|transcript|payload)|"
    r"hidden[\s_-]+reasoning|chain[\s_-]+of[\s_-]+thought|"
    r"private[\s_-]+(?:payload|prompt|context|memory|transcript|source)"
    r")\b",
    re.IGNORECASE,
)
_SENSITIVE_ROUTE_TEXT_RE = re.compile(
    r"(?<![A-Za-z0-9._/-])"
    r"(?:https?://[^\s\"'<>)]*)?"
    r"(?:/[A-Za-z0-9._-]+)*/token"
    r"(?:[/?#][^\s\"'<>)]*)?(?=$|[\s\"'<>),.;:])",
    re.IGNORECASE,
)

FileChangeOperation = Literal["created", "modified", "deleted"]
TestStatus = Literal["pass", "failed"]
GapType = Literal[
    "missing_test_evidence",
    "failed_test_evidence",
    "incomplete_scope",
    "missing_rollback_receipt",
    "missing_diff_evidence",
    "stale_read_evidence",
]
ProjectionStatus = Literal["complete", "incomplete"]


class FileChangeRecord(BaseModel):
    """A single file change backed by digest and diff evidence."""

    model_config = _MODEL_CONFIG

    file_digest: str = Field(alias="fileDigest")
    operation: FileChangeOperation
    diff_evidence_ref: str = Field(alias="diffEvidenceRef")

    @field_validator("file_digest")
    @classmethod
    def _validate_file_digest(cls, value: str) -> str:
        if _SHA256_DIGEST_RE.fullmatch(value) is None:
            raise ValueError("fileDigest must be a sha256:<hex64> digest")
        return value

    @field_validator("diff_evidence_ref")
    @classmethod
    def _validate_diff_evidence_ref(cls, value: str) -> str:
        if _DIFF_EVIDENCE_REF_RE.fullmatch(value) is None:
            raise ValueError("diffEvidenceRef must be a valid diff: reference")
        return value


class TestRunRecord(BaseModel):
    """A test run backed by evidence receipt."""

    model_config = _MODEL_CONFIG

    test_suite_ref: str = Field(alias="testSuiteRef")
    status: TestStatus
    evidence_ref: str = Field(alias="evidenceRef")

    @field_validator("test_suite_ref")
    @classmethod
    def _validate_test_suite_ref(cls, value: str) -> str:
        if _TEST_SUITE_REF_RE.fullmatch(value) is None:
            raise ValueError("testSuiteRef must be a valid test: reference")
        return value

    @field_validator("evidence_ref")
    @classmethod
    def _validate_evidence_ref(cls, value: str) -> str:
        if _TEST_EVIDENCE_REF_RE.fullmatch(value) is None:
            raise ValueError("evidenceRef must be a valid test: reference")
        return value


class EvidenceGap(BaseModel):
    """An identified gap in the evidence chain."""

    model_config = _MODEL_CONFIG

    gap_type: GapType = Field(alias="gapType")
    description: str

    @field_validator("description")
    @classmethod
    def _validate_description(cls, value: str) -> str:
        if not value or len(value) > 500:
            raise ValueError("description must be 1-500 characters")
        if _contains_unsafe_public_text(value):
            raise ValueError("description must not contain unsafe public text")
        return value


class RollbackStatus(BaseModel):
    """Gate 2 rollback receipt status."""

    model_config = _MODEL_CONFIG

    gate2_receipt_ref: str | None = Field(alias="gate2ReceiptRef")
    verified: bool

    @field_validator("gate2_receipt_ref")
    @classmethod
    def _validate_gate2_receipt_ref(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if _RECEIPT_REF_RE.fullmatch(value) is None:
            raise ValueError("gate2ReceiptRef must be a valid receipt reference")
        return value

    @model_validator(mode="after")
    def _validate_consistency(self) -> Self:
        if self.verified and self.gate2_receipt_ref is None:
            raise ValueError("verified rollback requires a gate2ReceiptRef")
        return self


class CodingFinalProjection(BaseModel):
    """The governed final projection of a coding task.

    Only verified claims backed by evidence are allowed. Each section
    is explicitly typed to prevent unverified assertions.
    """

    model_config = _MODEL_CONFIG

    changed_files: tuple[FileChangeRecord, ...] = Field(alias="changedFiles")
    tests_run: tuple[TestRunRecord, ...] = Field(alias="testsRun")
    evidence_gaps: tuple[EvidenceGap, ...] = Field(alias="evidenceGaps")
    rollback_status: RollbackStatus = Field(alias="rollbackStatus")
    next_action: str | None = Field(alias="nextAction")

    default_off: Literal[True] = Field(default=True, alias="defaultOff")
    production_workspace_mutation_allowed: Literal[False] = Field(
        default=False,
        alias="productionWorkspaceMutationAllowed",
    )

    @field_validator("default_off", mode="before")
    @classmethod
    def _validate_default_off(cls, value: object) -> object:
        if value is not True:
            raise ValueError("defaultOff must remain true")
        return value

    @field_validator("production_workspace_mutation_allowed", mode="before")
    @classmethod
    def _validate_production_mutation(cls, value: object) -> object:
        if value is not False:
            raise ValueError("productionWorkspaceMutationAllowed must remain false")
        return value

    @field_validator("next_action")
    @classmethod
    def _validate_next_action(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if len(value) > 500:
            raise ValueError("nextAction must be at most 500 characters")
        if _contains_unsafe_public_text(value):
            raise ValueError("nextAction must not contain unsafe public text")
        return value

    @model_validator(mode="after")
    def _validate_projection_consistency(self) -> Self:
        # If there are evidence gaps, next_action should be set
        if self.evidence_gaps and self.next_action is None:
            raise ValueError(
                "nextAction is required when evidence gaps exist"
            )
        return self


class CodingFinalProjectionResult(BaseModel):
    """Result of evaluating a coding final projection."""

    model_config = _MODEL_CONFIG

    status: ProjectionStatus
    has_evidence_gaps: bool = Field(alias="hasEvidenceGaps")
    projection: CodingFinalProjection

    def public_projection(self) -> dict[str, object]:
        """Return a digest-safe, readable public projection.

        No raw file paths, contents, auth tokens. Only digests and
        evidence references.
        """
        return {
            "status": self.status,
            "changedFileCount": len(self.projection.changed_files),
            "changedFiles": [
                {
                    "fileDigest": record.file_digest,
                    "operation": record.operation,
                    "diffEvidenceRef": record.diff_evidence_ref,
                }
                for record in self.projection.changed_files
            ],
            "testRunCount": len(self.projection.tests_run),
            "testsRun": [
                {
                    "testSuiteRef": record.test_suite_ref,
                    "status": record.status,
                    "evidenceRef": record.evidence_ref,
                }
                for record in self.projection.tests_run
            ],
            "evidenceGapCount": len(self.projection.evidence_gaps),
            "evidenceGaps": [
                {
                    "gapType": gap.gap_type,
                    "description": gap.description,
                }
                for gap in self.projection.evidence_gaps
            ],
            "rollbackVerified": self.projection.rollback_status.verified,
            "nextAction": self.projection.next_action,
            "defaultOff": self.projection.default_off,
            "productionWorkspaceMutationAllowed": self.projection.production_workspace_mutation_allowed,
        }


def build_final_projection(
    projection: CodingFinalProjection,
) -> CodingFinalProjectionResult:
    """Evaluate a coding final projection and determine its status.

    Status is 'complete' only when:
    - No evidence gaps
    - All tests pass (or no tests required with no gap)
    - Rollback is verified
    """
    has_gaps = len(projection.evidence_gaps) > 0
    has_failed_tests = any(
        test.status == "failed" for test in projection.tests_run
    )
    rollback_unverified = not projection.rollback_status.verified

    is_complete = not has_gaps and not has_failed_tests and not rollback_unverified

    return CodingFinalProjectionResult.model_validate({
        "status": "complete" if is_complete else "incomplete",
        "hasEvidenceGaps": has_gaps,
        "projection": projection.model_dump(by_alias=True),
    })


def downgrade_unsupported_claims(text: str) -> str:
    """Downgrade or block unsupported completion claims in text.

    Scans for patterns like "all tests pass", "everything is done",
    "I fixed the bug", etc. and replaces them with a downgraded marker.
    """
    result = text
    for pattern in _UNSUPPORTED_CLAIM_PATTERNS:
        match = pattern.search(result)
        if match:
            matched_text = match.group(0)
            result = result.replace(matched_text, f"[unverified: {matched_text}]")
    return result


def _contains_unsafe_public_text(value: str) -> bool:
    return (
        _PRODUCTION_PATH_RE.search(value) is not None
        or _PRIVATE_PATH_RE.search(value) is not None
        or _AUTH_PUBLIC_TEXT_RE.search(value) is not None
        or _PRIVATE_PUBLIC_TEXT_RE.search(value) is not None
        or _SENSITIVE_ROUTE_TEXT_RE.search(value) is not None
    )


__all__ = [
    "CodingFinalProjection",
    "CodingFinalProjectionResult",
    "EvidenceGap",
    "FileChangeRecord",
    "RollbackStatus",
    "TestRunRecord",
    "build_final_projection",
    "downgrade_unsupported_claims",
]
