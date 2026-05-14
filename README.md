# Magi

**The self-supervised agent framework. Your rules. Your agent. Zero fork.**

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![TypeScript](https://img.shields.io/badge/TypeScript-5.x-3178C6.svg)](https://www.typescriptlang.org/)
[![Node.js](https://img.shields.io/badge/Node.js-%3E%3D20-339933.svg)](https://nodejs.org/)

## What is Magi

Magi is an open-source agent framework with 60+ built-in hooks and tools that provide production-grade defaults for workspace control, memory, scheduled work, evidence gates, and deterministic checks.

But the real power is that **you** add your own hooks, tools, classifiers, and gates — medical safety, legal compliance, financial regulation, content moderation — without touching a line of core code. You define what "well-controlled" means for your domain.

Run it with Anthropic, OpenAI, Google, Ollama, LM Studio, vLLM, llama.cpp, LiteLLM, or any OpenAI-compatible endpoint.

## Why Magi

| | Traditional agents | Magi |
| --- | --- | --- |
| Safety rules | Hardcoded by vendor | You define them (hooks) |
| Domain tools | Fork the codebase | `magi tool create` |
| Custom classifiers | Not supported | YAML config, zero extra LLM calls |
| Quality gates | One-size-fits-all | Per-project, per-domain |
| Verification | Trust the model | Deterministic evidence contracts |

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

Interactive chat:

```bash
npx tsx src/cli/index.ts chat
```

One-shot task:

```bash
npx tsx src/cli/index.ts run "summarize workspace/knowledge"
```

Pipe input from another command or file:

```bash
cat notes.md | npx tsx src/cli/index.ts run --session notes
```

Run against a specific model or force plan mode:

```bash
npx tsx src/cli/index.ts run --model llama3.1 --plan "draft an implementation plan"
```

Serve the local HTTP API and browser app:

```bash
npx tsx src/cli/index.ts serve --port 8080
```

After npm publishing, the same surface is available as `magi-agent chat`,
`magi-agent run`, and `magi-agent serve`.

| Command | Use it for |
| --- | --- |
| `magi-agent init` | Generate `magi-agent.yaml` for hosted or local LLMs. |
| `magi-agent chat` | Persistent interactive terminal session with Claude Code-style terminal chrome. |
| `magi-agent run "task"` | Single task with streamed output. |
| `magi-agent run --session name` | Reuse a named CLI session and memory context. |
| `magi-agent run --model name --plan "task"` | Override the model for one task and start in plan mode. |
| `magi-agent serve --port 8080` | Start the self-hosted app and HTTP runtime API. |

## Your First Custom Hook

Hooks let you inject domain-specific logic at any point in the agent lifecycle — before a tool runs, after a response, before a commit, on session start.

```bash
magi hook create my-compliance-check --point beforeCommit
```

This scaffolds a hook in `./hooks/my-compliance-check/`:

```typescript
// hooks/my-compliance-check/index.ts
import type { Hook, HookContext, HookResult } from "magi-agent";

const hook: Hook = {
  name: "my-compliance-check",
  point: "beforeCommit",
  priority: 100,

  async execute(ctx: HookContext): Promise<HookResult> {
    const response = ctx.pendingResponse;

    // Your domain logic here
    if (response.includes("guaranteed returns")) {
      return {
        action: "block",
        reason: "Response contains prohibited financial guarantee language",
      };
    }

    return { action: "pass" };
  },
};

export default hook;
```

Test it:

```bash
magi hook test my-compliance-check --input "This stock has guaranteed returns of 50%"
```

## Your First Custom Tool

Custom tools use the same `Tool<I, O>` interface as built-in tools. They are first-class citizens, not plugin wrappers.

```bash
magi tool create medical-lookup --permission net
```

This scaffolds a tool in `./tools/medical-lookup/`:

```
tools/medical-lookup/
  index.ts          # Tool implementation
  manifest.yaml     # Metadata and permissions
  medical-lookup.test.ts
```

```typescript
// tools/medical-lookup/index.ts
import type { Tool, ToolInput, ToolOutput } from "magi-agent";

interface MedicalLookupInput extends ToolInput {
  drugName: string;
  field?: "interactions" | "dosage" | "contraindications";
}

interface MedicalLookupOutput extends ToolOutput {
  drugName: string;
  results: string[];
  source: string;
}

const tool: Tool<MedicalLookupInput, MedicalLookupOutput> = {
  name: "MedicalLookup",
  description: "Look up drug information from verified medical databases",
  permission: "net",

  inputSchema: {
    type: "object",
    properties: {
      drugName: { type: "string", description: "Drug name to look up" },
      field: {
        type: "string",
        enum: ["interactions", "dosage", "contraindications"],
      },
    },
    required: ["drugName"],
  },

  async execute(input: MedicalLookupInput): Promise<MedicalLookupOutput> {
    const response = await fetch(
      `https://api.openfda.gov/drug/label.json?search=${input.drugName}`
    );
    const data = await response.json();

    return {
      drugName: input.drugName,
      results: data.results?.map((r: Record<string, string[]>) =>
        r[input.field ?? "interactions"]
      ) ?? [],
      source: "openFDA",
    };
  },
};

