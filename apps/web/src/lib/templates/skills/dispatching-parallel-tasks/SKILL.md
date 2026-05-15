---
name: dispatching-parallel-tasks
description: Use when facing 2+ independent tasks that can be worked on without shared state or sequential dependencies
---

# Dispatching Parallel Tasks

## Overview

When you have multiple unrelated problems (different test files, different subsystems, different bugs), investigating them sequentially wastes time. Each investigation is independent and can happen in sequence but with clear isolation.

**Core principle:** One problem domain at a time. Clear boundaries. No cross-contamination.

## When to Use

**Use when:**
- 3+ tasks with different root causes
- Multiple subsystems broken independently
- Each problem can be understood without context from others
- No shared state between investigations

**Don't use when:**
- Failures are related (fix one might fix others)
- Need to understand full system state
- Tasks would interfere with each other

## The Pattern

### 1. Identify Independent Domains

Group failures by what's broken:
- Problem A: Soul query timeout
- Problem B: Config file parsing error
- Problem C: Deploy script permission issue

Each domain is independent -- fixing soul query doesn't affect deploy script.

### 2. Create Task Queue

Add each domain as a separate task in `plans/TASK-QUEUE.md`:
```markdown
## Queue
- Task 1: Fix soul query timeout
  - Context: [specific error, related files]
  - Success: soul-query.sh returns within 90s
- Task 2: Fix config parsing error
  - Context: [specific error, related files]
  - Success: openclaw.json validates correctly
- Task 3: Fix deploy script permissions
  - Context: [specific error, related files]
  - Success: deploy-agent-files.sh runs without permission errors
```

### 3. Execute Isolated

For each task:
- Focus ONLY on that domain
- Don't touch files from other domains
- Verify fix independently
- Commit separately
- Move to next task

### 4. Integration Verify

After all tasks complete:
- Run full verification suite
- Check for unexpected interactions
- Confirm all fixes work together

## Common Mistakes

**Too broad scope:** "Fix everything" -- break into specific domains first
**No isolation:** Mixing fixes from different domains in same commit
**No verification:** Skipping integration check after all fixes

## Key Benefits

1. **Focus** -- narrow scope per task, less context to track
2. **Independence** -- tasks don't interfere with each other
3. **Recoverability** -- if session resets, TASK-QUEUE.md tracks progress
4. **Clear commits** -- one logical change per commit
