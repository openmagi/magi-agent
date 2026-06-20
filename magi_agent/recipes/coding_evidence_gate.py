from __future__ import annotations

from collections.abc import Mapping
import hashlib
import re
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator

from magi_agent.evidence.coding_verification import (
    CodingVerificationAuditRequest,
    evaluate_coding_verification_audit,
)
from magi_agent.evidence.types import EvidenceRecord
from magi_agent.ops.authority import FalseOnlyAuthorityModel


CodingEvidenceGateStatus = Literal[
    "disabled",
    "not_applicable",
    "passed",
    "audit_required",
    "repair_required",
    "blocked_local",
]
CodingEvidenceGateEnforcement = Literal["audit", "local_block"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_PUBLIC_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:!-]{0,180}$")
_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_RECEIPT_REF_RE = re.compile(r"^coding-evidence-gate-receipt:[a-f0-9]{24}$")
_PRIVATE_TEXT_RE = re.compile(
    r"(?:/Users/|/home/|/workspace/|/data/bots/|/var/lib/|authorization|"
    r"cookie|token|secret|session[_-]?key|password|credential|private[_-]?key|"
    r"bearer\s+[A-Za-z0-9._~+/=-]{6,}|sk[-_][A-Za-z0-9._-]{6,}|"
    r"gh[opusr]_[A-Za-z0-9_]{6,}|github_pat_[A-Za-z0-9_]+|"
    r"xox[a-z]-[A-Za-z0-9._-]+|AKIA[0-9A-Z]{8,}|AIza[A-Za-z0-9_-]+)",
    re.IGNORECASE,
)


class CodingEvidenceGateConfig(FalseOnlyAuthorityModel):
    # C-4 PR-G3: re-parented onto FalseOnlyAuthorityModel. The kernel owns the
    # frozen/extra-forbid/validate-default config and force-falses the
    # ``Literal[False]`` field on every construction surface (closes a
    # pre-existing ``model_construct`` leak on ``productionBlockEnabled``).
    enabled: bool = False
    local_evaluation_enabled: bool = Field(default=False, alias="localEvaluationEnabled")
    enforcement: CodingEvidenceGateEnforcement = "audit"
    production_block_enabled: Literal[False] = Field(
        default=False,
        alias="productionBlockEnabled",
    )


class CodingEvidenceGateAuthorityFlags(FalseOnlyAuthorityModel):
    # C-4 PR-G3: re-parented onto FalseOnlyAuthorityModel. The kernel's
    # introspection-based ``_force_false`` validator + ``_ser`` serializer +
    # ``model_construct`` route-through-validate + ``model_copy`` route-through-
    # validate replace the hand-pasted ``model_construct`` / ``model_copy``
    # overrides and the 6-field ``@field_serializer`` that previously
    # force-falses the authority flags. The ``_false_authority_overrides()``
    # helper is gone -- the kernel handles every Literal[False] field uniformly.
    local_evaluation_only: bool = Field(default=False, alias="localEvaluationOnly")
    local_claim_blocked: bool = Field(default=False, alias="localClaimBlocked")
    final_answer_blocked: Literal[False] = Field(default=False, alias="finalAnswerBlocked")
    user_visible_output_allowed: Literal[False] = Field(
        default=False,
        alias="userVisibleOutputAllowed",
    )
    traffic_attached: Literal[False] = Field(default=False, alias="trafficAttached")
    runner_attached: Literal[False] = Field(default=False, alias="runnerAttached")
    live_tool_attached: Literal[False] = Field(default=False, alias="liveToolAttached")
    production_write_allowed: Literal[False] = Field(
        default=False,
        alias="productionWriteAllowed",
    )


