---
name: plan
description: Universal task planner. Use when the user requests a complex task that requires thinking before doing. Detects phrases like "이거 해줘", "build X", "구현해줘", "set up", "만들어줘" where the task involves 3+ steps. Plans first, then executes — and can delegate to /bulk or /pipeline when the plan calls for it.
metadata:
  author: openmagi
  version: "1.0"
  user_invocable: true
  triggers:
    - /plan
---

# Plan — Think First, Then Execute

When a user asks for something complex ("이 기능 구현해줘", "build me X", "set this up"), you tend to dive in and lose the thread halfway. This skill forces you to plan the full approach first, then execute step by step — checking off each step before moving to the next.

This is the **universal task planner**. It plans any complex work, and when execution calls for it, delegates to specialized execution skills like `/bulk` (parallel independent tasks) or `/pipeline` (dependency-chain workflows with cron).

**This is a RIGID skill. Follow the protocol exactly. Do not skip steps.**

## When to Use

### Automatic Detection (suggest plan mode)
- User asks for a goal that involves 3+ implementation steps
- The task requires research/analysis before action
- Multiple files, services, or systems need coordinated changes
- The outcome isn't obvious from the request alone

When auto-detected, ask:
> "복잡한 작업이라 계획을 먼저 세우고 단계별로 진행할까요?"

If user says yes (or anything affirmative), proceed. If no, handle normally.

### Explicit Trigger (immediate plan mode)
- `/plan <task description>` — enter plan mode immediately, no confirmation needed
- User explicitly says "계획 세워서 해줘", "plan it out", "step by step으로 해줘"

### When NOT to Use
- Simple tasks completable in 1-2 steps → just do it
- Pure research/analysis with no implementation → use deep-research

## The Protocol

### Phase 1: Analyze & Plan

**Do NOT touch any code yet.** Think first.

1. **Understand the goal** — What is the user actually trying to achieve? Ask clarifying questions if ambiguous.
2. **Survey the codebase** — Read relevant files, understand current state, identify constraints.
3. **Design the approach** — Architecture decisions, file changes needed, potential risks.
4. **Write the plan** — Break into bite-sized sequential steps.

Write the plan to `plans/PLAN.md` (overwrite if exists):

```markdown
# [Goal Summary]

**목표:** [One sentence]
**접근:** [2-3 sentences about the approach]

---

## Steps

### Step 1: [Action]
- [ ] [Specific sub-task with file path]
- [ ] [Verification command or check]

### Step 2: [Action]
- [ ] [Specific sub-task]
- [ ] [Verification]

...

### Step N: Final Verification
- [ ] [End-to-end check]
```

**Plan rules:**
- Each step = one focused action (2-5 minutes)
- Include exact file paths
- Include verification for each step (test, build, manual check)
- Order matters — each step should build on the previous
- Keep it DRY — don't repeat context across steps

### Phase 2: Confirm

Present the plan to the user:

> **계획 (N단계)**
> 1. [step 1 summary]
> 2. [step 2 summary]
> ...
> N. 최종 검증
>
> 수정할 부분 있으면 말해줘. 없으면 시작합니다.

If user's tone is urgent ("바로 해", "고고", "just do it"), skip confirmation and start.

### Phase 3: Choose Execution Strategy

After planning, decide the best execution method based on what the plan looks like:

#### A) Sequential (default) — steps depend on each other
Process one step at a time:
1. Mark current step as 🔄 in `plans/PLAN.md`
2. **Focus entirely on this step** — pretend other steps don't exist
3. Execute all sub-tasks in the step
4. Run the verification
5. If verification passes: mark as ✅, move to next step
6. If verification fails: fix the issue, re-verify, then mark ✅

