"""Scope vocabulary drift guard.

PR-P5.3: the auto turn-scope classifier (current_scope_from_task_profile,
scope_for_task_type, TASK_TYPE_TO_SCOPE) and preset_scope_matches were removed
(zero production callers; the axis was never wired to enforcement and is now
retired). Only the vocabulary remains, so only the vocabulary is tested here.
"""
from __future__ import annotations

from magi_agent.customize.scope import ALWAYS_SCOPE, SCOPES


def test_scope_vocabulary_matches_custom_rules_six_values() -> None:
    """Drift guard: customize/scope.py must speak the same vocabulary as
    customize/custom_rules.py — they both bind to the same UI selector."""
    from magi_agent.customize.custom_rules import SCOPES as CUSTOM_RULE_SCOPES

    assert SCOPES == CUSTOM_RULE_SCOPES


def test_always_scope_in_vocabulary() -> None:
    assert ALWAYS_SCOPE in SCOPES
