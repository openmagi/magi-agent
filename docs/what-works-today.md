# What Works Today

A scannable map of what the local `magi` CLI can actually do right now, what ships in shadow/observe-only mode by design, and what is still planned.

"Default-off" in this project describes the **enforcement/governance layer** — the boundary modules that can block, modify, or gate agent behavior — **not** the agent's ability to do work. With a provider key configured, the local CLI executes real model calls and real first-party tools today. The shadow posture exists because untested enforcement is worse than no enforcement; see [Boundaries](/docs/boundaries).

## Capability map

| ✅ Works today (local CLI + provider key) | 🚧 Default-off / shadow today | ❌ Planned |
|---|---|---|
| Real model calls — 4 providers (Anthropic / OpenAI / Gemini / Fireworks) via LiteLlm [^1] | Evidence / governance enforcement boundaries (observe-only / local-fake) [^3] | Live enforcement authority attached to traffic (Stage 3) [^3] |
| First-party local tools: file read/write/edit, patch apply, Bash — on by default [^2] | External channel delivery (Telegram / Discord live send) | Cross-boundary repair orchestration across multiple contracts |
| Permission prompts gating tools (`default` / `acceptEdits` / `bypassPermissions`) [^2] | External integrations (Composio) | Additional external authority for managed systems |
| Sessions, headless NDJSON + interactive TUI | Recipe execution engine (manifests are metadata-only today) | |
| Local HTTP dashboard (`magi-agent serve`) | Always-on gateway daemon — `magi gateway start` supervises the watcher fleet; `--once` for a single tick | |
| | Live web search/fetch — `WebSearch` / `WebFetch` return an honest `web_research_not_configured` error (no simulated results) until a live provider is configured | |

### ✅ Works today

- **Real model calls** across four providers (Anthropic, OpenAI, Gemini/Google, Fireworks) through LiteLlm, once a provider key (`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GEMINI_API_KEY` | `GOOGLE_API_KEY` / `FIREWORKS_API_KEY`) or `~/.magi/config.toml` is present. With no key, the CLI still launches against a model-free stub. [^1]
- **First-party local tools** (file read/write/edit, patch apply, Bash) are exposed by default on the real-runner path. [^2]
- **Permission gating** in Claude-Code style: `default`, `acceptEdits`, and `bypassPermissions` modes decide when a tool may run. [^2] Note: a headless one-shot `magi -p` in `default` mode with `--output text` has no surface to answer approvals, so tool calls are denied — use the interactive TUI, `--permission-mode acceptEdits`/`bypassPermissions`, or a `stream-json` responder to let tools run. [^2]
- **Sessions** plus both the **headless** NDJSON surface and the **interactive TUI** (with `/` [slash commands](/docs/cli-commands)).
- **Local HTTP dashboard** via `magi-agent serve`.

### 🚧 Default-off / shadow today

- **Evidence / governance enforcement boundaries** run in observe-only / local-fake mode. The ledger records, but no boundary verdict blocks output or side effects. [^3]
- **External channel delivery** (Telegram / Discord live send).
- **External integrations** (Composio).
- **Always-on gateway daemon** — `magi gateway start` is a supervising daemon (runs until SIGINT/SIGTERM; `--once` keeps the legacy single scheduler tick), but it is gated by `MAGI_GATEWAY_DAEMON_ENABLED` and each watcher still respects its own gate (e.g. `MAGI_SCHEDULER_EXECUTOR_ENABLED`).
- **Live web search/fetch** (`WebSearch` / `WebFetch`): a default install has no live web provider, so these tools return an honest `web_research_not_configured` error — never simulated results. Once the live-web env gates plus at least one provider (jina-reader / insane-fetch / platform endpoint) are configured, the handlers delegate to the live provider router. See the WebSearch / WebFetch section in [Tools](/docs/tools).

### ❌ Planned

- Live enforcement authority attached to real traffic and decisions ("Stage 3").
- Additional external authority for managed systems.

[^1]: `magi_agent/cli/wiring.py::_build_default_runner` selects a real model-backed ADK runner when a provider key or `~/.magi/config.toml` is configured (`magi_agent/cli/real_runner.py`), otherwise falls back to the model-free stub (`magi_agent/cli/local_runner.py`).
[^2]: `magi_agent/cli/wiring.py::_build_first_party_adk_tools` / `_first_party_tools_enabled` (True unless `MAGI_FIRST_PARTY_TOOLS_ENABLED` is off); permission modes in `magi_agent/cli/permissions.py`.
[^3]: `magi_agent/evidence/rollout.py::EvidenceRolloutMetadata` — `traffic_attached` / `execution_attached` are `Literal[False]`, meaning the enforcement boundary is not attached to live traffic/decisions. These flags govern whether the governance layer blocks/gates the agent, not whether the agent can execute tasks.
