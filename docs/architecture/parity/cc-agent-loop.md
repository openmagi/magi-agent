# Claude Code — Agentic Tool-Execution Layer (parity reference for Magi Agent)

> Deep-dive of Claude Code's (CC) **agentic execution layer**: tool set, permission
> model, agent loop, system-prompt/todo/plan mechanics, and tool-result rendering.
> This is the layer the existing CC-CLI interface docs (`00`–`07`) deliberately
> skipped — they cover the *interface* (entrypoint, headless IO, REPL, Ink
> rendering) and treat `query()` as an opaque "engine boundary" (see
> `docs/architecture/claude-code-cli/00-overview.md:44-48`, which literally says
> "tools, model, MCP — out of scope here").
>
> **Source of evidence.** The working copy at
> `/Users/kevin/Desktop/claude_code/cc-workspace/claude-code/src` is **un-minified
> reconstructed TypeScript** (1900+ `.ts/.tsx` files, source-map recovered), NOT
> the shipped minified `cli.js`. So most findings below are **CONFIRMED** at
> `file:line`. Items marked **INFERRED** are where behavior depends on server-side
> code (the sampling API), runtime feature flags, or `USER_TYPE==='ant'` branches
> we can't fully execute.
>
> Goal: copy these patterns into Magi's Python `magi` CLI, which today does
> chat-only with no real tool loop.

---

## 1. Tool set

### 1.1 The `Tool` contract (CONFIRMED — `src/Tool.ts`)

Every tool is an object built via `buildTool(def)` (`Tool.ts:783`) that fills in
fail-closed defaults (`Tool.ts:757-769`): `isEnabled→true`,
`isConcurrencySafe→false`, `isReadOnly→false`, `isDestructive→false`,
`checkPermissions→{behavior:'allow'}` (defers to the general permission system),
`toAutoClassifierInput→''`, `userFacingName→name`.

The interface (`Tool.ts:362-695`) — the load-bearing members for a parity build:

| Member | Purpose | Evidence |
|---|---|---|
| `name`, `aliases?`, `searchHint?` | identity; aliases for renamed tools; `searchHint` is a keyword phrase for ToolSearch | `Tool.ts:347-378,456` |
| `inputSchema` (Zod) / `inputJSONSchema?` | typed params; validated before call (`Tool.ts:394-397`) | `Tool.ts:394` |
| `prompt(opts)` | returns the **tool description string** sent to the model | `Tool.ts:518-523` |
| `description(input,opts)` | short one-liner (separate from `prompt`) | `Tool.ts:386` |
| `validateInput?(input,ctx)` | tool-specific validation → model-facing error, no UI | `Tool.ts:489-492` |
| `checkPermissions(input,ctx)` | tool-specific permission decision (allow/deny/ask/passthrough) | `Tool.ts:500-503` |
| `call(input,ctx,canUseTool,parentMsg,onProgress)` → `Promise<ToolResult<O>>` | the actual execution; can stream progress | `Tool.ts:379-385` |
| `isReadOnly(input)`, `isConcurrencySafe(input)`, `isDestructive?(input)` | scheduling + safety flags | `Tool.ts:402-406` |
| `isEnabled()` | feature/flag gating | `Tool.ts:403` |
| `maxResultSizeChars` | result auto-persisted to disk past this; `Infinity` = never persist | `Tool.ts:457-466` |
| `shouldDefer?` / `alwaysLoad?` | ToolSearch deferral controls | `Tool.ts:442-449` |
| `mapToolResultToToolResultBlockParam(out,id)` | maps tool output → API `tool_result` block | `Tool.ts:557` |
| `renderToolUseMessage`, `renderToolResultMessage?`, `renderToolUseRejectedMessage?`, `renderToolUseErrorMessage?` | TUI rendering (see §5) | `Tool.ts:566-667` |
| `interruptBehavior?()` → `'cancel'|'block'` | what to do if user submits mid-run; default `'block'` | `Tool.ts:407-416` |
| `toAutoClassifierInput(input)` | compact string fed to the auto-mode security classifier | `Tool.ts:549-556` |
| `getPath?`, `preparePermissionMatcher?`, `backfillObservableInput?` | path tools; hook/permission pattern matching; legacy-field backfill | `Tool.ts:481-516` |

