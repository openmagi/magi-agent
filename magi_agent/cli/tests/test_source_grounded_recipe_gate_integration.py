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
from magi_agent.recipes.compiler import (
    AgentRecipeCompiler,
    PackRegistry,
    ProfileResolutionRequest,
    RecipeSnapshot,
    build_recipe_snapshot_id,
)
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
    final_text: str = "here is the summary",
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
        final_text=final_text,
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


_REAL_JWT = ".".join(
    (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
        "eyJzdWIiOiIxMjM0NTY3ODkwIn0",
        "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c",
    )
)


def test_flag_on_clean_source_read_PASSES_full_recipe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE PAYOFF: a clean source-read turn now PASSES the whole recipe gate.

    The named source ref is matched AND the three force-merged hard refs
    (no_production_attachment / public_redaction / redaction_audit) are now
    satisfied by their live satisfiers, so a perfect non-coding source-grounded
    turn passes the pre-final gate — with NO safety guard removed.
    """
    monkeypatch.setenv("MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED", "1")
    monkeypatch.setenv("MAGI_CODING_REPAIR_LOOP_ENABLED", "0")
    payload = _gate(
        _real_assembly(),
        records=(_source_record(),),
        final_text="The file at /Users/kevin/notes.md mentions the token economy.",
    )

    assert payload["decision"] == "pass"
    assert payload["missingValidators"] == []
    assert payload["missingEvidence"] == []
    assert _SOURCE_EVIDENCE_REF in payload["matchedRefs"]
    for hard in (*_HARD_VALIDATORS, *_HARD_EVIDENCE):
        assert hard in payload["matchedRefs"]


def test_flag_on_credential_leak_BLOCKS_on_public_redaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A source-read turn that leaks a credential blocks on public_redaction.

    The redaction satisfiers refuse to emit ``public_redaction`` /
    ``redaction_audit`` when a real credential is present, so the gate blocks —
    proving the wiring is a genuine guard, not a rubber stamp.
    """
    monkeypatch.setenv("MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED", "1")
    monkeypatch.setenv("MAGI_CODING_REPAIR_LOOP_ENABLED", "0")
    payload = _gate(
        _real_assembly(),
        records=(_source_record(),),
        final_text=f"The file leaked this token: {_REAL_JWT}",
    )

    assert payload["decision"] == "block"
    # source ref + the production-attachment invariant are satisfied...
    assert _SOURCE_EVIDENCE_REF in payload["matchedRefs"]
    assert "no_production_attachment" in payload["matchedRefs"]
    # ...but the credential keeps the redaction refs missing.
    assert "public_redaction" in payload["missingValidators"]
    assert "redaction_audit" in payload["missingEvidence"]


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


