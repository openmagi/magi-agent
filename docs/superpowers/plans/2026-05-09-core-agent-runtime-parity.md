# Core Agent Runtime Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring `openmagi/magi-agent` back to parity with the current cloud `clawy-core-agent` runtime while preserving OSS-only local provider, CLI, local KB, and `.magi` workspace behavior.

**Architecture:** Treat `/Users/kevin/Desktop/claude_code/clawy` `origin/main:infra/docker/clawy-core-agent/src` as the committed cloud source of truth. Port behavior by feature, not by wholesale file copy, because `magi-agent` intentionally differs in branding, local LLM provider plumbing, app routes, CLI, and storage roots.

**Tech Stack:** TypeScript, Vitest, Node runtime tools, SSE agent events, React/Vite web app.

---

## Current Inspection Summary

Cloud committed source inspected: `/Users/kevin/Desktop/claude_code/clawy` `origin/main`, path `infra/docker/clawy-core-agent/src`.

OSS source inspected: `/Users/kevin/Desktop/claude_code/magi-agent` `main`, path `src`.

Already present in OSS and should not be reworked unless tests show a regression:
- Execution contract acceptance criteria, criterion-linked evidence, resource bindings, resource boundary gate.
- Source ledger, research contract, `ExternalSourceCache`, `ExternalSourceRead`, and `source_inspected` events.
- `PatchApply`, patch previews, package dependency source resolver, project verification planner, command output logs, background shell tasks.
- Browser tools in child-agent presets.
- OSS-only local LLM providers, CLI, local KB, app runtime routes, and `.magi` storage roots.

Confirmed gaps:
- `SpawnWorktreeApply` tool is missing.
- Goal loop is behind: no distilled goal spec, no completion criteria in judge/continuation, default budget is `5` capped at `20` instead of `30` capped at `50`, and goal-mode execution-contract evidence is missing.
- Live model progress event `llm_progress` is missing from runtime sanitizer/SSE/web.
- Concrete child-agent work progress is partially behind: no delegated prompt summary detail and no child tool start/end previews.
- `CodingBenchmark` is behind: OSS only has `record` and `summary`; cloud has `list_tasks`, `start_run`, `report`, golden task workspaces, and benchmark report evidence.
- Browser preview frames are wired in OSS transport/web, but `src/tools/Browser.ts` does not emit frames after actions.
- Social browser one-time connect retry flow is missing.
- Session resume is behind: no intent classifier, no active-work resume packet, no 30s fail-open blocking timeout, and `Agent` queues resume seed as a normal injected message instead of hidden context.
- Missing coverage files: `src/Session.goalLoop.test.ts` and `src/turn/HookContextBuilder.test.ts`.

Guardrails:
- Preserve `.magi` paths in OSS. Do not introduce `.clawy` or `clawy-*` runtime names.
- Preserve `withMagiBinPath`, local provider registration, CLI, local KB, and no cloud-only API proxy assumptions.
- Do not add cloud smart routers to OSS. User-selected/configured LLM remains the only model source.

### Task 1: Goal Loop Metadata And Budget

**Files:**
- Modify: `src/goals/GoalLoop.ts`
- Modify: `src/goals/GoalLoop.test.ts`
- Modify: `src/goals/GoalJudge.ts`
- Test: `src/goals/GoalLoop.test.ts`, `src/goals/GoalJudge.test.ts`

- [ ] Add a failing test in `src/goals/GoalLoop.test.ts` for cloud budget behavior:
  - `goalLoopMaxTurns({})` returns `30`.
  - `CORE_AGENT_GOAL_MAX_TURNS="200"` caps at `50`.
  - invalid values fall back to `30`.
- [ ] Add a failing test for `parseGoalSpecResult`:
  - JSON with `title`, `objective`, `completionCriteria` is parsed and bounded.
  - malformed JSON falls back to a compact title/objective and default completion criterion.
- [ ] Port from cloud `goals/GoalLoop.ts`:
  - `GoalSpec`
  - `parseGoalSpecResult`
  - `distillGoalSpec`
  - completion criteria fields in `GoalContinuationInput`
  - completion criteria in `buildGoalContinuationMessage`
  - budget default `30`, cap `50`
- [ ] Port `GoalJudgeInput.completionCriteria` from cloud `goals/GoalJudge.ts` and include criteria in the judge prompt.
- [ ] Run:
  ```bash
  npm test -- src/goals/GoalLoop.test.ts src/goals/GoalJudge.test.ts
  ```
  Expected: both files pass.