export default tool;
```

Test it:

```bash
magi tool test medical-lookup --input '{"drugName": "aspirin"}'
```

## Custom Classifier Dimensions

Add domain-specific classifier dimensions without writing code. The classifier runs alongside the built-in request analysis with zero extra LLM calls — dimensions are evaluated from the same classification pass.

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

    financial_compliance:
      phase: "request"
      prompt: "Does this request involve specific investment advice or return guarantees?"
      output_schema:
        containsInvestmentAdvice: boolean
        containsReturnGuarantee: boolean
```

Then use results in your hooks:

```typescript
async execute(ctx: HookContext): Promise<HookResult> {
  const { containsDosage } = ctx.classifierResult.medical_safety;
  if (containsDosage) {
    return { action: "inject", content: "IMPORTANT: Verify all dosage information against official sources." };
  }
  return { action: "pass" };
}
```

## Natural Language Rules

Define agent behavior rules in plain language. Magi converts them into executable hooks.

```bash
magi hook create-from-rule "Block responses containing investment advice without disclaimers"
magi hook create-from-rule "Require manager approval for any code deletion over 50 lines"
magi hook create-from-rule "투자 조언이 포함된 응답에 면책조항 경고 추가"
```

Each command generates a typed hook with the rule logic, ready to customize:

```bash
# Generated: hooks/block-investment-advice-without-disclaimers/index.ts
magi hook test block-investment-advice-without-disclaimers \
  --input "You should buy AAPL, it will definitely go up 30% this quarter"
```

## Tool & Hook Configuration

Configure hooks, tools, classifiers, and their interactions in `magi.config.yaml`:

```yaml
hooks:
  disable_builtin:
    - "builtin:output-purity-gate"
  directory: "./hooks"

tools:
  disable_builtin:
    - Browser
  directory: "./tools"
  packages:
    - "@magi-tools/pubmed-search"

classifier:
  custom_dimensions:
    medical_safety:
      phase: "request"
      prompt: "Does this involve drug dosage?"
      output_schema:
        containsDosage: boolean
```

Disable built-in hooks or tools that conflict with your domain. Load tools from local directories or published npm packages. All configuration is declarative — no code changes needed.

## Architecture

### Hook Lifecycle

```
User message
  -> requestClassifier (+ custom dimensions)
  -> onSessionStart / onTurnStart
  -> beforeToolCall          ← your safety gates
  -> [tool execution]
  -> afterToolCall           ← your audit logging
  -> beforeCommit            ← your quality gates
  -> final response or retry
  -> afterResponse           ← your compliance checks
  -> onTurnEnd
```

### Priority Bands

Hooks run in priority order within each lifecycle point. Lower numbers run first.

| Band | Priority range | Purpose |
| --- | --- | --- |
| Critical | 0-49 | Security, safety blocks |
| High | 50-99 | Compliance, regulatory checks |
| Normal | 100-199 | Domain logic, custom gates |
| Low | 200-299 | Logging, telemetry, analytics |
| Passive | 300+ | Non-blocking observation |

### Tool Permission Model

Every tool declares its required permissions. The runtime enforces them before execution.

| Permission | Access |
| --- | --- |
| `none` | Pure computation, no side effects |
| `fs:read` | Read files from workspace |
| `fs:write` | Write files to workspace |
| `net` | Network access (HTTP, WebSocket) |
| `exec` | Execute shell commands |
| `spawn` | Spawn child agents |

Custom tools declare permissions in `manifest.yaml`. The agent operator can further restrict permissions per-project in `magi.config.yaml`.

