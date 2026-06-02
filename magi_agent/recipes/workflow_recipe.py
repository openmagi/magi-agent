"""Track 17 PR5 — Workflow-as-recipe + reuse ("save as command").

This module makes dynamic workflows DEFINABLE as recipes and REUSABLE by name,
composing ONLY existing surfaces — no parallel recipe/registry/materialization
system is introduced:

1. **Define-as-recipe** — a workflow IS a ``recipes/compiler.RecipeSnapshot``
   materialized through ``recipes/materializer.RecipeMaterializer`` (the same
   plan-as-data path every recipe uses).

2. **Recipe → contract bridge** — :func:`compile_workflow_from_recipe` turns a
   ``(RecipeSnapshot, ReliabilityMaterializationPlan)`` pair into a
   ``WorkflowCompileInput`` and runs it through the EXISTING
   ``workflows/compiler.compile_governed_workflow`` so the result is a real
   ``CompiledWorkflowContract`` that the EXISTING
   ``validate_compiled_workflow()`` accepts.  This is the governance gate PR1
   locked: a saved workflow that fails validation never executes.

3. **Live reusable registry** — :class:`SavedWorkflowRegistry` is the "save as
   slash-command" surface.  It promotes the slash-command IMPORT metadata that
   ``recipes/compiler.py`` (superpowers-compat pack, compiler:2082-2097) held as
   metadata-only into a LIVE, named, versioned entry that follows the
   ``workflows/registry.py`` pattern (frozen entries, sha256 digests, duplicate
   rejection).  A saved workflow is invocable by name: looked up, RE-materialized
   from its recipe, RE-validated, and executed.

4. **Bundled deep-research recipe** — :func:`build_deep_research_workflow`
   composes the EXISTING research packs (fan-out research children) + the PR4
   ``cross_review`` filter + ``final_assembly`` cited synthesis into one
   reusable, governed workflow.  :func:`assemble_cited_synthesis` turns the
   executor's surviving (cross-checked) claim refs into a
   ``MetaFinalAssemblyPlan`` whose citations are exactly the claims that survived
   peer cross-review (the filtered orphan is never cited).

Default-OFF / context isolation: nothing here flips a locked ``Literal[False]``
authority flag, attaches a runner/route, or performs I/O.  Registering and
looking up a saved workflow is metadata and is always available; EXECUTING it
still obeys ``MAGI_WORKFLOW_EXECUTOR_ENABLED`` via ``execute_workflow``.
"""
from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

from magi_agent.harness.cross_review import CrossReviewStep
from magi_agent.harness.workflow_executor import WorkflowExecutorResult
from magi_agent.meta_orchestration.child_acceptance import ChildAcceptanceVerdict
from magi_agent.meta_orchestration.final_assembly import (
    MetaFinalAssemblyPlan,
    assemble_final_output_from_inspection,
)
from magi_agent.meta_orchestration.inspection_loop import (
    MetaInspectedChildVerdict,
    inspect_child_verdicts,
)
from magi_agent.recipes.compiler import (
    AgentRecipeCompiler,
    PackRegistry,
    PRIVATE_IDENTIFIER_FRAGMENTS,
    ProfileResolutionRequest,
    RecipeSnapshot,
)
from magi_agent.recipes.materializer import (
    RecipeMaterializer,
    ReliabilityMaterializationPlan,
)
from magi_agent.workflows.compiler import (
    CompiledWorkflowContract,
    compile_governed_workflow,
    WorkflowCompileInput,
)
from magi_agent.workflows.registry import WorkflowRegistryEntry


_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)

#: The slash-command import callback the recipe compiler's superpowers-compat
#: pack declares as metadata-only (recipes/compiler.py:2087).  PR5 promotes this
#: provenance ref onto every live saved-workflow registry entry so the entry is
#: traceable back to the "save as slash-command" import surface.
SLASH_COMMAND_IMPORT_CALLBACK_REF = (
    "callback:superpowers-compat:slash-command-import-metadata"
)

#: The runtime-contract version the workflow compiler requires registered
#: entries to declare so the selected recipe is considered runnable.
_RUNNABLE_RUNTIME_CONTRACT_VERSION = "programmable-determinism.v1"

