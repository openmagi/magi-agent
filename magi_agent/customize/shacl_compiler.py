"""SHACL compiler module — Task 3.1: pure, deterministic, zero model/LLM calls.

This module provides two pure functions for the NL→SHACL compiler pipeline:

  * ``available_fields()``  — the "WHAT menu" of usable evidence types and their
    field keys.  Used as compiler-prompt context and dashboard autocomplete source.

  * ``preview_cases()``     — deterministic SHACL preview: calls ``run_shacl_rule``
    for each sample record and returns structured results.

Field-level detail
------------------
``_BUILTIN_FIELD_HINTS`` is a best-effort, honest-but-sparse registry of the field
keys that real evidence producers actually emit into ``EvidenceRecord.fields``.

Policy:
  * Every key listed here was verified against the real producer's source code
    (``public_projection()``, ``to_evidence_record()``, or the concrete
    ``fields={}`` dict assigned at emission time).
  * Types whose real producer could NOT be located, or whose field schema could
    not be confirmed with confidence, are listed as ``[]`` (empty).
  * An empty, honest hint is REQUIRED over a guessed or incorrect one — feeding
    wrong field names into the NL compiler generates ``magi:field_<wrong_key>``
    predicates in SHACL shapes that silently never fire (determinism failure).

``preview_cases()`` running a real shape against sample records is the authoritative
backstop that will surface shapes referencing non-existent fields (violations never
fire → shape always "passes" even when it shouldn't).  Field hints are a
compile-time aid, not a runtime guarantee.

Spec: docs/plans/2026-06-18-shacl-PR3-compiler-tasks.md Task 3.1
"""
from __future__ import annotations

from collections.abc import Sequence

from magi_agent.evidence.builtin import builtin_evidence_catalog
from magi_agent.evidence.shacl_verifier import run_shacl_rule
from magi_agent.evidence.types import EvidenceRecord

