# Magi

**The programmable agent that runs on rules you write — not prompts you pray it follows.**

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![TypeScript](https://img.shields.io/badge/TypeScript-5.x-3178C6.svg)](https://www.typescriptlang.org/)
[![Node.js](https://img.shields.io/badge/Node.js-%3E%3D20-339933.svg)](https://nodejs.org/)

Stop praying your agent follows the prompt. Enforce them.

Magi is an open-source AI agent where **you** define verification rules in code — hooks, classifiers, gates — and the runtime enforces them on every response. The agent can't skip them. Any model, any provider.

## The problem with every other agent

You write a prompt: *"Always verify sources before answering."* The agent tries. Usually it works. Sometimes it doesn't — it cites a file it never opened, fabricates a number, or promises to "send results later" and ends the turn.

The prompt was a suggestion. The model chose to ignore it.

This is fine when you're watching. You catch the mistake and try again. But when the agent runs on its own — answering customers overnight, generating reports via cron, processing documents in a pipeline — no one is there to catch it.

**Magi solves this by turning your rules into runtime enforcement.** You write a hook that says "block any response that cites a file the agent didn't read." The runtime runs it. The agent literally cannot ship that response. Not because you asked nicely — because the code won't let it.

## Quick Start

### Docker (recommended)

```bash
git clone https://github.com/openmagi/magi-agent.git
cd magi-agent
cp .env.example .env
cp magi-agent.yaml.example magi-agent.yaml
docker compose up --build
```

Open `http://localhost:8080/app` and paste the server token from `.env`.

### CLI

```bash
npm install
npm run build
npx tsx src/cli/index.ts init
```

```bash
npx tsx src/cli/index.ts chat                              # interactive session
npx tsx src/cli/index.ts run "summarize workspace/knowledge" # one-shot task
npx tsx src/cli/index.ts serve --port 8080                  # browser app + API
```

| Command | Use it for |
| --- | --- |
| `magi-agent init` | Generate `magi-agent.yaml` for hosted or local LLMs |
| `magi-agent chat` | Persistent interactive terminal session |
| `magi-agent run "task"` | Single task with streamed output |
| `magi-agent run --model name "task"` | Override model for one task |
| `magi-agent serve --port 8080` | Self-hosted app and HTTP API |

## Write your rules

### Hooks — rules the agent can't skip

Hooks are TypeScript functions that run at lifecycle points: before a tool call, before a response commits, after a response, on session start. They use the same `RegisteredHook` interface as built-in hooks. No adapters. No wrappers. Full access.

```bash
magi hook create my-compliance-check --point beforeCommit
```

```typescript
// hooks/my-compliance-check/index.ts
const hook: Hook = {
  name: "my-compliance-check",
  point: "beforeCommit",
  priority: 100,

  async execute(ctx: HookContext): Promise<HookResult> {
    const response = ctx.pendingResponse;

    if (response.includes("guaranteed returns")) {
      return {
        action: "block",
        reason: "Response contains prohibited financial guarantee language",
      };
    }

    return { action: "pass" };
  },
};
```

When this hook blocks, the runtime tells the agent what went wrong and lets it retry with corrected output. After retries exhaust, it delivers the best available response. The agent never gets stuck. But it also can't skip your rule.

```bash
magi hook test my-compliance-check --input "This stock has guaranteed returns of 50%"
```

### Classifiers — domain checks in YAML, zero extra LLM calls

Add domain-specific classifier dimensions without writing code. They run inside the existing classification pass — no additional API calls.

```yaml
# magi.config.yaml
classifier:
  custom_dimensions:
    medical_safety:
      phase: "request"
      prompt: "Does this involve drug dosage or medical treatment recommendations?"
      output_schema:
        containsDosage: boolean
        containsTreatmentAdvice: boolean
```

Then use results in your hooks:

```typescript
async execute(ctx: HookContext): Promise<HookResult> {
  const { containsDosage } = ctx.classifierResult.medical_safety;
  if (containsDosage) {
    return { action: "inject", content: "Verify all dosage information against official sources." };
  }
  return { action: "pass" };
}
```

### Natural language rules

Define rules in plain language. Magi generates typed hooks ready to customize.

```bash
magi hook create-from-rule "Block responses containing investment advice without disclaimers"
magi hook create-from-rule "Require source verification for any numerical claim"
```

### Disable and override built-ins

Magi ships with 60+ built-in hooks. Disable any that don't fit your domain:

```yaml
# magi.config.yaml
hooks:
  disable_builtin:
    - "builtin:output-purity-gate"
  overrides:
    my-compliance-check:
      priority: 85
      blocking: true
```

You control what runs. Not us.

## Custom tools

Tools use the same `Tool<I, O>` interface as built-ins. First-class, not plugin wrappers.

```bash
magi tool create medical-lookup --permission net
```

```typescript
const tool: Tool<MedicalLookupInput, MedicalLookupOutput> = {
  name: "MedicalLookup",
  description: "Look up drug information from verified medical databases",
  permission: "net",
  inputSchema: { /* ... */ },

  async execute(input): Promise<MedicalLookupOutput> {
    const response = await fetch(`https://api.openfda.gov/drug/label.json?search=${input.drugName}`);
    // ...
  },
};
```

## Any model

Run with any provider or local model. The hook and classifier system works the same regardless of which model generates the draft.

```bash
# Hosted
ANTHROPIC_API_KEY=sk-... MAGI_MODEL=claude-sonnet-4-6
OPENAI_API_KEY=sk-...    MAGI_MODEL=gpt-4.1

# Local
ollama serve && ollama pull llama3.1
OPENAI_BASE_URL=http://localhost:11434/v1 MAGI_MODEL=llama3.1
```

Works with Ollama, LM Studio, vLLM, llama.cpp, LiteLLM, or any OpenAI-compatible endpoint.

## Architecture

### How rules are enforced

```
User message
  → requestClassifier (+ your custom dimensions)
  → onSessionStart / onTurnStart
  → beforeToolCall          ← your safety gates
  → [tool execution]
  → afterToolCall           ← your audit logging
  → beforeCommit            ← your quality gates
  → blocked? → retry with corrective message
  → passed? → commit to transcript, deliver to user
  → afterResponse           ← your compliance checks
```

### Priority bands

| Band | Range | Purpose |
| --- | --- | --- |
| Critical | 0-49 | Security, safety blocks |
| High | 50-99 | Compliance, regulatory checks |
| Normal | 100-199 | Domain logic, custom gates |
| Low | 200-299 | Logging, telemetry |
| Passive | 300+ | Non-blocking observation |

### Tool permissions

| Permission | Access |
| --- | --- |
| `none` | Pure computation |
| `fs:read` | Read workspace files |
| `fs:write` | Write workspace files |
| `net` | Network access |
| `exec` | Shell commands |
| `spawn` | Child agents |

## What's built in

**Runtime:** evidence contracts, deterministic tools (Clock, DateRange, Calculation), scheduled delivery safety, child agent spawning, Hipocampus memory.

**60+ hooks:** security gates, grounding verification, completion evidence, deferral blocking, fact-checking, and operational defaults — all overridable.

**60+ tools:** file operations, search, code analysis, knowledge base, artifacts, browser.

**Surfaces:** browser app, CLI, desktop (PWA/Tauri), Telegram, Discord, webhook, HTTP API.

## Workspace

```text
workspace/
  knowledge/       local KB documents
  memory/          Hipocampus memory
  artifacts/       generated outputs
  hooks/           your custom hooks
  tools/           your custom tools
  harness-rules/   Markdown runtime rules
```

## Managed platform

[openmagi.ai](https://openmagi.ai) adds managed accounts, billing, encrypted secrets, hosted runtimes, Knowledge Base storage, and support. The open-source version gives you the part that matters: the runtime, the hooks, the tools, and the workspace.

## Docs

- [Self-host hardening](docs/SELF-HOST-HARDENING.md)
- [Desktop app](docs/desktop-app.md)
- [Open-source app plan](docs/plans/2026-05-04-open-source-agent-app.md)
- [Runtime proof coverage map](docs/notes/2026-04-30-execution-discipline-coverage-map.md)

## License

Apache-2.0. See [LICENSE](LICENSE).
