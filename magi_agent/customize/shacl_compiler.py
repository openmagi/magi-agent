"""SHACL compiler module -- Tasks 3.1 + 3.2: pure helpers + NL-to-SHACL compiler.

Task 3.1 (this module, no model): ``available_fields()`` and ``preview_cases()``.
Task 3.2 (this module, model_factory): ``compile_nl_to_shacl()``, ``explain_shape()``,
and ``review_compilation()``.

All model-calling functions in Task 3.2:
  - Run at REGISTRATION time only.  The runtime (PR1 SHACL verifier) NEVER calls
    these functions.  They must NEVER be invoked on the hot path.
  - Follow the fail-open / model_factory-injection pattern from
    ``magi_agent.customize.criterion_engine`` and
    ``magi_agent.introspection.egress_gate``:
      * ``model_factory`` is a ``Callable[[], <ADK model>] | None``.
      * ``model_factory is None`` -- graceful degraded result, NEVER raise.
      * Tests inject a fake factory (zero network / real provider calls).
  - Use the ADK async-generator contract for model invocation, delegating to
    ``magi_agent.introspection.egress_gate._invoke_llm`` exactly as
    ``criterion_engine`` does (shared invocation seam).

Plumbing-test limitation (from spec task 3.2):
  Task 3.2 tests verify PLUMBING only -- prompt contains the field menu,
  .ttl is extracted from the response, parse failures trigger retry, reviewer
  parses structured verdicts.  They do NOT verify compile quality (a real model
  is required; CI runs fake-only).

Task 3.1 -- pure, deterministic, zero model/LLM calls.

This module provides two pure functions for the NL-to-SHACL compiler pipeline:

  * ``available_fields()``  -- the "WHAT menu" of usable evidence types and their
    field keys.  Used as compiler-prompt context and dashboard autocomplete source.

  * ``preview_cases()``     -- deterministic SHACL preview: calls ``run_shacl_rule``
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
  * An empty, honest hint is REQUIRED over a guessed or incorrect one -- feeding
    wrong field names into the NL compiler generates ``magi:field_<wrong_key>``
    predicates in SHACL shapes that silently never fire (determinism failure).

``preview_cases()`` running a real shape against sample records is the authoritative
backstop that will surface shapes referencing non-existent fields (violations never
fire -- shape always "passes" even when it shouldn't).  Field hints are a
compile-time aid, not a runtime guarantee.

Spec: docs/plans/2026-06-18-shacl-PR3-compiler-tasks.md Tasks 3.1 and 3.2
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from typing import Any

from magi_agent.evidence.builtin import builtin_evidence_catalog
from magi_agent.evidence.shacl_verifier import run_shacl_rule, validate_shape_ttl
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


# ---------------------------------------------------------------------------
# Task 3.2 — model-calling functions (registration time only, model_factory)
# ---------------------------------------------------------------------------
#
# Model-call interface mirrored from:
#   magi_agent.customize.criterion_engine.evaluate_criterion
#   magi_agent.introspection.egress_gate._invoke_llm
#
# The shared invocation seam is ``_invoke_llm`` (egress_gate), which calls
# ``model.generate_content_async(llm_request, stream=False)`` and collects
# all text parts from the ADK LlmResponse async generator.
#
# Factory resolution order (mirrors egress_critic / criterion_engine):
#   1. Test injection — caller passes a fake factory directly.
#   2. None → fail-open (no model, graceful degraded result, never raise).
#
# ---------------------------------------------------------------------------

# Fence regex for extracting .ttl from model responses (code-fenced or raw).
_FENCE_RE = re.compile(r"```(?:turtle|ttl|text)?|```", re.IGNORECASE)

# Allowed verdict values for review_compilation.
_REVIEW_VERDICTS = frozenset({"aligned", "mismatch", "overbroad", "underbroad", "unknown"})


def _render_fields_menu(fields: list[dict]) -> str:
    """Render the available-fields menu as a compact prompt section.

    Combines caller-supplied ``fields`` with ``available_fields()`` so the model
    always sees the full menu even if the caller passes a partial/empty list.
    """
    # Merge caller-supplied with the full builtin menu (builtin is authoritative).
    builtin_menu = available_fields()
    builtin_by_type = {item["evidenceType"]: item for item in builtin_menu}

    # Overlay caller-supplied items (may include extra context).
    merged: dict[str, dict] = dict(builtin_by_type)
    for item in fields:
        ev_type = item.get("evidenceType", "")
        if ev_type and ev_type not in merged:
            merged[ev_type] = item

    lines: list[str] = ["Available magi:field_<key> predicates by evidence type:"]
    for ev_type in sorted(merged):
        item = merged[ev_type]
        field_list = item.get("fields", [])
        if field_list:
            keys = ", ".join(f"magi:field_{k}" for k in field_list)
            lines.append(f"  {ev_type}: {keys}")
        else:
            lines.append(f"  {ev_type}: (no verified field keys — avoid magi:field_* for this type)")
    return "\n".join(lines)


def _extract_ttl_from_response(text: str) -> str:
    """Extract .ttl content from a model response.

    Handles code-fenced responses (```turtle ... ```, ```ttl ... ```, ``` ... ```)
    as well as raw Turtle text.  Returns the extracted string (may still be invalid
    Turtle — validation is done by validate_shape_ttl).
    """
    text = text.strip()
    # Try to unwrap a code fence.
    if text.startswith("```"):
        lines = text.splitlines()
        # Drop the opening fence line and the closing ``` line (if present).
        inner_lines = lines[1:]
        if inner_lines and inner_lines[-1].strip() == "```":
            inner_lines = inner_lines[:-1]
        text = "\n".join(inner_lines).strip()
    return text


_COMPILE_SYSTEM_INSTRUCTION = (
    "You are a SHACL constraint compiler for an AI agent evidence system. "
    "Given a natural-language constraint description and a menu of available "
    "evidence types and fields, output a valid SHACL Turtle (.ttl) shape that "
    "expresses the constraint using the magi ontology predicates listed. "
    "Output ONLY the Turtle text, optionally in a ```turtle code fence. "
    "Do not include any explanation or prose."
)

_COMPILE_PROMPT_TEMPLATE = """\
Compile the following constraint description into a valid SHACL Turtle shape.

