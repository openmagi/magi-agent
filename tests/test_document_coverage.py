"""Tests for the deterministic DocumentCoverage evidence boundary (Task B).

The boundary measures how many meaningful source units survive a document
render round-trip and emits a digest-only record. It must be pure, deterministic,
never raise, and never store raw text.
"""

from __future__ import annotations

import re

from magi_agent.evidence.document_coverage import (
    DOCUMENT_COVERAGE_EVIDENCE_TYPE,
    DocumentCoverageBoundary,
    DocumentCoverageRecord,
    evidence_declaration_from_record,
    evidence_record_from_record,
)
from magi_agent.evidence.types import EvidenceRecord


_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")


def _assert_no_raw_text(record: DocumentCoverageRecord, *, forbidden: list[str]) -> None:
    """The record (and its projection) must contain only digests + numbers."""
    projection = record.public_projection()
    blob = repr(projection) + repr(record.model_dump(by_alias=True))
    for token in forbidden:
        assert token not in blob, f"raw source text {token!r} leaked into record"
    # missing digests are sha256-shaped, nothing else.
    for digest in record.missing_unit_digests:
        assert _DIGEST_RE.fullmatch(digest)
    assert _DIGEST_RE.fullmatch(record.source_digest)
    assert _DIGEST_RE.fullmatch(record.doc_digest)


class TestFullCoverage:
    def test_doc_contains_all_source_lines(self) -> None:
        source = "Alpha line\nBeta line\nGamma line"
        doc = "Alpha line\n\nBeta line\n\nGamma line"
        record = DocumentCoverageBoundary().build_record(
            source_markdown=source, doc_text=doc
        )
        assert record.total_units == 3
        assert record.covered_units == 3
        assert record.coverage_ratio == 1.0
        assert record.status == "pass"
        assert record.missing_unit_digests == ()


class TestPartialCoverage:
    def test_missing_lines_produce_failed_status_and_digests(self) -> None:
        source = "Kept one\nKept two\nDropped three\nDropped four"
        doc = "Kept one\nKept two"
        record = DocumentCoverageBoundary().build_record(
            source_markdown=source, doc_text=doc
        )
        assert record.total_units == 4
        assert record.covered_units == 2
        assert record.coverage_ratio == 0.5
        assert record.status == "failed"
        # Two missing units → two digests, in source order.
        assert len(record.missing_unit_digests) == 2
        _assert_no_raw_text(
            record,
            forbidden=["Dropped three", "Dropped four", "Kept one"],
        )


class TestMarkdownMarkerStripping:
    def test_heading_marker_does_not_break_matching(self) -> None:
        # Source carries a `# Heading`; the rendered doc only has the words.
        source = "# Heading\n\nBody paragraph"
        doc = "Heading\nBody paragraph"
        record = DocumentCoverageBoundary().build_record(
            source_markdown=source, doc_text=doc
        )
        assert record.total_units == 2
        assert record.covered_units == 2
        assert record.status == "pass"

    def test_bullet_ordered_and_table_markers_stripped(self) -> None:
        source = (
            "## Items\n"
            "- First bullet\n"
            "* Second bullet\n"
            "1. First step\n"
            "| Metric | Value |\n"
            "| --- | --- |\n"
            "| Revenue | 1000 |\n"
        )
        # Rendered text has the literal words, no markdown markers, separator gone.
        doc = "Items\nFirst bullet\nSecond bullet\nFirst step\nMetric\nValue\nRevenue\n1000"
        record = DocumentCoverageBoundary().build_record(
            source_markdown=source, doc_text=doc
        )
        # Separator line is dropped; table cells become individual units.
        assert record.covered_units == record.total_units
        assert record.status == "pass"


