---
name: retry-with-strategy
description: Use after a failed command, failed fix, rejected answer, blocked commit gate, or repeated error; requires changing the diagnostic strategy rather than retrying blindly
---

# Retry With Strategy

Use this skill whenever the previous attempt failed.

## Process

1. Read the exact failure message.
2. Explain what the failure proves and what it does not prove.
3. Choose one new hypothesis.
4. Change one variable or gather one missing piece of evidence.
5. Retry only after the strategy changes.

## Escalation

After three failed fixes or retries, stop adding patches. Re-check assumptions, compare with a working reference, or ask for a decision if architecture or product intent is unclear.