AVAILABLE FIELDS (use ONLY these magi:field_* predicates):
{fields_menu}

CONSTRAINT DESCRIPTION:
{nl_text}

Target class: magi:Evidence
Namespace prefixes to include:
  @prefix sh: <http://www.w3.org/ns/shacl#> .
  @prefix magi: <https://openmagi.ai/ns/evidence#> .
  @prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

Output ONLY valid SHACL Turtle.  Use sh:NodeShape targeting magi:Evidence.
"""

_COMPILE_RETRY_PROMPT_TEMPLATE = """\
The previous SHACL Turtle output was invalid.  Errors:
{errors}

Please correct the shape and output ONLY valid SHACL Turtle.

AVAILABLE FIELDS (use ONLY these magi:field_* predicates):
{fields_menu}

CONSTRAINT DESCRIPTION:
{nl_text}
"""


async def compile_nl_to_shacl(
    nl_text: str,
    fields: list[dict],
    *,
    model_factory: Callable[[], Any] | None,
) -> dict:
    """Compile a natural-language constraint into a SHACL Turtle shape.

    REGISTRATION TIME ONLY.  Never call this from the runtime (PR1 verifier).

    Parameters
    ----------
    nl_text:
        The natural-language description of the constraint.
    fields:
        The available fields menu (from ``available_fields()``).  Injected into
        the prompt so the model uses only real ``magi:field_*`` predicates.
    model_factory:
        Factory returning an ADK model.  When ``None`` → fail-open (compiler
        unavailable), never raises.

    Returns
    -------
    dict
        ``{"ok": True, "shapeTtl": <str>}`` on success.
        ``{"ok": False, "error": <str>, "shapeTtl": None}`` on failure.

    Retry policy
    ------------
    Max 2 total attempts.  On attempt-1 validation failure the errors are fed
    back into the retry prompt.  Persistent failure → ``ok=False``.

    Plumbing-test note
    ------------------
    Tests verify that the prompt contains the field menu and that .ttl is
    extracted + validated.  They do NOT verify semantic compile quality
    (requires a real model; CI runs fake-only).
    """
    if model_factory is None:
        return {"ok": False, "error": "compiler unavailable", "shapeTtl": None}

    try:
        from magi_agent.introspection.egress_gate import _invoke_llm  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"compiler unavailable: {exc}", "shapeTtl": None}

    fields_menu = _render_fields_menu(fields)
    _MAX_ATTEMPTS = 2
    last_errors: list[str] = []

    for attempt in range(_MAX_ATTEMPTS):
        try:
            model = model_factory()
            if model is None:
                return {"ok": False, "error": "compiler unavailable (factory returned None)", "shapeTtl": None}

            if attempt == 0:
                prompt = _COMPILE_PROMPT_TEMPLATE.format(
                    fields_menu=fields_menu,
                    nl_text=nl_text,
                )
            else:
                prompt = _COMPILE_RETRY_PROMPT_TEMPLATE.format(
                    errors="\n".join(last_errors),
                    fields_menu=fields_menu,
                    nl_text=nl_text,
                )

            raw_text = await _invoke_llm(model, prompt)
            ttl = _extract_ttl_from_response(raw_text)
            errors = validate_shape_ttl(ttl)
            if not errors:
                return {"ok": True, "shapeTtl": ttl}
            # Validation failed — store errors for retry prompt.
            last_errors = errors
        except Exception as exc:  # noqa: BLE001
            last_errors = [str(exc)]

    error_msg = "; ".join(last_errors) if last_errors else "compilation failed after retries"
    return {"ok": False, "error": error_msg, "shapeTtl": None}


_EXPLAIN_SYSTEM_INSTRUCTION = (
    "You are a SHACL shape explainer.  Given a SHACL Turtle (.ttl) shape for an AI "
    "agent evidence system, explain in plain language what constraint the shape enforces "
    "and which evidence types / fields it targets.  Be concise and clear."
)

_EXPLAIN_PROMPT_TEMPLATE = """\
Explain the following SHACL Turtle shape in plain natural language:

