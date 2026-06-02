from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


ProjectionMode = Literal[
    "raw_text_allowed",
    "structured_claims_then_render",
    "artifact_only",
    "abstain",
    "block",
]
ClaimSupportStatus = Literal["supported", "weak", "unverifiable", "contradicted", "failed"]
ClaimType = Literal["factual_claim", "numeric_claim", "policy_claim", "artifact_summary", "other"]
ProjectionStatus = Literal["projected", "blocked", "abstain"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_SAFE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,180}$")
_JWT_LIKE_RE = re.compile(
    r"(?:^|[^A-Za-z0-9_-])[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\."
    r"[A-Za-z0-9_-]{10,}(?:$|[^A-Za-z0-9_-])"
)
_PRIVATE_TEXT_RE = re.compile(
    r"(?:"
    r"authorization\s*:|set-cookie\s*:|\bcookie\b|\bbearer\s+[A-Za-z0-9._~+/=-]{6,}|"
    r"\bsid=[A-Za-z0-9._-]+|\bsk-[A-Za-z0-9._-]{6,}|gh[opusr]_[A-Za-z0-9_]{6,}|"
    r"github_pat_[A-Za-z0-9_]+|xox[a-z]-[A-Za-z0-9._-]+|AKIA[0-9A-Z]{8,}|"
    r"AIza[A-Za-z0-9_-]+|api[_-]?key\s*[:=]|password\s*[:=]|secret\s*[:=]|"
    r"token\s*[:=]|private[_-]?key|"
    r"/Users(?:/|\b)|/home(?:/|\b)|/workspace(?:/|\b)|/data/bots(?:/|\b)|"
    r"/var/lib/kubelet(?:/|\b)|pvc-[A-Za-z0-9-]+|"
    r"raw[_ -]?(?:tool|child|prompt|transcript|output|result|log|args)|"
    r"hidden[_ -]?reasoning|chain[_ -]?of[_ -]?thought"
    r")",
    re.IGNORECASE,
)


class GovernedClaim(BaseModel):
    model_config = _MODEL_CONFIG

    claim_id: str = Field(alias="claimId")
    text: str
    claim_type: ClaimType = Field(alias="claimType")
    support_status: ClaimSupportStatus = Field(alias="supportStatus")
    citation_refs: tuple[str, ...] = Field(default=(), alias="citationRefs")
    calculation_refs: tuple[str, ...] = Field(default=(), alias="calculationRefs")

    @field_validator("claim_id")
    @classmethod
    def _validate_claim_id(cls, value: str) -> str:
        return _safe_ref(value, field_name="claimId")

    @field_validator("text")
    @classmethod
    def _validate_text(cls, value: str) -> str:
        text = value.strip()
        if not text or _contains_private_text(text):
            raise ValueError("claim text must be non-empty and public-safe")
        return text

    @field_validator("citation_refs", "calculation_refs")
    @classmethod
    def _validate_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_safe_ref(ref, field_name="evidenceRef") for ref in value)


class GovernedArtifact(BaseModel):
    model_config = _MODEL_CONFIG

    artifact_ref: str = Field(alias="artifactRef")
    summary: str
    support_status: ClaimSupportStatus = Field(default="supported", alias="supportStatus")

    @field_validator("artifact_ref")
    @classmethod
    def _validate_artifact_ref(cls, value: str) -> str:
        return _safe_ref(value, field_name="artifactRef")

    @field_validator("summary")
    @classmethod
    def _validate_summary(cls, value: str) -> str:
        text = value.strip()
        if not text or _contains_private_text(text):
            raise ValueError("artifact summary must be non-empty and public-safe")
        return text


class GovernedDraft(BaseModel):
    model_config = _MODEL_CONFIG

    request_id: str = Field(alias="requestId")
    raw_draft: str = Field(default="", alias="rawDraft")
    claims: tuple[GovernedClaim, ...] = Field(default=(), alias="claims")
    artifacts: tuple[GovernedArtifact, ...] = Field(default=(), alias="artifacts")

    @field_validator("request_id")
    @classmethod
    def _validate_request_id(cls, value: str) -> str:
        return _safe_ref(value, field_name="requestId")


