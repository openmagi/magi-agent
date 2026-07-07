<div align="center">

# Open Magi Agent

**A programmable AI agent that does real work under rules you author.**

[Website](https://openmagi.ai) ·
[CLI](docs/cli/magi.md) ·
[What works today](docs/what-works-today.md) ·
[Releases](https://github.com/openmagi/magi-agent/releases)

![ci](https://github.com/openmagi/magi-agent/actions/workflows/ci.yml/badge.svg)
![release](https://img.shields.io/github/v/release/openmagi/magi-agent)
![license](https://img.shields.io/badge/license-Apache--2.0-111827)

</div>

> **Early beta.** Under active development; expect rough edges.

Magi is a governed agent runtime built on ADK. The model proposes work; a
deterministic control plane you program decides which of those proposals become
state, evidence, side effects, or user-visible output. It runs locally, works
with any provider you bring, and is Apache-2.0.

## Install & quickstart

```bash
# 1. Install (Homebrew)
brew install openmagi/tap/magi-agent

# 2. Set whichever provider key you have (auto-detected, one is enough)
export ANTHROPIC_API_KEY=...     # or OPENAI_API_KEY / GEMINI_API_KEY (GOOGLE_API_KEY)
                                 #    / FIREWORKS_API_KEY / OPENROUTER_API_KEY

# 3. Run the CLI, or serve the local dashboard
magi -p "Inspect this repository and summarize the runnable surfaces"
magi-agent serve --port 8080     # then open http://localhost:8080/dashboard
```

One provider key builds a real model-backed runner; with none, the CLI falls
back to a model-free stub. Keys and the model can live in `~/.magi/config.toml`
instead of the environment. Local runs default to `bypassPermissions`; pass
`--permission-mode default` for per-tool approval prompts, or `--mode plan` for
read-only planning.

Two binaries: **`magi`** is the headless / TUI CLI, **`magi-agent`** runs the
server and local dashboard. Full setup, config resolution, flags, and runtime
profiles: [Getting started](docs/getting-started.md) ·
[CLI reference](docs/cli/magi.md).

## What you get today

With a provider key configured, a default local install runs, on by default:

- **Real model calls** across 5 providers (Anthropic, OpenAI, Gemini, Fireworks,
  OpenRouter) via LiteLlm.
- **First-party tools**: file read/write/edit, patch apply, Bash, ripgrep, LSP
  diagnostics, plus a stateful Python interpreter.
- **Subagents** (live child runner) and an in-turn **work queue**.
- **Memory** that captures, recalls, and rolls up across turns, and survives
  restarts via a durable plan ledger + an **ambient goal loop** that keeps going
  on multi-step work.
- **Sessions** with `--resume` / `--continue`, transcript logs, and an
  interactive TUI with `/` slash commands.
- **Keyless `WebFetch`** (jina-reader + local fetch), a browser tool, and a
  **local knowledge base** (`KnowledgeSearch` over your workspace).
- A **local dashboard** (Customize / Policies / Integrations / Credentials /
  Knowledge / Work Queue / Audit) and an encrypted local credential vault.
- The **pre-final evidence gate**, which blocks a coding turn's output when
  required evidence is missing.

The authoritative, source-cited map of what is on, default-off, or planned is
[What works today](docs/what-works-today.md).

## Why: programmable determinism

Prompt-only control is weak for real work: an agent can claim it read a document
it never opened, cite a source that does not support the claim, skip an approval,
or ship a plausible answer with no audit trail. Coding agents worked first
because tests, review, and git already governed the work for free. Most research,
finance, legal, and operations work has no such oracle, so the runtime has to
govern the work itself, and the rules have to become executable.

Magi makes the control *around* the model deterministic, not the model. A policy
you author decides what context the model sees, which tools are allowed or need
approval, which claims require source / file / test / delivery evidence, which
validators run before an answer or side effect, how failures are repaired, and
what lands in the audit ledger. The gate that decides is plain code reading an
append-only evidence record, with zero model calls. Policy is composed per task,
so the same engine becomes a strict coding agent or an audit-grade research agent
by changing the policy, not the core.

Stated honestly: in a domain with no free oracle, Magi does not guarantee the
answer is *correct* (no gate can, that is the model's job). It guarantees the
answer was produced *under your policy* and can prove it: only approved sources
used, every citation real, no prohibited action taken, a complete audit trail.
The full model, per-run flow, and control trace are in
[Architecture](docs/architecture.md) and
[Why the harness, not just hooks](docs/why-the-harness.md).

## Extend it

Everything is authored as a disk pack in one format, loaded through the same path
as the bundled first-party packs: a user pack can add a primitive, override a
first-party ref, or disable a pack, with no privileged handle. You mostly change
a config value or author a doc; you swap an `impl.py` only when a manifest cannot
express the behavior.

| To change… | How | Status |
| --- | --- | --- |
| Procedure, format, tone | drop a `SKILL.md` | ✅ live |
| A tool, validator, evidence producer, callback, control_plane, connector | author a `pack.toml` (+ `impl.py` for code-bearing types) | ✅ live |
| A harness preset (a named bundle of the above) | `pack.toml` `type="harness"` | ✅ live |
| Governance posture: **Policies** (compositions of rules) scoped by **Modes** | the dashboard **Customize** tab, natural language, or `config.toml` | ✅ live |
| A recipe-as-code pack | `pack.toml` `type="recipe"` | ✅ loads by default; LLM recipe *routing* is lab, default-OFF |
| A new verifier stage or lifecycle hook point | upstream change only | immutable core, by design |

You author governance as **Policies** (compositions of rules) applied through
**Modes** (scoped postures), by hand, in natural language, or in the dashboard
Customize tab. See [Customization](docs/customization.md), [Modes](docs/modes.md),
and [Write your first pack](docs/pack-authoring.md) (with the
[manifest](docs/pack-manifest-reference.md) and
[first-party packs](docs/first-party-packs.md) references).

**The immutable core.** Everything above is a swappable, non-privileged pack.
What you *cannot* change is a small immutable core: the engine loop, the
hard-safety gates and their priority floor, the monotonicity rule (a pack may
only *add* constraints, never weaken or bypass a verdict), and the set of
lifecycle hook points. That closed floor is what lets the runtime load anyone's
pack and still keep its guarantees: an external check can make a task stricter,
never neuter it.

## Safety defaults

High-authority behavior (tool execution, memory writes, workspace mutation,
browser/channel delivery, scheduled work, external integrations) stays behind
explicit configuration and durable evidence. A provider key enables the real
local model and first-party local tools; what stays default-off is external
delivery/integrations, computer-use, and production enforcement authority. Treat
HTTP success as transport evidence only; acceptance for governed workflows comes
from durable receipts. Threat model and scoping:
[Security](docs/security.md) · [Integrations](docs/integrations.md).

## Docs

- Getting started: [docs/getting-started.md](docs/getting-started.md)
- What works today: [docs/what-works-today.md](docs/what-works-today.md)
- CLI reference: [docs/cli/magi.md](docs/cli/magi.md)
- Customization & modes: [docs/customization.md](docs/customization.md) · [docs/modes.md](docs/modes.md)
- Architecture: [docs/architecture.md](docs/architecture.md)
- Why the harness, not just hooks: [docs/why-the-harness.md](docs/why-the-harness.md)
- Extending with packs: [docs/pack-authoring.md](docs/pack-authoring.md)
- Source-verified research: [docs/source-verified-research.md](docs/source-verified-research.md)
- Runtime architecture (deep): [magi_agent/ARCHITECTURE.md](magi_agent/ARCHITECTURE.md)
- Contributing / source build: [CONTRIBUTING.md](CONTRIBUTING.md)

## License

Apache-2.0.
