"""Integration tests — prove pinned recipeRefs bind child gates/validators/instructions.

TY3 coverage
============
A) Binding proof (compiler level, flag-agnostic):
   Pinning ``openmagi.research`` → RecipeSnapshot gains research's distinctive
   validator/approval_gate/instruction refs that are absent in the no-pin baseline.

B) Baseline contrast:
   No pin → those same distinctive refs are NOT present in the snapshot.
   Proves the pin, not default-selection, caused the binding.

C) Runner-policy level (no live model):
   ``_build_default_runner_policy_assembly`` with ``pinned_recipe_pack_ids``
   produces a ``RunnerPolicyAssembly`` whose ``selected_pack_ids`` includes
   ``openmagi.research`` and whose ``required_validators`` contains the research
   reliability-policy validators (``citation_support``, ``fact_grounding``).

The chosen pack — ``openmagi.research`` — has these DISTINCTIVE refs absent in the
default (no-pin) compiled snapshot (compiler.py ~2246-2275):
  validator_refs   : ("validator:research:citation-support",
                       "validator:research:fact-grounding",
                       "validator:research:evidence-checks")
  approval_gate_refs: ("approval:research:external-source-use",)
  instruction_refs  : ("instruction:research:source-policy",)

``openmagi.research`` depends on ``openmagi.web-acquisition``, which is auto-pulled
in via dependency resolution (select() recursion in ProfileResolver).

The assertion surface (RecipeSnapshot fields):
  snapshot.validator_refs         — per-pack validator refs aggregated
  snapshot.approval_gate_refs     — per-pack approval gate refs aggregated
  snapshot.instruction_refs       — per-pack instruction refs aggregated
  snapshot.selected_pack_ids      — selected pack membership

RunnerPolicyAssembly (cli/engine.py):
  assembly.selected_pack_ids      — from plan.selected_pack_ids
  assembly.required_validators    — from plan.final_gate_policy.required_validators
                                    + reliability-policy merge
"""
from __future__ import annotations

import os
import pytest


# ---------------------------------------------------------------------------
# Helpers: distinctive refs of openmagi.research
# ---------------------------------------------------------------------------

_RESEARCH_PACK_ID = "openmagi.research"

# Distinctive validator refs declared in the openmagi.research RecipePackManifest
# (compiler.py:2263-2267).  All three are ABSENT when no pin is applied.
_RESEARCH_VALIDATOR_REFS = frozenset({
    "validator:research:citation-support",
    "validator:research:fact-grounding",
    "validator:research:evidence-checks",
})

# Distinctive approval-gate ref (compiler.py:2268)
_RESEARCH_APPROVAL_GATE_REF = "approval:research:external-source-use"

# Distinctive instruction ref (compiler.py:2262)
_RESEARCH_INSTRUCTION_REF = "instruction:research:source-policy"

# Reliability-policy validator refs produced for openmagi.research by
# RecipeReliabilityPolicyRegistry (reliability_policy.py:136)
_RESEARCH_RELIABILITY_VALIDATORS = frozenset({"citation_support", "fact_grounding"})


# ---------------------------------------------------------------------------
# Shared compiler/registry setup
# ---------------------------------------------------------------------------


def _make_compiler():
    """Return an AgentRecipeCompiler backed by the first-party pack registry."""
    from magi_agent.recipes.compiler import AgentRecipeCompiler, PackRegistry

    return AgentRecipeCompiler(PackRegistry.with_first_party_packs())


def _make_request(*, pinned_pack_id: str | None = None):
    """Build a ProfileResolutionRequest, optionally with an explicit recipe pin."""
    from magi_agent.recipes.compiler import ProfileResolutionRequest

    runtime_context: dict[str, object] = {"channel": "cli"}
    if pinned_pack_id is not None:
        runtime_context["explicitRecipeSelection"] = {
            "mode": "this_turn",
            "requiredRecipeRefs": [{"recipeId": pinned_pack_id}],
            "allowAdditionalAutoRecipes": True,
        }
    return ProfileResolutionRequest(
        taskProfile={},
        runtimeContext=runtime_context,
        recipePackConfig={},
    )


# ---------------------------------------------------------------------------
# A — Binding proof (compiler level)
# ---------------------------------------------------------------------------


