# Multi-Recipe Composition

How multiple recipe packs compose via profile resolution, and how harness scoping selects evidence contracts by role.

Recipe packs compose through profile resolution with restrictive merge semantics. Harnesses scope evidence contracts by agent role and spawn depth. Pack dependencies, opt-out, and provider conflict detection are implemented.

## How composition works

When multiple recipe packs are selected, the recipe compiler merges their policy using CompositionPolicyMetadata. Validators use all_of merge (all must pass). Evidence, approval gates, and audit use union merge (combined set). Provider/tool conflicts are detected and block compilation if different packs declare conflicting providers for the same tool.

Pack dependencies are declared via depends_on_pack_ids. If a dependency is unavailable, the dependent pack is omitted with reason 'dependency_unavailable'. Hard-safety packs cannot be opted out regardless of composition.

## ProfileResolver 5-layer merge

The recipe compilation pipeline is PackRegistry -> ProfileResolver (5-layer merge: user, workspace, task, pack_config, runtime) -> AgentRecipeCompiler -> RecipeSnapshot (snapshot_id = sha256(pack_ids)[:16]). ProfileResolutionRequest provides five layers that are deep-merged with sensitive key sanitization: userProfile (user preferences), workspacePolicy (workspace-level defaults), taskProfile (task-specific overrides), recipePackConfig (pack-specific configuration), and runtimeContext (runtime environment).

Layer merge strips sensitive keys (tokens, secrets, credentials, raw output, private paths) and produces a sanitized ResolvedRecipeProfile. The resolved profile includes selected_pack_ids, opted_out_pack_ids, and composition policy metadata.

### CompositionPolicyMetadata (Python, implemented)

```
class CompositionPolicyMetadata(BaseModel):
    validator_merge: str = 'all_of'      # all validators must pass
    approval_gate_merge: str = 'union'    # union of approval requirements
    evidence_merge: str = 'union'         # union of evidence requirements
    audit_merge: str = 'union'            # union of audit obligations
    budget_cap: int | None = None         # minimum across all layers
    memory_mode: str = 'normal'           # most restrictive wins
    side_effect_posture: str = 'allow'    # most restrictive wins
    conflict_refs: tuple[str, ...] = ()   # detected provider conflicts
    blocked: bool = False                 # true if conflicts exist
```

## Harness scoping by agent role

The harness engine scopes evidence contracts and hooks by agent role (general, coding, research) and run context (main vs child at various spawn depths). Main runs get all four packs (coding, research, verification, hard-safety). Child runs get only their role-specific pack plus hard-safety.

Hooks are scoped via HookScope and filtered by resolve_scoped_harness_hooks. Security-critical hooks always apply regardless of scope. Non-security hooks are skipped if the run context does not match their scope, and the skipped hook names are recorded in skipped_by_scope.

## Opt-out behavior

Users can opt out of evidence contracts by including the contract_id in opted_out_evidence_contract_ids on the HarnessResolutionRequest. Opted-out contracts have effective_enforcement set to 'off' and are recorded in skipped_evidence_contracts with reason 'opted_out'.

Hard-safety contracts (hard_safety=True) cannot be opted out. The validator rejects opt_out_applied=true for hard-safety evidence. Non-hard recipe packs must keep opt_out_allowed=True and customizable=True.

## Implementation status

Recipe pack compilation, profile resolution, composition policy, dependency management, opt-out, and provider conflict detection are implemented. Harness scoping with evidence contract resolution by role, depth, and run context is implemented and active.

The gap: compiled RecipeSnapshots are not consumed by a runtime execution engine. Harness enforcement (via HarnessEngine, evidence contracts, and boundary modules) is the active enforcement path. Recipe metadata and harness enforcement can coexist: packs declare intent via recipes, and contracts enforce requirements via harnesses.
