# Track 19 — General-Automation Live Harness (implementation plan)

> Status: in progress (subagent-driven-development)
> Branch: `track-19-ga-live-harness` off `origin/main` (f1afbce)
> Master flag: `MAGI_GA_LIVE_ENABLED` (default OFF) — distinct from the coding `MAGI_WORKFLOW_EXECUTOR_ENABLED`.
> Rollout: gate-driven (shadow → canary `186bf3d7` → gate≥5 → fleet), fail-closed.

## Why

`harness/general_automation/` + `recipes/first_party/general_automation/` are a complete *declaration/classification/evidence* tier with **no live consumer** (grep: `classify_shell_policy`/`classify_path_access`/`apply_output_budget_policy`/`project_general_automation_preset` have zero production callers outside the GA dir/tests/shadow). The only live tool host is read-only (`tools/local_readonly.py`). This Track wires the verification brain to an execution body for the `general` agent role, and ports OpenCode's completion-driving ergonomics — **without weakening any existing evidence/gate/hard-safety invariant**. Analysis: `docs/plans/2026-06-03-magi-general-automation-harness-improvements.md`.

## Invariants every PR MUST preserve (repo discipline)

1. **Default-OFF.** All new live behavior gated behind `MAGI_GA_LIVE_ENABLED` (and existing readiness gates). Flag-OFF must be a no-op vs current `main`.
2. **Hard-safety untouchable.** Never downgrade `security-policy-hard-safety` verifier, hard-safety hooks/presets, or `Literal[False]` authority flags on metadata/projection models. New live execution lives in *consumers*, not by flipping declaration-layer flags.
3. **Evidence-first.** Any new live tool emits the existing receipt/evidence records; no live mutation without a receipt.
4. **No forbidden-core-path edits.** Respect `discipline_boundary` / `test_general_automation_safe_queue_matrix` path-scope guard (do not edit sealed/forbidden core paths).
5. **pydantic frozen, `extra="forbid"`, typed.** Match surrounding style. `uv run --extra dev pytest` for the touched tests.
6. **TDD.** RED → GREEN → REFACTOR per task.

> Baseline note: 3 tests fail only in this nested worktree due to env (`REPO_ROOT` path-depth overshoot in `test_general_automation_safe_queue_matrix`; sandboxed `socket` import in `test_research_permission_patterns`). They pass in the canonical checkout/CI. Not regressions; implementers ignore these 3 specific ids and run their own targeted tests.

## PR DAG (hybrid)

```
P0 stack (sequential):  PR1 → PR2 → PR3 → PR4
P1 (parallel after PR4): PR5  PR6  PR7  PR8
P2 (parallel after PR4): PR9  PR10  PR11  PR12  PR13
```

---

## P0 — wire the brain to a body

