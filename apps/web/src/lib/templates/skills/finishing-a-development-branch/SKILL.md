---
name: finishing-a-development-branch
description: Use when implementation is complete, all tests pass, and you need to decide how to integrate the work - guides completion of development work by presenting structured options for merge, PR, or cleanup
---

# Finishing a Development Branch

## Overview

Guide completion of development work by presenting clear options and handling chosen workflow.

**Core principle:** Verify tests -> Present options -> Execute choice -> Clean up.

## The Process

### Step 1: Verify Tests

Before presenting options, verify tests pass:

```
system.run ["pytest"]  # or relevant test command
```

**If tests fail:** STOP. Cannot proceed until tests pass.

**If tests pass:** Continue to Step 2.

### Step 2: Present Options

Present exactly these 4 options:

```
Implementation complete. What would you like to do?

1. Merge back to main locally
2. Push and create a Pull Request
3. Keep the branch as-is (I'll handle it later)
4. Discard this work

Which option?
```

Don't add explanation -- keep options concise.

### Step 3: Execute Choice

**Option 1: Merge Locally**
```
system.run ["git", "checkout", "main"]
system.run ["git", "pull"]
system.run ["git", "merge", "<feature-branch>"]
# Verify tests on merged result
system.run ["git", "branch", "-d", "<feature-branch>"]
```

**Option 2: Push and Create PR**
```
system.run ["git", "push", "-u", "origin", "<feature-branch>"]
system.run ["gh", "pr", "create", "--title", "<title>", "--body", "<summary>"]
```

**Option 3: Keep As-Is**
Report: "Keeping branch <name>."

**Option 4: Discard**
Confirm first -- show commits that will be lost. Wait for explicit confirmation.
```
system.run ["git", "checkout", "main"]
system.run ["git", "branch", "-D", "<feature-branch>"]
```

## Red Flags

**Never:**
- Proceed with failing tests
- Merge without verifying tests on result
- Delete work without confirmation
- Force-push without explicit request

**Always:**
- Verify tests before offering options
- Present exactly 4 options
- Get typed confirmation for discard
