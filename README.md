# Clawy Agent

**Open-source runtime for personal AI agents that can finish work reliably.**

Clawy Agent is not a prompt chain and not a chatbot wrapper. It is a durable
agent runtime: every task runs inside an observable loop with tool execution,
runtime checks, persistent transcripts, memory, deterministic evidence, file
delivery, scheduled automation, and user-defined harness rules.

If you are tired of agents that create files but forget to send them, claim work
is done without verification, compute dates or totals from model intuition, lose
context after a restart, misroute scheduled jobs, or ignore workflow
instructions buried in the prompt, Clawy Agent moves those behaviors out of
vibes and into runtime state.

Think Claude Code, but open-source, multi-provider, always-on, and programmable.

## Why Clawy Agent

Most agent frameworks give you a model, a tool schema, and a loop. That is not
enough for real personal agents.

Real agents need to:

- keep working across long tasks, restarts, and channel reconnects
- remember user context without stuffing the whole chat into the next prompt
- run tools while respecting file boundaries, safety rules, and permissions
- pause for user input without losing the turn
- verify work, exact values, and source usage before committing a final answer
- deliver generated files back to the user instead of only writing them to disk
- run scheduled workflows without letting the model guess delivery channels or
  execute worker tasks in the wrong role
- expose the control surface so operators can add rules without forking core code

Clawy Agent is built around that premise. The LLM is the reasoning engine; the
runtime is the discipline layer that decides what must be evidenced,
persisted, retried, blocked, or delivered.

## The Runtime Model

Every user request becomes an atomic `Turn`. A turn can stream, think, call
tools, receive tool results, retry failed drafts, ask the user a question, spawn
child agents, and only then commit a final answer.

```
User message
  -> beforeTurnStart          session resume, onboarding, memory prep
  -> beforeLLMCall            context, identity, rules, memory, policy
  -> LLM stream               text, thinking, tool_use
  -> beforeToolUse            permission gates, resource checks
  -> Tool execution           files, shell, browser, web, documents, child agents
  -> afterToolUse             provenance, delivery, harness checks
  -> ... repeat until ready
  -> beforeCommit             verification, output purity, delivery gates
  -> turn_committed           transcript, memory, artifacts, channel delivery
```

The important part: checks are not just text in the system prompt. They are
runtime gates at the points where mistakes happen, backed by an
`ExecutionContract` that carries criteria, resource bindings, verification
evidence, and deterministic evidence through the turn.

## What Makes It Different

| Capability | What it means in practice |
| --- | --- |
| **Atomic agentic loop** | The agent can plan, execute tools, evaluate outputs, and continue until the job is actually complete. |
| **Lifecycle hooks** | Add deterministic or LLM-judged checks at `beforeLLMCall`, `beforeToolUse`, `afterToolUse`, `beforeCommit`, and more. |
| **Execution contracts** | Acceptance criteria, resource bindings, used-resource provenance, verification evidence, and deterministic requirements live in runtime state. |
| **Deterministic exactness** | Dates, time windows, counts, averages, sums, percent changes, and comparisons can be forced through runtime evidence instead of model guesswork. |
| **Scheduled-work discipline** | Cron turns are treated as orchestration work: delivery channel is persisted, parent turns stay meta-only, worker work is delegated, and delivery safety is enforced. |
| **Replayable transcripts** | Tool calls, tool results, control events, compaction boundaries, and canonical assistant messages are persisted for restart-safe replay. |
| **Hipocampus memory** | A layered memory system with root/daily/weekly/monthly compaction and qmd-backed recall. |
| **User Harness Rules** | Install Markdown rules that become runtime checks, including required tools, required tool input patterns, LLM verifiers, and blockers. |
| **Native delivery path** | Documents, spreadsheets, and workspace files can be generated, registered, and delivered back through supported channels. |
| **Child agents** | Spawn background agents with bounded tools, workspace isolation, and result delivery. |
| **Multi-channel** | Run the same runtime from CLI, HTTP, Telegram, or Discord. |
| **Multi-provider** | Use Anthropic, OpenAI, or Google models through one runtime interface. |

## Built-In Capabilities

Clawy Agent ships with 30+ native tools and runtime subsystems:

- **Workspace tools:** `FileRead`, `FileWrite`, `FileEdit`, `Glob`, `Grep`, `Bash`
- **Web and browser:** `WebSearch`, `WebFetch`, `Browser`
- **Deterministic workbench:** `Clock`, `DateRange`, `Calculation`
- **Knowledge and memory:** `KnowledgeSearch`, Hipocampus recall, qmd indexing
- **Generated outputs:** `DocumentWrite`, `SpreadsheetWrite`, `FileDeliver`, `FileSend`
- **Artifacts:** `ArtifactCreate`, `ArtifactRead`, `ArtifactList`, `ArtifactUpdate`, `ArtifactDelete`
- **Delegation:** `SpawnAgent`, `TaskList`, `TaskGet`, `TaskOutput`, `TaskStop`
- **Planning and control:** `EnterPlanMode`, `ExitPlanMode`, `AskUserQuestion`, `TaskBoard`
- **Automation:** `CronCreate`, `CronList`, `CronUpdate`, `CronDelete`
- **Discipline:** `CommitCheckpoint`, execution contracts, verification evidence gates
- **Skills:** workspace `skills/` loading plus `POST /v1/admin/skills/reload`

Optional dependencies enable richer formats and rendering paths, including DOCX,
PDF, HWPX, XLSX, qmd, and Playwright-backed browser work.

## Architecture

```
Agent
  |-- Session                         one conversation / channel thread
  |   |-- Turn                        atomic agentic loop
  |   |-- Transcript                  append-only JSONL replay log
  |   |-- Context                     identity + rules + memory + tool state
  |   |-- ExecutionContract           criteria + resources + deterministic evidence
  |
  |-- ToolRegistry                    native tools + loaded skills
  |-- HookRegistry                    runtime control plane
  |-- PolicyKernel                    compiled runtime policy + user harness rules
  |-- OutputArtifactRegistry          generated files and delivery metadata
  |-- BackgroundTaskRegistry          spawned child-agent work
  |-- CronScheduler                   durable scheduled tasks + channel routing
  |-- HipocampusService               memory compaction + recall
  |-- ChannelAdapters                 CLI, HTTP, Telegram, Discord
```

Design principles:

- **Runtime over prompt vibes.** Important constraints should live in hooks,
  gates, transcripts, and tool boundaries, not only in instructions.
- **Durability by default.** A useful agent should survive reconnects, retries,
  background work, and long conversations.
- **Evidence before exact claims.** Dates, counts, arithmetic, source usage, and
  completion claims should be grounded in tool results or explicitly marked as
  unverifiable.
- **Operator control.** Users should be able to install rules and skills without
  patching the core runtime.
- **Visible work.** Tool calls, progress, generated artifacts, and delivery
  events are first-class runtime state.
- **Fail open where ergonomic, fail closed where safety matters.** Memory recall
  should not kill a turn; unsafe file writes and false completion claims can.

## Reliability Architecture

Clawy Agent is designed for the failure modes that show up once agents are used
for real work, not only demos.

### Execution Contracts

Each turn can carry an `ExecutionContract`. The contract records:

- acceptance criteria and their verification state
- resource bindings and used-resource provenance
- generated artifacts and delivery evidence
- deterministic requirements and deterministic evidence

Hooks and tools read and write this contract throughout the turn. That lets the
runtime block a weak final answer because a criterion is still pending, because
the agent cited a resource it did not use, or because a numeric/date claim was
not backed by deterministic evidence.

### Deterministic Exactness

When a request asks for exact values, the runtime can classify it as requiring
deterministic evidence. Typical triggers include date ranges, recency windows,
counts, totals, averages, percent changes, financial values, and comparisons.

The model is then expected to use native tools such as `Clock`, `DateRange`,
`Calculation`, `FileRead`, `KnowledgeSearch`, `WebFetch`, or `WebSearch`
instead of doing mental math. Those tools can record structured evidence on the
execution contract. Before commit, the deterministic evidence verifier compares
the draft answer against the recorded evidence and can force a retry when the
answer invents or contradicts exact values.

The result is not "the model was told to be careful." The runtime has a place to
store the requirement, a place to store the evidence, and a gate that can reject
the final answer.

### Scheduled Work

Cron jobs are treated as durable workflows, not delayed chat messages. When a
cron is created, Clawy Agent captures the source delivery channel instead of
asking the model to choose a target later. When the cron fires, the parent turn
is constrained to meta-orchestration: inspect the schedule, delegate the actual
work to a child agent, and summarize or deliver the result.

Cron safety is enforced through several runtime pieces working together:

- `CronScheduler` persists cron records and next-fire times
- `cronMetaOrchestrator` keeps parent cron turns in the orchestration role
- `beforeToolUse` guards prevent parent cron turns from doing worker I/O
- `beforeCommit` checks reject cron parent answers that skipped delegation
- `cronDeliverySafety` blocks direct or ambiguous channel delivery patterns
- `TaskBoard` iteration state, the sweeper, and stop conditions keep long
  scheduled loops restart-safe and bounded

That is how Clawy Agent avoids the common failure where a scheduled agent
ignores the workflow boundary, opens the wrong resource, or sends the result to
the wrong channel.

### Operator Rules And Skills

Operators can extend the runtime without forking it:

- Markdown harness rules compile into executable gates
- `require_tool` ensures a tool was successfully used in the current turn
- `require_tool_input_match` ensures a successful tool used the expected input,
  such as a specific `WebFetch.url` or `Bash.command` pattern
- `llm_verifier` adds scoped judgment checks where deterministic checks are not
  enough
- workspace `skills/` can add prompt-only or script-backed tools and can be
  reloaded through `POST /v1/admin/skills/reload`

Native tools are restored after skill loading, so a workspace skill cannot
accidentally replace core tools such as `Browser`, `WebSearch`, `WebFetch`,
`Clock`, `DateRange`, or `Calculation`.

## Quick Start

```bash
git clone https://github.com/ClawyPro/clawy-agent.git
cd clawy-agent
npm install
npx tsx src/cli/index.ts init
npx tsx src/cli/index.ts start
```

The `init` command writes `clawy-agent.yaml`. The `start` command runs an
interactive terminal agent against the configured workspace.

## Installation

### From Source

```bash
git clone https://github.com/ClawyPro/clawy-agent.git
cd clawy-agent
npm install
```

Run commands with:

```bash
npx tsx src/cli/index.ts <command>
```

### From npm

An npm package is planned. Until it is published, use the source install above.

```bash
npm install -g clawy-agent
clawy-agent <command>
```

## Usage Modes

### Interactive CLI

```bash
npx tsx src/cli/index.ts start
```

Terminal conversation mode for local work.

### Server

```bash
npx tsx src/cli/index.ts serve --port 8080
```

Starts the HTTP API server. If Telegram or Discord tokens are configured, the
same process also connects to those channels.

### Programmatic

```typescript
import { Agent } from "clawy-agent";

const agent = new Agent({
  botId: "my-agent",
  userId: "local",
  workspaceRoot: "./workspace",
  model: "claude-sonnet-4-6",
  gatewayToken: process.env.ANTHROPIC_API_KEY!,
  apiProxyUrl: "https://api.anthropic.com",
});

await agent.start();
```

## Configuration

Run `npx tsx src/cli/index.ts init` to generate `clawy-agent.yaml`
interactively, or create it manually:

```yaml
llm:
  provider: anthropic          # anthropic, openai, or google
  model: claude-sonnet-4-6
  apiKey: ${ANTHROPIC_API_KEY}

channels:
  telegram:
    token: ${TELEGRAM_BOT_TOKEN}
  discord:
    token: ${DISCORD_BOT_TOKEN}

hooks:
  builtin:
    factGrounding: true
    preRefusalVerifier: true
    workspaceAwareness: true
    sessionResume: true
    discipline: false

memory:
  enabled: true
  compaction: true

workspace: ./workspace

identity:
  name: "My Agent"
  instructions: "You are a helpful coding assistant."
```

## Channels

### Telegram