class CodingEvidenceGateRequest(BaseModel):
    model_config = _MODEL_CONFIG

    evidence_records: tuple[EvidenceRecord, ...] = Field(alias="evidenceRecords")
    completion_claimed: bool = Field(alias="completionClaimed")
    claim_text: str | None = Field(default=None, repr=False, alias="claimText")
    claim_ref: str = Field(default="claim:coding", alias="claimRef")
    last_code_mutation_at: int | float = Field(alias="lastCodeMutationAt")
    require_commit_checkpoint: bool = Field(default=False, alias="requireCommitCheckpoint")

    @field_validator("evidence_records")
    @classmethod
    def _revalidate_records(cls, value: tuple[EvidenceRecord, ...]) -> tuple[EvidenceRecord, ...]:
        return tuple(
            EvidenceRecord.model_validate(
                record.model_dump(by_alias=False, mode="python", warnings=False)
            )
            if isinstance(record, EvidenceRecord)
            else EvidenceRecord.model_validate(record)
            for record in value
        )

    @field_validator("completion_claimed", "require_commit_checkpoint", mode="before")
    @classmethod
    def _validate_bool(cls, value: object) -> object:
        if not isinstance(value, bool):
            raise ValueError("coding evidence gate booleans must be strict booleans")
        return value

    @field_validator("claim_text")
    @classmethod
    def _bound_claim_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip():
            raise ValueError("claimText must be non-empty when provided")
        return value[:4000]

    @field_validator("claim_ref")
    @classmethod
    def _validate_claim_ref(cls, value: str) -> str:
        return _safe_ref(value)


class CodingEvidenceGateDecision(BaseModel):
    model_config = _MODEL_CONFIG

    status: CodingEvidenceGateStatus
    reason_codes: tuple[str, ...] = Field(alias="reasonCodes")
    claim_ref: str = Field(alias="claimRef")
    claim_digest: str = Field(alias="claimDigest")
    receipt_ref: str = Field(alias="receiptRef")
    verdict_state: str | None = Field(default=None, alias="verdictState")
    verifier_status: str | None = Field(default=None, alias="verifierStatus")
    required_evidence_types: tuple[str, ...] = Field(alias="requiredEvidenceTypes")
    matched_evidence_types: tuple[str, ...] = Field(alias="matchedEvidenceTypes")
    missing_evidence_types: tuple[str, ...] = Field(alias="missingEvidenceTypes")
    failure_codes: tuple[str, ...] = Field(alias="failureCodes")
    authority_flags: CodingEvidenceGateAuthorityFlags = Field(alias="authorityFlags")

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set
        values["authorityFlags"] = _coerce_authority_flags(values.get("authorityFlags"))
        return cls.model_validate(values)

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        data = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            data.update(dict(update))
        data["authorityFlags"] = _coerce_authority_flags(data.get("authorityFlags"))
        return type(self).model_validate(data)

    def public_projection(
        self,
        *,
        schema_version: Literal["v1", "v2"] = "v2",
    ) -> dict[str, object]:
        base_projection: dict[str, object] = {
            "status": self.status,
            "reasonCodes": list(self.reason_codes),
            "claimRef": _public_ref(self.claim_ref),
            "claimDigest": _public_digest(self.claim_digest),
            "receiptRef": _public_receipt_ref(self.receipt_ref),
            "verdictState": self.verdict_state,
            "verifierStatus": self.verifier_status,
            "authorityFlags": self.authority_flags.model_dump(by_alias=True),
        }
        if schema_version == "v1":
            return {
                **base_projection,
                "requiredEvidenceTypes": list(self.required_evidence_types),
                "matchedEvidenceTypes": list(self.matched_evidence_types),
                "missingEvidenceTypes": list(self.missing_evidence_types),
                "failureCodes": list(self.failure_codes),
            }
        return {
            **base_projection,
            "requiredEvidenceTypeCount": len(self.required_evidence_types),
            "matchedEvidenceTypeCount": len(self.matched_evidence_types),
            "missingEvidenceTypeCount": len(self.missing_evidence_types),
            "failureCount": len(self.failure_codes),
        }


class CodingEvidenceGateMaterialization(BaseModel):
    model_config = _MODEL_CONFIG

    recipe_id: str = Field(default="openmagi.dev-coding.evidence-gate", alias="recipeId")
    verifier_id: str = Field(
        default="dev-coding-verification-audit",
        alias="verifierId",
    )
    validator_callback_refs: tuple[str, ...] = Field(
        default=(
            "validator:dev-coding-verification-audit",
            "validator:completion-evidence-local",
        ),
        alias="validatorCallbackRefs",
    )
    required_evidence_types: tuple[str, ...] = Field(
        default=("GitDiff", "TestRun"),
        alias="requiredEvidenceTypes",
    )
    optional_evidence_types: tuple[str, ...] = Field(
        default=("CommitCheckpoint",),
        alias="optionalEvidenceTypes",
    )
    attachment_flags: Mapping[str, Literal[False]] = Field(alias="attachmentFlags")

    def public_projection(self) -> dict[str, object]:
        return {
            "recipeId": self.recipe_id,
            "verifierId": self.verifier_id,
            "validatorCallbackRefs": list(self.validator_callback_refs),
            "requiredEvidenceTypes": list(self.required_evidence_types),
            "optionalEvidenceTypes": list(self.optional_evidence_types),
            "attachmentFlags": dict(_FALSE_ATTACHMENT_FLAGS),
        }


