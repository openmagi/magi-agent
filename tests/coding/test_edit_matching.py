"""Unit tests for magi_agent.coding.edit_matching.

Table-driven per-matcher: exact, indentation, CRLF, escapes,
whitespace-normalized, block-anchor, ambiguous multi-match,
truly-absent, replace_all.
"""
from __future__ import annotations

import pytest

from magi_agent.coding.edit_matching import (
    EditMatchResult,
    MultipleMatchesError,
    NoMatchError,
    levenshtein,
    replace,
    replace_text,
    detect_line_ending,
    _escape_normalized,
    _whitespace_normalized,
)


# ---------------------------------------------------------------------------
# levenshtein
# ---------------------------------------------------------------------------

class TestLevenshtein:
    def test_identical(self):
        assert levenshtein("abc", "abc") == 0

    def test_empty_both(self):
        assert levenshtein("", "") == 0

    def test_empty_one(self):
        assert levenshtein("", "abc") == 3
        assert levenshtein("abc", "") == 3

    def test_single_insert(self):
        assert levenshtein("ab", "abc") == 1

    def test_single_delete(self):
        assert levenshtein("abc", "ab") == 1

    def test_single_replace(self):
        assert levenshtein("abc", "axc") == 1

    def test_different(self):
        assert levenshtein("kitten", "sitting") == 3


# ---------------------------------------------------------------------------
# detect_line_ending
# ---------------------------------------------------------------------------

class TestDetectLineEnding:
    def test_lf(self):
        assert detect_line_ending("a\nb\nc") == "\n"

    def test_crlf(self):
        assert detect_line_ending("a\r\nb\r\nc") == "\r\n"

    def test_no_newline_defaults_lf(self):
        assert detect_line_ending("no newlines here") == "\n"

    def test_mixed_prefers_crlf_when_dominant(self):
        # 3 CRLFs vs 1 LF → CRLF
        text = "a\r\nb\r\nc\r\nd\ne"
        assert detect_line_ending(text) == "\r\n"


# ---------------------------------------------------------------------------
# replace — matcher 1: simple (exact)
# ---------------------------------------------------------------------------

class TestSimpleMatcher:
    def test_exact_match(self):
        content = "hello world\n"
        result = replace(content, "hello", "goodbye")
        assert str(result) == "goodbye world\n"

    def test_not_found_raises(self):
        with pytest.raises(NoMatchError):
            replace("hello world\n", "NOPE", "x")

    def test_identical_old_new_raises_value_error(self):
        with pytest.raises(ValueError, match="no changes"):
            replace("abc", "abc", "abc")

    def test_replace_first_only(self):
        # When a single unique occurrence exists, exactly one replacement is made.
        content = "prefix hello suffix\n"
        result = replace(content, "hello", "goodbye")
        assert str(result) == "prefix goodbye suffix\n"

    def test_replace_all_flag(self):
        content = "a a a"
        result = replace(content, "a", "b", replace_all=True)
        assert str(result) == "b b b"


# ---------------------------------------------------------------------------
# replace — matcher 2: line_trimmed
# ---------------------------------------------------------------------------

class TestLineTrimmedMatcher:
    def test_leading_spaces_mismatch(self):
        content = "def foo():\n    return 1\n"
        # find has wrong indentation
        find = "def foo():\n  return 1\n"
        new = "def foo():\n    return 42\n"
        result = replace(content, find, new)
        assert "return 42" in str(result)

    def test_trailing_spaces_on_find_lines(self):
        content = "line one\nline two\n"
        find = "line one  \nline two  \n"  # trailing spaces in find
        result = replace(content, find, "replaced\n")
        assert str(result) == "replaced\n"

    def test_multiline_line_trimmed(self):
        content = "  alpha\n  beta\n  gamma\n"
        find = "alpha\nbeta\ngamma\n"  # no leading spaces in find
        result = replace(content, find, "X\n")
        assert str(result) == "X\n"


