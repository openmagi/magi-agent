import { PUBLIC_BRAND } from "./brand";

export type DocsPageSlug =
  | "overview"
  | "getting-started"
  | "quickstart"
  | "cli"
  | "configuration"
  | "customization"
  | "runtime"
  | "tools"
  | "contracts"
  | "hooks"
  | "memory"
  | "skills"
  | "automation"
  | "integrations"
  | "api"
  | "deployment"
  | "security"
  | "architecture"
  | "reference"
  | "troubleshooting";

export type DocsGroup = "Start" | "Configure" | "Operate" | "Reference";

export type DocsCodeBlock = {
  title: string;
  lines: readonly string[];
};

export type DocsLink = {
  label: string;
  href: string;
  external?: boolean;
};

export type DocsSection = {
  id: string;
  label: string;
  title: string;
  body: readonly string[];
  bullets?: readonly string[];
  code?: DocsCodeBlock;
  links?: readonly DocsLink[];
};

export type DocsPage = {
  slug: DocsPageSlug;
  href: string;
  group: DocsGroup;
  navLabel: string;
  title: string;
  description: string;
  summary: string;
  sections: readonly DocsSection[];
};

const SOURCE_URL = PUBLIC_BRAND.sourceUrl;
const SOURCE_CLONE_URL = `${SOURCE_URL}.git`;
const LOCAL_APP_URL = "http://localhost:8080/app";

