# Clawy Agent

**Autonomous task runtime with agentic interaction.**

Unlike chat-based agent frameworks that respond to one message at a time, Clawy Agent runs an agentic loop — it plans, executes tools, evaluates results, and iterates until the task is complete. You can observe, intervene, and guide at any point.

Think Claude Code, but open-source, multi-provider, and programmable.

## Features

- **Autonomous task execution** — Agent runs a persistent loop: plan → execute → evaluate → iterate until done
- **Agentic interaction** — Observe progress, intervene mid-turn, guide direction. Agent can ask questions back
- **Programmable LLM hooks** — Insert LLM-judged checkpoints anywhere in the turn lifecycle for deterministic control
- **Multi-provider** — Anthropic Claude, OpenAI GPT, Google Gemini natively supported
- **27+ built-in tools** — Bash, FileRead/Write/Edit, Glob, Grep, SpawnAgent, Cron, and more
- **Multi-channel** — Telegram, Discord, HTTP API out of the box
- **Built-in memory** — Hipocampus 5-level compaction for persistent cross-session context
- **Coding discipline** — Optional TDD and git commit enforcement
- **Child agents** — Spawn sub-agents for parallel task execution

## Quick Start

```bash
git clone https://github.com/ClawyPro/clawy-agent.git
cd clawy-agent
npm install
npx tsx src/cli/index.ts init
npx tsx src/cli/index.ts start
```

## Installation

### From Source (recommended)

```bash
git clone https://github.com/ClawyPro/clawy-agent.git
cd clawy-agent
npm install
```

Then run commands with `npx tsx src/cli/index.ts <command>`.

### From npm (coming soon)

```bash
npm install -g clawy-agent
clawy-agent <command>
```

## Usage Modes

### Interactive (CLI)

```bash
npx tsx src/cli/index.ts start
```

Terminal conversation mode. Like Claude Code.

### Server (Telegram / Discord / HTTP API)

```bash
npx tsx src/cli/index.ts serve --port 8080
```

Starts the agent as an HTTP API server. If Telegram or Discord tokens are configured, the agent automatically connects to those channels and responds to messages.

### Programmatic

```typescript
import { Agent } from 'clawy-agent'

const agent = new Agent({
  botId: 'my-agent',
  userId: 'local',
  workspaceRoot: './workspace',
  model: 'claude-sonnet-4-6',
  gatewayToken: process.env.ANTHROPIC_API_KEY!,
  apiProxyUrl: 'https://api.anthropic.com',
})

await agent.start()
```

## Configuration

Run `npx tsx src/cli/index.ts init` to generate `clawy-agent.yaml` interactively, or create it manually:

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

## Telegram Bot Setup

1. Create a bot via [@BotFather](https://t.me/BotFather) on Telegram
2. Copy the bot token
3. Add it to your config:

```yaml
channels:
  telegram:
    token: ${TELEGRAM_BOT_TOKEN}
```

4. Set the env var and start:

```bash
export TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
export ANTHROPIC_API_KEY=sk-ant-...
npx tsx src/cli/index.ts serve
```

The agent will automatically start long-polling Telegram for messages and reply in the chat. Typing indicators, reply-to context, and `/reset` command are supported out of the box.

## Discord Bot Setup

1. Create an application at [Discord Developer Portal](https://discord.com/developers/applications)
2. Create a bot under the application, copy the token
3. Invite the bot to your server with the `bot` + `applications.commands` scopes
4. Add to your config:

```yaml
channels:
  discord:
    token: ${DISCORD_BOT_TOKEN}
```

5. Start the agent — it connects to Discord gateway automatically. The bot responds to @mentions.

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

All providers support streaming, tool use, and the full agentic loop. The provider layer handles format conversion automatically.

## Custom Hooks

The core differentiator. Insert LLM-judged checkpoints anywhere in the turn lifecycle:

```
User message
  → beforeTurnStart
    → [agentic loop]
      → beforeLLMCall       ← Context augmentation
      → LLM streaming
      → afterLLMCall        ← Response analysis
      → beforeToolUse       ← Tool permit/deny
      → Tool execution
      → [loop continues...]
    → beforeCommit          ← Quality verification
  → afterTurnEnd            ← Memory save, cleanup
```

### Built-in Hooks

| Hook | Default | Purpose |
|------|---------|---------|
| `factGrounding` | on | Hallucination prevention |
| `preRefusalVerifier` | on | Prevents unnecessary refusals |
| `workspaceAwareness` | on | Auto-injects filesystem context |
| `sessionResume` | on | Seeds context on session resume |
| `discipline` | off | TDD/git commit enforcement |
| `dangerousPatterns` | on | Blocks dangerous operations |

### User Harness Rules

User Harness Rules are runtime checks that you install as Markdown files in the agent workspace. They are useful for rules like "when a document is created, attach it to chat before claiming completion" or "verify the final answer before committing."

Quick setup:

```bash
mkdir -p ./workspace/harness-rules
cp examples/harness-rules/file-delivery-after-create.md ./workspace/harness-rules/
cp examples/harness-rules/final-answer-verifier.md ./workspace/harness-rules/
npx tsx src/cli/index.ts start
```

You can also put one structured rule directly in `./workspace/USER-HARNESS-RULES.md`, or write natural-language operational rules in `./workspace/USER-RULES.md`:

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

Supported triggers are `beforeCommit` and `afterToolUse`. Supported actions are `require_tool`, `llm_verifier`, and `block`. Unknown natural-language lines stay advisory; only recognized patterns or structured frontmatter become executable rules. Set `CORE_AGENT_USER_HARNESS_RULES=off` to disable these checks.

## Architecture

```
Agent (singleton)
  ├── Session (per conversation)
  │   ├── Turn (atomic agentic loop)
  │   │   ├── LLM call → Tool dispatch → Evaluate → Repeat
  │   │   └── Hook checkpoints at each lifecycle point
  │   ├── Transcript (persistent history)
  │   └── Context (layered: identity + rules + memory + tools)
  ├── Tool Registry (27+ built-in)
  ├── Hook Registry (built-in + custom)
  ├── Channel Adapters (Telegram, Discord)
  ├── Cron Scheduler
  ├── Memory (Hipocampus compaction)
  └── SpawnAgent (child agent execution)
```

## Requirements

- Node.js 22+
- An LLM API key (Anthropic, OpenAI, or Google)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

Apache 2.0 — see [LICENSE](LICENSE).