### PR1 — `general` harness pack
**Where it fits:** `harness/resolved.py` builds `ResolvedHarnessPack`s per `(run_on, agent_role)`. `_default_effective_harness_packs` special-cases only `coding`/`research`; `general` gets an evidence-scope context but **no pack**. Add one so the `general` role resolves a concrete tool/hook/permission/evidence posture.
**Spec:**
- Add a `general` `ResolvedHarnessPack` (the model has no `kind` field — it is identified by its field name on `ResolvedHarnessPresetState` + its string in `effective_harness_packs`; `source="builtin"`) with `components`: tools = the GA read set + *gated* shell/file/spreadsheet/web/browser tool *names* (declaration only — no handler attach), `hooks` = GA-scoped hooks, `permissionDefaults` reflecting the GA presets (`write_requires_approval`, `external_directory_requires_approval`), and the GA evidence-scope context.
- Wire it into `_default_effective_harness_packs` selection for `agent_role == "general"` (main gets it; child runs get role pack + hard-safety only, mirroring coding at `resolved.py:382-387`).
- **Flag-OFF inert:** when `MAGI_GA_LIVE_ENABLED` is false the pack still resolves but carries no live handlers (matches today's metadata-only posture), so `main` behavior is unchanged.
**Files:** `magi_agent/harness/resolved.py` (+ helpers it imports). New tests `tests/test_resolved_general_pack.py`.
**Acceptance:** `general` role resolves a pack with the GA tool/permission/evidence components; coding/research packs unchanged; flag-OFF snapshot identical to pre-change for non-general roles.

### PR2 — GA classifiers → live allow/ask/deny gate
**Where it fits:** `tools/permission.py` + `tools/dispatcher.py` decide tool execution. Today GA classifiers are unconsumed. Make them the live gate for the `general` pack.
**Spec:**
- At the dispatch/permission boundary, when the active pack is `general` AND `MAGI_GA_LIVE_ENABLED`: route shell tool calls through `classify_shell_policy`, file/path calls through `classify_path_access`, and apply `apply_output_budget_policy` to tool outputs.
- Map decisions to existing control flow: `denied` → block (raise the existing permission-denied result); `approval_required` → `pending_control_request` via the hook-bus `HookPermissionBoundary` (`hooks/bus.py:176`) + a `control_projection(controlType="approval_required")`; `allowed`/`workspace_local` → proceed.
- Emit the matching receipt (`ShellPolicyReceipt`, `ExternalDirectoryApprovalReceipt`) into the evidence ledger on every gated call.
- Flag-OFF: bypass entirely (current behavior).
**Files:** `tools/permission.py`, `tools/dispatcher.py`, small adapter in `harness/general_automation/` if needed (consume, don't rewrite classifiers). Tests `tests/test_ga_live_permission_gate.py`.
**Acceptance:** with flag ON + `general` pack, a destructive `rm -rf` is blocked with a receipt; an external-dir write yields `pending_control_request` + approval receipt; a workspace read proceeds silently; flag-OFF path unchanged.

### PR3 — task-completion verifier in `finalise`
**Where it fits:** `runtime/turn_policy.py:handle_stop_reason` finalises on `end_turn`. `harness/verifier_bus.py` already orders a `task_plan_completion` stage but ships disabled. Wire a completion verifier so a `general` task can't finalise without the deliverable evidence its contract required.
**Spec:**
- Ship a `TaskCompletionVerifier` (stage `task_plan_completion`) that, for the `general` role with `MAGI_GA_LIVE_ENABLED`, checks the ledger for the required deliverable receipts/artifact-refs declared by the active contract; missing → route `repair` (re-enter loop with a synthetic "you still owe X" message), not terminal.
- Honor the protected hard-safety verifier ordering (semantic critic stays escalation-only; this verifier is deterministic).
- Flag-OFF or non-general: verifier inert (defaultEnabled=False preserved for other roles).
**Files:** `harness/verifier_bus.py` (+ a verifier impl module), hook into `turn_policy` finalise path. Tests `tests/test_ga_task_completion_verifier.py`.
**Acceptance:** a `general` turn that ends `end_turn` without the required artifact receipt is routed to `repair`; with the receipt present it finalises; other roles unaffected.

### PR4 — gate + canary rollout
**Where it fits:** mirror `gates/workflow_executor_readiness.py`.
**Spec:**
- `MAGI_GA_LIVE_ENABLED` env flag (truthy `{1,true,yes,on}`), default OFF, single source of truth (config/env).
- `gates/ga_live_readiness.py`: shadow → canary `186bf3d7` → gate≥5 → fleet, fail-closed, with telemetry counters (gated-call counts, block/approval/allow, completion-verifier repairs).
- Docs: a short `docs/notes/` rollout-readiness note.
**Files:** `config/env.py` (or wherever flags live), `gates/ga_live_readiness.py`, telemetry. Tests `tests/test_ga_live_readiness.py`.
**Acceptance:** gate fails closed until shadow+canary criteria met; flag default OFF verified.

---

## P1 — completion-driving ergonomics (parallel after PR4)

### PR5 — max-steps wrap-up brake
`turn_policy`: at `max_iterations - 1`, inject a wrap-up instruction + disable tool projection so the model emits a "done / remaining / next" summary instead of silent cutoff. Mirrors OpenCode `max-steps.txt`. Tests for the brake firing + tool-disable.

### PR6 — per-turn constraint re-injection hook
A `general`-scoped `BEFORE_LLM_CALL` (or `BEFORE_SYSTEM_PROMPT`) hook that re-injects the active contract's required-evidence checklist + open `approval_required` controls every turn (compaction-proof). Mirrors OpenCode plan-reminder re-injection.

### PR7 — blocking `question` tool
A model-callable `question` tool for the `general` pack: emits `control_projection(controlType="approval_required")` carrying the question (options + free-text), blocks the turn (`pending_control_request`), resumes on reply via the existing control/resume-ref machinery. OpenCode's biggest "don't guess" lever, with an evidence trail.

### PR8 — progressive-disclosure skills/recipes
(a) system-prompt section listing GA presets/contracts by title+whenToUse; (b) a `load_recipe` tool that injects the full contract/playbook on demand; (c) mark that injected body compaction-protected (add a `PRUNE_PROTECTED` analog keyed on the recipe/skill tool-result in `context/microcompact.py` + `auto_compact.py`, mirroring OpenCode `PRUNE_PROTECTED_TOOLS=["skill"]`).

## P2 — plan→act, delegation, hygiene (parallel after PR4)

### PR9 — plan→act live switch
Wire `harness/plan_gate` approval to a `policy_state` re-resolution: on plan-exit-equivalent control approval, flip resolved profile `automation.plan` → execution preset + inject "execute the plan" synthetic message. Snapshot already exists; connect it.

### PR10 — GA scoped delegation
Enable a `general`-scoped, depth≤2 subagent (reuse child-runtime-envelope) returning a **receipt-backed** result. Strictly better than OpenCode's last-text-part (child work is evidenced).

### PR11 — path_policy read/write split
`path_policy` echoes `operation_class` but gates identically. Make `write`/`delete` require approval while workspace `read` stays silent (matches OpenCode `read:"*":"allow"` + env-file asks).

### PR12 — secret-scrub consolidation
Merge the 3 divergent scrub regex sets (`shell_policy` vs `output_budget_policy` vs `followup_refs`/guardrail) into one shared scrubber (superset) to prevent a leak via the weakest set.

### PR13 — shell path target cross-check
`shell_policy` extracts `/`-path tokens but doesn't gate them; pipe extracted targets through `classify_path_access` so e.g. `cat /etc/passwd` redirect/arg targets are path-gated.

---

## Execution flow (this session)
Per-PR: implementer subagent (TDD, commit) → spec-compliance reviewer → code-quality reviewer → fix loop → next PR. After PR13: multiple independent final reviewers → fix loop. Then push branch + open PR(s).