1. Create a bot via [@BotFather](https://t.me/BotFather).
2. Copy the bot token.
3. Add it to `clawy-agent.yaml`.

```yaml
channels:
  telegram:
    token: ${TELEGRAM_BOT_TOKEN}
```

Then start server mode:

```bash
export TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
export ANTHROPIC_API_KEY=sk-ant-...
npx tsx src/cli/index.ts serve
```

The agent uses Telegram long polling and supports typing indicators, reply
context, and `/reset`.

### Discord

1. Create an application at the [Discord Developer Portal](https://discord.com/developers/applications).
2. Create a bot and copy the token.
3. Invite it with the `bot` and `applications.commands` scopes.
4. Add the token to config and start `serve`.

The bot responds to mentions in channels where it is present.

## Multi-Provider LLM

Switch providers by changing `llm.provider` and `llm.apiKey`:

```yaml
# Anthropic Claude
llm:
  provider: anthropic
  model: claude-sonnet-4-6
  apiKey: ${ANTHROPIC_API_KEY}

# OpenAI GPT
llm:
  provider: openai
  model: gpt-5.4
  apiKey: ${OPENAI_API_KEY}

# Google Gemini
llm:
  provider: google
  model: gemini-2.5-flash
  apiKey: ${GOOGLE_API_KEY}
```

All providers support streaming, tool use, and the full agentic loop. The
provider layer handles message and tool-call format conversion.

## Hooks: The Control Plane

Hooks are the core extension point. They can inspect or modify turn state,
inject context, approve or block tools, verify final answers, write memory, and
emit audit events.

Common built-in gates include:

| Hook | Purpose |
| --- | --- |
| `factGrounding` | Reduces unsupported factual claims. |
| `preRefusalVerifier` | Challenges unnecessary refusals before they reach the user. |
| `deterministicExactness` | Classifies exact numeric/date/count requests and records deterministic requirements. |
| `deterministicEvidenceVerifier` | Checks final exact claims against recorded deterministic evidence. |
| `workspaceAwareness` | Injects relevant filesystem context. |
| `sessionResume` | Restores continuity when a session resumes. |
| `discipline` | Enables TDD/git enforcement for coding tasks. |
| `dangerousPatterns` | Blocks unsafe operations. |
| `outputPurityGate` | Blocks leaked internal planning in final answers. |
| `completionEvidenceGate` | Requires evidence before completion claims. |
| `resourceBoundaryGate` | Prevents use of resources outside the task boundary. |
| `cronMetaOrchestrator` | Keeps scheduled parent turns in a meta-orchestration role. |
| `cronDeliverySafety` | Prevents ambiguous or direct channel delivery from cron worker paths. |
| `userHarnessRules` | Enforces operator-installed Markdown rules. |

## User Harness Rules

User Harness Rules are runtime checks installed as Markdown files in the agent
workspace. They let an operator turn "please always do X" into an executable
gate.

Example use cases:

- if a document or spreadsheet is created, deliver it before saying it is ready
- before final answer, verify all requested acceptance criteria
- block a response that cites a file the agent did not read this turn
- require a tool call after a specific type of generated output

Quick setup:

```bash
mkdir -p ./workspace/harness-rules
cp examples/harness-rules/file-delivery-after-create.md ./workspace/harness-rules/
cp examples/harness-rules/final-answer-verifier.md ./workspace/harness-rules/
cp examples/harness-rules/tool-input-match.md ./workspace/harness-rules/
npx tsx src/cli/index.ts start
```

You can also put one structured rule in `./workspace/USER-HARNESS-RULES.md`, or
write natural-language operational rules in `./workspace/USER-RULES.md`:

```markdown
- 파일을 만들면 반드시 채팅에 첨부해줘.
- 최종 답변 전에는 요구사항을 충족했는지 한 번 더 검사해.
```

Structured Markdown rules use YAML frontmatter:

```markdown
---
id: user-harness:file-delivery-after-create
trigger: beforeCommit
condition:
  anyToolUsed:
    - DocumentWrite
    - SpreadsheetWrite
action:
  type: require_tool
  toolName: FileDeliver
enforcement: block_on_fail
timeoutMs: 2000
---

When a document or spreadsheet is created, deliver it to the chat before claiming completion.
```

Supported triggers are `beforeCommit` and `afterToolUse`. Supported actions are
`require_tool`, `require_tool_input_match`, `llm_verifier`, and `block`.
`require_tool_input_match` checks that a successful same-turn tool call used a
specific tool input field, such as `toolName: Bash` with `inputPath: command` or
`toolName: WebFetch` with `inputPath: url`. Conditions can also include
`userMessageMatches` for regex-scoped rules. Unknown
natural-language lines stay advisory; recognized patterns and structured
frontmatter become executable rules. Set `CORE_AGENT_USER_HARNESS_RULES=off` to
disable these checks.

## Migration Guides

- [Migration from legacy gateway](docs/MIGRATION-FROM-LEGACY-GATEWAY.md)
- [Migration from Hermes Agent](docs/MIGRATION-FROM-HERMES.md)

## Requirements

- Node.js 22+
- An API key for Anthropic, OpenAI, or Google

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

Apache 2.0. See [LICENSE](LICENSE).