class ProjectionPolicy(BaseModel):
    model_config = _MODEL_CONFIG

    policy_id: str = Field(alias="policyId")
    mode: ProjectionMode
    governed: bool = True

    @field_validator("policy_id")
    @classmethod
    def _validate_policy_id(cls, value: str) -> str:
        return _safe_ref(value, field_name="policyId")


class ProjectionDecision(BaseModel):
    model_config = _MODEL_CONFIG

    status: ProjectionStatus
    policy_id: str = Field(alias="policyId")
    request_id: str = Field(alias="requestId")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    user_visible_text: str | None = Field(default=None, alias="userVisibleText")
    claim_refs: tuple[str, ...] = Field(default=(), alias="claimRefs")
    artifact_refs: tuple[str, ...] = Field(default=(), alias="artifactRefs")

    @field_validator("policy_id", "request_id")
    @classmethod
    def _validate_decision_ref(cls, value: str) -> str:
        return _safe_ref(value, field_name="decisionRef")

    @field_validator("user_visible_text")
    @classmethod
    def _validate_user_visible_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        if _PRIVATE_TEXT_RE.search(text) or _JWT_LIKE_RE.search(text):
            raise ValueError("user-visible projection text must be public-safe")
        return text

    @field_validator("claim_refs", "artifact_refs")
    @classmethod
    def _validate_decision_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_safe_ref(ref, field_name="projectionRef") for ref in value)

    def public_projection(self) -> dict[str, object]:
        reason_codes = self.reason_codes
        user_visible_text = self.user_visible_text
        if user_visible_text is not None and _contains_private_text(user_visible_text):
            user_visible_text = None
            reason_codes = (*reason_codes, "projection_public_text_redacted")
        payload: dict[str, object] = {
            "status": _safe_public_status(self.status),
            "policyId": _safe_public_ref(self.policy_id),
            "requestId": _safe_public_ref(self.request_id),
            "reasonCodes": tuple(_safe_public_ref(code) for code in reason_codes),
            "userVisibleText": user_visible_text,
            "claimRefs": tuple(_safe_public_ref(ref) for ref in self.claim_refs),
            "artifactRefs": tuple(_safe_public_ref(ref) for ref in self.artifact_refs),
        }
        return {
            key: list(value) if isinstance(value, tuple) else value
            for key, value in payload.items()
            if value not in ((), {}, None)
        }