**Between steps**, briefly report:
> ✅ Step N 완료: [one-line summary]
> 🔄 Step N+1 시작: [what you're about to do]

#### B) Bulk dispatch — plan reveals independent parallel tasks
If the plan contains a group of steps that are **completely independent** (no shared files, no output dependencies), delegate them to `/bulk`:
1. Complete any prerequisite sequential steps first
2. Hand off the independent group via `/bulk` with full context per task
3. After `/bulk` completes, resume remaining sequential steps

Example: "Step 1-2: set up DB schema (sequential) → Step 3-5: build 3 independent API endpoints (bulk) → Step 6: integration test (sequential)"

#### C) Pipeline — plan requires long-running auto-progression
If the plan involves steps that should auto-progress on a schedule (e.g., iterative research, polling, multi-round refinement), delegate to `/pipeline`:
1. Complete any prerequisite steps first
2. Hand off the pipeline-appropriate portion via `/pipeline` with step definitions
3. Monitor pipeline completion, then resume remaining work

**Decision guide:**
| Plan Shape | Strategy |
|-----------|----------|
| Steps build on each other linearly | **Sequential** (A) |
| Middle section has 3+ independent tasks | **Sequential → Bulk → Sequential** (A+B) |
| Steps need auto-progression or cron | **Sequential → Pipeline** (A+C) |
| Mix of everything | Combine as needed — plan is the map |

### Phase 4: Handle Blockers

When a step fails or you're stuck:

1. Mark step as ⚠️ in plan
2. **Stop immediately** — do not proceed to next step
3. Report:
   > "⚠️ Step N 문제 발생: [reason]
   > 해결 방법:
   > A) [option A]
   > B) [option B]
   > 어떻게 할까요?"
4. Wait for user decision before continuing

### Phase 5: Completion

When all steps are done:

1. Run final end-to-end verification
2. Clean up `plans/PLAN.md` (delete or archive)
3. Report:

> **완료 (N/N)**
> ✅ Step 1: [summary]
> ✅ Step 2: [summary]
> ...
> ✅ Step N: [summary]
>
> [Any follow-up recommendations]

## Complexity Adaptation

Not all plans need the same depth:

| Complexity | Steps | Plan Detail | Example |
|-----------|-------|-------------|---------|
| Medium (3-5 steps) | Inline plan | Brief — file paths + actions | "Add a new API endpoint with auth" |
| High (6-10 steps) | Full plan doc | Detailed — code snippets + tests | "Build a notification system" |
| Very High (10+ steps) | Full plan + TDD | Complete — failing tests first | "Migrate auth from JWT to OAuth" |

For **medium** tasks: plan can be shown inline (no file needed) and executed immediately.
For **high/very high** tasks: always write to `plans/PLAN.md`.

## vs Other Skills

| Skill | When to Use | Relationship to /plan |
|-------|------------|----------------------|
| **/plan** | Complex goal → think first → structured execution | This skill — the planner |
| **/bulk** | Multiple independent tasks → queue → parallel dispatch | /plan can delegate to /bulk for parallel steps |
| **/pipeline** | Long-running workflow with auto-progression via cron | /plan can delegate to /pipeline for cron-based steps |
| **/complex-coding** | Delegate to coding agent subagent (PM mode) | /plan can recommend /complex-coding for heavy coding steps |

**Key distinction:** `/bulk` and `/pipeline` are execution engines. `/plan` is the brain that decides what to do, then picks the right engine for each part of the plan.

## Common Mistakes

| Mistake | Why it's bad | Do this instead |
|---------|-------------|-----------------|
| Diving into code without planning | Lose track, miss edge cases | Always plan first |
| Planning too granularly | Analysis paralysis, plan becomes stale | Keep steps bite-sized but not micro |
| Skipping verification between steps | Errors compound | Verify every step |
| Continuing past a blocker | Later steps break | Stop and ask |
| Not updating the plan | Lost progress tracking | Mark ✅/🔄/⚠️ as you go |

## Anti-Patterns

**The "I already know how to do this" skip** — Even if you think you know the approach, write the plan. The act of writing reveals gaps you didn't see.

**The infinite planning loop** — If planning takes longer than 2 minutes, your plan is too detailed. Ship the plan and start executing.

**Context bleed** — Thinking about Step 5 while working on Step 2. Each step is its own world.
