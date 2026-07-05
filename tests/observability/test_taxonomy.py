"""Unit tests for magi_agent.observability.taxonomy — the single taxonomy source."""
from __future__ import annotations

from magi_agent.observability.taxonomy import (
    CATEGORIES,
    NOISE_KINDS,
    get_meta_taxonomy,
)


# ---------------------------------------------------------------------------
# NOISE_KINDS — exact set
# ---------------------------------------------------------------------------

def test_noise_kinds_exact():
    """NOISE_KINDS is exactly the six runtime noise event kinds (B1: thinking_delta added)."""
    assert set(NOISE_KINDS) == {
        "text_delta",
        "heartbeat",
        "thinking_delta",
        "turn_phase",
        "runtime_trace",
        "tool_progress",
    }


def test_noise_kinds_is_list():
    assert isinstance(NOISE_KINDS, list)


# ---------------------------------------------------------------------------
# CATEGORIES — structure
# ---------------------------------------------------------------------------

def test_categories_is_dict():
    assert isinstance(CATEGORIES, dict)


def test_categories_expected_keys():
    assert set(CATEGORIES.keys()) == {"lifecycle", "tools", "policy", "errors", "other"}


def test_lifecycle_kinds():
    expected = {
        "turn_start", "turn_end", "checkpoint",
        "compaction_start", "compaction_end", "aborted",
    }
    assert set(CATEGORIES["lifecycle"]) == expected


def test_tools_kinds():
    assert set(CATEGORIES["tools"]) == {"tool_start", "tool_end", "source_inspected"}


def test_policy_kinds():
    assert set(CATEGORIES["policy"]) == {"rule_check", "rule_violation"}


def test_errors_kinds():
    assert set(CATEGORIES["errors"]) == {"error", "aborted"}


def test_other_kinds():
    """B2: child_started (subagent spawn) added alongside child_progress.

    PR-3: reasoning_promoted (a reasoning-only terminal promoted to the final
    answer) is a visible, non-noise anomaly marker in the 'other' group.
    """
    assert set(CATEGORIES["other"]) == {
        "child_progress",
        "child_started",
        "artifact_created",
        "reasoning_promoted",
        "task_board",
    }


def test_categories_values_are_lists():
    for cat, kinds in CATEGORIES.items():
        assert isinstance(kinds, list), f"{cat} must be a list"


# ---------------------------------------------------------------------------
# get_meta_taxonomy() — serializable payload contract
# ---------------------------------------------------------------------------

def test_get_meta_taxonomy_returns_dict():
    payload = get_meta_taxonomy()
    assert isinstance(payload, dict)


def test_get_meta_taxonomy_has_categories_and_noise_kinds():
    payload = get_meta_taxonomy()
    assert "categories" in payload
    assert "noise_kinds" in payload


def test_get_meta_taxonomy_categories_matches_CATEGORIES():
    payload = get_meta_taxonomy()
    assert payload["categories"] == CATEGORIES


def test_get_meta_taxonomy_noise_kinds_matches_NOISE_KINDS():
    payload = get_meta_taxonomy()
    assert payload["noise_kinds"] == NOISE_KINDS


def test_get_meta_taxonomy_is_json_serializable():
    """Payload must be JSON-serializable (all plain Python types)."""
    import json
    payload = get_meta_taxonomy()
    serialized = json.dumps(payload)
    reparsed = json.loads(serialized)
    assert reparsed["categories"] == payload["categories"]
    assert reparsed["noise_kinds"] == payload["noise_kinds"]


def test_get_meta_taxonomy_no_fictional_kinds():
    """Noise kinds must NOT appear in any category (they are a separate surface)."""
    payload = get_meta_taxonomy()
    all_cat_kinds = {k for kinds in payload["categories"].values() for k in kinds}
    # None of the noise kinds should be in the categories dict
    for nk in payload["noise_kinds"]:
        assert nk not in all_cat_kinds, f"noise kind '{nk}' should not be in categories"
