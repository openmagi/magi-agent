# Clawy Agent

**Open-source runtime for personal AI agents that actually finish work.**

Clawy Agent is not a prompt chain and not a chatbot wrapper. It is a durable
agent runtime: every task runs inside an observable loop with tool execution,
runtime checks, persistent transcripts, memory, file delivery, and user-defined
harness rules.

If you are tired of agents that create files but forget to send them, claim work
is done without verification, lose context after a restart, or ignore workflow
instructions buried in the prompt, Clawy Agent moves those behaviors out of
vibes and into the runtime.

Think Claude Code, but open-source, multi-provider, always-on, and programmable.

## Why Clawy Agent

Most agent frameworks give you a model, a tool schema, and a loop. That is not
enough for real personal agents.

Real agents need to:

- keep working across long tasks, restarts, and channel reconnects
- remember user context without stuffing the whole chat into the next prompt
- run tools while respecting file boundaries, safety rules, and permissions
- pause for user input without losing the turn
- verify work before committing a final answer
- deliver generated files back to the user instead of only writing them to disk
- expose the control surface so operators can add rules without forking core code

Clawy Agent is built around that premise. The LLM is the reasoning engine; the
runtime is the discipline layer.

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
runtime gates at the points where mistakes happen.

## What Makes It Different

| Capability | What it means in practice |
| --- | --- |
| **Agentic loop** | The agent can plan, execute tools, evaluate outputs, and continue until the job is actually complete. |
| **Lifecycle hooks** | Add deterministic or LLM-judged checks at `beforeLLMCall`, `beforeToolUse`, `afterToolUse`, `beforeCommit`, and more. |
| **Execution discipline** | Acceptance criteria, verification evidence, TDD/git discipline, and commit-time gates can block weak completion claims. |
| **Replayable transcripts** | Tool calls, tool results, control events, compaction boundaries, and canonical assistant messages are persisted for restart-safe replay. |
| **Hipocampus memory** | A layered memory system with root/daily/weekly/monthly compaction and qmd-backed recall. |
| **User Harness Rules** | Install Markdown rules that become runtime checks, such as "deliver files before saying done." |
| **Native delivery path** | Documents, spreadsheets, and workspace files can be generated, registered, and delivered back through supported channels. |
| **Child agents** | Spawn background agents with bounded tools, workspace isolation, and result delivery. |
| **Multi-channel** | Run the same runtime from CLI, HTTP, Telegram, or Discord. |
| **Multi-provider** | Use Anthropic, OpenAI, or Google models through one runtime interface. |

## Built-In Capabilities

Clawy Agent ships with 30+ native tools and runtime subsystems:

- **Workspace tools:** `FileRead`, `FileWrite`, `FileEdit`, `Glob`, `Grep`, `Bash`
- **Web and browser:** `WebSearch`, `WebFetch`, `Browser`
- **Knowledge and memory:** `KnowledgeSearch`, Hipocampus recall, qmd indexing
- **Generated outputs:** `DocumentWrite`, `SpreadsheetWrite`, `FileDeliver`, `FileSend`
- **Artifacts:** `ArtifactCreate`, `ArtifactRead`, `ArtifactList`, `ArtifactUpdate`, `ArtifactDelete`
- **Delegation:** `SpawnAgent`, `TaskList`, `TaskGet`, `TaskOutput`, `TaskStop`
- **Planning and control:** `EnterPlanMode`, `ExitPlanMode`, `AskUserQuestion`, `TaskBoard`
- **Automation:** `CronCreate`, `CronList`, `CronUpdate`, `CronDelete`
- **Discipline:** `CommitCheckpoint`, execution contracts, verification evidence gates

Optional dependencies enable richer formats and rendering paths, including DOCX,
PDF, HWPX, XLSX, qmd, and Playwright-backed browser work.

## Architecture

```
Agent
  |-- Session                         one conversation / channel thread
  |   |-- Turn                        atomic agentic loop
  |   |-- Transcript                  append-only JSONL replay log
  |   |-- Context                     identity + rules + memory + tool state
  |   |-- ExecutionContract           criteria + evidence + resource bindings
  |
  |-- ToolRegistry                    native tools + loaded skills
  |-- HookRegistry                    runtime control plane
  |-- OutputArtifactRegistry          generated files and delivery metadata
  |-- BackgroundTaskRegistry          spawned child-agent work
  |-- CronScheduler                   durable scheduled tasks
  |-- HipocampusService               memory compaction + recall
  |-- ChannelAdapters                 CLI, HTTP, Telegram, Discord
```

Design principles:

- **Runtime over prompt vibes.** Important constraints should live in hooks,
  gates, transcripts, and tool boundaries, not only in instructions.
- **Durability by default.** A useful agent should survive reconnects, retries,
  background work, and long conversations.
- **Operator control.** Users should be able to install rules and skills without
  patching the core runtime.
- **Visible work.** Tool calls, progress, generated artifacts, and delivery
  events are first-class runtime state.
- **Fail open where ergonomic, fail closed where safety matters.** Memory recall
  should not kill a turn; unsafe file writes and false completion claims can.

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
| `workspaceAwareness` | Injects relevant filesystem context. |
| `sessionResume` | Restores continuity when a session resumes. |
| `discipline` | Enables TDD/git enforcement for coding tasks. |
| `dangerousPatterns` | Blocks unsafe operations. |
| `outputPurityGate` | Blocks leaked internal planning in final answers. |
| `completionEvidenceGate` | Requires evidence before completion claims. |
| `resourceBoundaryGate` | Prevents use of resources outside the task boundary. |

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
`require_tool`, `llm_verifier`, and `block`. Unknown natural-language lines stay
advisory; recognized patterns and structured frontmatter become executable
rules. Set `CORE_AGENT_USER_HARNESS_RULES=off` to disable these checks.

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
