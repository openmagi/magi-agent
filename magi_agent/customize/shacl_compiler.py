"""SHACL compiler module — Task 3.1: pure, deterministic, zero model/LLM calls.

This module provides two pure functions for the NL→SHACL compiler pipeline:

  * ``available_fields()``  — the "WHAT menu" of usable evidence types and their
    field keys.  Used as compiler-prompt context and dashboard autocomplete source.

  * ``preview_cases()``     — deterministic SHACL preview: calls ``run_shacl_rule``
    for each sample record and returns structured results.

Field-level detail note (DESIGN DEVIATION / CONCERN)
-----------------------------------------------------
``BUILTIN_EVIDENCE_TYPES`` (and ``builtin_evidence_catalog()``) expose *metadata*
about each evidence type (description, producer_surfaces, source_kinds) but do NOT
include a static list of ``fields`` keys.  Field keys are attached to
``EvidenceRecord.fields`` at runtime by producers — they are not declared in the
catalog schema.  ``BuiltInEvidenceType`` has no ``fields`` attribute.

As a result, ``available_fields()`` returns ``"fields": []`` for all builtin types.
A richer field menu would require either:
  (a) a hand-authored ``BUILTIN_FIELD_HINTS`` mapping (type → [field, ...]), or
  (b) scanning live EvidenceRecord samples from the ledger at request time.

The compiler prompt can still use the evidenceType names to guide shape targeting
(``sh:targetClass``, ``magi:type`` filter).  The ``fields`` list will be populated
once a field-hints registry is added (Task 3.1 follow-up).

Spec: docs/plans/2026-06-18-shacl-PR3-compiler-tasks.md Task 3.1
"""
from __future__ import annotations

from collections.abc import Sequence

from magi_agent.evidence.builtin import builtin_evidence_catalog
from magi_agent.evidence.shacl_verifier import run_shacl_rule
from magi_agent.evidence.types import EvidenceRecord

# ---------------------------------------------------------------------------
# Optional hand-authored field hints per evidence type.
# These represent the COMMON keys producers emit into EvidenceRecord.fields.
# They are best-effort / illustrative — not exhaustive.  Extend as producers
# are documented.  The compiler uses them to suggest valid magi:field_<key>
# predicate names in generated SHACL shapes.
# ---------------------------------------------------------------------------
_BUILTIN_FIELD_HINTS: dict[str, list[str]] = {
    "GitDiff":                     ["command", "diffSummary", "filesChanged"],
    "TestRun":                     ["command", "exitCode", "passed", "failed", "duration"],
    "CodeDiagnostics":             ["checker", "diagnosticCount", "zeroDiagnostics"],
    "CommitCheckpoint":            ["commitSha", "message", "filesChanged"],
    "FileDeliver":                 ["fileName", "mimeType", "sizeBytes", "artifactRef"],
    "ArtifactVerify":              ["artifactRef", "verified", "digest"],
    "DeterministicEvidenceVerifier": ["ruleId", "passed", "details"],
    "WebSearch":                   ["query", "resultCount", "engine"],
    "KnowledgeSearch":             ["query", "resultCount", "knowledgeBase"],
    "SourceInspection":            ["sourceId", "uri", "kind", "inspected"],
    "PlanVerifier":                ["planId", "passed", "details"],
    "Calculation":                 ["expression", "result", "unit"],
    "DateRange":                   ["startDate", "endDate", "durationDays"],
    "Clock":                       ["timestamp", "timezone"],
    "TelegramDeliveryAck":         ["messageId", "chatId", "deliveredAt"],
    "PromptTransform":             ["hookName", "sectionCount"],
    "EditMatch":                   ["filePath", "matchScore", "matchedSpan"],
    "DocumentCoverage":            ["documentId", "coveredUnits", "totalUnits", "coverage"],
}


