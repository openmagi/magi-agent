from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


CompletionMode = Literal["required", "best_effort", "not_applicable"]
EvidenceFreshnessVerdict = Literal["current", "stale", "unknown", "not_applicable"]
EvidenceSupportVerdict = Literal["supports", "weak", "contradicts", "unknown", "not_applicable"]
FreshnessPolicy = Literal["none", "max_age_days", "turn_clock_required"]
ResearchAcceptanceStatus = Literal["missing", "partial", "satisfied", "blocked"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)
_PUBLIC_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{1,180}$")
_EVIDENCE_TYPE_RE = re.compile(r"^[a-z][a-z0-9_.:-]{1,80}$")
_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_SECRET_TEXT_RE = re.compile(
    r"(?:Bearer\s+[A-Za-z0-9._~+/=-]{8,}|gh[opusr]_[A-Za-z0-9_]{8,}|"
    r"sk-(?:live|test)?[-_A-Za-z0-9]{8,}|\b\d{5,}:[A-Za-z0-9_-]{8,}\b|"
    r"[A-Z0-9_]*(?:SECRET|TOKEN|KEY|PASSWORD|COOKIE)[A-Z0-9_]*\s*[:=]\s*"
    r"[^,\s}{\n]{4,})",
    re.IGNORECASE,
)
_PRIVATE_PATH_RE = re.compile(
    r"(?:/Users/[^,\s\"']+|/workspace/[^,\s\"']+|/data/bots/[^,\s\"']+|"
    r"/var/lib/kubelet/[^,\s\"']+|pvc-[A-Za-z0-9-]+)",
    re.IGNORECASE,
)
_UNSAFE_TEXT_RE = re.compile(
    r"raw[_ -]?(?:source|transcript|tool|prompt|output|result|log)|"
    r"hidden[_ -]?reasoning|chain[_ -]?of[_ -]?thought|authorization|"
    r"model[_ -]?generated[_ -]?summary|model[_ -]?summary|"
    r"cookie|set-cookie|api[_ -]?key|secret|token",
    re.IGNORECASE,
)
_FORBIDDEN_EVIDENCE_TYPE_PARTS = frozenset(
    {
        "api",
        "auth",
        "cookie",
        "key",
        "log",
        "model",
        "output",
        "path",
        "private",
        "prompt",
        "raw",
        "result",
        "secret",
        "summary",
        "token",
        "tool",
        "transcript",
    }
)
_FORBIDDEN_EVIDENCE_TYPE_SUBSTRINGS = frozenset(
    {
        "apikey",
        "apitoken",
        "authcookie",
        "authtoken",
        "credential",
        "modelgeneratedsummary",
        "modelsummary",
        "privatepath",
        "privatelog",
        "rawoutput",
        "rawsource",
        "rawtool",
        "secret",
        "token",
        "toollog",
        "tooloutput",
    }
)


class _ResearchAcceptanceModel(BaseModel):
    model_config = _MODEL_CONFIG

    @classmethod
    def model_construct(cls, *args: object, **kwargs: object) -> Self:
        raise TypeError("model_construct is disabled for research acceptance contracts")

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        data = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            data.update(update)
        return type(self).model_validate(data)


class ResearchAcceptanceExecutionPosture(_ResearchAcceptanceModel):

    default_off: Literal[True] = Field(default=True, alias="defaultOff")
    local_only: Literal[True] = Field(default=True, alias="localOnly")
    fake_provider_only: Literal[True] = Field(default=True, alias="fakeProviderOnly")
    live_execution_allowed: Literal[False] = Field(default=False, alias="liveExecutionAllowed")
    provider_calls_allowed: Literal[False] = Field(default=False, alias="providerCallsAllowed")
    adk_runner_attached: Literal[False] = Field(default=False, alias="adkRunnerAttached")
    function_tool_attached: Literal[False] = Field(default=False, alias="functionToolAttached")


