from __future__ import annotations

from collections.abc import Iterable, Mapping
import re
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator


SupportStatus = Literal["supported", "weak", "unverifiable", "contradicted", "not_checked", "failed"]
ClaimType = Literal[
    "numeric",
    "date",
    "numeric_date",
    "comparison",
    "superlative",
    "quote",
    "causal",
    "compound",
    "other",
]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)
_DIGEST_PREFIX = "sha256:"
_SAFE_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{1,180}$")
_PROTECTED_FRAGMENTS = (
    "author" + "ization",
    "coo" + "kie",
    "to" + "ken",
    "se" + "cret",
    "api_" + "key",
    "pass" + "word",
    "pro" + "mpt",
    "sess" + "ion",
    "priv" + "ate",
    "bearer",
    "credential",
    "auth",
    "oauth",
)
_RAW_MARKERS = (
    "raw:",
    "rawref",
    "rawtoollog",
    "rawchildtranscript",
    "childrawtoollog",
    "rawoutput",
    "rawresult",
    "hiddenreasoning",
    "privatememory",
)
_PROTECTED_COMPACT_MARKERS = tuple(
    "".join(character for character in marker if character.isalnum())
    for marker in _PROTECTED_FRAGMENTS + _RAW_MARKERS
)
_PATHLIKE_COMPACT_MARKERS = ("users", "home", "ssh", "idrsa", "env", "kube", "kubeconfig", "varlib", "databots")
_REASON_CODES = (
    "unsupported_claim_not_renderable",
    "compound_claim_must_split_or_reject",
    "citation_ref_missing",
    "citation_source_not_opened",
)


class _FrozenNoUpdateModel(BaseModel):
    model_config = _MODEL_CONFIG

    def model_copy(self, *, update: Mapping[str, object] | None = None, deep: bool = False) -> Self:
        if update:
            raise ValueError("model_copy update is disabled for claim grounding contracts")
        _ = deep
        return type(self).model_validate(self.model_dump(by_alias=True, mode="json"))


class CitationRef(_FrozenNoUpdateModel):
    source_ref: str = Field(alias="sourceRef")
    snapshot_ref: str = Field(alias="snapshotRef")
    content_digest: str = Field(alias="contentDigest")
    span_ref: str = Field(alias="spanRef")
    quote_digest: str | None = Field(default=None, alias="quoteDigest")
    opened_proof: StrictBool = Field(alias="openedProof")
    fetched_at: str = Field(alias="fetchedAt")
    source_date: str | None = Field(default=None, alias="sourceDate")

    @field_validator("source_ref", "snapshot_ref", "span_ref")
    @classmethod
    def _validate_ref(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "citation ref")
        return _safe_ref(value, field_name=field_name)

    @field_validator("fetched_at")
    @classmethod
    def _validate_fetched_at(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("fetchedAt must be non-empty")
        _reject_private_text(value, "fetchedAt")
        return value

    @field_validator("source_date")
    @classmethod
    def _validate_source_date(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip():
            raise ValueError("sourceDate must be non-empty when provided")
        _reject_private_text(value, "sourceDate")
        return value

    @field_validator("content_digest", "quote_digest")
    @classmethod
    def _validate_optional_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_digest(value)


class AtomicClaim(_FrozenNoUpdateModel):
    claim_id: str = Field(alias="claimId")
    text: str
    claim_type: ClaimType = Field(alias="claimType")
    support_status: SupportStatus = Field(alias="supportStatus")
    citation_refs: tuple[CitationRef, ...] = Field(default=(), alias="citationRefs")

    @field_validator("claim_id")
    @classmethod
    def _validate_claim_id(cls, value: str) -> str:
        return _safe_ref(value, field_name="claimId")

    @field_validator("text")
    @classmethod
    def _validate_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("claim text must be non-empty")
        _reject_private_text(value, "claim text")
        return value

    @field_validator("citation_refs", mode="before")
    @classmethod
    def _normalize_citation_refs(cls, value: object) -> tuple[CitationRef, ...]:
        if value is None:
            return ()
        if isinstance(value, str) or not isinstance(value, Iterable):
            raise ValueError("citationRefs must be an array")
        return tuple(value)  # type: ignore[arg-type]


class ClaimProjectionEligibility(_FrozenNoUpdateModel):
    ok: StrictBool
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")

    @field_validator("reason_codes")
    @classmethod
    def _validate_reason_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for reason_code in value:
            if reason_code not in _REASON_CODES:
                raise ValueError("reasonCodes must be canonical claim grounding reason codes")
        return value


def validate_claim_projection_eligibility(claims: tuple[AtomicClaim, ...]) -> ClaimProjectionEligibility:
    reasons: list[str] = []
    for claim in claims:
        if claim.claim_type == "compound":
            reasons.append("compound_claim_must_split_or_reject")
        if claim.support_status != "supported":
            reasons.append("unsupported_claim_not_renderable")
        if not claim.citation_refs:
            reasons.append("citation_ref_missing")
        if any(not citation.opened_proof for citation in claim.citation_refs):
            reasons.append("citation_source_not_opened")
    return ClaimProjectionEligibility(ok=not reasons, reasonCodes=tuple(dict.fromkeys(reasons)))


def _require_digest(value: str) -> str:
    suffix = value.removeprefix(_DIGEST_PREFIX)
    if not value.startswith(_DIGEST_PREFIX) or len(suffix) != 64 or any(
        char not in "0123456789abcdef" for char in suffix
    ):
        raise ValueError("citation digests must be sha256 digests")
    return value


def _safe_ref(value: str, *, field_name: str) -> str:
    clean = value.strip()
    if not clean or not _SAFE_REF_RE.fullmatch(clean):
        raise ValueError(f"{field_name} must be non-empty safe public reference")
    lowered = clean.lower()
    compact = "".join(character for character in lowered if character.isalnum())
    if (
        any(fragment in lowered for fragment in _PROTECTED_FRAGMENTS)
        or any(marker in lowered for marker in _RAW_MARKERS)
        or any(marker in compact for marker in _PROTECTED_COMPACT_MARKERS)
        or _looks_path_like(clean, compact)
        or "/" in clean
        or "\\" in clean
        or clean.startswith(("~", "."))
    ):
        raise ValueError(f"{field_name} contains protected runtime data marker")
    return clean


def _reject_private_text(value: str, field_name: str) -> None:
    lowered = value.lower()
    compact = "".join(character for character in lowered if character.isalnum())
    if (
        any(fragment in lowered for fragment in _PROTECTED_FRAGMENTS)
        or any(marker in lowered for marker in _RAW_MARKERS)
        or any(marker in compact for marker in _PROTECTED_COMPACT_MARKERS)
        or _looks_path_like(value, compact)
        or "/" in value
        or "\\" in value
        or value.strip().startswith(("~", "."))
    ):
        raise ValueError(f"{field_name} contains protected runtime data marker")


def _looks_path_like(value: str, compact: str) -> bool:
    if not any(sep in value for sep in (":", ".", "-")):
        return False
    if "users" in compact or "home" in compact:
        return True
    return any(
        marker in compact
        for marker in ("ssh", "idrsa", "kube", "kubeconfig", "varlib", "databots", "etcpasswd")
    ) or ("passwd" in compact and "etc" in compact) or (
        "env" in compact and any(marker in compact for marker in _PATHLIKE_COMPACT_MARKERS)
    )
