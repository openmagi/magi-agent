# Recipe Schema Reference

Complete schema reference for RecipePackManifest, RecipeSnapshot, and ProfileResolutionRequest.

The full Pydantic schema for recipe pack manifests, snapshots, and profile resolution requests. Recipes are metadata-only policy compilation units with no execution engine.

## RecipePackManifest

RecipePackManifest declares a recipe pack: its identity, safety policy, dependency requirements, and references to instructions, tools, callbacks, validators, and evidence. All manifests are metadata-only. The live_tool_refs, live_callback_refs, and runner_route_refs fields are validated to be empty and serialize to empty tuples.

- pack_id (str, alias packId) — Unique recipe pack identifier. Must match ^[a-z0-9][a-z0-9_-]{0,63}(\.[a-z0-9][a-z0-9_-]{0,63})+$.
- version (str, default "1") — Pack version. Must be a safe version string.
- display_name (str, alias displayName) — Human-readable name.
- description (str) — Description of the recipe pack.
- default_enabled (bool, alias defaultEnabled, default False) — Whether enabled by default.
- hard_safety (bool, alias hardSafety, default False) — If true, pack is non-opt-out and non-customizable.
- opt_out_allowed (bool, alias optOutAllowed, default True) — Whether users can opt out. Must be True if hard_safety is False.
- customizable (bool, default True) — Whether the pack can be customized. Must be True if hard_safety is False.
- task_profile_selectors (tuple[str, ...], alias taskProfileSelectors) — Task profile selectors for pack matching.
- depends_on_pack_ids (tuple[str, ...], alias dependsOnPackIds) — Pack IDs this pack depends on.
- instruction_refs (tuple[str, ...], alias instructionRefs) — Instruction references.
- tool_refs (tuple[str, ...], alias toolRefs) — Tool references.
- callback_refs (tuple[str, ...], alias callbackRefs) — Callback references.
- validator_refs (tuple[str, ...], alias validatorRefs) — Validator references.
- approval_gate_refs (tuple[str, ...], alias approvalGateRefs) — Approval gate references.
- evidence_refs (tuple[str, ...], alias evidenceRefs) — Evidence references.
- compatible_runtime_contract_versions (tuple[str, ...], alias compatibleRuntimeContractVersions, default ("recipe-pack.v1",)) — Compatible runtime contract versions.

- [Recipes overview](/docs/recipes)
- [Build a recipe](/docs/build-a-recipe)

## RecipeSnapshot

RecipeSnapshot captures the resolved state of recipe compilation: which packs were selected, which were opted out, and the merged profile. It is produced by the recipe compiler after evaluating ProfileResolutionRequest against the registry.

- snapshot_id (str, alias snapshotId) — Unique snapshot identifier (SHA-256 digest of the resolved profile).
- resolved_profile (Mapping[str, object], alias resolvedProfile) — Merged and sanitized profile data. Sensitive keys are stripped.
- selected_pack_ids (tuple[str, ...], alias selectedPackIds) — Pack IDs included in this snapshot.
- opted_out_pack_ids (tuple[str, ...], alias optedOutPackIds) — Pack IDs the user opted out of.
- non_opt_out_pack_ids (tuple[str, ...], alias nonOptOutPackIds) — Pack IDs that cannot be opted out (hard_safety packs).
- instruction_refs through audit_refs — Merged refs from all selected packs.
- composition_policy_metadata (CompositionPolicyMetadata) — Policy metadata from the composition step.
- recipe_selection (RecipeSelectionMetadata) — Metadata about how recipes were selected (source, requested, applied, omitted, omission reasons).

## ProfileResolutionRequest

ProfileResolutionRequest provides the input layers for recipe compilation. Layers are merged in priority order: runtime_context (highest) > task_profile > workspace_policy > user_profile (lowest). Sensitive keys are filtered during merge.

- user_profile (Mapping[str, object], alias userProfile, default {}) — User-level preferences.
- workspace_policy (Mapping[str, object], alias workspacePolicy, default {}) — Workspace-level policy overrides.
- task_profile (Mapping[str, object], alias taskProfile, default {}) — Task-specific profile overrides.
- recipe_pack_config (Mapping[str, object], alias recipePackConfig, default {}) — Per-pack configuration overrides.
- runtime_context (Mapping[str, object], alias runtimeContext, default {}) — Runtime context (highest priority).

## Metadata-only: No Execution Engine

RecipePackManifest is a metadata-only declaration. The live_tool_refs, live_callback_refs, and runner_route_refs fields are validated empty and serialize to empty tuples. Manifests declare what a recipe references, not how to execute it. The execution engine is a future boundary that will wire manifest refs to runtime primitives.

- [Multi-recipe composition](/docs/multi-recipe-composition)