### Task 2: Goal Loop Execution Contracts

**Files:**
- Modify: `src/execution/ExecutionContract.ts`
- Modify: `src/execution/ExecutionContract.test.ts`
- Modify: `src/Session.ts`
- Create: `src/Session.goalLoop.test.ts`

- [ ] Add failing tests from cloud `Session.goalLoop.test.ts`, adapted only for Magi names where assertions contain branding.
- [ ] Add failing tests from cloud `ExecutionContract.test.ts` for `goalMode` start-turn control:
  - initial goal loop uses heavy control reason `goal_loop`.
  - continuation uses heavy control reason `goal_loop_continuation`.
  - goal metadata becomes constraints/current plan.
- [ ] Port cloud `ExecutionContract.startTurn({ userMessage, metadata })` support, including `goalContractFromMetadata`, `goalContractConstraints`, and `goalContractPlan`.
- [ ] Port cloud `Session.ts` goal handling:
  - call `distillGoalSpec` for initial goal mode.
  - persist `missionTitle`, `goalObjective`, `goalCompletionCriteria`, `goalSourceRequest`.
  - pass criteria into `judgeGoalTurn`.
  - call `recordGoalJudgeContractEvidence` for done, blocked, needs-user, and budget-exhausted decisions.
  - pass metadata into `executionContract.startTurn`.
- [ ] Run:
  ```bash
  npm test -- src/execution/ExecutionContract.test.ts src/Session.goalLoop.test.ts
  ```
  Expected: both files pass.

### Task 3: Live Model Progress

**Files:**
- Modify: `src/transport/SseWriter.ts`
- Modify: `src/transport/safeAgentEvent.ts`
- Modify: `src/transport/safeAgentEvent.test.ts`
- Modify: `src/Turn.ts`
- Modify: `src/Turn.retry.test.ts`
- Modify: `src/turn/HeartbeatMonitor.ts`
- Modify: `src/turn/HeartbeatMonitor.test.ts`
- Modify: `apps/web/src/App.tsx`
- Modify: `apps/web/app-shell.test.ts`

- [ ] Add failing sanitizer tests for `llm_progress` based on cloud `safeAgentEvent.test.ts`.
- [ ] Add failing turn test from cloud `Turn.retry.test.ts` asserting `llm_progress` before LLM wait.
- [ ] Add `llm_progress` to `AgentEvent` in `src/transport/SseWriter.ts`.
- [ ] Add sanitizer case in `src/transport/safeAgentEvent.ts`.
- [ ] Port cloud `Turn.ts` emissions before `readOneStream`.
- [ ] Port cloud heartbeat interval changes: silence threshold `20_000`, interval `30_000`.
- [ ] Add app handling so `llm_progress` appears in the work inspector as a non-raw progress row.
- [ ] Run:
  ```bash
  npm test -- src/transport/safeAgentEvent.test.ts src/Turn.retry.test.ts src/turn/HeartbeatMonitor.test.ts apps/web/app-shell.test.ts
  npm run web:check
  ```
  Expected: tests and web typecheck pass.

### Task 4: Concrete Child-Agent Work Progress

**Files:**
- Modify: `src/util/toolResult.ts`
- Modify: `src/spawn/ChildAgentLoop.ts`
- Modify: `src/spawn/ChildAgentLoop.test.ts`
- Modify: `src/tools/SpawnAgent.ts`
- Modify: `src/tools/SpawnAgent.test.ts`
- Modify: `src/turn/ToolDispatcher.ts`
- Modify: `src/turn/ToolDispatcher.test.ts`
- Modify: `src/transport/SseWriter.ts`
- Modify: `src/transport/safeAgentEvent.ts`
- Modify: `src/transport/safeAgentEvent.test.ts`
- Modify: `apps/web/src/App.tsx`