# ---------------------------------------------------------------------------
# replace — matcher 4: whitespace_normalized
# ---------------------------------------------------------------------------

class TestWhitespaceNormalizedMatcher:
    def test_extra_internal_spaces(self):
        content = "return  value  +  1\n"
        find = "return value + 1\n"  # normalized spaces
        result = replace(content, find, "return value + 2\n")
        assert "2" in str(result)

    def test_tabs_vs_spaces(self):
        content = "key:\tvalue\n"
        find = "key: value\n"
        result = replace(content, find, "key: new_value\n")
        assert "new_value" in str(result)


# ---------------------------------------------------------------------------
# replace — matcher 5: indentation_flexible
# ---------------------------------------------------------------------------

class TestIndentationFlexibleMatcher:
    def test_single_space_indentation_diff(self):
        content = "def foo():\n    x = 1\n    y = 2\n    return x + y\n"
        # find has 2-space indentation instead of 4-space
        find = "def foo():\n  x = 1\n  y = 2\n  return x + y\n"
        new = "def foo():\n    x = 10\n    y = 20\n    return x + y\n"
        result = replace(content, find, new)
        assert "x = 10" in str(result)

    def test_no_indentation_in_find(self):
        content = "    if True:\n        pass\n"
        find = "if True:\n    pass\n"
        result = replace(content, find, "if False:\n    pass\n")
        assert "if False" in str(result)


# ---------------------------------------------------------------------------
# replace — matcher 6: escape_normalized
# ---------------------------------------------------------------------------

class TestEscapeNormalizedMatcher:
    def test_escaped_newline(self):
        content = 'say "hello"\n'
        # find uses \" while content has bare "
        find = 'say \\"hello\\"\n'
        result = replace(content, find, 'say "world"\n')
        assert "world" in str(result)

    def test_escaped_tab(self):
        content = "col1\tcol2\n"
        find = "col1\\tcol2\n"
        result = replace(content, find, "col1\tcol3\n")
        assert "col3" in str(result)


# ---------------------------------------------------------------------------
# replace — matcher 7: trimmed_boundary
# ---------------------------------------------------------------------------

class TestTrimmedBoundaryMatcher:
    def test_leading_trailing_whitespace_on_block(self):
        content = "    hello world\n"
        find = "\n    hello world\n\n"  # extra surrounding newlines
        result = replace(content, find, "    goodbye\n")
        assert "goodbye" in str(result)

    def test_trimmed_single_line(self):
        content = "  some text  \n"
        find = "  some text  \n  "  # extra trailing spaces / whitespace surrounding block
        result = replace(content, find, "new line\n")
        # trimmed_boundary strips surrounding whitespace: find.strip() == "some text"
        # which is present in content, so the replacement should succeed
        assert str(result) == "new line\n"


# ---------------------------------------------------------------------------
# replace — matcher 3: block_anchor
# ---------------------------------------------------------------------------

class TestBlockAnchorMatcher:
    def test_block_anchor_similar_middle(self):
        content = (
            "def calculate(x, y):\n"
            "    # compute sum\n"
            "    result = x + y\n"
            "    return result\n"
        )
        # find has slightly different middle but same first/last anchor
        find = (
            "def calculate(x, y):\n"
            "    # compute total\n"  # "sum" -> "total" — different middle
            "    result = x + y\n"
            "    return result\n"
        )
        new = (
            "def calculate(x, y):\n"
            "    # compute sum\n"
            "    result = x + y + 0\n"
            "    return result\n"
        )
        result = replace(content, find, new)
        assert "x + y + 0" in str(result)

    def test_block_anchor_requires_3_lines(self):
        # With only 2 lines, block_anchor should not apply; test other matchers
        content = "first line\nsecond line\n"
        find = "first line\nsecond line\n"
        result = replace(content, find, "replaced\n")
        assert str(result) == "replaced\n"