## Features

### Runtime

- **Evidence contracts:** completion can be blocked unless work has evidence attached to user criteria.
- **Deterministic exactness:** `Clock`, `DateRange`, and `Calculation` tools keep dates, quantities, and arithmetic out of model guesswork.
- **Cron-safe agents:** scheduled delivery safety, deterministic cron control, and background task lifecycle tracking.
- **Spawn agents:** delegate bounded work to child agents with structured criteria and resource bindings.
- **Hipocampus memory:** time-structured memory for durable context, session resume, and compaction.

### Extensibility

- **Custom hooks:** add domain safety, compliance, and quality gates at any lifecycle point.
- **Custom tools:** build first-class tools with the same `Tool<I, O>` interface as built-in tools.
- **Custom classifiers:** add domain dimensions to the request classifier via YAML config, zero extra LLM calls.
- **Natural language rules:** define agent behavior rules in plain language (English, Korean, any language).
- **Operator harness rules:** Markdown rules promoted into runtime checks.
- **Package ecosystem:** install published tool packages from npm.

### Agent Capabilities

- **Any model:** hosted providers, local OpenAI-compatible servers, Ollama, LM Studio, vLLM, llama.cpp, and LiteLLM.
- **Local knowledge base:** write and search project knowledge inside `workspace/knowledge`.
- **60+ built-in tools:** file operations, search, code analysis, knowledge, artifacts, browser, and more.
- **60+ built-in hooks:** security, safety, compliance, quality, and operational defaults.

### Surfaces

- **Browser app:** `http://localhost:8080/app` — chat, workspace files, runtime inspector, knowledge, artifacts, and evidence.
- **CLI:** `magi-agent chat`, `magi-agent run`, `magi-agent serve` — terminal-native agent with Claude Code-style chrome.
- **Desktop:** PWA or Tauri build for macOS, Windows, and Linux.
- **Channels:** Telegram, Discord, webhook.
- **HTTP API:** local `/v1/app/*` endpoints for programmatic access.

## Local LLMs

The default config is local OpenAI-compatible. For Ollama on the host machine:

```bash
ollama serve
ollama pull llama3.1
```

`.env`:

```bash
OPENAI_BASE_URL=http://host.docker.internal:11434/v1
OPENAI_API_KEY=
CORE_AGENT_ROUTING_MODE=direct
CORE_AGENT_MODEL=llama3.1
MAGI_AGENT_SERVER_TOKEN=change-me-local-token
```

`magi-agent.yaml`:

```yaml
llm:
  provider: openai-compatible
  model: ${CORE_AGENT_MODEL}
  baseUrl: ${OPENAI_BASE_URL}
  apiKey: ${OPENAI_API_KEY}

server:
  gatewayToken: ${MAGI_AGENT_SERVER_TOKEN}

workspace: ./workspace
```

Use the same shape for LM Studio, vLLM, llama.cpp, LiteLLM, or another OpenAI-compatible server by changing `OPENAI_BASE_URL` and `CORE_AGENT_MODEL`.

## Local Workspace

```text
workspace/
  knowledge/       local KB documents
  memory/          Hipocampus memory
  artifacts/       generated outputs
  skills/          user-installed skills
  harness-rules/   Markdown runtime rules
```

Open-source deployments do not need an external Knowledge Base. Put Markdown, text, CSV, JSON, YAML, or HTML under `workspace/knowledge`, then use `KnowledgeSearch` or the app's Knowledge panel.

## Managed Platform

[openmagi.ai](https://openmagi.ai) adds managed accounts, hosted auth, billing, encrypted secrets, fleet provisioning, managed Knowledge Base storage, observability, and support. The open-source version gives builders the part that matters most: the runtime, the hooks, the tools, and the local workspace.

## Docs

- [Self-host hardening](docs/SELF-HOST-HARDENING.md)
- [Desktop app](docs/desktop-app.md)
- [Open-source app plan](docs/plans/2026-05-04-open-source-agent-app.md)
- [Runtime proof coverage map](docs/notes/2026-04-30-execution-discipline-coverage-map.md)
- [Migration from Hermes](docs/MIGRATION-FROM-HERMES.md)
- [Migration from legacy gateway](docs/MIGRATION-FROM-LEGACY-GATEWAY.md)

## License

Apache-2.0. See [LICENSE](LICENSE).