class CodingEvidenceGate:
    """Coding-owned completion-claim evidence gate over already-recorded evidence."""

    def __init__(self, config: CodingEvidenceGateConfig | None = None) -> None:
        self.config = config or CodingEvidenceGateConfig()

    def evaluate(
        self,
        request: CodingEvidenceGateRequest | Mapping[str, object],
    ) -> CodingEvidenceGateDecision:
        parsed = (
            request
            if isinstance(request, CodingEvidenceGateRequest)
            else CodingEvidenceGateRequest.model_validate(request)
        )
        claim_digest = _claim_digest(parsed.claim_text)
        if not self.config.enabled or not self.config.local_evaluation_enabled:
            return _decision(
                "disabled",
                ("coding_evidence_gate_disabled",),
                parsed,
                claim_digest,
                flags=CodingEvidenceGateAuthorityFlags(),
            )
        flags = CodingEvidenceGateAuthorityFlags(localEvaluationOnly=True)
        if not parsed.completion_claimed:
            return _decision(
                "not_applicable",
                ("no_completion_claim",),
                parsed,
                claim_digest,
                flags=flags,
            )

        audit_result = evaluate_coding_verification_audit(
            CodingVerificationAuditRequest(
                evidenceRecords=parsed.evidence_records,
                lastCodeMutationAt=parsed.last_code_mutation_at,
                requireCommitCheckpoint=parsed.require_commit_checkpoint,
            )
        )
        verdict = audit_result.verdict
        required = verdict.requirement_coverage
        matched = tuple(record.type for record in verdict.matched_evidence)
        missing = tuple(requirement.type for requirement in verdict.missing_requirements)
        failures = tuple(failure.code for failure in verdict.failures)
        if verdict.ok:
            return _decision(
                "passed",
                ("coding_evidence_gate_passed",),
                parsed,
                claim_digest,
                flags=flags,
                verdict_state=verdict.state,
                verifier_status=audit_result.verifier_result.status,
                required=required,
                matched=matched,
            )

        missing_only = bool(missing) and all(
            failure == "EVIDENCE_CONTRACT_MISSING" for failure in failures
        )
        reason = "coding_evidence_missing" if missing_only else "coding_evidence_failed"
        status: CodingEvidenceGateStatus
        local_blocked = False
        if self.config.enforcement == "local_block":
            status = "blocked_local"
            local_blocked = True
        elif missing_only:
            status = "audit_required"
        else:
            status = "repair_required"
        return _decision(
            status,
            (reason,),
            parsed,
            claim_digest,
            flags=flags.model_copy(update={"localClaimBlocked": local_blocked}),
            verdict_state=verdict.state,
            verifier_status=audit_result.verifier_result.status,
            required=required,
            matched=matched,
            missing=missing,
            failures=failures,
        )


class CodingEvidenceGateHarnessBinding:
    """Recipe-owned final-claim binding; no core completion criteria are encoded."""

    def __init__(self, config: CodingEvidenceGateConfig | None = None) -> None:
        self.config = config or CodingEvidenceGateConfig()

    def materialize(self) -> CodingEvidenceGateMaterialization:
        return CodingEvidenceGateMaterialization(
            attachmentFlags=dict(_FALSE_ATTACHMENT_FLAGS),
        )

    def evaluate_completion_claim(
        self,
        request: CodingEvidenceGateRequest | Mapping[str, object],
    ) -> CodingEvidenceGateDecision:
        return CodingEvidenceGate(self.config).evaluate(request)


