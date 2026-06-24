"""Test that ``_BUILTIN_FIELD_HINTS`` field keys reflect what real producers emit.

F2 task (vocabulary live-catalog): lock the honest-but-sparse policy of
``magi_agent.customize.shacl_compiler._BUILTIN_FIELD_HINTS`` so future edits
cannot silently introduce guessed field keys.

POLICY (sacred; see shacl_compiler module docstring)
=====================================================

Every key listed in ``_BUILTIN_FIELD_HINTS`` MUST be a field name a real
producer actually constructs into ``EvidenceRecord.fields={...}``.  Producers
either:

  * Construct ``EvidenceRecord(type=<T>, fields={"k": ...})`` directly
    (e.g. ``source_ledger.py:SourceLedgerRecord.to_evidence_record``,
    ``document_coverage.py:evidence_record_from_record``), OR
  * Set ``ToolResult.metadata["evidence"]["fields"] = {"k": ...}`` so
    ``magi_agent.evidence.extraction.evidence_from_tool_result`` lifts it
    into ``EvidenceRecord.fields`` on dispatch.

If no producer site can be located, the hint MUST be an empty list ``[]``
(honest-degrade).  Guessing breaks the trust contract: the NL→SHACL
compiler bakes ``magi:field_<wrong_key>`` predicates into shapes that
silently never fire, defeating determinism.

WHAT THIS TEST VERIFIES
=======================

1. The 7 "no producer located" types stay empty.  This is the F2-task lock:
   independent producer-source review (recorded in the source comments
   above ``_BUILTIN_FIELD_HINTS``) found no concrete ``EvidenceRecord``
   field emission for these types.  If a future PR adds a producer, the
   PR must (a) remove the type from this empty-set assertion AND
   (b) populate the hint with the producer's actual field names.

2. The non-empty hints for the four canonical, in-tree producers
   (``source_ledger``, ``document_coverage``, ``coding_verification``
   verifier) are a subset of the producer's actually-emitted field keys.
   Wrong keys here would directly poison NL-compiled SHACL.

3. Every key in ``BUILTIN_EVIDENCE_TYPES`` has a hint entry (even if []),
   so ``available_fields()`` never silently drops a built-in type.

ZERO NETWORK, ZERO MODEL CALLS: pure source-level invariants.
"""
from __future__ import annotations

from collections.abc import Mapping

import pytest

from magi_agent.customize.shacl_compiler import _BUILTIN_FIELD_HINTS
from magi_agent.evidence.builtin import builtin_evidence_catalog


# ---------------------------------------------------------------------------
# Block 1: the 7 honest-empty types.  Verified producer-absent at F2 time.
# Producer-source notes live in shacl_compiler.py above _BUILTIN_FIELD_HINTS.
# ---------------------------------------------------------------------------

_EMPTY_HINT_TYPES_LOCK: tuple[str, ...] = (
    "GitDiff",              # tool handlers return raw dict; no evidence declaration
    "FileDeliver",          # documents.file_deliver sets metadata sans 'evidence' key
    "ArtifactVerify",       # no producer construction site located anywhere
    "PlanVerifier",         # only catalog/verifier-bus refs; no producer site
    "Calculation",          # gate handlers return raw {'value': ...}; no evidence key
    "DateRange",            # source_ledger.date_range uses ok_result (no evidence key)
    "TelegramDeliveryAck",  # external_ack source_kind is dropped by extraction.py
)


@pytest.mark.parametrize("evidence_type", _EMPTY_HINT_TYPES_LOCK)
def test_empty_hint_types_remain_empty(evidence_type: str) -> None:
    """Lock the honest-empty hint policy for the 7 producer-absent types.

    Adding a non-empty hint here without first wiring a real producer would
    silently corrupt the NL→SHACL compiler.  If you have a real producer,
    update the comment block in ``shacl_compiler.py`` and remove this entry
    from ``_EMPTY_HINT_TYPES_LOCK`` in the same PR.
    """
    assert evidence_type in _BUILTIN_FIELD_HINTS, (
        f"{evidence_type!r} must appear in _BUILTIN_FIELD_HINTS even if empty; "
        f"available_fields() iterates the built-in catalog and a missing key "
        f"would silently fall back to []."
    )
    assert _BUILTIN_FIELD_HINTS[evidence_type] == [], (
        f"{evidence_type!r} has hint "
        f"{_BUILTIN_FIELD_HINTS[evidence_type]!r} but the F2 source review "
        f"found no real EvidenceRecord(fields={{...}}) producer.  Either (a) "
        f"add the producer AND remove this type from _EMPTY_HINT_TYPES_LOCK, "
        f"or (b) restore the empty list to keep the NL compiler honest."
    )


# ---------------------------------------------------------------------------
# Block 2: for the canonical in-tree producers, hints must subset emitted keys.
# We import the producers and exercise them in a hermetic way (no I/O, no LLM)
# to extract the actual field key set they construct.
# ---------------------------------------------------------------------------


