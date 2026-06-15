"""Integration: real recipe -> materializer -> assembly -> pre-final gate.

Unlike ``test_source_ledger_gate_wiring`` (which hand-builds a narrow
``RunnerPolicyAssembly`` whose ONLY outstanding required validator is the named
source-evidence ref), these tests drive the REAL path:

    RecipeReliabilityPolicyRegistry.for_recipe("openmagi.source-grounded")
        -> RecipeMaterializer.materialize(...)               [force-merges hard
           -> plan.final_gate_policy.required_validators        validators]
              -> RunnerPolicyAssembly                           (as cli/real_runner builds it)
                 -> MagiEngineDriver._pre_final_gate_payload  [the live gate]

The point is to settle the user's "just make a recipe" claim HONESTLY: does
adding ``openmagi.source-grounded`` (requiring ``verifier:research-source-evidence``)
make a real non-coding turn pass on source-read and block on no-source, WITHOUT
weakening safety?

The recipe registry force-merges ``_HARD_VALIDATORS``
(``no_production_attachment``, ``public_redaction``) and ``_HARD_EVIDENCE``
(``redaction_audit``) into EVERY recipe. These are bare names (no
``verifier:`` / ``evidence:`` / ``sha256:`` prefix). The engine recomputes
``missing_validators``/``missing_evidence`` as a plain set-difference against
``observed_public_refs`` (which only ever holds PREFIXED public refs), and
nothing in the live engine path EMITS these bare hard refs. So they are always
missing and the gate always blocks — even on a perfect source-read turn whose
named ref IS matched.

These tests assert that reality (they would FAIL if the recipe alone were
enough), and pin the named-ref projection as the one piece that DOES work.
"""
from __future__ import annotations

import pytest

from magi_agent.cli.engine import MagiEngineDriver, RunnerPolicyAssembly
from magi_agent.recipes.compiler import RecipeSnapshot, build_recipe_snapshot_id
from magi_agent.recipes.materializer import RecipeMaterializer

_SOURCE_EVIDENCE_REF = "verifier:research-source-evidence"
_RECIPE_ID = "openmagi.source-grounded"
# The force-merged hard requirements (see reliability_policy._HARD_VALIDATORS /
# _HARD_EVIDENCE). Bare names, no public-ref prefix, no live producer.
_HARD_VALIDATORS = ("no_production_attachment", "public_redaction")
_HARD_EVIDENCE = ("redaction_audit",)


def _real_assembly() -> RunnerPolicyAssembly:
    """Build the assembly the way ``cli/real_runner`` does for this recipe.

    Drives the REAL compiler-snapshot -> materializer -> final-gate-policy path,
    then maps ``plan.final_gate_policy`` onto ``RunnerPolicyAssembly`` exactly as
    ``_build_default_runner_policy_assembly`` does (minus the dev-coding /
    disk-pack validator extras, which this read-only recipe does not trigger).
    """
    packs = (_RECIPE_ID,)
    snapshot = RecipeSnapshot(
        snapshotId=build_recipe_snapshot_id(packs),
        resolvedProfile={"taskType": "research"},
        selectedPackIds=packs,
        nonOptOutPackIds=packs,
    )
    # _materializer_model normalizes ("anthropic", anything) -> ("anthropic", "haiku").
    plan = RecipeMaterializer.with_reliability_defaults().materialize(
        snapshot,
        modelProvider="anthropic",
        modelLabel="haiku",
    )
    return RunnerPolicyAssembly(
        modelProvider="anthropic",
        modelLabel="anthropic/claude-sonnet-4-5",
        selectedPackIds=plan.selected_pack_ids,
        evidenceRequirements=plan.final_gate_policy.required_evidence,
        requiredValidators=plan.final_gate_policy.required_validators,
        missingEvidenceAction=plan.final_gate_policy.missing_evidence_action,
        repairPolicy={
            "action": plan.final_gate_policy.missing_evidence_action,
            "source": "recipe-materializer",
        },
        taskProfile={"taskType": "research"},
    )