export const DOCS_PAGES: readonly DocsPage[] = [
  {
    slug: "overview",
    href: "/docs",
    group: "Start",
    navLabel: "Overview",
    title: "Open Magi Docs",
    description:
      "A developer-first guide to the Magi Agent open-source runtime and the hosted Open Magi Cloud service.",
    summary:
      "Start here for the map: install the open-source work agent, configure models and memory, understand runtime contracts, then decide what to self-host and what to run through Open Magi Cloud.",
    sections: [
      {
        id: "quick-links",
        label: "Start",
        title: "Quick Links",
        body: [
          "Open Magi documentation is organized around the path an operator takes: install, configure, run reliable work, connect tools, deploy, and harden the system.",
          "If you are handing this to an AI coding agent, start with the agent install prompt in Getting Started and the machine-readable docs endpoints.",
        ],
        bullets: [
          "New local install: Getting Started",
          "First working run: Quickstart",
          "Terminal usage: CLI",
          "Provider keys and memory: Configuration",
          "Hooks, tools, and classifier extensibility: Customization",
          "Contracts, hooks, and progress: Runtime, Contracts, and Hooks",
          "Tool behavior: Tools",
          "Memory and Knowledge Base: Memory",
          "Skill packages and runtime hooks: Skills",
          "Scheduled and delegated work: Automation",
          "API, channels, and desktop: Integrations and API",
          "Self-hosting and cloud split: Deployment",
          "Permissions and server tokens: Security",
          "Exact names and fixes: Reference and Troubleshooting",
        ],
        links: [
          { label: "Install locally", href: "/docs/getting-started" },
          { label: "Run quickstart", href: "/docs/quickstart" },
          { label: "Use the CLI", href: "/docs/cli" },
          { label: "Read hooks", href: "/docs/hooks" },
          { label: "Read contracts", href: "/docs/contracts" },
          { label: "Tool reference", href: "/docs/tools" },
          { label: "Self-host hardening", href: "/docs/security" },
          { label: "View source", href: SOURCE_URL, external: true },
        ],
      },
      {
        id: "what-it-is",
        label: "Concept",
        title: "What Open Magi is",
        body: [
          "Magi Agent is the open-source AI work agent: a durable runtime that runs tasks across models, tools, files, and channels.",
          "Open Magi Cloud is the hosted layer for managed accounts, billing, model credits, Knowledge Base capacity, encrypted secrets, runtime nodes, monitoring, and support.",
        ],
        bullets: [
          "Open-source runtime first. You can inspect, modify, and self-host the agent.",
          "Provider-neutral execution. Route work across Claude, GPT, Gemini, local models, and OpenAI-compatible endpoints.",
          "Runtime evidence. Execution contracts and gates decide whether work is actually complete.",
          "Hosted when needed. Use cloud for operational capacity rather than rebuilding account, billing, and fleet systems.",
        ],
      },
      {
        id: "runtime-model",
        label: "Mental model",
        title: "Runtime model",
        body: [
          "Think of Open Magi as a work surface with a runtime behind it, not a chat transcript with a model attached.",
          "A run reads workspace context, selects models, calls tools, writes artifacts, reports progress, and verifies completion against the task contract.",
        ],
        bullets: [
          "Workspace: files, notes, memory, channels, and tool configuration.",
          "Run: the current unit of agent work with progress and evidence.",
          "Contract: expectations for artifacts, checks, permissions, and completion.",
          "Surface: web app, desktop shell, API, and channel integrations.",
        ],
      },
      {
        id: "agent-readable",
        label: "For agents",
        title: "Machine-readable docs",
        body: [
          "The docs are available as text endpoints so coding agents can load the product model without scraping rendered pages.",
          "Use the compact index for orientation and the full docs when an agent needs implementation-level context.",
        ],
        links: [
          { label: "/llms.txt", href: "/llms.txt" },
          { label: "/docs/llms.txt", href: "/docs/llms.txt" },
          { label: "/docs/llms-full.txt", href: "/docs/llms-full.txt" },
        ],
      },
    ],
  },
  {
    slug: "getting-started",
    href: "/docs/getting-started",
    group: "Start",
    navLabel: "Getting Started",
    title: "Getting Started",
    description:
      "Clone Magi Agent, run it with Docker Compose or source commands, and hand setup to an AI coding agent.",
    summary:
      "Run Magi Agent locally first. The quickest path is Docker Compose; source mode is better when you are changing the runtime.",
    sections: [
      {
        id: "requirements",
        label: "Prerequisites",
        title: "Before you install",
        body: [
          "Magi Agent expects a normal developer workstation with Git, Node.js, npm, and Docker available. Source mode uses TypeScript commands; Docker Compose gives you the fastest full-stack local run.",
          "Keep provider credentials outside source control. Use environment variables or a local `.env` file that stays private.",
        ],
        bullets: [
          "Git for cloning the canonical repository.",
          "Node.js and npm for source mode.",
          "Docker and Docker Compose for the local full-stack run.",
          "At least one model provider key, unless you are only inspecting the UI or using a local compatible endpoint.",
        ],
      },
      {
        id: "docker",
        label: "For humans",
        title: "Install locally with Docker Compose",
        body: [
          `Clone ${SOURCE_URL}, review the example environment, then build and run the local stack.`,
          `Open ${LOCAL_APP_URL} after the containers are ready.`,
        ],
        code: {
          title: "Terminal",
          lines: [
            `git clone ${SOURCE_CLONE_URL}`,
            "cd magi-agent",
            "cp .env.example .env",
            "cp magi-agent.yaml.example magi-agent.yaml",
            "docker compose up --build",
            `open ${LOCAL_APP_URL}`,
          ],
        },
        bullets: [
          "Use Docker Compose when you want the fastest local product loop.",
          "Edit `.env` before connecting real provider keys or channel credentials.",
          "Stop the stack with Ctrl-C, then `docker compose down` when you want to remove containers.",
        ],
      },
      {
        id: "source",
        label: "Source mode",
        title: "Run from source",
        body: [
          "Use source mode when you are changing runtime code, hooks, UI, or integration behavior.",
          "The source command starts the TypeScript runtime directly so code edits are easier to inspect and debug.",
        ],
        code: {
          title: "Terminal",
          lines: [
            `git clone ${SOURCE_CLONE_URL}`,
            "cd magi-agent",
            "npm install",
            "cp .env.example .env",
            "cp magi-agent.yaml.example magi-agent.yaml",
            "npx tsx src/cli/index.ts start",
          ],
        },
      },
      {
        id: "for-agents",
        label: "For agents",
        title: "Agent handoff prompt",
        body: [
          "Paste this into Codex, Claude Code, or another coding agent when you want setup handled from a clean folder.",
        ],
        code: {
          title: "Agent prompt",
          lines: [
            "Read AGENTS.md first if the repository includes one.",
            `Clone ${SOURCE_URL} into ./magi-agent.`,
            "Inspect README.md and docs before editing.",
            "Install dependencies with npm install.",
            "Prefer Docker Compose for a first local smoke unless I ask for source mode.",
            "Run docker compose up --build, then Open http://localhost:8080/app and report the local URL.",
            "Do not expose secrets. Ask before changing database, auth, billing, or production deployment behavior.",
          ],
        },
      },
      {
        id: "first-run",
        label: "Verification",
        title: "What to verify first",
        body: [
          "A useful first run proves the app surface loads, the runtime can start, and configuration is explicit.",
          "Do not judge the install complete only because dependencies installed. Verify the work surface and runtime health.",
        ],
        bullets: [
          "The app opens at the configured local URL.",
          "The runtime starts without missing environment errors you did not expect.",
          "Model provider configuration is visible and kept out of source control.",
          "A simple task can create or update a workspace artifact.",
        ],
      },
    ],
  },
  {
    slug: "quickstart",
    href: "/docs/quickstart",
    group: "Start",
    navLabel: "Quickstart",
    title: "Quickstart Tutorial",
    description:
      "A first-task walkthrough for running Magi Agent locally, checking runtime state, using memory, and producing an artifact.",
    summary:
      "Use this tutorial when you want to prove the runtime works end to end: initialize config, start the server, run one task, inspect evidence, and deliver a file.",
    sections: [
      {
        id: "first-task",
        label: "Walkthrough",
        title: "First task walkthrough",
        body: [
          "This quickstart is intentionally concrete. It proves more than a page load: the CLI can read config, the runtime can start, a session can run, tools can write an artifact, and the app can inspect the resulting state.",
          "Run this from a clean checkout before changing policy, hooks, or deployment settings.",
        ],
        code: {
          title: "Terminal",
          lines: [
            `git clone ${SOURCE_CLONE_URL}`,
            "cd magi-agent",
            "npm install",
            "npx tsx src/cli/index.ts init",
            "npx tsx src/cli/index.ts serve --port 8080",
          ],
        },
        bullets: [
          "Open `http://localhost:8080/app` once the server logs that it is listening.",
          "Keep the server terminal open; run one-shot commands from another shell.",
          "If you use Docker Compose instead, the same tutorial starts after the app opens.",
        ],
      },
      {
        id: "run-task",
        label: "Run",
        title: "Run a task with evidence",
        body: [
          "Use `magi-agent run` for a shell-native proof. The task below asks the runtime to read workspace context, write a status artifact, and state what evidence it used.",
          "The same runtime path is used by the web app and desktop shell; the CLI is just the most direct way to debug it.",
        ],
        code: {
          title: "Terminal",
          lines: [
            "cat README.md | npx tsx src/cli/index.ts run --session quickstart --plan \\",
            "  \"Read the project summary, create workspace/quickstart-status.md, and include verification evidence.\"",
          ],
        },
        bullets: [
          "`--session quickstart` keeps follow-up context under one named session.",
          "`--plan` biases the runtime toward planning before write tools are used.",
          "The final answer should mention the artifact path and the verification evidence used.",
        ],
      },
      {
        id: "inspect",
        label: "Inspect",
        title: "Inspect runtime state",
        body: [
          "After a task runs, inspect state through the app API. These calls are the same read model the UI uses for sessions, tools, skills, tasks, artifacts, crons, memory, and Knowledge Base.",
          "Set `MAGI_AGENT_SERVER_TOKEN` before exposing the server outside localhost. Local development may omit auth only when the server is bound to a trusted local process.",
        ],
        code: {
          title: "Terminal",
          lines: [
            "curl http://localhost:8080/v1/app/runtime",
            "curl 'http://localhost:8080/v1/app/sessions'",
            "curl 'http://localhost:8080/v1/app/workspace/file?path=quickstart-status.md'",
          ],
        },
        bullets: [
          "Runtime state should show the registered tools and loaded runtime hooks.",
          "Workspace reads are path-normalized and stay inside the configured workspace root.",
          "Use the app UI when you want transcript, Knowledge Base, artifact, and cron panels in one place.",
        ],
      },
      {
        id: "next",
        label: "Next",
        title: "Where to go next",
        body: [
          "After the quickstart works, read the CLI page for command details, Tools for tool behavior, Contracts for reliability gates, and Hooks for customization points.",
        ],
        bullets: [
          "Change model routing in Configuration.",
          "Add a harness rule in Customization.",
          "Add a Skill runtime hook in Skills.",
          "Expose the runtime only after reading Security and API.",
        ],
      },
    ],
  },
  {
    slug: "cli",
    href: "/docs/cli",
    group: "Start",
    navLabel: "CLI",
    title: "CLI",
    description:
      "Use the Magi Agent terminal interface for interactive chat, one-shot tasks, scripts, pipes, and the local HTTP app server.",
    summary:
      "Self-host/local CLI. The CLI is the smallest way to run the same work runtime: initialize config, chat interactively, run one-off tasks, pipe input, override models, or serve the local browser app.",
    sections: [
      {
        id: "install-cli",
        label: "Install",
        title: "Install and initialize",
        body: [
          "Self-host/local CLI is configured from a source checkout or installed package. Cloud CLI now uses `openmagi cloud login --bot <id>` with browser login from the bot dashboard so users do not copy hosted session tokens by hand.",
          "In source mode the CLI entrypoint is `src/cli/index.ts`. After the package is published, the same surface is exposed as `magi-agent`.",
          "The CLI reads `magi-agent.yaml` from the current working directory. Run init once, then edit provider, workspace, server token, identity, hooks, and memory settings before using real credentials.",
        ],
        code: {
          title: "Source checkout",
          lines: [
            `git clone ${SOURCE_CLONE_URL}`,
            "cd magi-agent",
            "npm install",
            "npm run build",
            "npx tsx src/cli/index.ts init",
          ],
        },
        bullets: [
          "`magi-agent init` creates `magi-agent.yaml` interactively.",
          "`magi-agent --help` prints the current command surface.",
          "`magi-agent version` prints the installed package version.",
          "Use environment variable references such as `${ANTHROPIC_API_KEY}` in config instead of pasting secrets into prompts.",
        ],
      },
      {
        id: "command-map",
        label: "Commands",
        title: "CLI command map",
        body: [
          "Use `magi-agent chat` when you want a persistent terminal session, `magi-agent run` when you want a single task, and `magi-agent serve` when you want the HTTP API plus local browser app.",
          "`magi-agent start` is kept as a backwards-compatible alias for `magi-agent chat`.",
        ],
        code: {
          title: "Commands",
          lines: [
            "magi-agent init",
            "magi-agent chat",
            "magi-agent start",
            "magi-agent run \"summarize workspace/knowledge\"",
            "cat notes.md | magi-agent run --session notes",
            "magi-agent run --model llama3.1 --plan \"draft an implementation plan\"",
            "magi-agent serve --port 8080",
          ],
        },
        bullets: [
          "`chat`: interactive terminal mode with local slash commands such as `/help`, `/status`, `/compact`, `/reset`, and `/exit`.",
          "`run`: streams one prompt, then exits with a shell-friendly status.",
          "`--session`: reuses a named runtime session and its memory context.",
          "`--model`: overrides the configured model for one task.",
          "`--plan`: starts the run in plan mode before write or execution tools are used.",
          "`serve --port`: starts the self-hosted app at `http://localhost:<port>/app` and the runtime API on the same process.",
        ],
      },
      {
        id: "source-equivalents",
        label: "Source",
        title: "Source-mode equivalents",
        body: [
          "When running from a checkout before npm packaging, replace `magi-agent` with `npx tsx src/cli/index.ts`.",
          "This is useful for runtime development because the command executes the TypeScript source directly.",
        ],
        code: {
          title: "Source mode",
          lines: [
            "npx tsx src/cli/index.ts init",
            "npx tsx src/cli/index.ts chat",
            "npx tsx src/cli/index.ts run \"write a status update from workspace/knowledge\"",
            "cat notes.md | npx tsx src/cli/index.ts run --session notes",
            "npx tsx src/cli/index.ts run --model llama3.1 --plan \"draft an implementation plan\"",
            "npx tsx src/cli/index.ts serve --port 8080",
          ],
        },
      },
      {
        id: "config",
        label: "Config",
        title: "magi-agent.yaml",
        body: [
          "`magi-agent.yaml` is the CLI control file. It selects the provider, model, workspace path, local server token, identity text, built-in hooks, custom hooks, and memory behavior.",
          "The default open-source flow can run against a local OpenAI-compatible endpoint, or you can switch to Anthropic, OpenAI, or Google by changing `llm.provider`, `llm.model`, and the matching API key.",
        ],
        code: {
          title: "Minimal local provider",
          lines: [
            "llm:",
            "  provider: openai-compatible",
            "  model: ${CORE_AGENT_MODEL}",
            "  baseUrl: ${OPENAI_BASE_URL}",
            "  apiKey: ${OPENAI_API_KEY}",
            "",
            "server:",
            "  gatewayToken: ${MAGI_AGENT_SERVER_TOKEN}",
            "",
            "workspace: ./workspace",
            "identity:",
            "  name: \"Magi\"",
            "  instructions: \"You are a helpful coding assistant.\"",
          ],
        },
        bullets: [
          "`workspace` points to the local files, knowledge, memory, artifacts, skills, and harness rules the runtime can use.",
          "`server.gatewayToken` protects the local HTTP API and browser app. Do not reuse a provider API key as the browser token.",
          "`llm.capabilities` can describe local model context window, output size, and price metadata so routing and budget logic are explicit.",
        ],
      },
      {
        id: "scripted-work",
        label: "Automation",
        title: "Scripted and scheduled work",
        body: [
          "Use one-shot mode for shell scripts, cron jobs, and CI-style operator tasks. The runtime still uses the same hooks, contracts, memory, and evidence gates; the only difference is that input and output are terminal-native.",
          "For long-running app workflows or live artifact review, run `magi-agent serve` and use the browser surface.",
        ],
        bullets: [
          "Pipe documents into `run` for summarization, extraction, or transformation.",
          "Use a stable `--session` for recurring workflows that should remember prior context.",
          "Use `--plan` when the task should produce a reviewable plan before tool execution.",
          "Use `serve` when users need the Knowledge panel, artifact downloads, runtime inspector, or chat history.",
        ],
      },
    ],
  },
  {
    slug: "configuration",
    href: "/docs/configuration",
    group: "Configure",
    navLabel: "Configuration",
    title: "Configuration",
    description:
      "Configure providers, routing profiles, Knowledge Base behavior, secrets, tools, and hosted cloud settings.",
    summary:
      "Open Magi should stay provider-neutral. Configuration makes models, tools, memory, and cloud boundaries explicit instead of binding work to one vendor.",
    sections: [
      {
        id: "models",
        label: "Models",
        title: "Provider-neutral model routing",
        body: [
          "Route by capability instead of brand. Use one model for deep reasoning, another for drafting, another for long context, and local compatible endpoints when deployment constraints require it.",
          "Self-hosted installs should keep provider credentials in environment variables. Hosted Open Magi stores cloud credentials in the managed account boundary.",
        ],
        bullets: [
          "Anthropic, OpenAI, and Google are first-class provider families.",
          "OpenAI-compatible endpoints cover local or custom deployments when they expose a compatible API.",
          "Profiles should describe capability, context length, latency, and budget.",
          "A tool-heavy run should prefer models that can reliably call tools.",
        ],
      },
      {
        id: "knowledge-base",
        label: "Knowledge",
        title: "Knowledge Base and memory",
        body: [
          "The Knowledge Base gives agents durable context beyond a single prompt. Use it for documents, project notes, source material, prior decisions, and reusable workspace memory.",
          "Memory should preserve useful decisions without turning every transcript into permanent context.",
        ],
        bullets: [
          "Separate short-term chat context from durable workspace knowledge.",
          "Keep source material attached to factual work so outputs can include evidence trails.",
          "Use hosted Knowledge Base capacity when you want managed storage and search.",
          "Use self-hosted storage when documents must stay inside your own infrastructure.",
        ],
      },
      {
        id: "tools",
        label: "Tools",
        title: "Tools, skills, and channels",
        body: [
          "Tools are part of the runtime contract. Document which tools are available, what credentials they require, and whether they are safe for unattended execution.",
          "Skills and harness rules should be treated as operational configuration because they change what the agent is allowed to consider complete.",
        ],
        bullets: [
          "Enable only the tools required for the workspace.",
          "Keep channel credentials separate from model provider credentials.",
          "Prefer explicit approval gates for tools that mutate external systems.",
          "Record workspace-specific harness rules in source-controlled Markdown when possible.",
        ],
      },
      {
        id: "environment",
        label: "Environment",
        title: "Environment variables",
        body: [
          "Use environment variables for provider keys, server tokens, local URLs, and integration credentials. Avoid prompt-level secrets and committed `.env` files.",
          "When hosted, prefer the cloud settings surface for user-level configuration. When self-hosted, use your deployment secret manager.",
        ],
        code: {
          title: "Example",
          lines: [
            "ANTHROPIC_API_KEY=...",
            "OPENAI_API_KEY=...",
            "GOOGLE_GENERATIVE_AI_API_KEY=...",
            "MAGI_AGENT_SERVER_TOKEN=...",
            "MAGI_AGENT_BASE_URL=http://localhost:8080",
          ],
        },
      },
    ],
  },
  {
    slug: "customization",
    href: "/docs/customization",
    group: "Configure",
    navLabel: "Customization",
    title: "Customization",
    description:
      "Customize Magi Agent with structured harness rules, lifecycle hooks, skill runtime hooks, model profiles, and workspace policy.",
    summary:
      "Customization is how Open Magi becomes an operator-controlled runtime: encode recurring rules as harness gates, attach hooks to lifecycle points, and keep contracts explicit enough for the runtime to block weak completion.",
    sections: [
      {
        id: "where-to-customize",
        label: "Map",
        title: "Customization surfaces",
        body: [
          "Use `magi-agent.yaml` for local CLI configuration, `workspace/agent.config.yaml` for runtime workspace policy, and Markdown harness rule files for reusable operator rules.",
          "The runtime treats these as policy inputs. They are loaded into the policy kernel and hook registry so they can affect execution, not just prompt style.",
        ],
        bullets: [
          "`magi-agent.yaml`: provider, model, workspace, server token, identity, built-in hooks, custom hook paths, and memory.",
          "`workspace/agent.config.yaml`: structured `harness_rules:` and per-workspace runtime toggles.",
          "`workspace/USER-HARNESS-RULES.md`: one local Markdown rule pack with frontmatter.",
          "`workspace/harness-rules/*.md`: downloaded or team-managed Markdown rule packs.",
          "`workspace/skills/*/SKILL.md`: skill instructions plus optional Skill runtime hooks.",
        ],
      },
      {
        id: "structured-harness",
        label: "Harness",
        title: "Structured harness rules",
        body: [
          "User Harness Rules turn workspace expectations into typed gates. A rule has an `id`, `trigger`, optional `when` condition, required action, enforcement mode, and timeout.",
          "`trigger: beforeCommit` runs before the assistant finalizes a response. `trigger: afterToolUse` observes or gates after a matching tool completes.",
        ],
        code: {
          title: "workspace/agent.config.yaml",
          lines: [
            "harness_rules:",
            "  - id: pricing-source-fetch",
            "    enabled: true",
            "    description: \"Pricing answers must use the canonical pricing source.\"",
            "    trigger: beforeCommit",
            "    when:",
            "      user_message_matches: \"(pricing|price|cost|요금|가격|비용)\"",
            "    require:",
            "      tool: \"WebFetch\"",
            "      input_path: \"url\"",
            "      pattern: \"^https://docs\\\\.example\\\\.com/\"",
            "    enforcement: block_on_fail",
            "    timeoutMs: 2000",
            "",
            "  - id: audit-shell-output",
            "    enabled: true",
            "    trigger: afterToolUse",
            "    when:",
            "      tool_name: \"Bash\"",
            "    require:",
            "      type: block",
            "      reason: \"Bash use is temporarily disabled in this workspace.\"",
            "    enforcement: audit",
          ],
        },
        bullets: [
          "`enforcement: block_on_fail` makes the rule a gate that can stop completion.",
          "`enforcement: audit` records the result without blocking the user flow.",
          "`when.user_message_matches` and `when.user_message_includes` scope a rule to user intent.",
          "`when.any_tool_used` scopes a `beforeCommit` rule to prior tool evidence.",
          "`require.tool` checks that a required tool ran.",
          "`require.input_path` plus `pattern` checks that the tool used the expected resource or command.",
        ],
      },
      {
        id: "markdown-rule-packs",
        label: "Rules",
        title: "Markdown rule packs",
        body: [
          "Use Markdown packs when a rule needs human-readable explanation next to machine-readable frontmatter. This is useful for team policy, downloadable rule packs, and examples that should live in source control.",
          "The frontmatter becomes the typed harness rule; the Markdown body becomes explanatory source text for policy status and review.",
        ],
        code: {
          title: "workspace/harness-rules/file-delivery-after-create.md",
          lines: [
            "---",
            "id: user-harness:file-delivery-after-create",
            "trigger: beforeCommit",
            "condition:",
            "  anyToolUsed:",
            "    - DocumentWrite",
            "    - SpreadsheetWrite",
            "    - FileWrite",
            "    - FileEdit",
            "action:",
            "  type: require_tool",
            "  toolName: FileDeliver",
            "enforcement: block_on_fail",
            "timeoutMs: 2000",
            "---",
            "",
            "When a file, document, spreadsheet, report, or artifact is created or modified for the user, deliver it to the chat before claiming completion.",
          ],
        },
        bullets: [
          "Use `action.type: require_tool` for delivery or inspection requirements.",
          "Use `action.type: require_tool_input_match` when the tool must touch a specific source, endpoint, path, or command.",
          "Use `action.type: llm_verifier` for semantic final-answer checks that cannot be expressed as deterministic tool evidence.",
          "Keep verifier prompts narrow and return-oriented: pass or fail with a short reason.",
        ],
      },
      {
        id: "skill-hooks",
        label: "Skills",
        title: "Skill runtime hooks",
        body: [
          "Skills can declare runtime hooks in `SKILL.md` frontmatter. This lets a skill bring its own guardrails for tool use, permission decisions, or final-answer checks.",
          "Skill runtime hooks are intentionally narrower than built-in hooks. They currently target `beforeToolUse`, `afterToolUse`, and `beforeCommit`, require an `if` matcher, and support `block` or `permission_decision` behavior.",
        ],
        code: {
          title: "SKILL.md frontmatter",
          lines: [
            "---",
            "name: guarded-shell",
            "description: Run shell commands only after explicit user confirmation.",
            "runtime_hooks:",
            "  - name: ask-bash",
            "    point: beforeToolUse",
            "    if: \"Bash(*)\"",
            "    action: permission_decision",
            "    decision: ask",
            "    reason: \"Confirm this shell command before it runs.\"",
            "    priority: 45",
            "    blocking: true",
            "---",
          ],
        },
        bullets: [
          "`if` uses the same matcher family as hook registry rules, such as `Bash(*)`, `Read(*.ts)`, `beforeCommit`, or `*`.",
          "`permission_decision` can approve, deny, or ask the user through the runtime's human-in-the-loop flow.",
          "`blocking: true` makes timeout or failure affect the phase unless the hook is configured fail-open by the runtime.",
          "Command hooks from Claude-style skill metadata are normalized into Magi hook points when the command comes from a trusted skill root.",
        ],
      },
      {
        id: "custom-hooks",
        label: "Hooks",
        title: "Custom lifecycle hooks",
        body: [
          "Use hooks for cross-cutting runtime behavior that should run at a lifecycle point: before model calls, after model calls, before tools, after tools, before commit, after commit, compaction, task checkpoints, artifacts, and errors.",
          "A custom hook should be small, deterministic where possible, and easy to test. If it changes phase input, return `replace`; if it must stop the phase, return `block`; if it only records state, make it non-blocking.",
        ],
        code: {
          title: "Conceptual hook shape",
          lines: [
            "export default {",
            "  name: \"require-ticket-reference\",",
            "  point: \"beforeCommit\",",
            "  priority: 80,",
            "  blocking: true,",
            "  timeoutMs: 5000,",
            "  async handler(args, ctx) {",
            "    const contract = ctx.executionContract?.snapshot();",
            "    if (!contract?.taskState.goal?.includes(\"support\")) return { action: \"continue\" };",
            "    if (!/TICKET-[0-9]+/.test(args.assistantText)) {",
            "      return { action: \"block\", reason: \"Final answer must cite the support ticket id.\" };",
            "    }",
            "    return { action: \"continue\" };",
            "  },",
            "};",
          ],
        },
        bullets: [
          "Prefer `beforeCommit` for final-answer gates and evidence checks.",
          "Prefer `beforeToolUse` for permissions and dangerous tool calls.",
          "Prefer `afterToolUse` for evidence recording and resource tracking.",
          "Prefer `beforeLLMCall` for prompt additions such as execution contract or memory injection.",
        ],
      },
      {
        id: "extensible-hooks",
        label: "Extensible",
        title: "Extensible hook system",
        body: [
          "Beyond workspace harness rules, Magi supports full TypeScript hooks that share the same RegisteredHook interface as built-in hooks. Create, test, and manage hooks through the CLI or magi.config.yaml without touching core code.",
          "Hooks are discovered from three directories in priority order: project-local ./hooks/, user-global ~/.magi/hooks/, and installed npm packages under @magi-hooks/*. Name collisions resolve in favor of the higher-priority source.",
        ],
        code: {
          title: "Create and test a custom hook",
          lines: [
            "# Scaffold a new hook",
            "magi hook create my-compliance-check --point beforeCommit",
            "",
            "# List all hooks (built-in + custom)",
            "magi hook list",
            "",
            "# Run fixture-based tests",
            "magi hook test my-compliance-check",
            "",
            "# Enable/disable hooks via config",
            "magi hook enable my-compliance-check",
            "magi hook disable builtin:output-purity-gate",
          ],
        },
        bullets: [
          "Custom hooks use the same RegisteredHook interface as built-in hooks. No adapter layer, no wrapper — full access to HookContext, typed args, and all hook results.",
          "Hook priority bands: 0-10 identity/memory, 10-50 classification/discipline, 50-80 safety gates, 80-95 verification, 100+ user hooks.",
          "magi.config.yaml controls disable_builtin, hook directory, priority/timeout overrides, and custom classifier dimensions.",
          "Custom classifier dimensions append to existing Haiku classifier prompts — zero additional LLM calls regardless of how many dimensions you add.",
          "Natural language rules: describe a rule in plain language (English or Korean) and the CLI generates the hook code, config, and test fixtures.",
        ],
      },
      {
        id: "natural-language-hooks",
        label: "Natural Language",
        title: "Natural language rule builder",
        body: [
          "Non-developers can define hooks by describing rules in plain language. The CLI or HTTP API converts descriptions into TypeScript hooks and classifier dimensions using a fast model.",
          "This bridges the gap between domain experts who know what rules to enforce and developers who know how to implement them.",
        ],
        code: {
          title: "Create hooks from natural language",
          lines: [
            "# English",
            'magi hook create-from-rule "Block responses with drug dosage outside safe ranges"',
            "",
            "# Korean",
            'magi hook create-from-rule "\uD22C\uC790 \uC870\uC5B8\uC774 \uD3EC\uD568\uB41C \uC751\uB2F5\uC5D0 \uBA74\uCC45\uC870\uD56D \uACBD\uACE0 \uCD94\uAC00"',
            "",
            "# HTTP API (for dashboard integration)",
            "POST /api/hooks/from-natural-language",
            '{ "description": "Block responses containing PII", "language": "en" }',
          ],
        },
        bullets: [
          "Auto-detects Korean or English input.",
          "Generates hook TypeScript code, magi.config.yaml snippet, and test fixture.",
          "If the rule is simple pattern matching, also generates a classifier dimension (YAML-only, no code needed).",
          "Categories: safety, compliance, moderation, custom — each with appropriate default priority.",
        ],
      },
      {
        id: "safe-rollout",
        label: "Operate",
        title: "Roll out policy safely",
        body: [
          "Start a new rule in audit mode, review runtime logs and user impact, then switch to block mode once the rule is specific enough.",
          "Avoid workspace-wide rules that match every task unless the behavior is truly universal. Overbroad harness policy can make good work look blocked or force unnecessary user prompts.",
        ],
        bullets: [
          "Use narrow ids so audit logs explain which rule fired.",
          "Prefer regexes that match user intent or tool input rather than broad words.",
          "Keep timeouts short for deterministic checks and longer only for LLM verifier hooks.",
          "Add a local example task that proves the rule passes and one that proves it blocks.",
          "Document team-specific rules next to the workspace so future operators know why they exist.",
        ],
      },
    ],
  },
  {
    slug: "runtime",
    href: "/docs/runtime",
    group: "Operate",
    navLabel: "Runtime",
    title: "Runtime",
    description:
      "How Magi Agent runs work: execution contracts, hooks, progress state, artifacts, memory, and harness rules.",
    summary:
      "The runtime is the difference between a helpful chat response and reliable work. It owns the loop, the checks, and the evidence that a task is actually complete.",
    sections: [
      {
        id: "work-loop",
        label: "Loop",
        title: "Work loop",
        body: [
          "A run reads workspace context, selects a model, calls tools, writes artifacts, reports progress, and checks whether the requested result exists.",
          "The UI should show live progress in plain language so the user can interrupt, redirect, or verify the run before the final answer.",
        ],
        bullets: [
          "Read context from chat, files, Knowledge Base, memory, and channel state.",
          "Select a model profile suited to the work.",
          "Execute tools through the configured runtime boundary.",
          "Write artifacts and evidence before finalizing the answer.",
        ],
      },
      {
        id: "execution-contract",
        label: "Reliability",
        title: "ExecutionContract and first-class contracts",
        body: [
          "ExecutionContract is the runtime's first-class contract object for a task. `ExecutionContractStore` tracks the active goal, constraints, current plan, completed steps, blockers, acceptance criteria, resource bindings, used resources, artifacts, deterministic requirements, and verification evidence.",
          "The contract prevents the system from treating a confident explanation as completed work when the requested file, decision, calculation, source trail, external action, or child-agent result is missing.",
          "On each turn the runtime can parse an explicit task contract from the user message, merge it with prior state, inject the rendered contract into `beforeLLMCall`, and verify completion in `beforeCommit` before the final answer is allowed through.",
        ],
        bullets: [
          "Goal and constraints keep the current work order visible across turns.",
          "Structured acceptance criteria can be pending, passed, failed, or waived.",
          "Resource bindings define allowed workspace paths, source paths, artifact ids, resource ids, and database handles.",
          "Used-resource records tie tool calls to workspace paths, sources, artifacts, external URLs, or handles.",
          "Verification evidence records commands, assertions, exit codes, artifacts, and criteria ids.",
          "Deterministic evidence covers clock, date range, calculation, data query, and verification checks.",
          "Completion claims can be retried or blocked when required criteria remain unmet.",
        ],
      },
      {
        id: "hooks",
        label: "Hooks",
        title: "HookRegistry and lifecycle hooks",
        body: [
          "HookRegistry is the typed extension layer around the run. Every hook registers a `HookPoint`, a handler, priority, timeout, blocking mode, and optional `if` matcher.",
          "`runPre` executes blocking pre-hooks sequentially by priority and lets them continue, replace the phase input, block, skip, or return a `permission_decision`. `runPost` executes observer hooks concurrently and logs failures without bubbling them into the turn.",
          "Hooks receive `HookContext`: bot, user, session, turn, scoped LLM client, transcript, event emitter, structured logger, provider health, source ledger, research contract, and the active `ExecutionContractStore`.",
        ],
        bullets: [
          "`beforeTurnStart` and `afterTurnEnd` frame one user turn.",
          "`beforeLLMCall` and `afterLLMCall` wrap model input and output; contract and memory injection live here.",
          "`beforeToolUse` and `afterToolUse` wrap each tool call; permission and evidence hooks live here.",
          "`beforeCommit`, `afterCommit`, and `onAbort` decide whether a final answer can be committed.",
          "`onError`, `onTaskCheckpoint`, `beforeCompaction`, `afterCompaction`, `onRuleViolation`, and `onArtifactCreated` support reliability, memory, and audit behavior.",
          "`replace` is for controlled mutation, `block` is for user-visible gates, and non-blocking hooks are for telemetry or best-effort enrichment.",
        ],
      },
      {
        id: "harness",
        label: "Rules",
        title: "User Harness Rules",
        body: [
          "User Harness Rules let a workspace encode local expectations as typed runtime policy. They are useful for recurring tasks where the user cares about exact sources, deliverables, approvals, format, or delivery behavior.",
          "Rules can live in `workspace/agent.config.yaml` under `harness_rules:`, in `workspace/USER-HARNESS-RULES.md`, or in `workspace/harness-rules/*.md`. They compile into policy directives and hooks that can audit or block completion.",
          "Use harness rules for things that must be true before done. Use normal instructions for preferences that should guide tone or style but should not block work.",
        ],
        bullets: [
          "`trigger: beforeCommit` checks the final answer and current run state.",
          "`trigger: afterToolUse` reacts after a specific tool or input pattern appears.",
          "`action.type: require_tool` requires tool evidence before completion.",
          "`action.type: require_tool_input_match` requires a tool call to target an approved path, URL, command, or input field.",
          "`action.type: llm_verifier` runs a semantic verifier for final-answer quality gates.",
          "`enforcement: audit` records a result; `enforcement: block_on_fail` makes it a hard gate.",
        ],
      },
      {
        id: "child-harness",
        label: "Delegation",
        title: "ChildAgentHarness and delegated work",
        body: [
          "Child-agent delegation is also contract-driven. `SpawnAgent` creates a bounded work order with persona, goal, constraints, acceptance criteria, resource bindings, allowed tools, delivery mode, and completion contract.",
          "`ChildAgentHarness` tracks the child lifecycle so child work is not treated as a loose promise. Parent and child runs share structured criteria and artifact handoff rules rather than relying on text alone.",
        ],
        bullets: [
          "Use child agents for focused subtasks with concrete output requirements.",
          "Require `completion_contract.required_evidence` such as text, files, artifacts, tool calls, or none only when no evidence is expected.",
          "Use required files and artifact handoff for durable deliverables.",
          "Use background delivery for long work that should surface a result event later.",
          "Keep spawn depth bounded and describe retry or idempotency expectations in the work order.",
        ],
      },
    ],
  },
  {
    slug: "tools",
    href: "/docs/tools",
    group: "Reference",
    navLabel: "Tools",
    title: "Tools",
    description:
      "Reference for the built-in Magi Agent tools: files, shell, search, browser, artifacts, documents, spreadsheets, delegation, cron, and human approval.",
    summary:
      "Tools are the runtime's action surface. Read this page to understand what can read, write, mutate, search, delegate, schedule, or ask the user before you grant a workspace broad permissions.",
    sections: [
      {
        id: "tool-reference",
        label: "Reference",
        title: "Tool reference",
        body: [
          "Magi Agent registers tools from the runtime, then skill tools from the workspace. Core tools are intentionally named so harness rules and hooks can match them deterministically.",
          "Treat this list as the default local runtime surface. Hosted Open Magi may expose the same concepts through managed credentials and cloud-side policy.",
        ],
        bullets: [
          "`FileRead`: read workspace files within the configured root.",
          "`FileWrite`: create or replace workspace files.",
          "`FileEdit`: patch existing workspace files with controlled edits.",
          "`Bash`: run shell commands inside the workspace boundary.",
          "`TestRun`: run verification commands and capture structured outcomes.",
          "`Glob` and `Grep`: discover files and search text before editing.",
          "`CodeWorkspace`: inspect project structure for coding tasks.",
          "`KnowledgeSearch`: query Knowledge Base collections.",
          "`WebSearch`, `web-search`, and `web_search`: search the web through configured search providers.",
          "`WebFetch`: fetch a specific URL as source evidence.",
          "`Browser` and `SocialBrowser`: inspect web pages or social surfaces where enabled.",
          "`Clock`, `DateRange`, and `Calculation`: produce deterministic evidence for dates, ranges, and arithmetic.",
          "`DocumentWrite` and `SpreadsheetWrite`: create rich document and spreadsheet artifacts.",
          "`FileDeliver` and `FileSend`: deliver created files back to a user or channel.",
          "`AskUserQuestion`: pause for human input.",
          "`SpawnAgent`, `TaskList`, `TaskGet`, `TaskOutput`, and `TaskStop`: delegate and manage background work.",
          "`TaskBoard`: track visible work items during complex runs.",
          "`CronCreate`, `CronList`, `CronUpdate`, and `CronDelete`: schedule recurring work.",
          "`ArtifactCreate`, `ArtifactRead`, `ArtifactList`, `ArtifactUpdate`, and `ArtifactDelete`: manage durable artifacts.",
        ],
      },
      {
        id: "permissions",
        label: "Permissions",
        title: "Permission model",
        body: [
          "Each tool has a permission profile and may be marked dangerous. The runtime can ask for approval before sensitive calls, and hooks can return a `permission_decision` with `approve`, `deny`, or `ask`.",
          "Do not rely on names alone for safety. Pair tool permissions with workspace root isolation, server-token auth, scoped credentials, and harness rules for required evidence.",
        ],
        bullets: [
          "Read-only tools should still be scoped to the workspace or approved source domains.",
          "Write-capable tools should usually be covered by delivery or verification gates.",
          "External mutation tools should require human approval unless the workspace explicitly allows unattended operation.",
          "Tool output should be recorded as evidence when the final answer depends on it.",
        ],
      },
      {
        id: "common-patterns",
        label: "Patterns",
        title: "Common tool patterns",
        body: [
          "Reliable work usually chains tools rather than using one powerful call. Read before edit, search before cite, write before claim, deliver before saying a file is attached, and verify before completion.",
        ],
        code: {
          title: "Evidence-oriented sequence",
          lines: [
            "1. Grep or Glob to locate sources.",
            "2. FileRead, KnowledgeSearch, or WebFetch to collect evidence.",
            "3. FileWrite, FileEdit, DocumentWrite, or SpreadsheetWrite to create the deliverable.",
            "4. TestRun, Bash, Clock, DateRange, or Calculation to verify deterministic claims.",
            "5. FileDeliver when the user asked for a downloadable file.",
            "6. beforeCommit hooks check the ExecutionContract before the final answer is committed.",
          ],
        },
      },
      {
        id: "custom-tools",
        label: "Custom",
        title: "Custom tools",
        body: [
          "Define domain-specific tools that become first-class citizens alongside built-in tools. Custom tools use the same Tool interface — no adapter, no wrapper, same permission model, same hook integration.",
          "Tools are discovered from project-local ./tools/, user-global ~/.magi/tools/, and npm packages under @magi-tools/*. Configure and override tools in magi.config.yaml.",
        ],
        code: {
          title: "Create and manage custom tools",
          lines: [
            "# Scaffold a new tool",
            "magi tool create medical-lookup --permission net",
            "",
            "# List all tools (built-in + custom)",
            "magi tool list",
            "",
            "# Test with fixtures",
            "magi tool test medical-lookup",
            "",
            "# Disable a built-in tool",
            "magi tool disable Browser",
          ],
        },
        bullets: [
          "Custom tools implement the same Tool<I, O> interface as built-in tools: name, description, inputSchema, permission, execute().",
          "Permission classes: read, write, execute, net, meta — hooks and harness rules can gate custom tools the same way they gate built-in tools.",
          "Tool SDK helpers: stringProp(), numberProp(), defineInput() for schema building; okResult(), errorResult() for return values; createTestHarness() for testing.",
          "Tools can be marked dangerous: true for operations requiring explicit user consent.",
          "YAML fixtures test tools without running the full agent: define input, expected status, and output assertions.",
        ],
      },
      {
        id: "tool-config",
        label: "Config",
        title: "Tool configuration",
        body: [
          "Use magi.config.yaml to manage tool availability without editing code. Disable built-in tools, set per-tool permission overrides, register npm tool packages, and control timeout behavior.",
        ],
        code: {
          title: "magi.config.yaml",
          lines: [
            "tools:",
            '  directory: "./tools"',
            '  global_directory: "~/.magi/tools"',
            "",
            "  disable_builtin:",
            "    - Browser",
            "    - SocialBrowser",
            "",
            "  overrides:",
            "    medical-lookup:",
            "      permission: net",
            "      timeoutMs: 60000",
            "",
            "  packages:",
            '    - "@magi-tools/pubmed-search"',
            '    - "@magi-tools/legal-search"',
          ],
        },
        bullets: [
          "Loading priority: built-in → disable_builtin removal → project-local → user-global → npm packages → overrides.",
          "Name collisions: project-local tools override global or package tools of the same name.",
          "Per-tool execution logs: ./logs/tools/<toolName>.jsonl with automatic 10MB rotation.",
          "HTTP API: GET /api/tools for listing, POST /api/tools/:name/enable and /disable for runtime control.",
        ],
      },
    ],
  },
  {
    slug: "contracts",
    href: "/docs/contracts",
    group: "Operate",
    navLabel: "Contracts",
    title: "Execution Contracts",
    description:
      "How task contracts, acceptance criteria, verification modes, deterministic evidence, and resource bindings make completion checkable.",
    summary:
      "Execution contracts make the agent's work order explicit. They turn user intent into goals, constraints, criteria, resources, evidence, and gates that hooks can inspect.",
    sections: [
      {
        id: "schema",
        label: "Schema",
        title: "Execution contract schema",
        body: [
          "`ExecutionContractStore` owns the active `ExecutionTaskState`. The state includes the goal, constraints, current plan, completed steps, blockers, criteria, acceptance criteria, resource bindings, used resources, deterministic requirements, deterministic evidence, verification mode, verification evidence, request classifications, final-answer classifications, memory recall, and artifacts.",
          "The contract is not just prompt text. Hooks and tools can update the store, and `beforeCommit` gates can block completion when required evidence is missing.",
        ],
        bullets: [
          "`goal`: the current user-visible objective.",
          "`constraints`: instructions or boundaries that must survive across turns.",
          "`criteria` and `acceptanceCriteria`: required outcomes with pending, passed, failed, or waived status.",
          "`verificationMode`: `none`, `sample`, or `full` depending on risk.",
          "`resourceBindings`: allowed workspace paths, source paths, artifact ids, resource ids, and database handles.",
          "`usedResources`: actual resources touched by tools.",
          "`deterministicRequirements`: clock, date range, calculation, counting, data query, and comparison checks.",
          "`deterministic evidence`: command, calculation, date, data query, assertion, resource, and status records.",
          "`verificationEvidence`: tool, hook, manual, or beforeCommit proof with status and optional exit code.",
        ],
      },
      {
        id: "task-contract",
        label: "Prompt",
        title: "Explicit task_contract blocks",
        body: [
          "Use a task contract when the work has hard requirements. The parser recognizes `<task_contract>` and fields such as `verification_mode`, `acceptance_criteria`, `constraints`, `current_plan`, `completed_steps`, `blockers`, and `artifacts`.",
          "This lets an operator or upstream system provide a contract before the model improvises a plan.",
        ],
        code: {
          title: "Prompt contract",
          lines: [
            "<task_contract>",
            "  <verification_mode>full</verification_mode>",
            "  <constraints>",
            "    <item>Use only files under workspace/reports.</item>",
            "    <item>Do not claim delivery until FileDeliver succeeds.</item>",
            "  </constraints>",
            "  <acceptance_criteria>",
            "    <item id=\"c1\">Create a spreadsheet artifact.</item>",
            "    <item id=\"c2\">Verify totals with Calculation or TestRun.</item>",
            "    <item id=\"c3\">Deliver the output file to chat.</item>",
            "  </acceptance_criteria>",
            "  <resource_bindings>",
            "    <workspace_path>reports/input.csv</workspace_path>",
            "  </resource_bindings>",
            "</task_contract>",
          ],
        },
      },
      {
        id: "completion-gates",
        label: "Gates",
        title: "Completion gates",
        body: [
          "A completion gate is a hook or policy check that compares the final answer with the contract state. The common failure mode it prevents is a model saying work is done before the file, source, delivery, or verification exists.",
          "Use contracts for work products, not stylistic preferences. A tone preference belongs in identity instructions; a required source trail belongs in the contract.",
        ],
        bullets: [
          "File or document work should produce an artifact id, path, and delivery evidence.",
          "Research work should bind source paths or URLs and record source authority.",
          "Coding work should bind changed files and verification commands.",
          "Delegated work should require child-agent evidence or an explicit waiver.",
        ],
      },
    ],
  },
  {
    slug: "hooks",
    href: "/docs/hooks",
    group: "Configure",
    navLabel: "Hooks",
    title: "Hooks",
    description:
      "Lifecycle hook reference for observing, replacing, blocking, asking permission, and recording evidence during a run.",
    summary:
      "Hooks are the main extension layer for policy and reliability. They run around turns, model calls, tools, commits, errors, memory compaction, rule violations, and artifacts.",
    sections: [
      {
        id: "matrix",
        label: "Reference",
        title: "HookPoint matrix",
        body: [
          "`HookPoint` is the closed set of lifecycle points a hook can target. Unknown points are rejected when hooks load so policy stays typed.",
          "`runPre` runs blocking hooks in priority order before the phase. `runPost` runs observer hooks after the phase and logs failures without crashing the run.",
        ],
        bullets: [
          "`beforeTurnStart`: inspect or enrich a new user message.",
          "`afterTurnEnd`: observe committed or aborted turn status.",
          "`beforeLLMCall`: inject contract, memory, or source context before the model call.",
          "`afterLLMCall`: inspect model output and stop reason.",
          "`beforeToolUse`: approve, deny, ask, replace, block, or skip a tool call.",
          "`afterToolUse`: record evidence from tool output.",
          "`beforeCommit`: verify final answer, artifacts, delivery, and acceptance criteria.",
          "`afterCommit`: observe committed assistant text.",
          "`onAbort`: record why the run stopped.",
          "`onError`: capture error code, message, and phase.",
          "`onTaskCheckpoint`: persist turn-level memory after commit.",
          "`beforeCompaction` and `afterCompaction`: customize transcript compaction.",
          "`onRuleViolation`: emit policy violations.",
          "`onArtifactCreated`: react to new artifacts.",
        ],
      },
      {
        id: "results",
        label: "Results",
        title: "HookResult behavior",
        body: [
          "A hook can return nothing or `{ action: \"continue\" }` for normal flow. Pre-hooks can return `replace`, `block`, `skip`, or `permission_decision` when the phase needs intervention.",
          "`permission_decision` is most useful on `beforeToolUse`: approve a tool call, deny it with a reason, or ask the user through the runtime's human-in-the-loop flow.",
        ],
        code: {
          title: "HookResult",
          lines: [
            "{ action: \"continue\" }",
            "{ action: \"replace\", value: nextArgs }",
            "{ action: \"block\", reason: \"Missing source evidence.\" }",
            "{ action: \"skip\" }",
            "{ action: \"permission_decision\", decision: \"ask\", reason: \"Confirm shell command.\" }",
          ],
        },
      },
      {
        id: "custom-hook-example",
        label: "Example",
        title: "Before-commit gate example",
        body: [
          "Use `beforeCommit` when the rule is about whether the final answer is allowed. This example blocks completion unless a required artifact has been recorded.",
        ],
        code: {
          title: "Conceptual hook",
          lines: [
            "export default {",
            "  name: \"require-artifact\",",
            "  point: \"beforeCommit\",",
            "  priority: 80,",
            "  blocking: true,",
            "  async handler(args, ctx) {",
            "    const state = ctx.executionContract?.snapshot().taskState;",
            "    if (!state?.acceptanceCriteria.some((item) => /artifact/i.test(item))) {",
            "      return { action: \"continue\" };",
            "    }",
            "    if (!state.artifacts.length) {",
            "      return { action: \"block\", reason: \"Required artifact was not created.\" };",
            "    }",
            "    return { action: \"continue\" };",
            "  },",
            "};",
          ],
        },
      },
      {
        id: "hook-cli",
        label: "CLI",
        title: "Hook CLI",
        body: [
          "The magi hook CLI manages the full hook lifecycle: create scaffolds, list all hooks with status, enable or disable hooks, run fixture tests, and query execution logs.",
        ],
        code: {
          title: "Hook management commands",
          lines: [
            "magi hook create <name> --point <hookPoint>    # Scaffold hook + fixture",
            "magi hook list                                  # All hooks with priority and status",
            "magi hook enable <name>                         # Enable in magi.config.yaml",
            "magi hook disable <name>                        # Disable (builtin or custom)",
            "magi hook test <name>                           # Run fixtures",
            "magi hook test --all                            # Test all custom hooks",
            "magi hook logs <name> --since 2h --limit 50     # Query JSONL logs",
            'magi hook create-from-rule "Block unsafe content"  # Natural language',
          ],
        },
        bullets: [
          "Scaffold generates RegisteredHook TypeScript, fixture YAML, and inline docs.",
          "Fixtures define input args and expected action/reason for deterministic testing.",
          "Logs are per-hook JSONL files with timestamp, action, reason, duration, and errors.",
          "create-from-rule accepts English or Korean descriptions and generates complete hook code.",
        ],
      },
      {
        id: "custom-classifiers",
        label: "Classifiers",
        title: "Custom classifier dimensions",
        body: [
          "Add domain-specific classification dimensions without code. Custom dimensions are appended to the existing classifier system prompt — zero additional LLM calls regardless of how many dimensions you define.",
          "Hook handlers access custom classification results through ctx.customClassification, a typed map keyed by dimension name.",
        ],
        code: {
          title: "magi.config.yaml classifier section",
          lines: [
            "classifier:",
            "  custom_dimensions:",
            "    medical_safety:",
            '      phase: "request"',
            "      prompt: |",
            "        Does this request involve drug dosage recommendations?",
            "        If so, are the dosages within safe ranges?",
            "      output_schema:",
            "        containsDosage: boolean",
            "        withinSafeRange: boolean",
            '        flaggedDrugs: "string[]"',
            "",
            "    financial_compliance:",
            '      phase: "final_answer"',
            "      prompt: |",
            "        Does the answer contain specific financial advice?",
            "      output_schema:",
            "        containsInvestmentAdvice: boolean",
            "        requiresDisclaimer: boolean",
          ],
        },
        bullets: [
          "phase: request runs during user message classification; final_answer runs before commit.",
          "output_schema defines the JSON shape the model returns — typed as Record<string, unknown> in hooks.",
          "Multiple dimensions per phase are merged into a single LLM call — performance is constant.",
          "Access in hooks: ctx.customClassification?.get('medical_safety')?.containsDosage",
        ],
      },
    ],
  },
  {
    slug: "memory",
    href: "/docs/memory",
    group: "Operate",
    navLabel: "Memory",
    title: "Memory",
    description:
      "How workspace memory, Knowledge Base documents, Hipocampus-style summaries, qmd search, and compaction work together.",
    summary:
      "Memory keeps useful context durable without stuffing every transcript back into the next prompt. Use Knowledge Base for source material and Hipocampus-style memory for learned operational context.",
    sections: [
      {
        id: "model",
        label: "Model",
        title: "Memory model",
        body: [
          "Open Magi separates chat context, workspace files, Knowledge Base documents, and durable memory. This lets agents remember useful decisions while still grounding factual work in retrievable sources.",
          "The runtime can recall memory before a turn, update it at task checkpoints, and compact long transcripts when context pressure rises.",
        ],
        bullets: [
          "Chat transcript: short-term conversational context.",
          "Workspace files: explicit project material and generated artifacts.",
          "Knowledge Base: uploaded or written documents for retrieval.",
          "Hipocampus memory: durable learned notes, decisions, preferences, and operational state.",
          "qmd search: queryable memory/document index for recall.",
          "Compaction: summarize long histories while preserving the useful state.",
        ],
      },
      {
        id: "knowledge",
        label: "Knowledge",
        title: "Knowledge Base operations",
        body: [
          "Use Knowledge Base for source documents the agent should search and cite. In local mode, app routes can list collections, list documents, read files, write files, and search collections.",
          "Hosted Open Magi adds managed storage, quota, and account boundaries around the same concept.",
        ],
        code: {
          title: "API examples",
          lines: [
            "GET /v1/app/knowledge",
            "GET /v1/app/knowledge/search?q=delivery%20evidence&collection=reports",
            "GET /v1/app/knowledge/file?path=knowledge/reports/runtime-proof.md",
            "POST /v1/app/knowledge/file",
          ],
        },
      },
      {
        id: "compaction",
        label: "Compaction",
        title: "Compaction and recall",
        body: [
          "`beforeCompaction` and `afterCompaction` hooks let the runtime inspect transcript reduction. The goal is not to erase context, but to preserve commitments, criteria, artifacts, blockers, and useful decisions in a compact form.",
          "Use memory for facts that should survive sessions. Use task contracts for requirements that must be checked before completion.",
        ],
        bullets: [
          "Keep durable memory short, sourced, and operational.",
          "Do not promote every casual user message into permanent memory.",
          "Prefer Knowledge Base documents for long source material.",
          "Use qmd search when recall needs to span many notes or prior runs.",
        ],
      },
    ],
  },
  {
    slug: "skills",
    href: "/docs/skills",
    group: "Configure",
    navLabel: "Skills",
    title: "Skills",
    description:
      "Create reusable Magi Agent skills with instructions, optional script tools, and runtime hooks.",
    summary:
      "Skills package procedural knowledge. They can add instructions, expose skill-specific tools, and declare runtime_hooks that enforce policy when the skill is active.",
    sections: [
      {
        id: "layout",
        label: "Layout",
        title: "Skill file layout",
        body: [
          "A skill is a directory with a `SKILL.md` file. The frontmatter names the skill and describes when it should be used; the body gives instructions the agent can follow.",
          "Workspace skills live under `workspace/skills/*/SKILL.md`. Built-in templates can be copied into a workspace and modified for team-specific workflows.",
        ],
        code: {
          title: "workspace/skills/release-check/SKILL.md",
          lines: [
            "---",
            "name: release-check",
            "description: Verify a release branch before publishing.",
            "---",
            "",
            "# Release Check",
            "",
            "1. Inspect changed files.",
            "2. Run focused tests.",
            "3. Check docs and migration risk.",
            "4. Summarize evidence before recommending deploy.",
          ],
        },
      },
      {
        id: "runtime-hooks",
        label: "Hooks",
        title: "Skill runtime hooks",
        body: [
          "Skills can declare `runtime_hooks:` in frontmatter. Supported hook points are `beforeToolUse`, `afterToolUse`, and `beforeCommit`. Supported actions are `block` and `permission_decision`; trusted command hooks can also be normalized from Claude-style skill metadata.",
          "Use these hooks when a skill needs a guardrail that should travel with the skill, not just sit in a global workspace policy file.",
        ],
        code: {
          title: "SKILL.md",
          lines: [
            "---",
            "name: shell-review",
            "description: Review shell commands before they run.",
            "runtime_hooks:",
            "  - name: confirm-bash",
            "    point: beforeToolUse",
            "    if: \"Bash(*)\"",
            "    action: permission_decision",
            "    decision: ask",
            "    reason: \"This skill requires confirmation before shell execution.\"",
            "    priority: 50",
            "    blocking: true",
            "---",
          ],
        },
        bullets: [
          "`if` is required so hooks remain scoped.",
          "`decision: ask` routes to the human-in-the-loop flow when available.",
          "`blocking: true` makes failure or timeout affect the phase.",
          "Prefer skill hooks for skill-local safety and harness rules for workspace-wide guarantees.",
        ],
      },
      {
        id: "debugging",
        label: "Operate",
        title: "Loading and debugging skills",
        body: [
          "The app runtime exposes loaded skills, runtime hook counts, and hook issues through runtime and skills endpoints. Use these when a skill does not appear to affect behavior.",
        ],
        code: {
          title: "API examples",
          lines: [
            "GET /v1/app/runtime",
            "GET /v1/app/skills",
            "POST /v1/app/skills/reload",
            "POST /v1/admin/skills/reload",
          ],
        },
      },
    ],
  },
  {
    slug: "automation",
    href: "/docs/automation",
    group: "Operate",
    navLabel: "Automation",
    title: "Automation",
    description:
      "Run scheduled work, background tasks, mission-style goals, and delegated agents with visible state and delivery channels.",
    summary:
      "Automation in Magi Agent is runtime-managed: cron jobs create synthetic turns, background tasks report progress, and delegated agents run with bounded contracts.",
    sections: [
      {
        id: "cron",
        label: "Cron",
        title: "Cron jobs",
        body: [
          "Cron jobs are stored under workspace runtime state and fire as synthetic turns. Each cron captures the delivery channel at creation time so a web-authored job can deliver back to web, and a channel-authored job can return to that channel.",
          "The scheduler tracks `nextFireAt`, `lastFiredAt`, consecutive failures, durable/session scope, and internal system jobs.",
        ],
        code: {
          title: "Runtime tools",
          lines: [
            "CronCreate",
            "CronList",
            "CronUpdate",
            "CronDelete",
          ],
        },
        bullets: [
          "Durable crons survive runtime restart.",
          "Session-scoped crons are swept when the session closes.",
          "Internal crons are hidden from normal bot tooling.",
          "A cron auto-disables after repeated failures.",
        ],
      },
      {
        id: "goals",
        label: "Goals",
        title: "Goal loop and Mission work",
        body: [
          "Use a Goal loop or Mission-style workflow when work needs repeated progress checks rather than one assistant turn. The important design rule is the same as normal tasks: each loop step should update a contract, evidence, or artifact.",
          "Long-running missions should use visible progress, retry limits, and clear stop conditions so they do not turn into unbounded background work.",
        ],
        bullets: [
          "Define the mission goal and constraints before the first turn.",
          "Use TaskBoard when the user needs visible progress.",
          "Emit checkpoints after meaningful progress.",
          "Stop or ask the user when blockers make the next step ambiguous.",
        ],
      },
      {
        id: "delegation",
        label: "Delegation",
        title: "Background tasks and child agents",
        body: [
          "`SpawnAgent` starts delegated work with persona, goal, constraints, acceptance criteria, allowed tools, and completion contract. Task tools inspect, stream, or stop the background job.",
          "Use delegation for independent subtasks with concrete outputs. Do not use it to hide uncertainty in the parent run.",
        ],
        code: {
          title: "Task tools",
          lines: [
            "SpawnAgent",
            "TaskList",
            "TaskGet",
            "TaskOutput",
            "TaskStop",
            "TaskBoard",
          ],
        },
      },
    ],
  },
  {
    slug: "integrations",
    href: "/docs/integrations",
    group: "Configure",
    navLabel: "Integrations",
    title: "Integrations",
    description:
      "Connect the web app, Tauri desktop shell, Telegram, external tools, search, and the runtime API.",
    summary:
      "Integrations should extend the same workspace state. A channel is a surface for the same runtime, not a separate bot with separate memory.",
    sections: [
      {
        id: "surfaces",
        label: "Surfaces",
        title: "Web app and Tauri desktop shell",
        body: [
          "The web app is the primary surface for setup, workspace review, billing, Knowledge Base, and long-running work.",
          "The Tauri desktop shell wraps the same local or hosted app surface for users who want a native window while preserving the same account and workspace state.",
        ],
        bullets: [
          "Use web for the most complete configuration surface.",
          "Use desktop when the user wants a native workspace window.",
          "Keep account, bots, channels, and billing anchored to the same backend state.",
          "Do not fork behavior between web and desktop unless the platform requires it.",
        ],
      },
      {
        id: "channels",
        label: "Channels",
        title: "Telegram and chat channels",
        body: [
          "Channels let users interact with the same workspace from different contexts. Telegram is useful for lightweight requests and notifications, while web chat is better for rich artifacts and long work review.",
          "Channel credentials should be isolated from provider credentials and rotated when access changes.",
        ],
        bullets: [
          "Use web chat for file review, Knowledge Base management, and detailed work progress.",
          "Use Telegram for fast capture, reminders, and lightweight agent interaction.",
          "Preserve channel identity so the runtime can distinguish task context.",
          "Keep channel permissions explicit and revocable.",
        ],
      },
      {
        id: "tools",
        label: "Tools",
        title: "External tools and APIs",
        body: [
          "Tools should be connected through explicit configuration and runtime policy. A tool call that changes external state should be visible, auditable, and interruptible.",
          "When using search, business APIs, cloud storage, or custom endpoints, document the allowed operations for the workspace.",
        ],
        bullets: [
          "Mark read-only tools separately from write-capable tools.",
          "Use approval gates for sensitive external actions.",
          "Record enough tool output to explain what happened after the run.",
          "Prefer least-privilege credentials for each integration.",
        ],
      },
      {
        id: "runtime-api",
        label: "API",
        title: "Runtime API boundary",
        body: [
          "The runtime API should be treated as the boundary between clients and the agent process. Clients ask for state or start work; the runtime owns execution.",
          "Protect local and hosted runtime endpoints with a server token when they are exposed beyond a trusted local process.",
        ],
        code: {
          title: "Common endpoints",
          lines: [
            "GET /v1/app/runtime",
            "GET /v1/app/channel/:channelName/messages",
            "POST /v1/app/channel/:channelName/messages",
            "POST /v1/app/channel/:channelName/run",
          ],
        },
      },
    ],
  },
  {
    slug: "api",
    href: "/docs/api",
    group: "Reference",
    navLabel: "API",
    title: "Runtime API",
    description:
      "Reference for the local app runtime API, chat/SSE endpoints, workspace routes, Knowledge Base routes, tasks, artifacts, crons, skills, and settings.",
    summary:
      "The Runtime API is the boundary clients use to inspect state and start work. The runtime still owns execution, hooks, contracts, memory, and tool calls.",
    sections: [
      {
        id: "runtime-api-reference",
        label: "Reference",
        title: "Runtime API reference",
        body: [
          "`magi-agent serve --port 8080` starts the local HTTP app server. Routes are grouped by app runtime state, chat turns, control requests, settings, health, compliance, MCP, and skill reloads.",
          "When `MAGI_AGENT_SERVER_TOKEN` is configured, protected routes require `Authorization: Bearer <token>`.",
        ],
        code: {
          title: "Core app routes",
          lines: [
            "GET /v1/app/runtime",
            "GET /v1/app/sessions",
            "GET /v1/app/transcript?sessionKey=<key>",
            "GET /v1/app/evidence?sessionKey=<key>",
            "GET /v1/app/skills",
            "POST /v1/app/skills/reload",
            "GET /v1/app/workspace?path=.",
            "GET /v1/app/workspace/file?path=README.md",
            "POST /v1/app/workspace/file",
            "GET /v1/app/workspace/download?path=README.md",
            "GET /v1/app/memory",
            "GET /v1/app/memory/search?q=alpha&limit=3",
            "POST /v1/app/memory/compact",
            "POST /v1/app/memory/reindex",
            "GET /v1/app/knowledge",
            "GET /v1/app/knowledge/search?q=query&collection=reports",
            "GET /v1/app/knowledge/file?path=knowledge/reports/file.md",
            "POST /v1/app/knowledge/file",
            "GET /v1/app/artifacts/:artifactId/content?tier=l0",
            "GET /v1/app/artifacts/:artifactId/download",
            "GET /v1/app/tasks/:taskId/output",
            "POST /v1/app/tasks/:taskId/stop",
            "GET /v1/app/crons",
            "POST /v1/app/crons",
            "PATCH /v1/app/crons/:cronId",
            "DELETE /v1/app/crons/:cronId",
          ],
        },
      },
      {
        id: "chat",
        label: "Chat",
        title: "Chat and control routes",
        body: [
          "Chat turns stream through the OpenAI-compatible SSE route. Control routes let the UI answer human-in-the-loop questions, interrupt a turn, replay control events, and inject queued messages.",
        ],
        code: {
          title: "Turn routes",
          lines: [
            "POST /v1/chat/completions",
            "POST /v1/turns/:turnId/ask-response",
            "GET /v1/control-requests?sessionKey=<key>&channelName=general",
            "GET /v1/control-events?sessionKey=<key>&since=<seq>",
            "POST /v1/chat/inject",
            "POST /v1/chat/interrupt",
            "GET /v1/sessions/:sessionKey/heartbeat",
          ],
        },
        bullets: [
          "Use `POST /v1/chat/completions` for streaming runs.",
          "Use `POST /v1/chat/interrupt` for cooperative stop behavior.",
          "Use ask-response routes only when a pending `AskUserQuestion` or permission decision exists.",
          "Use model override headers or request fields only when the deployment allows runtime model overrides.",
        ],
      },
      {
        id: "settings",
        label: "Settings",
        title: "Config and restart routes",
        body: [
          "Self-hosted operators can inspect and reload runtime config through app settings routes. Restart is intentionally opt-in: the server reports unsupported unless `MAGI_AGENT_RESTART_COMMAND` is configured.",
        ],
        code: {
          title: "Settings routes",
          lines: [
            "GET /v1/app/config",
            "POST /v1/app/config",
            "POST /v1/app/config/reload",
            "POST /v1/app/runtime/restart",
            "GET /v1/app/harness-rules",
            "GET /v1/app/harness-rules/:filename",
            "PUT /v1/app/harness-rules/:filename",
            "DELETE /v1/app/harness-rules/:filename",
          ],
        },
      },
      {
        id: "health",
        label: "Health",
        title: "Health, audit, compliance, and MCP",
        body: [
          "Use health routes for shallow readiness and feature status, audit/compliance routes for operator review, and `/mcp` when connecting an external MCP client.",
        ],
        code: {
          title: "Operational routes",
          lines: [
            "GET /health",
            "GET /healthz",
            "GET /v1/compliance?sessionKey=&since=&until=",
            "GET /v1/audit",
            "GET /v1/parity/evidence",
            "POST /mcp",
            "POST /v1/admin/skills/reload",
          ],
        },
      },
    ],
  },
  {
    slug: "deployment",
    href: "/docs/deployment",
    group: "Operate",
    navLabel: "Deployment",
    title: "Deployment",
    description:
      "Choose local, self-hosted, or Open Magi Cloud deployment and understand which responsibilities move with each option.",
    summary:
      "Start locally, self-host when you need control, and use Open Magi Cloud when you want managed accounts, runtime capacity, billing, Knowledge Base, and operations.",
    sections: [
      {
        id: "local",
        label: "Local",
        title: "Local development",
        body: [
          "Local development is for evaluation, customization, and runtime work. It is not the same as a hardened public deployment.",
          "Use Docker Compose for the fastest local stack and source mode when you need to debug or change runtime code.",
        ],
        code: {
          title: "Local smoke",
          lines: [
            "docker compose up --build",
            `open ${LOCAL_APP_URL}`,
            "npx tsx src/cli/index.ts start",
          ],
        },
      },
      {
        id: "self-host",
        label: "Self-host",
        title: "Self-host hardening",
        body: [
          "Self-hosting means you own network exposure, tokens, provider credentials, storage, updates, backups, and monitoring.",
          "Before exposing a runtime outside localhost, add a server token, TLS, network restrictions, and a process for rotating credentials.",
        ],
        bullets: [
          "Set `MAGI_AGENT_SERVER_TOKEN` for server endpoints.",
          "Put the app behind TLS and a trusted reverse proxy.",
          "Keep provider and channel credentials in a secret manager.",
          "Back up workspace state and Knowledge Base storage.",
          "Monitor runtime logs, tool failures, and restart behavior.",
        ],
      },
      {
        id: "cloud",
        label: "Hosted",
        title: "Cloud boundary",
        body: [
          "Open Magi Cloud hosts the operational layer: accounts, billing, model credits, Knowledge Base capacity, encrypted secrets, runtime capacity, monitoring, and support.",
          "The cloud boundary is useful when teams want the open-source mental model without owning every production system.",
        ],
        bullets: [
          "Use cloud for managed accounts and billing.",
          "Use cloud when runtime capacity or uptime matters more than infrastructure control.",
          "Keep self-hosted for private networks, custom compliance boundaries, or deep runtime modification.",
          "Move between modes by keeping workspace concepts and provider configuration explicit.",
        ],
      },
      {
        id: "release",
        label: "Releases",
        title: "Release discipline",
        body: [
          "Treat runtime releases like infrastructure changes. A change to hooks, completion gates, memory, or tool permissions can alter how work is judged.",
          "Run focused tests for the touched runtime path and smoke the app surface before making the build available to users.",
        ],
        bullets: [
          "Check installation instructions after changing packaging.",
          "Verify machine-readable docs when adding or renaming pages.",
          "Smoke web and desktop surfaces after public navigation changes.",
          "Document deployment impact: web, runtime, database, billing, auth, or infra.",
        ],
      },
    ],
  },
  {
    slug: "security",
    href: "/docs/security",
    group: "Operate",
    navLabel: "Security",
    title: "Security",
    description:
      "Protect model keys, runtime endpoints, channel credentials, Knowledge Base data, and approval-sensitive tools.",
    summary:
      "Security for AI work agents is mostly boundary discipline: keep secrets out of prompts, keep tools least-privilege, and make sensitive actions visible.",
    sections: [
      {
        id: "secrets",
        label: "Secrets",
        title: "Secrets and credentials",
        body: [
          "Provider keys, channel tokens, API credentials, and billing secrets must never be committed or pasted into prompts.",
          "Self-hosted deployments should use environment variables or a secret manager. Hosted cloud stores user secrets inside the managed account boundary.",
        ],
        bullets: [
          "Rotate stale credentials immediately.",
          "Separate read-only and write-capable tool credentials.",
          "Use distinct credentials per workspace or environment when possible.",
          "Do not include secrets in exported artifacts or diagnostic screenshots.",
        ],
      },
      {
        id: "runtime-api",
        label: "API",
        title: "Runtime API boundary",
        body: [
          "Local runtime endpoints are convenient during development, but exposed endpoints need authentication and network controls.",
          "Set `MAGI_AGENT_SERVER_TOKEN` before exposing the runtime beyond localhost, and only place it behind trusted infrastructure.",
        ],
        code: {
          title: "Server token",
          lines: [
            "MAGI_AGENT_SERVER_TOKEN=<your-server-token>",
            "MAGI_AGENT_BASE_URL=https://agent.example.com",
          ],
        },
      },
      {
        id: "permissions",
        label: "Permissions",
        title: "Tools and approval gates",
        body: [
          "A model should not silently mutate external systems. Sensitive tools need clear policy, approval gates, and an audit trail.",
          "Use harness rules to encode the conditions under which a tool can be used or a task can be marked complete.",
        ],
        bullets: [
          "Require approval for payments, messaging, publishing, or destructive actions.",
          "Log tool calls with enough context to review what happened.",
          "Prefer scoped credentials that can only access the resources needed.",
          "Make interruption and retry behavior visible to the user.",
        ],
      },
      {
        id: "data",
        label: "Data",
        title: "Knowledge Base and workspace data",
        body: [
          "Workspace data can contain sensitive source material, customer information, and business context. Treat Knowledge Base storage as production data.",
          "Choose self-hosted storage for strict private boundaries, or hosted cloud when managed encrypted storage and operations are the priority.",
        ],
        bullets: [
          "Know where uploaded files are stored.",
          "Limit who can access workspace memory and Knowledge Base documents.",
          "Keep evidence trails without leaking unnecessary raw data.",
          "Define retention and deletion practices for production workspaces.",
        ],
      },
    ],
  },
  {
    slug: "architecture",
    href: "/docs/architecture",
    group: "Reference",
    navLabel: "Architecture",
    title: "Architecture",
    description:
      "How Open Magi structures clients, runtime state, execution contracts, hooks, memory, and cloud operations.",
    summary:
      "Open Magi is a runtime for reliable work, not just a chat wrapper. It treats state, tools, artifacts, and verification as first-class execution components.",
    sections: [
      {
        id: "layers",
        label: "Layers",
        title: "System layers",
        body: [
          "The system has four practical layers: client surfaces, runtime API, agent runtime, and optional hosted control plane.",
          "Keeping these layers explicit lets the open-source runtime remain portable while the cloud service handles managed operations.",
        ],
        bullets: [
          "Client surfaces: web app, Tauri desktop shell, chat channels, and API consumers.",
          "Runtime API: local or hosted HTTP boundary for app state and work requests.",
          "Agent runtime: model routing, tool execution, hooks, memory, artifacts, and verification.",
          "Cloud control plane: accounts, billing, encrypted secrets, capacity, monitoring, and support.",
        ],
      },
      {
        id: "state",
        label: "State",
        title: "Workspace state",
        body: [
          "Workspace state contains channels, messages, files, memory, tool configuration, and execution history.",
          "The runtime should be able to continue work from prior context without asking the user to restate the entire project.",
        ],
        bullets: [
          "Short-term state supports the current run.",
          "Durable memory preserves useful decisions and facts.",
          "Knowledge Base stores source material for retrieval and evidence.",
          "Artifacts are the deliverables the user can inspect after work completes.",
        ],
      },
      {
        id: "contracts",
        label: "Reliability",
        title: "Execution contracts",
        body: [
          "Execution contracts connect user intent to runtime verification. They define output expectations, constraints, required evidence, and completion criteria.",
          "The architecture should make completion a checked state, not just the final text generated by a model.",
        ],
        bullets: [
          "Completion gates verify that the expected work product exists.",
          "Evidence gates connect claims to source material.",
          "Coding gates connect code changes to tests and file summaries.",
          "Delivery gates ensure requested exports are actually available.",
        ],
      },
      {
        id: "hooks",
        label: "Extensibility",
        title: "HookRegistry",
        body: [
          "HookRegistry is the extension point for lifecycle behavior. Hooks let the runtime enforce policy without hard-coding every workflow into the main loop.",
          "Use hooks for cross-cutting behavior: validation, progress tracking, memory updates, evidence checks, and failure handling.",
        ],
      },
    ],
  },
  {
    slug: "reference",
    href: "/docs/reference",
    group: "Reference",
    navLabel: "Reference",
    title: "Reference",
    description:
      "Quick reference for CLI commands, config files, environment variables, workspace layout, hook names, and operational endpoints.",
    summary:
      "Use Reference when you already understand the model and need exact names: commands, flags, environment variables, files, hook points, and endpoint families.",
    sections: [
      {
        id: "cli-reference",
        label: "CLI",
        title: "CLI command reference",
        body: [
          "The `magi-agent` CLI has no external CLI framework dependency. It parses a small command surface directly: `init`, `chat`, `start`, `run`, `serve`, and `version`.",
        ],
        code: {
          title: "Commands",
          lines: [
            "magi-agent init",
            "magi-agent chat",
            "magi-agent start",
            "magi-agent run [prompt] [--session <key>] [--model <model>] [--plan]",
            "magi-agent serve [--port <1-65535>]",
            "magi-agent version",
            "magi-agent --help",
          ],
        },
      },
      {
        id: "env",
        label: "Environment",
        title: "Environment variables",
        body: [
          "Keep secrets in environment variables or a secret manager. Do not paste provider keys into prompts, docs, screenshots, or committed config files.",
        ],
        code: {
          title: "Common variables",
          lines: [
            "ANTHROPIC_API_KEY",
            "OPENAI_API_KEY",
            "GOOGLE_GENERATIVE_AI_API_KEY",
            "OPENAI_BASE_URL",
            "CORE_AGENT_MODEL",
            "MAGI_AGENT_SERVER_TOKEN",
            "MAGI_AGENT_BASE_URL",
            "MAGI_AGENT_RESTART_COMMAND",
            "BRAVE_SEARCH_API_KEY",
            "TELEGRAM_BOT_TOKEN",
          ],
        },
        bullets: [
          "`MAGI_AGENT_SERVER_TOKEN` protects exposed runtime endpoints.",
          "`MAGI_AGENT_RESTART_COMMAND` enables the self-hosted restart route.",
          "Provider keys should be scoped to the environment and rotated when leaked.",
          "Search and channel keys should be separate from model provider keys.",
        ],
      },
      {
        id: "files",
        label: "Files",
        title: "Workspace file layout",
        body: [
          "The exact open-source layout can evolve, but the runtime concepts are stable: config in `magi-agent.yaml`, source in `src/`, workspace state under the configured workspace root, and generated runtime state under `workspace/core-agent`.",
        ],
        code: {
          title: "Common paths",
          lines: [
            "magi-agent.yaml",
            ".env",
            "workspace/",
            "workspace/agent.config.yaml",
            "workspace/USER-HARNESS-RULES.md",
            "workspace/harness-rules/*.md",
            "workspace/skills/*/SKILL.md",
            "workspace/knowledge/",
            "workspace/memory/",
            "workspace/core-agent/crons/index.json",
            "workspace/core-agent/",
          ],
        },
      },
      {
        id: "hook-reference",
        label: "Hooks",
        title: "Hook point quick reference",
        body: [
          "Hook names are case-sensitive and validated at load time.",
        ],
        code: {
          title: "HookPoint",
          lines: [
            "beforeTurnStart",
            "afterTurnEnd",
            "beforeLLMCall",
            "afterLLMCall",
            "beforeToolUse",
            "afterToolUse",
            "beforeCommit",
            "afterCommit",
            "onAbort",
            "onError",
            "onTaskCheckpoint",
            "beforeCompaction",
            "afterCompaction",
            "onRuleViolation",
            "onArtifactCreated",
          ],
        },
      },
    ],
  },
  {
    slug: "troubleshooting",
    href: "/docs/troubleshooting",
    group: "Reference",
    navLabel: "Troubleshooting",
    title: "Troubleshooting",
    description:
      "Diagnose local startup, auth, missing tools, stale config, memory, Knowledge Base, hook, harness, desktop, and API problems.",
    summary:
      "Troubleshooting starts by locating the boundary: config, server token, runtime process, tool permission, hook policy, Knowledge Base path, or client surface.",
    sections: [
      {
        id: "startup",
        label: "Startup",
        title: "Runtime does not start",
        body: [
          "Check that dependencies are installed, `magi-agent.yaml` exists, and provider configuration is explicit. If source mode fails, run the same command with `--help` or `version` to separate CLI parsing from runtime startup.",
        ],
        code: {
          title: "Checks",
          lines: [
            "npm install",
            "npx tsx src/cli/index.ts --help",
            "npx tsx src/cli/index.ts version",
            "npx tsx src/cli/index.ts init",
            "npx tsx src/cli/index.ts serve --port 8080",
          ],
        },
        bullets: [
          "If config is missing, run `magi-agent init`.",
          "If the port is busy, choose another `serve --port` value.",
          "If a provider call fails, verify the provider key and base URL outside the agent first.",
        ],
      },
      {
        id: "auth",
        label: "Auth",
        title: "API returns 401 or UI cannot inspect runtime",
        body: [
          "A configured bearer token protects runtime routes. Set the same `MAGI_AGENT_SERVER_TOKEN` in the client environment or call routes with `Authorization: Bearer <token>`.",
        ],
        code: {
          title: "Curl",
          lines: [
            "curl -H \"Authorization: Bearer $MAGI_AGENT_SERVER_TOKEN\" http://localhost:8080/v1/app/runtime",
          ],
        },
      },
      {
        id: "hooks",
        label: "Policy",
        title: "Hooks or harness rules block completion",
        body: [
          "A block is usually expected behavior: the runtime found that a required source, artifact, approval, delivery, or verification step is missing. Inspect loaded skills, runtime hooks, and harness rules before weakening policy.",
        ],
        bullets: [
          "Check `GET /v1/app/runtime` for runtime hook counts and policy state.",
          "Check `GET /v1/app/skills` for loaded skill hooks and issues.",
          "Switch new harness rules to `enforcement: audit` while tuning matchers.",
          "Make regexes narrower when unrelated tasks are blocked.",
          "Use `beforeCommit` for final-answer evidence gates, not broad style checks.",
        ],
      },
      {
        id: "memory",
        label: "Memory",
        title: "Memory or Knowledge Base feels stale",
        body: [
          "Separate source problems from recall problems. Knowledge Base search should find source documents; Hipocampus memory should recall durable decisions; compaction should preserve commitments and blockers.",
        ],
        code: {
          title: "Checks",
          lines: [
            "GET /v1/app/knowledge",
            "GET /v1/app/knowledge/search?q=<query>",
            "GET /v1/app/memory",
            "GET /v1/app/memory/search?q=<query>",
            "POST /v1/app/memory/reindex",
            "POST /v1/app/memory/compact",
          ],
        },
      },
      {
        id: "desktop",
        label: "Desktop",
        title: "Desktop app opens the wrong surface",
        body: [
          "The desktop shell should point at the same Open Magi web dashboard or local app URL as the browser. If it opens stale Clawy URLs, check the desktop build config, public app URL, and packaged asset metadata.",
          "macOS warnings that an app is damaged are usually signing/quarantine-related, not a runtime API failure. Rebuild or repackage the desktop artifact, then verify the dashboard URL and icon metadata before publishing a download.",
        ],
      },
    ],
  },
] as const;

