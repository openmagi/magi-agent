"""Unit tests for the child-toolset profile resolver (PR1, doc 07).

These tests exercise the SMALL pure module ``magi_agent.runtime.child_toolset``
that maps the ``MAGI_CHILD_RUNNER_TOOLSET`` env gate to a profile literal plus
a read-only tool allowlist. No network, no model, no heavy imports.
"""

from __future__ import annotations

import pytest

from magi_agent.runtime.child_toolset import (
    CHILD_TOOLSET_ENV,
    MUTATING_TOOL_NAMES,
    READONLY_TOOL_NAMES,
    resolve_child_toolset_profile,
    toolset_allowlist,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("none", "none"),
        ("readonly", "readonly"),
        ("full", "full"),
        ("READONLY", "readonly"),  # case-insensitive
        ("  full  ", "full"),  # whitespace tolerant
    ],
)
def test_resolve_known_profiles(value: str, expected: str) -> None:
    assert resolve_child_toolset_profile({CHILD_TOOLSET_ENV: value}) == expected


def test_resolve_unset_defaults_to_none() -> None:
    # NOTE: this expectation changes to "inherit" in the new design.
    # Replaced by test_resolve_unset_defaults_to_inherit below.
    # Keep as a renamed tombstone so git blame is traceable.
    pass  # superseded — see test_resolve_unset_defaults_to_inherit


def test_resolve_empty_defaults_to_none() -> None:
    # NOTE: this expectation changes to "inherit" in the new design.
    # Replaced by test_resolve_empty_defaults_to_inherit below.
    pass  # superseded — see test_resolve_empty_defaults_to_inherit


@pytest.mark.parametrize("bad", ["bogus", "read-only", "all", "rw", "1", "true"])
def test_resolve_unknown_value_defaults_to_none(bad: str) -> None:
    """Any unrecognised value degrades to the safe ``none`` profile."""
    assert resolve_child_toolset_profile({CHILD_TOOLSET_ENV: bad}) == "none"


def test_resolve_uses_os_environ_when_env_is_none(monkeypatch) -> None:
    monkeypatch.setenv(CHILD_TOOLSET_ENV, "readonly")
    assert resolve_child_toolset_profile() == "readonly"


def test_readonly_tool_names_are_non_mutating_tools() -> None:
    """The read-only allowlist must contain ONLY non-mutating tools.

    Includes source-inspection tools plus pure side-effect-free helpers
    (PR-N added ``Calculation``, a deterministic AST expression evaluator
    with no fs/net/subprocess surface).
    """
    assert "FileRead" in READONLY_TOOL_NAMES
    assert "Glob" in READONLY_TOOL_NAMES
    assert "Grep" in READONLY_TOOL_NAMES
    # PR-N: pure helpers are allowed when they have zero side effects.
    assert "Calculation" in READONLY_TOOL_NAMES
    # No workspace-mutating tools may appear in the read-only allowlist.
    for forbidden in ("FileWrite", "Edit", "Bash", "PatchApply"):
        assert forbidden not in READONLY_TOOL_NAMES


def test_toolset_allowlist_none_is_empty() -> None:
    """``none`` → empty allowlist (text-only child, byte-identical to v1)."""
    assert toolset_allowlist("none") == ()


def test_toolset_allowlist_readonly_is_readonly_names() -> None:
    assert toolset_allowlist("readonly") == READONLY_TOOL_NAMES


def test_toolset_allowlist_full_is_none_sentinel() -> None:
    """``full`` → ``None`` sentinel meaning 'no name filter' (whole toolset)."""
    assert toolset_allowlist("full") is None


# ---------------------------------------------------------------------------
# inherit profile — RED tests (written before implementation)
# ---------------------------------------------------------------------------


def test_resolve_unset_defaults_to_inherit() -> None:
    """UNSET env → 'inherit' (the new default)."""
    assert resolve_child_toolset_profile({}) == "inherit"


def test_resolve_empty_defaults_to_inherit() -> None:
    """Empty string env → 'inherit'."""
    assert resolve_child_toolset_profile({CHILD_TOOLSET_ENV: ""}) == "inherit"


def test_resolve_whitespace_only_defaults_to_inherit() -> None:
    """Whitespace-only value → 'inherit'."""
    assert resolve_child_toolset_profile({CHILD_TOOLSET_ENV: "   "}) == "inherit"


def test_resolve_inherit_literal_is_recognised() -> None:
    """'inherit' is a recognised profile literal."""
    assert resolve_child_toolset_profile({CHILD_TOOLSET_ENV: "inherit"}) == "inherit"


def test_resolve_inherit_case_insensitive() -> None:
    """'INHERIT' is normalised to 'inherit'."""
    assert resolve_child_toolset_profile({CHILD_TOOLSET_ENV: "INHERIT"}) == "inherit"


def test_toolset_allowlist_inherit_is_none_sentinel() -> None:
    """'inherit' → None sentinel (no name filter; parent-cap applied later)."""
    assert toolset_allowlist("inherit") is None


def test_mutating_tool_names_contains_expected_tools() -> None:
    """MUTATING_TOOL_NAMES must include all write-class tools from EDIT_CLASS_TOOLS."""
    for name in ("FileEdit", "FileWrite", "Edit", "Write", "ApplyPatch", "Bash"):
        assert name in MUTATING_TOOL_NAMES, f"Expected {name!r} in MUTATING_TOOL_NAMES"


def test_mutating_tool_names_is_frozenset() -> None:
    """MUTATING_TOOL_NAMES should be a frozenset for O(1) membership tests."""
    assert isinstance(MUTATING_TOOL_NAMES, frozenset)
