"""PR5 — Workflow-as-recipe + reuse ("save as command").

Track 17 PR5 makes dynamic workflows DEFINABLE as recipes (reusing the EXISTING
``recipes/compiler.py`` ``RecipeSnapshot`` + ``recipes/materializer.py``) and
REUSABLE by name (promoting the slash-command import metadata to a LIVE
reusable registry entry following the ``workflows/registry.py`` pattern).

Mandatory behaviours locked here:

1. **recipe → materialize → validate → execute round-trip.** A recipe-defined
   workflow materializes (via the real ``RecipeMaterializer``), compiles into a
   ``CompiledWorkflowContract``, passes ``validate_compiled_workflow()``, and
   executes through ``execute_workflow`` — the same governance gate PR1 locks.

2. **saved workflow re-invocable by name.** A workflow saved into the live
   ``SavedWorkflowRegistry`` is looked up by name, RE-materialized, RE-validated,
   and executed — proving lossless reuse (the registry holds the recipe, not a
   frozen contract snapshot).

3. **deep-research recipe produces a cited, cross-checked result.** The bundled
   ``deep-research`` recipe fans out research children, runs ``cross_review``
   filtering (a claim no peer corroborates is genuinely removed), and assembles a
   cited synthesis via ``final_assembly`` (citations = accepted child evidence
   refs that survived cross-review).

Invariants preserved:
- a recipe that fails ``validate_compiled_workflow`` does NOT execute;
- default-OFF (registering/looking up is metadata; EXECUTING obeys
  ``MAGI_WORKFLOW_EXECUTOR_ENABLED``).
"""
from __future__ import annotations

import asyncio

import pytest


# ---------------------------------------------------------------------------
# Helpers — local-fake child runner reused across tests
# ---------------------------------------------------------------------------

_FAKE_EVIDENCE_REF = "evidence:abcdef1234567890"


class _FakeChildRunner:
    openmagi_local_fake_provider = True

    def __init__(self) -> None:
        self.calls = 0

    async def run_child(self, request: object) -> dict[str, object]:
        self.calls += 1
        return {
            "childExecutionId": "child:1234567890abcdef",
            "status": "completed",
            "summary": "fake completed",
            "evidenceRefs": (_FAKE_EVIDENCE_REF,),
            "artifactRefs": (),
            "auditEventRefs": (),
        }


def _deep_research_topology() -> tuple[dict[str, object], ...]:
    """Three peer research agents; one orphan claim must be filtered out."""
    return (
        {"agent_ref": "peer:research-0", "claim_refs": ("claim:finding-a",)},
        {
            "agent_ref": "peer:research-1",
            "claim_refs": ("claim:finding-a", "claim:finding-b"),
        },
        {
            "agent_ref": "peer:research-2",
            "claim_refs": ("claim:finding-b", "claim:finding-orphan"),
        },
    )


# ---------------------------------------------------------------------------
# Test 1 — recipe → materialize → validate → execute round-trip
# ---------------------------------------------------------------------------

