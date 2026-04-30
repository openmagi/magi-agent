# Migration Guide: Legacy Gateway → Clawy Agent

> **Audience:** AI coding agents (Claude Code, Codex, etc.) assisting a user
> who is migrating their legacy gateway bot to Clawy Agent. Read this document
> fully before starting the migration.

## Overview

Clawy Agent is the open-source successor to legacy runtime.
It replaces the `legacy-gateway.json` + `node-host` + gateway architecture with
a single TypeScript process that handles LLM calls, tool execution, channels,
memory, and hooks natively.

**What changes:**
- `legacy-gateway.json` → `clawy-agent.yaml`
- Gateway + node-host → single `clawy-agent serve` process
- Brave Search API → built-in DuckDuckGo WebSearch (no API key)
- `system.run` → `Bash` tool
- `file read/write` → `FileRead` / `FileWrite` / `FileEdit`
- `web_search` / `web_fetch` → `WebSearch` / `WebFetch`
- `sessions_spawn` → `SpawnAgent`
- Session idle reset → persistent sessions with `/reset` command
- Smart routing (LIGHT/MEDIUM/HEAVY) → single model (or custom hook)

**What stays the same:**
- Workspace directory structure (`AGENTS.md`, `IDENTITY.md`, `MEMORY.md`, etc.)
- Skills directory (`skills/*.md`)
- Knowledge directory (`knowledge/*.md`)
- Telegram / Discord channel support
- Cron scheduling
- qmd memory search
- Session key format (`agent:main:<channel>:<chatId>`)

## Pre-Migration Checklist

Before starting, gather these from the existing legacy gateway deployment:

```
□ workspace/ directory contents (the bot's brain — AGENTS.md, IDENTITY.md, etc.)
□ legacy-gateway.json (for config mapping)
□ skills/ directory contents
□ knowledge/ directory contents
□ Telegram bot token (from BotFather)
□ Discord bot token (if used)
□ LLM API key (Anthropic, OpenAI, etc.)
□ Any custom tools or scripts
```

## Step-by-Step Migration

### Step 1: Install Clawy Agent

```bash
git clone https://github.com/ClawyPro/clawy-agent.git
cd clawy-agent
npm install
```

### Step 2: Convert legacy-gateway.json → clawy-agent.yaml

Map the legacy gateway config to Clawy Agent format:

**legacy gateway (`legacy-gateway.json`):**
```json
{
  "models": {
    "providers": {
      "anthropic": { "apiKey": "<KEY>" }
    }
  },
  "agents": {
    "defaults": {
      "model": { "primary": "anthropic/claude-sonnet-4-5" },
      "contextTokens": 30000
    }
  },
  "channels": {
    "telegram": {
      "botToken": "<TELEGRAM_TOKEN>"
    }
  },
  "tools": {
    "web": {
      "search": {
        "provider": "brave",
        "apiKey": "<BRAVE_KEY>"
      }
    }
  }
}
```

**Clawy Agent (`clawy-agent.yaml`):**
```yaml
llm:
  provider: anthropic
  model: claude-sonnet-4-6       # Note: update model name to current version
  apiKey: ${ANTHROPIC_API_KEY}

channels:
  telegram:
    token: ${TELEGRAM_BOT_TOKEN}
  # discord:
  #   token: ${DISCORD_BOT_TOKEN}

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
  name: "<bot name from IDENTITY.md>"
  instructions: "<paste core instructions from AGENTS.md>"
```

**Key differences:**
- No Brave Search API key needed — `WebSearch` uses DuckDuckGo natively
- No smart routing config — single model. For multi-model, use a custom hook
- No `contextTokens` / `contextPruning` — Clawy Agent handles compaction automatically
- No `session.reset.idleMinutes` — sessions persist; user sends `/reset` to clear
- No `gateway.auth.token` — Clawy Agent uses the LLM API key as bearer token

### Step 3: Copy Workspace Files

Copy the entire workspace directory from the legacy gateway deployment:

```bash
# From legacy gateway pod/server:
cp -r /path/to/legacy-gateway/workspace ./workspace

# Or from K8s:
kubectl cp <namespace>/<pod>:/home/ocuser/.clawy/workspace ./workspace
```

The workspace structure is **fully compatible**. These files work as-is:

| File | Purpose | Migration Notes |
|------|---------|----------------|
| `AGENTS.md` | Core behavior rules | Works as-is |
| `IDENTITY.md` | Bot identity | Works as-is |
| `USER.md` | User profile | Works as-is |
| `MEMORY.md` | Long-term memory | Works as-is |
| `CLAUDE.md` | Operational details | Works as-is |
| `HEARTBEAT.md` | Autonomous behavior | Works as-is — cron system compatible |
| `TOOLS.md` | Tool documentation | **Review:** remove legacy gateway-specific tool refs |
| `SCRATCHPAD.md` | Working state | Works as-is |
| `knowledge/` | RAG files | Works as-is — qmd indexes on startup |
| `skills/` | Skill files | Works as-is — auto-loaded as prompt-only tools |

### Step 4: Update TOOLS.md

legacy gateway's `TOOLS.md` references tools by their legacy gateway names. Update to
Clawy Agent tool names:

| legacy gateway Tool | Clawy Agent Tool | Notes |
|---------------|-----------------|-------|
| `system.run` | `Bash` | Same capability, different name |
| `file.read` | `FileRead` | |
| `file.write` | `FileWrite` | |
| `file.edit` | `FileEdit` | New — supports partial edits |
| `web_search` | `WebSearch` | DuckDuckGo, no API key needed |
| `web_fetch` | `WebFetch` | Built-in HTML-to-text extraction |
| `sessions_spawn` | `SpawnAgent` | Enhanced — background delivery support |
| `rag.search` | (automatic) | qmd memory injector runs as a hook |
| `notify` | `NotifyUser` | Requires chat-proxy (Clawy Pro only) |
| `file.glob` | `Glob` | |
| `file.grep` | `Grep` | |

