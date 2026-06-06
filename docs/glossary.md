# Glossary

Status: ✅ Active — definitions of the core Magi Agent terms.

Short definitions of the terms used throughout these docs. Each links to the
fuller treatment where one exists.

- **Magi Agent** — the programmable AI agent runtime. It wraps a model and makes
  the state transitions around the model deterministic and configurable.

- **`magi`** — the local command-line interface (headless and interactive TUI).
  See [CLI](/docs/cli).

- **`magi-agent`** — the local HTTP server binary that also serves the web
  dashboard (`magi-agent serve`). Installed by the same Homebrew formula as `magi`.

- **Provider** — the model vendor behind a run: `anthropic`, `openai`, `gemini`,
  or `fireworks`. Selected by a provider API key or `~/.magi/config.toml`.

- **Recipe** — a declarative pack that selects which policies/contracts apply to a
  class of work. Recipes are currently policy/metadata snapshots; see
  [recipes](/docs/recipes) for current execution status.

- **Harness** — reusable enforcement behavior attached to runtime stages. Where a
  prompt asks and a hook observes, a harness owns state and enforces it. See
  [harnesses](/docs/harnesses).

- **Hook** — a callback at a lifecycle point (e.g. `beforeToolUse`, `afterToolUse`,
  `beforeCommit`) that can observe, add context, or block a step. See
  [hooks](/docs/hooks) and the [hook points reference](/docs/hook-points-reference).

- **Tool** — a first-party capability the agent can call (file read/write/edit,
  patch apply, Bash, search, etc.). See [tools](/docs/tools).

- **Skill** — packaged instructions/workflow the agent can load to perform a task.
  See [skills](/docs/skills).

- **ToolHost** — the component that owns tool execution, permission checks, and
  approvals. See [toolhost](/docs/toolhost).

- **Permission mode** — how tool calls are gated: `default` (ask per tool),
  `acceptEdits` (auto-allow edit-class tools), `bypassPermissions` (allow all).

- **Boundary** — a runtime checkpoint that can gate an action (tool, child result,
  memory write, delivery, final answer). Enforcement boundaries ship default-off
  (shadow) today. See [boundaries](/docs/boundaries) and
  [default-off gates](/docs/default-off-gates).

- **Evidence** — durable receipts recorded for actions and claims (source, file,
  calculation, test, approval, delivery). See [evidence](/docs/evidence).

- **Evidence contract** — a declarative requirement that certain claims/actions
  carry specific evidence, with triggers (`afterToolUse`/`beforeCommit`) and an
  `on_missing` policy (`audit` or `block_final_answer`). See
  [evidence contracts](/docs/evidence-contracts).

- **Projection** — the governed rendering of user-visible output, excluding raw
  tool data, private paths, and unsupported claims. See [projection](/docs/projection).

- **Policy snapshot** — the frozen effective rules for a run (allowed tools,
  approvals, evidence rules, repair rules, projection rules).

- **Default-off / shadow** — describes the enforcement/governance layer and
  external delivery/integrations, which start disabled or observe-only. It does
  NOT mean the agent cannot run; see [what works today](/docs/what-works-today).

- **Channel** — an external messaging surface (e.g. Telegram, Discord). Live
  delivery is default-off (shadow) today. See [channels](/docs/channels).

- **Spawn depth** — the nesting level of a run: `0` for the main agent, `>0` for
  child agents. Used to scope which contracts apply.