# ---------------------------------------------------------------------------
# Hand-authored field hints per evidence type — HONEST-BUT-SPARSE.
#
# Policy (see module docstring):
#   - Every key is verified against the real producer's source.
#   - [] means: producer not found or fields not confidently verifiable.
#   - DO NOT add guessed keys.  Wrong keys silently break NL-compiled shapes.
#
# Producer sources verified:
#   GitDiff      — gate5b_full_toolhost._handle_git_diff() returns
#                  {isGitRepo, status, numstat}; gate1a returns
#                  {status, workspaceLooksLikeGit}.  Neither matches the
#                  former hints (command/diffSummary/filesChanged).
#                  contracts.py matches on "changedFiles" but that key comes
#                  from tool metadata, not EvidenceRecord.fields.  → []
#   TestRun      — extraction.py _test_fields_from_projected_event() builds
#                  fields from "command" and "exitCode" keys.  Contracts also
#                  validate exitCode.  "passed"/"failed"/"duration" not found.
#   CodeDiagnostics — code_diagnostics_receipts.py CodeDiagnosticsRecord
#                  .public_projection() → checker, fileDigest, errorCount,
#                  capped, diagnosticsDigest, entries.  diagnosticCount and
#                  zeroDiagnostics never emitted.
#   CommitCheckpoint — plugins/native/coding.py commit_checkpoint() emits
#                  {checkpointDigest, pathRef}.  Former hints (commitSha/
#                  message/filesChanged) never emitted.
#   FileDeliver  — no EvidenceRecord field emission found; delivery metadata
#                  lives in ToolResult, not EvidenceRecord.fields.  → []
#   ArtifactVerify — no EvidenceRecord field emission found.  → []
#   DeterministicEvidenceVerifier — coding_verification.py _audit_evidence()
#                  builds fields={verdictOk, verdictState, enforcement,
#                  matchedEvidenceTypes, missingRequirementTypes, failureCodes,
#                  requiredEvidenceTypes, blockModeEnabled, finalAnswerBlocked}.
#                  Former hints (ruleId/passed/details) never emitted.
#   WebSearch    — source_ledger.py SourceLedgerRecord.to_evidence_record()
#                  emits {sourceId, sourceIds, sourceKind, inspected}.
#                  shadow/research_source_evidence_contract.py also validates
#                  query and resultCount.  "engine" not found.
#   KnowledgeSearch — same source_ledger path as WebSearch.  "knowledgeBase"
#                  not found in any producer.
#   SourceInspection — source_ledger.py SourceLedgerRecord.to_evidence_record()
#                  emits {sourceId, sourceIds, sourceKind, inspected}.
#                  Former "uri"/"kind" not emitted (uri is redacted; kind is
#                  only in SourceLedgerRecord, not EvidenceRecord.fields).
#   PlanVerifier — found only as a catalog type and verifier_bus ref; no
#                  concrete EvidenceRecord producer located.  → []
#   Calculation  — gate1a returns {"value": ...} as raw tool output, not
#                  EvidenceRecord.fields.  expression/result/unit not found.  → []
#   DateRange    — referenced in shadow contract but no concrete producer
#                  found.  → []
#   Clock        — source_ledger kind=clock: to_evidence_record() emits
#                  {sourceId, sourceIds, sourceKind, inspected}.
#                  shadow/research_source_evidence_contract.py also requires
#                  "date" field.  Former hints (timestamp/timezone) not emitted.
#   TelegramDeliveryAck — no real EvidenceRecord field producer found.  → []
#   PromptTransform — runtime/message_builder.py _apply_prompt_transform()
#                  emits {hook_name, sections_modified, tokens_before,
#                  tokens_after} (snake_case, not camelCase).
#                  Former hints (hookName/sectionCount) never emitted.
#   EditMatch    — edit_match_receipts.py EditMatchReceiptRecord.public_projection()
#                  → {type, tier, tierIndex, confidence, ambiguous, fileDigest,
#                  spanDigest}.  Former hints (filePath/matchScore/matchedSpan)
#                  never emitted.
#   DocumentCoverage — document_coverage.py DocumentCoverageRecord.public_projection()
#                  → {type, totalUnits, coveredUnits, coverageRatio, threshold,
#                  missingUnitDigests, sourceDigest, docDigest, status}.
#                  Former hint "documentId" never emitted; "coverage"→coverageRatio.
# ---------------------------------------------------------------------------
_BUILTIN_FIELD_HINTS: dict[str, list[str]] = {
    # Verified against real producers — keys are actually emitted.
    "TestRun":                     ["command", "exitCode"],
    "CodeDiagnostics":             ["checker", "errorCount", "fileDigest", "diagnosticsDigest"],
    "CommitCheckpoint":            ["checkpointDigest", "pathRef"],
    "DeterministicEvidenceVerifier": [
        "verdictOk", "verdictState", "enforcement",
        "matchedEvidenceTypes", "missingRequirementTypes",
        "failureCodes", "requiredEvidenceTypes",
        "blockModeEnabled", "finalAnswerBlocked",
    ],
    "WebSearch":                   ["query", "resultCount", "sourceKind", "sourceIds"],
    "KnowledgeSearch":             ["query", "resultCount", "sourceKind", "sourceIds"],
    "SourceInspection":            ["sourceId", "sourceIds", "sourceKind", "inspected"],
    "Clock":                       ["sourceKind", "date"],
    "PromptTransform":             ["hook_name", "sections_modified", "tokens_before", "tokens_after"],
    "EditMatch":                   ["tier", "tierIndex", "confidence", "ambiguous", "fileDigest", "spanDigest"],
    "DocumentCoverage":            ["totalUnits", "coveredUnits", "coverageRatio", "threshold", "status", "sourceDigest", "docDigest"],
    # Producer not found or fields not confidently verifiable — honest empty hint.
    "GitDiff":                     [],
    "FileDeliver":                 [],
    "ArtifactVerify":              [],
    "PlanVerifier":                [],
    "Calculation":                 [],
    "DateRange":                   [],
    "TelegramDeliveryAck":         [],
}


def available_fields() -> list[dict]:
    """Return the menu of usable evidence types and their known field keys.

    Each item is::

        {"evidenceType": <str>, "fields": [<field_key>, ...]}

    The list is derived from ``BUILTIN_EVIDENCE_TYPES`` (via
    ``builtin_evidence_catalog()``) and augmented with field hints from
    ``_BUILTIN_FIELD_HINTS``.

    DESIGN NOTE — field-level detail
    ---------------------------------
    ``_BUILTIN_FIELD_HINTS`` is an honest-but-sparse registry: every key listed
    was verified against the real producer's source code.  Types whose real
    producer could not be located, or whose field schema could not be confirmed,
    are listed with ``fields: []``.  An empty, honest hint is REQUIRED over a
    guessed one — wrong field names cause the NL→SHACL compiler to generate
    ``magi:field_<wrong_key>`` predicates that silently never fire.

    ``preview_cases()`` (running the real shape against sample records) is the
    authoritative backstop that catches shapes referencing non-existent fields.

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