class ProjectionRenderer:
    def __init__(self, policy: ProjectionPolicy | Mapping[str, object]) -> None:
        self.policy = (
            policy if isinstance(policy, ProjectionPolicy) else ProjectionPolicy.model_validate(policy)
        )

    def project(self, draft: GovernedDraft | Mapping[str, object]) -> ProjectionDecision:
        parsed = draft if isinstance(draft, GovernedDraft) else GovernedDraft.model_validate(draft)
        if self.policy.mode == "block":
            return self._decision(parsed, "blocked", ("projection_policy_blocked",))
        if self.policy.mode == "abstain":
            return self._decision(parsed, "abstain", ("projection_policy_abstain",))
        if self.policy.mode == "artifact_only":
            return self._project_artifacts(parsed)
        if self.policy.mode == "raw_text_allowed":
            return self._project_raw_text(parsed)
        return self._project_structured_claims(parsed)

    def _project_structured_claims(self, draft: GovernedDraft) -> ProjectionDecision:
        if self.policy.governed and not draft.claims and draft.raw_draft.strip():
            return self._decision(
                draft,
                "blocked",
                ("governed_raw_draft_projection_forbidden",),
            )
        if not draft.claims:
            return self._decision(draft, "abstain", ("structured_claims_missing",))

        rendered: list[str] = []
        claim_refs: list[str] = []
        for claim in draft.claims:
            if claim.support_status != "supported":
                return self._decision(
                    draft,
                    "blocked",
                    ("claim_support_not_sufficient",),
                    claim_refs=(claim.claim_id,),
                )
            refs = claim.citation_refs + claim.calculation_refs
            if claim.claim_type == "numeric_claim" and not refs:
                return self._decision(
                    draft,
                    "blocked",
                    ("numeric_claim_missing_evidence_ref",),
                    claim_refs=(claim.claim_id,),
                )
            claim_refs.append(claim.claim_id)
            rendered.append(f"- {claim.text}{_format_refs(refs)}")
        return self._decision(
            draft,
            "projected",
            ("structured_claims_projected",),
            user_visible_text="\n".join(rendered),
            claim_refs=tuple(claim_refs),
        )

    def _project_artifacts(self, draft: GovernedDraft) -> ProjectionDecision:
        if not draft.artifacts:
            return self._decision(draft, "abstain", ("artifact_ref_missing",))
        for artifact in draft.artifacts:
            if artifact.support_status != "supported":
                return self._decision(
                    draft,
                    "blocked",
                    ("artifact_support_not_sufficient",),
                    artifact_refs=(artifact.artifact_ref,),
                )
        artifact_refs = tuple(artifact.artifact_ref for artifact in draft.artifacts)
        rendered = "\n".join(
            f"- {artifact.summary} [{artifact.artifact_ref}]" for artifact in draft.artifacts
        )
        return self._decision(
            draft,
            "projected",
            ("artifact_projection_ready",),
            user_visible_text=rendered,
            artifact_refs=artifact_refs,
        )

    def _project_raw_text(self, draft: GovernedDraft) -> ProjectionDecision:
        if self.policy.governed:
            return self._decision(draft, "blocked", ("governed_raw_text_mode_forbidden",))
        text = draft.raw_draft.strip()
        if not text:
            return self._decision(draft, "abstain", ("raw_text_missing",))
        if _contains_private_text(text):
            return self._decision(draft, "blocked", ("raw_text_private_payload_blocked",))
        return self._decision(
            draft,
            "projected",
            ("raw_text_projected",),
            user_visible_text=text,
        )

    def _decision(
        self,
        draft: GovernedDraft,
        status: ProjectionStatus,
        reason_codes: tuple[str, ...],
        *,
        user_visible_text: str | None = None,
        claim_refs: tuple[str, ...] = (),
        artifact_refs: tuple[str, ...] = (),
    ) -> ProjectionDecision:
        return ProjectionDecision(
            status=status,
            policyId=self.policy.policy_id,
            requestId=draft.request_id,
            reasonCodes=reason_codes,
            userVisibleText=user_visible_text,
            claimRefs=claim_refs,
            artifactRefs=artifact_refs,
        )


def _safe_ref(value: str, *, field_name: str) -> str:
    text = value.strip()
    if _contains_private_text(text) or _SAFE_REF_RE.fullmatch(text) is None:
        raise ValueError(f"{field_name} must be a sanitized public ref")
    return text


def _contains_private_text(value: str) -> bool:
    return bool(_PRIVATE_TEXT_RE.search(value) or _JWT_LIKE_RE.search(value))


def _safe_public_ref(value: str) -> str:
    try:
        return _safe_ref(value, field_name="publicRef")
    except ValueError:
        return "redacted_ref"


def _safe_public_status(value: str) -> ProjectionStatus:
    return value if value in {"projected", "blocked", "abstain"} else "blocked"


def _format_refs(refs: tuple[str, ...]) -> str:
    if not refs:
        return ""
    return "".join(f" [{ref}]" for ref in refs)


__all__ = [
    "ClaimSupportStatus",
    "ClaimType",
    "GovernedArtifact",
    "GovernedClaim",
    "GovernedDraft",
    "ProjectionDecision",
    "ProjectionMode",
    "ProjectionPolicy",
    "ProjectionRenderer",
    "ProjectionStatus",
]
