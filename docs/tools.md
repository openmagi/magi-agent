# Tools

Tools are the controlled activity surface, not direct model authority.

ToolHost / activity boundary checks decide whether source, file, delivery, child, memory, artifact, workspace, and integration operations can execute and what receipts they produce.

## ToolHost / activity boundary

A tool call is a proposal until it crosses the ToolHost / activity boundary. The boundary checks policy, permissions, approvals, idempotency, workspace scope, and tool-specific invariants.

Successful activity produces receipts. Source/file/test/calculation/delivery operations can create evidence used by validators and guardrails.

The runtime includes 21 core tools. Two (Bash, TestRun) are marked dangerous and require approval.

- Read: FileRead, FileEdit (inspection), Glob, Grep, GitDiff, ArtifactRead, ArtifactList.
- Write: FileWrite, FileEdit (mutation), ArtifactCreate.
- Execute: Bash (dangerous, requires approval), TestRun (dangerous, 5 minute timeout).
- Meta: AskUserQuestion, EnterPlanMode, ExitPlanMode, Clock, Calculation, HealthStatus, TaskList, TaskGet, TaskOutput, CronList.
- Source reads produce source receipts and citeable spans.
- File reads and writes produce path, digest, and workspace-scope receipts.
- Tests and calculations produce executable evidence and result digests.
- Delivery tools produce delivery receipts and destination-safe projections.
- Side-effecting tools require approval and idempotency receipts.

## Validators and guardrails

Validators check claims and actions against receipts. They should run close to the boundary where unsupported data would become durable or visible.

Guardrails are runtime checks over state transitions. They are stronger than asking the model to remember a rule.