class TestThresholdBehavior:
    def test_below_threshold_fails(self) -> None:
        source = "a\nb\nc\nd"
        doc = "a\nb\nc"  # 0.75 coverage
        record = DocumentCoverageBoundary().build_record(
            source_markdown=source, doc_text=doc, threshold=0.95
        )
        assert record.coverage_ratio == 0.75
        assert record.status == "failed"

    def test_lower_threshold_passes_same_doc(self) -> None:
        source = "a\nb\nc\nd"
        doc = "a\nb\nc"  # 0.75 coverage
        record = DocumentCoverageBoundary().build_record(
            source_markdown=source, doc_text=doc, threshold=0.5
        )
        assert record.coverage_ratio == 0.75
        assert record.status == "pass"

    def test_exact_threshold_boundary_passes(self) -> None:
        source = "a\nb\nc\nd"
        doc = "a\nb\nc"  # 0.75 coverage
        record = DocumentCoverageBoundary().build_record(
            source_markdown=source, doc_text=doc, threshold=0.75
        )
        assert record.status == "pass"

    def test_out_of_range_threshold_is_clamped(self) -> None:
        record = DocumentCoverageBoundary().build_record(
            source_markdown="a\nb", doc_text="a\nb", threshold=5.0
        )
        assert 0.0 <= record.threshold <= 1.0
        assert record.status == "pass"


class TestEmptySourceEdge:
    def test_total_zero_passes_with_ratio_one(self) -> None:
        record = DocumentCoverageBoundary().build_record(
            source_markdown="   \n\n  ", doc_text="anything"
        )
        assert record.total_units == 0
        assert record.covered_units == 0
        assert record.coverage_ratio == 1.0
        assert record.status == "pass"
        assert record.missing_unit_digests == ()


class TestNeverRaises:
    def test_weird_inputs_do_not_raise(self) -> None:
        boundary = DocumentCoverageBoundary()
        weird_sources = [
            "",
            "```\nunclosed fence",
            "| | | |\n| --- |",
            "\x00\x01\x02 binary-ish",
            "üñîçødé heading\n# 漢字 line",
            "*" * 1000,
            "\n".join(["line"] * 5000),
        ]
        for source in weird_sources:
            record = boundary.build_record(source_markdown=source, doc_text="")
            assert isinstance(record, DocumentCoverageRecord)
            assert record.status in {"pass", "failed"}

    def test_nan_threshold_does_not_raise(self) -> None:
        record = DocumentCoverageBoundary().build_record(
            source_markdown="a\nb", doc_text="a\nb", threshold=float("nan")
        )
        assert 0.0 <= record.threshold <= 1.0


class TestMissingDigestCap:
    def test_missing_digests_are_capped(self) -> None:
        source = "\n".join(f"unique line number {i}" for i in range(500))
        record = DocumentCoverageBoundary().build_record(
            source_markdown=source, doc_text=""
        )
        assert record.total_units == 500
        assert record.covered_units == 0
        assert record.status == "failed"
        # Counts reflect truth; stored digests are bounded.
        assert len(record.missing_unit_digests) <= 64


class TestEvidenceHelpers:
    def test_declaration_round_trips_into_evidence_record(self) -> None:
        record = DocumentCoverageBoundary().build_record(
            source_markdown="kept\ndropped", doc_text="kept"
        )
        declaration = evidence_declaration_from_record(record, tool_name="DocumentWrite")
        assert declaration["type"] == DOCUMENT_COVERAGE_EVIDENCE_TYPE
        # failed coverage -> failed evidence status (audit-only; Task C blocks).
        assert declaration["status"] == "failed"
        assert declaration["fields"]["status"] == "failed"
        assert declaration["source"]["kind"] == "verifier"

    def test_evidence_record_helper_builds_valid_record(self) -> None:
        record = DocumentCoverageBoundary().build_record(
            source_markdown="all here", doc_text="all here"
        )
        evidence = evidence_record_from_record(record, tool_name="DocumentWrite")
        assert isinstance(evidence, EvidenceRecord)
        assert evidence.type == "DocumentCoverage"
        assert evidence.status == "ok"
        assert evidence.fields["coverageRatio"] == 1.0


class TestRecordIsFrozenDigestOnly:
    def test_record_rejects_non_digest_values(self) -> None:
        import pytest

        with pytest.raises(Exception):
            DocumentCoverageRecord(
                totalUnits=1,
                coveredUnits=0,
                coverageRatio=0.0,
                threshold=0.95,
                missingUnitDigests=("raw missing text",),
                sourceDigest="sha256:" + "a" * 64,
                docDigest="sha256:" + "b" * 64,
                status="failed",
            )