#: Governed projection renderer (never ``raw_text_allowed`` — that is forbidden
#: by ``validate_compiled_workflow``).
_GOVERNED_RENDERER = "structured_claims_only"

#: Hard invariants every governed workflow contract must assert.
_HARD_INVARIANTS = {
    "rawDraftStreamingForbidden": True,
    "toolhostOnlyExecution": True,
    "validatorBeforeProjection": True,
}

#: Maximum number of evidence requirements forwarded from the plan to the
#: compiled contract.  The workflow compiler validates the evidence producer set
#: size; capping here keeps the bridge self-consistent without needing to inspect
#: the compiler's internal limit.
_MAX_BRIDGE_EVIDENCE_REQUIREMENTS = 4

#: Default research tool allowlist (read-only; none in HARD_DENIED_TOOLS).
_DEFAULT_TOOL_ALLOWLIST = ("SourceLedgerRead", "SearchFiles")
_DEFAULT_VALIDATOR = "deterministic-verifier"
_DEFAULT_EVIDENCE_REQUIREMENT = "SourceInspection"


# ---------------------------------------------------------------------------
# Recipe → contract bridge
# ---------------------------------------------------------------------------

def _stable_digest(*parts: str) -> str:
    seed = "\n".join(parts).encode("utf-8")
    return "sha256:" + hashlib.sha256(seed).hexdigest()


def compile_workflow_from_recipe(
    snapshot: RecipeSnapshot,
    plan: ReliabilityMaterializationPlan,
    *,
    workflow_id: str,
    version: str,
    extra_tool_allowlist: tuple[str, ...] = (),
    extra_available_tools: tuple[str, ...] = (),
) -> CompiledWorkflowContract:
    """Bridge a recipe ``(snapshot, plan)`` into a ``CompiledWorkflowContract``.

    The contract is produced through the EXISTING
    ``workflows/compiler.compile_governed_workflow`` so it is a genuine governed
    contract (not a hand-rolled parallel object).  The single selected recipe is
    ``{workflow_id}.v{version}`` and is registered as a runnable
    ``WorkflowRegistryEntry`` so ``validate_compiled_workflow`` accepts it.

    Evidence requirements and validators are derived from the materialization
    plan when present (with safe defaults) and their producer/available sets are
    kept self-consistent so a well-formed recipe validates.

    ``extra_tool_allowlist`` / ``extra_available_tools`` exist for negative
    tests: passing a HARD_DENIED tool makes the resulting contract fail
    validation (exercising the "fails validation ⇒ never executes" invariant)
    without touching any locked authority flag.
    """
    recipe_id = f"{workflow_id}.v{version}"
    entry = WorkflowRegistryEntry(
        workflowId=workflow_id,
        version=version,
        ownerRef=f"saved-workflow:{workflow_id}",
        status="active",
        sourceDigest=_stable_digest("saved-workflow-source", snapshot.snapshot_id, recipe_id),
        promotionHistory=("draft:recipe", "staging:recipe", "active:recipe"),
        compatibleRuntimeContractVersion=_RUNNABLE_RUNTIME_CONTRACT_VERSION,
    )

    # Evidence requirements: derive a stable producer set from the plan's
    # evidence intents when present, else fall back to a default source check.
    evidence_requirements: tuple[str, ...] = (
        tuple(_safe_ref_tokens(plan.evidence_requirements))[:_MAX_BRIDGE_EVIDENCE_REQUIREMENTS]
        or (_DEFAULT_EVIDENCE_REQUIREMENT,)
    )
    validator_refs: tuple[str, ...] = (_DEFAULT_VALIDATOR,)

    tool_allowlist = (*_DEFAULT_TOOL_ALLOWLIST, *extra_tool_allowlist)
    available_tools = (*_DEFAULT_TOOL_ALLOWLIST, *extra_available_tools)

    compile_input = WorkflowCompileInput(
        workflowId=workflow_id,
        version=version,
        selectedRecipes=(recipe_id,),
        registeredWorkflows=(entry,),
        toolAllowlist=tool_allowlist,
        toolDenylist=(),
        evidenceRequirements=evidence_requirements,
        validatorRefs=validator_refs,
        projectionPolicy=_GOVERNED_RENDERER,
        repairPolicy="retry-once",
        approvalPolicy="auto",
        contextProjectionPolicy="explicit",
        budgets=_budgets_from_plan(plan),
        hardInvariants=_HARD_INVARIANTS,
        effectivePolicySnapshotDigest=_stable_digest(
            "saved-workflow-policy", snapshot.snapshot_id, recipe_id
        ),
        availableTools=available_tools,
        availableValidators=validator_refs,
        availableRenderers=(_GOVERNED_RENDERER,),
        evidenceProducers=evidence_requirements,
        routePrecedence=(),
        noMatchTerminalState="block",
    )
    return compile_governed_workflow(compile_input)