- [ ] Add failing tests from cloud `ChildAgentLoop.test.ts` and `ToolDispatcher.test.ts` for child tool start/end previews.
- [ ] Port cloud `summariseDelegatedPrompt` and `buildToolInputPreview` into `src/util/toolResult.ts`.
- [ ] Port delegated detail on `spawn_started` and child harness creation in `src/tools/SpawnAgent.ts`.
- [ ] Port cloud `ChildAgentLoop.ts` child tool event id, start/end emitters, and output preview behavior.
- [ ] Ensure `apps/web/src/App.tsx` renders the resulting `tool_start`, `tool_end`, and `child_progress` events without raw JSON dumps.
- [ ] Run:
  ```bash
  npm test -- src/spawn/ChildAgentLoop.test.ts src/tools/SpawnAgent.test.ts src/turn/ToolDispatcher.test.ts src/transport/safeAgentEvent.test.ts apps/web/app-shell.test.ts
  ```
  Expected: all pass.

### Task 5: SpawnWorktreeApply Tool

**Files:**
- Create: `src/tools/SpawnWorktreeApply.ts`
- Create: `src/tools/SpawnWorktreeApply.test.ts`
- Modify: `src/Agent.ts`
- Modify: `src/tools/SpawnAgent.ts`
- Modify: `src/verification/VerificationEvidence.ts`
- Modify: `src/verification/VerificationEvidence.test.ts`

- [ ] Copy cloud `tools/SpawnWorktreeApply.ts` and replace any cloud workspace naming with OSS-safe `.spawn` and `.magi` conventions if encountered.
- [ ] Copy cloud `tools/SpawnWorktreeApply.test.ts` and adapt assertions only where Magi branding or path roots differ.
- [ ] Register `makeSpawnWorktreeApplyTool(config.workspaceRoot)` in `src/Agent.ts` initial registration and native override restoration.
- [ ] Restore the `SpawnAgent` description sentence: after a `git_worktree` child finishes, use `SpawnWorktreeApply` to preview, apply, or reject child changes.
- [ ] Add `"SpawnWorktreeApply"` and evidence kind `"spawn_worktree_apply"` to verification evidence allowlists/tests.
- [ ] Run:
  ```bash
  npm test -- src/tools/SpawnWorktreeApply.test.ts src/tools/SpawnAgent.test.ts src/verification/VerificationEvidence.test.ts src/Agent.tools.test.ts
  ```
  Expected: all pass.

### Task 6: Browser Frame Emit And Social Connect

**Files:**
- Modify: `src/tools/Browser.ts`
- Modify: `src/tools/Browser.test.ts`
- Modify: `src/tools/SocialBrowser.ts`
- Modify: `src/tools/SocialBrowser.test.ts`
- Recheck: `src/transport/SseWriter.ts`, `src/transport/safeAgentEvent.ts`, `apps/web/src/App.tsx`

- [ ] Add failing `Browser` test based on cloud: successful `open` emits `browser_frame` without CDP secrets.
- [ ] Port cloud browser frame helpers into `src/tools/Browser.ts`.
- [ ] Use OSS transient directory `.magi/browser-frames` instead of `.clawy` or `.openmagi`.
- [ ] Emit frames after successful `open`, `snapshot`, `scrape`, `click`, `fill`, `scroll`, `screenshot`, `mouse_click`, `keyboard_type`, and `press`.
- [ ] Add failing `SocialBrowser` test based on cloud: `social_browser_session_required` asks user to connect, then retries claim.
- [ ] Port `socialConnectChoiceId`, `SOCIAL_BROWSER_CANCEL_CHOICE_ID`, `runRequiresSocialBrowserSession`, and `askToConnectSocialBrowser`.
- [ ] Preserve OSS session names and Magi binary path usage, for example `magi-social-instagram-*` and `withMagiBinPath`.
- [ ] Run:
  ```bash
  npm test -- src/tools/Browser.test.ts src/tools/SocialBrowser.test.ts src/transport/safeAgentEvent.test.ts apps/web/app-shell.test.ts
  ```
  Expected: all pass.

### Task 7: Coding Benchmark Golden Runner And Reports

**Files:**
- Modify: `src/tools/CodingBenchmark.ts`
- Modify: `src/tools/CodingBenchmark.test.ts`
- Modify: `src/verification/VerificationEvidence.ts`
- Modify: `src/verification/VerificationEvidence.test.ts`

- [ ] Add failing tests from cloud `CodingBenchmark.test.ts` for:
  - `list_tasks`
  - `start_run`
  - unknown golden task rejection
  - `report`
  - evidence kind `benchmark_report`
- [ ] Port cloud `CodingBenchmark.ts` actions and types:
  - `CodingBenchmarkSuite`
  - `CodingGoldenTask`
  - `CodingGoldenRun`
  - report grouping by category/task/golden run
  - Markdown and JSON report renderers
