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


# ---------------------------------------------------------------------------
# Regression: spanDigest must hash the pre-edit matched text, not post-edit
# ---------------------------------------------------------------------------


class TestSpanDigestMatchesPreEditText:
    """Regression guard for the spanDigest semantics bug.

    Before the fix, build_record sliced post-edit content with pre-edit offsets,
    which yielded a fragment of the new text (e.g. "goodb" instead of "hello").
    These tests assert that spanDigest is the sha256 of the ACTUAL matched
    (pre-edit) candidate text, accessible as match.matched_text.
    """

    def test_matched_span_indexes_original_content(self):
        """matched_span offsets must index the pre-edit original content body."""
        original = "hello world\n"
        match = replace(original, "hello", "goodbye-much-longer")
        start, end = match.matched_span
        # Strip BOM offset (none here) — body starts at index 0.
        bom_offset = 1 if original.startswith("﻿") else 0
        body = original[bom_offset:]
        assert body[start - bom_offset : end - bom_offset] == match.matched_text

    def test_matched_text_equals_candidate(self):
        """matched_text must be the exact pre-edit substring that was replaced."""
        original = "hello world\n"
        match = replace(original, "hello", "goodbye-much-longer")
        assert match.matched_text == "hello"

    def test_span_digest_equals_sha256_of_matched_text(self):
        """spanDigest must be sha256 of matched_text, not of any post-edit fragment."""
        original = "hello world\n"
        match = replace(original, "hello", "goodbye-much-longer")
        boundary = _make_boundary()
        record = boundary.build_record(match=match, file_content=match.result)
        expected_span_digest = _sha256_hex(match.matched_text)
        assert record.span_digest == expected_span_digest

    def test_span_digest_is_not_sha256_of_post_edit_fragment(self):
        """Confirm the old bug is absent: digest of post-edit slice must differ."""
        original = "hello world\n"
        match = replace(original, "hello", "goodbye-much-longer")
        boundary = _make_boundary()
        record = boundary.build_record(match=match, file_content=match.result)
        # The old (buggy) behaviour sliced match.result with matched_span offsets.
        start, end = match.matched_span
        post_edit_fragment = match.result[start:end] if start < len(match.result) else ""
        # post_edit_fragment is "goodb" (first 5 chars of "goodbye-much-longer")
        buggy_digest = _sha256_hex(post_edit_fragment)
        assert record.span_digest != buggy_digest, (
            "spanDigest should NOT equal sha256 of the post-edit fragment "
            f"'{post_edit_fragment}'; it must equal sha256 of pre-edit text '{match.matched_text}'"
        )