### 1.2 Built-in tool registry (CONFIRMED — `src/tools.ts:193-251` `getAllBaseTools()`)

The always-on / common subset (the ones to copy first), with notable behaviors:

| Tool | Purpose | Key params | Notable behavior / limits |
|---|---|---|---|
| **Bash** | run shell command | `command`, `timeout?`, `run_in_background?`, `description?` | `maxResultSizeChars: 30_000` (`BashTool.tsx:424`); `isConcurrencySafe = isReadOnly` (`BashTool.tsx:434-435`); default/max timeout from `getDefault/MaxBashTimeoutMs`; sandbox via `dangerouslyDisableSandbox`; subcommand-level permission parsing (`prompt.ts`, `bashPermissions.ts`). Prompt steers AWAY from `cat/sed/awk/grep/find` toward dedicated tools (`BashTool/prompt.ts:280-291`). |
| **Read** (`FileReadTool`) | read file/image | `file_path`, `offset?`, `limit?` | `maxResultSizeChars: Infinity` — never persisted (would create Read→file→Read loop, `FileReadTool.ts:340-342`); self-bounds at `maxTokens: 25000` and throws if exceeded, telling model to use offset/limit (`limits.ts:7`, `FileReadTool.ts:178-181`); `isReadOnly()→true`, `isConcurrencySafe()→true` (`FileReadTool.ts:373-376`). Output is `cat -n` line-numbered. |
| **Edit** (`FileEditTool`) | exact string replace | `file_path`, `old_string`, `new_string`, `replace_all?` | `strict: true`, `maxResultSizeChars: 100_000` (`FileEditTool.ts:89-90`); **MUST Read the file first** or errors (`FileEditTool/prompt.ts:4`); fails if `old_string` not unique; `backfillObservableInput` expands `file_path` so hook allowlists can't be bypassed via `~`/relative (`FileEditTool.ts:115-121`); `checkPermissions` → `checkWritePermissionForTool`. |
| **Write** (`FileWriteTool`) | create/overwrite file | `file_path`, `content` | write-permission-gated; "ALWAYS prefer editing existing files." |
| **Glob** | filename pattern search | `pattern`, `path?` | read-only, concurrency-safe; **omitted when bfs/ugrep are embedded** in the binary (`tools.ts:201`). |
| **Grep** | content search (ripgrep) | `pattern`, `path?`, `glob?`, `output_mode?`… | same embed gating. |
| **TodoWrite** | structured task list | `todos[]` (`content`+`activeForm`+`status`) | result rendered to a side panel, not transcript (`Tool.ts:561-564`); steers "exactly ONE `in_progress`" (`TodoWriteTool/prompt.ts`). |
| **Task / Agent** (`AgentTool`) | spawn a subagent | `description`, `prompt`, `subagent_type` | recursively runs `query()` in a child context; subagents have a restricted tool allowlist (`constants/tools.ts` `ASYNC_AGENT_ALLOWED_TOOLS`/`ALL_AGENT_DISALLOWED_TOOLS`). |
| **WebFetch**, **WebSearch** | network | url/query | open-world; permission-gated. |
| **NotebookEdit** | edit Jupyter cells | `notebook_path`, `cell_*` | file-write semantics. |
| **EnterPlanMode / ExitPlanMode** | plan-mode transitions | (ExitPlanMode takes NO plan param — reads from plan file) | see §4. |
| **AskUserQuestion** | structured user question | choices | `requiresUserInteraction()→true` (always prompts even in bypass). |
| **ToolSearch** | load deferred tool schemas | `query` (`select:Name` or keywords) | present only when tool-search is enabled (`tools.ts:249`). |

Conditional / flag-gated tools (ant-only or feature-flagged, lower copy priority):
SkillTool, ConfigTool, TungstenTool, REPLTool, LSP, EnterWorktree/ExitWorktree,
SendMessage/TeamCreate/TeamDelete (swarms), Sleep, Cron\*, RemoteTrigger, Monitor,
PushNotification, PowerShell, Snip, MCP resource tools (`tools.ts:25-249`).

