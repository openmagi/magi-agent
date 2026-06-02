from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


DecisionStatus = Literal["passed", "repair_required", "blocked", "skipped"]


_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9])\$?-?\d[\d,]*(?:\.\d+)?%?")
_PRIVATE_REF_RE = re.compile(
    r"(?:"
    r"/Users/|/home/|/workspace/|/data/bots/|/var/lib/kubelet/|"
    r"Bearer\s+|github_pat_|gh[opusr]_|xox[a-z]-|AKIA|AIza|sk-|"
    r"authorization|cookie|secret|token|password|credential|api[_-]?key|private[_-]?key"
    r")",
    re.IGNORECASE,
)
_HIGH_RISK_DOMAINS = frozenset({"accounting", "finance", "tax", "legal"})
_DETERMINISTIC_TYPES = frozenset(
    {
        "Calculation",
        "SpreadsheetValidation",
        "SpreadsheetDiff",
        "SQLQueryResult",
        "TestRun",
        "ToolResult",
    }
)


class CalculationAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    final_answer_allowed: bool = Field(default=False, alias="finalAnswerAllowed")
    user_visible_output_allowed: bool = Field(default=False, alias="userVisibleOutputAllowed")
    deterministic_evidence_verified: bool = Field(
        default=False,
        alias="deterministicEvidenceVerified",
    )


class NumericClaimRequest(BaseModel):
    model_config = _MODEL_CONFIG

    domain: str
    output_text: str = Field(alias="outputText")
    evidence_records: tuple[Mapping[str, object], ...] = Field(
        default=(),
        alias="evidenceRecords",
    )


class CalculationEvidenceDecision(BaseModel):
    model_config = _MODEL_CONFIG

    status: DecisionStatus
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    evidence_refs: tuple[str, ...] = Field(default=(), alias="evidenceRefs")
    authority_flags: CalculationAuthorityFlags = Field(
        default_factory=CalculationAuthorityFlags,
        alias="authorityFlags",
    )

    @property
    def final_answer_allowed(self) -> bool:
        return self.authority_flags.final_answer_allowed

    def public_projection(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reasonCodes": list(self.reason_codes),
            "evidenceRefs": list(self.evidence_refs),
            "authorityFlags": self.authority_flags.model_dump(by_alias=True),
        }


class CalculationEvidencePolicy:
    def __init__(self, *, enabled: bool = False) -> None:
        self.enabled = enabled

    def evaluate(self, request: NumericClaimRequest) -> CalculationEvidenceDecision:
        numbers = _numbers(request.output_text)
        if not self.enabled:
            return CalculationEvidenceDecision(
                status="skipped",
                reasonCodes=("calculation_policy_disabled",),
            )
        if not numbers:
            return CalculationEvidenceDecision(
                status="passed",
                reasonCodes=("no_numeric_claim_detected",),
                authorityFlags=CalculationAuthorityFlags(
                    finalAnswerAllowed=False,
                    userVisibleOutputAllowed=False,
                    deterministicEvidenceVerified=False,
                ),
            )

        deterministic = [
            record
            for record in request.evidence_records
            if _is_deterministic_evidence(record)
        ]
        reason_codes: list[str] = []
        if any(str(record.get("type")) == "ModelReasoning" for record in request.evidence_records):
            reason_codes.append("model_explanation_not_calculation_evidence")
        if request.domain.casefold() in _HIGH_RISK_DOMAINS and not deterministic:
            reason_codes.append("high_risk_numeric_claim_requires_deterministic_evidence")
        if not deterministic:
            reason_codes.append("numeric_claim_missing_calculation_evidence")
            return _decision("repair_required", reason_codes, ())

        evidence_refs = tuple(
            str(record.get("evidenceRef"))
            for record in deterministic
            if _public_ref(str(record.get("evidenceRef", "")))
        )
        observed_numbers = {
            _normalize_number(number)
            for record in deterministic
            for number in _observed_numbers(record)
        }
        normalized_claims = {_normalize_number(number) for number in numbers}
        if not observed_numbers:
            return _decision(
                "repair_required",
                ("numeric_claim_missing_observed_result_binding",),
                evidence_refs,
            )
        if observed_numbers and not normalized_claims.issubset(observed_numbers):
            return _decision(
                "blocked",
                ("numeric_claim_mismatch",),
                evidence_refs,
            )
        return CalculationEvidenceDecision(
            status="passed",
            reasonCodes=("deterministic_calculation_evidence_present",),
            evidenceRefs=evidence_refs,
            authorityFlags=CalculationAuthorityFlags(
                finalAnswerAllowed=False,
                userVisibleOutputAllowed=False,
                deterministicEvidenceVerified=True,
            ),
        )


def _decision(
    status: DecisionStatus,
    reason_codes: tuple[str, ...] | list[str],
    evidence_refs: tuple[str, ...],
) -> CalculationEvidenceDecision:
    return CalculationEvidenceDecision(
        status=status,
        reasonCodes=tuple(sorted(dict.fromkeys(reason_codes))),
        evidenceRefs=evidence_refs,
        authorityFlags=CalculationAuthorityFlags(
            finalAnswerAllowed=False,
            userVisibleOutputAllowed=False,
            deterministicEvidenceVerified=False,
        ),
    )


def _numbers(text: str) -> tuple[str, ...]:
    return tuple(match.group(0).strip("$") for match in _NUMBER_RE.finditer(text))


def _normalize_number(value: str) -> str:
    return value.strip().strip("$").replace(",", "").rstrip("%")


def _is_deterministic_evidence(record: Mapping[str, object]) -> bool:
    evidence_type = str(record.get("type", ""))
    if evidence_type not in _DETERMINISTIC_TYPES:
        return False
    if evidence_type == "SQLQueryResult":
        return _is_digest(record.get("queryDigest")) and _is_digest(record.get("resultDigest"))
    if evidence_type in {"SpreadsheetValidation", "SpreadsheetDiff"}:
        return (
            _is_digest(record.get("formulaDigest"))
            or _is_digest(record.get("diffDigest"))
        ) and _is_digest(record.get("recalcDigest"))
    if evidence_type == "ToolResult":
        return bool(record.get("deterministicCalculation")) and _is_digest(record.get("resultDigest"))
    return _is_digest(record.get("resultDigest"))


def _observed_numbers(record: Mapping[str, object]) -> tuple[str, ...]:
    values = record.get("observedNumbers")
    if isinstance(values, str):
        return (values,)
    if isinstance(values, tuple | list):
        return tuple(str(value) for value in values)
    return ()


def _is_digest(value: object) -> bool:
    return isinstance(value, str) and _DIGEST_RE.fullmatch(value) is not None


def _public_ref(value: str) -> bool:
    return (
        _PRIVATE_REF_RE.search(value) is None
        and re.fullmatch(r"^[A-Za-z][A-Za-z0-9_.:-]{1,180}$", value) is not None
    )


__all__ = [
    "CalculationAuthorityFlags",
    "CalculationEvidenceDecision",
    "CalculationEvidencePolicy",
    "NumericClaimRequest",
]
