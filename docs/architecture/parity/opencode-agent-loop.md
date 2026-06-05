# OpenCode Agentic Execution Layer — Code-Level Deep Dive

**Purpose.** Extract the concrete mechanisms OpenCode uses for its tool/agent-loop
layer so we can build the equivalent in Magi's `magi` CLI (which today only does
chat — no real tools, no agentic tool-execution loop).

**Source root.** All paths below are relative to
`/Users/kevin/Desktop/claude_code/cc-workspace/opencode/packages/opencode/src`
unless noted. OpenCode is written in TypeScript on top of **Effect-TS** (an
algebraic-effects runtime) and the **Vercel AI SDK** (`ai` package). The Effect
machinery is mostly incidental ceremony — the *patterns* translate cleanly to
Python.

> Verification note: every non-obvious claim cites `file:line`. Where a behavior
> spans the AI SDK, that is called out explicitly because we cannot copy the AI
> SDK verbatim — we must re-implement its loop semantics ourselves.

---

## 1. Tool system / registry

### 1.1 The tool contract (`Tool.Def`)

`tool/tool.ts:53-63` defines the canonical tool interface:

```ts
export interface Def<Parameters, M extends Metadata> {
  id: string                         // tool name the model sees
  description: string                // model-facing description
  parameters: Parameters             // Effect Schema (decoder) for args
  jsonSchema?: JSONSchema7           // explicit JSON Schema override (optional)
  execute(args, ctx: Context): Effect<ExecuteResult<M>>
  formatValidationError?(error): string
}
```

The **execution context** passed to every tool (`tool/tool.ts:34-44`):

```ts
export type Context<M> = {
  sessionID, messageID, agent, abort: AbortSignal, callID?, extra?,
  messages: MessageV2.WithParts[],
  metadata(input): Effect<void>      // stream live status/title back to UI
  ask(input): Effect<void>           // request permission (throws if denied)
}
```

The **result shape** (`tool/tool.ts:46-51`):

```ts
export interface ExecuteResult<M> {
  title: string                      // short label for the UI row
  metadata: M                        // structured data (diff, diagnostics…)
  output: string                     // the text the MODEL sees as tool result
  attachments?: FilePart[]           // images/PDFs returned as file parts
}
```

Key design points:

- **Args are validated by a typed schema, and a validation failure is a
  *model-facing* error, not a crash.** `InvalidArgumentsError`
  (`tool/tool.ts:22-32`) produces prose: *"The X tool was called with invalid
  arguments: … Please rewrite the input so it satisfies the expected schema."*
  This is fed back to the model as the tool result so it self-corrects.
- **Every tool's output is auto-truncated** unless the tool already set
  `metadata.truncated` (`tool/tool.ts:128-142`). The wrapper decodes args, runs
  `execute`, then runs the result through `Truncate.output` (see §2.7).
- **The wrapper is built once per tool init** (`tool/tool.ts:97-147`); the schema
  decoder closure is hoisted (`tool/tool.ts:109`) so it is not re-allocated per
  call.

### 1.2 The registry (`tool/registry.ts`)

`ToolRegistry` (`tool/registry.ts:73-80`) exposes:
- `ids()` / `all()` — list every tool
- `tools({providerID, modelID, agent})` — **filtered, per-request** tool set

The builtin list is assembled at `tool/registry.ts:248-266`. Built-ins (in
registration order): `invalid`, `question` (gated), `shell` (=bash), `read`,
`glob`, `grep`, `edit`, `write`, `task`, `fetch` (webfetch), `todo`
(=`todowrite`), `search` (websearch), `repo_clone`/`repo_overview` (experimental
"scout"), `skill`, `patch` (=`apply_patch`), `lsp` (experimental), `plan`
(experimental). **Custom tools** come from (a) plugin modules and (b) files
matching `{tool,tools}/*.{js,ts}` in config dirs (`tool/registry.ts:199-220`).

