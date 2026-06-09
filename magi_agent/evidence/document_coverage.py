"""Task B — DocumentCoverage evidence boundary for authored documents.

This is the ADK-native realization of the legacy TS "agentic authoring loop /
source-content coverage guard": a deterministic check that the rendered document
actually contains the source content it was supposed to carry.  When a document
tool (e.g. ``docx_write``) renders markdown into a binary document, the boundary
re-extracts the rendered text and measures how many meaningful source units
survived the render round-trip.

The record is metadata-only and public-safe: missing source units are referenced
by sha256 digests (never raw text), and source/document bodies are referenced by
a single content digest each.  The :class:`DocumentCoverageBoundary` is the only
place that can produce a valid :class:`DocumentCoverageRecord` — the model cannot
synthesise it from text alone.

Design contract
---------------
* Coverage MUST be measured against the *redacted* ``safe_source`` produced by
  :func:`magi_agent.web_acquisition.policy.redact_public_text`, NOT the raw input
  (see ``document_write_tools`` docstring).  Comparing against the raw source
  would yield false coverage failures for redacted tokens.
* Default behaviour: the record is ALWAYS built (cheap, deterministic, pure,
  side-effect free).  Blocking enforcement on a ``"failed"`` coverage status is
  separately gated (Task C) — this module never blocks and never raises.
"""

from __future__ import annotations

import hashlib
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from magi_agent.evidence.types import EvidenceRecord, EvidenceSource


DOCUMENT_COVERAGE_EVIDENCE_TYPE: Literal["DocumentCoverage"] = "DocumentCoverage"

_DEFAULT_THRESHOLD = 0.95
# Cap stored missing digests so a wholly-uncovered large source cannot bloat the
# record. The ratio/counts still reflect the true totals.
_MAX_MISSING_DIGESTS = 64

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)

_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")

# Leading markdown block markers to strip so a unit is the literal words.
_LEADING_MARKER_RE = re.compile(r"^\s*(?:#{1,6}\s+|[-*+]\s+|\d+[.)]\s+|>\s+)")
_FENCE_RE = re.compile(r"^\s*```")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{1,}:?\s*(?:\|\s*:?-{1,}:?\s*)+\|?\s*$")
_WHITESPACE_RE = re.compile(r"\s+")
# Inline emphasis markers (``**bold**``/``*italic*``/``_x_``/``` `code` ```) are
# stripped at render time, so strip them for matching too — coverage is measured
# on the literal words, not the markdown punctuation.
_INLINE_MARKER_RE = re.compile(r"[*_`]")


