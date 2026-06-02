from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any, Literal, Self
from weakref import finalize

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_validator

from magi_agent.evidence.runtime_issuance import (
    RuntimeIssueAuthority,
    require_runtime_issue_authority,
)


ResearchClaimKind = Literal[
    "factual",
    "numeric",
    "comparative",
    "temporal",
    "inference",
    "opinion",
]
ResearchClaimSupportVerdict = Literal[
    "supported",
    "weak",
    "unsupported",
    "contradicted",
    "stale",
    "not_evaluated",
]
ResearchClaimProjectionMode = Literal["fact", "qualified", "omitted", "needs_repair"]
ResearchClaimFreshnessVerdict = Literal["current", "stale", "not_checked", "not_applicable"]
ResearchClaimRelevanceVerdict = Literal["relevant", "irrelevant", "unknown"]
ResearchClaimStaleSupportPolicy = Literal["block", "downgrade"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="never",
    hide_input_in_errors=True,
)
_PUBLIC_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{1,180}$")
_SOURCE_REF_RE = re.compile(r"^src_[1-9][0-9]*$")
_EVIDENCE_KIND_RE = re.compile(r"^[a-z][a-z0-9_.:-]{1,80}$")
_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_SECRET_TEXT_RE = re.compile(
    r"(?:Bearer\s+[A-Za-z0-9._~+/=-]{8,}|gh[opusr]_[A-Za-z0-9_]{8,}|"
    r"github_pat_[A-Za-z0-9_]{8,}|AKIA[0-9A-Z]{12,}|ASIA[0-9A-Z]{12,}|"
    r"AIza[0-9A-Za-z_-]{12,}|xox[baprs]-[A-Za-z0-9-]{8,}|"
    r"sk-(?:live|test)?[-_A-Za-z0-9]{8,}|\b\d{5,}:[A-Za-z0-9_-]{8,}\b|"
    r"[A-Z0-9_]*(?:SECRET|TOKEN|KEY|PASSWORD|COOKIE)[A-Z0-9_]*\s*[:=]\s*"
    r"[^,\s}{\n]{4,})",
    re.IGNORECASE,
)
_PRIVATE_PATH_RE = re.compile(
    r"(?:/Users/[^,\s\"']+|/home/[^,\s\"']+|/root/[^,\s\"']+|"
    r"/workspace/[^,\s\"']+|/data/bots/[^,\s\"']+|"
    r"/var/lib/kubelet/[^,\s\"']+|pvc-[A-Za-z0-9-]+)",
    re.IGNORECASE,
)
_UNSAFE_TEXT_RE = re.compile(
    r"https?://|file://|raw[_ -]?(?:source|transcript|tool|prompt|output|result|log)|"
    r"source[_ -]?(?:body|content|html|text)|hidden[_ -]?reasoning|"
    r"chain[_ -]?of[_ -]?thought|authorization|cookie|set-cookie|"
    r"api[_ -]?key|secret|token|model[_ -]?summary|model[_ -]?generated[_ -]?summary",
    re.IGNORECASE,
)
_FORBIDDEN_EVIDENCE_KIND_PARTS = frozenset(
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
_FORBIDDEN_EVIDENCE_KIND_SUBSTRINGS = frozenset(
    {
        "apikey",
        "apitoken",
        "authcookie",
        "authtoken",
        "credential",
        "modelgeneratedsummary",
        "modelsummary",
        "privatepath",
        "rawoutput",
        "rawsource",
        "rawtool",
        "secret",
        "token",
        "tooloutput",
    }
)
_FORBIDDEN_RAW_KEY_TOKENS = frozenset(
    {
        "authorization",
        "cookie",
        "html",
        "modelgeneratedsummary",
        "modelsummary",
        "privatepath",
        "prompt",
        "rawoutput",
        "rawprompt",
        "rawsource",
        "rawsourcetext",
        "rawtool",
        "secret",
        "sourcebody",
        "sourcecontent",
        "sourcehtml",
        "sourcetext",
        "token",
        "tooloutput",
        "url",
        "uri",
    }
)
_ADK_USAGE_NOTES = (
    "Metadata only; no ADK Runner or FunctionTool is attached; "
    "ArtifactService and Evaluation are planned for fixture handles and checks."
)
_CLAIM_SUPPORT_OBJECT_IDS: set[int] = set()
_CLAIM_SUPPORT_FINGERPRINTS: dict[int, object] = {}
_CLAIM_SUPPORT_FINALIZERS: dict[int, object] = {}


class _ResearchClaimModel(BaseModel):
    model_config = _MODEL_CONFIG

    @classmethod
    def model_construct(cls, *args: object, **kwargs: object) -> Self:
        raise TypeError("model_construct is disabled for research claim graph contracts")

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        data = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            data.update(dict(update))
        return type(self).model_validate(data)


class ResearchClaimExecutionPosture(_ResearchClaimModel):
    default_off: Literal[True] = Field(default=True, alias="defaultOff")
    local_only: Literal[True] = Field(default=True, alias="localOnly")
    fake_provider_only: Literal[True] = Field(default=True, alias="fakeProviderOnly")
    live_execution_allowed: Literal[False] = Field(default=False, alias="liveExecutionAllowed")
    provider_calls_allowed: Literal[False] = Field(default=False, alias="providerCallsAllowed")
    browser_execution_allowed: Literal[False] = Field(
        default=False,
        alias="browserExecutionAllowed",
    )
    tool_execution_allowed: Literal[False] = Field(default=False, alias="toolExecutionAllowed")
    adk_runner_attached: Literal[False] = Field(default=False, alias="adkRunnerAttached")
    function_tool_attached: Literal[False] = Field(default=False, alias="functionToolAttached")


class ResearchClaimSupportRef(_ResearchClaimModel):
    _issued_by_claim_support_verifier: bool = PrivateAttr(default=False)

    support_ref_id: str = Field(alias="supportRefId")
    source_ref_id: str = Field(alias="sourceRefId")
    span_refs: tuple[str, ...] = Field(alias="spanRefs")
    source_digest: str = Field(alias="sourceDigest")
    evidence_digest: str = Field(alias="evidenceDigest")
    evidence_kind: str = Field(alias="evidenceKind")
    support_verdict: ResearchClaimSupportVerdict = Field(alias="supportVerdict")
    freshness_verdict: ResearchClaimFreshnessVerdict = Field(alias="freshnessVerdict")
    relevance_verdict: ResearchClaimRelevanceVerdict = Field(alias="relevanceVerdict")
    claim_text_digest: str | None = Field(default=None, alias="claimTextDigest")
    claim_value_digest: str | None = Field(default=None, alias="claimValueDigest")
    observed_value_digest: str | None = Field(default=None, alias="observedValueDigest")
    single_source_policy_digest: str | None = Field(
        default=None,
        alias="singleSourcePolicyDigest",
    )
    stale_support_policy: ResearchClaimStaleSupportPolicy = Field(
        default="block",
        alias="staleSupportPolicy",
    )
    public_label: str | None = Field(default=None, alias="publicLabel")

    @classmethod
    def issue_verified_support_ref(
        cls,
        *,
        runtime_authority: RuntimeIssueAuthority | None = None,
        support_ref_id: str,
        source_ref_id: str,
        span_refs: Iterable[str],
        source_digest: str,
        evidence_digest: str,
        evidence_kind: str,
        support_verdict: ResearchClaimSupportVerdict,
        freshness_verdict: ResearchClaimFreshnessVerdict,
        relevance_verdict: ResearchClaimRelevanceVerdict,
        claim_text_digest: str | None = None,
        claim_value_digest: str | None = None,
        observed_value_digest: str | None = None,
        single_source_policy_digest: str | None = None,
        stale_support_policy: ResearchClaimStaleSupportPolicy = "block",
        public_label: str | None = None,
    ) -> Self:
        require_runtime_issue_authority(
            runtime_authority,
            scope="research_claim_support",
        )
        support_ref = cls(
            supportRefId=support_ref_id,
            sourceRefId=source_ref_id,
            spanRefs=tuple(span_refs),
            sourceDigest=source_digest,
            evidenceDigest=evidence_digest,
            evidenceKind=evidence_kind,
            supportVerdict=support_verdict,
            freshnessVerdict=freshness_verdict,
            relevanceVerdict=relevance_verdict,
            claimTextDigest=claim_text_digest,
            claimValueDigest=claim_value_digest,
            observedValueDigest=observed_value_digest,
            singleSourcePolicyDigest=single_source_policy_digest,
            staleSupportPolicy=stale_support_policy,
            publicLabel=public_label,
        )
        return _mark_claim_support_ref_issued(support_ref)

    @property
    def is_claim_support_verifier_issued(self) -> bool:
        return self._issued_by_claim_support_verifier

    @model_validator(mode="before")
    @classmethod
    def _validate_raw_support_ref(cls, value: object) -> object:
        if isinstance(value, Mapping):
            _reject_unsafe_raw_value(value)
        return value

    @field_validator("support_ref_id")
    @classmethod
    def _validate_support_ref_id(cls, value: str) -> str:
        return _public_ref(value, "supportRefId")

    @field_validator("source_ref_id")
    @classmethod
    def _validate_source_ref_id(cls, value: str) -> str:
        clean = value.strip()
        _reject_unsafe_public_text(clean, "sourceRefId")
        if not _SOURCE_REF_RE.fullmatch(clean):
            raise ValueError("sourceRefId must be a digest-safe source ref")
        return clean

    @field_validator("span_refs")
    @classmethod
    def _validate_span_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("spanRefs must be non-empty")
        if len(set(value)) != len(value):
            raise ValueError("spanRefs must not contain duplicates")
        return tuple(_public_ref(item, "spanRef") for item in value)

    @field_validator(
        "source_digest",
        "evidence_digest",
        "claim_text_digest",
        "claim_value_digest",
        "observed_value_digest",
        "single_source_policy_digest",
    )
    @classmethod
    def _validate_optional_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _DIGEST_RE.fullmatch(value):
            raise ValueError("digest fields must be sha256 hex digests")
        return value

    @field_validator("evidence_kind")
    @classmethod
    def _validate_evidence_kind(cls, value: str) -> str:
        return _safe_evidence_kind(value, "evidenceKind")

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

    @model_validator(mode="after")
    def _validate_support_ref_shape(self) -> Self:
        if (self.claim_value_digest is None) != (self.observed_value_digest is None):
            raise ValueError("claimValueDigest and observedValueDigest must be paired")
        if self.single_source_policy_digest is not None and self.support_verdict != "supported":
            raise ValueError("single-source comparative policy only applies to supported refs")
        return self

    def public_projection(self) -> dict[str, object]:
        validated = _validate_support_ref_object(self)
        return {
            "supportRefId": validated.support_ref_id,
            "sourceRefId": validated.source_ref_id,
            "spanRefs": validated.span_refs,
            "sourceDigest": validated.source_digest,
            "evidenceDigest": validated.evidence_digest,
            "evidenceKind": validated.evidence_kind,
            "supportVerdict": validated.support_verdict,
            "freshnessVerdict": validated.freshness_verdict,
            "relevanceVerdict": validated.relevance_verdict,
            "claimTextDigest": validated.claim_text_digest,
            "claimValueDigest": validated.claim_value_digest,
            "observedValueDigest": validated.observed_value_digest,
            "singleSourcePolicyDigest": validated.single_source_policy_digest,
            "staleSupportPolicy": validated.stale_support_policy,
        }


class ResearchClaimNode(_ResearchClaimModel):
    claim_id: str = Field(alias="claimId")
    claim_text_digest: str = Field(alias="claimTextDigest")
    claim_preview: str | None = Field(default=None, alias="claimPreview")
    claim_kind: ResearchClaimKind = Field(alias="claimKind")
    support_refs: tuple[ResearchClaimSupportRef, ...] = Field(alias="supportRefs")
    support_verdict: ResearchClaimSupportVerdict = Field(alias="supportVerdict")
    projection_mode: ResearchClaimProjectionMode = Field(alias="projectionMode")

    @model_validator(mode="before")
    @classmethod
    def _validate_raw_claim_node(cls, value: object) -> object:
        if isinstance(value, Mapping):
            _reject_unsafe_raw_value(value)
        return value

    @field_validator("claim_id")
    @classmethod
    def _validate_claim_id(cls, value: str) -> str:
        return _public_ref(value, "claimId")

    @field_validator("claim_text_digest")
    @classmethod
    def _validate_claim_text_digest(cls, value: str) -> str:
        if not _DIGEST_RE.fullmatch(value):
            raise ValueError("claimTextDigest must be a sha256 hex digest")
        return value

    @field_validator("claim_preview")
    @classmethod
    def _validate_claim_preview(cls, value: str | None) -> str | None:
        if value is None:
            return None
        clean = value.strip()
        if not clean:
            return None
        _reject_unsafe_public_text(clean, "claimPreview")
        if len(clean) > 240:
            raise ValueError("claimPreview must be at most 240 characters")
        return clean

    @field_validator("support_refs")
    @classmethod
    def _validate_support_refs(
        cls,
        value: tuple[ResearchClaimSupportRef, ...],
    ) -> tuple[ResearchClaimSupportRef, ...]:
        validated_refs = tuple(_validate_support_ref_object(item) for item in value)
        ref_ids = [item.support_ref_id for item in value]
        if len(set(ref_ids)) != len(ref_ids):
            raise ValueError("supportRefs must not contain duplicate supportRefId values")
        return validated_refs

    @model_validator(mode="after")
    def _validate_derived_fields(self) -> Self:
        for support_ref in self.support_refs:
            if (
                support_ref.claim_text_digest is not None
                and support_ref.claim_text_digest != self.claim_text_digest
            ):
                raise ValueError("support ref claimTextDigest must match claimTextDigest")
        derived_verdict = derive_research_claim_support_verdict(
            self.claim_kind,
            self.support_refs,
        )
        derived_projection = derive_research_claim_projection_mode(
            self.claim_kind,
            derived_verdict,
        )
        if self.support_verdict != derived_verdict:
            raise ValueError("supportVerdict must match deterministic support metadata")
        if self.projection_mode != derived_projection:
            raise ValueError("projectionMode must match deterministic support verdict")
        return self

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        data = self.model_dump(by_alias=True, mode="python", warnings=False)
        data["supportRefs"] = self.support_refs
        if update:
            data.update(dict(update))
        data["supportVerdict"] = derive_research_claim_support_verdict(
            data["claimKind"],
            tuple(_validate_support_ref_object(item) for item in data.get("supportRefs", ())),
        )
        data["projectionMode"] = derive_research_claim_projection_mode(
            data["claimKind"],
            data["supportVerdict"],
        )
        return type(self).model_validate(data)

    def public_projection(self) -> dict[str, object]:
        validated = _validate_claim_node_object(self)
        render_as_fact = (
            validated.projection_mode == "fact"
            and validated.support_verdict == "supported"
        )
        public_preview = (
            validated.claim_preview
            if validated.projection_mode in {"fact", "qualified"}
            else None
        )
        return {
            "claimId": validated.claim_id,
            "claimTextDigest": validated.claim_text_digest,
            "claimPreview": public_preview,
            "claimKind": validated.claim_kind,
            "supportRefs": tuple(
                ref.public_projection() for ref in validated.support_refs
            ),
            "supportVerdict": validated.support_verdict,
            "projectionMode": validated.projection_mode,
            "renderAsFact": render_as_fact,
        }


class ResearchClaimGraph(_ResearchClaimModel):
    claim_graph_id: str = Field(alias="claimGraphId")
    claims: tuple[ResearchClaimNode, ...]
    execution_posture: ResearchClaimExecutionPosture = Field(
        default_factory=ResearchClaimExecutionPosture,
        alias="executionPosture",
    )
    adk_usage_notes: str = Field(default=_ADK_USAGE_NOTES, alias="adkUsageNotes")

    @field_validator("claim_graph_id")
    @classmethod
    def _validate_claim_graph_id(cls, value: str) -> str:
        return _public_ref(value, "claimGraphId")

    @field_validator("adk_usage_notes")
    @classmethod
    def _validate_adk_usage_notes(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("adkUsageNotes must be non-empty")
        _reject_unsafe_public_text(clean, "adkUsageNotes")
        if len(clean) > 240:
            raise ValueError("adkUsageNotes must be at most 240 characters")
        return clean

    @field_validator("claims")
    @classmethod
    def _validate_claims(
        cls,
        value: tuple[ResearchClaimNode, ...],
    ) -> tuple[ResearchClaimNode, ...]:
        claim_ids = [item.claim_id for item in value]
        if len(set(claim_ids)) != len(claim_ids):
            raise ValueError("claims must not contain duplicate claimId values")
        return value

    def public_projection(self) -> dict[str, object]:
        validated = _validate_claim_graph_object(self)
        return {
            "claimGraphId": validated.claim_graph_id,
            "executionPosture": validated.execution_posture.model_dump(
                by_alias=True,
                mode="python",
                warnings=False,
            ),
            "adkUsageNotes": validated.adk_usage_notes,
            "claims": tuple(claim.public_projection() for claim in validated.claims),
        }


def build_research_claim_node(
    *,
    claim_id: str,
    claim_text_digest: str,
    claim_kind: ResearchClaimKind,
    support_refs: Iterable[ResearchClaimSupportRef],
    claim_preview: str | None = None,
) -> ResearchClaimNode:
    validated_support_refs = tuple(_validate_support_ref_object(item) for item in support_refs)
    support_verdict = derive_research_claim_support_verdict(
        claim_kind,
        validated_support_refs,
    )
    return ResearchClaimNode(
        claimId=claim_id,
        claimTextDigest=claim_text_digest,
        claimPreview=claim_preview,
        claimKind=claim_kind,
        supportRefs=validated_support_refs,
        supportVerdict=support_verdict,
        projectionMode=derive_research_claim_projection_mode(claim_kind, support_verdict),
    )


def derive_research_claim_support_verdict(
    claim_kind: ResearchClaimKind,
    support_refs: Iterable[ResearchClaimSupportRef],
) -> ResearchClaimSupportVerdict:
    refs = tuple(_validate_support_ref_object(item) for item in support_refs)
    if not refs:
        return "not_evaluated"

    relevant_refs = tuple(ref for ref in refs if ref.relevance_verdict == "relevant")
    if not relevant_refs:
        return "unsupported"

    if any(ref.support_verdict == "contradicted" for ref in relevant_refs):
        return "contradicted"

    if any(
        ref.claim_value_digest is not None
        and ref.observed_value_digest is not None
        and ref.claim_value_digest != ref.observed_value_digest
        for ref in relevant_refs
    ):
        return "contradicted"

    current_supported_refs = tuple(
        ref
        for ref in relevant_refs
        if ref.support_verdict == "supported" and ref.freshness_verdict == "current"
    )
    if current_supported_refs:
        if claim_kind == "comparative":
            source_ids = {ref.source_ref_id for ref in current_supported_refs}
            documented_single_source_policy = any(
                ref.single_source_policy_digest is not None for ref in current_supported_refs
            )
            if len(source_ids) < 2 and not documented_single_source_policy:
                return "weak"
        return "supported"

    if any(
        ref.support_verdict == "weak" and ref.freshness_verdict == "current"
        for ref in relevant_refs
    ):
        return "weak"

    stale_refs = tuple(
        ref
        for ref in relevant_refs
        if ref.freshness_verdict == "stale"
        and ref.support_verdict in {"supported", "weak"}
    )
    if stale_refs:
        if all(ref.stale_support_policy == "downgrade" for ref in stale_refs):
            return "weak"
        return "stale"

    if any(ref.support_verdict == "not_evaluated" for ref in relevant_refs):
        return "not_evaluated"
    return "unsupported"


def derive_research_claim_projection_mode(
    claim_kind: ResearchClaimKind,
    support_verdict: ResearchClaimSupportVerdict,
) -> ResearchClaimProjectionMode:
    if support_verdict == "supported":
        if claim_kind in {"inference", "opinion"}:
            return "qualified"
        return "fact"
    if support_verdict == "weak":
        return "qualified"
    if support_verdict in {"contradicted", "stale"}:
        return "omitted"
    return "needs_repair"


def project_research_claim_graph(
    graph: ResearchClaimGraph,
) -> dict[str, object]:
    return _validate_claim_graph_object(graph).public_projection()


def _mark_claim_support_ref_issued(ref: ResearchClaimSupportRef) -> ResearchClaimSupportRef:
    ref._issued_by_claim_support_verifier = True
    object_id = id(ref)
    _CLAIM_SUPPORT_OBJECT_IDS.add(object_id)
    _CLAIM_SUPPORT_FINGERPRINTS[object_id] = _model_fingerprint(ref)
    _CLAIM_SUPPORT_FINALIZERS[object_id] = finalize(
        ref,
        _discard_claim_support_ref_object_id,
        object_id,
    )
    return ref


def _discard_claim_support_ref_object_id(object_id: int) -> None:
    _CLAIM_SUPPORT_OBJECT_IDS.discard(object_id)
    _CLAIM_SUPPORT_FINGERPRINTS.pop(object_id, None)
    _CLAIM_SUPPORT_FINALIZERS.pop(object_id, None)


def _validate_support_ref_object(value: object) -> ResearchClaimSupportRef:
    if not isinstance(value, ResearchClaimSupportRef):
        raise TypeError("claim graph supportRefs must be verifier-issued support ref objects")
    if not value.is_claim_support_verifier_issued or id(value) not in _CLAIM_SUPPORT_OBJECT_IDS:
        raise ValueError("support ref must be issued by the claim support verifier")
    issued_fingerprint = _CLAIM_SUPPORT_FINGERPRINTS.get(id(value))
    if issued_fingerprint != _model_fingerprint(value):
        raise ValueError("support ref was modified after claim support verifier issuance")
    ResearchClaimSupportRef.model_validate(
        value.model_dump(by_alias=True, mode="python", warnings=False)
    )
    return value


def _validate_claim_node_object(value: object) -> ResearchClaimNode:
    if not isinstance(value, ResearchClaimNode):
        raise TypeError("claim graph claims must be research claim node objects")
    _public_ref(value.claim_id, "claimId")
    if not _DIGEST_RE.fullmatch(value.claim_text_digest):
        raise ValueError("claimTextDigest must be a sha256 hex digest")
    if value.claim_kind not in ResearchClaimKind.__args__:
        raise ValueError("claimKind must be a supported research claim kind")
    if value.claim_preview is not None:
        clean_preview = value.claim_preview.strip()
        if clean_preview:
            _reject_unsafe_public_text(clean_preview, "claimPreview")
            if len(clean_preview) > 240:
                raise ValueError("claimPreview must be at most 240 characters")
    support_refs = tuple(_validate_support_ref_object(ref) for ref in value.support_refs)
    ref_ids = [item.support_ref_id for item in support_refs]
    if len(set(ref_ids)) != len(ref_ids):
        raise ValueError("supportRefs must not contain duplicate supportRefId values")
    derived_verdict = derive_research_claim_support_verdict(
        value.claim_kind,
        support_refs,
    )
    derived_projection = derive_research_claim_projection_mode(
        value.claim_kind,
        derived_verdict,
    )
    if value.support_verdict != derived_verdict:
        raise ValueError("supportVerdict must match deterministic support metadata")
    if value.projection_mode != derived_projection:
        raise ValueError("projectionMode must match deterministic support verdict")
    return value


def _validate_claim_graph_object(value: object) -> ResearchClaimGraph:
    if not isinstance(value, ResearchClaimGraph):
        raise TypeError("claim graph projection requires a research claim graph object")
    _public_ref(value.claim_graph_id, "claimGraphId")
    clean_notes = value.adk_usage_notes.strip()
    if not clean_notes:
        raise ValueError("adkUsageNotes must be non-empty")
    _reject_unsafe_public_text(clean_notes, "adkUsageNotes")
    if len(clean_notes) > 240:
        raise ValueError("adkUsageNotes must be at most 240 characters")
    ResearchClaimExecutionPosture.model_validate(
        value.execution_posture.model_dump(by_alias=True, mode="python", warnings=False)
    )
    claims = tuple(_validate_claim_node_object(claim) for claim in value.claims)
    claim_ids = [item.claim_id for item in claims]
    if len(set(claim_ids)) != len(claim_ids):
        raise ValueError("claims must not contain duplicate claimId values")
    return value


def _model_fingerprint(model: BaseModel) -> object:
    return _freeze_for_fingerprint(
        model.model_dump(by_alias=True, mode="python", warnings=False)
    )


def _freeze_for_fingerprint(value: object) -> object:
    if isinstance(value, Mapping):
        return tuple(
            (key, _freeze_for_fingerprint(nested))
            for key, nested in sorted(value.items(), key=lambda item: str(item[0]))
        )
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(_freeze_for_fingerprint(nested) for nested in value)
    return value


def _public_ref(value: str, field_name: str) -> str:
    clean = value.strip()
    if not clean:
        raise ValueError(f"{field_name} must be non-empty")
    _reject_unsafe_public_text(clean, field_name)
    if not _PUBLIC_REF_RE.fullmatch(clean):
        raise ValueError(f"{field_name} must be a digest-safe public id")
    return clean


def _safe_evidence_kind(value: str, field_name: str) -> str:
    clean = value.strip()
    if not _EVIDENCE_KIND_RE.fullmatch(clean):
        raise ValueError(f"{field_name} must be a digest-safe lower-case public id")
    parts = {part for part in re.split(r"[_.:-]+", clean) if part}
    normalized = re.sub(r"[^a-z0-9]", "", clean)
    if parts & _FORBIDDEN_EVIDENCE_KIND_PARTS or any(
        fragment in normalized for fragment in _FORBIDDEN_EVIDENCE_KIND_SUBSTRINGS
    ):
        raise ValueError(
            f"{field_name} must not reference raw, model, tool, private, auth, token, or secret data"
        )
    return clean


def _reject_unsafe_public_text(value: str, field_name: str) -> None:
    if _PRIVATE_PATH_RE.search(value):
        raise ValueError(f"{field_name} must not contain private paths")
    if _SECRET_TEXT_RE.search(value) or _UNSAFE_TEXT_RE.search(value):
        raise ValueError(
            f"{field_name} must not contain raw, private, auth, token, or secret data"
        )


def _reject_unsafe_raw_value(value: object) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized_key = re.sub(r"[^a-z0-9]", "", str(key).lower())
            if normalized_key in _FORBIDDEN_RAW_KEY_TOKENS:
                raise ValueError("claim graph metadata must not contain raw/private fields")
            _reject_unsafe_raw_value(nested)
        return
    if isinstance(value, str):
        _reject_unsafe_public_text(value, "claim graph metadata")
        return
    if isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray)):
        for nested in value:
            _reject_unsafe_raw_value(nested)


__all__ = [
    "ResearchClaimExecutionPosture",
    "ResearchClaimFreshnessVerdict",
    "ResearchClaimGraph",
    "ResearchClaimKind",
    "ResearchClaimNode",
    "ResearchClaimProjectionMode",
    "ResearchClaimRelevanceVerdict",
    "ResearchClaimStaleSupportPolicy",
    "ResearchClaimSupportRef",
    "ResearchClaimSupportVerdict",
    "build_research_claim_node",
    "derive_research_claim_projection_mode",
    "derive_research_claim_support_verdict",
    "project_research_claim_graph",
]
