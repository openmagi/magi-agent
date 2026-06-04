"""Unit tests for magi_agent.coding.edit_matching.

Table-driven per-matcher: exact, indentation, CRLF, escapes,
whitespace-normalized, block-anchor, ambiguous multi-match,
truly-absent, replace_all.
"""
from __future__ import annotations

import pytest

from magi_agent.coding.edit_matching import (
    MultipleMatchesError,
    NoMatchError,
    levenshtein,
    replace,
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
        assert result == "goodbye world\n"

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
        assert result == "prefix goodbye suffix\n"

    def test_replace_all_flag(self):
        content = "a a a"
        result = replace(content, "a", "b", replace_all=True)
        assert result == "b b b"


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
        assert "return 42" in result

    def test_trailing_spaces_on_find_lines(self):
        content = "line one\nline two\n"
        find = "line one  \nline two  \n"  # trailing spaces in find
        result = replace(content, find, "replaced\n")
        assert result == "replaced\n"

    def test_multiline_line_trimmed(self):
        content = "  alpha\n  beta\n  gamma\n"
        find = "alpha\nbeta\ngamma\n"  # no leading spaces in find
        result = replace(content, find, "X\n")
        assert result == "X\n"


# ---------------------------------------------------------------------------
# replace — matcher 4: whitespace_normalized
# ---------------------------------------------------------------------------

class TestWhitespaceNormalizedMatcher:
    def test_extra_internal_spaces(self):
        content = "return  value  +  1\n"
        find = "return value + 1\n"  # normalized spaces
        result = replace(content, find, "return value + 2\n")
        assert "2" in result

    def test_tabs_vs_spaces(self):
        content = "key:\tvalue\n"
        find = "key: value\n"
        result = replace(content, find, "key: new_value\n")
        assert "new_value" in result


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
        assert "x = 10" in result

    def test_no_indentation_in_find(self):
        content = "    if True:\n        pass\n"
        find = "if True:\n    pass\n"
        result = replace(content, find, "if False:\n    pass\n")
        assert "if False" in result


# ---------------------------------------------------------------------------
# replace — matcher 6: escape_normalized
# ---------------------------------------------------------------------------

class TestEscapeNormalizedMatcher:
    def test_escaped_newline(self):
        content = 'say "hello"\n'
        # find uses \" while content has bare "
        find = 'say \\"hello\\"\n'
        result = replace(content, find, 'say "world"\n')
        assert "world" in result

    def test_escaped_tab(self):
        content = "col1\tcol2\n"
        find = "col1\\tcol2\n"
        result = replace(content, find, "col1\tcol3\n")
        assert "col3" in result


# ---------------------------------------------------------------------------
# replace — matcher 7: trimmed_boundary
# ---------------------------------------------------------------------------

class TestTrimmedBoundaryMatcher:
    def test_leading_trailing_whitespace_on_block(self):
        content = "    hello world\n"
        find = "\n    hello world\n\n"  # extra surrounding newlines
        result = replace(content, find, "    goodbye\n")
        assert "goodbye" in result

    def test_trimmed_single_line(self):
        content = "  some text  \n"
        find = "  some text  \n  "  # extra trailing spaces / whitespace surrounding block
        result = replace(content, find, "new line\n")
        # trimmed_boundary strips surrounding whitespace: find.strip() == "some text"
        # which is present in content, so the replacement should succeed
        assert result == "new line\n"


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
        assert "x + y + 0" in result

    def test_block_anchor_requires_3_lines(self):
        # With only 2 lines, block_anchor should not apply; test other matchers
        content = "first line\nsecond line\n"
        find = "first line\nsecond line\n"
        result = replace(content, find, "replaced\n")
        assert result == "replaced\n"


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
        assert "x = 10" in result


# ---------------------------------------------------------------------------
# replace — CRLF preservation
# ---------------------------------------------------------------------------

class TestCrlfPreservation:
    def test_crlf_file_lf_find(self):
        content = "line1\r\nline2\r\nline3\r\n"
        find = "line1\nline2\n"  # LF in find, CRLF in file
        result = replace(content, find, "replaced\n")
        # Result should preserve CRLF
        assert "\r\n" in result
        assert "replaced" in result

    def test_lf_file_crlf_find(self):
        content = "line1\nline2\nline3\n"
        find = "line1\r\nline2\r\n"  # CRLF in find, LF in file
        result = replace(content, find, "replaced\n")
        assert "\r\n" not in result
        assert "replaced" in result


# ---------------------------------------------------------------------------
# replace — BOM preservation
# ---------------------------------------------------------------------------

class TestBomPreservation:
    def test_bom_preserved_on_replace(self):
        bom = "﻿"
        content = bom + "hello world\n"
        result = replace(content, "hello", "goodbye")
        assert result.startswith(bom)
        assert "goodbye" in result


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
        assert result == "new\nnew\n"

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
        assert result == "X b X b X\n"

    def test_replace_all_single_occurrence_still_works(self):
        content = "one two three\n"
        result = replace(content, "two", "2", replace_all=True)
        assert result == "one 2 three\n"


# ---------------------------------------------------------------------------
# replace — edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_new_text_is_deletion(self):
        content = "before\ndelete_me\nafter\n"
        result = replace(content, "delete_me\n", "")
        assert result == "before\nafter\n"

    def test_empty_old_text_raises(self):
        # Empty old_text should raise ValueError (existing gate5b contract)
        # edit_matching.replace raises ValueError for empty old
        with pytest.raises(ValueError):
            replace("content\n", "", "new")

    def test_full_file_replacement(self):
        content = "entire file content\n"
        result = replace(content, "entire file content\n", "new content\n")
        assert result == "new content\n"


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
        assert result == "msg = 'replaced'\n"

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
        assert "2" in result