def test_recipe_materialize_validate_execute_round_trip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A recipe-defined workflow materializes, passes validation, and executes."""
    monkeypatch.setenv("MAGI_WORKFLOW_EXECUTOR_ENABLED", "1")

    from openmagi_core_agent.recipes.compiler import (
        AgentRecipeCompiler,
        PackRegistry,
        ProfileResolutionRequest,
    )
    from openmagi_core_agent.recipes.materializer import RecipeMaterializer
    from openmagi_core_agent.recipes.workflow_recipe import (
        compile_workflow_from_recipe,
    )
    from openmagi_core_agent.harness.workflow_executor import (
        WorkflowExecutorConfig,
        execute_workflow,
    )
    from openmagi_core_agent.workflows.compiler import (
        CompiledWorkflowContract,
        validate_compiled_workflow,
    )

    # Define the workflow as a recipe (EXISTING compiler).
    snapshot = AgentRecipeCompiler(PackRegistry.with_first_party_packs()).compile(
        ProfileResolutionRequest(taskProfile={"taskType": "research"})
    )
    # Materialize via the EXISTING materializer.
    plan = RecipeMaterializer.with_reliability_defaults().materialize(
        snapshot,
        modelProvider="google",
        modelLabel="gemini-3.5-flash",
    )

    # Bridge recipe → CompiledWorkflowContract (the PR5 materialization path).
    contract = compile_workflow_from_recipe(
        snapshot,
        plan,
        workflow_id="openmagi.recipe.research",
        version="1.0.0",
    )
    assert isinstance(contract, CompiledWorkflowContract)

    # Governance gate: the bridged contract must validate.
    verdict = validate_compiled_workflow(contract)
    assert verdict.ok, f"recipe contract failed validation: {verdict.reason_codes}"

    # Execute through the real executor path.
    config = WorkflowExecutorConfig(enabled=True, local_fake_child_runner_enabled=True)
    result = asyncio.run(
        execute_workflow(contract, config=config, child_runner=_FakeChildRunner())
    )
    assert result.status in {"accepted", "partial"}
    assert result.workflow_id == "openmagi.recipe.research"
    assert result.child_tasks_dispatched > 0


def test_recipe_that_fails_validation_does_not_execute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bridged contract that fails validation must NOT dispatch any children."""
    monkeypatch.setenv("MAGI_WORKFLOW_EXECUTOR_ENABLED", "1")

    from openmagi_core_agent.recipes.compiler import (
        AgentRecipeCompiler,
        PackRegistry,
        ProfileResolutionRequest,
    )
    from openmagi_core_agent.recipes.materializer import RecipeMaterializer
    from openmagi_core_agent.recipes.workflow_recipe import (
        compile_workflow_from_recipe,
    )
    from openmagi_core_agent.harness.workflow_executor import (
        WorkflowExecutorConfig,
        execute_workflow,
    )

    snapshot = AgentRecipeCompiler(PackRegistry.with_first_party_packs()).compile(
        ProfileResolutionRequest(taskProfile={"taskType": "research"})
    )
    plan = RecipeMaterializer.with_reliability_defaults().materialize(
        snapshot,
        modelProvider="google",
        modelLabel="gemini-3.5-flash",
    )
    # Inject a hard-denied tool into the allowlist → forces a validation failure
    # without touching any locked authority flag.
    contract = compile_workflow_from_recipe(
        snapshot,
        plan,
        workflow_id="openmagi.recipe.research",
        version="1.0.0",
        extra_tool_allowlist=("Bash",),  # HARD_DENIED_TOOLS member
        extra_available_tools=("Bash",),
    )

    runner = _FakeChildRunner()
    config = WorkflowExecutorConfig(enabled=True, local_fake_child_runner_enabled=True)
    result = asyncio.run(
        execute_workflow(contract, config=config, child_runner=runner)
    )
    assert result.status == "validation_failed"
    assert result.child_tasks_dispatched == 0
    assert runner.calls == 0


# ---------------------------------------------------------------------------
# Test 2 — saved workflow re-invocable by name
# ---------------------------------------------------------------------------

def test_saved_workflow_reinvocable_by_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """Register a workflow, look it up by name, re-materialize/validate/execute."""
    monkeypatch.setenv("MAGI_WORKFLOW_EXECUTOR_ENABLED", "1")

    from openmagi_core_agent.recipes.compiler import (
        AgentRecipeCompiler,
        PackRegistry,
        ProfileResolutionRequest,
    )
    from openmagi_core_agent.recipes.workflow_recipe import (
        SavedWorkflowEntry,
        SavedWorkflowRegistry,
    )
    from openmagi_core_agent.harness.workflow_executor import (
        WorkflowExecutorConfig,
        execute_workflow,
    )
    from openmagi_core_agent.workflows.compiler import validate_compiled_workflow

    snapshot = AgentRecipeCompiler(PackRegistry.with_first_party_packs()).compile(
        ProfileResolutionRequest(taskProfile={"taskType": "research"})
    )

    # "Save as command" — register the recipe by name (metadata, always-available).
    registry = SavedWorkflowRegistry.empty()
    saved = registry.register(
        SavedWorkflowEntry.from_recipe(
            name="my-research",
            version="1.0.0",
            snapshot=snapshot,
            workflow_id="openmagi.saved.my-research",
            model_provider="google",
            model_label="gemini-3.5-flash",
        )
    )
    assert "my-research" in registry.names()

    # Look up by name — the registry holds the recipe, not a frozen contract.
    looked_up = registry.get("my-research")
    assert looked_up.name == "my-research"
    assert looked_up.snapshot.snapshot_id == snapshot.snapshot_id

    # Re-materialize + re-validate from the saved recipe (proves reuse is lossless).
    contract = registry.resolve_contract("my-research")
    verdict = validate_compiled_workflow(contract)
    assert verdict.ok, f"re-resolved contract failed validation: {verdict.reason_codes}"

    # Execute the re-resolved contract.
    config = WorkflowExecutorConfig(enabled=True, local_fake_child_runner_enabled=True)
    result = asyncio.run(
        execute_workflow(contract, config=config, child_runner=_FakeChildRunner())
    )
    assert result.status in {"accepted", "partial"}
    assert result.workflow_id == "openmagi.saved.my-research"
    _ = saved