**Per-request model-aware tool filtering** (`tool/registry.ts:316-328`) — the
single most important registry behavior to copy:
- WebSearch only enabled for specific providers/flags.
- **GPT (non-oss, non-gpt-4) models get `apply_patch` instead of `edit`/`write`**;
  every other model gets `edit`/`write` and *not* `apply_patch`. The model's own
  family dictates which file-edit primitive is exposed.

The registry also **augments descriptions dynamically per request**
(`tool/registry.ts:344-357`): the `task` tool's description gets the live list of
available sub-agents appended (`describeTask`, `:301-314`), and the `skill`
tool's description gets the live list of available skills appended
(`describeSkill`, `:282-299`). Plugins can rewrite any tool definition via the
`tool.definition` hook (`tool/registry.ts:339`).

### 1.3 Built-in tool catalog (every tool + key params)

| Tool id | Purpose | Key params | Source |
|---|---|---|---|
| `read` | Read a file or directory; returns numbered lines; handles images/PDFs as attachments | `filePath`, `offset?`, `limit?` | `tool/read.ts:29-37` |
| `write` | Overwrite/create a file (whole-file) | `content`, `filePath` | `tool/write.ts:20-25` |
| `edit` | Exact string replacement w/ fuzzy fallbacks | `filePath`, `oldString`, `newString`, `replaceAll?` | `tool/edit.ts:47-56` |
| `apply_patch` | Multi-file V4A patch (GPT models only) | `patchText` | `tool/apply_patch.ts:18-20` |
| `bash`/`shell` | Run a shell command (parsed for permission scan) | `command`, `description`, `workdir?`, `timeout?` | `tool/shell/prompt.ts` (Parameters) |
| `grep` | ripgrep content search by regex | `pattern`, `path?`, `include?` | `tool/grep.ts:14-22` |
| `glob` | ripgrep filename search by glob | `pattern`, `path?` | `tool/glob.ts:12-17` |
| `webfetch` | Fetch URL → markdown/text/html | `url`, `format?`, `timeout?` | `tool/webfetch.ts:13-22` |
| `websearch` | Web search (provider/flag gated) | (provider-specific) | `tool/websearch.ts` |
| `todowrite` | Maintain structured task list | `todos[]` `{content,status,priority}` | `tool/todo.ts:9-19` |
| `task` | Launch a sub-agent (foreground/background, resumable) | `description`, `prompt`, `subagent_type`, `task_id?`, `background?` | `tool/task.ts:34-52` |
| `skill` | Load a skill's instructions into context | (skill name) | `tool/skill.ts` |
| `question` | Ask the user a structured question | — | `tool/question.ts` |
| `plan` | Suggest entering/exiting plan mode | — | `tool/plan.ts` |
| `lsp` | Query LSP (experimental) | — | `tool/lsp.ts` |
| `invalid` | Catch-all for repaired/broken tool calls | — | `tool/invalid.ts` |

`read`/`grep`/`glob` defaults to absolute or instance-`directory`-relative
resolution; all three call `ctx.ask(...)` (permission) before touching the FS.

---

## 2. Core tool implementations (read / write / edit / bash / grep / glob)

### 2.1 `read` (`tool/read.ts`)

- **Limits/constants** (`:14-20`): default 2000 lines, **max 2000 chars/line**
  (truncated with a suffix), **50 KB byte cap**, 4 KB sniff sample.
- **Path resolution** (`:206-214`): relative → resolved against
  `instance.directory`; Windows normalized.
- **Permission** (`:227-232`): asks `permission:"read"` with the worktree-relative
  path as the pattern, `always:["*"]` (so "always allow" whitelists all reads).
- **Missing-file affordance** (`:48-71`): on not-found, lists ≤3 fuzzy-similar
  filenames in the dir → *"Did you mean one of these?"*.
- **Directory read** (`:236-262`): lists entries (dirs get trailing `/`), paginated
  by offset/limit, wrapped in `<path>/<type>/<entries>` tags.
- **Binary detection** (`:153-198`): extension blacklist + null-byte / >30%
  non-printable heuristic on the 4 KB sample → refuses with *"Cannot read binary
  file"*.