class DocumentCoverageRecord(BaseModel):
    """Evidence record capturing source-content coverage of a rendered document.

    Only sha256 digests and aggregate counts are stored — never raw text.
    """

    model_config = _MODEL_CONFIG

    type: Literal["DocumentCoverage"] = DOCUMENT_COVERAGE_EVIDENCE_TYPE
    total_units: int = Field(ge=0, alias="totalUnits")
    covered_units: int = Field(ge=0, alias="coveredUnits")
    coverage_ratio: float = Field(ge=0.0, le=1.0, alias="coverageRatio")
    threshold: float = Field(ge=0.0, le=1.0)
    missing_unit_digests: tuple[str, ...] = Field(
        default=(),
        alias="missingUnitDigests",
    )
    source_digest: str = Field(alias="sourceDigest")
    doc_digest: str = Field(alias="docDigest")
    status: Literal["pass", "failed"]

    @field_validator("source_digest", "doc_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        if not _DIGEST_RE.fullmatch(value):
            raise ValueError("digest must be sha256:<64 hex chars>")
        return value

    @field_validator("missing_unit_digests")
    @classmethod
    def _validate_missing_digests(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for digest in value:
            if not _DIGEST_RE.fullmatch(digest):
                raise ValueError("missing unit digests must be sha256:<64 hex chars>")
        return value

    def public_projection(self) -> dict[str, object]:
        return {
            "type": self.type,
            "totalUnits": self.total_units,
            "coveredUnits": self.covered_units,
            "coverageRatio": self.coverage_ratio,
            "threshold": self.threshold,
            "missingUnitDigests": list(self.missing_unit_digests),
            "sourceDigest": self.source_digest,
            "docDigest": self.doc_digest,
            "status": self.status,
        }


class DocumentCoverageBoundary:
    """Builds ``DocumentCoverage`` evidence from real source + rendered text.

    Deterministic and pure: never raises, never mutates inputs, and never stores
    raw text — only digests and aggregate counts.
    """

    def build_record(
        self,
        *,
        source_markdown: str,
        doc_text: str,
        threshold: float = _DEFAULT_THRESHOLD,
    ) -> DocumentCoverageRecord:
        """Measure how many source units appear in the rendered document text.

        Parameters
        ----------
        source_markdown:
            The *redacted* ``safe_source`` rendered into the document.  Tokenized
            into meaningful units (non-blank lines with leading markdown markers
            stripped); table-separator and code-fence lines are dropped.
        doc_text:
            The text extracted back out of the rendered document (e.g. via the
            ``_read_docx`` body walk).
        threshold:
            ``status`` is ``"pass"`` iff ``coverage_ratio >= threshold``.
        """
        safe_threshold = _clamp_unit(threshold, default=_DEFAULT_THRESHOLD)
        source_digest = _compute_digest(source_markdown)
        doc_digest = _compute_digest(doc_text)
        normalized_doc = _normalize(doc_text)

        units = _tokenize_units(source_markdown)
        total = len(units)
        if total == 0:
            return DocumentCoverageRecord(
                totalUnits=0,
                coveredUnits=0,
                coverageRatio=1.0,
                threshold=safe_threshold,
                missingUnitDigests=(),
                sourceDigest=source_digest,
                docDigest=doc_digest,
                status="pass",
            )

        covered = 0
        missing_digests: list[str] = []
        for unit in units:
            normalized_unit = _normalize(unit)
            if normalized_unit and normalized_unit in normalized_doc:
                covered += 1
            elif len(missing_digests) < _MAX_MISSING_DIGESTS:
                missing_digests.append(_compute_digest(unit))

        ratio = covered / total
        status: Literal["pass", "failed"] = (
            "pass" if ratio >= safe_threshold else "failed"
        )
        return DocumentCoverageRecord(
            totalUnits=total,
            coveredUnits=covered,
            coverageRatio=ratio,
            threshold=safe_threshold,
            missingUnitDigests=tuple(missing_digests),
            sourceDigest=source_digest,
            docDigest=doc_digest,
            status=status,
        )


def evidence_declaration_from_record(
    record: DocumentCoverageRecord,
    *,
    tool_name: str | None = None,
    tool_call_id: str | None = None,
) -> dict[str, object]:
    """Build an evidence *declaration* dict for ``ToolResult.metadata["evidence"]``.

    A tool surfaces a structured :class:`EvidenceRecord` to the
    ``LocalToolEvidenceCollector`` / verifier-bus by placing this declaration
    under ``metadata["evidence"]``; ``evidence_from_tool_result`` then builds the
    canonical record.  The status mirrors the coverage status so a ``"failed"``
    coverage produces a ``failed`` evidence record (audit-only here; Task C
    decides whether to block).
    """
    source: dict[str, object] = {"kind": "verifier", "verifierName": "document_coverage"}
    if tool_name:
        source["toolName"] = tool_name
    if tool_call_id:
        source["toolCallId"] = tool_call_id
    return {
        "type": DOCUMENT_COVERAGE_EVIDENCE_TYPE,
        "status": "ok" if record.status == "pass" else "failed",
        "fields": record.public_projection(),
        "source": source,
    }


def evidence_record_from_record(
    record: DocumentCoverageRecord,
    *,
    observed_at: int | float = 0,
    tool_name: str | None = None,
    tool_call_id: str | None = None,
) -> EvidenceRecord:
    """Build a canonical :class:`EvidenceRecord` directly from the coverage record.

    Useful for callers/tests that want the typed record without round-tripping
    through ``ToolResult`` metadata.
    """
    source_kwargs: dict[str, object] = {
        "kind": "verifier",
        "verifierName": "document_coverage",
    }
    if tool_name:
        source_kwargs["toolName"] = tool_name
    if tool_call_id:
        source_kwargs["toolCallId"] = tool_call_id
    return EvidenceRecord(
        type=DOCUMENT_COVERAGE_EVIDENCE_TYPE,
        status="ok" if record.status == "pass" else "failed",
        observedAt=observed_at,
        source=EvidenceSource.model_validate(source_kwargs),
        fields=record.public_projection(),
    )


# ---------------------------------------------------------------------------
# Tokenization / normalization helpers
# ---------------------------------------------------------------------------


def _tokenize_units(source: str) -> tuple[str, ...]:
    """Split markdown source into meaningful literal-word units.

    A unit is a non-blank line with leading block markers (``#``/``-``/``*``/
    ``+``/``1.``/``>``) stripped and surrounding table pipes removed.  Code
    fences and table-separator lines are dropped (they carry no source words).
    """
    units: list[str] = []
    for raw_line in source.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        stripped = raw_line.strip()
        if not stripped:
            continue
        if _FENCE_RE.match(raw_line):
            continue
        if _TABLE_SEP_RE.match(raw_line):
            continue
        cleaned = _LEADING_MARKER_RE.sub("", stripped)
        if "|" in cleaned:
            # Table row: each cell is its own unit (matches per-cell rendering).
            cells = [cell.strip() for cell in cleaned.strip("|").split("|")]
            units.extend(cell for cell in cells if cell)
            continue
        if cleaned:
            units.append(cleaned)
    return tuple(units)


def _normalize(text: str) -> str:
    """Normalize for matching: drop inline emphasis markers, collapse whitespace, casefold."""
    without_markers = _INLINE_MARKER_RE.sub("", text)
    return _WHITESPACE_RE.sub(" ", without_markers).strip().casefold()


def _clamp_unit(value: object, *, default: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return default
    numeric = float(value)
    if numeric != numeric:  # NaN
        return default
    if numeric < 0.0:
        return 0.0
    if numeric > 1.0:
        return 1.0
    return numeric


def _compute_digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


__all__ = [
    "DOCUMENT_COVERAGE_EVIDENCE_TYPE",
    "DocumentCoverageBoundary",
    "DocumentCoverageRecord",
    "evidence_declaration_from_record",
    "evidence_record_from_record",
]