def test_saved_registry_promotes_slash_command_import_metadata() -> None:
    """The live registry entry carries the slash-command import provenance that
    the recipe compiler's superpowers-compat pack declared as metadata-only."""
    from openmagi_core_agent.recipes.compiler import (
        AgentRecipeCompiler,
        PackRegistry,
        ProfileResolutionRequest,
    )
    from openmagi_core_agent.recipes.workflow_recipe import (
        SavedWorkflowEntry,
        SavedWorkflowRegistry,
        SLASH_COMMAND_IMPORT_CALLBACK_REF,
    )

    snapshot = AgentRecipeCompiler(PackRegistry.with_first_party_packs()).compile(
        ProfileResolutionRequest(taskProfile={"taskType": "research"})
    )
    registry = SavedWorkflowRegistry.empty()
    registry.register(
        SavedWorkflowEntry.from_recipe(
            name="cited-research",
            version="2.0.0",
            snapshot=snapshot,
            workflow_id="openmagi.saved.cited-research",
            model_provider="google",
            model_label="gemini-3.5-flash",
        )
    )
    entry = registry.get("cited-research")
    # The entry promotes the slash-command import metadata to a LIVE, invocable-
    # by-name entry (this is the metadata the recipe compiler held at compiler:2082-2097).
    assert entry.invocation_name == "/cited-research"
    assert SLASH_COMMAND_IMPORT_CALLBACK_REF in entry.import_provenance_refs


def test_saved_registry_rejects_duplicate_name_version() -> None:
    from openmagi_core_agent.recipes.compiler import (
        AgentRecipeCompiler,
        PackRegistry,
        ProfileResolutionRequest,
    )
    from openmagi_core_agent.recipes.workflow_recipe import (
        SavedWorkflowEntry,
        SavedWorkflowRegistry,
    )

    snapshot = AgentRecipeCompiler(PackRegistry.with_first_party_packs()).compile(
        ProfileResolutionRequest(taskProfile={"taskType": "research"})
    )

    def _entry() -> SavedWorkflowEntry:
        return SavedWorkflowEntry.from_recipe(
            name="dup",
            version="1.0.0",
            snapshot=snapshot,
            workflow_id="openmagi.saved.dup",
            model_provider="google",
            model_label="gemini-3.5-flash",
        )

    registry = SavedWorkflowRegistry.empty()
    registry.register(_entry())
    with pytest.raises(ValueError):
        registry.register(_entry())


def test_unknown_saved_workflow_name_raises() -> None:
    from openmagi_core_agent.recipes.workflow_recipe import SavedWorkflowRegistry

    registry = SavedWorkflowRegistry.empty()
    with pytest.raises(KeyError):
        registry.get("does-not-exist")


def test_registry_get_returns_last_registered_not_highest_version() -> None:
    """get(name) returns the *last-registered* entry, not the highest version.

    Register v1.0 then v2.0 — default lookup returns v2.0 (last-write-wins).
    Register out-of-order (v3.0 then v1.1) — default lookup returns v1.1.
    Explicit version lookup always resolves the requested version.
    """
    from openmagi_core_agent.recipes.compiler import (
        AgentRecipeCompiler,
        PackRegistry,
        ProfileResolutionRequest,
    )
    from openmagi_core_agent.recipes.workflow_recipe import (
        SavedWorkflowEntry,
        SavedWorkflowRegistry,
    )

    snapshot = AgentRecipeCompiler(PackRegistry.with_first_party_packs()).compile(
        ProfileResolutionRequest(taskProfile={"taskType": "research"})
    )

    def _entry(name: str, version: str) -> SavedWorkflowEntry:
        return SavedWorkflowEntry.from_recipe(
            name=name,
            version=version,
            snapshot=snapshot,
            workflow_id=f"openmagi.saved.{name}",
            model_provider="google",
            model_label="gemini-3.5-flash",
        )

    # In-order registration: v1.0 then v2.0 → default returns v2.0.
    registry = SavedWorkflowRegistry.empty()
    registry.register(_entry("ordered", "1.0"))
    registry.register(_entry("ordered", "2.0"))
    assert registry.get("ordered").version == "2.0"
    # Explicit version still resolves v1.0.
    assert registry.get("ordered", "1.0").version == "1.0"
    assert registry.get("ordered", "2.0").version == "2.0"

    # Out-of-order registration: v3.0 then v1.1 → default returns v1.1
    # (last-registered, not the "highest" semantic version).
    registry2 = SavedWorkflowRegistry.empty()
    registry2.register(_entry("unordered", "3.0"))
    registry2.register(_entry("unordered", "1.1"))
    assert registry2.get("unordered").version == "1.1"
    assert registry2.get("unordered", "3.0").version == "3.0"


