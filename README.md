<div align="center">

# Open Magi Agent

**The programmable AI agent that gets real work done under your rules.**

[Website](https://openmagi.ai) ·
[CLI](docs/cli/magi.md) ·
[Releases](https://github.com/openmagi/magi-agent/releases)

![status](https://img.shields.io/badge/status-early%20beta-f97316)
![ci](https://github.com/openmagi/magi-agent/actions/workflows/ci.yml/badge.svg)
![install](https://img.shields.io/badge/install-Homebrew-2563eb)
![cli](https://img.shields.io/badge/CLI-magi-7c3aed)
![dashboard](https://img.shields.io/badge/dashboard-local-16a34a)
![license](https://img.shields.io/badge/license-Apache--2.0-111827)

</div>

> **Early beta:** Magi Agent is under active development. Expect rough edges.

> **Install once, run locally:** Homebrew installs the runtime, the `magi` CLI,
> and the local web dashboard. Optional external integrations require explicit
> configuration.

Magi Agent is a programmable AI agent runtime that actually gets things done.
Instead of relying on prompts and hoping the model follows every instruction,
Magi lets users configure the runtime around the model: which context it sees,
which tools it can use, what evidence must be recorded, what requires approval,
how failures are repaired, and what can be projected to the user.

## The Problem

Agents are powerful, but prompt-only control is weak for real work. An agent can
say it read a document it never opened, cite a source that does not support the
claim, skip an approval, or ship a plausible answer with no audit trail.

Coding agents worked first because in code the hard parts of governance come
**for free**:

| Governance question | In coding, handled for free by                       |
| ------------------- | ---------------------------------------------------- |
| Is it right?        | Tests and CI: a cheap, deterministic answer key.     |
| Is it allowed?      | A human reviewing the diff and approving the merge.  |
| Can you explain it? | Git: every commit is an audit trail.                 |
| Cost of a mistake?  | Low: branch and revert.                              |

Tests, review, and git did the governing; that free answer key is coding's
**oracle**. Most research, finance, legal, and operations work has none of it.
There is no "run the tests" for "is this analysis right," and no diff to approve.
So the harness has to govern the work itself.

Prompting is not control. The rules have to become executable.

## Two Jobs of a Harness

The scaffolding around a model does two different jobs:

1. **Capability** makes the model *do the work* better: edit matching, format and
   error recovery, context management, the affordances and reliability machinery
   that fill in for a model's mistakes.
2. **Governance** keeps the work *within rules*: what the agent is allowed to do
   (policy), what must be proven before an answer ships (verification), and what is
   recorded (audit). Policy is the configurable instrument; governance is the job.

Today's coding agents are heavy on (1). They could afford to be light on (2)
because coding's free oracle, plus a human reviewing the diff, already did the
checking.

As models improve this inverts. Capability migrates into the model, so the
machinery that compensates for its mistakes thins out. At the same time autonomy
removes the human gate and knowledge work removes the oracle, so governance has to
be *authored* into the runtime. The harness does not get lighter; its weight
shifts from compensating for the model to governing an autonomous one.

## The Solution: Programmable Determinism

Magi adds the missing structure at runtime, as a control plane you program. It
has two properties, and both carry weight.

**Deterministic.** We do not make the *model* deterministic; it stays creative,
and can be incomplete or wrong. We make the *control around it* deterministic:
which of the model's proposals become state, evidence, side effects, or
user-visible output. The component that decides is plain code reading an
append-only evidence record, with zero model calls in the gate. The model
proposes; the control plane disposes.

Concretely, a policy defines:

- what context is visible to the model;
- which tools are allowed and which require approval;
- which actions must write receipts;
- which claims require source, file, calculation, test, or delivery evidence;
- which validators run before a tool call, child result, memory write, final
  answer, artifact, or external delivery;
- how the runtime repairs, retries, downgrades, falls back, or abstains;
- what becomes user-visible output;
- what is recorded in the audit ledger.

**Programmable.** Policy is not universal: what counts as allowed, proven, and
recorded varies by domain, task, organization, and jurisdiction, and none of it
lives in the model's weights. So it is composed and configured per task, not
hardcoded, and the same engine becomes a strict coding agent or an audit-grade
research agent by changing the policy, not the core. Capability scaffolding, by
contrast, is domain-agnostic, which is why one harness can serve every task;
policy is domain-specific, which is why it cannot.

What this buys you, stated honestly: in a domain with no free oracle, Magi does
not guarantee the answer is *correct* (no gate can; that is the model's job). It
guarantees the answer was produced *under your policy* and can prove it: only
approved sources used, every citation real, no prohibited action taken, a
complete audit trail. It moves trust from "the model is smart" to "the
constraints were met, and here is the proof."

## Install & Quickstart

```bash
# 1. Install (Homebrew)
brew install --force-bottle openmagi/tap/magi-agent

# 2. Set ONE provider key (auto-detected)
export ANTHROPIC_API_KEY=...   # or OPENAI_API_KEY / GEMINI_API_KEY / GOOGLE_API_KEY / FIREWORKS_API_KEY / OPENROUTER_API_KEY

# 3. Run the CLI, or serve the local dashboard
magi -p "Inspect this repository and summarize the runnable surfaces"
magi-agent serve --port 8080        # then open http://localhost:8080/dashboard
```

Setting one provider key builds a real model-backed runner; with none, the CLI
falls back to a model-free stub. You can persist the key and model in
`~/.magi/config.toml` instead of the environment. Tool execution is gated by
Claude-Code-style permission modes (`--permission-mode default` to approve each
tool, `--mode plan` for read-only planning).

The **Local web dashboard** runs at http://localhost:8080/dashboard; run
`magi-agent serve --help` for serve options. If Homebrew tries to build from
source instead of using the prebuilt bottle, reinstall it:
`brew reinstall openmagi/tap/magi-agent --force-bottle`.

Runtime profiles are selected by `MAGI_RUNTIME_PROFILE`. To dogfood the full
experimental feature set, run with `MAGI_RUNTIME_PROFILE=lab` (or persist it as
the only line in `~/.magi/profile.env`); it survives `brew upgrade`. See
[Getting started → Runtime profiles](docs/getting-started.md#runtime-profiles).

Full setup, config resolution, and CLI flags:
[Getting started](docs/getting-started.md) · [CLI reference](docs/cli/magi.md).

## Architecture

Magi controls the loop around ADK. The model sees a bounded context packet and
proposes work; a runtime-only control plane enforces your policy on those
proposals, deciding which become state, evidence, side effects, or user-visible
output. This is the governance machinery, not a capability layer: it does not
make the model smarter, it governs what the model's output is allowed to become.

Magi is not one loop. It is five layers, each answering a different *kind* of
need. The rule is simple: **reach for the lowest layer that can express what you
want, and only climb when that layer genuinely cannot.**

| Layer | Role | Reach for it when | Lives in |
| --- | --- | --- | --- |
| **Skill** | Behavior guidance: procedure, format, tone (no code) | "Do the work *this way*" | `SKILL.md` files |
| **Tool** | A capability the model can call | "The agent needs a new *ability*" | tool registry (`tools/`) |
| **Recipe** | Per-task composition: which tools, validators, evidence, phases, and model to attach | "For *this class of task*, always assemble this" | recipe packs (`recipes/`, incl. `first_party/`) |
| **Evidence** | Append-only proof record + enforcement gates | "This claim or action must be *proven*, or blocked" | evidence ledger (`evidence/`) |
| **Harness** | The execution machinery and runtime primitives a recipe references | "A guarantee the model cannot be *trusted* to keep" | runtime engine + gates (`harness/`) |

How they relate: a **Recipe** is a bill of materials. It references **Tool**,
**Evidence**, and **Harness** primitives *by name* (`tool_refs`,
`validator_refs`, `evidence_refs`, …); it does not implement them. The
**Harness** owns the engine, gates, loops, and schedulers; that is where the
referenced primitives actually live. The **Evidence** ledger is a separate,
always-on record produced at the tool-dispatch boundary, which gates consume.
**Skill** and project context ride on top as model-visible guidance. (Every one
of these is authored as a disk pack in the same format; see
[Extending the runtime](#extending-the-runtime).)

Because the layers are orthogonal, each degrades on its own:

- swap only the Recipe → same engine, different domain (a contract-review bot and
  a coding bot are the *same runtime, different manifest*);
- run with no opinionated packs ("vanilla") → the *enforcement and verification*
  levers go quiet, but prompt, tool, and model levers still work;
- turn evidence *enforcement* off → records are still written (the dispatch-seam
  producer is default-on); they are simply not gated.

The model-visible vs runtime-only boundary, the per-run flow, and a full
end-to-end control trace ("the model proposes, the control plane disposes") are
in [Architecture](docs/architecture.md). The case for owning runtime state
instead of bolting on hooks is in
[Why the harness, not just hooks](docs/why-the-harness.md).

## First-Party Harnesses

These are pre-built **policy** bundles (Recipes wiring Harness-layer primitives)
for a common work class, so you do not assemble one from scratch. They differ by
what each domain makes verifiable: coding's checkpoints (read-before-edit,
diff/test evidence) lean on the domain's free oracle, while research, general
automation, and authority boundaries are verification Magi has to author. Magi
ships first-party policy for the work classes that need deterministic
checkpoints:

- research-first source inspection, citation, verifier, rule-check, and final
  projection;
- coding read-before-edit, patch/diff/test evidence, mutation receipts, rollback
  receipts, and false-success blocking;
- general automation queueing, approval, and delivery boundaries;
- memory, scheduler, mission, channel, and browser authority boundaries;
- document and spreadsheet authoring evidence;
- child-agent, delegation, fork, replay, and compaction continuity;
- evidence-first projection and audit reporting.

You can enable these surfaces explicitly for the workflows you want to run. Local
development can run the contracts and fixture suites without granting live tool
authority. For the full pack list see
[First-party packs](docs/first-party-packs.md).

## Verify Source Before Claim

The flagship governance example is a research task that answers only from
inspected sources, each claim linked to a source span. It illustrates the
evidence-governance model; the full walkthrough and the three-tier guarantee
ceiling live in [Source-verified research](docs/source-verified-research.md).

> **Status:** this is the evidence-governance *model*, not a fresh-install hard
> block. The research final-projection gate is **audit-only** (default-OFF): it
> records claims but **does not block the final answer**. The gate that *does*
> block today is the coding-domain **pre-final** completion/evidence gate
> (default-ON for coding turns). Treat the research rungs as the governance
> model, not as out-of-the-box research blocking.

## Extending the runtime

The harnesses above are a starting point, not a ceiling. Every primitive seam
(tool, callback, validator, harness, control_plane, evidence_producer, recipe,
connector) is authored as a disk pack. First-party ships as bundled packs in the
same format, loaded through the same path as yours: a user pack can add a new
primitive, override a first-party ref, or remove a first-party pack entirely.
Each implementation receives the same narrow typed context first-party receives;
there is no privileged handle.

At a glance: what you can change, how, and what is live today:

| To change…                                                              | How (compose = author a pack · configure = set a value) | Status |
| ----------------------------------------------------------------------- | ------------------------------------------------------- | ------ |
| Procedure, format, tone                                                 | drop a `SKILL.md`                                       | ✅ live |
| A tool, validator, evidence producer, callback, control_plane, connector | author a `pack.toml` (+ `impl.py` for code-bearing types) | ✅ live |
| A harness preset (a named bundle of the above)                          | author a `pack.toml` `type="harness"`                  | ✅ live |
| Which packs are on; enforcement strength                                | `~/.magi/config.toml` or a flag                        | ✅ live |
| A recipe: the per-task assembly of tools/evidence/validators           | author a `pack.toml` `type="recipe"`                   | 🚧 authored & discovered today; runtime selection landing (default-OFF) |
| An agent role: a scope label for packs/hooks                           | author a `pack.toml` `type="role"`                     | 🚧 landing (default-OFF) |
| A new verifier stage or lifecycle hook point                            | upstream change only                                   | immutable core, by design |

Legend: ✅ live · 🚧 implemented, landing behind a default-OFF flag. Items marked
"immutable core" are part of the fixed core (see [The immutable core](#the-immutable-core)).

Scaffolding (`magi pack new`), discovery (`~/.magi/packs/`, `<cwd>/.magi/packs/`),
and overriding or disabling a first-party pack (`[packs] disable` in
`~/.magi/config.toml`) are covered in
[Write your first pack](docs/pack-authoring.md), with the
[manifest](docs/pack-manifest-reference.md) and
[typed-context](docs/pack-context-reference.md) references.

For most needs you never write code: **change a config value** (`config.toml` /
a flag), **author a doc** (a `SKILL.md` or a `pack.toml`), or **swap a file** (a
pack's `impl.py`) only when a manifest cannot express the behavior.

### The immutable core

Everything above, including the **first-party harnesses and recipes**, is a
swappable, non-privileged pack loaded through the same path as yours. What you
*cannot* change is a small **immutable core**: the **engine loop**, the
**hard-safety gates** and their priority floor, the **monotonicity rule** (a pack
may only *add* constraints, never weaken or bypass a verdict), and the set of
lifecycle **hook points**. That immutable core is the trusted base. It is exactly
what lets the runtime load anyone's pack and still keep its guarantees: an
external check can make a task stricter, never neuter it. This is the closed floor
under the open seam: programmable policy on top, fixed enforcement underneath.

## Open and Neutral

Magi runs on any model you bring (Anthropic, OpenAI, Google, Fireworks,
OpenRouter, local) and is Apache-2.0. That is not just licensing hygiene; it is
the point. Vendor coding agents are locked to one model. OpenCode opened that up,
but only for code. Magi takes the neutral seat for every kind of work, and a
single-model vendor structurally will not fill it: a model maker has little reason
to build a model-neutral governance layer that commoditizes its own model. The
enforcement floor you depend on should not be owned by the same party that
supplies the model it governs.

## Safety Model

High-authority behavior (live model calls, tool execution, memory writes,
workspace mutation, browser/channel delivery, scheduled work, database/billing
writes, external integrations) stays behind explicit configuration, approvals,
and durable evidence. A provider key enables the real local model and first-party
local tools (file read/write/edit, patch, Bash, under permission modes); what
stays default-off is external delivery/integrations and production enforcement
authority. Treat HTTP success and SSE completion as transport evidence only;
acceptance for governed workflows comes from durable receipts and audit
checkpoints.

Threat model, permission modes, and integration scoping:
[Security](docs/security.md) · [Integrations](docs/integrations.md).

## Docs

- Getting started: [docs/getting-started.md](docs/getting-started.md)
- Learning path: [docs/learning-path.md](docs/learning-path.md)
- What works today: [docs/what-works-today.md](docs/what-works-today.md)
- CLI reference: [docs/cli/magi.md](docs/cli/magi.md)
- Architecture: [docs/architecture.md](docs/architecture.md)
- Why the harness, not just hooks: [docs/why-the-harness.md](docs/why-the-harness.md)
- Extending with packs: [docs/pack-authoring.md](docs/pack-authoring.md)
- Runtime architecture (deep): [magi_agent/ARCHITECTURE.md](magi_agent/ARCHITECTURE.md)
- Contributing / source build: [CONTRIBUTING.md](CONTRIBUTING.md)

## License

Apache-2.0.