class ResearchSourceFreshnessPolicy(_ResearchAcceptanceModel):
    policy: FreshnessPolicy = "none"
    max_age_days: int | None = Field(default=None, alias="maxAgeDays", ge=0)
    requires_turn_clock: bool = Field(default=False, alias="requiresTurnClock")

    @model_validator(mode="after")
    def _validate_policy_shape(self) -> Self:
        if self.policy == "max_age_days" and self.max_age_days is None:
            raise ValueError("maxAgeDays is required for max_age_days freshness policy")
        if self.policy != "max_age_days" and self.max_age_days is not None:
            raise ValueError("maxAgeDays is only valid for max_age_days freshness policy")
        if self.policy == "turn_clock_required" and not self.requires_turn_clock:
            raise ValueError("turn_clock_required freshness policy requires requiresTurnClock=true")
        return self


class ResearchAcceptanceEvidenceRef(_ResearchAcceptanceModel):
    evidence_ref_id: str = Field(alias="evidenceRefId")
    evidence_type: str = Field(alias="evidenceType")
    support_verdict: EvidenceSupportVerdict = Field(alias="supportVerdict")
    freshness_verdict: EvidenceFreshnessVerdict = Field(alias="freshnessVerdict")
    digest: str
    span_refs: tuple[str, ...] = Field(default=(), alias="spanRefs")
    public_label: str | None = Field(default=None, alias="publicLabel")

    @field_validator("evidence_ref_id")
    @classmethod
    def _validate_evidence_ref_id(cls, value: str) -> str:
        return _public_ref(value, "evidenceRefId")

    @field_validator("evidence_type")
    @classmethod
    def _validate_evidence_type(cls, value: str) -> str:
        return _safe_evidence_type(value, "evidenceType")

    @field_validator("digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        if not _DIGEST_RE.fullmatch(value):
            raise ValueError("digest must be a sha256 hex digest")
        return value

    @field_validator("span_refs")
    @classmethod
    def _validate_span_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("spanRefs must not contain duplicate values")
        return tuple(_public_ref(item, "spanRef") for item in value)

    @field_validator("public_label")
    @classmethod
    def _validate_public_label(cls, value: str | None) -> str | None:
        if value is None:
            return None
        clean = value.strip()
        if not clean:
            return None
        _reject_unsafe_public_text(clean, "publicLabel")
        if len(clean) > 160:
            raise ValueError("publicLabel must be at most 160 characters")
        return clean

    def public_projection(self) -> dict[str, object]:
        data = self.model_dump(by_alias=True, mode="python", warnings=False)
        return {
            "evidenceRefId": data["evidenceRefId"],
            "evidenceType": data["evidenceType"],
            "supportVerdict": data["supportVerdict"],
            "freshnessVerdict": data["freshnessVerdict"],
            "digest": data["digest"],
            "spanRefs": data["spanRefs"],
            "publicLabel": data["publicLabel"],
        }


class ResearchAcceptanceCriterion(_ResearchAcceptanceModel):
    criteria_id: str = Field(alias="criteriaId")
    description: str
    required_evidence_types: tuple[str, ...] = Field(alias="requiredEvidenceTypes")
    optional_evidence_types: tuple[str, ...] = Field(default=(), alias="optionalEvidenceTypes")
    source_freshness_policy: ResearchSourceFreshnessPolicy = Field(
        default_factory=ResearchSourceFreshnessPolicy,
        alias="sourceFreshnessPolicy",
    )
    completion_mode: CompletionMode = Field(default="required", alias="completionMode")
    status: ResearchAcceptanceStatus | None = None
    evidence_refs: tuple[ResearchAcceptanceEvidenceRef, ...] = Field(
        default=(),
        alias="evidenceRefs",
    )

    @field_validator("criteria_id")
    @classmethod
    def _validate_criteria_id(cls, value: str) -> str:
        return _public_ref(value, "criteriaId")

    @field_validator("description")
    @classmethod
    def _validate_description(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("description must be non-empty")
        _reject_unsafe_public_text(clean, "description")
        if len(clean) > 500:
            raise ValueError("description must be at most 500 characters")
        return clean

    @field_validator("required_evidence_types", "optional_evidence_types")
    @classmethod
    def _validate_evidence_types(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("evidence type lists must not contain duplicate values")
        return tuple(_safe_evidence_type(item, "evidence type") for item in value)

    @model_validator(mode="after")
    def _derive_or_validate_status(self) -> Self:
        if self.completion_mode == "not_applicable" and (
            self.required_evidence_types
            or self.optional_evidence_types
            or self.evidence_refs
        ):
            raise ValueError(
                "not_applicable criteria cannot declare or attach evidence requirements"
            )
        if set(self.required_evidence_types) & set(self.optional_evidence_types):
            raise ValueError("required and optional evidence types must not overlap")
        declared_evidence_types = set(self.required_evidence_types) | set(
            self.optional_evidence_types
        )
        undeclared = {
            ref.evidence_type
            for ref in self.evidence_refs
            if ref.evidence_type not in declared_evidence_types
        }
        if undeclared:
            raise ValueError("evidenceRefs must use declared evidence types")
        derived = derive_research_acceptance_status(self)
        if self.status is not None and self.status != derived:
            raise ValueError("status must match deterministic evidence-derived status")
        object.__setattr__(self, "status", derived)
        return self

    def public_projection(self) -> dict[str, object]:
        return {
            "criteriaId": self.criteria_id,
            "description": self.description,
            "requiredEvidenceTypes": self.required_evidence_types,
            "optionalEvidenceTypes": self.optional_evidence_types,
            "sourceFreshnessPolicy": self.source_freshness_policy.model_dump(
                by_alias=True,
                mode="python",
                warnings=False,
            ),
            "completionMode": self.completion_mode,
            "status": self.status,
            "evidenceRefs": tuple(ref.public_projection() for ref in self.evidence_refs),
        }


class ResearchAcceptanceCriteriaSet(_ResearchAcceptanceModel):
    criteria_set_id: str = Field(alias="criteriaSetId")
    target_label: str = Field(alias="targetLabel")
    criteria: tuple[ResearchAcceptanceCriterion, ...]
    execution_posture: ResearchAcceptanceExecutionPosture = Field(
        default_factory=ResearchAcceptanceExecutionPosture,
        alias="executionPosture",
    )
    adk_usage_notes: str | None = Field(default=None, alias="adkUsageNotes")

    @field_validator("criteria_set_id")
    @classmethod
    def _validate_criteria_set_id(cls, value: str) -> str:
        return _public_ref(value, "criteriaSetId")

    @field_validator("target_label")
    @classmethod
    def _validate_target_label(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("targetLabel must be non-empty")
        _reject_unsafe_public_text(clean, "targetLabel")
        if len(clean) > 240:
            raise ValueError("targetLabel must be at most 240 characters")
        return clean

    @field_validator("adk_usage_notes")
    @classmethod
    def _validate_adk_usage_notes(cls, value: str | None) -> str | None:
        if value is None:
            return None
        clean = value.strip()
        if not clean:
            return None
        _reject_unsafe_public_text(clean, "public text")
        if len(clean) > 240:
            raise ValueError("public text field must be at most 240 characters")
        return clean

    @field_validator("criteria")
    @classmethod
    def _validate_criteria(
        cls,
        value: tuple[ResearchAcceptanceCriterion, ...],
    ) -> tuple[ResearchAcceptanceCriterion, ...]:
        if not value:
            raise ValueError("criteria must be non-empty")
        ids = [criterion.criteria_id for criterion in value]
        if len(set(ids)) != len(ids):
            raise ValueError("criteria must not contain duplicate criteriaId values")
        return value

    def public_projection(self) -> dict[str, object]:
        return {
            "criteriaSetId": self.criteria_set_id,
            "targetLabel": self.target_label,
            "executionPosture": self.execution_posture.model_dump(
                by_alias=True,
                mode="python",
                warnings=False,
            ),
            "adkUsageNotes": self.adk_usage_notes,
            "criteria": tuple(criterion.public_projection() for criterion in self.criteria),
        }


def derive_research_acceptance_status(
    criterion: ResearchAcceptanceCriterion,
) -> ResearchAcceptanceStatus:
    if criterion.completion_mode == "not_applicable":
        return "satisfied"

    required = set(criterion.required_evidence_types)
    if not required:
        return "satisfied" if criterion.completion_mode == "best_effort" else "missing"

    usable_required = {
        ref.evidence_type
        for ref in criterion.evidence_refs
        if ref.evidence_type in required
        and ref.support_verdict == "supports"
        and _freshness_satisfies_policy(ref, criterion)
    }
    weak_or_stale_required = any(
        ref.evidence_type in required
        and (
            ref.support_verdict in {"weak", "unknown"}
            or not _freshness_satisfies_policy(ref, criterion)
        )
        for ref in criterion.evidence_refs
    )
    contradictory_evidence = any(
        ref.support_verdict == "contradicts"
        for ref in criterion.evidence_refs
    )

    if contradictory_evidence:
        return "blocked"
    if required <= usable_required:
        return "satisfied"
    if usable_required or weak_or_stale_required or criterion.evidence_refs:
        return "partial"
    return "partial" if criterion.completion_mode == "best_effort" else "missing"


def project_research_acceptance_criteria_set(
    criteria_set: ResearchAcceptanceCriteriaSet,
) -> dict[str, object]:
    validated = ResearchAcceptanceCriteriaSet.model_validate(
        criteria_set.model_dump(by_alias=True, mode="python", warnings=False)
    )
    return validated.public_projection()


def pricing_acceptance_criteria(target_label: str) -> ResearchAcceptanceCriteriaSet:
    return ResearchAcceptanceCriteriaSet(
        criteriaSetId="research-acceptance-pricing",
        targetLabel=f"{target_label} pricing",
        adkUsageNotes="Metadata only; no ADK Runner or FunctionTool is attached.",
        criteria=(
            ResearchAcceptanceCriterion(
                criteriaId="pricing.current_price_points",
                description="Current price points require inspected pricing source evidence.",
                requiredEvidenceTypes=("source_inspection", "pricing_page"),
                optionalEvidenceTypes=("archive_snapshot",),
                sourceFreshnessPolicy={"policy": "max_age_days", "maxAgeDays": 30},
            ),
            ResearchAcceptanceCriterion(
                criteriaId="pricing.billing_terms",
                description="Billing terms require inspected source evidence from a public policy or pricing page.",
                requiredEvidenceTypes=("source_inspection", "billing_terms"),
                optionalEvidenceTypes=("terms_page",),
                sourceFreshnessPolicy={"policy": "max_age_days", "maxAgeDays": 90},
                completionMode="best_effort",
            ),
            ResearchAcceptanceCriterion(
                criteriaId="pricing.source_date",
                description="Pricing claims require a temporal anchor for source freshness.",
                requiredEvidenceTypes=("clock",),
                optionalEvidenceTypes=("source_date",),
                sourceFreshnessPolicy={"policy": "turn_clock_required", "requiresTurnClock": True},
            ),
        ),
    )


def positioning_acceptance_criteria(target_label: str) -> ResearchAcceptanceCriteriaSet:
    return ResearchAcceptanceCriteriaSet(
        criteriaSetId="research-acceptance-positioning",
        targetLabel=f"{target_label} positioning",
        adkUsageNotes="Metadata only; no ADK Runner or FunctionTool is attached.",
        criteria=(
            ResearchAcceptanceCriterion(
                criteriaId="positioning.official_description",
                description="Positioning requires inspected official source evidence.",
                requiredEvidenceTypes=("source_inspection", "official_source"),
                optionalEvidenceTypes=("about_page",),
                sourceFreshnessPolicy={"policy": "max_age_days", "maxAgeDays": 180},
            ),
            ResearchAcceptanceCriterion(
                criteriaId="positioning.competitor_context",
                description="Competitor context is best effort and requires explicit inspected source metadata when present.",
                requiredEvidenceTypes=("source_inspection",),
                optionalEvidenceTypes=("competitor_page",),
                sourceFreshnessPolicy={"policy": "max_age_days", "maxAgeDays": 180},
                completionMode="best_effort",
            ),
        ),
    )


def recent_events_acceptance_criteria(target_label: str) -> ResearchAcceptanceCriteriaSet:
    return ResearchAcceptanceCriteriaSet(
        criteriaSetId="research-acceptance-recent-events",
        targetLabel=f"{target_label} recent events",
        adkUsageNotes="Metadata only; no ADK Runner or FunctionTool is attached.",
        criteria=(
            ResearchAcceptanceCriterion(
                criteriaId="recent_events.temporal_anchor",
                description="Recent event research requires a turn-local clock evidence reference.",
                requiredEvidenceTypes=("clock",),
                optionalEvidenceTypes=(),
                sourceFreshnessPolicy={"policy": "turn_clock_required", "requiresTurnClock": True},
            ),
            ResearchAcceptanceCriterion(
                criteriaId="recent_events.event_source",
                description="Recent event claims require inspected source evidence within the freshness window.",
                requiredEvidenceTypes=("source_inspection",),
                optionalEvidenceTypes=("web_search",),
                sourceFreshnessPolicy={"policy": "max_age_days", "maxAgeDays": 14},
            ),
            ResearchAcceptanceCriterion(
                criteriaId="recent_events.corroboration",
                description="Corroboration should use a separate public source when available.",
                requiredEvidenceTypes=("source_inspection",),
                optionalEvidenceTypes=("secondary_source",),
                sourceFreshnessPolicy={"policy": "max_age_days", "maxAgeDays": 30},
                completionMode="best_effort",
            ),
        ),
    )


def _public_ref(value: str, field_name: str) -> str:
    clean = value.strip()
    if not clean:
        raise ValueError(f"{field_name} must be non-empty")
    _reject_unsafe_public_text(clean, field_name)
    if not _PUBLIC_REF_RE.fullmatch(clean):
        raise ValueError(f"{field_name} must be a digest-safe public id")
    return clean


def _safe_evidence_type(value: str, field_name: str) -> str:
    clean = value.strip()
    if not _EVIDENCE_TYPE_RE.fullmatch(clean):
        raise ValueError(f"{field_name} must be a digest-safe lower-case public id")
    parts = {part for part in re.split(r"[_.:-]+", clean) if part}
    normalized = re.sub(r"[^a-z0-9]", "", clean)
    if parts & _FORBIDDEN_EVIDENCE_TYPE_PARTS or any(
        fragment in normalized for fragment in _FORBIDDEN_EVIDENCE_TYPE_SUBSTRINGS
    ):
        raise ValueError(
            f"{field_name} must not reference raw, model, tool, private, auth, token, or secret data"
        )
    return clean


def _freshness_satisfies_policy(
    ref: ResearchAcceptanceEvidenceRef,
    criterion: ResearchAcceptanceCriterion,
) -> bool:
    if ref.freshness_verdict == "current":
        return True
    return (
        ref.freshness_verdict == "not_applicable"
        and criterion.source_freshness_policy.policy == "none"
    )


def _reject_unsafe_public_text(value: str, field_name: str) -> None:
    if _PRIVATE_PATH_RE.search(value):
        raise ValueError(f"{field_name} must not contain private paths")
    if _SECRET_TEXT_RE.search(value) or _UNSAFE_TEXT_RE.search(value):
        raise ValueError(f"{field_name} must not contain raw, private, auth, token, or secret data")


__all__ = [
    "CompletionMode",
    "EvidenceFreshnessVerdict",
    "EvidenceSupportVerdict",
    "FreshnessPolicy",
    "ResearchAcceptanceCriteriaSet",
    "ResearchAcceptanceCriterion",
    "ResearchAcceptanceEvidenceRef",
    "ResearchAcceptanceExecutionPosture",
    "ResearchAcceptanceStatus",
    "ResearchSourceFreshnessPolicy",
    "derive_research_acceptance_status",
    "pricing_acceptance_criteria",
    "positioning_acceptance_criteria",
    "project_research_acceptance_criteria_set",
    "recent_events_acceptance_criteria",
]
