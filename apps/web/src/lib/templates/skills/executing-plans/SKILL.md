---
name: executing-plans
description: Use when you have a written implementation plan to execute step by step with review checkpoints
---

# Executing Plans

## Overview

Load plan, review critically, execute tasks in batches, checkpoint between batches.

**Core principle:** Batch execution with checkpoints for review.

## The Process

### Step 1: Load and Review Plan

1. Read plan file (`plans/CURRENT-PLAN.md` or specified plan)
2. Review critically -- identify any questions or concerns
3. If concerns: Raise them with Kevin before starting
4. If no concerns: Proceed

### Step 2: Execute Batch

**Default: First 3 tasks**

For each task:
1. Mark as in_progress (change icon to arrow)
2. Follow each step exactly (plan has bite-sized steps)
3. Run verifications as specified
4. Mark as completed

### Step 3: Checkpoint

When batch complete:
- Update SCRATCHPAD.md with progress
- Update plan status icons
- Show what was implemented
- Show verification output
- If Kevin is in conversation: "Ready for feedback."
- If autonomous: Continue to next batch

### Step 4: Continue

- Apply changes if needed based on feedback
- Execute next batch
- Repeat until complete

### Step 5: Complete

After all tasks complete and verified:
- Run full verification suite (see `skills/verification-before-completion/SKILL.md`)
- Update SCRATCHPAD with final state
- Delete CURRENT-PLAN.md
- Commit all work

## When to Stop and Ask

**STOP executing immediately when:**
- Hit a blocker (missing dependency, test fails repeatedly, instruction unclear)
- Plan has critical gaps
- You don't understand an instruction
- Verification fails repeatedly (3+ times on same step)

**Ask for clarification rather than guessing.**

## Remember

- Review plan critically first
- Follow plan steps exactly
- Don't skip verifications
- Reference skills when plan mentions them
- Between batches: checkpoint to SCRATCHPAD
- Stop when blocked, don't guess
- Never commit on main without Kevin's consent