def test_from_recipe_rejects_leading_slash_in_name() -> None:
    """from_recipe must reject names that already start with '/' to prevent
    invocation_name returning '//...'."""
    from openmagi_core_agent.recipes.compiler import (
        AgentRecipeCompiler,
        PackRegistry,
        ProfileResolutionRequest,
    )
    from openmagi_core_agent.recipes.workflow_recipe import SavedWorkflowEntry

    snapshot = AgentRecipeCompiler(PackRegistry.with_first_party_packs()).compile(
        ProfileResolutionRequest(taskProfile={"taskType": "research"})
    )

    with pytest.raises(ValueError, match="bare command token"):
        SavedWorkflowEntry.from_recipe(
            name="/my-research",
            version="1.0.0",
            snapshot=snapshot,
            workflow_id="openmagi.saved.my-research",
            model_provider="google",
            model_label="gemini-3.5-flash",
        )

    # A name without a leading slash succeeds and gets the prefix from the property.
    entry = SavedWorkflowEntry.from_recipe(
        name="my-research",
        version="1.0.0",
        snapshot=snapshot,
        workflow_id="openmagi.saved.my-research",
        model_provider="google",
        model_label="gemini-3.5-flash",
    )
    assert entry.invocation_name == "/my-research"


# ---------------------------------------------------------------------------
# Test 3 — bundled deep-research recipe: cited + cross-checked
# ---------------------------------------------------------------------------

def test_deep_research_recipe_produces_cited_cross_checked_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bundled deep-research recipe fans out, runs cross_review filtering,
    and assembles a cited synthesis via final_assembly.

    Assertions prove:
    - the deep-research recipe round-trips (materialize → validate → execute);
    - a claim no peer corroborates is genuinely filtered (cross_review);
    - the assembled synthesis carries citations = surviving (cross-checked)
      claims, and the orphan claim is NOT among the citations.
    """
    monkeypatch.setenv("MAGI_WORKFLOW_EXECUTOR_ENABLED", "1")

    from openmagi_core_agent.recipes.workflow_recipe import (
        build_deep_research_workflow,
        assemble_cited_synthesis,
    )
    from openmagi_core_agent.harness.workflow_executor import (
        WorkflowExecutorConfig,
        execute_workflow,
    )
    from openmagi_core_agent.workflows.compiler import validate_compiled_workflow

    bundle = build_deep_research_workflow(
        peer_attestations=_deep_research_topology(),
        min_peer_support=2,
    )

    # Governance gate: the deep-research recipe contract must validate.
    verdict = validate_compiled_workflow(bundle.contract)
    assert verdict.ok, f"deep-research contract failed validation: {verdict.reason_codes}"

    # Execute fan-out + cross_review filtering through the real executor.
    config = WorkflowExecutorConfig(enabled=True, local_fake_child_runner_enabled=True)
    events: list[dict[str, object]] = []
    result = asyncio.run(
        execute_workflow(
            bundle.contract,
            config=config,
            child_runner=_FakeChildRunner(),
            event_sink=events.append,
            cross_review_step=bundle.cross_review_step,
        )
    )
    assert result.status in {"accepted", "partial"}

    # cross_review genuinely FILTERED the orphan claim.
    assert "claim:finding-orphan" in result.cross_review_filtered_claim_refs
    assert "claim:finding-orphan" not in result.cross_review_surviving_claim_refs
    assert set(result.cross_review_surviving_claim_refs) == {
        "claim:finding-a",
        "claim:finding-b",
    }
    # The cross-review evidence event flowed.
    assert any("cross_review" in str(e.get("detail", "")) for e in events)

    # Cited synthesis via final_assembly: citations are the cross-checked
    # (surviving) claims; the filtered orphan is NOT cited.
    assembly = assemble_cited_synthesis(result)
    citations = set(assembly.accepted_child_evidence_refs)
    assert "claim:finding-a" in citations
    assert "claim:finding-b" in citations
    assert "claim:finding-orphan" not in citations
    assert assembly.projection_mode == "ready_for_projection"
    # final_assembly never used raw transcripts (context isolation invariant).
    assert assembly.raw_child_transcript_used is False


def test_deep_research_recipe_default_off_does_not_execute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default-OFF: with the executor gate off the bundled deep-research recipe
    neither fans out nor runs cross_review."""
    monkeypatch.delenv("MAGI_WORKFLOW_EXECUTOR_ENABLED", raising=False)

    from openmagi_core_agent.recipes.workflow_recipe import build_deep_research_workflow
    from openmagi_core_agent.harness.workflow_executor import (
        WorkflowExecutorConfig,
        execute_workflow,
    )

    bundle = build_deep_research_workflow(
        peer_attestations=_deep_research_topology(),
        min_peer_support=2,
    )
    config = WorkflowExecutorConfig(enabled=True, local_fake_child_runner_enabled=True)
    events: list[dict[str, object]] = []
    result = asyncio.run(
        execute_workflow(
            bundle.contract,
            config=config,
            child_runner=_FakeChildRunner(),
            event_sink=events.append,
            cross_review_step=bundle.cross_review_step,
        )
    )
    assert result.status == "disabled"
    assert result.child_tasks_dispatched == 0
    assert result.cross_review_filtered_claim_refs == ()
    assert events == []
