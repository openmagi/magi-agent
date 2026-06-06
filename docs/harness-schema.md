# Harness Schema Reference

Complete schema reference for HarnessEngine, HarnessResolutionRequest, ResolvedHarnessPresetState, EvidenceContractScope, and BuiltinHarnessPreset.

The full schema for harness resolution: how agent role, spawn depth, and run context determine which evidence contracts and hook presets apply.

## HarnessResolutionRequest

HarnessResolutionRequest specifies the agent context for harness resolution. The HarnessEngine uses it to determine which evidence contracts apply and which hooks are in scope.

- agent_role (AgentRole, alias agentRole, default "general") — One of "general", "coding", "research".
- spawn_depth (int, alias spawnDepth, default 0, ge 0) — Spawn depth. 0 for main runs, >0 for child runs.
- run_on (RunOn | None, alias runOn, default None) — One of "main", "child", or None (auto-derived from spawn_depth).
- opted_out_evidence_contract_ids (tuple[str, ...], alias optedOutEvidenceContractIds, default ()) — Contract IDs the user has opted out of.

### Validation: run_on and spawn_depth must be consistent

```
# main runs must use spawnDepth=0
# child runs must use spawnDepth > 0
```

## ResolvedHarnessPresetState

ResolvedHarnessPresetState is the output of HarnessEngine.resolve(). It captures the fully resolved harness state: which packs are enabled, which evidence contracts apply, which were skipped or opted out, and the verdict readiness metadata.

- profile_name (str, alias profileName) — Profile name, e.g. "openmagi-opinionated".
- run_on (RunOn, alias runOn, default "main") — "main" or "child".
- agent_role (AgentRole, alias agentRole, default "general") — "general", "coding", or "research".
- spawn_depth (int, alias spawnDepth, default 0) — Spawn depth of this run.
- coding (ResolvedHarnessPack) — Coding pack resolution state.
- research (ResolvedHarnessPack) — Research pack resolution state.
- verification (ResolvedHarnessPack) — Verification pack resolution state.
- hard_safety (ResolvedHardSafety, alias hardSafety) — Hard safety gates that cannot be opted out.
- evidence_contracts (tuple[ResolvedEvidenceContractSnapshot, ...]) — All evidence contract snapshots.
- effective_evidence_contracts (tuple[str, ...], alias effectiveEvidenceContracts) — Contract IDs with enforcement enabled.
- skipped_evidence_contracts (tuple[SkippedEvidenceContract, ...]) — Contracts skipped with reason.
- evidence_verdict_readiness (EvidenceVerdictReadinessMetadata) — Verdict readiness tracking.

- [Harnesses overview](/docs/harnesses)
- [Build a harness](/docs/build-a-harness)

## EvidenceContractScope

EvidenceContractScope defines when an evidence contract applies based on agent role, run type, and spawn depth. It also declares enforcement level, opt-out policy, and hard safety status.

- contract_id (str, alias contractId) — Non-empty contract identifier.
- agent_roles (tuple[AgentRole, ...], alias agentRoles) — Which agent roles this contract applies to. Non-empty, no duplicates.
- run_on (tuple[RunOn, ...], alias runOn) — Which run types ("main", "child") this contract applies to.
- spawn_depth (SpawnDepthRange, alias spawnDepth) — Depth range filter with min_depth (default 0) and optional max_depth.
- enforcement (EvidenceEnforcement, default "off") — One of "off", "audit", "block_final_answer".
- audit_before_block (bool, alias auditBeforeBlock, default True) — Required true when enforcement is "block_final_answer".
- opt_out_allowed (bool, alias optOutAllowed, default True) — Whether opt-out is permitted. Must be False for hard_safety contracts.
- hard_safety (bool, alias hardSafety, default False) — Hard safety contracts cannot be opted out.
- failure_channel (Literal["evidence_contract"], default "evidence_contract") — Failure routing channel.

## BuiltinHarnessPreset Catalog

The builtin preset catalog defines all standard harness presets. Each preset declares its category, hook points, opt-out policy, and contributed hooks/tools/ledgers. Hard-safety presets (dangerous-patterns, path-escape, secret-exposure, git-safety, sealed-files, arity-permission) cannot be opted out and are always security-critical.

- answer-quality — Category: answer. Hook: afterLLMCall. Verifier gate: answer-quality.
- fact-grounding — Category: fact. Hook: afterLLMCall. Verifier gate: grounding-required.
- self-claim — Category: fact. Hook: afterLLMCall. Verifier gate: self-claim.
- deterministic-evidence — Category: fact. Hooks: afterToolUse, afterLLMCall. Verifier gate: deterministic-evidence.
- coding-verification — Category: coding. Hook: beforeCommit. Blocking, fail-open.
- coding-context — Category: coding. Hook: beforeLLMCall. Contributes repo-map, coding-context, focus-chain hooks.
- response-language — Category: output. Hook: afterLLMCall. Config gate: response-language-policy.
- source-authority — Category: research. Hooks: afterToolUse, afterLLMCall. Contributes source-ledger.
- memory-continuity — Category: memory. Hooks: beforeCompaction, afterCompaction. Contributes memory-ledger.
- dangerous-patterns — Category: security. Hard-safety. Hook: beforeToolUse. Blocking.
- path-escape — Category: security. Hard-safety. Hooks: beforeToolUse, beforeCommit.
- secret-exposure — Category: security. Hard-safety. Hook: beforeCommit. Blocking, fail-open.
- git-safety — Category: security. Hard-safety. Hook: beforeToolUse. Blocking, fail-closed.
- sealed-files — Category: security. Hard-safety. Hooks: beforeTurnStart, beforeCommit, afterCommit.

- [Hook points reference](/docs/hook-points-reference)
