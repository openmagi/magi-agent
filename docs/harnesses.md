# Harnesses

Runtime policy extensions that own state, boundaries, evidence, and projection rules.

Harnesses are the active enforcement layer. They resolve which evidence contracts apply to each agent turn based on the agent's role, task depth, and run context, then attach scoped hooks to enforce them.

## What harnesses are

A harness is a runtime policy extension that owns evidence contracts, hook scoping, and preset state. Unlike recipes (which compile metadata snapshots), harnesses actively resolve and enforce evidence requirements during runs.

HarnessEngine is fully implemented. It takes hook manifests, evidence contract scopes, and a rollout mode, then resolves a HarnessResolutionRequest into scoped hooks and a ResolvedHarnessPresetState.

## HarnessEngine resolution

HarnessEngine.resolve() takes a HarnessResolutionRequest (agent_role, spawn_depth, run_on, opted_out_evidence_contract_ids) and returns a tuple of (selected hooks, resolved harness state). The engine delegates to build_default_resolved_harness_state for evidence contract resolution and resolve_scoped_harness_hooks for hook filtering.

### HarnessEngine (Python, implemented and active)

```
class HarnessEngine:
    def __init__(self, *,
        hooks: tuple[HookManifest, ...] = (),
        evidence_contracts: tuple[EvidenceContractScope, ...] = (),
        evidence_rollout_mode: EvidenceRolloutMode = 'audit',
    ) -> None: ...

    def resolve(
        self, request: HarnessResolutionRequest,
    ) -> tuple[tuple[HookManifest, ...], ResolvedHarnessPresetState]: ...

class HarnessResolutionRequest(BaseModel):
    agent_role: AgentRole = 'general'  # 'general' | 'coding' | 'research'
    spawn_depth: int = 0
    run_on: RunOn | None = None  # 'main' | 'child'
    opted_out_evidence_contract_ids: tuple[str, ...] = ()
```

## Evidence contract scoping

EvidenceContractScope defines when an evidence contract applies based on agent_roles (general, coding, research), run_on (main, child), and spawn_depth range (minDepth, maxDepth). The scope resolution produces an EvidenceScopeDecision with applies, effective_enforcement, opt_out_applied, and hard_safety flags.

Contracts with hard_safety=true cannot be opted out. Contracts with enforcement=block_final_answer require audit_before_block=true. Third-party evidence scope defaults enforce traffic-free operation (trafficAttached=false, executionAttached=false).

- AgentRole: 'general' | 'coding' | 'research' -- determines which contracts apply
- RunOn: 'main' | 'child' -- main runs use spawnDepth=0, child runs use spawnDepth>0
- SpawnDepthRange: minDepth (default 0), maxDepth (optional) -- filters by nesting level
- EvidenceEnforcement: 'off' | 'audit' | 'block_final_answer' -- what happens on missing evidence

## ResolvedHarnessPresetState and RuntimeProfile

The resolved state (harness/resolved.py, build_default_resolved_harness_state()) includes three built-in harness packs (coding, research, verification), hard safety gates, effective hooks and packs lists, evidence contract snapshots, and verdict readiness metadata. The default profile is RuntimeProfile('openmagi-opinionated') from harness/profiles.py.

RuntimeProfile defines a HardSafetyPolicy with 5 gates (permission-arbiter, path-safety, secret-safety, sealed-file-policy, git-safety) and 5 FeaturePacks (coding, research, verification, local-tools, cloud). The coding pack includes tools (FileRead, FileEdit, PatchApply), hooks (coding-verification, completion-evidence), and child agent review. The research pack includes tools (WebSearch, WebFetch, KnowledgeSearch), hooks (source-authority, claim-citation, fact-grounding), and citation-required delivery.

## How harnesses differ from prompts, hooks, and skills

Prompts ask the model to cooperate. Hooks observe lifecycle events and can block them. Skills teach procedures. Harnesses own state: they define evidence contracts with triggers and requirements, resolve them against the run context, and produce enforcement verdicts.

A hook can inspect a payload and block a boundary. A harness defines what evidence must exist before that boundary, what happens when evidence is missing (audit or block), and which contracts can be opted out. This is why harnesses compose determinism while hooks compose observation.