- **Image/PDF** (`:270-289`): returns the bytes as a base64 `data:` URL
  *attachment* with a one-line text output, not inline text.
- **Streaming line reader** (`:108-151`): streams bytes, splits lines, enforces
  the byte cap mid-stream with a tagged `ReadStop` error to abort the file stream
  early. Output is line-number-prefixed (`${i+offset}: ${line}`, `:303`) and ends
  with an explicit continuation hint: *"(Output capped at 50 KB … Use offset=N to
  continue.)"* (`:308-314`).
- **Side effects**: warms the LSP for the file (`:89-91`, `:317`); appends any
  pending instruction reminders as a `<system-reminder>` block (`:319-321`).

### 2.2 `write` (`tool/write.ts`)

- Whole-file overwrite/create. Reads existing content to **compute a unified
  diff** (`createTwoFilesPatch` + `trimDiff`, `:53`) and passes that diff in the
  permission `metadata` so the UI can show what will change *before* approval
  (`:54-62`).
- Preserves BOM (`util/bom`, `:47-64`).
- After writing: runs the project formatter (`format.file`, `:65`), publishes
  `File.Edited` + `FileWatcher.Updated` bus events, then **touches the LSP and
  surfaces diagnostics** — *"LSP errors detected in this file, please fix:"* — for
  the current file and up to 5 other files (`:75-90`). This is a tight
  edit→typecheck→feedback loop entirely inside the tool result.

### 2.3 `edit` (`tool/edit.ts`) — the fuzzy-match strategy worth studying

The model passes `oldString`/`newString`. Matching is done by `replace()`
(`:674-711`), which tries a **prioritized cascade of replacers** until one finds
a unique match (`:681-691`):

1. `SimpleReplacer` — exact substring.
2. `LineTrimmedReplacer` — line-by-line ignoring leading/trailing whitespace.
3. `BlockAnchorReplacer` — for ≥3-line blocks, anchor on first+last line and
   Levenshtein-score the middle (`:284-417`); single-candidate uses a relaxed
   threshold, multi-candidate picks the best above 0.3.
4. `WhitespaceNormalizedReplacer` — collapse all whitespace runs.
5. `IndentationFlexibleReplacer` — strip common indentation.
6. `EscapeNormalizedReplacer` — unescape `\n \t \"` etc.
7. `TrimmedBoundaryReplacer`.
8. `ContextAwareReplacer` — anchor + ≥50% middle-line match.
9. `MultiOccurrenceReplacer` — yields every exact match (for `replaceAll`).

**Uniqueness enforcement** (`:692-710`): for non-`replaceAll`, the candidate is
accepted only if `indexOf === lastIndexOf` (exactly one occurrence); otherwise it
errors *"Found multiple matches… Provide more surrounding context."* If nothing
matched at all: *"Could not find oldString… It must match exactly, including
whitespace, indentation, and line endings."*

Other notable behavior:
- `oldString === ""` is a **create-file** path (`:90-117`).
- Line endings are detected and preserved (`:22-33`, `:125-127`).
- **Per-file mutex** via a `Semaphore` map keyed by resolved path
  (`:35-45`, `:88`) prevents concurrent edits corrupting a file.
- Same diff-in-permission-metadata + format + LSP-diagnostics feedback as `write`.
- `replace()` and the replacers are **exported** (`:213`, `:240`…) and reused by
  `write`/`apply_patch` (`trimDiff`).

### 2.4 `bash`/`shell` (`tool/shell.ts`) — the most security-sensitive tool

- **Default timeout** 2 min (`:343`, overridable by flag); negative timeout
  rejected (`:616-618`); kill with 3 s grace on abort/timeout (`:548-555`).
- **Tree-sitter command parsing** (`:307-332`, `:623-626`): the command string is
  parsed (bash or PowerShell grammar) into an AST. From the AST it extracts:
  - **filesystem-touching commands** (`rm cp mv mkdir touch chmod cat …`,
    `:30-65`) and resolves their path arguments to detect **writes outside the
    project root** → asks `permission:"external_directory"` (`:266-287`,
    `:393-401`).
  - **a per-command permission pattern**: the literal command text plus a
    *prefix-glob* (`BashArity.prefix(tokens).join(" ") + " *"`, `:403-406`) so
    "always allow `git status *`" works without re-prompting on every variant.