```turtle
{shape_ttl}
```

Describe what constraint it enforces, which evidence type it targets, and which
fields / conditions it checks.  One to three sentences.
"""


async def explain_shape(
    shape_ttl: str,
    *,
    model_factory: Callable[[], Any] | None,
) -> str:
    """Reverse-explain a SHACL Turtle shape in natural language.

    REGISTRATION TIME ONLY.  Never call this from the runtime (PR1 verifier).

    This is a DIFFERENT model call from ``compile_nl_to_shacl`` — it is used
    after compilation to let operators verify the compiled shape is semantically
    equivalent to their original intent (round-trip confirmation).

    Parameters
    ----------
    shape_ttl:
        SHACL Turtle text to explain.
    model_factory:
        Factory returning an ADK model.  When ``None`` → fallback string,
        never raises.

    Returns
    -------
    str
        Natural-language explanation from the model, or a fallback string when
        the model is unavailable.  Never raises.
    """
    if model_factory is None:
        return "(explanation unavailable — no compiler model configured)"

    try:
        from magi_agent.introspection.egress_gate import _invoke_llm  # noqa: PLC0415

        model = model_factory()
        if model is None:
            return "(explanation unavailable — factory returned None)"

        prompt = _EXPLAIN_PROMPT_TEMPLATE.format(shape_ttl=shape_ttl)
        raw_text = await _invoke_llm(model, prompt)
        return raw_text.strip() if raw_text.strip() else "(explanation unavailable — empty model response)"
    except Exception:  # noqa: BLE001 — fail-open
        return "(explanation unavailable — model error)"


_REVIEW_SYSTEM_INSTRUCTION = (
    "You are an independent SHACL shape reviewer.  Given a natural-language constraint "
    "description and a SHACL Turtle shape, assess whether the shape correctly expresses "
    "the constraint.  Reply with ONLY a JSON object: "
    '{"verdict": "aligned"|"mismatch"|"overbroad"|"underbroad", '
    '"issues": [<string>, ...], "confidence": <float 0.0-1.0>}'
)

_REVIEW_PROMPT_TEMPLATE = """\
Review whether the following SHACL Turtle shape correctly expresses the constraint.

