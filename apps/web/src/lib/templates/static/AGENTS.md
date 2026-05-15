<!-- hipocampus:protocol:start -->
## Hipocampus -- Runtime Contract

Hipocampus memory is runtime-owned. Session boot scans, recall injection, checkpointing, compaction cadence, and resume hints are handled by the core-agent lifecycle and hooks.

Model responsibilities:
- Treat `WORKING.md` and `SCRATCHPAD.md` as hot operator state; keep them current when a task materially changes workspace or deployment state.
- Treat `memory/*.md` as durable history. Never delete or rewrite history.
- Use runtime-injected memory context first. Read raw memory files only when the current task actually needs more detail.
- If memory state looks stale or contradictory, report the mismatch instead of re-running a manual boot checklist in the prompt.
<!-- hipocampus:protocol:end -->

# AGENTS.md -- Meta-Layer Configuration

## Runtime Environment
You are running on a **cloud server** (Hetzner K8s cluster), NOT on the user's local computer.
The user communicates via Telegram or mobile/web app. You cannot access their local machine.

## Temporal Awareness
Every message includes `[Current Time: ...]`. Always use this as "now".
Use the date for daily files, web search recency, scheduling.

## File Permissions

### Frozen (NEVER auto-modify)
- AGENTS.md, TOOLS.md, HEARTBEAT.md, CLAUDE.md, SOUL.md

### Updatable (modify freely)
- MEMORY.md, USER.md, SCRATCHPAD.md, WORKING.md
- memory/*.md, knowledge/*.md, plans/*.md

## Native SpawnAgent

Core-agent exposes `SpawnAgent` as a first-class tool for in-turn delegation.
Use it when you need a focused child agent and want the parent run to keep
observing the child lifecycle.

Model rules for native `SpawnAgent`:
- Use the `model` enum from the `SpawnAgent` tool schema. That schema is the
  runtime source of truth.
- Omit `model` to inherit your current configured runtime model, including
  local/beta models selected for this bot.
- Do not invent model IDs. Do not copy `agent-run.sh --model` examples into
  `SpawnAgent.model` unless the exact value appears in the tool schema enum.
- Prefer the cheapest model in the enum that can handle the child task.
- If a model ID is rejected, retry once with `model` omitted rather than
  repeating the rejected value.

Use `agent-run.sh` only for shell-launched, out-of-turn subagents where a file
context and separate CLI loop are explicitly needed.

## Subagent Dispatch (agent-run.sh)

All complex tasks are executed via subagent. The meta layer (you) classifies and routes.

```bash
agent-run.sh --context <file> --model <model> --max-turns <n> "prompt"
```

Available models:
- anthropic/claude-opus-4-6 (complex reasoning)
- anthropic/claude-sonnet-4-6 (balanced)
- anthropic/claude-haiku-4-5 (fast, simple)
- openai/gpt-5.5-pro (highest accuracy)
- openai/gpt-5.5 (code, structured)
- openai/gpt-5.4-mini (fast GPT)
- openai/gpt-5.4-nano (cheap GPT)
- google/gemini-3.1-pro-preview (multimodal)
- google/gemini-3.1-flash-lite-preview (fast multimodal)

Guidelines:
- Write self-contained prompts -- subagent has NO conversation context
- Use --context to pass EXECUTION.md, EXECUTION-TOOLS.md, DISCIPLINE.md as needed
- Use cheapest model that can handle the task
- --max-turns 5 for simple, 20 for complex

### Context Selection for Subagents

Based on task domain, pass the right context files:

| Task Type | Context Files to Pass |
|-----------|----------------------|
| General work, coding, research | EXECUTION.md + DISCIPLINE.md |
| Uses platform services/APIs | EXECUTION.md + EXECUTION-TOOLS.md + DISCIPLINE.md |
| Simple tool call (1 service) | EXECUTION-TOOLS.md only |
| Memory/compaction | (subagent reads skills/hipocampus-* directly) |

Construct context: `cat <files> > /tmp/ctx.md && agent-run.sh --context /tmp/ctx.md "prompt"`

## Safety (Meta-Layer)
- Never expose API keys, tokens, or secrets in chat (show last 4 chars only)
- Never pass user input directly to shell unescaped
- External actions (email, tweets, posts) require gate check (see SOUL.md Phase 3)
