# Tools

Tools are the controlled activity surface, not direct model authority.

ToolHost / activity boundary checks decide whether source, file, delivery, child, memory, artifact, workspace, and integration operations can execute and what receipts they produce.

## ToolHost / activity boundary

A tool call is a proposal until it crosses the ToolHost / activity boundary. The boundary checks policy, permissions, approvals, idempotency, workspace scope, and tool-specific invariants.

Successful activity produces receipts. Source/file/test/calculation/delivery operations can create evidence used by validators and guardrails.

## First-party tool catalog

The core registry (`magi_agent/tools/catalog.py`) declares 21 first-party tools.
19 are `enabled_by_default=True`; `MemoryWrite` (gated by
`MAGI_MEMORY_WRITE_ENABLED`) and `InspectSelfEvidence` (gated by
`MAGI_SELF_INTROSPECTION_ENABLED`) are off by default. Two (`Bash`, `TestRun`)
are marked `dangerous` and require approval. The handlers for the file / search
/ execute tools are bound by the core toolhost (`core_toolhost.py`).

| Tool | Purpose | Permission |
|---|---|---|
| `FileRead` | Read workspace file contents. | read (read-only) |
| `Glob` | List workspace paths matching a glob. | read (read-only) |
| `Grep` | Search workspace text by pattern. | read (read-only) |
| `GitDiff` | Inspect workspace git diff metadata. | read (read-only) |
| `FileWrite` | Write workspace file contents. | write (edit/act) |
| `FileEdit` | Edit existing workspace file contents. | write (edit/act) |
| `PatchApply` | Apply a Codex-style multi-file envelope patch. | write (edit/act) |
| `MemoryWrite` | Write to local memory. Off by default (gated by `MAGI_MEMORY_WRITE_ENABLED`). | write (gated) |
| `Bash` | Run a shell command (dangerous, requires approval). | execute (act) |
| `TestRun` | Run a project verification command (dangerous, 5-min timeout). | execute (act) |
| `TodoWrite` | Record / update the agent's task list. | meta |
| `AskUserQuestion` | Request user input through the control surface. | meta |
| `EnterPlanMode` | Enter read-only planning mode. | meta |
| `ExitPlanMode` | Exit planning and continue in act mode. | meta |
| `Clock` | Read current time metadata. | meta (read-only) |
| `Calculation` | Evaluate deterministic calculation metadata. | meta (read-only) |
| `TaskList` | List local background task metadata. | meta (read-only) |
| `TaskGet` | Read local background task metadata. | meta (read-only) |
| `TaskOutput` | Read local background task output metadata. | meta (read-only) |
| `CronList` | List local cron schedule metadata. | meta (read-only) |

Read / meta-read tools are concurrency-safe and available in both `plan` and
`act` modes. Write and execute tools are `act`-only and mutate the workspace.

### WebSearch / WebFetch (plugin web tools)

`WebSearch` and `WebFetch` ship in the `openmagi.web` plugin
(`magi_agent/plugins/native/web.py`), not the core registry above. Following the
catalog's permission convention, they carry the `net` permission (outbound
network egress), distinct from the local read/write/execute/meta tools:

| Tool | Purpose | Permission |
|---|---|---|
| `WebSearch` | Search the web via a live provider router. | net (egress; default: not configured → error) |
| `WebFetch` | Fetch a URL via a live provider router. | net (egress; default: not configured → error) |

They have **no fabricated fallback**: on a default install with
no live web provider configured they return an honest
`web_research_not_configured` error instead of simulated results. Local CLI
search uses the direct web toolset when `BRAVE_API_KEY` and `FIRECRAWL_API_KEY`
are set, or when `MAGI_WEB_SEARCH_PROVIDER=serpapi`, `SERPAPI_API_KEY`, and
`FIRECRAWL_API_KEY` are set. To activate the native provider-router path, set
`CORE_AGENT_PYTHON_LIVE_WEB_ACQUISITION_ENABLED=1` and
`CORE_AGENT_PYTHON_WEB_PROVIDER_ROUTER_ENABLED=1`, then configure
`MAGI_PLATFORM_BASE_URL` + `MAGI_PLATFORM_API_KEY`. Jina Reader
(`CORE_AGENT_PYTHON_JINA_READER_ENABLED=1`, optionally with
`MAGI_JINA_API_KEY`) and InsaneFetch
(`CORE_AGENT_PYTHON_INSANE_FETCH_ENABLED=1`) are fetch/reader-style providers;
they do not by themselves provide live search. With router providers set, the
handlers delegate to `magi_agent/web_acquisition/research_tools.py`.

`WebReader` is **not exposed** by the native plugin — the catalog registers only
`WebSearch` and `WebFetch`. The live provider router has a jina-reader path, but
there is no `WebReader` tool handler today, so it is out of scope for this row.

### Example: invocation and approval

A tool call is a proposal that must clear the permission gate. When
`--permission-mode` is omitted, local CLI runs use `bypassPermissions` so tools
execute without approval prompts. Choose stricter modes explicitly when needed:

- **Prompting mode:** pass `--permission-mode default` to ask before tools that
  require approval.
- **Edit-only auto-approval:** pass `--permission-mode acceptEdits` to allow
  file edits and patches while prompting for non-edit tools.
- **Automation responder:** use `--output stream-json` with an inbound responder
  if you want a host process to answer approval requests.

```text
# Interactive — no approval prompts by default:
magi
> run the test suite and report failures

# Headless, same default-bypass behavior:
magi -p "fix the failing test in foo.py"
```

`Bash` and `TestRun` are `dangerous`; they are not auto-allowed by
`acceptEdits`. `bypassPermissions` allows them after hard-safety checks pass.

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