- **Streaming output with a rolling byte budget** (`:479-559`): output is streamed
  to the UI via `ctx.metadata` deltas; once it exceeds `maxBytes` the full output
  is spilled to a temp file and only a tail preview is kept
  (`:498-521`, `tail()` `:228-258`). The model result includes
  *"...output truncated... Full output saved to: <file>"* (`:578-580`).
- **Timeout/abort markers** are appended in a `<shell_metadata>` block telling the
  model it can retry with a larger timeout (`:561-583`).
- `stdin: "ignore"` always (`:299-305`) — no interactive hangs.

### 2.5 `grep` (`tool/grep.ts`)

- Delegates to a `Ripgrep` service (`:72-78`) with `cwd`, `pattern`,
  `glob` (= `include`), and the abort signal. Single-file vs directory search is
  derived from `stat` (`:67-71`).
- **Results sorted by mtime** (newest first, `:111`), capped at **100 matches**
  (`:113-116`), grouped by file path, each match printed as
  `Line N: <text>` with per-line cap of 2000 chars (`:128-130`).
- Emits a truncation hint when capped (`:133-138`) and a *"some paths were
  inaccessible"* note for partial results (`:140-143`).
- Asks `permission:"grep"` first (`:45-54`).

### 2.6 `glob` (`tool/glob.ts`)

- Uses the same `Ripgrep` service's `files()` stream (`:56-72`), takes
  `limit+1` to detect truncation, caps at **100 files**, sorts by mtime
  (`:74-78`). Errors if the path is a file not a directory (`:46-48`). Asks
  `permission:"glob"`.

### 2.7 Output truncation (`tool/truncate.ts`) — shared safety net

- Defaults: **2000 lines / 50 KB** (`:16-17`), overridable via config
  `tool_output.max_lines/max_bytes` (`:76-84`).
- When output exceeds the limit it **spills the full text to a `tool_*` temp
  file** and returns a preview + a hint (`:86-142`). The hint *adapts to whether
  the agent has the `task` tool*: if so it tells the model to delegate processing
  of the saved file to a sub-agent to save context (`:130-132`).
- Temp files auto-expire after 7 days (`:55-67`, `:144-152`).

---

## 3. The agent loop

There are **two nested loops**: an outer "step" loop (`session/prompt.ts`) and an
inner per-turn stream processor (`session/processor.ts`). The actual
model↔tool↔model cycling is owned by the **AI SDK's `streamText`**
(`session/llm.ts:272`), which OpenCode wraps.

### 3.1 Inner loop — stream processor (`session/processor.ts`)

`processor.create()` returns a `Handle` with `process(streamInput)` (`:37-53`).
`process` (`:780-849`):

1. Sets session status `busy`, opens `llm.stream(streamInput)` (`:789-790`).
2. **Drives the LLM event stream** (`:792-796`): each event goes to
   `handleEvent`; the stream is consumed until completion *or* until
   `needsCompaction` is set (context overflow).
3. `handleEvent` (`:305-689`) is a big switch over the normalized event types:
   - `reasoning-start/delta/end` → live reasoning parts.
   - `tool-input-start` / `tool-input-end` → create a pending tool part
     (`ensureToolCall`, `:231-278`).
   - `tool-call` → mark the part `running` with parsed input (`:377-450`);
     **doom-loop guard** fires here.
   - `tool-result` → normalize attachments, mark part `completed`
     (`completeToolCall`, `:452-502`, `:168-192`).
   - `tool-error` → mark part `error` (`failToolCall`, `:504-523`, `:194-211`).
   - `text-start/delta/end` → stream assistant text parts (`:619-684`).
   - `step-start` / `step-finish` → snapshot the worktree, accumulate
     usage/cost, write a `step-finish` part, fork a background summary, and set
     `needsCompaction` if the context overflowed (`:528-617`).
