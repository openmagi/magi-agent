# Magi

**The self-supervised agent framework. Your rules. Your agent. Zero fork.**

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![TypeScript](https://img.shields.io/badge/TypeScript-5.x-3178c6.svg)](https://www.typescriptlang.org/)
[![Node.js](https://img.shields.io/badge/Node.js-%3E%3D20-339933.svg)](https://nodejs.org/)

---

## What is Magi?

Magi is an open-source agent runtime where **you** define what "well-controlled" means.

60+ built-in hooks provide production-grade defaults for evidence gates, deterministic checks, delivery safety, and memory. But the real power is that you add **your** hooks -- medical safety, legal compliance, financial regulation, content moderation -- without touching a line of core code. Drop a TypeScript file in `hooks/`, add a YAML dimension to `magi.config.yaml`, or describe a rule in plain language. The runtime enforces it.

Run it with Anthropic, OpenAI, Google, Ollama, LM Studio, vLLM, llama.cpp, LiteLLM, or any OpenAI-compatible endpoint.

## Why hooks matter

| | Traditional agents | Magi |
|---|---|---|
| Safety rules | Hardcoded by vendor | You define them |
| Domain compliance | Not supported | YAML config or TypeScript |
| Custom classifiers | Fork the code | Append to existing LLM call |
| Quality gates | One-size-fits-all | Per-project, per-domain |
| Verification | Trust the model | Runtime evidence required |

Other agent frameworks decide what "safe" means for you. Magi gives you the control plane and lets you wire in whatever "safe" means in **your** domain.

### What hooks actually catch

We run 30+ autonomous bots in production. The most common failures are not dramatic -- they are plausible-sounding answers that happen to be wrong:

- The agent reads a config showing `claude-opus-4-6`, reports "the system uses gpt-5.5"
- Says "I fixed the bug and tests pass" without ever running `npm test`
- Promises "I'll send the report later" and never does — no cron, no background task
- Reads financial data, then reports wrong numbers in the answer

None of these involve exceeding permissions. The agent had access to every tool. It chose not to use them, or used them and misreported the results. Permission gates are orthogonal to answer quality.

These are the built-in hooks that catch each failure class:

| Hook | Catches |
|------|---------|
| `deferralBlocker` | "I'll send it later" with no scheduled delivery |
| `selfClaimVerifier` | Claims about files or data without supporting reads |
| `factGroundingVerifier` | Tool results that contradict the stated answer |
| `resourceExistenceChecker` | References to files never read |
| `goalProgressGate` | Text-only responses to action requests |
| `completionEvidenceGate` | "Fixed it" without running tests |
| `answerVerifier` | Deflection, partial answers, or refusal |
| `deterministicEvidenceVerifier` | Numeric/date claims not backed by tool evidence |

[Full architecture comparison: how this differs from Claude Code →](https://openmagi.ai/blog/magi-vs-claude-code)

## Quick start

```bash
git clone https://github.com/openmagi/magi-agent.git
cd magi-agent
cp .env.example .env
cp magi-agent.yaml.example magi-agent.yaml
docker compose up --build
```

Open `http://localhost:8080/app` and paste the server token from `.env`.

Or run from source as a CLI agent:

```bash
npm install && npm run build
npx tsx src/cli/index.ts init
npx tsx src/cli/index.ts chat
```

## Your first custom hook

Create a hook in one command:

```bash
magi hook create my-compliance-check --point beforeCommit
```

This scaffolds `hooks/my-compliance-check.ts` and a test fixture:

```typescript
import type { HookArgs, HookContext, HookResult, RegisteredHook } from "magi-agent/hooks/types";

const hook: RegisteredHook<"beforeCommit"> = {
  name: "my-compliance-check",
  point: "beforeCommit",
  priority: 100,
  blocking: true,
  timeoutMs: 5_000,

  async handler(
    args: HookArgs["beforeCommit"],
    ctx: HookContext,
  ): Promise<HookResult<HookArgs["beforeCommit"]> | void> {
    const text = args.assistantText;

    // Your domain logic here. Block, warn, or continue.
    if (text.includes("guaranteed returns")) {
      return {
        action: "block",
        reason: "Response contains prohibited financial guarantees.",
      };
    }

    return { action: "continue" };
  },
};

export default hook;
```

Test it:

```bash
magi hook test my-compliance-check
```

List all hooks (built-in + yours):

```bash
magi hook list
```

No core code was modified. No fork needed. Your hook runs alongside the 60 built-in hooks at the priority you set.

### Real-world examples included

See `examples/hooks/` for complete, runnable hooks:

- **`medical-safety.ts`** -- Blocks drug dosage recommendations without disclaimers
- **`financial-compliance.ts`** -- Catches prohibited investment guarantees
- **`content-moderation.ts`** -- Domain-specific content filtering

## Custom classifier dimensions

Need the LLM classifier to evaluate a new dimension? Add it in YAML -- no code, no extra LLM calls:

```yaml
# magi.config.yaml
classifier:
  custom_dimensions:
    regulatory_risk:
      phase: final_answer
      prompt: |
        Evaluate whether the response contains statements that could
        constitute unregistered investment advice under SEC regulations.
        Consider: specific stock recommendations, price predictions,
        and "guaranteed return" language.
      output_schema:
        risk_level: '"none" | "low" | "medium" | "high"'
        flagged_phrases: "string[]"
```

Custom dimensions piggyback on the existing classifier call. Zero additional LLM requests, zero code changes.

## Natural language rules

Don't know TypeScript? Describe your rule in plain English (or Korean):

```markdown
<!-- harness-rules/investment-disclaimer.md -->

# Investment Disclaimer Rule

Every response that discusses specific stocks, bonds, or investment
products must include the disclaimer: "This is not financial advice.
Consult a licensed financial advisor."
```

```markdown
<!-- harness-rules/medical-disclaimer.md -->

# 의료 면책 조항 규칙

약물 복용량, 투약 일정, 또는 구체적인 치료 방법을 언급하는 모든 응답에는
반드시 "이 정보는 의료 전문가의 조언을 대체하지 않습니다"라는 면책 조항을
포함해야 합니다.
```

Drop a Markdown file in `harness-rules/` and the runtime promotes it into a gate. The PolicyKernel parses your rules and enforces them at `beforeCommit` or `afterToolUse`.

## Architecture

### Hook lifecycle

Every user request passes through a sequence of hook points. Your custom hooks slot in at any point, at any priority.

```
User message
  |
  v
[beforeTurnStart] ---- Can block the turn entirely
  |
  v
[beforeLLMCall] ------ Modify system prompt, inject context
  |
  v
  LLM generates response
  |
  v
[afterLLMCall] ------- Inspect raw LLM output
  |
  v
[beforeToolUse] ------ Gate individual tool calls
  |
  v
  Tool executes
  |
  v
[afterToolUse] ------- Inspect tool results, enforce policies
  |
  v
  ... (repeat for each tool call) ...
  |
  v
[beforeCommit] ------- Final quality gate before response delivery
  |
  v
[afterCommit] -------- Post-delivery logging, analytics
  |
  v
[afterTurnEnd] ------- Cleanup, memory flush
```

### Priority bands

Hooks execute in priority order (lower = earlier). Use these bands to position your hooks relative to built-in ones:

| Band | Priority | Purpose |
|------|----------|---------|
| Critical safety | 0-30 | Hard blocks, sealed file checks |
| Verification | 30-60 | Evidence gates, fact grounding |
| Quality | 60-90 | Answer verification, citation checks |
| **Your hooks** | **80-120** | Domain compliance, custom gates |
| Observation | 120-150 | Logging, analytics, memory |
| Cleanup | 150+ | Post-turn housekeeping |

### Configuration at every level

```yaml
# magi.config.yaml
hooks:
  directory: ./hooks                  # Project hooks
  global_directory: ~/.magi/hooks     # Org-wide hooks

  disable_builtin:
    - factGroundingVerifier           # Turn off a built-in you don't need

  overrides:
    medical-safety:
      priority: 30                    # Promote to critical safety band
      blocking: true
      timeoutMs: 3000
```

## Features

**Runtime**
- 60+ built-in hooks: evidence gates, deterministic checks, delivery safety, anti-hallucination, citation verification
- User-defined hooks: TypeScript files, auto-loaded from `hooks/` directory
- Custom classifier dimensions: YAML-defined, zero extra LLM calls
- Natural language rules: Markdown files in `harness-rules/` promoted to runtime gates
- Hook CLI: `create`, `list`, `enable`, `disable`, `test`, `logs`
- PolicyKernel: Markdown rules parsed into typed enforcement policies

**Agent capabilities**
- Multi-provider LLM: Anthropic, OpenAI, Google, Ollama, LM Studio, vLLM, llama.cpp, LiteLLM
- Hipocampus memory: time-structured memory with compaction (daily/weekly/monthly/root)
- Local knowledge base: workspace KB with search, no external service needed
- Deterministic tools: `Clock`, `DateRange`, `Calculation` keep facts out of model guesswork
- Child agents: `SpawnAgent` with structured criteria and resource bindings
- Cron-safe scheduling: delivery safety, deterministic cron control, background task lifecycle
- Execution contracts: acceptance criteria, resource bindings, evidence tracking

**Surfaces**
- Browser app: self-hosted at `localhost:8080/app` with workspace, knowledge, artifacts, and runtime inspector
- CLI: `magi chat`, `magi run`, `magi serve`
- Desktop: PWA or Tauri build
- Channels: Telegram, Discord, webhook

## CLI reference

| Command | Purpose |
|---|---|
| `magi init` | Generate config for hosted or local LLMs |
| `magi chat` | Interactive terminal session |
| `magi run "task"` | Single task with streamed output |
| `magi serve --port 8080` | Start the self-hosted app and HTTP API |
| `magi hook create <name> --point <point>` | Scaffold a new hook |
| `magi hook list` | List all registered hooks |
| `magi hook test <name>` | Run hook test fixtures |
| `magi hook enable/disable <name>` | Toggle hooks via config |
| `magi hook logs <name>` | View hook execution history |

## Local LLMs

```bash
ollama serve && ollama pull llama3.1
```

```env
OPENAI_BASE_URL=http://host.docker.internal:11434/v1
OPENAI_API_KEY=
CORE_AGENT_ROUTING_MODE=direct
CORE_AGENT_MODEL=llama3.1
```

Works the same with LM Studio, vLLM, llama.cpp, LiteLLM, or any OpenAI-compatible server.

## Managed platform

[openmagi.ai](https://openmagi.ai) provides the hosted version with managed auth, billing, fleet provisioning, encrypted secrets, knowledge base storage, observability, and support. The open-source version gives you the part that matters most: the runtime, the hooks, and the workspace.

## Docs

- [Self-host hardening](docs/SELF-HOST-HARDENING.md)
- [Desktop app](docs/desktop-app.md)
- [Open-source app plan](docs/plans/2026-05-04-open-source-agent-app.md)
- [Runtime proof coverage map](docs/notes/2026-04-30-execution-discipline-coverage-map.md)

## License

Apache-2.0. See [LICENSE](LICENSE).
