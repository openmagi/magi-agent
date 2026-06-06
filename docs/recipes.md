# Recipes

Status: 🚧 Default-off — recipe packs compile to metadata/policy snapshots only; there is no runtime execution engine that consumes a snapshot to enforce policy during a live run yet (`magi_agent/recipes/`).

> **There is no `--recipe` flag.** Recipes are not something you "run" from the
> CLI today. `docs/cli/magi.md` exposes no recipe flag, and nothing reads a
> `RecipeSnapshot` to apply tool permissions, evidence rules, or projection at
> runtime. Recipes describe *what policy would apply*; enforcement comes from
> harnesses and evidence contracts (see [harnesses.md](harnesses.md)).

Composable workflow definitions that declare policy, evidence rules, and projection for a task type.

Recipes compile metadata snapshots that declare what packs, evidence, tools, validators, and projection rules apply to a run. The recipe system is implemented and active for metadata compilation; runtime execution of recipe policy is planned.

## What recipes are

A recipe is a preset for how the agent handles a specific type of task. For example, the coding recipe requires the agent to run tests before claiming it fixed a bug. The research recipe requires source inspection before making factual claims. You can combine multiple recipes, and the runtime enforces all of them automatically.

Recipes compile into metadata that configures the harness engine. The recipe execution engine (which would apply recipes as live runtime policy) is planned but not yet implemented — current enforcement happens through the harness and evidence contract system.

Implementation: the recipe metadata compilation pipeline (RecipePackManifest, ProfileResolutionRequest, ResolvedRecipeProfile, RecipeSnapshot) is implemented and active. Recipe packs compile into snapshots with resolved profiles, selected pack IDs, composition policy, and merged refs. However, no runtime execution engine consumes these snapshots to enforce policy during live runs. Enforcement today comes from harnesses and evidence contracts, not from recipe snapshots.

## RecipeSnapshot compilation

RecipeSnapshot is the compiled output of recipe resolution. It captures the resolved profile, selected and opted-out pack IDs, merged instruction/tool/callback/validator/evidence/audit refs, composition policy metadata, and attachment flags.

The snapshot is metadata-only: it records what policy would apply, but does not itself execute or enforce that policy at runtime. All attachment flags (trafficAttached, executionAttached, routeAttached, runnerAttached, liveToolsAttached, liveCallbacksAttached) are locked to false.

### RecipeSnapshot fields (Python, implemented)

```
class RecipeSnapshot(BaseModel):
    snapshot_id: str
    resolved_profile: Mapping[str, object]
    selected_pack_ids: tuple[str, ...]
    opted_out_pack_ids: tuple[str, ...]
    non_opt_out_pack_ids: tuple[str, ...]
    composition_policy_metadata: CompositionPolicyMetadata
    recipe_selection: RecipeSelectionMetadata
    instruction_refs: tuple[str, ...]
    tool_refs: tuple[str, ...]
    callback_refs: tuple[str, ...]
    validator_refs: tuple[str, ...]
    approval_gate_refs: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    audit_refs: tuple[str, ...]
    attachment_flags: RecipeAttachmentFlags  # all false
```

## RecipePackManifest and first-party packs

Each recipe pack is declared as a RecipePackManifest (recipes/compiler.py) with a pack_id, display name, description, task profile selectors, depends_on_pack_ids, and refs to instructions, tools, callbacks, validators, approval gates, evidence, and audit. Packs enforce a metadata-only invariant: live_tool_refs, live_callback_refs, and runner_route_refs must be empty. All attachment_flags are False.

The runtime defines 16 first-party packs: openmagi.context-safety (hard safety), openmagi.evidence (hard safety), openmagi.agent-methodology (superpowers-compatible), openmagi.superpowers-compat, openmagi.web-acquisition, openmagi.research, openmagi.dev-coding, openmagi.missions (metadata-only), openmagi.memory-agentmemory, openmagi.office-automation, openmagi.spreadsheet-automation, openmagi.browser-automation, openmagi.document-review, openmagi.lightweight-scripting, plus 2 framework packs.

- pack_id: unique identifier (dotted safe ID format, e.g. openmagi.dev-coding)
- hard_safety: if true, opt_out_allowed and customizable must be false (openmagi.context-safety, openmagi.evidence)
- opt_out_allowed: whether users can disable this pack (default true for non-hard-safety)
- task_profile_selectors: task types this pack applies to
- depends_on_pack_ids: pack dependencies resolved before compilation
- tool_refs, evidence_refs, validator_refs: metadata refs to pack components
- attachment_flags: all False (trafficAttached, executionAttached, routeAttached, runnerAttached, liveToolsAttached, liveCallbacksAttached)

## Profile resolution

ProfileResolutionRequest provides five configuration layers: userProfile, workspacePolicy, taskProfile, recipePackConfig, and runtimeContext. These layers are deep-merged (with sensitive key sanitization) to produce a ResolvedRecipeProfile.

The resolved profile records selected_pack_ids, opted_out_pack_ids, and a CompositionPolicyMetadata that captures merge semantics (validatorMerge: all_of, evidenceMerge: union), budget caps, memory mode, side-effect posture, and provider/tool conflict detection.

## What recipes do not do today

Recipes do not execute policy. There is no recipe execution engine that reads a RecipeSnapshot and enforces tool permissions, evidence requirements, or projection rules during a live run. That enforcement comes from harnesses (HarnessEngine) and evidence contracts (EvidenceContract), which are fully implemented and active.

The conceptual interfaces ToolHostRequest, ToolHostReceipt, RepairDecision, ProjectionResult, and ValidationResult are not implemented. The real enforcement boundaries are the 15 hook points (HookPoint enum) and the dedicated boundary modules (evidence/tool_boundary.py, evidence/enforcement_boundary.py, memory/write_boundary.py, runtime/commit_boundary.py, artifacts/delivery_boundary.py, runtime/child_runner_boundary.py, runtime/projection_write_boundary.py).

## How recipes relate to harnesses

Recipes and harnesses sit on two sides of the same gap:

- A **recipe** is the *declaration* — a compiled `RecipeSnapshot` that says which
  packs, tools, validators, evidence, and projection rules *should* apply to a
  task type. It is metadata, with every attachment flag locked to `False`.
- A **harness** is the *enforcer* — `HarnessEngine` plus the evidence-contract
  and boundary modules actually gate tool calls, evidence, and projection during
  a run.

Today the bridge between them is missing: no execution engine reads a recipe
snapshot and configures a harness from it. So you author and inspect recipe
metadata via the compilation pipeline, but you get enforcement by configuring a
harness directly. See [harnesses.md](harnesses.md) and
[build-a-harness.md](build-a-harness.md).