4. On finish, returns one of **`"compact" | "stop" | "continue"`** (`:845-847`):
   - `compact` if `needsCompaction`,
   - `stop` if blocked (a denied tool while `shouldBreak`) or errored,
   - `continue` otherwise.

**Crucial detail: tool *execution* is not in the processor.** The AI SDK calls
each tool's `execute` (wired in `session/tools.ts:84-114`) as it streams; the
processor only *observes* tool lifecycle events and persists them. This is the
"loop is owned by the SDK" model. (We must build this loop explicitly in Python.)

### 3.2 Outer loop — `runLoop` (`session/prompt.ts:1244-1497`)

A `while (true)` step loop (`:1252`):
- Recomputes the message list (with compaction filtering, `:1256`) each step.
- **Exit condition** (`:1273-1291`): the last assistant message finished with a
  non-`tool-calls` reason **and** has no pending tool calls → break. (Tolerates
  providers that return `stop` while tool calls are present, `:1265-1271`.)
- `step++`; on step 1 forks title generation (`:1293-1300`).
- Handles sub-tasks and compaction tasks inline (`:1303-1320`).
- `maxSteps = agent.steps ?? Infinity`; on the last step it appends a synthetic
  assistant message (`MAX_STEPS` prompt) telling the model to wrap up
  (`:1339-1340`, `:1451`).
- Resolves the **per-step tool set** (`SessionTools.resolve`, `:1387-1401`),
  injects system prompt + environment + instructions + skills
  (`:1435-1443`), then calls `handle.process(...)` (`:1444-1455`).
- Maps the processor result: `stop`→break, `compact`→enqueue compaction+continue,
  else continue (`:1476-1492`).

### 3.3 Streaming protocol (`session/llm.ts`)

- The default runtime is the **AI SDK** `streamText` (`:272-340`). Its
  `fullStream` async-iterable is adapted to OpenCode's internal `LLMEvent`
  stream via `LLMAISDK.toLLMEvents` (`:358-364`).
- **Tool-call repair** (`:278-298`): if the model emits a wrong-cased tool name
  that matches a real tool lowercased, it is silently repaired; otherwise the
  call is rerouted to the `invalid` tool with the error embedded.
- `activeTools` excludes `invalid` (`:303`); `toolChoice` can be forced
  `required` for structured output (`:305`).
- An optional experimental "native" runtime (`:220-259`) bypasses the AI SDK and
  emits the same `LLMEvent` stream directly — proof the event protocol is the
  real seam, not the SDK.

### 3.4 Loop termination & error/retry handling

- **Retry** (`session/retry.ts`): `policy()` (`:175-198`) is an exponential
  backoff schedule (initial 2 s, ×2, capped at 30 s without headers, `:25-65`)
  that **respects `retry-after`/`retry-after-ms` response headers**
  (`:34-64`). `retryable()` (`:67-151`) classifies which errors retry: 5xx always,
  rate-limit/overloaded patterns, free-tier upsell; **context-overflow is never
  retried** (`:69`) — it triggers compaction instead.
- The processor wraps the stream in
  `Effect.retry(policy)` then `Effect.catch(halt)` (`:810-841`); `halt`
  (`:751-778`) converts overflow→compaction, otherwise records the error on the
  assistant message and sets status idle.
- **Doom-loop guard** (`processor.ts:424-449`): if the last 3 parts are the *same
  tool with identical input*, it asks a `doom_loop` permission before proceeding —
  a built-in defense against the model getting stuck repeating one tool call.
- **Abort** (`:798-805`): interrupt sets `aborted`, halts, and `cleanup`
  (`:691-749`) marks in-flight tool parts as `"error" / interrupted: true`.

---

## 4. Permission system (`permission/index.ts`, `core/src/permission.ts`)

### 4.1 Model

- A **Rule** is `{ permission, pattern, action }` where action ∈
  `allow | deny | ask` (`core/src/permission.ts:6-14`).
- **Evaluation** (`core/src/permission.ts:21-31`): given a `permission` name and a
  concrete `pattern` (e.g. tool name, file path, or command text), find the
  **last** rule across all rulesets whose `permission` and `pattern` both wildcard-
  match; default if none is **`ask`**. (Last-match-wins → later/more-specific
  rulesets override.)
