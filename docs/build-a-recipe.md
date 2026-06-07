# Build a Recipe

Step-by-step guide to creating a new recipe pack with tool, evidence, and validator refs.

Walk through authoring a RecipePackManifest: declare a pack ID, specify tool and evidence refs, configure opt-out behavior, and test compilation locally. Note: recipe execution is not yet active; today recipes compile metadata only.

## Prerequisites

Recipe authoring requires the Magi Agent source tree. Recipe packs are defined as RecipePackManifest instances in Python. You will need the magi_agent package importable for validation.

Recipes today compile into metadata snapshots (RecipeSnapshot). There is no runtime execution engine that consumes these snapshots. The value of authoring a recipe pack now is to declare policy intent that will be enforced once the execution engine ships.

## RecipePackManifest structure

A pack manifest declares a unique pack_id (dotted safe ID like 'myorg.code-review'), a display name, description, and refs to the components it provides. All live refs (live_tool_refs, live_callback_refs, runner_route_refs) must be empty -- the manifest is metadata-only.

### Example pack manifest (Python)

```
from magi_agent.recipes.compiler import RecipePackManifest

pack = RecipePackManifest(
    pack_id='myorg.code-review',
    version='1',
    display_name='Code Review',
    description='Requires GitDiff and TestRun evidence before commit',
    default_enabled=False,
    hard_safety=False,
    opt_out_allowed=True,
    customizable=True,
    task_profile_selectors=('code-review', 'pull-request'),
    depends_on_pack_ids=(),
    tool_refs=('FileRead', 'FileEdit'),
    evidence_refs=('GitDiff', 'TestRun'),
    validator_refs=('coding-verification',),
    callback_refs=(),
    approval_gate_refs=(),
    audit_refs=(),
)
```

## Opt-out and hard safety

Non-hard-safety packs must keep opt_out_allowed=True and customizable=True. This lets users disable packs that do not fit their workflow. Hard-safety packs (hard_safety=True) cannot be opted out or customized -- they enforce invariants like permission-arbiter, path-safety, and secret-safety.

If your pack declares hard_safety=True, it must set opt_out_allowed=False and customizable=False. The validator rejects any other combination.

## Testing compilation locally

Validate your manifest by constructing it and checking that Pydantic validation passes. Then test profile resolution by creating a ProfileResolutionRequest and calling the compiler to produce a RecipeSnapshot.

Since recipe execution is not active, your test verifies that the snapshot compiles without errors and that your pack appears in selected_pack_ids. Runtime enforcement of the snapshot policy is future work.

### Validation test (Python)

```
# Verify manifest validates
manifest = RecipePackManifest(
    pack_id='myorg.code-review', version='1',
    display_name='Code Review',
    description='Requires evidence before commit',
    tool_refs=('FileRead',), evidence_refs=('GitDiff',),
)
assert manifest.pack_id == 'myorg.code-review'
assert manifest.live_tool_refs == ()  # must be empty
```

## Execution engine status

The recipe execution engine that would read RecipeSnapshot and enforce tool permissions, evidence requirements, and projection rules during live runs is not implemented. Today, enforcement comes from harnesses (HarnessEngine + EvidenceContractScope) and boundary modules. Recipe metadata compilation is the only active piece.

Third-party recipe authors can prepare packs now. When the execution engine ships, compiled snapshots will drive runtime policy without code changes to existing manifests.

## Complete custom recipe: spreadsheet fact-checker

This is a complete, self-contained recipe that a third-party author could write. It requires the agent to inspect source documents before citing spreadsheet numbers, and to produce Calculation evidence for any derived figures. The full recipe is under 100 lines of Python.

### Full recipe: spreadsheet fact-checker (under 100 lines)

