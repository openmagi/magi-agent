from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from datetime import datetime
from hashlib import sha256
from typing import Any, Literal, Self
from weakref import finalize

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    field_serializer,
    field_validator,
    model_validator,
)

from openmagi_core_agent.evidence.runtime_issuance import (
    RuntimeIssueAuthority,
    require_runtime_issue_authority,
)


ResearchActionVerb = Literal[
    "searched",
    "read",
    "reviewed",
    "compared",
    "checked",
    "confirmed",
    "verified",
    "analyzed",
    "summarized",
    "inspected",
]
ResearchActionProofStatus = Literal["allowed", "denied"]
ResearchActionProofReason = Literal[
    "receipt_match",
    "missing_receipt",
    "receipt_mismatch",
    "requirement_mismatch",
]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_ACTION_VERBS: frozenset[str] = frozenset(ResearchActionVerb.__args__)  # type: ignore[attr-defined]
_PUBLIC_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{1,180}$")
_SAFE_KIND_RE = re.compile(r"^[a-z][a-z0-9_.:-]{1,80}$")
_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_SENTENCE_RE = re.compile(r"[^.!?\n]+(?:[.!?]|$)")
_MODEL_SUMMARY_RE = re.compile(
    r"\b(?:model|llm)[ _-]?(?:generated[ _-]?)?summary\b",
    re.IGNORECASE,
)
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
    r"raw[_ -]?(?:source|transcript|tool|prompt|output|result|log)|"
    r"hidden[_ -]?reasoning|chain[_ -]?of[_ -]?thought|authorization|"
    r"cookie|set-cookie|api[_ -]?key|secret|token",
    re.IGNORECASE,
)
_FORBIDDEN_KIND_PARTS = frozenset(
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
_FORBIDDEN_KIND_SUBSTRINGS = frozenset(
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
        "token",
        "tooloutput",
    }
)
_VERB_ALIASES: dict[str, ResearchActionVerb] = {
    "search": "searched",
    "searched": "searched",
    "read": "read",
    "review": "reviewed",
    "reviewed": "reviewed",
    "compare": "compared",
    "compared": "compared",
    "comparing": "compared",
    "check": "checked",
    "checked": "checked",
    "confirm": "confirmed",
    "confirmed": "confirmed",
    "verify": "verified",
    "verified": "verified",
    "analyze": "analyzed",
    "analyzed": "analyzed",
    "summarize": "summarized",
    "summarized": "summarized",
    "inspect": "inspected",
    "inspected": "inspected",
}
_ACTION_PATTERN = re.compile(
    r"\b(?:"
    r"(?:I|we)\s+"
    r"(searched|read|reviewed|compared|checked|confirmed|verified|analyzed|summarized|inspected)"
    r"|the\s+agent\s+"
    r"(searched|read|reviewed|compared|checked|confirmed|verified|analyzed|summarized|inspected)"
    r"|after\s+(comparing|checking|reviewing|reading|analyzing|summarizing|inspecting)"
    r")\b",
    re.IGNORECASE,
)
_ACTION_CLAUSE_PATTERN = re.compile(
    r"\b(?:"
    r"(?:I|we|the\s+agent)\s+"
    r"(?:searched|read|reviewed|compared|checked|confirmed|verified|analyzed|summarized|inspected)"
    r"(?:\s*(?:,|and)\s*"
    r"(?:searched|read|reviewed|compared|checked|confirmed|verified|analyzed|summarized|inspected))*"
    r"|after\s+"
    r"(?:comparing|checking|reviewing|reading|analyzing|summarizing|inspecting)"
    r"(?:\s*(?:,|and)\s*"
    r"(?:comparing|checking|reviewing|reading|analyzing|summarizing|inspecting))*"
    r")\b",
    re.IGNORECASE,
)
_ACTION_VERB_TOKEN_RE = re.compile(
    r"\b("
    r"searched|read|reviewed|compared|checked|confirmed|verified|analyzed|summarized|inspected|"
    r"comparing|checking|reviewing|reading|analyzing|summarizing|inspecting"
    r")\b",
    re.IGNORECASE,
)
_GERUND_VERBS: dict[str, ResearchActionVerb] = {
    "comparing": "compared",
    "checking": "checked",
    "reviewing": "reviewed",
    "reading": "read",
    "analyzing": "analyzed",
    "summarizing": "summarized",
    "inspecting": "inspected",
}
_DEFAULT_RECEIPT_KINDS: tuple[str, ...] = (
    "toolhost_receipt",
    "source_receipt",
)
_ADK_USAGE_NOTES = "Metadata only; no ADK Runner or FunctionTool is attached."
_RUNTIME_ISSUED_RECEIPT_OBJECT_IDS: set[int] = set()
_RUNTIME_ISSUED_RECEIPT_FINGERPRINTS: dict[int, object] = {}
_RUNTIME_ISSUED_RECEIPT_FINALIZERS: dict[int, object] = {}
_VERIFIER_ISSUED_VERDICT_OBJECT_IDS: set[int] = set()
_VERIFIER_ISSUED_VERDICT_FINGERPRINTS: dict[int, object] = {}
_VERIFIER_ISSUED_VERDICT_FINALIZERS: dict[int, object] = {}


