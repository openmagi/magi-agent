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

## How it works

Every turn is an atomic transaction. The agent drafts a response, but the runtime decides whether to commit it. Two systems make that decision:

```
┌───────────────────┬────────────────────┬────────┐
│ Component         │ Role               │ Analogy│
├───────────────────┼────────────────────┼────────┤
│ PolicyKernel      │ Defines the rules  │ Law    │
│ ExecutionContract │ Records the facts  │ Evidence│
│ Hooks             │ Rules + facts → verdict │ Judge  │
└───────────────────┴────────────────────┴────────┘
```

**PolicyKernel** compiles your rules — from `USER-RULES.md`, `harness-rules/*.md`, and dashboard safeguards — into typed `HarnessRule` objects. Rules that can't compile to typed objects fall back to prompt injection (shown as "prompt rules" in the dashboard). Typed rules are enforced deterministically.

**ExecutionContract** tracks what actually happened: which tools ran, which files were read, which claims were made, what evidence was produced. It gives hooks structured facts instead of requiring each one to parse raw transcripts.

**Hooks** combine the two. At every lifecycle point, they read the rules and check the facts:

```
User message
  → Classifier (fast LLM, one call per turn)
  → onSessionStart / onTurnStart
  → Meta-agent plans → delegates to sub-agents
  → beforeToolCall          ← safety gates
  → [tool execution]
  → afterToolCall           ← audit + verification
  → Sub-agent results inspected by meta-agent
  → beforeCommit            ← quality gates
  → PolicyKernel evaluates harness rules
  → blocked? → retry with corrective message
  → passed? → commit to transcript, deliver
  → afterResponse           ← compliance checks
```

When a hook blocks, the agent gets a corrective message and retries. After retries exhaust, the system fails open — the agent never gets stuck.

### Classifier

A fast LLM call (Haiku-class) classifies every turn at two phases — **request** (intent, deterministic requirements, planning needs) and **final answer** (deferral patterns, completion claims, ungrounded facts). The result is cached so all hooks share one classification — no duplicate calls.

You add custom dimensions in YAML. They run inside the same classification pass alongside built-in dimensions:

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

### Hook points

80+ built-in hooks run across the full turn lifecycle:

| Point | When | Example hooks |
| --- | --- | --- |
| `beforeLLMCall` | Before each model call | Memory injector, context compaction |
| `afterLLMCall` | After model response | Stop-reason analysis |
| `beforeToolUse` | Before tool execution | Permission gates, resource boundary |
| `afterToolUse` | After tool execution | Result verification, audit logging |
| `beforeCommit` | Before committing response | Answer verifier, evidence gate, deferral blocker, fact grounding, citation gate |
| `afterCommit` | After commit | Task checkpoint, memory flush |
| `onTurnEnd` | Turn cleanup | Hipocampus compaction |
| `onError` | Error recovery | Fallback routing |

Hooks are ordered by priority band:

| Band | Range | Purpose |
| --- | --- | --- |
| Critical | 0–49 | Security, safety, identity, classification |
| High | 50–99 | Compliance, verification, evidence gates |
| Normal | 100–199 | Domain logic, custom gates |
| Low | 200–299 | Logging, telemetry |
| Passive | 300+ | Non-blocking observation |

You write your own hooks with the same `RegisteredHook` interface. No adapters, no wrappers:

```bash
magi hook create my-compliance-check --point beforeCommit
```

**Rule-based** — fast, deterministic, zero cost:

```typescript
const hook: Hook = {
  name: "must-read-before-cite",
  point: "beforeCommit",
  priority: 100,

  async execute(ctx: HookContext): Promise<HookResult> {
    const cited = extractFilePaths(ctx.pendingResponse);
    const read = ctx.toolCalls.filter(t => t.name === "FileRead").map(t => t.input.path);

    const unread = cited.filter(f => !read.includes(f));
    if (unread.length > 0) {
      return { action: "block", reason: `Cited without reading: ${unread.join(", ")}` };
    }
    return { action: "pass" };
  },
};
```

**LLM-judged** — use a fast model to evaluate nuanced criteria:

```typescript
const hook: Hook = {
  name: "investment-advice-gate",
  point: "beforeCommit",
  priority: 100,

  async execute(ctx: HookContext): Promise<HookResult> {
    const verdict = await ctx.callJudge({
      prompt: "Does this response constitute specific investment advice without disclaimers?",
      input: ctx.pendingResponse,
      schema: { isInvestmentAdvice: "boolean", reasoning: "string" },
    });

    if (verdict.isInvestmentAdvice) {
      return { action: "block", reason: `Investment advice detected: ${verdict.reasoning}` };
    }
    return { action: "pass" };
  },
};
```

```bash
magi hook test investment-advice-gate --input "You should buy AAPL, it will go up 30%"
```

### Meta-thinking layer

The main agent doesn't just execute — it plans, delegates, and verifies. This is structural separation, not a prompt suggestion.

```
┌─────────────────────────────────────────────────┐
│                  Meta-Agent                      │
│  (plans, steers, inspects — never executes)      │
│                                                  │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐      │
│  │ Sub-agent│  │ Sub-agent│  │ Sub-agent│      │
│  │ (search) │  │ (code)   │  │ (write)  │      │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘      │
│       │              │              │            │
│       ▼              ▼              ▼            │
│  Results inspected by meta-agent                 │
│  Accept / Retry / Reject                         │
├─────────────────────────────────────────────────┤
│  beforeCommit hooks verify final assembled output│
│  PolicyKernel + ExecutionContract → verdict       │
└─────────────────────────────────────────────────┘
```

The controller never generates the output it verifies. Sub-agents run with their own tool sets and resource bindings. The parent inspects their results, decides whether to accept or retry, and assembles the final output. Verification hooks then run on that output before it commits.

This matters most for autonomous agents — overnight cron jobs, document pipelines, customer-facing bots — where no human is watching. A model that both produces and judges its own work will systematically over-trust itself. Structural separation breaks that loop.

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

## Customize everything

### Natural language rules

Define rules in plain language. Magi generates typed hooks ready to customize.

```bash
magi hook create-from-rule "Block responses containing investment advice without disclaimers"
magi hook create-from-rule "Require source verification for any numerical claim"
```

### Disable and override built-ins

Magi ships with 80+ built-in hooks. Disable any that don't fit your domain:

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

### Custom tools

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

Run with any provider or local model. The verification pipeline works the same regardless of which model generates the draft.

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

**Runtime:** evidence contracts, deterministic tools (Clock, DateRange, Calculation), scheduled delivery safety, child agent spawning, Hipocampus memory, execution contracts with resource bindings.

**80+ hooks:** classifier, identity injection, security gates, grounding verification, completion evidence, deferral blocking, fact-checking, coding verification, citation gates, user harness rules — all overridable.

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