export function getDocsPage(slug: DocsPageSlug): DocsPage {
  const page = DOCS_PAGES.find((candidate) => candidate.slug === slug);
  if (!page) {
    throw new Error(`Unknown docs page: ${slug}`);
  }
  return page;
}

export function buildLlmsText(): string {
  const pageIndex = DOCS_PAGES.map(
    (page) => `- ${page.title}: ${PUBLIC_BRAND.siteUrl}${page.href} - ${page.summary}`,
  ).join("\n");

  return [
    `# ${PUBLIC_BRAND.name}`,
    "",
    PUBLIC_BRAND.description,
    "",
    "## Canonical Links",
    "",
    `- Website: ${PUBLIC_BRAND.siteUrl}`,
    `- Source: ${SOURCE_URL}`,
    `- Docs: ${PUBLIC_BRAND.siteUrl}/docs`,
    `- Full agent docs: ${PUBLIC_BRAND.siteUrl}/docs/llms-full.txt`,
    "",
    "## Install",
    "",
    "```bash",
    `git clone ${SOURCE_CLONE_URL}`,
    "cd magi-agent",
    "cp .env.example .env",
    "cp magi-agent.yaml.example magi-agent.yaml",
    "docker compose up --build",
    "```",
    "",
    "## Source Mode And CLI",
    "",
    "```bash",
    "npm install",
    "npx tsx src/cli/index.ts init",
    "npx tsx src/cli/index.ts chat",
    "npx tsx src/cli/index.ts run \"summarize workspace/knowledge\"",
    "npx tsx src/cli/index.ts serve --port 8080",
    "```",
    "",
    "## Docs Index",
    "",
    pageIndex,
    "",
    "## Core Concepts",
    "",
    "- Open-source Magi Agent runtime with hosted Open Magi Cloud when managed infrastructure is preferred.",
    "- Provider-neutral model routing across Claude, GPT, Gemini, local models, and OpenAI-compatible endpoints.",
    "- Knowledge Base for durable workspace context and source-grounded work.",
    "- CLI-native runtime through magi-agent init, chat/start, run, serve, and version.",
    "- ExecutionContractStore, HookRegistry, User Harness Rules, ChildAgentHarness, and evidence gates for reliable completion.",
    "- Tool reference covers FileRead, FileWrite, FileEdit, Bash, KnowledgeSearch, WebSearch, WebFetch, Browser, DocumentWrite, SpreadsheetWrite, FileDeliver, AskUserQuestion, SpawnAgent, TaskBoard, and Cron tools.",
    "- Contracts, hooks, memory, skills, automation, API, reference, and troubleshooting pages mirror the operator depth expected from serious open-source agent docs.",
    "- Runtime API boundary with server-token protection for exposed deployments.",
  ].join("\n");
}

export function buildLlmsFullText(): string {
  const pages = DOCS_PAGES.map((page) => {
    const sections = page.sections.map((section) => {
      const body = section.body.join("\n\n");
      const bullets = section.bullets?.length
        ? `\n\n${section.bullets.map((bullet) => `- ${bullet}`).join("\n")}`
        : "";
      const code = section.code
        ? `\n\n### ${section.code.title}\n\n\`\`\`\n${section.code.lines.join("\n")}\n\`\`\``
        : "";
      const links = section.links?.length
        ? `\n\n${section.links.map((link) => `- [${link.label}](${link.href})`).join("\n")}`
        : "";

      return `## ${section.title}\n\n${body}${bullets}${code}${links}`;
    }).join("\n\n");

    return `# ${page.title}\n\n${page.summary}\n\n${sections}`;
  }).join("\n\n---\n\n");

  return [
    buildLlmsText(),
    "",
    "---",
    "",
    "# Full Docs",
    "",
    pages,
  ].join("\n");
}
