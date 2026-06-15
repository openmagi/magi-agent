"""``MAGI_FORCE_RECIPE`` pins the compiler recipe selection for a live CLI turn.

The default ``_build_default_runner_policy_assembly`` builds the compile request
with ``runtimeContext={"channel": "cli"}`` and NO explicit recipe selection, so
the compiler selects recipes automatically from the task profile. A live demo
turn sometimes needs to PIN exactly one recipe (e.g. so a source-grounded read
turn is exercised regardless of how the prompt classifies).

The compiler already honors an ``explicitRecipeSelection`` block on the runtime
context (the hosted-only selection path). These tests assert that setting
``MAGI_FORCE_RECIPE=<registered recipe/pack id>`` reuses that exact path so the
assembled plan selects (only) that recipe, and that leaving the env var unset
(or empty) leaves selection byte-identical to today.
"""
from __future__ import annotations

import pytest

from magi_agent.cli.real_runner import _build_default_runner_policy_assembly

# A registered first-party compiler pack id (see compiler._first_party_packs).
# ``openmagi.document-review`` is a read-only recipe that is NOT auto-selected
# for the research task profile used below, so forcing it is observable.
_FORCED_RECIPE = "openmagi.document-review"


def _assemble():
    return _build_default_runner_policy_assembly(
        model_provider="anthropic",
        model_label="anthropic/claude-sonnet-4-5",
        live_policy_callback_attached=True,
        task_profile={"taskType": "research"},
    )


def test_force_recipe_unset_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAGI_FORCE_RECIPE", raising=False)
    assembly = _assemble()
    assert assembly is not None
    # The research task profile auto-selects research packs and never the
    # document-review recipe.
    assert _FORCED_RECIPE not in assembly.selected_pack_ids
    assert "openmagi.research" in assembly.selected_pack_ids


def test_force_recipe_empty_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_FORCE_RECIPE", "")
    assembly = _assemble()
    assert assembly is not None
    assert _FORCED_RECIPE not in assembly.selected_pack_ids
    assert "openmagi.research" in assembly.selected_pack_ids


def test_force_recipe_blank_whitespace_is_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_FORCE_RECIPE", "   ")
    assembly = _assemble()
    assert assembly is not None
    assert _FORCED_RECIPE not in assembly.selected_pack_ids
    assert "openmagi.research" in assembly.selected_pack_ids


def test_force_recipe_pins_the_selected_recipe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_FORCE_RECIPE", _FORCED_RECIPE)
    assembly = _assemble()
    assert assembly is not None
    # The forced recipe is now selected even though the research task profile
    # would not auto-select it.
    assert _FORCED_RECIPE in assembly.selected_pack_ids