- [ ] Use OSS paths:
  - `.magi/coding-benchmark-runs.jsonl`
  - `.magi/coding-benchmark-golden`
  - `.magi/coding-benchmark-reports`
- [ ] Run:
  ```bash
  npm test -- src/tools/CodingBenchmark.test.ts src/verification/VerificationEvidence.test.ts
  ```
  Expected: all pass.

### Task 8: Session Resume Priority

**Files:**
- Modify: `src/hooks/builtin/sessionResume.ts`
- Modify: `src/hooks/builtin/sessionResume.test.ts`
- Modify: `src/Agent.ts`
- Modify: `src/Session.ts`

- [ ] Add failing tests from cloud `sessionResume.test.ts` for:
  - resume turn intent classification.
  - active-work resume packet when the user asks about current/interrupted work.
  - hook timeout `30_000`, blocking, fail-open behavior.
- [ ] Port cloud `classifyResumeTurnIntent`, `appendActiveWorkResumePacket`, and `BuildSessionResumeBlockOptions`.
- [ ] Update `makeSessionResumeHook` to `blocking: true`, `failOpen: true`, `timeoutMs: 30_000`.
- [ ] In `src/Agent.ts`, change `appendResumeSeed` to set `s.meta.resumeSeededAt = Date.now()` and call `s.enqueueHiddenContext(seed)`.
- [ ] Ensure `SessionMeta` includes `resumeSeededAt?: number`.
- [ ] Run:
  ```bash
  npm test -- src/hooks/builtin/sessionResume.test.ts src/Agent.routing.test.ts src/Session.inject.test.ts
  ```
  Expected: all pass.

### Task 9: Missing Coverage Files

**Files:**
- Create: `src/turn/HookContextBuilder.test.ts`
- Recheck: `src/turn/HookContextBuilder.ts`

- [ ] Copy cloud `turn/HookContextBuilder.test.ts`.
- [ ] Confirm `buildHookContext` exposes `sourceLedger` and `researchContract`.
- [ ] Run:
  ```bash
  npm test -- src/turn/HookContextBuilder.test.ts
  ```
  Expected: pass.

### Task 10: Full Verification And PR

**Files:**
- Rebuild generated web asset only if the repo currently tracks `apps/web/dist`.
- Update docs only if README currently claims features that changed during implementation.

- [ ] Run focused suite:
  ```bash
  npm test -- src/goals/GoalLoop.test.ts src/goals/GoalJudge.test.ts src/execution/ExecutionContract.test.ts src/Session.goalLoop.test.ts src/transport/safeAgentEvent.test.ts src/Turn.retry.test.ts src/turn/HeartbeatMonitor.test.ts src/spawn/ChildAgentLoop.test.ts src/tools/SpawnAgent.test.ts src/tools/SpawnWorktreeApply.test.ts src/tools/Browser.test.ts src/tools/SocialBrowser.test.ts src/tools/CodingBenchmark.test.ts src/hooks/builtin/sessionResume.test.ts src/turn/HookContextBuilder.test.ts
  ```
- [ ] Run full verification:
  ```bash
  npm run web:check
  npm run web:build
  npm run lint
  npm test
  npm run build
  git diff --check
  ```
- [ ] Manually smoke local app if runtime starts:
  ```bash
  npm run dev
  ```
  Check `/app` chat, work inspector progress, browser frame preview, dashboard link, and configured LLM selector.
- [ ] Commit with message:
  ```bash
  git add src apps/web docs package.json package-lock.json
  git commit -m "feat: sync core agent runtime parity"
  ```
- [ ] Push branch and open draft PR against `openmagi/magi-agent:main`.

## Suggested Execution Order

1. Task 5 first if coding-agent isolation is the top priority.
2. Tasks 1 and 2 together because goal metadata and contracts are coupled.
3. Tasks 3 and 4 together because both feed the work inspector.
4. Tasks 6 and 7 can be parallelized after runtime event types are stable.
5. Task 8 should be last among runtime changes because it affects first-turn behavior and can make unrelated tests harder to read.

## Self-Review

- Spec coverage: every confirmed cloud-to-OSS gap from inspection has a task.
- OSS guardrails: plan explicitly preserves `.magi`, local providers, CLI, local KB, and no smart router.
- Known intentional differences: cloud API proxy registration and SaaS delivery URLs are not imported into OSS.
