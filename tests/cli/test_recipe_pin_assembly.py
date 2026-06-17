"""User-explicit recipe pin injected into pre-turn assembly.

Task 2 tests: ``_build_default_runner_policy_assembly`` accepts
``pinned_recipe_pack_ids`` and injects them as ``explicitRecipeSelection``
into the compiler request, causing the pinned pack's obligations (validators,
evidence requirements) to enter the frozen baseline.
"""
from __future__ import annotations

from magi_agent.cli.real_runner import _build_default_runner_policy_assembly


def _assembly(pins):
    return _build_default_runner_policy_assembly(
        model_provider="openai",
        model_label="gpt-5.5",
        live_policy_callback_attached=True,
        task_profile={"taskTypes": ["research"]},   # non-coding baseline
        pinned_recipe_pack_ids=pins,
    )


def test_pin_injects_coding_obligation_into_baseline():
    asm = _assembly(["openmagi.dev-coding"])
    assert asm is not None
    assert "openmagi.dev-coding" in asm.selected_pack_ids
    assert any("dev-coding" in v for v in asm.required_validators)


def test_no_pin_excludes_coding_obligation():
    asm = _assembly([])
    assert asm is not None
    assert "openmagi.dev-coding" not in asm.selected_pack_ids