- Rulesets come from config (`fromConfig`, `:288-300`), the agent
  (`agent.permission`), and the session (`session.permission`), merged at call
  time (`session/tools.ts:70`).

### 4.2 The `ask` flow (`permission/index.ts:171-211`)

When a tool calls `ctx.ask({permission, patterns, always, metadata})`:
1. For each pattern, `evaluate` the merged ruleset:
   - any `deny` → throw `DeniedError` immediately (model-facing message lists the
     blocking rules, `:95-101`, `:179-183`).
   - all `allow` → return without prompting (`:184`).
   - otherwise → `needsAsk = true`.
2. If asking: create a pending entry with an Effect `Deferred`, publish a
   `permission.asked` bus event, and **block on the deferred** until the user
   replies (`:190-210`). This is the short-circuit: the tool's `execute` is
   suspended mid-flight until approval.

### 4.3 Replies (`permission/index.ts:213-269`)

`reply({requestID, reply, message?})` where reply ∈ `once | always | reject`:
- `reject` → fail the deferred with `RejectedError` (or `CorrectedError` carrying
  the user's feedback text, `:225-242`); **also rejects all other pending requests
  in the same session** (cancels the turn's queued asks).
- `once` → succeed the deferred, nothing persisted (`:244-245`).
- `always` → succeed **and persist** an `allow` rule for each `always` pattern
  (`:247-253`), then auto-resolve any other pending requests that the new rules
  now satisfy (`:255-268`).

The two rejection error types map to model behavior: a plain reject yields *"The
user rejected permission to use this specific tool call"*; a reject-with-feedback
yields *"…with the following feedback: <text>"* (`:81-93`) so the model can
course-correct.

### 4.4 Permission modes / config

- `disabled(tools, ruleset)` (`core/src/permission.ts:37-45`): a tool whose
  effective rule is `pattern:"*" action:"deny"` is fully removed (the `skill`
  tool and the system prompt's skill block are suppressed this way,
  `session/system.ts:66`). Edit-family tools (`edit/write/apply_patch`) all share
  the `"edit"` permission key (`:19`).
- Sub-agents derive a *narrowed* permission set from the parent
  (`agent/subagent-permissions.ts`, used in `tool/task.ts:147-159`).

---

## 5. System prompt / agent instruction

### 5.1 Prompt selection (`session/system.ts:19-33`)

The base prompt is **chosen by model family**: `anthropic.txt` for Claude,
`gpt.txt`/`codex.txt`/`beast.txt` for OpenAI variants, `gemini.txt`,
`kimi.txt`, `trinity.txt`, else `default.txt`. (This is why the registry also
swaps `edit`↔`apply_patch` by model — prompt and tools are co-tuned per family.)

### 5.2 Assembly order (`session/prompt.ts:1435-1443`)

For each step the final system array is:
`[...environment, ...instructions, ...(skills ? [skills] : [])]`
- **environment** (`session/system.ts:48-63`): model id, working directory,
  worktree root, "is git repo", platform, today's date — wrapped in `<env>` tags.
- **instructions**: project/global instruction files (AGENTS.md-style).
- **skills** (`session/system.ts:65-77`): a verbose listing of available skills,
  telling the model to call the `skill` tool to load one.

The base prompt files do **not** enumerate tool schemas — tool name + description
+ JSON Schema are delivered through the AI SDK `tools` field
(`session/tools.ts:81-84`), and the *behavioral* tool guidance lives in prose in
the prompt.

### 5.3 Behavioral mechanics encoded in the prompt (`session/prompt/anthropic.txt`)

- **TodoWrite-driven planning** (`:30-65`): "Use these tools VERY frequently…",
  worked examples of writing todos, marking exactly one `in_progress`, and marking
  `completed` immediately. The `todowrite.txt` description (`tool/todowrite.txt`)
  encodes the full state machine (`pending/in_progress/completed/cancelled`, "one
  in_progress at a time", "mark completed only after verification").
- **Tool-usage policy** (`:75-95`): prefer the `Task` sub-agent for open-ended
  search to save context; use dedicated tools (Read/Edit/Write) instead of
  `cat/sed/echo`; **parallelize independent tool calls in one message**; never use
  bash/echo to talk to the user.
- **Code references** as `file_path:line_number` (`:99-105`).
- **Plan mode** is a separate agent toggled via the `plan` tool
  (`tool/plan-enter.txt` / `plan-exit.txt`): enter to research/design, exit to a
  build agent to implement.
- **Doing-tasks / system-reminder** convention (`:67-72`): tool results and user
  messages may carry `<system-reminder>` tags injected by the host; the read tool
  and reminders subsystem use exactly this channel (`tool/read.ts:319-321`,
  `session/reminders.ts`).

---

## Patterns worth copying into Magi

Concrete, source-anchored mechanisms to replicate. Magi is Python, so "copy" means
re-implement the *behavior*, not the Effect/AI-SDK plumbing.

1. **A single tool contract + auto-truncating wrapper.** Mirror
   `Tool.Def`/`ExecuteResult` (`tool/tool.ts:46-63`) — `{name, description,
   params_schema, execute(args, ctx) -> {title, output, metadata, attachments}}` —
   and put a *universal* output-truncation + temp-file-spill step around every
   tool (`tool/tool.ts:128-142`, `tool/truncate.ts:86-142`). Default 2000
   lines / 50 KB. This one wrapper prevents context blowups across all tools.

2. **Schema-validation failures become model-facing "rewrite your input"
   errors, not exceptions.** Copy `InvalidArgumentsError`
   (`tool/tool.ts:22-32`) and tool-call repair (`session/llm.ts:278-298`):
   wrong-cased names auto-repair; unrecoverable calls route to an `invalid` tool
   carrying the error. Keeps the loop alive instead of crashing on bad args.

3. **Permission as a suspendable `ask(...)` inside `execute`, with
   once/always/reject and last-match-wins wildcard rules.** Reproduce
   `permission/index.ts:171-269` + `core/src/permission.ts:21-45`: tools call
   `ctx.ask` and *block* until reply; `always` persists an `allow` rule;
   `reject(feedback)` returns prose the model can act on; reject cancels the whole
   turn's queued asks. Gate `bash`, `edit/write/apply_patch` (shared `edit` key),
   `webfetch`, and writes outside the project root.

4. **The `edit` tool's prioritized fuzzy-replacer cascade with strict
   uniqueness.** Port `replace()` and its 9 replacers (`tool/edit.ts:674-711`,
   `:240-636`): exact → line-trimmed → block-anchor(Levenshtein) → whitespace →
   indentation → escape → trimmed → context-aware → multi-occurrence, accepting
   only a *unique* match. This is what makes string-replace editing robust to
   model whitespace drift — the single highest-leverage tool to get right.

5. **Bash safety via AST parsing, not regex: prefix-glob permission patterns +
   external-dir write detection + streaming output cap.** Copy
   `tool/shell.ts:374-410` (extract FS-touching commands and resolve their path
   args to catch out-of-root writes), the `prefix + " *"` always-allow pattern
   (`:403-406`), `stdin:"ignore"`, the kill-with-grace timeout (`:540-555`), and
   the rolling-byte-budget tail-on-overflow output (`:479-583`).

**Runner-up patterns** (copy after the core 5): per-request **model-aware tool
filtering** (`tool/registry.ts:316-328`) and per-family system prompts
(`session/system.ts:19-33`); the **doom-loop guard** (3× identical tool call →
ask, `processor.ts:424-449`); **header-aware exponential retry that never retries
context-overflow** (`session/retry.ts:34-151`); the **edit→format→LSP-diagnostics
feedback loop** baked into write/edit results (`tool/write.ts:75-90`,
`tool/edit.ts:193-197`); and **TodoWrite as the planning surface** with the
state-machine prompt (`tool/todowrite.txt`, `session/prompt/anthropic.txt:30-65`).
