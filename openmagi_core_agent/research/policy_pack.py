from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Mapping
from hashlib import sha256
from typing import Any, Literal, Self, get_args
from weakref import finalize

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from openmagi_core_agent.research.acceptance_criteria import (
    positioning_acceptance_criteria,
    pricing_acceptance_criteria,
    recent_events_acceptance_criteria,
)
from openmagi_core_agent.research.action_claims import ResearchActionVerb
from openmagi_core_agent.research.repair import RESEARCH_REPAIR_ACTIONS
from openmagi_core_agent.research.repair import ResearchRepairAction


DEFAULT_RESEARCH_POLICY_PACK_KEY = "research.determinism.default"

ResearchPolicyPackOwner = Literal["openmagi_first_party_research_harness"]
ResearchPolicyPackSource = Literal["first_party_research_recipe"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="never",
    hide_input_in_errors=True,
)
_PUBLIC_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{1,180}$")
_SAFE_TOKEN_RE = re.compile(r"^[a-z][a-z0-9_.:-]{1,120}$")
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
    r"hidden[_ -]?reasoning|chain[_ -]?of[_ -]?thought|authorization|"
    r"cookie|set-cookie|api[_ -]?key|secret|token|model[_ -]?summary|"
    r"model[_ -]?generated[_ -]?summary",
    re.IGNORECASE,
)
_FORBIDDEN_TOKEN_PARTS = frozenset(
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
        "transcript",
    }
)
_ADK_USAGE_NOTES = (
    "Research policy pack metadata only; no ADK Agent, Runner, FunctionTool, "
    "callbacks, SessionService, MemoryService, ArtifactService, Evaluation, "
    "live provider, browser, model call, ToolHost execution, memory write, or "
    "channel delivery is attached."
)
_REQUIRED_SOURCE_PROOF = (
    "runtime_source_ref",
    "opened_snapshot_or_document_read",
    "content_digest",
    "inspected_timestamp",
    "source_kind",
    "span_refs",
    "redaction_status",
    "freshness_window",
)
_DEFAULT_VERIFIER_STAGES = (
    ("stage:action-proof", "action-proof", ("afterLLMCall", "intermediateSynthesis")),
    ("stage:source-proof", "source-proof", ("afterToolUse", "sourceSummary")),
    ("stage:claim-proof", "claim-proof", ("intermediateSynthesis", "finalProjection")),
    ("stage:task-proof", "task-proof", ("onTaskCheckpoint", "finalProjection")),
    (
        "stage:intermediate-boundary",
        "intermediate-boundary",
        ("sourceSummary", "childResult", "intermediateSynthesis"),
    ),
    ("stage:repair", "repair", ("beforeCommit", "finalProjection")),
    ("stage:final-projection", "final-projection", ("finalProjection",)),
)
_ACTIVATION_GATES = (
    "explicit_policy_pack_ref",
    "local_fake_provider_only",
    "toolhost_receipts_required",
    "runtime_evidence_graph_required",
    "no_live_authority",
)
_REQUIRED_VERIFIER_REFS = frozenset(stage[1] for stage in _DEFAULT_VERIFIER_STAGES)
_FACT_REQUIRES = ("supported", "current", "relevant", "verified_span")
_POLICY_PACK_OBJECT_IDS: set[int] = set()
_POLICY_PACK_FINGERPRINTS: dict[int, str] = {}
_POLICY_PACK_FINALIZERS: dict[int, object] = {}


class _ResearchPolicyPackModel(BaseModel):
    model_config = _MODEL_CONFIG

    @classmethod
    def model_construct(cls, *args: object, **kwargs: object) -> Self:
        raise TypeError("model_construct is disabled for research policy pack contracts")

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = deep
        data = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            data.update(_alias_updates(type(self), update))
        return type(self).model_validate(data)