**Tool assembly pipeline** (CONFIRMED `tools.ts:271-367`): `getAllBaseTools()` →
`getTools(permCtx)` filters by deny rules (`filterToolsByDenyRules`, `tools.ts:262-269`)
and `isEnabled()` → `assembleToolPool()`/`getMergedTools()` merge MCP tools,
dedup by name (built-ins win), and **sort each partition for prompt-cache
stability** so MCP tools never interleave into the cached built-in prefix
(`tools.ts:345-367`). `CLAUDE_CODE_SIMPLE` collapses to just Bash/Read/Edit
(`tools.ts:273-298`).

---

## 2. Permission model

### 2.1 Modes (CONFIRMED — `src/types/permissions.ts:16-38`, `PermissionMode.ts`)

External (user-addressable) modes: `default`, `acceptEdits`, `bypassPermissions`,
`plan`, `dontAsk`. Internal adds `auto` (classifier-gated, ant/flag) and `bubble`
(`types/permissions.ts:27-35`). Behaviors are a 3-value enum: `allow | deny | ask`
(plus an internal `passthrough` meaning "no objection, defer") (`types/permissions.ts:44`).

| Mode | Effect (CONFIRMED in `permissions.ts`) |
|---|---|
| `default` | rules + tool `checkPermissions`; unresolved `passthrough` → `ask` (prompt user) (`permissions.ts:1299-1310`). |
| `acceptEdits` | file writes inside cwd auto-allow (`filesystem.ts:1360-1366`); other risky tools still ask. |
| `bypassPermissions` | allow everything EXCEPT deny rules (1d), content-specific ask rules (1f), and safety-checks (1g) which stay bypass-immune (`permissions.ts:1262-1281`). |
| `plan` | read/explore only; write tools resolve to `ask` so the model can't edit until plan is approved (see §4). |
| `dontAsk` | converts every `ask` → `deny` (`permissions.ts:505-517`). |
| `auto` | instead of prompting the user, runs an **AI YOLO classifier** (`classifyYoloAction`) to allow/deny, with acceptEdits fast-path + safe-tool allowlist short-circuits, denial tracking, and fail-closed/open on classifier unavailability (`permissions.ts:518-927`). INFERRED detail: classifier prompt content is server-side. |

### 2.2 The permission pipeline (CONFIRMED — `permissions.ts` `hasPermissionsToUseToolInner` `:1158-1319`)

Ordered, fail-closed steps:

1. **1a deny rule** for whole tool → `deny` (`:1171-1181`).
2. **1b ask rule** for whole tool → `ask` (unless sandbox auto-allow) (`:1184-1206`).
3. **1c** call `tool.checkPermissions(input)` → tool-specific decision (`:1208-1223`).
4. **1d** tool said `deny` → `deny` (`:1226-1228`).
5. **1e** `tool.requiresUserInteraction()` + ask → keep `ask` even in bypass (`:1231-1236`).
6. **1f** content-specific ask rule (e.g. `Bash(npm publish:*)`) → `ask`, bypass-immune (`:1244-1250`).
7. **1g** safetyCheck paths (`.git/`, `.claude/`, shell configs) → `ask`, bypass-immune (`:1255-1260`).
8. **2a** if mode is bypass (or plan-started-from-bypass) → `allow` (`:1268-1281`).
9. **2b** explicit always-allow rule → `allow` (`:1284-1297`).
10. **3** `passthrough` → `ask` with a generated request message (`:1299-1310`).

Rules carry a **source** (`userSettings`/`projectSettings`/`localSettings`/`cliArg`/
`session`/`policySettings`/…, `permissions.ts:109-114`) and remembering "always
allow" persists a rule to the chosen source via `persistPermissionUpdates`
(`permissions.ts:425-433`). Rule grammar: `Bash`, `Bash(git *)`, `mcp__server`,
`mcp__server__*`, `Agent(Explore)` (`permissions.ts:238-342`).

### 2.3 How a denial reaches the model (CONFIRMED — `toolExecution.ts:995-1103`)

When `permissionDecision.behavior !== 'allow'`, CC pushes a `tool_result` with
`is_error: true` and the human-readable denial message
(`createPermissionRequestMessage`, `permissions.ts:137-211`) as the content
(`toolExecution.ts:1030-1071`). The tool **never executes**. In `auto` mode a
denied action can fire `PermissionDenied` hooks; if a hook says retry, an
`isMeta` user message tells the model it may retry (`toolExecution.ts:1075-1100`).
Input-validation and unknown-tool failures produce analogous `<tool_use_error>`
results (`toolExecution.ts:396-409,664-679`).