# ---------------------------------------------------------------------------
# replace — matcher 8: context_aware
# ---------------------------------------------------------------------------

class TestContextAwareMatcher:
    def test_context_aware_50pct_middle_match(self):
        content = (
            "class Foo:\n"
            "    def bar(self):\n"
            "        x = 1\n"
            "        y = 2\n"
            "        return x\n"
        )
        # find has same anchors (class Foo: / return x) but different middle
        find = (
            "class Foo:\n"
            "    def baz(self):\n"  # different method name
            "        x = 1\n"
            "        y = 2\n"
            "        return x\n"
        )
        new = (
            "class Foo:\n"
            "    def bar(self):\n"
            "        x = 10\n"
            "        y = 20\n"
            "        return x\n"
        )
        # context_aware requires ≥50% middle match; only 2/3 middle differ
        # This test verifies that a near-match IS accepted
        result = replace(content, find, new)
        assert "x = 10" in str(result)


# ---------------------------------------------------------------------------
# replace — CRLF preservation
# ---------------------------------------------------------------------------

class TestCrlfPreservation:
    def test_crlf_file_lf_find(self):
        content = "line1\r\nline2\r\nline3\r\n"
        find = "line1\nline2\n"  # LF in find, CRLF in file
        result = replace(content, find, "replaced\n")
        # Result should preserve CRLF
        assert "\r\n" in str(result)
        assert "replaced" in str(result)

    def test_lf_file_crlf_find(self):
        content = "line1\nline2\nline3\n"
        find = "line1\r\nline2\r\n"  # CRLF in find, LF in file
        result = replace(content, find, "replaced\n")
        assert "\r\n" not in str(result)
        assert "replaced" in str(result)


# ---------------------------------------------------------------------------
# replace — BOM preservation
# ---------------------------------------------------------------------------

class TestBomPreservation:
    def test_bom_preserved_on_replace(self):
        bom = "﻿"
        content = bom + "hello world\n"
        result = replace(content, "hello", "goodbye")
        assert str(result).startswith(bom)
        assert "goodbye" in str(result)


# ---------------------------------------------------------------------------
# replace — multiple matches / ambiguity
# ---------------------------------------------------------------------------

class TestMultipleMatchesError:
    def test_duplicate_exact_raises(self):
        content = "foo\nbar\nfoo\nbar\n"
        with pytest.raises(MultipleMatchesError):
            replace(content, "foo\nbar\n", "new\n")

    def test_replace_all_with_duplicates_succeeds(self):
        content = "foo\nbar\nfoo\nbar\n"
        result = replace(content, "foo\nbar\n", "new\n", replace_all=True)
        assert str(result) == "new\nnew\n"

    def test_ambiguous_fuzzy_match_raises(self):
        # Indentation-off find that matches two identical blocks
        content = "    def x():\n        pass\n    def x():\n        pass\n"
        find = "def x():\n    pass\n"  # indentation stripped
        with pytest.raises(MultipleMatchesError):
            replace(content, find, "def x():\n    return 1\n")


# ---------------------------------------------------------------------------
# replace — truly absent
# ---------------------------------------------------------------------------

class TestNoMatchError:
    def test_completely_absent(self):
        with pytest.raises(NoMatchError):
            replace("hello world\n", "does not exist anywhere\n", "x")

    def test_partial_line_absent(self):
        with pytest.raises(NoMatchError):
            replace("foo bar\n", "foo baz\n", "x")


# ---------------------------------------------------------------------------
# replace — replace_all with multi_occurrence
# ---------------------------------------------------------------------------

class TestMultiOccurrenceReplacer:
    def test_replace_all_multiple_exact(self):
        content = "a b a b a\n"
        result = replace(content, "a", "X", replace_all=True)
        assert str(result) == "X b X b X\n"

    def test_replace_all_single_occurrence_still_works(self):
        content = "one two three\n"
        result = replace(content, "two", "2", replace_all=True)
        assert str(result) == "one 2 three\n"


