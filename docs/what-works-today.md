# What Works Today

A scannable map of what the local `magi` CLI can actually do right now, what ships in shadow/observe-only mode by design, and what is still planned.

"Default-off" in this project describes the **enforcement/governance layer** — the boundary modules that can block, modify, or gate agent behavior — **not** the agent's ability to do work. With a provider key configured, the local CLI executes real model calls and real first-party tools today. The shadow posture exists because untested enforcement is worse than no enforcement; see [Boundaries](/docs/boundaries).

## Capability map

| ✅ Works today (local CLI + provider key) | 🚧 Default-off / shadow today | ❌ Planned |
|---|---|---|
| Real model calls — 5 providers (Anthropic / OpenAI / Gemini / Fireworks / OpenRouter) via LiteLlm [^1] | Rollout enforcement boundaries (observe-only / local-fake) [^3] | Live enforcement authority attached to traffic (Stage 3) [^3] |
| Pre-final completion/evidence gate — default-ON, blocks coding-turn output [^4] | | |
| First-party local tools: file read/write/edit, patch apply, Bash — on by default [^2] | External channel delivery (Telegram / Discord live send) | Cross-boundary repair orchestration across multiple contracts |
| Permission prompts gating tools (`default` / `acceptEdits` / `bypassPermissions`) [^2] | External integrations (Composio) | Additional external authority for managed systems |
| Sessions, headless NDJSON + interactive TUI | Recipe execution engine (manifests are metadata-only today) | |
| Local HTTP dashboard (`magi-agent serve`) | Always-on gateway daemon — `magi gateway start` supervises the watcher fleet; `--once` for a single tick | |
| | Live web search/fetch — `WebSearch` / `WebFetch` return an honest `web_research_not_configured` error (no simulated results) until a live provider is configured | |

### ✅ Works today

- **Real model calls** across five providers (Anthropic, OpenAI, Gemini/Google, Fireworks, OpenRouter) through LiteLlm, once a provider key (`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GEMINI_API_KEY` | `GOOGLE_API_KEY` / `FIREWORKS_API_KEY` / `OPENROUTER_API_KEY`) or `~/.magi/config.toml` is present. With no key, the CLI still launches against a model-free stub. [^1]
- **First-party local tools** (file read/write/edit, patch apply, Bash) are exposed by default on the real-runner path. [^2]
- **Permission gating** in Claude-Code style: `default`, `acceptEdits`, and `bypassPermissions` modes decide when a tool may run. [^2] When `--permission-mode` is omitted, local CLI runs default to `bypassPermissions`; pass `--permission-mode default` to opt into per-tool approval prompts. [^2]
- **Sessions** plus both the **headless** NDJSON surface and the **interactive TUI** (with `/` [slash commands](/docs/cli-commands)).
- **Local HTTP dashboard** via `magi-agent serve`.

### 🚧 Default-off / shadow today

- **Evidence / governance enforcement boundaries** (the rollout boundary modules) run in observe-only / local-fake mode — the ledger records, but those boundary verdicts are not attached to live traffic. [^3] **One exception that already blocks today:** the pre-final completion/evidence gate (`magi_agent/cli/engine.py`) is default-ON and, on coding turns, blocks output with a `pre_final_evidence_gate_blocked` terminal error when the required evidence is missing and repair cannot satisfy it. [^4]
- **External channel delivery** (Telegram / Discord live send).
- **External integrations** (Composio).
- **Always-on gateway daemon** — `magi gateway start` is a supervising daemon (runs until SIGINT/SIGTERM; `--once` keeps the legacy single scheduler tick), but it is gated by `MAGI_GATEWAY_DAEMON_ENABLED` and each watcher still respects its own gate (e.g. `MAGI_SCHEDULER_EXECUTOR_ENABLED`).
- **Live web search/fetch** (`WebSearch` / `WebFetch`): a default install has no live web provider, so these tools return an honest `web_research_not_configured` error — never simulated results. Once the live-web env gates plus at least one provider (jina-reader / insane-fetch / platform endpoint) are configured, the handlers delegate to the live provider router. See the WebSearch / WebFetch section in [Tools](/docs/tools). [^5]

### ❌ Planned

- Live enforcement authority attached to real traffic and decisions ("Stage 3"). See [Default-Off Gates](/docs/default-off-gates) for the Stage 1/2/3 definitions and promotion criteria.
- Additional external authority for managed systems.

[^1]: `magi_agent/cli/wiring.py::_build_default_runner` selects a real model-backed ADK runner when a provider key or `~/.magi/config.toml` is configured (`magi_agent/cli/real_runner.py`), otherwise falls back to the model-free stub (`magi_agent/cli/local_runner.py`).
[^2]: `magi_agent/cli/wiring.py::_build_first_party_adk_tools` / `_first_party_tools_enabled` (True unless `MAGI_FIRST_PARTY_TOOLS_ENABLED` is off); permission modes in `magi_agent/cli/permissions.py`.
[^3]: `magi_agent/evidence/rollout.py::EvidenceRolloutMetadata` — `traffic_attached` / `execution_attached` are `Literal[False]`, meaning the enforcement boundary is not attached to live traffic/decisions. These flags govern whether the governance layer blocks/gates the agent, not whether the agent can execute tasks.
[^4]: `magi_agent/cli/engine.py::_pre_final_gate_payload` / `_pre_final_gate_applies` — the pre-final gate is evaluated before a turn finalizes. When a coding turn is missing required evidence and the repair loop cannot satisfy it, the engine yields an `EngineResult` with `error="pre_final_evidence_gate_blocked"` and returns, so the turn's output is withheld. `_pre_final_gate_applies` defaults to gating (returns `True` when the dev-coding pack is not explicitly selected) and gates coding turns when it is.
[^5]: `magi_agent/plugins/native/web.py::web_search` / `web_fetch` return the honest `web_research_not_configured` error (`WEB_RESEARCH_NOT_CONFIGURED_ERROR_CODE`) until `build_native_web_boundary` resolves a live provider; the env gates and provider router live in `magi_agent/web_acquisition/research_tools.py`.