class _ResearchActionModel(BaseModel):
    model_config = _MODEL_CONFIG

    @classmethod
    def model_construct(cls, *args: object, **kwargs: object) -> Self:
        raise TypeError("model_construct is disabled for research action claim contracts")

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


class ResearchActionExecutionPosture(_ResearchActionModel):
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
    channel_delivery_allowed: Literal[False] = Field(default=False, alias="channelDeliveryAllowed")
    user_visible_python_activation: Literal[False] = Field(
        default=False,
        alias="userVisiblePythonActivation",
    )

    @field_serializer(
        "default_off",
        "local_only",
        "fake_provider_only",
        "live_execution_allowed",
        "provider_calls_allowed",
        "browser_execution_allowed",
        "tool_execution_allowed",
        "channel_delivery_allowed",
        "user_visible_python_activation",
    )
    def _serialize_posture(self, value: object) -> bool:
        return bool(value)


class ResearchActionClaim(_ResearchActionModel):
    claim_id: str = Field(alias="claimId")
    action_verb: ResearchActionVerb = Field(alias="actionVerb")
    claim_text: str = Field(alias="claimText")
    claim_text_digest: str | None = Field(default=None, alias="claimTextDigest")
    sentence_index: int = Field(alias="sentenceIndex", ge=0)

    @model_validator(mode="before")
    @classmethod
    def _validate_raw_claim(cls, value: object) -> object:
        if isinstance(value, Mapping):
            _reject_unsafe_raw_value(value)
        return value

    @field_validator("claim_id")
    @classmethod
    def _validate_claim_id(cls, value: str) -> str:
        return _public_ref(value, "claimId")

    @field_validator("claim_text")
    @classmethod
    def _validate_claim_text(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("claimText must be non-empty")
        _reject_unsafe_public_text(clean, "claimText")
        if len(clean) > 500:
            raise ValueError("claimText must be at most 500 characters")
        return clean

    @field_validator("claim_text_digest")
    @classmethod
    def _validate_claim_text_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _DIGEST_RE.fullmatch(value):
            raise ValueError("claimTextDigest must be a sha256 hex digest")
        return value

    @model_validator(mode="after")
    def _bind_claim_text_digest(self) -> Self:
        expected = _claim_text_digest(self.claim_text)
        if self.claim_text_digest is None:
            object.__setattr__(self, "claim_text_digest", expected)
        elif self.claim_text_digest != expected:
            raise ValueError("claimTextDigest must be bound to claimText")
        return self


class ResearchActionProofReceiptRef(_ResearchActionModel):
    _issued_by_runtime_boundary: bool = PrivateAttr(default=False)

    receipt_id: str = Field(alias="receiptId")
    action_verb: ResearchActionVerb = Field(alias="actionVerb")
    receipt_kind: str = Field(alias="receiptKind")
    runtime_issued: Literal[True] = Field(alias="runtimeIssued")
    receipt_authority: Literal["openmagi_runtime_boundary"] = Field(alias="receiptAuthority")
    tool_host_mediated: Literal[True] = Field(alias="toolHostMediated")
    tool_id: str | None = Field(default=None, alias="toolId")
    source_id: str | None = Field(default=None, alias="sourceId")
    observed_at: str = Field(alias="observedAt")
    digest: str
    public_label: str | None = Field(default=None, alias="publicLabel")

    @classmethod
    def issue_runtime_receipt(
        cls,
        *,
        runtime_authority: RuntimeIssueAuthority | None = None,
        receipt_id: str,
        action_verb: ResearchActionVerb,
        receipt_kind: str,
        tool_id: str,
        source_id: str,
        observed_at: str,
        public_label: str | None = None,
    ) -> Self:
        require_runtime_issue_authority(
            runtime_authority,
            scope="research_action_proof",
        )
        receipt = cls(
            receiptId=receipt_id,
            actionVerb=action_verb,
            receiptKind=receipt_kind,
            runtimeIssued=True,
            receiptAuthority="openmagi_runtime_boundary",
            toolHostMediated=True,
            toolId=tool_id,
            sourceId=source_id,
            observedAt=observed_at,
            digest=_receipt_digest(
                receipt_id=receipt_id,
                action_verb=action_verb,
                receipt_kind=receipt_kind,
                tool_id=tool_id,
                source_id=source_id,
                observed_at=observed_at,
            ),
            publicLabel=public_label,
        )
        _mark_runtime_receipt_issued(receipt)
        return receipt

    @property
    def is_runtime_boundary_issued(self) -> bool:
        return (
            bool(self.__pydantic_private__.get("_issued_by_runtime_boundary"))
            and id(self) in _RUNTIME_ISSUED_RECEIPT_OBJECT_IDS
        )

    @model_validator(mode="before")
    @classmethod
    def _validate_raw_receipt(cls, value: object) -> object:
        if isinstance(value, Mapping):
            _reject_unsafe_raw_value(value)
        return value

    @field_validator("receipt_id")
    @classmethod
    def _validate_receipt_id(cls, value: str) -> str:
        return _public_ref(value, "receiptId")

    @field_validator("receipt_kind")
    @classmethod
    def _validate_receipt_kind(cls, value: str) -> str:
        return _safe_kind(value, "receiptKind")

    @field_validator("tool_id", "source_id")
    @classmethod
    def _validate_optional_public_ref(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _public_ref(value, "receipt ref")

    @field_validator("observed_at")
    @classmethod
    def _validate_observed_at(cls, value: str) -> str:
        clean = value.strip()
        _parse_utcish_datetime(clean, "observedAt")
        return clean

    @field_validator("digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        if not _DIGEST_RE.fullmatch(value):
            raise ValueError("digest must be a sha256 hex digest")
        return value

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
    def _validate_receipt_digest_binding(self) -> Self:
        if self.tool_id is None or self.source_id is None:
            raise ValueError("runtime receipt refs require toolId and sourceId")
        expected = _receipt_digest(
            receipt_id=self.receipt_id,
            action_verb=self.action_verb,
            receipt_kind=self.receipt_kind,
            tool_id=self.tool_id,
            source_id=self.source_id,
            observed_at=self.observed_at,
        )
        if self.digest != expected:
            raise ValueError("digest must be bound to receipt metadata")
        return self

    def public_projection(self) -> dict[str, object]:
        return {
            "receiptId": self.receipt_id,
            "actionVerb": self.action_verb,
            "receiptKind": self.receipt_kind,
            "runtimeIssued": self.runtime_issued,
            "receiptAuthority": self.receipt_authority,
            "toolHostMediated": self.tool_host_mediated,
            "toolId": self.tool_id,
            "sourceId": self.source_id,
            "observedAt": self.observed_at,
            "digest": self.digest,
            "publicLabel": self.public_label,
        }


class ResearchActionProofRequirement(_ResearchActionModel):
    claim_id: str = Field(alias="claimId")
    claim_text_digest: str | None = Field(default=None, alias="claimTextDigest")
    required_action_verb: ResearchActionVerb = Field(alias="requiredActionVerb")
    required_receipt_kinds: tuple[str, ...] = Field(alias="requiredReceiptKinds")
    required_tool_ids: tuple[str, ...] = Field(default=(), alias="requiredToolIds")
    required_source_ids: tuple[str, ...] = Field(default=(), alias="requiredSourceIds")
    not_before: str | None = Field(default=None, alias="notBefore")
    not_after: str | None = Field(default=None, alias="notAfter")

    @model_validator(mode="before")
    @classmethod
    def _validate_raw_requirement(cls, value: object) -> object:
        if isinstance(value, Mapping):
            _reject_unsafe_raw_value(value)
        return value

    @field_validator("claim_id")
    @classmethod
    def _validate_claim_id(cls, value: str) -> str:
        return _public_ref(value, "claimId")

    @field_validator("claim_text_digest")
    @classmethod
    def _validate_claim_text_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _DIGEST_RE.fullmatch(value):
            raise ValueError("claimTextDigest must be a sha256 hex digest")
        return value

    @field_validator("required_receipt_kinds")
    @classmethod
    def _validate_required_receipt_kinds(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("requiredReceiptKinds must be non-empty")
        if len(set(value)) != len(value):
            raise ValueError("requiredReceiptKinds must not contain duplicate values")
        return tuple(_safe_kind(item, "requiredReceiptKinds") for item in value)

    @field_validator("required_tool_ids", "required_source_ids")
    @classmethod
    def _validate_public_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("required id lists must not contain duplicate values")
        return tuple(_public_ref(item, "required id") for item in value)

    @field_validator("not_before", "not_after")
    @classmethod
    def _validate_optional_time(cls, value: str | None) -> str | None:
        if value is None:
            return None
        clean = value.strip()
        _parse_utcish_datetime(clean, "time bound")
        return clean

    @model_validator(mode="after")
    def _validate_time_window(self) -> Self:
        if self.not_before is not None and self.not_after is not None:
            before = _parse_utcish_datetime(self.not_before, "notBefore")
            after = _parse_utcish_datetime(self.not_after, "notAfter")
            if before > after:
                raise ValueError("notBefore must be before or equal to notAfter")
        return self

    def public_projection(self) -> dict[str, object]:
        return {
            "claimId": self.claim_id,
            "claimTextDigest": self.claim_text_digest,
            "requiredActionVerb": self.required_action_verb,
            "requiredReceiptKinds": self.required_receipt_kinds,
            "requiredToolIds": self.required_tool_ids,
            "requiredSourceIds": self.required_source_ids,
            "notBefore": self.not_before,
            "notAfter": self.not_after,
        }


class ResearchActionProofVerdict(_ResearchActionModel):
    _issued_by_action_verifier: bool = PrivateAttr(default=False)

    claim_id: str = Field(alias="claimId")
    claim_text_digest: str | None = Field(default=None, alias="claimTextDigest")
    action_verb: ResearchActionVerb = Field(alias="actionVerb")
    verdict: ResearchActionProofStatus
    reason_code: ResearchActionProofReason = Field(alias="reasonCode")
    matched_receipt_refs: tuple[str, ...] = Field(default=(), alias="matchedReceiptRefs")
    projected_text: str = Field(alias="projectedText")
    requirement: ResearchActionProofRequirement = Field(alias="requirement")
    execution_posture: ResearchActionExecutionPosture = Field(
        default_factory=ResearchActionExecutionPosture,
        alias="executionPosture",
    )
    adk_usage_notes: str = Field(default=_ADK_USAGE_NOTES, alias="adkUsageNotes")

    @property
    def is_action_verifier_issued(self) -> bool:
        return (
            bool(self.__pydantic_private__.get("_issued_by_action_verifier"))
            and id(self) in _VERIFIER_ISSUED_VERDICT_OBJECT_IDS
        )

    @field_validator("claim_id")
    @classmethod
    def _validate_claim_id(cls, value: str) -> str:
        return _public_ref(value, "claimId")

    @field_validator("claim_text_digest")
    @classmethod
    def _validate_claim_text_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _DIGEST_RE.fullmatch(value):
            raise ValueError("claimTextDigest must be a sha256 hex digest")
        return value

    @field_validator("matched_receipt_refs")
    @classmethod
    def _validate_matched_receipt_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("matchedReceiptRefs must not contain duplicate values")
        return tuple(_public_ref(item, "matchedReceiptRefs") for item in value)

    @field_validator("projected_text", "adk_usage_notes")
    @classmethod
    def _validate_public_text(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("public text must be non-empty")
        _reject_unsafe_public_text(clean, "public text")
        if len(clean) > 240:
            raise ValueError("public text must be at most 240 characters")
        return clean

    @model_validator(mode="after")
    def _validate_verdict_shape(self) -> Self:
        if self.verdict == "allowed":
            if self.reason_code != "receipt_match":
                raise ValueError("allowed verdicts require reasonCode=receipt_match")
            if not self.matched_receipt_refs:
                raise ValueError("allowed verdicts require matchedReceiptRefs")
            expected_text = f"verified: {self.action_verb}"
        else:
            if self.reason_code == "receipt_match":
                raise ValueError("denied verdicts must not use reasonCode=receipt_match")
            if self.matched_receipt_refs:
                raise ValueError("denied verdicts must not include matchedReceiptRefs")
            expected_text = f"not verified: {self.action_verb}"
        if self.projected_text != expected_text:
            raise ValueError("projectedText must match deterministic action proof wording")
        if self.requirement.claim_id != self.claim_id:
            raise ValueError("requirement claimId must match verdict claimId")
        digest_mismatch = (
            self.claim_text_digest is not None
            and self.requirement.claim_text_digest is not None
            and self.requirement.claim_text_digest != self.claim_text_digest
        )
        if self.reason_code == "requirement_mismatch":
            if self.requirement.required_action_verb == self.action_verb and not digest_mismatch:
                raise ValueError(
                    "requirement_mismatch requires a different verb or claim digest"
                )
        elif self.requirement.required_action_verb != self.action_verb or digest_mismatch:
            raise ValueError("requirement requiredActionVerb must match verdict actionVerb")
        return self

    def public_projection(self) -> dict[str, object]:
        return {
            "claimId": self.claim_id,
            "claimTextDigest": self.claim_text_digest,
            "actionVerb": self.action_verb,
            "verdict": self.verdict,
            "reasonCode": self.reason_code,
            "matchedReceiptRefs": self.matched_receipt_refs,
            "projectedText": self.projected_text,
            "requirement": self.requirement.public_projection(),
            "executionPosture": self.execution_posture.model_dump(
                by_alias=True,
                mode="python",
                warnings=False,
            ),
            "adkUsageNotes": self.adk_usage_notes,
        }


def detect_research_action_claims(text: str) -> tuple[ResearchActionClaim, ...]:
    _reject_unsafe_public_text(text, "text")
    claims: list[ResearchActionClaim] = []
    for sentence_index, sentence in enumerate(_sentences(text)):
        verbs = _action_verbs_in_sentence(sentence)
        if not verbs:
            continue
        for verb in verbs:
            claims.append(
                ResearchActionClaim(
                    claimId=f"claim:{len(claims) + 1}:{verb}",
                    actionVerb=verb,
                    claimText=sentence,
                    sentenceIndex=sentence_index,
                )
            )
    return tuple(claims)


def verify_research_action_claims(
    claims: Iterable[ResearchActionClaim | Mapping[str, object]],
    receipts: Iterable[ResearchActionProofReceiptRef],
    *,
    requirements: Iterable[ResearchActionProofRequirement | Mapping[str, object]] = (),
) -> tuple[ResearchActionProofVerdict, ...]:
    validated_claims = tuple(_validate_claim(claim) for claim in claims)
    validated_receipts = tuple(_validate_receipt(receipt) for receipt in receipts)
    validated_requirements = tuple(
        _validate_requirement(requirement)
        for requirement in requirements
    )
    claim_ids = {claim.claim_id for claim in validated_claims}
    if len(claim_ids) != len(validated_claims):
        raise ValueError("claims must not contain duplicate claimId values")
    requirements_by_claim_id = {
        requirement.claim_id: requirement
        for requirement in validated_requirements
    }
    if len(requirements_by_claim_id) != len(validated_requirements):
        raise ValueError("requirements must not contain duplicate claimId values")
    unknown_requirements = set(requirements_by_claim_id) - claim_ids
    if unknown_requirements:
        raise ValueError("requirements contain unknown claimId values")

    verdicts: list[ResearchActionProofVerdict] = []
    for claim in validated_claims:
        requirement = requirements_by_claim_id.get(claim.claim_id) or _default_requirement(claim)
        if requirement.required_action_verb != claim.action_verb:
            verdicts.append(_denied_verdict(claim, requirement, "requirement_mismatch"))
            continue
        if (
            requirement.claim_text_digest is not None
            and requirement.claim_text_digest != claim.claim_text_digest
        ):
            verdicts.append(_denied_verdict(claim, requirement, "requirement_mismatch"))
            continue
        matched = tuple(
            receipt
            for receipt in validated_receipts
            if _receipt_satisfies_requirement(receipt, requirement)
        )
        if matched:
            verdicts.append(
                _issued_verdict(
                    claimId=claim.claim_id,
                    claimTextDigest=claim.claim_text_digest,
                    actionVerb=claim.action_verb,
                    verdict="allowed",
                    reasonCode="receipt_match",
                    matchedReceiptRefs=tuple(receipt.receipt_id for receipt in matched),
                    projectedText=f"verified: {claim.action_verb}",
                    requirement=requirement,
                )
            )
        else:
            reason: ResearchActionProofReason = "missing_receipt"
            if validated_receipts:
                reason = "receipt_mismatch"
            verdicts.append(_denied_verdict(claim, requirement, reason))
    return tuple(verdicts)


def project_research_action_proof_verdicts(
    verdicts: Iterable[ResearchActionProofVerdict],
) -> tuple[dict[str, object], ...]:
    return tuple(_validate_verdict(verdict).public_projection() for verdict in verdicts)


def _issued_verdict(**kwargs: object) -> ResearchActionProofVerdict:
    verdict = ResearchActionProofVerdict(**kwargs)
    _mark_verifier_verdict_issued(verdict)
    return verdict


def _mark_runtime_receipt_issued(receipt: ResearchActionProofReceiptRef) -> None:
    object_id = id(receipt)
    receipt.__pydantic_private__["_issued_by_runtime_boundary"] = True
    _RUNTIME_ISSUED_RECEIPT_OBJECT_IDS.add(object_id)
    _RUNTIME_ISSUED_RECEIPT_FINGERPRINTS[object_id] = _model_fingerprint(receipt)
    _RUNTIME_ISSUED_RECEIPT_FINALIZERS[object_id] = finalize(
        receipt,
        _discard_runtime_receipt_object_id,
        object_id,
    )


def _discard_runtime_receipt_object_id(object_id: int) -> None:
    _RUNTIME_ISSUED_RECEIPT_OBJECT_IDS.discard(object_id)
    _RUNTIME_ISSUED_RECEIPT_FINGERPRINTS.pop(object_id, None)
    _RUNTIME_ISSUED_RECEIPT_FINALIZERS.pop(object_id, None)


def _mark_verifier_verdict_issued(verdict: ResearchActionProofVerdict) -> None:
    object_id = id(verdict)
    verdict.__pydantic_private__["_issued_by_action_verifier"] = True
    _VERIFIER_ISSUED_VERDICT_OBJECT_IDS.add(object_id)
    _VERIFIER_ISSUED_VERDICT_FINGERPRINTS[object_id] = _model_fingerprint(verdict)
    _VERIFIER_ISSUED_VERDICT_FINALIZERS[object_id] = finalize(
        verdict,
        _discard_verifier_verdict_object_id,
        object_id,
    )


def _discard_verifier_verdict_object_id(object_id: int) -> None:
    _VERIFIER_ISSUED_VERDICT_OBJECT_IDS.discard(object_id)
    _VERIFIER_ISSUED_VERDICT_FINGERPRINTS.pop(object_id, None)
    _VERIFIER_ISSUED_VERDICT_FINALIZERS.pop(object_id, None)


def _default_requirement(claim: ResearchActionClaim) -> ResearchActionProofRequirement:
    return ResearchActionProofRequirement(
        claimId=claim.claim_id,
        claimTextDigest=claim.claim_text_digest,
        requiredActionVerb=claim.action_verb,
        requiredReceiptKinds=_DEFAULT_RECEIPT_KINDS,
    )


def _denied_verdict(
    claim: ResearchActionClaim,
    requirement: ResearchActionProofRequirement,
    reason_code: ResearchActionProofReason,
) -> ResearchActionProofVerdict:
    return _issued_verdict(
        claimId=claim.claim_id,
        claimTextDigest=claim.claim_text_digest,
        actionVerb=claim.action_verb,
        verdict="denied",
        reasonCode=reason_code,
        matchedReceiptRefs=(),
        projectedText=f"not verified: {claim.action_verb}",
        requirement=requirement,
    )


def _receipt_satisfies_requirement(
    receipt: ResearchActionProofReceiptRef,
    requirement: ResearchActionProofRequirement,
) -> bool:
    if receipt.action_verb != requirement.required_action_verb:
        return False
    if receipt.receipt_kind not in requirement.required_receipt_kinds:
        return False
    if requirement.required_tool_ids and receipt.tool_id not in requirement.required_tool_ids:
        return False
    if requirement.required_source_ids and receipt.source_id not in requirement.required_source_ids:
        return False
    if (
        not requirement.required_tool_ids
        or not requirement.required_source_ids
        or requirement.not_before is None
        or requirement.not_after is None
    ):
        return False
    observed_at = _parse_utcish_datetime(receipt.observed_at, "observedAt")
    if requirement.not_before is not None:
        not_before = _parse_utcish_datetime(requirement.not_before, "notBefore")
        if observed_at < not_before:
            return False
    if requirement.not_after is not None:
        not_after = _parse_utcish_datetime(requirement.not_after, "notAfter")
        if observed_at > not_after:
            return False
    return True


def _validate_claim(value: ResearchActionClaim | Mapping[str, object]) -> ResearchActionClaim:
    if isinstance(value, ResearchActionClaim):
        return ResearchActionClaim.model_validate(
            value.model_dump(by_alias=True, mode="python", warnings=False)
        )
    return ResearchActionClaim.model_validate(value)


def _validate_receipt(
    value: ResearchActionProofReceiptRef,
) -> ResearchActionProofReceiptRef:
    if not isinstance(value, ResearchActionProofReceiptRef):
        raise TypeError("action proof receipts must be runtime-issued receipt objects")
    if not value.is_runtime_boundary_issued:
        raise ValueError("action proof receipts must be issued by the runtime boundary")
    expected = _RUNTIME_ISSUED_RECEIPT_FINGERPRINTS.get(id(value))
    if expected != _model_fingerprint(value):
        raise ValueError("action proof receipts were modified after runtime issuance")
    ResearchActionProofReceiptRef.model_validate(
        value.model_dump(by_alias=True, mode="python", warnings=False)
    )
    return value


def _validate_requirement(
    value: ResearchActionProofRequirement | Mapping[str, object],
) -> ResearchActionProofRequirement:
    if isinstance(value, ResearchActionProofRequirement):
        return ResearchActionProofRequirement.model_validate(
            value.model_dump(by_alias=True, mode="python", warnings=False)
        )
    return ResearchActionProofRequirement.model_validate(value)


def _validate_verdict(
    value: ResearchActionProofVerdict,
) -> ResearchActionProofVerdict:
    if not isinstance(value, ResearchActionProofVerdict):
        raise TypeError("action proof verdicts must be verifier-issued verdict objects")
    if not value.is_action_verifier_issued:
        raise ValueError("action proof verdicts must be issued by the verifier")
    expected = _VERIFIER_ISSUED_VERDICT_FINGERPRINTS.get(id(value))
    if expected != _model_fingerprint(value):
        raise ValueError("action proof verdict was modified after verifier issuance")
    ResearchActionProofVerdict.model_validate(
        value.model_dump(by_alias=True, mode="python", warnings=False)
    )
    return value


def _model_fingerprint(model: BaseModel) -> object:
    return _freeze_for_fingerprint(
        model.model_dump(by_alias=True, mode="python", warnings=False)
    )


def _freeze_for_fingerprint(value: object) -> object:
    if isinstance(value, Mapping):
        return tuple(
            (str(key), _freeze_for_fingerprint(item))
            for key, item in sorted(value.items(), key=lambda entry: str(entry[0]))
        )
    if isinstance(value, tuple | list):
        return tuple(_freeze_for_fingerprint(item) for item in value)
    return value


def _sentences(text: str) -> tuple[str, ...]:
    return tuple(
        match.group(0).strip()
        for match in _SENTENCE_RE.finditer(text)
        if match.group(0).strip()
    )


def _action_verbs_in_sentence(sentence: str) -> tuple[ResearchActionVerb, ...]:
    verbs: list[ResearchActionVerb] = []
    for clause in _ACTION_CLAUSE_PATTERN.finditer(sentence):
        for token in _ACTION_VERB_TOKEN_RE.finditer(clause.group(0)):
            verbs.append(_normalize_verb(token.group(0)))
    if verbs:
        return tuple(verbs)

    match = _ACTION_PATTERN.search(sentence)
    if match is None:
        return ()
    raw_verb = next(group for group in match.groups() if group)
    return (_normalize_verb(raw_verb),)


def _normalize_verb(value: str) -> ResearchActionVerb:
    clean = value.strip().casefold()
    if clean in _GERUND_VERBS:
        return _GERUND_VERBS[clean]
    verb = _VERB_ALIASES.get(clean)
    if verb is None or verb not in _ACTION_VERBS:
        raise ValueError("unsupported research action verb")
    return verb


def _public_ref(value: str, field_name: str) -> str:
    clean = value.strip()
    if not clean:
        raise ValueError(f"{field_name} must be non-empty")
    _reject_unsafe_public_text(clean, field_name)
    if not _PUBLIC_REF_RE.fullmatch(clean):
        raise ValueError(f"{field_name} must be a digest-safe public id")
    return clean


def _safe_kind(value: str, field_name: str) -> str:
    clean = value.strip()
    if not _SAFE_KIND_RE.fullmatch(clean):
        raise ValueError(f"{field_name} must be a digest-safe lower-case public id")
    parts = {part for part in re.split(r"[_.:-]+", clean) if part}
    normalized = re.sub(r"[^a-z0-9]", "", clean)
    if parts & _FORBIDDEN_KIND_PARTS or any(
        fragment in normalized
        for fragment in _FORBIDDEN_KIND_SUBSTRINGS
    ):
        raise ValueError(
            f"{field_name} must not reference raw, model, private, auth, token, or secret data"
        )
    return clean


def _receipt_digest(
    *,
    receipt_id: str,
    action_verb: str,
    receipt_kind: str,
    tool_id: str | None,
    source_id: str | None,
    observed_at: str,
) -> str:
    material = "\n".join(
        (
            "openmagi-research-action-receipt-v1",
            receipt_id.strip(),
            action_verb.strip(),
            receipt_kind.strip(),
            tool_id.strip() if tool_id is not None else "",
            source_id.strip() if source_id is not None else "",
            observed_at.strip(),
            "openmagi_runtime_boundary",
            "toolhost_mediated",
        )
    )
    return "sha256:" + sha256(material.encode("utf-8")).hexdigest()


def _claim_text_digest(claim_text: str) -> str:
    material = "\n".join(
        (
            "openmagi-research-action-claim-text-v1",
            claim_text.strip(),
        )
    )
    return "sha256:" + sha256(material.encode("utf-8")).hexdigest()


def _parse_utcish_datetime(value: str, field_name: str) -> datetime:
    clean = value.strip()
    if not clean:
        raise ValueError(f"{field_name} must be non-empty")
    try:
        parsed = datetime.fromisoformat(clean.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone offset")
    return parsed


def _reject_unsafe_raw_value(value: Mapping[object, object]) -> None:
    for key, item in value.items():
        normalized_key = re.sub(r"[^a-z0-9]", "", str(key).casefold())
        if normalized_key in _FORBIDDEN_RAW_KEY_TOKENS:
            raise ValueError("research action proof inputs must be metadata-only")
        _reject_unsafe_nested_value(item)


def _reject_unsafe_nested_value(value: object) -> None:
    if isinstance(value, str):
        _reject_unsafe_public_text(value, "value")
        return
    if isinstance(value, Mapping):
        _reject_unsafe_raw_value(value)
        return
    if isinstance(value, Iterable) and not isinstance(value, bytes | bytearray):
        for item in value:
            _reject_unsafe_nested_value(item)


def _reject_unsafe_public_text(value: str, field_name: str) -> None:
    if _PRIVATE_PATH_RE.search(value):
        raise ValueError(f"{field_name} must not contain private paths")
    if (
        _SECRET_TEXT_RE.search(value)
        or _UNSAFE_TEXT_RE.search(value)
        or _MODEL_SUMMARY_RE.search(value)
    ):
        raise ValueError(
            f"{field_name} must not contain raw, private, auth, token, or secret data"
        )


__all__ = [
    "ResearchActionClaim",
    "ResearchActionExecutionPosture",
    "ResearchActionProofReason",
    "ResearchActionProofReceiptRef",
    "ResearchActionProofRequirement",
    "ResearchActionProofStatus",
    "ResearchActionProofVerdict",
    "ResearchActionVerb",
    "detect_research_action_claims",
    "project_research_action_proof_verdicts",
    "verify_research_action_claims",
]