class ResearchClaimSupportPolicy(_ResearchPolicyPackModel):
    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        raise TypeError("ResearchClaimSupportPolicy subclasses are not accepted")

    supported_projection_mode: Literal["fact"] = Field(
        default="fact",
        alias="supportedProjectionMode",
    )
    weak_projection_mode: Literal["qualified"] = Field(
        default="qualified",
        alias="weakProjectionMode",
    )
    unsupported_projection_mode: Literal["omitted"] = Field(
        default="omitted",
        alias="unsupportedProjectionMode",
    )
    stale_projection_mode: Literal["repair_or_omitted"] = Field(
        default="repair_or_omitted",
        alias="staleProjectionMode",
    )
    contradicted_projection_mode: Literal["omitted"] = Field(
        default="omitted",
        alias="contradictedProjectionMode",
    )
    fact_requires: tuple[str, ...] = Field(
        default=_FACT_REQUIRES,
        alias="factRequires",
    )

    @field_validator("fact_requires")
    @classmethod
    def _validate_fact_requires(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        facts = tuple(_safe_token(item, "factRequires") for item in value)
        if facts != _FACT_REQUIRES:
            raise ValueError("factRequires must match the first-party research support policy")
        return facts


class ResearchCriteriaTemplateRef(_ResearchPolicyPackModel):
    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        raise TypeError("ResearchCriteriaTemplateRef subclasses are not accepted")

    criteria_set_id: str = Field(alias="criteriaSetId")
    template_key: str = Field(alias="templateKey")
    required_evidence_types: tuple[str, ...] = Field(alias="requiredEvidenceTypes")

    @field_validator("criteria_set_id", "template_key")
    @classmethod
    def _validate_public_refs(cls, value: str) -> str:
        return _public_ref(value, "criteria template")

    @field_validator("required_evidence_types")
    @classmethod
    def _validate_evidence_types(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("requiredEvidenceTypes must be non-empty")
        return tuple(_safe_token(item, "requiredEvidenceTypes") for item in value)


class ResearchVerifierStage(_ResearchPolicyPackModel):
    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        raise TypeError("ResearchVerifierStage subclasses are not accepted")

    stage_id: str = Field(alias="stageId")
    verifier_ref: str = Field(alias="verifierRef")
    boundary_refs: tuple[str, ...] = Field(alias="boundaryRefs")
    default_off: Literal[True] = Field(default=True, alias="defaultOff")
    live_execution_allowed: Literal[False] = Field(
        default=False,
        alias="liveExecutionAllowed",
    )
    tool_execution_allowed: Literal[False] = Field(
        default=False,
        alias="toolExecutionAllowed",
    )

    @field_validator("stage_id", "verifier_ref")
    @classmethod
    def _validate_public_refs(cls, value: str) -> str:
        return _public_ref(value, "verifier stage")

    @field_validator("boundary_refs")
    @classmethod
    def _validate_boundary_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("boundaryRefs must be non-empty")
        return tuple(_public_ref(item, "boundaryRefs") for item in value)


class ResearchPolicyPack(_ResearchPolicyPackModel):
    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        raise TypeError("ResearchPolicyPack subclasses are not accepted")

    key: str
    owner: ResearchPolicyPackOwner
    source: ResearchPolicyPackSource
    default_off: Literal[True] = Field(default=True, alias="defaultOff")
    local_only: Literal[True] = Field(default=True, alias="localOnly")
    fake_provider_only: Literal[True] = Field(default=True, alias="fakeProviderOnly")
    live_execution_allowed: Literal[False] = Field(
        default=False,
        alias="liveExecutionAllowed",
    )
    live_web_allowed: Literal[False] = Field(default=False, alias="liveWebAllowed")
    browser_execution_allowed: Literal[False] = Field(
        default=False,
        alias="browserExecutionAllowed",
    )
    provider_calls_allowed: Literal[False] = Field(
        default=False,
        alias="providerCallsAllowed",
    )
    tool_execution_allowed: Literal[False] = Field(
        default=False,
        alias="toolExecutionAllowed",
    )
    model_calls_allowed: Literal[False] = Field(default=False, alias="modelCallsAllowed")
    memory_writes_allowed: Literal[False] = Field(
        default=False,
        alias="memoryWritesAllowed",
    )
    channel_delivery_allowed: Literal[False] = Field(
        default=False,
        alias="channelDeliveryAllowed",
    )
    user_visible_python_activation_allowed: Literal[False] = Field(
        default=False,
        alias="userVisiblePythonActivationAllowed",
    )
    execution_verbs: tuple[ResearchActionVerb, ...] = Field(alias="executionVerbs")
    required_source_proof: tuple[str, ...] = Field(alias="requiredSourceProof")
    claim_support_policy: ResearchClaimSupportPolicy = Field(alias="claimSupportPolicy")
    criteria_templates: tuple[ResearchCriteriaTemplateRef, ...] = Field(
        alias="criteriaTemplates",
    )
    repair_actions: tuple[ResearchRepairAction, ...] = Field(alias="repairActions")
    verifier_stages: tuple[ResearchVerifierStage, ...] = Field(alias="verifierStages")
    activation_gates: tuple[str, ...] = Field(alias="activationGates")
    adk_usage_notes: str = Field(default=_ADK_USAGE_NOTES, alias="adkUsageNotes")

    @field_validator("key")
    @classmethod
    def _validate_key(cls, value: str) -> str:
        return _public_ref(value, "policy pack key")

    @field_validator("required_source_proof")
    @classmethod
    def _validate_required_source_proof(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        proof = tuple(_safe_token(item, "requiredSourceProof") for item in value)
        if not proof:
            raise ValueError("requiredSourceProof must be non-empty")
        if len(set(proof)) != len(proof):
            raise ValueError("requiredSourceProof must not contain duplicates")
        missing = set(_REQUIRED_SOURCE_PROOF) - set(proof)
        if missing:
            raise ValueError("requiredSourceProof is missing required research source invariants")
        return proof

    @field_validator("repair_actions")
    @classmethod
    def _validate_repair_actions(
        cls,
        value: tuple[ResearchRepairAction, ...],
    ) -> tuple[ResearchRepairAction, ...]:
        if not value:
            raise ValueError("repairActions must be non-empty")
        if len(set(value)) != len(value):
            raise ValueError("repairActions must not contain duplicates")
        missing = set(RESEARCH_REPAIR_ACTIONS) - set(value)
        if missing:
            raise ValueError("repairActions is missing required research repair semantics")
        return value

    @field_validator("activation_gates")
    @classmethod
    def _validate_activation_gates(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        gates = tuple(_safe_token(item, "activationGates") for item in value)
        if not gates:
            raise ValueError("activationGates must be non-empty")
        if len(set(gates)) != len(gates):
            raise ValueError("activationGates must not contain duplicates")
        missing = set(_ACTIVATION_GATES) - set(gates)
        if missing:
            raise ValueError("activationGates is missing required research activation gates")
        return gates

    @field_validator("adk_usage_notes")
    @classmethod
    def _validate_adk_usage_notes(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("adkUsageNotes must be non-empty")
        _reject_unsafe_public_text(clean, "adkUsageNotes")
        if len(clean) > 360:
            raise ValueError("adkUsageNotes must be at most 360 characters")
        return clean

    @model_validator(mode="after")
    def _validate_policy_pack_shape(self) -> Self:
        if type(self) is not ResearchPolicyPack:
            raise TypeError("ResearchPolicyPack contract subclasses are not accepted")
        if type(self.claim_support_policy) is not ResearchClaimSupportPolicy:
            raise TypeError("claimSupportPolicy must be a ResearchClaimSupportPolicy contract")
        if any(type(template) is not ResearchCriteriaTemplateRef for template in self.criteria_templates):
            raise TypeError("criteriaTemplates must use ResearchCriteriaTemplateRef contracts")
        if any(type(stage) is not ResearchVerifierStage for stage in self.verifier_stages):
            raise TypeError("verifierStages must use ResearchVerifierStage contracts")
        if tuple(self.execution_verbs) != tuple(get_args(ResearchActionVerb)):
            raise ValueError("executionVerbs must match the research action proof contract")
        if not self.criteria_templates:
            raise ValueError("criteriaTemplates must be non-empty")
        criteria_ids = [template.criteria_set_id for template in self.criteria_templates]
        template_keys = [template.template_key for template in self.criteria_templates]
        if len(set(criteria_ids)) != len(criteria_ids):
            raise ValueError("criteriaTemplates must not contain duplicate criteriaSetId values")
        if len(set(template_keys)) != len(template_keys):
            raise ValueError("criteriaTemplates must not contain duplicate templateKey values")
        required_template_signatures = {
            _criteria_template_signature(template) for template in _criteria_template_refs()
        }
        template_signatures = {
            _criteria_template_signature(template) for template in self.criteria_templates
        }
        if required_template_signatures - template_signatures:
            raise ValueError("criteriaTemplates is missing required research templates")
        if not self.verifier_stages:
            raise ValueError("verifierStages must be non-empty")
        stage_ids = [stage.stage_id for stage in self.verifier_stages]
        if len(set(stage_ids)) != len(stage_ids):
            raise ValueError("verifierStages must not contain duplicate stageId values")
        missing_verifiers = _REQUIRED_VERIFIER_REFS - {
            stage.verifier_ref for stage in self.verifier_stages
        }
        if missing_verifiers:
            raise ValueError("verifierStages is missing required research verifier stages")
        required_stage_signatures = {
            (stage_id, verifier_ref, boundary_refs)
            for stage_id, verifier_ref, boundary_refs in _DEFAULT_VERIFIER_STAGES
        }
        stage_signatures = {
            _verifier_stage_signature(stage) for stage in self.verifier_stages
        }
        if required_stage_signatures - stage_signatures:
            raise ValueError("verifierStages is missing required research verifier boundaries")
        _mark_policy_pack_created(self)
        return self

    @property
    def public_projection(self) -> Callable[[], dict[str, object]]:
        def _project() -> dict[str, object]:
            _validate_policy_pack_object(self)
            payload = self._public_payload()
            return {**payload, "digest": _digest_for(payload)}

        return _project

    @property
    def public_digest(self) -> Callable[[], str]:
        def _digest() -> str:
            _validate_policy_pack_object(self)
            return _digest_for(self._public_payload())

        return _digest

    def _public_payload(self) -> dict[str, object]:
        return {
            "key": self.key,
            "owner": self.owner,
            "source": self.source,
            "activation": {
                "defaultOff": self.default_off,
                "localOnly": self.local_only,
                "fakeProviderOnly": self.fake_provider_only,
                "liveExecutionAllowed": self.live_execution_allowed,
                "liveWebAllowed": self.live_web_allowed,
                "browserExecutionAllowed": self.browser_execution_allowed,
                "providerCallsAllowed": self.provider_calls_allowed,
                "toolExecutionAllowed": self.tool_execution_allowed,
                "modelCallsAllowed": self.model_calls_allowed,
                "memoryWritesAllowed": self.memory_writes_allowed,
                "channelDeliveryAllowed": self.channel_delivery_allowed,
                "userVisiblePythonActivationAllowed": (
                    self.user_visible_python_activation_allowed
                ),
            },
            "executionVerbs": self.execution_verbs,
            "requiredSourceProof": self.required_source_proof,
            "claimSupportPolicy": {
                "supportedProjectionMode": (
                    self.claim_support_policy.supported_projection_mode
                ),
                "weakProjectionMode": self.claim_support_policy.weak_projection_mode,
                "unsupportedProjectionMode": (
                    self.claim_support_policy.unsupported_projection_mode
                ),
                "staleProjectionMode": self.claim_support_policy.stale_projection_mode,
                "contradictedProjectionMode": (
                    self.claim_support_policy.contradicted_projection_mode
                ),
                "factRequires": self.claim_support_policy.fact_requires,
            },
            "criteriaTemplates": tuple(
                {
                    "criteriaSetId": template.criteria_set_id,
                    "templateKey": template.template_key,
                    "requiredEvidenceTypes": template.required_evidence_types,
                }
                for template in self.criteria_templates
            ),
            "repairActions": self.repair_actions,
            "verifierStages": tuple(
                {
                    "stageId": stage.stage_id,
                    "verifierRef": stage.verifier_ref,
                    "boundaryRefs": stage.boundary_refs,
                    "defaultOff": stage.default_off,
                    "liveExecutionAllowed": stage.live_execution_allowed,
                    "toolExecutionAllowed": stage.tool_execution_allowed,
                }
                for stage in self.verifier_stages
            ),
            "activationGates": self.activation_gates,
            "adkUsageNotes": self.adk_usage_notes,
        }


def build_default_research_policy_pack() -> ResearchPolicyPack:
    return ResearchPolicyPack(
        key=DEFAULT_RESEARCH_POLICY_PACK_KEY,
        owner="openmagi_first_party_research_harness",
        source="first_party_research_recipe",
        executionVerbs=tuple(get_args(ResearchActionVerb)),
        requiredSourceProof=_REQUIRED_SOURCE_PROOF,
        claimSupportPolicy=ResearchClaimSupportPolicy(),
        criteriaTemplates=_criteria_template_refs(),
        repairActions=RESEARCH_REPAIR_ACTIONS,
        verifierStages=_default_verifier_stages(),
        activationGates=_ACTIVATION_GATES,
    )


def select_research_policy_pack(
    recipe_metadata: Mapping[str, object],
    *,
    registry: Iterable[ResearchPolicyPack] | None = None,
) -> ResearchPolicyPack:
    if not isinstance(recipe_metadata, Mapping):
        raise TypeError("recipe metadata must be a mapping")
    has_canonical = "policyPackRef" in recipe_metadata
    has_alias = "policy_pack_ref" in recipe_metadata
    if not has_canonical and not has_alias:
        raise ValueError("research policy selection requires explicit policyPackRef")
    if has_canonical and not isinstance(recipe_metadata["policyPackRef"], str):
        raise TypeError("policyPackRef must be a string")
    if has_alias and not isinstance(recipe_metadata["policy_pack_ref"], str):
        raise TypeError("policy_pack_ref must be a string")
    if has_canonical and has_alias:
        canonical = _public_ref(str(recipe_metadata["policyPackRef"]), "policyPackRef")
        alias = _public_ref(str(recipe_metadata["policy_pack_ref"]), "policy_pack_ref")
        if canonical != alias:
            raise ValueError("policyPackRef and policy_pack_ref must match")
        requested = canonical
    elif has_canonical:
        requested = str(recipe_metadata["policyPackRef"])
    else:
        requested = str(recipe_metadata["policy_pack_ref"])
    requested_ref = _public_ref(requested, "policyPackRef")
    packs = (
        (build_default_research_policy_pack(),)
        if registry is None
        else tuple(registry)
    )
    by_key: dict[str, ResearchPolicyPack] = {}
    for pack in packs:
        if type(pack) is not ResearchPolicyPack:
            raise TypeError("research policy registry entries must be ResearchPolicyPack objects")
        _validate_policy_pack_object(pack)
        if pack.key in by_key:
            raise ValueError("research policy registry must not contain duplicate keys")
        by_key[pack.key] = pack
    try:
        return by_key[requested_ref]
    except KeyError as exc:
        raise ValueError(f"unknown research policy pack: {requested_ref}") from exc


def _criteria_template_refs() -> tuple[ResearchCriteriaTemplateRef, ...]:
    criteria_sets = (
        positioning_acceptance_criteria("default research"),
        pricing_acceptance_criteria("default research"),
        recent_events_acceptance_criteria("default research"),
    )
    refs: list[ResearchCriteriaTemplateRef] = []
    for criteria_set in criteria_sets:
        required_types = tuple(
            sorted(
                {
                    evidence_type
                    for criterion in criteria_set.criteria
                    for evidence_type in criterion.required_evidence_types
                }
            )
        )
        refs.append(
            ResearchCriteriaTemplateRef(
                criteriaSetId=criteria_set.criteria_set_id,
                templateKey=criteria_set.criteria_set_id.replace("research-acceptance-", ""),
                requiredEvidenceTypes=required_types,
            )
        )
    return tuple(sorted(refs, key=lambda item: item.criteria_set_id))


def _default_verifier_stages() -> tuple[ResearchVerifierStage, ...]:
    return tuple(
        ResearchVerifierStage(
            stageId=stage_id,
            verifierRef=verifier_ref,
            boundaryRefs=boundary_refs,
        )
        for stage_id, verifier_ref, boundary_refs in _DEFAULT_VERIFIER_STAGES
    )


def _criteria_template_signature(
    template: ResearchCriteriaTemplateRef,
) -> tuple[str, str, tuple[str, ...]]:
    return (
        template.criteria_set_id,
        template.template_key,
        template.required_evidence_types,
    )


def _verifier_stage_signature(
    stage: ResearchVerifierStage,
) -> tuple[str, str, tuple[str, ...]]:
    return (
        stage.stage_id,
        stage.verifier_ref,
        stage.boundary_refs,
    )


def _mark_policy_pack_created(pack: ResearchPolicyPack) -> None:
    object_id = id(pack)
    if object_id in _POLICY_PACK_FINGERPRINTS:
        return
    _POLICY_PACK_OBJECT_IDS.add(object_id)
    _POLICY_PACK_FINGERPRINTS[object_id] = _model_fingerprint(pack)
    _POLICY_PACK_FINALIZERS[object_id] = finalize(
        pack,
        _discard_policy_pack_object_id,
        object_id,
    )


def _discard_policy_pack_object_id(object_id: int) -> None:
    _POLICY_PACK_OBJECT_IDS.discard(object_id)
    _POLICY_PACK_FINGERPRINTS.pop(object_id, None)
    _POLICY_PACK_FINALIZERS.pop(object_id, None)


def _validate_policy_pack_object(pack: ResearchPolicyPack) -> None:
    _reject_unexpected_policy_pack_attributes(pack)
    object_id = id(pack)
    if object_id not in _POLICY_PACK_OBJECT_IDS:
        raise ValueError("research policy pack was not created by the policy pack contract")
    expected = _POLICY_PACK_FINGERPRINTS.get(object_id)
    if expected != _model_fingerprint(pack):
        raise ValueError("research policy pack was modified after creation")
    ResearchPolicyPack.model_validate(
        ResearchPolicyPack.model_dump(pack, by_alias=True, mode="python", warnings=False)
    )


def _reject_unexpected_policy_pack_attributes(pack: ResearchPolicyPack) -> None:
    allowed = set(ResearchPolicyPack.model_fields)
    extras = tuple(sorted(set(pack.__dict__) - allowed))
    if extras:
        raise ValueError(
            "research policy pack has unexpected runtime attributes: "
            + ", ".join(extras)
        )


def _public_ref(value: str, field_name: str) -> str:
    clean = value.strip()
    _reject_unsafe_public_text(clean, field_name)
    if not _PUBLIC_REF_RE.fullmatch(clean):
        raise ValueError(f"{field_name} must be a digest-safe public ref")
    return clean


def _safe_token(value: str, field_name: str) -> str:
    clean = value.strip()
    _reject_unsafe_public_text(clean, field_name)
    if not _SAFE_TOKEN_RE.fullmatch(clean):
        raise ValueError(f"{field_name} must be a digest-safe token")
    parts = frozenset(re.split(r"[_.:-]+", clean.casefold()))
    if parts & _FORBIDDEN_TOKEN_PARTS:
        raise ValueError(f"{field_name} must not contain raw, private, auth, or token data")
    return clean


def _reject_unsafe_public_text(value: str, field_name: str) -> None:
    if _PRIVATE_PATH_RE.search(value):
        raise ValueError(f"{field_name} must not contain private paths")
    if _SECRET_TEXT_RE.search(value) or _UNSAFE_TEXT_RE.search(value):
        raise ValueError(f"{field_name} must not contain raw, private, auth, token, or secret data")


def _alias_updates(model_class: type[BaseModel], update: Mapping[str, Any]) -> dict[str, Any]:
    alias_to_name = {
        field.alias: name
        for name, field in model_class.model_fields.items()
        if field.alias is not None
    }
    name_to_alias = {
        name: field.alias or name
        for name, field in model_class.model_fields.items()
    }
    return {
        name_to_alias.get(alias_to_name.get(key, key), key): value
        for key, value in update.items()
    }


def _digest_for(payload: object) -> str:
    material = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return "sha256:" + sha256(material.encode("utf-8")).hexdigest()


def _model_fingerprint(model: BaseModel) -> str:
    return _digest_for(
        BaseModel.model_dump(model, by_alias=True, mode="python", warnings=False)
    )


__all__ = [
    "DEFAULT_RESEARCH_POLICY_PACK_KEY",
    "ResearchClaimSupportPolicy",
    "ResearchCriteriaTemplateRef",
    "ResearchPolicyPack",
    "ResearchVerifierStage",
    "build_default_research_policy_pack",
    "select_research_policy_pack",
]