ORIGINAL CONSTRAINT DESCRIPTION:
{nl_text}

AVAILABLE FIELDS (reference):
{fields_menu}

COMPILED SHACL SHAPE:
```turtle
{shape_ttl}
```

Assess whether the shape:
  - "aligned"    — correctly expresses the constraint
  - "mismatch"   — expresses a different constraint
  - "overbroad"  — allows cases that should be blocked
  - "underbroad" — blocks cases that should be allowed

Reply with ONLY a JSON object (no prose):
{{"verdict": "aligned"|"mismatch"|"overbroad"|"underbroad", "issues": ["<issue>", ...], "confidence": <0.0-1.0>}}
"""

_CONSERVATIVE_REVIEW_RESULT: dict = {
    "verdict": "mismatch",
    "issues": ["review model returned an unparseable response"],
    "confidence": 0.0,
}


def _parse_review_response(text: str) -> dict | None:
    """Parse the reviewer's structured JSON response.  Returns None on failure."""
    if not isinstance(text, str):
        return None
    # Strip code fences if present.
    cleaned = _FENCE_RE.sub("", text).strip()
    # Grab the first {...} block.
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        cleaned = cleaned[start:end + 1]
    try:
        parsed = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    verdict = parsed.get("verdict")
    if verdict not in _REVIEW_VERDICTS:
        return None
    issues = parsed.get("issues", [])
    if not isinstance(issues, list):
        issues = []
    confidence = parsed.get("confidence", 0.0)
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    return {
        "verdict": verdict,
        "issues": [str(i) for i in issues],
        "confidence": confidence,
    }


async def review_compilation(
    nl_text: str,
    shape_ttl: str,
    fields: list[dict],
    *,
    model_factory: Callable[[], Any] | None,
) -> dict:
    """Independently review whether a compiled SHACL shape matches a NL constraint.

    REGISTRATION TIME ONLY.  Never call this from the runtime (PR1 verifier).

    This is an INDEPENDENT model call from ``compile_nl_to_shacl`` — a separate
    reviewer checks the compiler's output without sharing context with it.

    Parameters
    ----------
    nl_text:
        The original natural-language constraint description.
    shape_ttl:
        The compiled SHACL Turtle shape to review.
    fields:
        Available fields menu (for reviewer context).
    model_factory:
        Factory returning an ADK model.  When ``None`` → ``{"verdict": "unknown",
        "issues": [], "confidence": 0.0}``, never raises.

    Returns
    -------
    dict
        ``{"verdict": "aligned"|"mismatch"|"overbroad"|"underbroad"|"unknown",
           "issues": [...], "confidence": float}``.

        On parse failure → conservative ``{"verdict": "mismatch", ...}`` (safer
        to flag than to wave through).
        On ``model_factory=None`` → ``{"verdict": "unknown", ...}``.

    Plumbing-test note
    ------------------
    Tests verify JSON parsing and the conservative-on-failure / unknown-on-None
    contracts.  They do NOT verify review quality (requires a real model).
    """
    if model_factory is None:
        return {"verdict": "unknown", "issues": [], "confidence": 0.0}

    try:
        from magi_agent.introspection.egress_gate import _invoke_llm  # noqa: PLC0415

        model = model_factory()
        if model is None:
            return {"verdict": "unknown", "issues": [], "confidence": 0.0}

        fields_menu = _render_fields_menu(fields)
        prompt = _REVIEW_PROMPT_TEMPLATE.format(
            nl_text=nl_text,
            fields_menu=fields_menu,
            shape_ttl=shape_ttl,
        )
        raw_text = await _invoke_llm(model, prompt)
        parsed = _parse_review_response(raw_text)
        if parsed is None:
            # Parse failure → conservative mismatch (safer than flagging as aligned).
            return dict(_CONSERVATIVE_REVIEW_RESULT)
        return parsed
    except Exception:  # noqa: BLE001 — fail-open
        return dict(_CONSERVATIVE_REVIEW_RESULT)


__all__ = [
    "available_fields",
    "preview_cases",
    "compile_nl_to_shacl",
    "explain_shape",
    "review_compilation",
]
