"""Phase 1 — scope vocabulary + task-type → scope mapping + match semantics."""
from __future__ import annotations

import pytest

from magi_agent.customize.scope import (
    ALWAYS_SCOPE,
    DEFAULT_CURRENT_SCOPE,
    SCOPES,
    TASK_TYPE_TO_SCOPE,
    current_scope_from_task_profile,
    preset_scope_matches,
    scope_for_task_type,
)


def test_scope_vocabulary_matches_custom_rules_six_values() -> None:
    """Drift guard: customize/scope.py must speak the same vocabulary as
    customize/custom_rules.py — they both bind to the same UI selector."""
    from magi_agent.customize.custom_rules import SCOPES as CUSTOM_RULE_SCOPES

    assert SCOPES == CUSTOM_RULE_SCOPES


def test_always_scope_in_vocabulary() -> None:
    assert ALWAYS_SCOPE in SCOPES
    assert DEFAULT_CURRENT_SCOPE in SCOPES


def test_scope_for_task_type_coding() -> None:
    assert scope_for_task_type("coding") == "coding"


def test_scope_for_task_type_research_synonyms() -> None:
    assert scope_for_task_type("research") == "research"
    assert scope_for_task_type("web-acquisition") == "research"
    assert scope_for_task_type("browser-automation") == "research"


def test_scope_for_task_type_delivery_synonyms() -> None:
    for tt in ("artifact-delivery", "document", "office", "spreadsheet", "telegram"):
        assert scope_for_task_type(tt) == "delivery"


def test_scope_for_task_type_memory_synonyms() -> None:
    for tt in ("learning", "self-improvement"):
        assert scope_for_task_type(tt) == "memory"


def test_scope_for_task_type_task_synonyms() -> None:
    for tt in ("mission", "scheduled-work", "automation", "workflow", "superpowers"):
        assert scope_for_task_type(tt) == "task"


def test_scope_for_task_type_unknown_returns_none() -> None:
    assert scope_for_task_type("astronaut") is None
    assert scope_for_task_type("") is None


def test_scope_for_task_type_case_and_whitespace_tolerant() -> None:
    assert scope_for_task_type("  Coding  ") == "coding"


# --- current_scope_from_task_profile ---


def test_current_scope_none_profile_returns_default() -> None:
    assert current_scope_from_task_profile(None) == DEFAULT_CURRENT_SCOPE


def test_current_scope_empty_task_types_returns_default() -> None:
    assert current_scope_from_task_profile({"taskTypes": []}) == DEFAULT_CURRENT_SCOPE


def test_current_scope_single_coding_returns_coding() -> None:
    assert current_scope_from_task_profile({"taskTypes": ["coding"]}) == "coding"


def test_current_scope_coding_wins_over_other_classifications() -> None:
    """A turn that includes coding is a coding turn even when other types are
    present — coding signals always win."""
    assert (
        current_scope_from_task_profile({"taskTypes": ["research", "coding"]})
        == "coding"
    )


def test_current_scope_unknown_task_types_fallback_to_default() -> None:
    assert (
        current_scope_from_task_profile({"taskTypes": ["astronaut", "wizard"]})
        == DEFAULT_CURRENT_SCOPE
    )


def test_current_scope_snake_case_key_supported() -> None:
    assert current_scope_from_task_profile({"task_types": ["research"]}) == "research"


def test_current_scope_str_task_types_supported() -> None:
    assert current_scope_from_task_profile({"taskTypes": "delivery"}) == DEFAULT_CURRENT_SCOPE
    # "delivery" alone is not a registered task type; "document" is.
    assert current_scope_from_task_profile({"taskTypes": "document"}) == "delivery"


# --- preset_scope_matches ---


def test_preset_scope_matches_universal_always() -> None:
    assert preset_scope_matches(("always",), "coding") is True
    assert preset_scope_matches(("always",), "research") is True


def test_preset_scope_matches_exact_scope() -> None:
    assert preset_scope_matches(("coding",), "coding") is True


def test_preset_scope_does_not_match_other_scope() -> None:
    assert preset_scope_matches(("coding",), "research") is False


def test_preset_scope_multi_scope_matches_any() -> None:
    assert preset_scope_matches(("coding", "research"), "research") is True
    assert preset_scope_matches(("coding", "research"), "delivery") is False


def test_preset_scope_empty_tuple_matches_legacy_unscoped() -> None:
    """An empty scope tuple = "not classified yet" = match every turn so a
    legacy preset whose scope is not yet declared does not vanish."""
    assert preset_scope_matches((), "coding") is True