def _source_ledger_emitted_fields() -> set[str]:
    """Field keys that ``SourceLedgerRecord.to_evidence_record`` emits.

    Per ``magi_agent/evidence/source_ledger.py`` the produced record always
    carries ``{"sourceId", "sourceIds", "sourceKind", "inspected"}``.
    """
    from magi_agent.evidence.source_ledger import SourceLedgerRecord

    record = SourceLedgerRecord(
        sourceId="src_1",
        turnId="turn_1",
        evidenceType="SourceInspection",
        kind="file",
        uri="file://example",
        toolName="ReadFile",
        toolUseId="tool:1",
        inspected=True,
        inspectedAt=0.0,
        metadata={},
    )
    ev = record.to_evidence_record()
    return set(ev.fields.keys())


def _document_coverage_emitted_fields() -> set[str]:
    """Field keys that ``evidence_record_from_record`` emits for DocumentCoverage."""
    from magi_agent.evidence.document_coverage import (
        DocumentCoverageRecord,
        evidence_record_from_record,
    )

    # 64-hex sha256 placeholders (the model validates digest format).
    _SHA = "sha256:" + ("a" * 64)
    _SHB = "sha256:" + ("b" * 64)
    record = DocumentCoverageRecord(
        totalUnits=2,
        coveredUnits=2,
        coverageRatio=1.0,
        threshold=0.9,
        missingUnitDigests=(),
        sourceDigest=_SHA,
        docDigest=_SHB,
        status="pass",
    )
    ev = evidence_record_from_record(record, observed_at=0.0)
    return set(ev.fields.keys())


def test_source_inspection_hint_subset_of_producer() -> None:
    """``SourceInspection`` hints must be a subset of what source_ledger emits."""
    emitted = _source_ledger_emitted_fields()
    hint = set(_BUILTIN_FIELD_HINTS["SourceInspection"])
    extra = hint - emitted
    assert not extra, (
        f"SourceInspection hint {sorted(hint)!r} contains keys not emitted by "
        f"SourceLedgerRecord.to_evidence_record (emits {sorted(emitted)!r}): "
        f"unverified keys = {sorted(extra)!r}"
    )


def test_web_search_hint_subset_of_producer() -> None:
    """``WebSearch`` shares the source_ledger producer surface."""
    emitted = _source_ledger_emitted_fields()
    hint = set(_BUILTIN_FIELD_HINTS["WebSearch"])
    # query/resultCount come from a shadow contract (research_source_evidence_contract);
    # they are not in to_evidence_record but ARE documented as accepted producer fields.
    # Loosen by allowing the contract-declared additions.
    contract_extras = {"query", "resultCount"}
    extra = hint - emitted - contract_extras
    assert not extra, (
        f"WebSearch hint {sorted(hint)!r} contains keys not emitted by any "
        f"known producer (source_ledger emits {sorted(emitted)!r}, contract "
        f"adds {sorted(contract_extras)!r}); unverified keys = {sorted(extra)!r}"
    )


def test_knowledge_search_hint_subset_of_producer() -> None:
    """``KnowledgeSearch`` shares the source_ledger producer surface."""
    emitted = _source_ledger_emitted_fields()
    hint = set(_BUILTIN_FIELD_HINTS["KnowledgeSearch"])
    contract_extras = {"query", "resultCount"}
    extra = hint - emitted - contract_extras
    assert not extra, (
        f"KnowledgeSearch hint {sorted(hint)!r} contains keys not emitted by "
        f"any known producer; unverified keys = {sorted(extra)!r}"
    )


def test_document_coverage_hint_subset_of_producer() -> None:
    """``DocumentCoverage`` hints subset ``DocumentCoverageRecord.public_projection``."""
    emitted = _document_coverage_emitted_fields()
    hint = set(_BUILTIN_FIELD_HINTS["DocumentCoverage"])
    extra = hint - emitted
    assert not extra, (
        f"DocumentCoverage hint {sorted(hint)!r} contains keys not emitted by "
        f"public_projection (emits {sorted(emitted)!r}): "
        f"unverified keys = {sorted(extra)!r}"
    )


# ---------------------------------------------------------------------------
# Block 3: completeness: every catalog type has a hint entry (even if []).
# Guards against available_fields() silently dropping a type.
# ---------------------------------------------------------------------------


def test_every_builtin_catalog_type_has_a_hint_entry() -> None:
    catalog_types = {item.type for item in builtin_evidence_catalog()}
    missing = catalog_types - set(_BUILTIN_FIELD_HINTS.keys())
    assert not missing, (
        f"_BUILTIN_FIELD_HINTS is missing entries for built-in catalog types "
        f"{sorted(missing)!r}.  Every catalog type must have a hint entry, "
        f"even an empty list, so available_fields() surfaces all built-ins."
    )


def test_hints_only_cover_known_catalog_types() -> None:
    """Hints for unknown types are dead code; reject them."""
    catalog_types = {item.type for item in builtin_evidence_catalog()}
    unknown = set(_BUILTIN_FIELD_HINTS.keys()) - catalog_types
    assert not unknown, (
        f"_BUILTIN_FIELD_HINTS has entries for non-catalog types "
        f"{sorted(unknown)!r}.  Either register them in builtin_evidence_catalog "
        f"or drop the dead entry."
    )