def available_fields() -> list[dict]:
    """Return the menu of usable evidence types and their known field keys.

    Each item is::

        {"evidenceType": <str>, "fields": [<field_key>, ...]}

    The list is derived from ``BUILTIN_EVIDENCE_TYPES`` (via
    ``builtin_evidence_catalog()``) and augmented with best-effort field hints
    from ``_BUILTIN_FIELD_HINTS``.

    DESIGN NOTE — field-level detail
    ---------------------------------
    ``BuiltInEvidenceType`` does not carry a static field-key schema; field keys
    are runtime-attached by producers.  ``_BUILTIN_FIELD_HINTS`` is a hand-authored
    best-effort registry of common keys.  Items with no entry have ``fields: []``.
    A richer field menu requires a field-hints registry or live ledger sampling
    (Task 3.1 follow-up — see module docstring).

    Returns
    -------
    list[dict]
        Stable-sorted by ``evidenceType``.  Two calls always return an identical
        result (deterministic, no side effects, no model/LLM calls).
    """
    catalog = builtin_evidence_catalog()
    menu: list[dict] = []
    for item in catalog:
        menu.append(
            {
                "evidenceType": item.type,
                "fields": list(_BUILTIN_FIELD_HINTS.get(item.type, [])),
                "description": item.description,
                "producerSurfaces": list(item.producer_surfaces),
                "sourceKinds": list(item.source_kinds),
            }
        )
    # Stable sort by evidenceType for determinism.
    # (builtin_evidence_catalog() preserves insertion order from the tuple literal,
    # but sorting ensures stability regardless of any future catalog reordering.)
    menu.sort(key=lambda d: d["evidenceType"])
    return menu


def preview_cases(
    shape_ttl: str,
    sample_records: Sequence[EvidenceRecord],
    *,
    observed_at: int,
) -> list[dict]:
    """Run SHACL validation for each sample record and return structured results.

    For each ``EvidenceRecord`` in ``sample_records``, calls ``run_shacl_rule``
    with that single record and returns a summary dict::

        {
            "conforms":   bool | None,   # True/False/None (None = unknown/error)
            "status":     str,            # "ok" | "failed" | "unknown"
            "violations": tuple,          # violation dicts (empty on ok/unknown)
        }

    Parameters
    ----------
    shape_ttl:
        SHACL shape serialised as Turtle text.  A malformed shape causes every
        case to return ``status="unknown"`` — the fail-safe is delegated to
        ``run_shacl_rule``; this function never re-implements validation.
    sample_records:
        Sequence of ``EvidenceRecord`` instances to validate individually.
        Each record is wrapped in a single-element list for ``run_shacl_rule``.
    observed_at:
        Unix-epoch millisecond timestamp injected by the caller.  NEVER
        calls ``time.time()`` or ``datetime.now()`` — determinism requirement.

    Returns
    -------
    list[dict]
        One result dict per input record, in the same order.  Deterministic:
        identical inputs produce identical outputs.  Zero model/LLM calls.

    Fail-safe guarantee
    -------------------
    ``run_shacl_rule`` is itself fail-safe (any internal error → status="unknown").
    This function does NOT catch or re-raise exceptions from ``run_shacl_rule``;
    it relies entirely on that guarantee.  If an unexpected error propagates out
    of ``run_shacl_rule`` (which would be a bug in that layer), it will bubble up
    here — but the contract is that ``run_shacl_rule`` never raises.
    """
    results: list[dict] = []
    for record in sample_records:
        evidence = run_shacl_rule(
            [record],
            shape_ttl,
            rule_id="preview",
            observed_at=observed_at,
        )
        status = evidence.status
        fields = evidence.fields
        conforms: bool | None = fields.get("conforms")  # type: ignore[assignment]
        violations: tuple = fields.get("violations", ())  # type: ignore[assignment]

        results.append(
            {
                "conforms": conforms,
                "status": status,
                "violations": violations,
            }
        )
    return results


__all__ = ["available_fields", "preview_cases"]