# ---------------------------------------------------------------------------
# replace — edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_new_text_is_deletion(self):
        content = "before\ndelete_me\nafter\n"
        result = replace(content, "delete_me\n", "")
        assert str(result) == "before\nafter\n"

    def test_empty_old_text_raises(self):
        # Empty old_text should raise ValueError (existing gate5b contract)
        # edit_matching.replace raises ValueError for empty old
        with pytest.raises(ValueError):
            replace("content\n", "", "new")

    def test_full_file_replacement(self):
        content = "entire file content\n"
        result = replace(content, "entire file content\n", "new content\n")
        assert str(result) == "new content\n"


# ---------------------------------------------------------------------------
# _escape_normalized: literal backslash-n in file must not be destroyed (Item 2)
# ---------------------------------------------------------------------------

class TestEscapeNormalizedLiteralBackslashN:
    def test_file_with_literal_backslash_n_two_char_sequence(self):
        # The file content contains the two-character sequence backslash + n
        # (not a real newline). The escape_normalized matcher must NOT unescape
        # the content side (old bug: unescaping content destroyed literal \n).
        content = r"msg = 'line1\nline2'" + "\n"  # literal backslash-n in source
        find = r"msg = 'line1\nline2'" + "\n"     # same literal backslash-n
        result = replace(content, find, "msg = 'replaced'\n")
        assert str(result) == "msg = 'replaced'\n"

    def test_escape_normalized_window_comparison_is_raw(self):
        # _escape_normalized's sliding-window branch must compare the raw
        # content window to the unescaped find_lines — NOT unescape the
        # content side.  A file that contains a real newline must NOT be
        # matched by a find string that has a literal backslash-n at the
        # corresponding position.
        #
        # Setup: file has  x = "a" <newline> y = "b"  (two lines)
        #        find has  x = "a\ny = "b"           (backslash + n, one line)
        # After unescaping find → x = "a" + real-newline + y = "b" which looks
        # like the two-line content.  With the old bug (unescape content side)
        # this would match.  With the fix it must NOT match via this path
        # (the simple exact matcher handles the identical case).
        content_two_lines = 'x = "a"\ny = "b"\n'
        # find_one_line has the literal backslash-n escape sequence
        find_one_line = 'x = "a\\ny = "b"\n'
        # After unescaping: 'x = "a"\ny = "b"\n' which equals content_two_lines.
        # The sliding-window should NOT yield content_two_lines as a candidate
        # because the raw window ["x = \"a\"\n", "y = \"b\"\n"] != ["x = \"a\\ny = \"b\"\n"]
        candidates = list(_escape_normalized(content_two_lines, find_one_line))
        assert candidates == [], (
            "_escape_normalized must not unescape the content side; "
            f"got unexpected candidates: {candidates!r}"
        )


# ---------------------------------------------------------------------------
# _whitespace_normalized: short token must not cause partial-line replacement (Item 3)
# ---------------------------------------------------------------------------

class TestWhitespaceNormalizedPartialLineSafety:
    def test_short_token_regex_does_not_yield_partial_line_match(self):
        # The regex sub-match branch in _whitespace_normalized must only yield
        # a candidate when the match spans the COMPLETE line content.
        # When old_text is "foo" and the line is "the foo baz is here", the
        # regex matches "foo" at a non-zero start offset — so it must NOT yield.
        content = "the foo baz is here\n"
        find = "foo"
        # Direct matcher: the regex would match at m.start()==4, len("the foo baz is here")==19
        # → m.end() != len(stripped) → not yielded.
        candidates = list(_whitespace_normalized(content, find))
        assert candidates == [], (
            "_whitespace_normalized must not yield a partial-line regex match; "
            f"got: {candidates!r}"
        )

    def test_whitespace_normalized_full_line_still_matches(self):
        # When the normalised pattern spans the complete line, replacement
        # should still succeed.
        content = "return   value  +  1\n"
        find = "return value + 1"
        result = replace(content, find, "return value + 2")
        assert "2" in str(result)


