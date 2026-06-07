# Tools

Status: ✅ Active — first-party tools are registered and on by default; file/search/edit/patch/Bash run live once a provider key is set (`magi_agent/tools/catalog.py`, `core_toolhost.py`).

Tools are the controlled activity surface, not direct model authority.

ToolHost / activity boundary checks decide whether source, file, delivery, child, memory, artifact, workspace, and integration operations can execute and what receipts they produce.

## ToolHost / activity boundary

A tool call is a proposal until it crosses the ToolHost / activity boundary. The boundary checks policy, permissions, approvals, idempotency, workspace scope, and tool-specific invariants.

Successful activity produces receipts. Source/file/test/calculation/delivery operations can create evidence used by validators and guardrails.

## First-party tool catalog

The core registry (`magi_agent/tools/catalog.py`) declares 25 first-party tools.
24 are `enabled_by_default=True`; `MemoryWrite` is off unless
`MAGI_MEMORY_WRITE_ENABLED` is set. Two (`Bash`, `TestRun`) are marked
`dangerous` and require approval. The handlers for the file / search / execute
tools are bound by the core toolhost (`core_toolhost.py`).

| Tool | Purpose | Permission |
|---|---|---|
| `FileRead` | Read workspace file contents. | read (read-only) |
| `Glob` | List workspace paths matching a glob. | read (read-only) |
| `Grep` | Search workspace text by pattern. | read (read-only) |
| `GitDiff` | Inspect workspace git diff metadata. | read (read-only) |
| `ArtifactRead` | Read artifact metadata / content. | read (read-only) |
| `ArtifactList` | List artifact records for the turn. | read (read-only) |
| `FileWrite` | Write workspace file contents. | write (edit/act) |
| `FileEdit` | Edit existing workspace file contents. | write (edit/act) |
| `PatchApply` | Apply a Codex-style multi-file envelope patch. | write (edit/act) |
| `ArtifactCreate` | Create an artifact record for delivery. | write (edit/act) |
| `MemoryWrite` | Write to local memory. Off by default (gated by `MAGI_MEMORY_WRITE_ENABLED`). | write (gated) |
| `Bash` | Run a shell command (dangerous, requires approval). | execute (act) |
| `TestRun` | Run a project verification command (dangerous, 5-min timeout). | execute (act) |
| `ToolSearch` | Search deferred tool metadata. | meta |
| `TodoWrite` | Record / update the agent's task list. | meta |
| `AskUserQuestion` | Request user input through the control surface. | meta |
| `EnterPlanMode` | Enter read-only planning mode. | meta |
| `ExitPlanMode` | Exit planning and continue in act mode. | meta |
| `Clock` | Read current time metadata. | meta (read-only) |
| `Calculation` | Evaluate deterministic calculation metadata. | meta (read-only) |
| `HealthStatus` | Read local runtime health metadata. | meta (read-only) |
| `TaskList` | List local background task metadata. | meta (read-only) |
| `TaskGet` | Read local background task metadata. | meta (read-only) |
| `TaskOutput` | Read local background task output metadata. | meta (read-only) |
| `CronList` | List local cron schedule metadata. | meta (read-only) |

Read / meta-read tools are concurrency-safe and available in both `plan` and
`act` modes. Write and execute tools are `act`-only and mutate the workspace.

### Example: invocation and approval

A tool call is a proposal that must clear the permission gate. **How the gate is
resolved depends on the surface:**

- **Interactive (`magi` TUI):** under the `default` mode you are prompted to
  approve each tool; on approval it runs and a receipt is recorded.
- **Headless one-shot (`magi -p ...`) in `default` mode with `--output text`:**
  there is no prompt surface to answer the approval, so tool calls are **denied**
  rather than executed. To let tools run headlessly, pass
  `--permission-mode acceptEdits` (auto-allow edit-class tools) or
  `bypassPermissions` (allow all), or drive approvals over
  `--output stream-json` with an inbound responder.

```text
# Interactive — you approve each tool:
magi
> run the test suite and report failures

# Headless, allowing edits without prompts:
magi -p --permission-mode acceptEdits "fix the failing test in foo.py"
```

`Bash` and `TestRun` are `dangerous` and still require explicit approval (they are
not auto-allowed by `acceptEdits`).

Choosing `--permission-mode acceptEdits` auto-allows file edits
(`FileWrite` / `FileEdit` / `PatchApply`) without a prompt, while `Bash` and
`TestRun` still require approval. See [cli/magi.md](cli/magi.md) for the
permission modes and [common-tasks.md](common-tasks.md) for task-to-command
mappings.

- Source reads produce source receipts and citeable spans.
- File reads and writes produce path, digest, and workspace-scope receipts.
- Tests and calculations produce executable evidence and result digests.
- Delivery tools produce delivery receipts and destination-safe projections.
- Side-effecting tools require approval and idempotency receipts.

## Validators and guardrails

Validators check claims and actions against receipts. They should run close to the boundary where unsupported data would become durable or visible.

Guardrails are runtime checks over state transitions. They are stronger than asking the model to remember a rule.
