"""Tests for magi_agent.evidence.edit_match_receipts (PR1).

Verifies:
- Receipt builds correctly from an EditMatchResult.
- file_digest and span_digest are sha256:<64 hex> format.
- public_projection carries no raw text (only digests and scalars).
- "EditMatch" is a registered builtin evidence type.
"""
from __future__ import annotations

import hashlib

import pytest

from magi_agent.coding.edit_matching import replace
from magi_agent.evidence.edit_match_receipts import (
    EDIT_MATCH_EVIDENCE_TYPE,
    EditMatchReceiptBoundary,
    EditMatchReceiptRecord,
)
from magi_agent.evidence.types import BUILTIN_EVIDENCE_TYPES, validate_evidence_type_name


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _sha256_hex(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _make_boundary() -> EditMatchReceiptBoundary:
    return EditMatchReceiptBoundary()


# ---------------------------------------------------------------------------
# BUILTIN_EVIDENCE_TYPES registration
# ---------------------------------------------------------------------------


class TestEditMatchRegisteredBuiltin:
    def test_edit_match_in_builtin_types(self):
        assert "EditMatch" in BUILTIN_EVIDENCE_TYPES

    def test_edit_match_validates_as_builtin_name(self):
        result = validate_evidence_type_name("EditMatch")
        assert result == "EditMatch"

    def test_evidence_type_constant_value(self):
        assert EDIT_MATCH_EVIDENCE_TYPE == "EditMatch"


# ---------------------------------------------------------------------------
# EditMatchReceiptRecord — construction and digest format
# ---------------------------------------------------------------------------


class TestEditMatchReceiptRecord:
    def test_record_type_field(self):
        content = "hello world\n"
        match = replace(content, "hello", "goodbye")
        boundary = _make_boundary()
        record = boundary.build_record(match=match, file_content=match.result)
        assert record.type == "EditMatch"

    def test_file_digest_is_sha256(self):
        content = "hello world\n"
        match = replace(content, "hello", "goodbye")
        boundary = _make_boundary()
        record = boundary.build_record(match=match, file_content=match.result)
        assert record.file_digest.startswith("sha256:")
        assert len(record.file_digest) == len("sha256:") + 64

    def test_span_digest_is_sha256(self):
        content = "hello world\n"
        match = replace(content, "hello", "goodbye")
        boundary = _make_boundary()
        record = boundary.build_record(match=match, file_content=match.result)
        assert record.span_digest.startswith("sha256:")
        assert len(record.span_digest) == len("sha256:") + 64

    def test_tier_matches_match_result(self):
        content = "hello world\n"
        match = replace(content, "hello", "goodbye")
        boundary = _make_boundary()
        record = boundary.build_record(match=match, file_content=match.result)
        assert record.tier == match.tier

    def test_tier_index_matches_match_result(self):
        content = "hello world\n"
        match = replace(content, "hello", "goodbye")
        boundary = _make_boundary()
        record = boundary.build_record(match=match, file_content=match.result)
        assert record.tier_index == match.tier_index

    def test_confidence_matches_match_result(self):
        content = "hello world\n"
        match = replace(content, "hello", "goodbye")
        boundary = _make_boundary()
        record = boundary.build_record(match=match, file_content=match.result)
        assert record.confidence == match.confidence

    def test_ambiguous_matches_match_result(self):
        content = "hello world\n"
        match = replace(content, "hello", "goodbye")
        boundary = _make_boundary()
        record = boundary.build_record(match=match, file_content=match.result)
        assert record.ambiguous == match.ambiguous

    def test_file_digest_computed_from_file_content(self):
        """file_digest must be the sha256 of the file_content passed in."""
        content = "hello world\n"
        match = replace(content, "hello", "goodbye")
        boundary = _make_boundary()
        new_content = match.result
        record = boundary.build_record(match=match, file_content=new_content)
        expected = _sha256_hex(new_content)
        assert record.file_digest == expected

    def test_record_is_frozen(self):
        content = "hello world\n"
        match = replace(content, "hello", "goodbye")
        boundary = _make_boundary()
        record = boundary.build_record(match=match, file_content=match.result)
        with pytest.raises(Exception):  # pydantic frozen raises ValidationError
            record.tier = "hacked"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# public_projection — no raw text, only safe fields
# ---------------------------------------------------------------------------


class TestEditMatchPublicProjection:
    def test_public_projection_contains_type(self):
        content = "hello world\n"
        match = replace(content, "hello", "goodbye")
        boundary = _make_boundary()
        record = boundary.build_record(match=match, file_content=match.result)
        projection = record.public_projection()
        assert projection["type"] == "EditMatch"

    def test_public_projection_contains_tier(self):
        content = "hello world\n"
        match = replace(content, "hello", "goodbye")
        boundary = _make_boundary()
        record = boundary.build_record(match=match, file_content=match.result)
        projection = record.public_projection()
        assert "tier" in projection
        assert isinstance(projection["tier"], str)

    def test_public_projection_contains_confidence(self):
        content = "hello world\n"
        match = replace(content, "hello", "goodbye")
        boundary = _make_boundary()
        record = boundary.build_record(match=match, file_content=match.result)
        projection = record.public_projection()
        assert "confidence" in projection
        assert isinstance(projection["confidence"], float)

    def test_public_projection_contains_digests(self):
        content = "hello world\n"
        match = replace(content, "hello", "goodbye")
        boundary = _make_boundary()
        record = boundary.build_record(match=match, file_content=match.result)
        projection = record.public_projection()
        assert "fileDigest" in projection
        assert "spanDigest" in projection
        assert projection["fileDigest"].startswith("sha256:")
        assert projection["spanDigest"].startswith("sha256:")

    def test_public_projection_has_no_raw_text(self):
        """Projection must never contain the raw file content or matched span text."""
        file_content = "secret source content here\n"
        match = replace(file_content, "secret source content", "replaced")
        boundary = _make_boundary()
        record = boundary.build_record(match=match, file_content=match.result)
        projection = record.public_projection()
        projection_str = str(projection)
        assert "secret source content" not in projection_str
        assert "replaced" not in projection_str

    def test_public_projection_keys(self):
        content = "x = 1\n"
        match = replace(content, "x = 1", "x = 2")
        boundary = _make_boundary()
        record = boundary.build_record(match=match, file_content=match.result)
        projection = record.public_projection()
        expected_keys = {"type", "tier", "tierIndex", "confidence", "ambiguous", "fileDigest", "spanDigest"}
        assert set(projection.keys()) == expected_keys


# ---------------------------------------------------------------------------
# Direct model validation
# ---------------------------------------------------------------------------


class TestEditMatchReceiptRecordValidation:
    def test_bad_file_digest_raises(self):
        with pytest.raises(Exception):
            EditMatchReceiptRecord(
                tier="simple",
                tier_index=0,
                confidence=1.0,
                fileDigest="not-a-digest",
                spanDigest="sha256:" + "a" * 64,
            )

    def test_confidence_out_of_range_raises(self):
        with pytest.raises(Exception):
            EditMatchReceiptRecord(
                tier="simple",
                tier_index=0,
                confidence=1.5,
                fileDigest="sha256:" + "a" * 64,
                spanDigest="sha256:" + "b" * 64,
            )

    def test_valid_record_constructs(self):
        record = EditMatchReceiptRecord(
            tier="context_aware",
            tier_index=7,
            confidence=0.75,
            ambiguous=False,
            fileDigest="sha256:" + "a" * 64,
            spanDigest="sha256:" + "b" * 64,
        )
        assert record.tier == "context_aware"
        assert record.confidence == 0.75