def _safe_ref_tokens(refs: tuple[str, ...]) -> list[str]:
    """Sanitise plan evidence refs into governed identifiers.

    The workflow compiler rejects identifiers containing protected runtime-data
    fragments (e.g. ``prompt``/``session``).  We keep only refs free of those
    markers and replace any disallowed characters; evidence requirements are
    free-form strings in the contract, so simple alnum-ish tokens are fine.
    """
    out: list[str] = []
    for ref in refs:
        token = "".join(ch if (ch.isalnum() or ch in "-_:.") else "-" for ch in ref)
        lowered = token.lower()
        if any(frag in lowered for frag in PRIVATE_IDENTIFIER_FRAGMENTS):
            continue
        if token.strip():
            out.append(token)
    return out


def _budgets_from_plan(plan: ReliabilityMaterializationPlan) -> dict[str, object]:
    """Derive governed budgets from the plan's reliability policy.

    Only the two ALLOWED_BUDGET_KEYS the workflow compiler permits are emitted,
    clamped within the compiler's bounds.

    The plan has no dedicated per-workflow iteration budget field, so
    ``max_sota_escalations`` is used as a proxy: it conveys the operator's
    intent for how many escalation rounds (comparable to retry iterations) the
    reliability policy tolerates.  The default (10) is a fixed governed bound
    that keeps the contract valid when the plan does not specify this field.
    """
    max_iterations = 10
    sota = getattr(plan.reliability, "max_sota_escalations", None)
    if isinstance(sota, int) and not isinstance(sota, bool) and 0 < sota <= 100:
        # max_sota_escalations is the closest reliability-policy proxy available;
        # the plan does not expose a separate maxIterations budget.
        max_iterations = sota
    return {"maxIterations": max_iterations, "wallClockTimeoutMs": 60_000}


# ---------------------------------------------------------------------------
# Live reusable registry ("save as command")
# ---------------------------------------------------------------------------