**New tools not in legacy gateway:**
- `TaskBoard` — structured task tracking visible in UI
- `ArtifactCreate/Read/List/Update/Delete` — tiered artifact management
- `CronCreate/List/Update/Delete` — native cron scheduling
- `AskUserQuestion` — structured user input with choices
- `ExitPlanMode` — plan mode lifecycle control
- `CommitCheckpoint` — git commit with discipline enforcement

### Step 5: Update Skills

legacy gateway skills (`skills/<name>.md` or `skills/<name>/SKILL.md`) are
**compatible** with Clawy Agent. The skill loader reads the same format.

However, check for legacy gateway-specific tool references inside skills:

```bash
# Find legacy gateway tool references in skills
grep -r "system\.run\|file\.read\|file\.write\|web_search\|web_fetch\|sessions_spawn\|rag\.search" workspace/skills/
```

Replace any found references with Clawy Agent tool names (see table above).

### Step 6: Handle Crons

legacy gateway crons configured via `oc-cron-script.js` are stored differently.

**legacy gateway:** Crons defined in `legacy-gateway.json` or via bot commands, managed
by the gateway's cron scheduler.

**Clawy Agent:** Crons stored in `workspace/core-agent/crons/index.json`,
managed by the built-in `CronScheduler`. The bot can create crons via the
`CronCreate` tool.

If the bot had scheduled crons, they will need to be recreated after
migration. The bot can do this itself when instructed — tell it:

> "Set up the same cron schedules you had before. Check HEARTBEAT.md for
> the schedule definitions."

### Step 7: Set Environment Variables

```bash
export ANTHROPIC_API_KEY=sk-ant-...
# Or for other providers:
# export OPENAI_API_KEY=sk-...
# export GOOGLE_API_KEY=AI...

export TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
# export DISCORD_BOT_TOKEN=...
```

### Step 8: Start the Agent

```bash
# Interactive mode (terminal + channels):
npx tsx src/cli/index.ts start

# Server mode (channels + HTTP API):
npx tsx src/cli/index.ts serve
```

### Step 9: Verify

After starting, check:

1. **Telegram/Discord connected:** Send a message to the bot — it should respond
2. **Memory intact:** Ask the bot "what do you remember about me?"
3. **Skills working:** Try `/plan` or ask it to use a skill
4. **Web access:** Ask it to search for something
5. **Crons:** Check if scheduled tasks run (or ask bot to recreate them)

## Troubleshooting

### "Bot doesn't respond on Telegram"

- Check `TELEGRAM_BOT_TOKEN` is correct
- Ensure no other process is polling the same bot token (legacy gateway must be stopped first)
- Telegram only allows one poller per token — stop legacy gateway before starting Clawy Agent

### "qmd search returns empty"

- qmd indexes on startup — wait a few seconds after boot
- Check that `workspace/memory/` and `workspace/knowledge/` directories exist
- qmd is an optional dependency — install it: `npm install @tobilu/qmd`

### "Skills not loading"

- Skills must be in `workspace/skills/<name>/SKILL.md` or `workspace/skills/<name>.md`
- Check the startup log for `[clawy-agent] skills: loaded=N`

### "Model not found / API error"

- Model names may have changed (e.g., `claude-sonnet-4-5` → `claude-sonnet-4-6`)
- Check your API key is valid for the model tier

## Architecture Comparison

```
legacy gateway                          Clawy Agent
─────────                         ───────────
legacy-gateway.json                     clawy-agent.yaml
Gateway (node-host)               Agent (single process)
  ├── Telegram plugin               ├── TelegramPoller
  ├── Discord plugin                 ├── DiscordClient
  ├── Session manager                ├── Session (in-process)
  ├── Context pruner                 ├── ContextEngine
  ├── Smart router                   ├── (single model / hook)
  ├── Tool executor                  ├── ToolDispatcher + HookRegistry
  ├── RAG (qmd)                      ├── QmdManager + MemoryInjector
  ├── Subagent spawner               ├── SpawnAgent + ChildAgentLoop
  └── Cron scheduler                 └── CronScheduler

External deps:                    External deps:
  - Brave Search API key            - (none — WebSearch is built-in)
  - Gateway auth token              - LLM API key only
  - LLM API key
```

## What You Lose (and Alternatives)

| legacy gateway Feature | Status in Clawy Agent |
|-----------------|----------------------|
| Smart routing (LIGHT/MEDIUM/HEAVY) | Not built-in. Single model. Custom hook possible. |
| Brave Search | Replaced by DuckDuckGo WebSearch (no API key) |
| Session idle auto-reset | Manual `/reset` command. Custom hook possible. |
| `streamMode: partial` (Telegram) | Full streaming with typing indicator |
| Group chat allowlist | Not built-in. Custom hook possible. |
| Image dimension auto-resize | Not built-in. LLM handles natively. |

## What You Gain

| Clawy Agent Feature | Not in legacy gateway |
|---------------------|----------------|
| 28 programmable LLM hooks | Hooks didn't exist |
| Hipocampus 5-level compaction | Basic memory flush only |
| Anti-hallucination hooks | No equivalent |
| Coding discipline (TDD/git) | No equivalent |
| Superpowers skills (14 built-in) | Skills were user-managed only |
| TaskBoard structured tracking | No equivalent |
| Artifact management | No equivalent |
| Mid-turn user injection | No equivalent |
| Plan mode lifecycle | No equivalent |
| Background task delivery | No equivalent |
| Permission modes (default/plan/auto/bypass) | No equivalent |