def _decision(
    status: CodingEvidenceGateStatus,
    reason_codes: tuple[str, ...],
    request: CodingEvidenceGateRequest,
    claim_digest: str,
    *,
    flags: CodingEvidenceGateAuthorityFlags,
    verdict_state: str | None = None,
    verifier_status: str | None = None,
    required: tuple[str, ...] = (),
    matched: tuple[str, ...] = (),
    missing: tuple[str, ...] = (),
    failures: tuple[str, ...] = (),
) -> CodingEvidenceGateDecision:
    receipt_ref = _receipt_ref(
        status=status,
        reason_codes=reason_codes,
        claim_ref=request.claim_ref,
        claim_digest=claim_digest,
        required=required,
        matched=matched,
        missing=missing,
        failures=failures,
    )
    return CodingEvidenceGateDecision(
        status=status,
        reasonCodes=reason_codes,
        claimRef=request.claim_ref,
        claimDigest=claim_digest,
        receiptRef=receipt_ref,
        verdictState=verdict_state,
        verifierStatus=verifier_status,
        requiredEvidenceTypes=required,
        matchedEvidenceTypes=matched,
        missingEvidenceTypes=missing,
        failureCodes=failures,
        authorityFlags=flags,
    )


def _receipt_ref(
    *,
    status: str,
    reason_codes: tuple[str, ...],
    claim_ref: str,
    claim_digest: str,
    required: tuple[str, ...],
    matched: tuple[str, ...],
    missing: tuple[str, ...],
    failures: tuple[str, ...],
) -> str:
    seed = "|".join(
        (
            status,
            ",".join(reason_codes),
            claim_ref,
            claim_digest,
            ",".join(required),
            ",".join(matched),
            ",".join(missing),
            ",".join(failures),
        )
    )
    return "coding-evidence-gate-receipt:" + hashlib.sha256(
        seed.encode("utf-8")
    ).hexdigest()[:24]


def _claim_digest(value: str | None) -> str:
    payload = "" if value is None else value
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _safe_ref(value: str) -> str:
    text = value.strip()
    if not text or _PRIVATE_TEXT_RE.search(text) or _PUBLIC_REF_RE.fullmatch(text) is None:
        raise ValueError("claimRef must be a sanitized public reference")
    return text


def _public_ref(value: str) -> str:
    try:
        return _safe_ref(value)
    except ValueError:
        return "redacted_ref"


def _public_digest(value: str) -> str:
    text = value.strip()
    if _DIGEST_RE.fullmatch(text) and _PRIVATE_TEXT_RE.search(text) is None:
        return text
    return "sha256:" + ("0" * 64)


def _public_receipt_ref(value: str) -> str:
    text = value.strip()
    if _RECEIPT_REF_RE.fullmatch(text) and _PRIVATE_TEXT_RE.search(text) is None:
        return text
    return "redacted_ref"


# C-4 PR-G3: ``_false_authority_overrides()`` helper has been dropped. The
# kernel ``FalseOnlyAuthorityModel`` base on ``CodingEvidenceGateAuthorityFlags``
# now force-falses every ``Literal[False]`` field during ``model_validate`` /
# ``model_construct`` / ``model_copy``, so callers no longer have to spread a
# hand-listed dict at construction sites.


def _coerce_authority_flags(value: object) -> CodingEvidenceGateAuthorityFlags:
    if isinstance(value, CodingEvidenceGateAuthorityFlags):
        return value.model_copy()
    if isinstance(value, Mapping):
        return CodingEvidenceGateAuthorityFlags.model_validate(dict(value))
    return CodingEvidenceGateAuthorityFlags()


# Module-level constant inlined in place of the dropped helper. Used as the
# ``attachmentFlags`` payload on the two recipe materialization classes (whose
# ``attachment_flags`` field is ``Mapping[str, Literal[False]]``, NOT a
# force-false pydantic model).
_FALSE_ATTACHMENT_FLAGS: dict[str, bool] = {
    "finalAnswerBlocked": False,
    "userVisibleOutputAllowed": False,
    "trafficAttached": False,
    "runnerAttached": False,
    "liveToolAttached": False,
    "productionWriteAllowed": False,
}


__all__ = [
    "CodingEvidenceGate",
    "CodingEvidenceGateAuthorityFlags",
    "CodingEvidenceGateConfig",
    "CodingEvidenceGateDecision",
    "CodingEvidenceGateHarnessBinding",
    "CodingEvidenceGateMaterialization",
    "CodingEvidenceGateEnforcement",
    "CodingEvidenceGateRequest",
    "CodingEvidenceGateStatus",
]