def _source_record() -> dict[str, object]:
    return {
        "type": "SourceInspection",
        "status": "ok",
        "observedAt": 1000.0,
        "source": {"kind": "file", "toolName": "FileRead"},
    }


def _gate(
    assembly: RunnerPolicyAssembly,
    *,
    records: tuple[object, ...],
) -> dict[str, object]:
    driver = MagiEngineDriver(
        runner=object(),
        runner_policy_assembly=assembly,
        evidence_collector=lambda turn_id: records if turn_id == "t" else (),
    )
    payload = driver._pre_final_gate_payload(
        session_id="s",
        turn_id="t",
        prompt="read the file and summarize it",
        harness_state={"taskProfile": {"taskType": "research"}},
        observed_public_refs=set(),
        final_text="here is the summary",
    )
    assert payload is not None
    return payload


def test_recipe_materializes_named_ref_plus_force_merged_hard_requirements() -> None:
    """The real materializer puts the named ref AND the hard requirements in."""
    assembly = _real_assembly()
    assert assembly.selected_pack_ids == (_RECIPE_ID,)
    assert _SOURCE_EVIDENCE_REF in assembly.required_validators
    for hard in _HARD_VALIDATORS:
        assert hard in assembly.required_validators
    for hard in _HARD_EVIDENCE:
        assert hard in assembly.evidence_requirements


def test_flag_on_source_read_still_BLOCKS_due_to_force_merged_hard_validators(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE TRUTH: a perfect source-read turn STILL blocks under the new recipe.

    The named ref is matched (the projector works), but the force-merged hard
    validators / evidence are bare names with no live producer, so they remain
    missing and the gate blocks. "Just make a recipe" is NOT sufficient.
    """
    monkeypatch.setenv("MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED", "1")
    monkeypatch.setenv("MAGI_CODING_REPAIR_LOOP_ENABLED", "0")
    payload = _gate(_real_assembly(), records=(_source_record(),))

    # The source-evidence projector DID its job:
    assert _SOURCE_EVIDENCE_REF in payload["matchedRefs"]
    # ...but the gate STILL blocks, and ONLY on the force-merged hard refs:
    assert payload["decision"] == "block"
    assert set(payload["missingValidators"]) == set(_HARD_VALIDATORS)
    assert set(payload["missingEvidence"]) == set(_HARD_EVIDENCE)


def test_flag_off_source_read_blocks_on_named_ref_and_hard_refs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag OFF: named ref also missing, so block set includes it + hard refs."""
    monkeypatch.delenv("MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED", raising=False)
    monkeypatch.setenv("MAGI_CODING_REPAIR_LOOP_ENABLED", "0")
    payload = _gate(_real_assembly(), records=(_source_record(),))

    assert payload["decision"] == "block"
    assert _SOURCE_EVIDENCE_REF not in payload["matchedRefs"]
    assert _SOURCE_EVIDENCE_REF in payload["missingValidators"]
    assert set(_HARD_VALIDATORS).issubset(set(payload["missingValidators"]))


def test_no_source_blocks_on_named_ref_plus_hard_refs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag ON but no source read: named ref absent, hard refs still missing."""
    monkeypatch.setenv("MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED", "1")
    monkeypatch.setenv("MAGI_CODING_REPAIR_LOOP_ENABLED", "0")
    non_source = {
        "schemaVersion": "openmagi.localToolEvidenceReceipt.v1",
        "toolName": "Bash",
        "status": "ok",
        "receiptRefs": [],
        "evidenceRefs": [],
    }
    payload = _gate(_real_assembly(), records=(non_source,))

    assert payload["decision"] == "block"
    assert _SOURCE_EVIDENCE_REF in payload["missingValidators"]
    assert set(_HARD_VALIDATORS).issubset(set(payload["missingValidators"]))
