"""``MAGI_FORCE_RECIPE=openmagi.source-grounded`` resolves to a real pack.

A prior probe showed that pinning ``openmagi.source-grounded`` failed CLOSED
with ``omission_reasons={'openmagi.source-grounded': ('explicit_recipe_missing',)}``
because the id existed ONLY as a reliability-policy recipe id, never as a
selectable first-party compiler pack in ``compiler._first_party_packs()``. The
explicit-selection path can only select a registered pack, so the pin could not
resolve.

These tests pin the fix: ``openmagi.source-grounded`` is now a registered
first-party pack, so

  * the compiler selects it (no ``explicit_recipe_missing``), and
  * the materialized live assembly requires ``verifier:research-source-evidence``
    (the named source-evidence ref the live source-ledger projector satisfies on
    a source-read turn and leaves missing on a source-less one).
"""
from __future__ import annotations

import pytest

from magi_agent.cli.real_runner import _build_default_runner_policy_assembly
from magi_agent.recipes.compiler import (
    AgentRecipeCompiler,
    PackRegistry,
    ProfileResolutionRequest,
)

_RECIPE_ID = "openmagi.source-grounded"
_SOURCE_EVIDENCE_REF = "verifier:research-source-evidence"


def _explicit_selection_request() -> ProfileResolutionRequest:
    """Mirror the runtimeContext the CLI builds for ``MAGI_FORCE_RECIPE``."""
    return ProfileResolutionRequest(
        taskProfile={"taskType": "research"},
        runtimeContext={
            "channel": "cli",
            "explicitRecipeSelection": {
                "mode": "this_turn",
                "requiredRecipeRefs": [{"recipeId": _RECIPE_ID}],
            },
        },
        recipePackConfig={},
    )


def test_pack_is_registered_as_selectable_first_party_pack() -> None:
    registry = PackRegistry.with_first_party_packs()
    assert _RECIPE_ID in registry.pack_ids
    pack = registry.get(_RECIPE_ID)
    # Selectable read-only pack: not hard-safety, opt-out + customizable so the
    # explicit-selection authorization/runtime-contract checks admit it.
    assert pack.hard_safety is False
    assert pack.opt_out_allowed is True
    assert pack.customizable is True
    assert _SOURCE_EVIDENCE_REF in pack.validator_refs


def test_explicit_selection_resolves_without_explicit_recipe_missing() -> None:
    compiler = AgentRecipeCompiler(PackRegistry.with_first_party_packs())
    snapshot = compiler.compile(_explicit_selection_request())

    omission = snapshot.recipe_selection.omission_reasons
    assert _RECIPE_ID not in omission
    assert "explicit_recipe_missing" not in omission.get(_RECIPE_ID, ())
    assert snapshot.recipe_selection.admission_blocked is False
    assert _RECIPE_ID in snapshot.selected_pack_ids
    assert _SOURCE_EVIDENCE_REF in snapshot.validator_refs


def test_force_recipe_env_selects_source_grounded_with_source_ref(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_FORCE_RECIPE", _RECIPE_ID)
    assembly = _build_default_runner_policy_assembly(
        model_provider="anthropic",
        model_label="anthropic/claude-sonnet-4-5",
        live_policy_callback_attached=True,
        task_profile={"taskType": "research"},
    )
    assert assembly is not None
    assert _RECIPE_ID in assembly.selected_pack_ids
    # The materialized assembly requires the named source-evidence ref, so a
    # source-read turn passes and a source-less turn blocks.
    assert _SOURCE_EVIDENCE_REF in assembly.required_validators
