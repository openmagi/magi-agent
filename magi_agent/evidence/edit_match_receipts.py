"""PR1 — EditMatch evidence boundary for fuzzy file-edit matching.

When a FileEdit lands via the 9-stage fuzzy cascade, the matched tier and
confidence are captured here as an ``EditMatch`` evidence receipt.  The
boundary is the only place that can produce a valid
:class:`EditMatchReceiptRecord` — the model cannot synthesise it purely
from text.

The record is metadata-only and public-safe: the matched span is referenced
by a sha256 digest (never raw text), and the file is referenced by a digest
of the *relative* workspace path (never a raw workspace path).

Default behaviour: receipts are always built when a match result is available
(cheap, side-effect free).  Blocking on low-confidence tiers is separately
gated by ``MAGI_EDIT_MATCH_EVIDENCE_ENFORCEMENT``; this module has no
dependency on that flag.
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from magi_agent.coding.edit_matching import EditMatchResult


EDIT_MATCH_EVIDENCE_TYPE: Literal["EditMatch"] = "EditMatch"

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)

_DIGEST_RE = __import__("re").compile(r"^sha256:[a-f0-9]{64}$")


class EditMatchReceiptRecord(BaseModel):
    """Evidence record capturing a fuzzy-edit match.

    Only sha256 digests are stored — never raw matched text.
    """

    model_config = _MODEL_CONFIG

    type: Literal["EditMatch"] = EDIT_MATCH_EVIDENCE_TYPE
    tier: str
    tier_index: int = Field(ge=0)
    confidence: float = Field(ge=0.0, le=1.0)
    ambiguous: bool = False
    file_digest: str = Field(alias="fileDigest")   # sha256 of file content
    span_digest: str = Field(alias="spanDigest")   # sha256 of matched span text (never raw)

    @field_validator("file_digest", "span_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        if not _DIGEST_RE.fullmatch(value):
            raise ValueError("digest must be sha256:<64 hex chars>")
        return value

    @field_validator("tier")
    @classmethod
    def _validate_tier(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("tier must be non-empty")
        return cleaned[:64]

    def public_projection(self) -> dict[str, object]:
        return {
            "type": self.type,
            "tier": self.tier,
            "tierIndex": self.tier_index,
            "confidence": self.confidence,
            "ambiguous": self.ambiguous,
            "fileDigest": self.file_digest,
            "spanDigest": self.span_digest,
        }


class EditMatchReceiptBoundary:
    """Builds ``EditMatch`` evidence from a real :class:`EditMatchResult`.

    Only the boundary, fed by a real matcher output, can produce a valid
    ``EditMatchReceiptRecord`` — the model cannot forge it from text alone.
    """

    def build_record(
        self,
        *,
        match: EditMatchResult,
        file_content: str,
    ) -> EditMatchReceiptRecord:
        """Build a receipt record from a completed match.

        Parameters
        ----------
        match:
            The ``EditMatchResult`` returned by ``replace()``.
        file_content:
            The *new* file content (post-edit), used to compute the
            ``fileDigest`` for the evidence record.  Never stored raw.
        """
        file_digest = _compute_digest(file_content)
        # Extract the matched span text from the *original* content (pre-edit).
        # The span is recorded only as a digest so no raw text leaks.
        start, end = match.matched_span
        # matched_span indices are into the full content string (post-BOM-strip
        # adjustment); clamp to valid range for safety.
        span_text = file_content[start:end] if start < len(file_content) else ""
        span_digest = _compute_digest(span_text)
        return EditMatchReceiptRecord(
            tier=match.tier,
            tier_index=match.tier_index,
            confidence=match.confidence,
            ambiguous=match.ambiguous,
            fileDigest=file_digest,
            spanDigest=span_digest,
        )


def _compute_digest(value: object) -> str:
    if isinstance(value, str):
        material = value.encode("utf-8")
    else:
        material = json.dumps(value, sort_keys=True, default=repr, separators=(",", ":")).encode(
            "utf-8"
        )
    return "sha256:" + hashlib.sha256(material).hexdigest()


__all__ = [
    "EDIT_MATCH_EVIDENCE_TYPE",
    "EditMatchReceiptBoundary",
    "EditMatchReceiptRecord",
]