def test_pin_research_binds_validator_refs():
    """Pinning openmagi.research → snapshot.validator_refs includes all three
    distinctive research validator refs."""
    compiler = _make_compiler()
    snapshot = compiler.compile(_make_request(pinned_pack_id=_RESEARCH_PACK_ID))

    assert _RESEARCH_PACK_ID in snapshot.selected_pack_ids, (
        f"openmagi.research not in selected_pack_ids: {snapshot.selected_pack_ids}"
    )
    for ref in _RESEARCH_VALIDATOR_REFS:
        assert ref in snapshot.validator_refs, (
            f"Expected validator ref {ref!r} missing from snapshot.validator_refs: "
            f"{snapshot.validator_refs}"
        )


def test_pin_research_binds_approval_gate_ref():
    """Pinning openmagi.research → snapshot.approval_gate_refs includes the
    distinctive external-source-use gate."""
    compiler = _make_compiler()
    snapshot = compiler.compile(_make_request(pinned_pack_id=_RESEARCH_PACK_ID))

    assert _RESEARCH_APPROVAL_GATE_REF in snapshot.approval_gate_refs, (
        f"Expected approval gate ref {_RESEARCH_APPROVAL_GATE_REF!r} missing from "
        f"snapshot.approval_gate_refs: {snapshot.approval_gate_refs}"
    )


def test_pin_research_binds_instruction_ref():
    """Pinning openmagi.research → snapshot.instruction_refs includes the
    distinctive source-policy instruction."""
    compiler = _make_compiler()
    snapshot = compiler.compile(_make_request(pinned_pack_id=_RESEARCH_PACK_ID))

    assert _RESEARCH_INSTRUCTION_REF in snapshot.instruction_refs, (
        f"Expected instruction ref {_RESEARCH_INSTRUCTION_REF!r} missing from "
        f"snapshot.instruction_refs: {snapshot.instruction_refs}"
    )


# ---------------------------------------------------------------------------
# B — Baseline contrast (no pin → distinctive refs absent)
# ---------------------------------------------------------------------------


def test_no_pin_baseline_lacks_research_validator_refs():
    """No pin → snapshot.validator_refs does NOT contain research-specific validators
    (proves the pin is what caused binding, not automatic default-selection)."""
    compiler = _make_compiler()
    snapshot = compiler.compile(_make_request())  # no pin

    assert _RESEARCH_PACK_ID not in snapshot.selected_pack_ids, (
        f"openmagi.research should NOT be selected without a pin; "
        f"selected_pack_ids={snapshot.selected_pack_ids}"
    )
    for ref in _RESEARCH_VALIDATOR_REFS:
        assert ref not in snapshot.validator_refs, (
            f"Baseline snapshot unexpectedly contains research validator ref {ref!r}; "
            f"snapshot.validator_refs={snapshot.validator_refs}"
        )


def test_no_pin_baseline_lacks_research_approval_gate_ref():
    """No pin → approval:research:external-source-use absent from snapshot."""
    compiler = _make_compiler()
    snapshot = compiler.compile(_make_request())

    assert _RESEARCH_APPROVAL_GATE_REF not in snapshot.approval_gate_refs, (
        f"Baseline snapshot unexpectedly contains {_RESEARCH_APPROVAL_GATE_REF!r}; "
        f"snapshot.approval_gate_refs={snapshot.approval_gate_refs}"
    )


def test_no_pin_baseline_lacks_research_instruction_ref():
    """No pin → instruction:research:source-policy absent from snapshot."""
    compiler = _make_compiler()
    snapshot = compiler.compile(_make_request())

    assert _RESEARCH_INSTRUCTION_REF not in snapshot.instruction_refs, (
        f"Baseline snapshot unexpectedly contains {_RESEARCH_INSTRUCTION_REF!r}; "
        f"snapshot.instruction_refs={snapshot.instruction_refs}"
    )


# ---------------------------------------------------------------------------
# C — Runner-policy level (no live model)
# ---------------------------------------------------------------------------