class SavedWorkflowEntry(BaseModel):
    """A named, versioned, reusable workflow saved as a recipe.

    Holds the recipe ``snapshot`` (plan-as-data) plus the model labels needed to
    RE-materialize it on lookup — so reuse is lossless (the registry re-runs the
    real materialize → compile → validate path, it does not cache a frozen
    contract).  ``import_provenance_refs`` carries the promoted slash-command
    import metadata so the live entry is traceable to its "save as command"
    origin.
    """

    model_config = _MODEL_CONFIG

    name: str
    version: str
    workflow_id: str = Field(alias="workflowId")
    snapshot: RecipeSnapshot
    model_provider: str = Field(alias="modelProvider")
    model_label: str = Field(alias="modelLabel")
    import_provenance_refs: tuple[str, ...] = Field(
        default=(SLASH_COMMAND_IMPORT_CALLBACK_REF,),
        alias="importProvenanceRefs",
    )
    source_digest: str = Field(alias="sourceDigest")

    @property
    def invocation_name(self) -> str:
        """The slash-command-equivalent name this saved workflow is invoked by."""
        return f"/{self.name}"

    @classmethod
    def from_recipe(
        cls,
        *,
        name: str,
        version: str,
        snapshot: RecipeSnapshot,
        workflow_id: str,
        model_provider: str,
        model_label: str,
        import_provenance_refs: tuple[str, ...] = (SLASH_COMMAND_IMPORT_CALLBACK_REF,),
    ) -> "SavedWorkflowEntry":
        if not name.strip():
            raise ValueError("saved workflow name must be non-empty")
        if name.startswith("/"):
            raise ValueError(
                "saved workflow name must be the bare command token, not a slash-prefixed "
                "invocation name (invocation_name adds the '/' prefix automatically)"
            )
        if not version.strip():
            raise ValueError("saved workflow version must be non-empty")
        refs = tuple(dict.fromkeys((*import_provenance_refs, SLASH_COMMAND_IMPORT_CALLBACK_REF)))
        return cls(
            name=name,
            version=version,
            workflowId=workflow_id,
            snapshot=snapshot,
            modelProvider=model_provider,
            modelLabel=model_label,
            importProvenanceRefs=refs,
            sourceDigest=_stable_digest(
                "saved-workflow-entry", name, version, workflow_id, snapshot.snapshot_id
            ),
        )

    def resolve_contract(self) -> CompiledWorkflowContract:
        """Re-materialize the saved recipe and bridge it to a governed contract.

        This is the lossless-reuse step: the recipe snapshot is materialized
        afresh via the EXISTING ``RecipeMaterializer`` and bridged through
        :func:`compile_workflow_from_recipe`, so the resolved contract is
        re-validated by the executor's governance gate on every invocation.
        """
        plan = RecipeMaterializer.with_reliability_defaults().materialize(
            self.snapshot,
            modelProvider=self.model_provider,
            modelLabel=self.model_label,
        )
        return compile_workflow_from_recipe(
            self.snapshot,
            plan,
            workflow_id=self.workflow_id,
            version=self.version,
        )


class SavedWorkflowRegistry:
    """In-memory live registry of saved workflows, keyed by name.

    Mirrors the ``workflows/registry.py`` pattern (frozen value entries, sha256
    digests, duplicate rejection) but adds the name-lookup + re-materialize API
    that "save as command" reuse requires.  No durable storage is created — this
    is a within-session registry (the executor env gate, not the registry,
    governs execution).
    """

    def __init__(self, entries: Sequence[SavedWorkflowEntry] = ()) -> None:
        self._entries: dict[tuple[str, str], SavedWorkflowEntry] = {}
        self._latest: dict[str, SavedWorkflowEntry] = {}
        for entry in entries:
            self.register(entry)

    @classmethod
    def empty(cls) -> "SavedWorkflowRegistry":
        return cls(())

    def register(self, entry: SavedWorkflowEntry) -> SavedWorkflowEntry:
        key = (entry.name, entry.version)
        if key in self._entries:
            raise ValueError(
                f"duplicate saved workflow version: {entry.name} v{entry.version}"
            )
        self._entries[key] = entry
        self._latest[entry.name] = entry
        return entry

    def names(self) -> tuple[str, ...]:
        return tuple(self._latest)

    def get(self, name: str, version: str | None = None) -> SavedWorkflowEntry:
        """Look up a saved workflow entry.

        When ``version`` is omitted, returns the *last-registered* entry for
        ``name`` — not necessarily the highest semantic version.  Registration
        order determines the default (last-write-wins), so callers that need a
        specific version should always pass it explicitly.
        """
        if version is not None:
            try:
                return self._entries[(name, version)]
            except KeyError as exc:
                raise KeyError(f"unknown saved workflow: {name} v{version}") from exc
        try:
            return self._latest[name]
        except KeyError as exc:
            raise KeyError(f"unknown saved workflow: {name}") from exc

    def resolve_contract(
        self, name: str, version: str | None = None
    ) -> CompiledWorkflowContract:
        """Look up by name and re-resolve the governed contract for execution."""
        return self.get(name, version).resolve_contract()


# ---------------------------------------------------------------------------
# Bundled deep-research recipe
# ---------------------------------------------------------------------------

class DeepResearchWorkflowBundle(BaseModel):
    """A ready-to-execute deep-research workflow: governed contract + the
    ``cross_review`` step the executor runs after fan-out."""

    model_config = _MODEL_CONFIG

    contract: CompiledWorkflowContract
    cross_review_step: CrossReviewStep = Field(alias="crossReviewStep")
    saved_entry: SavedWorkflowEntry = Field(alias="savedEntry")


