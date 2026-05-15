---
name: task-contract-orchestration
description: Use when a request includes acceptance criteria, task_contract blocks, verification_mode, delivery requirements, or explicit definitions of done
---

# Task Contract Orchestration

Use this skill to preserve and satisfy the user's contract for a task.

## Contract Fields

Track:

- requested outcome
- acceptance criteria
- forbidden changes
- verification mode
- delivery channel or artifact
- deadline or async behavior

## Verification Modes

- `full`: verify every required item. Sampling is not enough.
- `sample`: verify representative items and clearly label the result as sample-verified.
- `none`: do not claim verification; state what was not checked.

## Closeout

Before answering:

1. Compare the final result against each acceptance criterion.
2. Check whether the verification mode was satisfied.
3. State any unmet or unverified items plainly.
4. Never call a task complete if the contract is only partially satisfied.