def test_runner_policy_assembly_with_research_pin(monkeypatch):
    """_build_default_runner_policy_assembly with pinned_recipe_pack_ids=
    ('openmagi.research',) produces an assembly whose selected_pack_ids includes
    openmagi.research and whose required_validators contains the reliability-policy
    validators for research.

    Uses a minimal non-empty task_profile (no 'research' in taskTypes) so the only
    route for openmagi.research to appear is via the explicit pin, keeping the test
    focused.

    Note: task_profile={} (empty) is falsy, so real_runner.py line
    ``dict(task_profile or _DEFAULT_FIRST_PARTY_TASK_PROFILE)`` would fall through
    to the default profile (which has 'research' in taskTypes).  We therefore pass
    a non-empty dict with a neutral marker to reliably suppress the default.
    """
    # Enable the evidence completion gate (default ON; be explicit for test isolation)
    monkeypatch.setenv("MAGI_EVIDENCE_COMPLETION_GATE_ENABLED", "1")
    # Clear MAGI_FORCE_RECIPE so our pin is the only explicit selection
    monkeypatch.delenv("MAGI_FORCE_RECIPE", raising=False)

    from magi_agent.cli.real_runner import _build_default_runner_policy_assembly

    # Non-empty task_profile with a neutral channel marker (no taskTypes that would
    # auto-select research via taskProfileSelectors).
    neutral_task_profile: dict[str, object] = {"channel": "test-isolation"}

    assembly = _build_default_runner_policy_assembly(
        model_provider="anthropic",
        model_label="claude-sonnet-4-6",
        live_policy_callback_attached=False,
        task_profile=neutral_task_profile,
        pinned_recipe_pack_ids=(_RESEARCH_PACK_ID,),
    )

    assert assembly is not None, (
        "_build_default_runner_policy_assembly returned None with gate enabled; "
        "check MAGI_EVIDENCE_COMPLETION_GATE_ENABLED propagation"
    )

    assert _RESEARCH_PACK_ID in assembly.selected_pack_ids, (
        f"openmagi.research not in assembly.selected_pack_ids: {assembly.selected_pack_ids}"
    )

    for validator in _RESEARCH_RELIABILITY_VALIDATORS:
        assert validator in assembly.required_validators, (
            f"Expected research reliability validator {validator!r} missing from "
            f"assembly.required_validators: {assembly.required_validators}"
        )


def test_runner_policy_assembly_baseline_lacks_research_validators(monkeypatch):
    """_build_default_runner_policy_assembly with no pin AND a task-profile that does
    NOT include 'research' in taskTypes → assembly.selected_pack_ids does NOT include
    openmagi.research and research reliability validators are absent.

    Note: ``_DEFAULT_FIRST_PARTY_TASK_PROFILE`` (used when task_profile=None)
    includes "research" in taskTypes, which auto-selects openmagi.research via its
    taskProfileSelectors.  We therefore supply an explicit minimal task_profile so
    the only way research could appear is via an explicit pin — which we don't supply.
    This cleanly proves the pin is what caused binding in the C-binding test above.
    """
    monkeypatch.setenv("MAGI_EVIDENCE_COMPLETION_GATE_ENABLED", "1")
    monkeypatch.delenv("MAGI_FORCE_RECIPE", raising=False)

    from magi_agent.cli.real_runner import _build_default_runner_policy_assembly

    # Non-empty task_profile with no task type that would auto-select research.
    # Must be non-empty: real_runner.py uses ``task_profile or _DEFAULT_FIRST_PARTY_TASK_PROFILE``
    # which falls through to the default (containing 'research') when the value is falsy.
    neutral_task_profile: dict[str, object] = {"channel": "test-isolation"}

    assembly = _build_default_runner_policy_assembly(
        model_provider="anthropic",
        model_label="claude-sonnet-4-6",
        live_policy_callback_attached=False,
        task_profile=neutral_task_profile,
        pinned_recipe_pack_ids=(),
    )

    assert assembly is not None, (
        "_build_default_runner_policy_assembly returned None with gate enabled"
    )

    assert _RESEARCH_PACK_ID not in assembly.selected_pack_ids, (
        f"openmagi.research unexpectedly selected without pin; "
        f"selected_pack_ids={assembly.selected_pack_ids}"
    )

    for validator in _RESEARCH_RELIABILITY_VALIDATORS:
        assert validator not in assembly.required_validators, (
            f"Research reliability validator {validator!r} unexpectedly present without pin; "
            f"assembly.required_validators={assembly.required_validators}"
        )