def build_deep_research_workflow(
    *,
    peer_attestations: Sequence[Mapping[str, object]],
    min_peer_support: int = 2,
    name: str = "deep-research",
    version: str = "1.0.0",
) -> DeepResearchWorkflowBundle:
    """Build the bundled deep-research-equivalent workflow.

    Composes EXISTING surfaces only:
    - the research recipe (``taskType="research"`` → research packs) compiled by
      the EXISTING ``AgentRecipeCompiler`` and materialized by the EXISTING
      ``RecipeMaterializer`` → fan-out research children via the executor's
      ``ResearchChildRunnerRecipe`` path;
    - the PR4 ``cross_review`` step (``peer_attestations`` → claim filtering via
      the verifier_bus ``source_claim_link`` verifier);
    - a ``SavedWorkflowEntry`` so the bundle is itself reusable by name.

    Citations come later from :func:`assemble_cited_synthesis` over the
    executor result's surviving (cross-checked) claim refs.
    """
    snapshot = AgentRecipeCompiler(PackRegistry.with_first_party_packs()).compile(
        ProfileResolutionRequest(taskProfile={"taskType": "research"})
    )
    entry = SavedWorkflowEntry.from_recipe(
        name=name,
        version=version,
        snapshot=snapshot,
        workflow_id="openmagi.deep-research",
        model_provider="google",
        model_label="gemini-3.5-flash",
    )
    contract = entry.resolve_contract()
    step = CrossReviewStep(
        reviewId=f"deep-research-{name}",
        peerAttestations=tuple(peer_attestations),
        minPeerSupport=min_peer_support,
    )
    return DeepResearchWorkflowBundle(
        contract=contract,
        crossReviewStep=step,
        savedEntry=entry,
    )


def assemble_cited_synthesis(
    result: WorkflowExecutorResult,
    *,
    assembly_id: str = "assembly:deep-research",
) -> MetaFinalAssemblyPlan:
    """Assemble a cited synthesis from a deep-research executor result.

    The citations are EXACTLY the claims that survived cross-review (the
    cross-checked set) — the filtered orphan claims are never cited.  Each
    surviving claim becomes an accepted child verdict whose
    ``accepted_evidence_refs`` is the surviving claim ref, then the EXISTING
    ``final_assembly`` path turns the accepted child verdicts into the cited
    ``MetaFinalAssemblyPlan`` (raw transcripts never used).

    Requires the result to carry surviving cross-review claims (i.e. a
    ``cross_review`` step ran on the live executor path).  Raises ``ValueError``
    when there are no surviving cross-checked claims to cite.
    """
    surviving = result.cross_review_surviving_claim_refs
    if not surviving:
        raise ValueError(
            "cannot assemble a cited synthesis without surviving cross-checked claims"
        )

    child_verdicts: list[MetaInspectedChildVerdict] = []
    for index, claim_ref in enumerate(surviving):
        child_verdicts.append(
            MetaInspectedChildVerdict.model_validate(
                {
                    "taskId": f"deep-research-child-{index}",
                    "required": True,
                    "attempt": 0,
                    "verdict": ChildAcceptanceVerdict._from_evaluation(
                        status="accepted",
                        reason_codes=("accepted",),
                        accepted_evidence_refs=(claim_ref,),
                        missing_evidence_refs=(),
                        retryable=False,
                        retry_budget_remaining=1,
                    ),
                }
            )
        )

    inspection = inspect_child_verdicts(
        f"loop:{assembly_id}",
        tuple(child_verdicts),
    )
    return assemble_final_output_from_inspection(
        assembly_id,
        inspection,
        required_verifier_refs=(),
    )


__all__ = [
    "DeepResearchWorkflowBundle",
    "SLASH_COMMAND_IMPORT_CALLBACK_REF",
    "SavedWorkflowEntry",
    "SavedWorkflowRegistry",
    "assemble_cited_synthesis",
    "build_deep_research_workflow",
    "compile_workflow_from_recipe",
]
