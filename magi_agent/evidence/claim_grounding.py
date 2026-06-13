from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
import re
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

from magi_agent.research.grounded_answer_guard import (
    GroundedAnswerStatus,
    evaluate_answer_grounding,
)


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


# ---------------------------------------------------------------------------
# Fact-grounding evidence producer — live wiring for the grounding guard.
#
# Turns the pure, side-effect-free
# ``magi_agent.research.grounded_answer_guard.evaluate_answer_grounding`` detector
# into something the live pre-final evidence gate (``cli.engine``) can consume.
# The detector answers one general agent-honesty question deterministically:
# does the committed answer assert a *specific* numeric/identifier value that is
# NOT supported anywhere in the tool/evidence corpus the agent actually
# collected? ``grounded`` means supported (or no specific value to ground — the
# G4 boundary); ``guess`` means a specific value with no corroborating evidence.
#
# This producer is the harness-side adapter: it harvests a ``tool_corpus`` from
# the turn's already-collected evidence records, runs the detector, and exposes
# the bare required-validator label the gate should treat as satisfied — but only
# on a grounded verdict. It performs no I/O, no network, no model call; flag
# gating lives entirely in the engine caller
# (``MAGI_FACT_GROUNDING_VERIFICATION_ENABLED``).
# ---------------------------------------------------------------------------

# The research recipe's ``required_validators`` carries the bare label
# ``fact_grounding`` (NOT a ``verifier:`` ref). The engine gate counts a
# required-validator as satisfied only when the exact same string appears in
# ``observed_public_refs``. So the label the producer satisfies on a grounded
# verdict MUST be exactly this bare label — see the gate wiring in cli/engine.py
# and ``test_producer_matched_label_is_the_research_requirement_label``.
FACT_GROUNDING_REQUIREMENT_LABEL = "fact_grounding"

# Bound how deep we walk a record's nested mappings/sequences when harvesting
# corpus strings (mirrors the depth guards in the public-ref collectors).
_MAX_CORPUS_DEPTH = 6


@dataclass(frozen=True)
class FactGroundingVerdict:
    """Result of grounding a committed answer against the collected corpus.

    ``satisfied_label`` is the bare required-validator the engine gate should
    treat as satisfied — present ONLY on a grounded verdict, ``None`` on a guess
    so the requirement stays missing and the gate blocks.
    """

    status: GroundedAnswerStatus
    reason_code: str
    extracted_value: str | None
    satisfied_label: str | None

    @property
    def grounded(self) -> bool:
        return self.status == "grounded"


class FactGroundingEvidenceProducer:
    """Adapter that grounds a final answer against the turn's evidence corpus."""

    def __init__(self, *, requirement_label: str = FACT_GROUNDING_REQUIREMENT_LABEL) -> None:
        self._requirement_label = requirement_label

    def evaluate(
        self,
        *,
        final_text: str,
        evidence_records: Sequence[object],
    ) -> FactGroundingVerdict:
        """Decide whether ``final_text`` is grounded in the collected corpus.

        Pure: builds the corpus from the records' readable strings, runs the
        deterministic detector, and projects the verdict. The satisfied label is
        attached only on a grounded verdict.
        """
        corpus = corpus_from_evidence_records(evidence_records)
        verdict = evaluate_answer_grounding(final_text, corpus)
        return FactGroundingVerdict(
            status=verdict.status,
            reason_code=verdict.reason_code,
            extracted_value=verdict.extracted_value,
            satisfied_label=(
                self._requirement_label if verdict.status == "grounded" else None
            ),
        )

    def satisfied_requirement_labels(
        self,
        *,
        final_text: str,
        evidence_records: Sequence[object],
    ) -> tuple[str, ...]:
        """Bare required-validator labels satisfied by a grounded verdict.

        Empty on a guess (so ``fact_grounding`` stays missing and the gate
        blocks); a single-element tuple with the requirement label on grounded.
        This is the shape the engine gate folds into ``observed_public_refs``.
        """
        verdict = self.evaluate(final_text=final_text, evidence_records=evidence_records)
        return (verdict.satisfied_label,) if verdict.satisfied_label is not None else ()


def corpus_from_evidence_records(evidence_records: Sequence[object]) -> tuple[str, ...]:
    """Harvest the readable string content of evidence records into a corpus.

    Reads each record's ``preview`` plus the string values nested under its
    ``fields`` and ``metadata`` mappings (the public, non-raw projections). A
    record exposing ``model_dump`` is dumped first (camel-aliased) so both
    pydantic ``EvidenceRecord`` instances and plain mapping projections work.
    Never raises: an unreadable record contributes nothing.
    """
    corpus: list[str] = []
    for record in evidence_records:
        _collect_corpus_strings(_record_view(record), corpus, depth=0)
    # De-dup while preserving order; drop blanks.
    return tuple(dict.fromkeys(item for item in corpus if item.strip()))


def _record_view(record: object) -> object:
    if isinstance(record, Mapping):
        return record
    model_dump = getattr(record, "model_dump", None)
    if callable(model_dump):
        try:
            return model_dump(by_alias=True, mode="python", warnings=False)
        except Exception:
            try:
                return model_dump()
            except Exception:
                return {}
    # Fall back to the handful of string-bearing attributes we know about.
    view: dict[str, object] = {}
    for attr in ("preview", "fields", "metadata"):
        value = getattr(record, attr, None)
        if value is not None:
            view[attr] = value
    return view


def _collect_corpus_strings(value: object, corpus: list[str], *, depth: int) -> None:
    if depth > _MAX_CORPUS_DEPTH:
        return
    if isinstance(value, str):
        corpus.append(value)
        return
    if isinstance(value, bool):
        return
    if isinstance(value, int | float):
        corpus.append(str(value))
        return
    if isinstance(value, Mapping):
        for nested in value.values():
            _collect_corpus_strings(nested, corpus, depth=depth + 1)
        return
    if isinstance(value, list | tuple | set | frozenset):
        for nested in value:
            _collect_corpus_strings(nested, corpus, depth=depth + 1)


__all__ = [
    "AtomicClaim",
    "CitationRef",
    "ClaimProjectionEligibility",
    "FACT_GROUNDING_REQUIREMENT_LABEL",
    "FactGroundingEvidenceProducer",
    "FactGroundingVerdict",
    "corpus_from_evidence_records",
    "validate_claim_projection_eligibility",
]
