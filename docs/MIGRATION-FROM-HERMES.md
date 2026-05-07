# Migration Guide: Hermes Agent → Magi

> **Audience:** AI coding agents (Claude Code, Codex, etc.) assisting a user
> who is migrating their Hermes Agent setup to Magi. Read this
> document fully before starting the migration.

## Overview

Hermes Agent (by Nous Research) is a Python-based self-improving AI agent.
Magi is a TypeScript-based autonomous task runtime. Both are
open-source, self-hosted, and support Telegram/Discord — but the
architecture, config format, and extension model differ significantly.

**What changes:**
- `~/.hermes/config.yaml` → `magi-agent.yaml` (different schema)
- `~/.hermes/SOUL.md` → `workspace/IDENTITY.md` + `identity` in YAML
- `~/.hermes/skills/` → `workspace/skills/` (compatible SKILL.md format)
- `~/.hermes/memory/` → `workspace/memory/` (different engine — qmd vs SQLite FTS5)
- Python runtime → Node.js 22+ runtime
- `hermes` CLI → `npx tsx src/cli/index.ts` (or `magi-agent` after npm publish)
- `hermes gateway` → `magi-agent serve`
- `hermes setup` → `magi-agent init`
- Plugin hooks (4 points) → Hook registry (28 built-in, full lifecycle)

**What stays conceptually the same:**
- SKILL.md format (agentskills.io compatible)
- Session-based conversations with persistent memory
- Telegram / Discord channel support
- Cron / scheduled tasks
- Tool-use agentic loop
- Workspace-based state

## Hermes → Magi Mapping

### Directory Structure

```
Hermes Agent (~/.hermes/)          Magi (./workspace/)
──────────────────────────         ─────────────────────────────
SOUL.md                            IDENTITY.md (or identity: in YAML)
config.yaml                        ../magi-agent.yaml
.env                               environment variables
skills/                            skills/ (same SKILL.md format)
  └── my-skill/SKILL.md              └── my-skill/SKILL.md
memory/                            memory/ (different engine)
  └── (SQLite FTS5 + HRR)            └── (markdown + qmd BM25/vector)
sessions/                          core-agent/sessions/ (JSONL transcripts)
logs/                              (stdout logging)
crons/                             core-agent/crons/index.json
```

### Config Conversion

**Hermes (`~/.hermes/config.yaml`):**
```yaml
model:
  provider: anthropic
  model: claude-sonnet-4-6
  api_key_env: ANTHROPIC_API_KEY

fallback_providers:
  - provider: openrouter
    model: anthropic/claude-sonnet

memory:
  memory_enabled: true
  user_profile_enabled: true
  memory_char_limit: 3200

tools:
  web_search:
    provider: firecrawl
    use_gateway: true

agent:
  max_turns: 90

gateway:
  telegram:
    bot_token_env: TELEGRAM_BOT_TOKEN
    allowed_users: [123456789]
  discord:
    bot_token_env: DISCORD_BOT_TOKEN
    require_mention: true

skills:
  external_dirs:
    - ~/.agents/skills
```

**Magi (`magi-agent.yaml`):**
```yaml
llm:
  provider: anthropic
  model: claude-sonnet-4-6
  apiKey: ${ANTHROPIC_API_KEY}

channels:
  telegram:
    token: ${TELEGRAM_BOT_TOKEN}
  discord:
    token: ${DISCORD_BOT_TOKEN}

hooks:
  builtin:
    factGrounding: true        # no equivalent in Hermes
    preRefusalVerifier: true   # no equivalent in Hermes
    workspaceAwareness: true
    sessionResume: true
    discipline: false

memory:
  enabled: true
  compaction: true

workspace: ./workspace

identity:
  name: "My Agent"
  instructions: "<paste content from SOUL.md>"
```

**Key differences:**
- No `fallback_providers` — Magi uses a single model (multi-model routing via custom hook)
- No `allowed_users` — implement via a custom `beforeTurnStart` hook if needed
- No `require_mention` — Discord adapter responds to @mentions by default
- No `firecrawl` — `WebSearch` (DuckDuckGo) and `WebFetch` are built-in, zero API keys
- No `max_turns` config — set via `maxTurnsPerSession` in `AgentConfig` (default 50)
- No `memory_char_limit` — hipocampus compaction handles context sizing automatically

### SOUL.md → identity + IDENTITY.md