```
from magi_agent.recipes.compiler import RecipePackManifest
from magi_agent.evidence.types import (
    EvidenceContract,
    EvidenceRequirement,
    EvidenceFieldMatcher,
    EvidenceRecord,
    EvidenceSource,
)
from magi_agent.harness.evidence_scope import (
    EvidenceContractScope,
    SpawnDepthRange,
)

# --- Pack manifest: declares what this recipe needs ---
spreadsheet_pack = RecipePackManifest(
    pack_id="myorg.spreadsheet-factcheck",
    display_name="Spreadsheet Fact-Checker",
    description="Verify spreadsheet numbers against source documents.",
    default_enabled=True,
    hard_safety=False,
    opt_out_allowed=True,
    customizable=True,
    task_profile_selectors=("spreadsheet", "data-analysis", "reporting"),
    depends_on_pack_ids=(),
    tool_refs=("tool:file.read",),
    evidence_refs=("evidence:source-inspection", "evidence:calculation"),
    validator_refs=(
        "validator:spreadsheet-factcheck:source-required",
        "validator:spreadsheet-factcheck:calculation-verified",
    ),
    callback_refs=(),
    approval_gate_refs=(),
    audit_refs=("audit:spreadsheet-factcheck",),
)

# --- Evidence contracts: what must be proven ---
source_contract = EvidenceContract(
    id="myorg.spreadsheet-factcheck.source-required",
    description="Require source inspection before citing numbers.",
    triggers=("afterToolUse", "beforeCommit"),
    requirements=(
        EvidenceRequirement(
            type="SourceInspection",
            fields={"inspected": EvidenceFieldMatcher(equals=True)},
        ),
    ),
    on_missing="audit",
    retry_message="Read the source document before citing these numbers.",
)

calculation_contract = EvidenceContract(
    id="myorg.spreadsheet-factcheck.calculation-verified",
    description="Require Calculation evidence for derived figures.",
    triggers=("beforeCommit",),
    requirements=(
        EvidenceRequirement(
            type="Calculation",
            fields={"verified": EvidenceFieldMatcher(equals=True)},
        ),
    ),
    on_missing="audit",
    retry_message="Verify the calculation before including derived figures.",
)

# --- Scopes: when do these contracts apply ---
source_scope = EvidenceContractScope(
    contract_id="myorg.spreadsheet-factcheck.source-required",
    agent_roles=("general", "research"),
    run_on=("main",),
    spawn_depth=SpawnDepthRange(min_depth=0, max_depth=0),
    enforcement="audit",
    audit_before_block=True,
    opt_out_allowed=True,
    hard_safety=False,
)

calculation_scope = EvidenceContractScope(
    contract_id="myorg.spreadsheet-factcheck.calculation-verified",
    agent_roles=("general", "research"),
    run_on=("main",),
    spawn_depth=SpawnDepthRange(min_depth=0, max_depth=0),
    enforcement="audit",
    audit_before_block=True,
    opt_out_allowed=True,
    hard_safety=False,
)

# --- Local test: verify contract matching ---
test_evidence = EvidenceRecord(
    type="SourceInspection",
    status="ok",
    observed_at=1716840000,
    source=EvidenceSource(kind="tool_trace", tool_name="FileRead"),
    fields={"inspected": True, "source_ref": "q1_revenue.xlsx"},
)
# verdict.ok == True when evidence matches requirements
# verdict.state == "pass" | "missing" | "failed"
```

## How this compares to built-in recipes

This recipe is under 100 lines. It defines one pack manifest, two evidence contracts, two scopes, and a local test. When activated, the harness engine resolves these contracts, the hook bus evaluates them at afterToolUse and beforeCommit, and the enforcement boundary logs audit events for missing evidence.

Compare this with the built-in research domain (~16,000 lines) which adds source ledger tracking with 9 source kinds and trust tiers, citation auditing with pass/failure/missing per citation, claim graph dependency tracking, multi-layer scope enforcement with child agent delegation, and repair policies with retry backoff. The built-in coding domain (~3,800 lines) adds git diff verification, test run validation with exit code checks, diagnostics integration, and planner command alignment. Most custom recipes need far less; the under-100-line pattern above covers the typical case.