def test_no_source_blocks_on_named_ref(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag ON but no source read: the hard refs are satisfied on a clean turn,
    yet the gate still BLOCKS because the named source ref is missing."""
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
    # the named source ref is the ONLY thing still missing: source grounding
    # is genuinely required and a source-less turn cannot pass.
    assert payload["missingValidators"] == [_SOURCE_EVIDENCE_REF]
    assert payload["missingEvidence"] == []
    for hard in (*_HARD_VALIDATORS, *_HARD_EVIDENCE):
        assert hard in payload["matchedRefs"]


# ---------------------------------------------------------------------------
# FULL real-compiler-path tests
#
# ``_real_assembly`` above hand-builds a snapshot whose ``selectedPackIds`` is
# ONLY ``openmagi.source-grounded``, so only that recipe's reliability policy
# (plus the force-merged context-safety hard refs) is materialized. The LIVE
# runner instead compiles via ``AgentRecipeCompiler`` with the explicit
# selection ``MAGI_FORCE_RECIPE`` uses, which force-selects the mandatory
# hard-safety packs (context-safety + evidence) as well. The tests below drive
# THAT path so the gate faces the full required-ref set a live turn sees.
# ---------------------------------------------------------------------------


def _compiled_assembly() -> RunnerPolicyAssembly:
    """Build the assembly the live runner builds for a forced source-grounded turn.

    Mirrors ``cli/real_runner._build_default_runner_policy_assembly`` exactly:
    explicit ``this_turn`` selection with ``allowAdditionalAutoRecipes=False``,
    compiled by the real ``AgentRecipeCompiler`` so the mandatory hard-safety
    packs are force-selected, then materialized to the final-gate policy.
    """
    runtime_context = {
        "channel": "cli",
        "explicitRecipeSelection": {
            "mode": "this_turn",
            "requiredRecipeRefs": [{"recipeId": _RECIPE_ID}],
            "allowAdditionalAutoRecipes": False,
        },
    }
    snapshot = AgentRecipeCompiler(PackRegistry.with_first_party_packs()).compile(
        ProfileResolutionRequest(
            taskProfile={"taskType": "research"},
            runtimeContext=runtime_context,
            recipePackConfig={},
        ),
        env={"MAGI_RECIPE_DEFAULT_PACKS_EXPANDED": "0"},
    )
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


def _projected_source_record() -> object:
    """A live ``LocalResearchSourceLedger`` source-read projected via the EXISTING
    ``SourceLedgerRecord.to_evidence_record()`` — exactly what the collector
    surfaces for a turn that read one source."""
    from magi_agent.evidence.source_ledger import LocalResearchSourceLedger

    ledger = LocalResearchSourceLedger(
        ledgerId="ledger:test",
        sessionId="session:test",
        turnId="t",
    )
    record = ledger.record_source(
        {
            "turnId": "t",
            "toolName": "DocumentRead",
            "toolUseId": "DocumentRead:local",
            "evidenceType": "SourceInspection",
            "kind": "file",
            "uri": "workspace://notes.md",
            "inspected": True,
            "contentHash": "deadbeef" * 8,
            "contentType": "text/plain",
            "snippets": ("the token economy section",),
            "metadata": {"pathRef": "notes.md"},
        }
    )
    return record.to_evidence_record()


def test_compiled_path_requires_the_full_force_selected_ref_set() -> None:
    """The live compiled path force-selects context-safety + evidence packs.

    Documents the full required-ref surface the gate must satisfy (web-acquisition
    refs are GONE once its dep is dropped from source-grounded — item 1)."""
    assembly = _compiled_assembly()
    assert "openmagi.context-safety" in assembly.selected_pack_ids
    assert "openmagi.evidence" in assembly.selected_pack_ids
    # item 1: web-acquisition dep removed ⇒ pack not selected, refs absent
    assert "openmagi.web-acquisition" not in assembly.selected_pack_ids
    for gone in (
        "no_auth_bypass",
        "source_quality",
        "verifier:web-acquisition:provider-boundary",
    ):
        assert gone not in assembly.required_validators
    assert "source_ledger" not in assembly.evidence_requirements
    assert "evidence:web-acquisition:source-ledger-input" not in assembly.evidence_requirements
    # source-grounded keeps its OWN named refs
    assert _SOURCE_EVIDENCE_REF in assembly.required_validators
    assert "evidence:inspected-source" in assembly.evidence_requirements


def test_compiled_path_clean_source_read_FULL_PASS(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE LIVE PAYOFF: a clean projected source-read PASSES the FULL compiled gate
    with EMPTY missingValidators AND missingEvidence — no safety guard removed."""
    monkeypatch.setenv("MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED", "1")
    monkeypatch.setenv("MAGI_CODING_REPAIR_LOOP_ENABLED", "0")
    payload = _gate(
        _compiled_assembly(),
        records=(_projected_source_record(),),
        final_text="The notes.md file discusses the token economy in plain prose.",
    )
    assert payload["decision"] == "pass"
    assert payload["missingValidators"] == []
    assert payload["missingEvidence"] == []
    assert _SOURCE_EVIDENCE_REF in payload["matchedRefs"]
    assert "evidence:inspected-source" in payload["matchedRefs"]


def test_compiled_path_no_source_BLOCKS_on_source_refs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No source read ⇒ the source-evidence refs stay missing ⇒ BLOCK, even though
    every redaction / runtime-record / no-block-mode ref is cleanly satisfied."""
    monkeypatch.setenv("MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED", "1")
    monkeypatch.setenv("MAGI_CODING_REPAIR_LOOP_ENABLED", "0")
    non_source = {
        "schemaVersion": "openmagi.localToolEvidenceReceipt.v1",
        "toolName": "Bash",
        "status": "ok",
        "receiptRefs": [],
        "evidenceRefs": [],
    }
    payload = _gate(_compiled_assembly(), records=(non_source,))
    assert payload["decision"] == "block"
    assert _SOURCE_EVIDENCE_REF in payload["missingValidators"]
    assert "evidence:inspected-source" in payload["missingEvidence"]


def test_compiled_path_credential_leak_BLOCKS_on_redaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A credential in the final text keeps the redaction refs missing ⇒ BLOCK,
    on BOTH the bare and prefixed context-safety redaction aliases."""
    monkeypatch.setenv("MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED", "1")
    monkeypatch.setenv("MAGI_CODING_REPAIR_LOOP_ENABLED", "0")
    payload = _gate(
        _compiled_assembly(),
        records=(_projected_source_record(),),
        final_text=f"The file leaked this token: {_REAL_JWT}",
    )
    assert payload["decision"] == "block"
    assert _SOURCE_EVIDENCE_REF in payload["matchedRefs"]
    assert "public_redaction" in payload["missingValidators"]
    assert "validator:context-safety:public-redaction" in payload["missingValidators"]
    assert "redaction_audit" in payload["missingEvidence"]
    assert "evidence:context-safety-redaction" in payload["missingEvidence"]