Hermes uses `SOUL.md` as the agent's identity (first thing in system prompt).
Magi has two options:

**Option A — YAML `identity` field (simple):**
```yaml
identity:
  name: "My Agent"
  instructions: "You are a helpful assistant who speaks Korean."
```

**Option B — Workspace `IDENTITY.md` (full legacy gateway compat):**
```bash
# Copy SOUL.md content into workspace IDENTITY.md
cp ~/.hermes/SOUL.md ./workspace/IDENTITY.md
```

The `IDENTITY.md` file is automatically injected into the system prompt
by the `identityInjector` hook (built-in, on by default). This is the
recommended approach for complex personalities.

You can also create `AGENTS.md` in the workspace root for behavioral
rules that supplement identity.

## Step-by-Step Migration

### Step 1: Install Magi

```bash
git clone https://github.com/openmagi/magi-agent.git
cd magi-agent
npm install
```

### Step 2: Create Workspace from Hermes State

```bash
mkdir -p workspace/skills workspace/memory workspace/knowledge

# Copy identity
cp ~/.hermes/SOUL.md workspace/IDENTITY.md

# Copy skills (SKILL.md format is compatible)
cp -r ~/.hermes/skills/* workspace/skills/ 2>/dev/null || true

# Copy any external shared skills
cp -r ~/.agents/skills/* workspace/skills/ 2>/dev/null || true
```

### Step 3: Export Memory

Hermes stores memory in SQLite (FTS5 + holographic). Magi uses
markdown files + qmd indexing. Memory must be converted:

```bash
# Option A: Ask Hermes to dump its memory before shutdown
# In a Hermes session, ask:
#   "Export all your memory and knowledge to markdown files in ~/hermes-export/"

# Option B: Create a fresh MEMORY.md from scratch
cat > workspace/MEMORY.md << 'EOF'
# Memory

## User Profile
<!-- Copy relevant user context from Hermes memory -->

## Key Facts
<!-- Copy important persistent facts -->

## Preferences
<!-- Copy user preferences -->
EOF
```

For knowledge files (RAG), copy any markdown/text documents to
`workspace/knowledge/`. Magi's qmd will index them on startup.

### Step 4: Create magi-agent.yaml

```bash
npx tsx src/cli/index.ts init
```

Or create manually — see the config conversion table above.

### Step 5: Migrate Skills

Hermes skills use the agentskills.io SKILL.md format. Magi reads
the same format. However, check for Hermes-specific tool references:

```bash
# Find Hermes-specific tool names in skills
grep -r "run_terminal\|read_file\|write_file\|search_web\|browser_action" workspace/skills/
```

Replace with Magi tool names:

| Hermes Tool | Magi Tool |
|-------------|-----------------|
| `run_terminal` | `Bash` |
| `read_file` | `FileRead` |
| `write_file` | `FileWrite` |
| `edit_file` | `FileEdit` |
| `search_web` | `WebSearch` |
| `fetch_url` | `WebFetch` |
| `browser_action` | `WebFetch` (or Playwright if installed) |
| `spawn_session` | `SpawnAgent` |
| `glob` | `Glob` |
| `grep` | `Grep` |
| `create_skill` | (agent writes SKILL.md to workspace/skills/) |
| `memory_search` | (automatic via memoryInjector hook) |
| `schedule` | `CronCreate` |

### Step 6: Handle Hermes Plugins

Hermes supports plugins that register tools, hooks, and commands. Magi
Agent has a different extension model:

| Hermes Extension | Magi Equivalent |
|-----------------|----------------------|
| Plugin with `pre_llm_call` hook | Custom hook on `beforeLLMCall` event |
| Plugin with `post_llm_call` hook | Custom hook on `afterLLMCall` event |
| Plugin with `on_session_start` hook | Custom hook on `beforeTurnStart` event |
| Plugin with `on_session_end` hook | Custom hook on `afterTurnEnd` event |
| Plugin that registers a tool | `ToolRegistry.register()` in Agent setup |
| Plugin that registers a slash command | `SlashCommandRegistry.register()` in Agent setup |
| Memory provider plugin | `QmdManager` (built-in, or replace via hook) |
| Context engine plugin | `ContextEngine` (built-in compaction) |

For programmatic extension, modify `src/Agent.ts` or create skill files.

### Step 7: Stop Hermes, Start Magi

**Important:** Telegram only allows one poller per bot token. Stop Hermes
before starting Magi.