### 2.4 `canUseTool` + the headless control protocol (CONFIRMED — `cli/structuredIO.ts`)

`canUseTool` is the injectable async fn `(tool,input,ctx,assistantMsg,toolUseID,
forceDecision?) → Promise<PermissionDecision>` (`Tool.ts:12`, signature at
`structuredIO.ts:534-541`). Two implementations:

- **Interactive REPL:** `hasPermissionsToUseTool` runs the pipeline; on `ask` it
  suspends the turn by `await`-ing a Promise resolved by dialog buttons (doc 00
  theme #7; doc 07 "promise-bridged permission gate").
- **Headless / stream-json:** `createCanUseTool` (`structuredIO.ts:531+`) runs the
  pipeline first; if the result is `allow`/`deny` it returns immediately; on `ask`
  it emits a `control_request` of subtype **`can_use_tool`** to stdout
  (`structuredIO.ts:589-604`) carrying `{tool_name, input, permission_suggestions,
  blocked_path, decision_reason, tool_use_id, agent_id}`, then awaits a matching
  **`control_response`** on stdin (`structuredIO.ts:362-405`). A racing
  `PermissionRequest` hook can resolve first; the loser is cancelled via
  `control_cancel_request` (`structuredIO.ts:556-637`). Duplicate/late responses
  are deduped by tool_use_id (`structuredIO.ts:149-405`).

This `control_request`/`control_response` FIFO is exactly the SDK-host permission
protocol (VS Code, claude.ai bridge) and is the cleanest thing to copy for Magi's
headless mode.

---

## 3. The agent loop

### 3.1 Shape (CONFIRMED — `src/query.ts` `query()` → `queryLoop()`, `:219-1729`)

`query()` is an `async function*` yielding a union of
`StreamEvent | RequestStartEvent | Message | TombstoneMessage | ToolUseSummaryMessage`
and returning a `Terminal` `{reason}` (`query.ts:219-228`). The engine boundary
both the REPL and headless paths funnel into
(`docs/architecture/claude-code-cli/00-overview.md:44-48`).

The loop is a `while (true)` over **turns** (`query.ts:307`). Per iteration:

1. **Pre-flight context management** (all before the API call): tool-result budget
   (`applyToolResultBudget`), history-snip, **microcompact**, context-collapse,
   **autocompact** — each can rewrite `messagesForQuery` and yield boundary
   messages (`query.ts:365-543`). Hard blocking-limit guard if auto-compact is off
   (`query.ts:628-648`).
2. **Stream the model** via `deps.callModel({messages, systemPrompt, tools, thinking,
   signal, options:{getToolPermissionContext, model, …}})` (`query.ts:659-708`).
   For each streamed `assistant` message: yield it (unless a recoverable error is
   *withheld* for retry, `query.ts:799-825`), push to `assistantMessages`, extract
   `tool_use` blocks, and set `needsFollowUp = true` (`query.ts:826-845`). The
   **sole loop-continue signal is "a tool_use block arrived"** — `stop_reason` is
   explicitly treated as unreliable (`query.ts:551-558`).
3. **Optional streaming tool execution:** when the `streamingToolExecution` gate is
   on, a `StreamingToolExecutor` starts tools *as their blocks arrive* and yields
   completed results mid-stream (`query.ts:561-568,836-862`). Otherwise tools run
   after the full assistant message.
4. **No tool_use → terminate** (`query.ts:1062`): run stop-hooks
   (`handleStopHooks`), token-budget check, and `return {reason:'completed'}`
   (or a recovery branch, see §3.3).
5. **Tool execution:** `runTools(toolUseBlocks, …)` (non-streaming) or
   `streamingToolExecutor.getRemainingResults()` (`query.ts:1380-1408`). Each
   yielded `update.message` is a `tool_result` user message that's both yielded to
   the consumer AND normalized into `toolResults` for the next API call.
6. **Post-tool:** attachments, queued-command drain, MCP tool refresh between turns
   (`refreshTools`, `query.ts:1660-1671`), optional async Haiku **tool-use summary**
   for the *next* turn (`generateToolUseSummary`, `query.ts:1412-1482`), maxTurns
   guard (`query.ts:1704-1712`).
7. **Recurse:** build next `State` with `messages = [...prev, ...assistant,
   ...toolResults]` and `continue` (`query.ts:1715-1727`).

So the canonical cycle is: **model → tool_use → (permission → execute) →
tool_result → model → …** until the model emits no tool_use.

### 3.2 Tool orchestration & concurrency (CONFIRMED — `services/tools/toolOrchestration.ts`)

`runTools` **partitions** consecutive tool calls into batches:
concurrency-safe (read-only) tools run **concurrently** (bounded by
`CLAUDE_CODE_MAX_TOOL_USE_CONCURRENCY`, default **10**), everything else runs
**serially** (`toolOrchestration.ts:8-116`). Partition rule: a tool joins the
prior batch only if both it and the batch are concurrency-safe; non-safe tools get
their own singleton batch (`:91-116`). `isConcurrencySafe` throwing → treated as
unsafe (fail-closed, `:99-107`). Concurrent batches accumulate `contextModifier`s
and apply them only after the batch completes (`:42-63`); serial tools apply
context changes immediately (`:140-141`).

Per-tool flow `runToolUse` → `checkPermissionsAndCallTool`
(`toolExecution.ts:337-490,599+`):
Zod schema parse → `validateInput` → speculative bash classifier → **PreToolUse
hooks** → `resolveHookPermissionDecision` (which calls `canUseTool`) → if not
`allow` emit denial result → else `tool.call()` → **PostToolUse hooks** → map to
`tool_result` block (auto-persist if over `maxResultSizeChars`). Progress events
are bridged into one async iterable via a `Stream` (`toolExecution.ts:492-570`).

### 3.3 Turn termination & recovery (CONFIRMED — `query.ts`)

Terminal reasons: `completed`, `aborted_streaming`, `aborted_tools`,
`max_turns`, `blocking_limit`, `prompt_too_long`, `image_error`, `model_error`,
`stop_hook_prevented`, `hook_stopped`. Recovery branches (each rewrites `State`
and `continue`s): model-fallback on `FallbackTriggeredError` (`:893-951`),
prompt-too-long → collapse-drain then reactive-compact (`:1085-1183`),
`max_output_tokens` → escalate cap 8k→64k then multi-turn "resume mid-thought"
nudge ×3 (`:1188-1256`), stop-hook blocking errors re-injected (`:1282-1306`),
token-budget continuation nudge (`:1308-1355`). On abort, the streaming executor
synthesizes `tool_result` blocks for in-flight tools so no `tool_use` is left
without a matching result (`:1015-1029,1484-1516`).

### 3.4 Compaction hooks

Compaction is woven into the loop pre-API (`microcompact`/`autocompact`/
`contextCollapse`/`snip`, `query.ts:401-543`) plus reactive (post-413) compaction
(`:1119-1166`). `PreCompact`/`PostCompact`/`SessionStart` hook events exist
(`CompactProgressEvent`, `Tool.ts:150-156`). **INFERRED:** the compaction summary
prompt itself is a forked `query()` with `querySource:'compact'` and lives partly
server-side.

---

## 4. System prompt + todo/plan mechanics

### 4.1 How tools are described to the model (CONFIRMED)

Tool schemas are sent as Anthropic `tools[]` (`deps.callModel({…tools})`,
`query.ts:662`); each tool's natural-language description comes from
`tool.prompt()` (`Tool.ts:518`). Descriptions are **rich, example-laden, and
opinionated** — e.g. Bash's prompt embeds the full git-commit/PR procedure and a
"prefer dedicated tools over cat/sed/grep" table (`BashTool/prompt.ts:81-369`);
Edit's prompt mandates Read-before-edit and uniqueness rules
(`FileEditTool/prompt.ts`); TodoWrite's prompt is ~150 lines of when-to/when-not-to
examples (`TodoWriteTool/prompt.ts`).

The default system prompt (sections in `constants/prompts.ts`) carries durable
agent behavior: `# Doing tasks` (YAGNI/minimal-complexity, read-before-modify,
"don't create files unless necessary", diagnose-before-retry, false-claims
mitigation — `prompts.ts:200-252`) and `# Executing actions with care`
(reversibility/blast-radius, confirm risky actions — `prompts.ts:254+`). The
effective prompt is layered: override > coordinator > agent > custom > default,
with `appendSystemPrompt` always last (`utils/systemPrompt.ts:30-58`). Tool
ordering is kept stable for prompt caching (`tools.ts:345-367`).

### 4.2 TodoWrite mechanics (CONFIRMED — `TodoWriteTool/prompt.ts`)

Steers the model to maintain a structured list whenever a task is ≥3 steps; each
todo has `content` (imperative), `activeForm` (present-continuous), and `status`
(`pending`/`in_progress`/`completed`) with **exactly one `in_progress`** at a time
and "mark complete immediately, never batch." The tool's result renders to a side
panel, not the transcript (`renderToolResultMessage` omitted → "surfaced
elsewhere", `Tool.ts:561-564`).

### 4.3 Plan mode (CONFIRMED — `EnterPlanModeTool`/`ExitPlanModeV2Tool` prompts + `permissions.ts`/`filesystem.ts`)

- **Enter:** `EnterPlanMode` transitions to `plan` mode "to explore the codebase
  and design an implementation approach for user approval"; prompt enumerates
  when to use it (architectural ambiguity, multi-file, unclear requirements) vs
  not (typos, clear single-function changes) (`EnterPlanModeTool/prompt.ts`).
- **Restriction mechanism:** plan mode doesn't strip write tools from the schema;
  instead write-permission checks resolve to `ask`/blocked in plan mode
  (`filesystem.ts` write path checks `mode`, and `currentModel` even routes plan
  mode to a different model when >200k tokens, `query.ts:572-578`). Plan/scratchpad
  files for the current session ARE writable so the model can save its plan
  (`filesystem.ts:1241,1494`). So the model can read/explore freely but is
  effectively blocked from mutating the repo until the plan is approved.
- **Exit:** `ExitPlanMode` takes **no plan argument** — it reads the plan the model
  already wrote to the session plan file and signals "ready for user approval"
  (`ExitPlanModeTool/prompt.ts`). The prompt is explicit it's only for
  implementation planning, not research, and that ExitPlanMode *is* the approval
  request (don't AskUserQuestion "is this plan ok?").

---

## 5. Tool-result rendering (connects to docs 05/07)

CONFIRMED via the `Tool` render contract (`Tool.ts:566-694`) and
`docs/architecture/claude-code-cli/07-message-diff-display-components.md`
(esp. §1.1 dispatcher, §2.1 per-tool render contract `:153-189`, §2.2–2.4 diff
pipeline `:192-256`):

- The transcript is a message list; a dispatcher (`Message.tsx`, doc 07 §1.2)
  routes each block. `tool_use` always precedes its `tool_result` in the array
  (doc 07 `:80`), letting the renderer pair them.
- Each tool owns its rendering: `renderToolUseMessage` (the call, possibly with
  partial streamed input), `renderToolResultMessage?` (the body; **omit → renders
  nothing**, used by TodoWrite which updates a panel, `Tool.ts:561-564`),
  `renderToolUseRejectedMessage?` (custom rejection UI, e.g. show the rejected
  diff), `renderToolUseErrorMessage?`, `renderToolUseProgressMessage?`,
  `renderGroupedToolUse?` (collapse N parallel calls). Edit/Write show a diff,
  Bash shows command + collapsible output, Read shows a file snippet
  (doc 07 §2.1 `:189`).
- Diffs: `getPatchForDisplay` → structured line patch (`diff` npm, context=3,
  5s timeout) → `StructuredDiff` → **Rust NAPI `color-diff-napi`** (syntect/bat +
  `similar` word-diff) with a pure-TS fallback; a whole hunk renders as ONE
  `RawAnsi` Yoga leaf for an "O(1) leaves per diff" invariant
  (doc 07 §2.2–2.4 `:192-256`).
- `isSearchOrReadCommand`/`isReadOnly`/`getActivityDescription`/`getToolUseSummary`
  drive condensed/collapsed display and spinner activity text
  (`Tool.ts:429-548`).
- Headless: results are projected as NDJSON `tool_result` messages on the same
  outbound FIFO (doc 02), not rendered.

---

## 6. CONFIRMED vs INFERRED summary

| Area | Status |
|---|---|
| Tool interface, defaults, registry, assembly | CONFIRMED (`Tool.ts`, `tools.ts`) |
| Per-tool params/limits (Bash 30k, Read ∞/25k tok, Edit 100k+read-first) | CONFIRMED |
| Permission pipeline ordering, modes, denial→model, rule grammar/sources | CONFIRMED (`permissions.ts`, `types/permissions.ts`) |
| `control_request`/`control_response` headless permission protocol | CONFIRMED (`structuredIO.ts`) |
| Agent loop shape, tool-use as continue-signal, concurrency partition | CONFIRMED (`query.ts`, `toolOrchestration.ts`) |
| Recovery branches (fallback/compact/max-tokens/stop-hook/budget) | CONFIRMED (`query.ts`) |
| TodoWrite / plan-mode steering + write-blocking | CONFIRMED (prompts + `filesystem.ts`) |
| Tool-result/diff rendering | CONFIRMED (`Tool.ts` + doc 07) |
| Auto-mode YOLO classifier prompt content; compaction summary prompt | INFERRED (server-side / ant-gated) |
| Exact maxTurns / streaming-executor gate defaults in prod | INFERRED (runtime flags) |

---

## 7. Patterns worth copying into Magi

Concrete, evidence-tied recommendations (Magi today: chat-only, no tool loop):

1. **A uniform `Tool` protocol with fail-closed defaults.** Port `buildTool` +
   the member set (`name`, `input_schema`, `prompt()`, `validate_input`,
   `check_permissions`, `call()`, `is_read_only`, `is_concurrency_safe`,
   `is_destructive`, `max_result_size_chars`, render hooks). Defaults must be
   `is_read_only=False`, `is_concurrency_safe=False`, `check_permissions=allow`
   (`Tool.ts:757-769`). This is the single biggest structural lift and everything
   else hangs off it.

2. **The async-generator agent loop with "tool_use is the only continue signal."**
   Build `query()` as a Python async generator: model-stream → collect `tool_use`
   blocks → if none, run stop-hooks and terminate (`reason`), else execute and
   recurse with `messages = prev + assistant + tool_results`
   (`query.ts:1062,1380-1408,1715-1727`). Do NOT trust `stop_reason`
   (`query.ts:551-558`). Magi already has the `RunnerSessionBoundary`/`run_turn`
   boundary (doc 00 `:87`) — make this loop the producer behind it.

3. **An ordered, fail-closed permission pipeline + injectable `can_use_tool`.**
   Copy the 1a–3 ordering (deny → ask-rule → tool check → bypass-immune
   safetyChecks → mode → allow-rule → passthrough→ask, `permissions.ts:1158-1319`),
   with rule sources/persistence and the four+ modes (default/acceptEdits/
   bypass/plan/dontAsk). Surface a denial to the model as an `is_error` tool_result
   (`toolExecution.ts:1030-1071`) so the model can adapt rather than crash.

4. **The `control_request`/`control_response` headless permission protocol.**
   For Magi's headless/NDJSON mode, emit a `can_use_tool` control_request on the
   outbound FIFO and await a matching control_response, deduped by tool_use_id,
   with cancel support (`structuredIO.ts:362-637`). This makes Magi drivable as a
   subprocess by an SDK/host (mirrors CC's VS Code/claude.ai bridge) and cleanly
   separates "the loop" from "who approves."

5. **Concurrency partitioning + result budgeting.** Run consecutive read-only
   tools concurrently (cap ~10) and everything else serially
   (`toolOrchestration.ts:8-116`); give each tool a `max_result_size_chars` and
   auto-persist oversized results to disk with a preview path
   (`Tool.ts:457-466`), with Read pinned to "never persist." Cheap wins that keep
   the loop fast and the context small.

Bonus (high ROI, low cost): adopt CC's **opinionated tool prompts** (Read-before-Edit,
prefer-dedicated-tools-over-bash, YAGNI `# Doing tasks` section) and **TodoWrite +
plan-mode** steering — these are pure prompt/text and drive most of the "feels like
a real coding agent" behavior (`BashTool/prompt.ts`, `FileEditTool/prompt.ts`,
`TodoWriteTool/prompt.ts`, `constants/prompts.ts:200-252`).
