---
name: subagent-result-qc
description: Use when receiving results from delegated agents, background workers, or parallel subtasks before relying on their claims in the final answer
---

# Subagent Result QC

Use this skill before treating delegated output as true.

## QC Steps

1. Separate evidence from conclusions.
2. Check that each delegated result addresses the assigned scope.
3. Verify critical claims against files, tool output, tests, or primary sources.
4. Detect conflicts between subagent outputs.
5. State uncertainty if a delegated result cannot be verified.

## Never Do

- Do not blindly paste subagent conclusions.
- Do not merge two conflicting results without resolving the conflict.
- Do not upgrade "sample checked" into "fully verified."