```bash
# Stop Hermes gateway
hermes gateway stop
# OR kill the process
pkill -f "hermes gateway"

# Set environment variables
export ANTHROPIC_API_KEY=sk-ant-...
export TELEGRAM_BOT_TOKEN=123456:ABC-DEF...

# Start Magi
npx tsx src/cli/index.ts serve
```

### Step 8: Verify

1. **Telegram/Discord:** Send a message — bot should respond
2. **Identity:** Ask "who are you?" — should match SOUL.md content
3. **Skills:** Ask it to use a specific skill — should find it in workspace/skills/
4. **Memory:** Note: previous Hermes memories are NOT automatically available (different engine). The agent starts fresh and builds memory over time via hipocampus
5. **Web search:** Ask it to search for something — WebSearch should work

### Step 9: Recreate Scheduled Tasks

Hermes cron jobs don't transfer automatically. Tell the agent:

> "I had these scheduled tasks in my previous setup: [list them].
> Please recreate them using CronCreate."

Or copy the schedule definitions into `workspace/HEARTBEAT.md` and the
agent will reference them.

## Feature Comparison — What Changes

### You Lose

| Hermes Feature | Status | Workaround |
|---------------|--------|-----------|
| Holographic Memory (HRR) | Not available | Hipocampus 5-level compaction (different approach, equally persistent) |
| Self-improving skill generation | Not automatic | Agent can create skills manually when instructed |
| 6 channel support (WhatsApp, Signal, Slack...) | Only Telegram + Discord | Add channel adapters in src/channels/ |
| `allowed_users` restriction | Not built-in | Custom `beforeTurnStart` hook |
| Fallback providers | Not built-in | Change `llm.provider` in YAML, or custom hook |
| Python ecosystem / plugins | TypeScript only | Rewrite plugins as hooks or tools |
| `hermes skills install` from hub | Not available | Copy SKILL.md files to workspace/skills/ |
| Smart model routing (LIGHT/MEDIUM/HEAVY) | Not built-in | Single model, or custom `beforeLLMCall` hook |
| Ink TUI (terminal UI) | Basic readline REPL | `magi-agent start` for interactive mode |

### You Gain

| Magi Feature | Not in Hermes |
|---------------------|---------------|
| 28 programmable hooks (full turn lifecycle) | Only 4 plugin hooks |
| Anti-hallucination (factGrounding, resourceExistence, preRefusal, deferralBlocker) | No equivalent |
| Mid-turn user injection (intervene while agent works) | No equivalent |
| Plan mode lifecycle (default → plan → auto → bypass) | No equivalent |
| Coding discipline (TDD, git commit enforcement) | No equivalent |
| TaskBoard (structured task tracking) | No equivalent |
| Artifact management (create/read/update/delete) | No equivalent |
| Background task delivery (SpawnAgent → deliver result) | No equivalent |
| Zero-dep LLM client (no SDK, raw HTTP) | SDK-based |
| Superpowers skills (14 built-in: brainstorming, systematic-debugging, TDD...) | Community skills only |
| CommitCheckpoint (atomic git commits in agentic loop) | No equivalent |

## Troubleshooting

### "Bot doesn't respond on Telegram"
- **Most common:** Hermes gateway still running and polling the same token
- Stop Hermes first: `hermes gateway stop` or `pkill -f hermes`
- Telegram allows only one poller per bot token

### "Skills not found"
- Check directory structure: `workspace/skills/<name>/SKILL.md`
- Hermes uses `~/.hermes/skills/`; copy them to `workspace/skills/`
- Check startup log: `[magi-agent] skills: loaded=N`

### "Agent doesn't remember previous conversations"
- Expected: Hermes and Magi use different memory engines
- Hermes memories (SQLite FTS5/HRR) cannot be imported directly
- The agent builds new memory via hipocampus compaction over time
- Seed important context in `workspace/MEMORY.md` manually

### "Missing tool: search_web / browser_action"
- `search_web` → use `WebSearch` (DuckDuckGo, built-in)
- `browser_action` → use `WebFetch` for page content; install `playwright` for JS rendering
- No API key needed for either

### "Python plugin won't work"
- Magi is TypeScript — Python plugins must be rewritten
- Simple plugins: convert to a hook in `src/hooks/builtin/`
- Tool plugins: convert to a tool in `src/tools/`
- Complex plugins: wrap as a Bash script called via the `Bash` tool
