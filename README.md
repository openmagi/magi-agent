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

## Four layers of control

Prompts are a single point of control that the model can ignore. Magi enforces rules across four independent layers — each one programmable, each one customizable.

### Layer 1: Classifier

A fast LLM call (Haiku-class) classifies every turn at two phases — request and final answer. It detects intent, deferral patterns, completion claims, deterministic requirements (dates, calculations, data queries), and planning needs. The result is cached for the turn so all hooks share one classification — no duplicate calls.

You add custom classifier dimensions in YAML. They run inside the same classification pass alongside the built-in dimensions:

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

### Layer 2: Hooks

74 hooks run at every lifecycle point: `beforeLLMCall`, `beforeToolUse`, `afterToolUse`, `beforeCommit`, `afterResponse`, `onSessionStart`, `onTurnEnd`. Blocking hooks reject output and force a retry with a corrective message. After retries exhaust, the system fails open — the agent never gets stuck.

You write your own hooks with the same `RegisteredHook` interface. No adapters, no wrappers:

```bash
magi hook create my-compliance-check --point beforeCommit
```

```typescript
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

```bash
magi hook test my-compliance-check --input "This stock has guaranteed returns of 50%"
```

### Layer 3: Policy engine

The **PolicyKernel** compiles your rules — from `USER-RULES.md`, `harness-rules/*.md`, and dashboard safeguards — into typed `HarnessRule` objects. The **ExecutionContract** tracks what happened during the turn: which tools ran, which files were read, which claims were made, what evidence was produced.

Hooks use this state to make decisions. A completion-evidence gate checks whether the agent produced the artifacts it claims to have produced. A resource-boundary gate checks whether tool calls stayed within declared scope. The policy engine gives hooks structured facts instead of requiring each hook to parse raw transcripts.

Rules that can't be compiled into typed objects fall back to prompt-level injection — shown as "prompt rules" in the dashboard. Typed rules are enforced deterministically by hooks.

### Layer 4: Meta-agent

The main agent operates as a meta-thinker: it plans, verifies, and steers. Actual execution — file operations, searches, code changes, document generation — is delegated to sub-agents via `SpawnAgent`.

The controller never generates the output it verifies. This structural separation is more reliable than self-policing via prompt, where the same model that produced a claim also judges whether the claim is accurate.

Sub-agents run with their own tool sets and resource bindings. The parent agent inspects their results, decides whether to accept or retry, and assembles the final output. Verification hooks then run on that final output before it commits.

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

Magi ships with 74 built-in hooks. Disable any that don't fit your domain:

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

Run with any provider or local model. The four control layers work the same regardless of which model generates the draft.

```bash
# Hosted
ANTHROPIC_API_KEY=sk-... CORE_AGENT_MODEL=claude-sonnet-4-6
OPENAI_API_KEY=sk-...    CORE_AGENT_MODEL=gpt-4.1

# Local
ollama serve && ollama pull llama3.1
OPENAI_BASE_URL=http://localhost:11434/v1 CORE_AGENT_MODEL=llama3.1
```

Works with Ollama, LM Studio, vLLM, llama.cpp, LiteLLM, or any OpenAI-compatible endpoint.

## Architecture

### How rules are enforced

```
User message
  → Classifier (LLM + rule-based hybrid, + your custom dimensions)
  → onSessionStart / onTurnStart
  → Meta-agent plans and delegates to sub-agents
  → beforeToolCall          ← your safety gates
  → [tool execution]
  → afterToolCall           ← your audit logging
  → Sub-agent results inspected by meta-agent
  → beforeCommit            ← your quality gates
  → PolicyKernel evaluates harness rules
  → blocked? → retry with corrective message
  → passed? → commit to transcript, deliver to user
  → afterResponse           ← your compliance checks
```

### Priority bands

| Band | Range | Purpose |
| --- | --- | --- |
| Critical | 0-49 | Security, safety, identity, classification |
| High | 50-99 | Compliance, verification, evidence gates |
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

**Runtime:** evidence contracts, deterministic tools (Clock, DateRange, Calculation), scheduled delivery safety, child agent spawning, Hipocampus memory, execution contracts with resource bindings.

**74 hooks:** classifier, identity injection, security gates, grounding verification, completion evidence, deferral blocking, fact-checking, coding verification, citation gates, user harness rules — all overridable.

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