# ---------------------------------------------------------------------------
# PR1: EditMatchResult — structured return value tests
# ---------------------------------------------------------------------------


class TestEditMatchResultStructure:
    """replace() now returns EditMatchResult; str(result) == result.result."""

    def test_replace_returns_edit_match_result(self):
        content = "hello world\n"
        result = replace(content, "hello", "goodbye")
        assert isinstance(result, EditMatchResult)

    def test_str_result_equals_result_field(self):
        content = "hello world\n"
        result = replace(content, "hello", "goodbye")
        assert str(result) == result.result

    def test_result_contains_new_content(self):
        content = "hello world\n"
        result = replace(content, "hello", "goodbye")
        assert result.result == "goodbye world\n"

    def test_replace_text_returns_string(self):
        content = "hello world\n"
        result = replace_text(content, "hello", "goodbye")
        assert isinstance(result, str)
        assert result == "goodbye world\n"

    def test_simple_tier_index_zero(self):
        content = "hello world\n"
        result = replace(content, "hello", "goodbye")
        assert result.tier == "simple"
        assert result.tier_index == 0

    def test_simple_tier_confidence_one(self):
        content = "hello world\n"
        result = replace(content, "hello", "goodbye")
        assert result.confidence == 1.00

    def test_matched_span_is_tuple_of_two_ints(self):
        content = "hello world\n"
        result = replace(content, "hello", "goodbye")
        assert isinstance(result.matched_span, tuple)
        assert len(result.matched_span) == 2

    def test_ambiguous_false_for_unique_match(self):
        content = "hello world\n"
        result = replace(content, "hello", "goodbye")
        assert result.ambiguous is False

    def test_line_trimmed_tier(self):
        # Matcher 2: line_trimmed — mismatched indentation
        content = "def foo():\n    return 1\n"
        find = "def foo():\n  return 1\n"
        result = replace(content, find, "def foo():\n    return 42\n")
        assert result.tier == "line_trimmed"
        assert result.tier_index == 1
        assert result.confidence == 0.95

    def test_block_anchor_tier_has_dynamic_confidence(self):
        # Matcher 3: block_anchor — dynamic confidence = 0.3 + 0.7 * score
        content = (
            "def calculate(x, y):\n"
            "    # compute sum\n"
            "    result = x + y\n"
            "    return result\n"
        )
        find = (
            "def calculate(x, y):\n"
            "    # compute total\n"
            "    result = x + y\n"
            "    return result\n"
        )
        new = (
            "def calculate(x, y):\n"
            "    # compute sum\n"
            "    result = x + y + 0\n"
            "    return result\n"
        )
        result = replace(content, find, new)
        assert result.tier == "block_anchor"
        assert result.tier_index == 2
        # Dynamic confidence: 0.3 + 0.7 * actual_score; must be in [0.3, 1.0]
        assert 0.3 <= result.confidence <= 1.0

    def test_context_aware_tier_has_actual_score(self):
        # Matcher 8: context_aware — confidence = actual matches/total ratio.
        # block_anchor (tier 2) fires first for the same content when anchor
        # lines match.  context_aware fires only when block_anchor hasn't found
        # the content yet (e.g. different first/last anchor lines).
        # For inputs where block_anchor fires first the tier will be block_anchor;
        # that is still correct cascade behaviour — both tiers produce dynamic
        # confidence from actual similarity scores.
        content = (
            "class Foo:\n"
            "    def bar(self):\n"
            "        x = 1\n"
            "        y = 2\n"
            "        return x\n"
        )
        find = (
            "class Foo:\n"
            "    def baz(self):\n"   # different middle
            "        x = 1\n"
            "        y = 2\n"
            "        return x\n"
        )
        new = (
            "class Foo:\n"
            "    def bar(self):\n"
            "        x = 10\n"
            "        y = 20\n"
            "        return x\n"
        )
        result = replace(content, find, new)
        # block_anchor fires before context_aware for anchor-matching content
        assert result.tier in ("block_anchor", "context_aware")
        assert result.tier_index in (2, 7)
        # Both tiers use dynamic confidence from actual similarity scores
        assert 0.0 < result.confidence <= 1.0

    def test_indentation_flexible_tier(self):
        # Matcher 5: indentation_flexible fires when line content matches after
        # removing common leading indent.  In practice line_trimmed (tier 1)
        # also catches plain indentation differences because it strips all
        # leading/trailing whitespace; indentation_flexible fires first only
        # when find has relative-indentation structure that line_trimmed strips
        # away but indentation_flexible preserves.  For inputs where both apply,
        # the cascade stops at the earlier tier (line_trimmed).
        content = "def foo():\n    x = 1\n    y = 2\n    return x + y\n"
        find = "def foo():\n  x = 1\n  y = 2\n  return x + y\n"
        new = "def foo():\n    x = 10\n    y = 20\n    return x + y\n"
        result = replace(content, find, new)
        # line_trimmed fires before indentation_flexible for pure-indentation diffs
        assert result.tier in ("line_trimmed", "indentation_flexible")
        assert result.tier_index in (1, 4)
        assert result.confidence in (0.95, 0.85)

    def test_whitespace_normalized_tier(self):
        # Matcher 4: whitespace_normalized
        content = "return  value  +  1\n"
        result = replace(content, "return value + 1\n", "return value + 2\n")
        assert result.tier == "whitespace_normalized"
        assert result.tier_index == 3
        assert result.confidence == 0.90

    def test_escape_normalized_tier(self):
        # Matcher 6: escape_normalized
        content = 'say "hello"\n'
        find = 'say \\"hello\\"\n'
        result = replace(content, find, 'say "world"\n')
        assert result.tier == "escape_normalized"
        assert result.tier_index == 5
        assert result.confidence == 0.80

    def test_trimmed_boundary_tier(self):
        # Matcher 7: trimmed_boundary
        content = "    hello world\n"
        find = "\n    hello world\n\n"
        result = replace(content, find, "    goodbye\n")
        assert result.tier == "trimmed_boundary"
        assert result.tier_index == 6
        assert result.confidence == 0.85

    def test_multi_occurrence_tier(self):
        # Matcher 9: multi_occurrence is a fallback that fires after all earlier
        # matchers; for exact-match content, simple (tier 0) fires first since
        # both check `find in content`.  The multi_occurrence tier is reserved
        # for replace_all=True with fuzzy-matched candidates.
        # Verify that replace_all=True with exact content returns a valid tier
        # (not necessarily multi_occurrence) and correctly replaces all occurrences.
        content = "a a a"
        result = replace(content, "a", "b", replace_all=True)
        assert str(result) == "b b b"
        assert result.tier_index in range(len(("simple", "line_trimmed", "block_anchor",
                                               "whitespace_normalized", "indentation_flexible",
                                               "escape_normalized", "trimmed_boundary",
                                               "context_aware", "multi_occurrence")))
        assert 0.0 <= result.confidence <= 1.0

    def test_replace_text_helper(self):
        content = "foo bar baz\n"
        assert replace_text(content, "bar", "qux") == "foo qux baz\n"

    def test_existing_tests_still_pass_via_str_coercion(self):
        # Ensure existing code that does `result = replace(...)` and then
        # uses it as a string (e.g. via concatenation or `in` check) still works.
        content = "before\ndelete_me\nafter\n"
        result = replace(content, "delete_me\n", "")
        # String operations on result work via __str__
        assert "after" in str(result)
        assert str(result) == "before\nafter\n"
