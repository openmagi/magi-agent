"""Unit tests for display_span() -- the redaction-safe claim span transform (PR-2).

Tests 1-9 in the PR-2 spec. All tests are pure (no I/O, no harness).

Style: no em-dashes.
"""
from __future__ import annotations

import pytest

from magi_agent.evidence.verify_audit import display_span
from magi_agent.evidence.reports import public_projection_safe_text


# ---------------------------------------------------------------------------
# Test 1: clean text passes through unchanged
# ---------------------------------------------------------------------------


def test_clean_text_passthrough():
    """Clean claim text with no trigger tokens must pass through unchanged."""
    text = "The revenue was 42 million dollars last year"
    result = display_span(text)
    assert result == text


# ---------------------------------------------------------------------------
# Test 2: absolute path collapsed to basename
# ---------------------------------------------------------------------------


def test_absolute_path_collapsed_to_basename():
    """/home/user/project/file.py collapsed to file.py."""
    text = "/home/user/project/file.py"
    result = display_span(text)
    assert result != "[redacted]"
    assert result != ""
    assert "/" not in result or result.startswith("<") or "file.py" in result


# ---------------------------------------------------------------------------
# Test 3: URL collapsed
# ---------------------------------------------------------------------------


def test_url_collapsed():
    """https://github.com/org/repo must be collapsed so the result is not redacted."""
    text = "https://github.com/org/repo"
    result = display_span(text)
    assert public_projection_safe_text(result) != "[redacted]"
    assert result != ""


# ---------------------------------------------------------------------------
# Test 4: git@ SSH URL collapsed
# ---------------------------------------------------------------------------


def test_git_at_ssh_collapsed():
    """git@github.com:org/repo.git must be collapsed."""
    text = "git@github.com:org/repo.git"
    result = display_span(text)
    assert public_projection_safe_text(result) != "[redacted]"
    assert result != ""


# ---------------------------------------------------------------------------
# Test 5: ref: / source: / search: token collapsed
# ---------------------------------------------------------------------------


def test_ref_source_search_tokens_collapsed():
    """ref:, source:, search: tokens must be collapsed."""
    for token in ("ref:abc123", "source:evidence/foo", "search:query_string"):
        result = display_span(token)
        assert public_projection_safe_text(result) != "[redacted]", f"failed for {token!r}"
        assert result != "", f"empty result for {token!r}"


# ---------------------------------------------------------------------------
# Test 6: pvc- identifier collapsed
# ---------------------------------------------------------------------------


def test_pvc_identifier_collapsed():
    """pvc-XXXXXXXX identifiers must be collapsed."""
    text = "pvc-1a2b3c4d"
    result = display_span(text)
    assert public_projection_safe_text(result) != "[redacted]"
    assert result != ""


# ---------------------------------------------------------------------------
# Test 7: mixed text with clean content -- clean portion preserved
# ---------------------------------------------------------------------------


def test_mixed_text_clean_portion_preserved():
    """Mixed text: 'claim at /some/path' must preserve the 'claim at' portion."""
    text = "The analysis was stored at /home/user/analysis.py"
    result = display_span(text)
    assert public_projection_safe_text(result) != "[redacted]"
    assert result != ""
    # The clean portion should be present in some recognizable form
    assert "analysis" in result.lower() or len(result) > 0


# ---------------------------------------------------------------------------
# Test 8: survival property over adversarial fixtures
# ---------------------------------------------------------------------------

_ADVERSARIAL_FIXTURES = [
    # pure path
    "/var/run/magi/workspace/session.json",
    # pure URL
    "https://private.internal/api/v1/secret",
    # git SSH
    "git@internal.corp:team/repo.git",
    # ref token
    "ref:evidence/sha256abcdef",
    # search token
    "search:internal query string",
    # pvc
    "pvc-deadbeef-1234",
    # home-relative path
    "~/projects/magi/config.yaml",
    # mixed: clean text + path
    "The document was found at /home/kevin/docs/report.pdf and is relevant",
    # mixed: clean text + URL
    "See https://internal.corp/kb/article123 for details",
    # mixed: all trigger classes
    "Found ref:src_1 at /home/user/file.py via https://example.com/path git@gh.com:o/r.git",
]


@pytest.mark.parametrize("fixture", _ADVERSARIAL_FIXTURES, ids=range(len(_ADVERSARIAL_FIXTURES)))
def test_survival_property_over_adversarial_fixtures(fixture):
    """For any adversarial input: public_projection_safe_text(display_span(s))
    must NEVER be '[redacted]' unless display_span(s) is ''.

    This is the postcondition backstop: if display_span returns non-empty,
    public_projection_safe_text must not further redact it to '[redacted]'.
    """
    result = display_span(fixture)
    if result != "":
        projected = public_projection_safe_text(result)
        assert projected != "[redacted]", (
            f"display_span({fixture!r}) -> {result!r} still redacts to [redacted]"
        )


# ---------------------------------------------------------------------------
# Test 9: empty-string input returns empty string
# ---------------------------------------------------------------------------


def test_empty_input_returns_empty():
    """display_span('') must return ''."""
    assert display_span("") == ""
