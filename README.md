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

Magi builds on Google's Agent Development Kit primitives for agents, runners,
tools, sessions, memory, artifacts, callbacks, plugins, and evaluation. Magi
adds the product contract around those primitives: policy, ToolHost, evidence,
approval, projection, fallback, and audit.

## The Problem

Modern agents are powerful, but prompt-only control is weak for real work.

An agent can say it read a document without reading it. It can cite a source
that does not support the claim. It can skip an approval step, write to the
wrong channel, carry an unsupported intermediate summary into the next step, or
produce something that looks plausible but is hard to trust.

Coding agents worked first for a specific reason: the coding loop ships with a
free **oracle**. Tests, typecheck, the compiler, and CI give a cheap,
deterministic verdict on whether the work is correct. The agent runs them, sees
red, and self-corrects; a human reviews the diff. Most research, finance,
operations, legal, and document work has no such oracle. There is no "run the
tests" for "is this analysis right" or "does this claim hold." That is the gap
Magi closes.

## Two Jobs of a Harness

The scaffolding around a model does two different jobs:

1. **Capability** makes the model *do the work* better: edit matching, format and
   error recovery, context management, the affordances and reliability machinery
   that fill in for a model's mistakes.
2. **Policy enforcement** keeps the work *within rules*: what the agent is allowed
   to do, what must be proven before an answer ships, what is recorded for audit.

Today's coding agents are heavy on (1). They could afford to be light on (2)
because coding's free oracle, plus a human reviewing the diff, already did the
checking.

## What Changes as Models Improve

Two things happen at once, pulling in opposite directions:

- **Capability migrates into the model.** Better models produce precise edits,
  valid tool calls, and hold longer context on their own. The reliability
  machinery that compensates for model mistakes thins out, the way prompt
  engineering and chain-of-thought scaffolding did before it.
- **Policy enforcement becomes the hard part.** When agents run autonomously the
  human leaves the loop, so no person gates each step. When they move past coding
  into knowledge work, there is no free oracle to lean on. In both cases the
  verification has to be *authored* into the runtime, not borrowed from the
  domain or the reviewer.

So the harness does not get lighter. Its weight shifts from *compensating for the
model* to *governing an autonomous one*.

## Why It Must Be Programmable

Policy is not universal. What counts as "allowed", "proven", and "recorded"
varies by domain, task, organization, user, risk appetite, and jurisdiction, and
none of it lives in the model's weights. A single model cannot know your
compliance regime or invent an oracle for your domain. You supply that as
configuration.

So one hardcoded, opinionated harness cannot fit every workflow. The control
surface has to be composable: attach a source-verification policy, an approval
policy, a coding-verification policy, or a reconciliation policy, per task,
without rewriting the agent core. Capability scaffolding, by contrast, is largely
domain-agnostic, which is why one harness could serve every task; policy is
domain-specific, which is why it cannot.

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

**Programmable.** Because that policy is plural and lives outside the weights, it
is composed and configured per task, not hardcoded. The same engine becomes a
strict coding agent or an audit-grade research agent by changing the policy, not
the core.

What this buys you, stated honestly: in a domain with no free oracle, Magi does
not guarantee the answer is *correct* (no gate can; that is the model's job). It
guarantees the answer was produced *under your policy* and can prove it: only
approved sources used, every citation real, no prohibited action taken, a
complete audit trail. It moves trust from "the model is smart" to "the
constraints were met, and here is the proof."

## Install

Install with Homebrew:

```bash
brew update
brew install --force-bottle openmagi/tap/magi-agent
```

Configure a model provider. Set one provider API key, and the runtime auto-detects
which provider you configured:

```bash
export ANTHROPIC_API_KEY=sk-ant-...      # or
export OPENAI_API_KEY=sk-...             # or
export GEMINI_API_KEY=...                # GOOGLE_API_KEY also works, or
export FIREWORKS_API_KEY=...
```

Or persist it in `~/.magi/config.toml`:

```toml
[model]
provider = "anthropic"
api_key  = "sk-ant-..."
# model  = "claude-sonnet-4-6"   # optional; a per-provider default is used otherwise
```

Resolution order: an explicit provider (`[model].provider` or `MAGI_PROVIDER`)
wins; otherwise the first provider with a key is auto-detected, in the order
anthropic → openai → gemini → fireworks. Override the model per run with
`MAGI_MODEL` or `magi --model <id>`.

Start the local API and web dashboard:

```bash
magi-agent serve --port 8080
open http://localhost:8080/dashboard
```

On startup `serve` prints the dashboard URL and whether a provider is configured.
Without a provider key the dashboard still loads and the CLI still launches, but
model replies are stubbed until a key is set.

Use the CLI:

```bash
magi
magi --help
magi -p "Inspect this repository and summarize the runnable surfaces"
magi --output text "Summarize this repository"
```

Both commands are installed by the same formula:

```bash
magi-agent --help
magi-agent serve --help
```

The dashboard is served by the same local agent. It does not need a separate
Node or Next.js process.

`--force-bottle` keeps the install on the prebuilt Homebrew package path. If
Homebrew still tries to build the formula from source on macOS, update the tap
metadata and reinstall the bottle:

```bash
brew update
brew reinstall openmagi/tap/magi-agent --force-bottle
```

## Quickstart (your first task)

The canonical path is Homebrew plus one provider key. Source checkout is for
contributors only.

```bash
# 1. Install
brew install --force-bottle openmagi/tap/magi-agent

# 2. Set ONE provider key (any of these works)
export ANTHROPIC_API_KEY=...   # or OPENAI_API_KEY / GEMINI_API_KEY / GOOGLE_API_KEY / FIREWORKS_API_KEY

# 3. Ask a no-tools question (the real model answers)
magi -p "What is 2+2?"
```

Setting one provider key builds a real model-backed runner. With no key (and no
`~/.magi/config.toml`), the CLI falls back to a model-free stub. The default
model per provider is `claude-sonnet-4-6` (anthropic), `gpt-5.5` (openai),
`gemini-3.5-flash` (gemini), and `accounts/fireworks/models/kimi-k2-instruct`
(fireworks). Model ids drift; override with `MAGI_MODEL` or `[model].model`.

For a task that uses tools (file read/write/edit, patch, Bash), tool execution
is gated by Claude-Code-style permission modes. A local CLI run defaults to
`bypassPermissions` when `--permission-mode` is omitted, so tools can run without
approval prompts. Pass `--permission-mode default` when you want per-tool
approval prompts, or `--mode plan` for read-only planning:

```bash
# Interactive: tools run without approval prompts by default
magi

# Headless: same default-bypass behavior
magi -p "Read README.md and summarize the install steps"
```

Expect the model to answer pure questions directly; for tool-using tasks you
will only see permission prompts when you explicitly choose a prompting mode.

## Front door / where to start

- Getting started: [docs/getting-started.md](docs/getting-started.md)
- Learning path: [docs/learning-path.md](docs/learning-path.md)
- What works today: [docs/what-works-today.md](docs/what-works-today.md)
- CLI reference: [docs/cli/magi.md](docs/cli/magi.md)

## Architecture

Magi controls the loop around ADK. The model sees a bounded context packet and
proposes work; a runtime-only control plane enforces your policy on those
proposals, deciding which become state, evidence, side effects, or user-visible
output. This is the policy-enforcement machinery, not a capability layer: it does
not make the model smarter, it governs what the model's output is allowed to
become.

### The stack

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

### The runtime boundary

The split that makes the layers enforceable is between what the model can see and
what only the runtime controls:

```text
MODEL-VISIBLE LOOP                  RUNTIME-ONLY CONTROL PLANE

User request
    |
    v
Allowed context packet   <--------- Policy snapshot
    |                               tools, approvals, evidence rules,
    v                               repair rules, projection rules
ADK model proposal
    |  action / claim / draft
    v
Boundary checks          ---------> ToolHost / activity boundary
    |                               source, file, delivery, child,
    v                               memory, artifact, workspace
Model can continue       <--------- Receipts + evidence ledger
                                    source spans, approval receipts,
                                    file/test/calculation/delivery proof

Final answer/artifact     <-------- Validators + repair/fallback policy
                                    unsupported claim -> repair, downgrade,
                                    abstain, block, or ask approval

User-visible projection   <-------- Output projector + audit checkpoint
```

### How a run flows

1. **Boot (once per session).** The runtime resolves a profile (which capability
   packs are on) and applies local `~/.magi/customize.json` tool overrides.
2. **Compile (per task).** A task profile selects recipe packs; the compiler and
   materializer bind their `*_refs` to real validators, tools, models, and
   evidence requirements, producing a frozen *plan*.
3. **Execute.** The engine runs the turn. Tool calls pass through the dispatcher,
   which both runs the tool *and* appends an evidence record (default-on).
4. **Verify.** Gates compare the plan's required evidence against the ledger and
   decide: continue, repair, downgrade, abstain, or block. Hard-safety gates
   (permission, path, secret, sealed-file, git) are always on and cannot be
   disabled.

| Stage primitive | Job |
| --- | --- |
| Policy snapshot | Freezes the effective rules (tools, approvals, evidence, repair) for the run |
| Context projector | Decides what the model is allowed to see |
| ADK Runner boundary | Lets the model propose text, actions, and tool calls |
| ToolHost / dispatcher | Owns tool execution, permission checks, and evidence production |
| Evidence ledger | Records source, file, calculation, test, approval, and delivery receipts |
| Validators | Check whether claims and actions satisfy the plan |
| Repair policy | Defines retry, downgrade, fallback, abstention, or block behavior |
| Output projector | Renders only public-safe, policy-compliant output |
| Audit/checkpoint | Preserves digest-safe evidence for review and replay |

### One task, end to end

A concrete trace of the same flow. Read it as: **left = the model proposes
(stochastic); right = the control plane disposes (deterministic).** Every arrow
crossing the line is a controlled handoff.

```text
USER INPUT:  "Research 2024 EU AI Act penalties, deliver a sourced brief.docx"

  MODEL-VISIBLE LOOP                  | RUNTIME-ONLY CONTROL PLANE
 ------------------------------------ + -----------------------------------------
                                      | (1) ROUTE + COMPILE  (per task)
                                      |     task profile -> recipe selection
                                      |     -> a frozen plan: tool / evidence /
                                      |        validator / stage refs  [Recipe]
                                      | (2) RESOLVE HARNESS  (per run context)
                                      |     agent_role / spawn_depth / run_on ->
                                      |     scoped hooks + active contracts  [Harness]
 (3) CONTEXT PACKET  <------------------  built here = the context boundary
     prompt + skills + project ctx    |  (decides what the model may see)
     + ONLY granted tool schemas      |  [Skill] [recipe-scoped Tools]
 (4) MODEL PROPOSES  -------------->   |  text + a tool call (web_search)
     (stochastic)                     |
                                      | (5) PRE-TOOL GATE
                                      |     callbacks/validators check permission;
                                      |     deny, or require approval  [Boundary: tool-perm]
                                      | (6) DISPATCH + PRODUCE EVIDENCE
                                      |     tool runs; at the tool-dispatch boundary
                                      |     an evidence record is appended
                                      |     [Evidence ledger]  default-on, redacted
 (7) ... loop 4-6 ...  <----------->   |  fetch sources, write brief.docx
                                      |
 (8) MODEL: "done"  --------------->   | (9) PRE-FINAL VERIFIER BUS = the gate
                                      |     DETERMINISTIC pure-read over records
                                      |     -- zero model calls --
                                      |       schema ............. pass
                                      |       tool_evidence ...... pass
                                      |       file_artifact ...... pass (brief.docx)
                                      |       source_claim_link .. BLOCK
                                      |            (a claim with no cited source)
                                      |       security_policy .... pass
                                      |       llm_critic ......... (optional, gated)
                                      |     [Stage] [Evidence gate]
                                      | (10) ACTION: block_final_answer
                                      |      -> repair / retry  [Harness loop]
 (11) MODEL re-answers  ----------->   |      (adds the citation) -> re-run 9 -> pass
                                      | (12) PROJECT OUTPUT  (what becomes visible)
 (13) USER SEES brief.docx  <--------  | (14) AUDIT LEDGER  (receipts, verifier events)
```

The gate at step 9 is a pure read over recorded evidence with **no model calls**.
That is what "deterministic control" means in practice. And
`source_claim_link` only fires because this is a *research* plan; on a coding
task that stage is never selected. *Same engine, different plan.*

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

You can enable these surfaces explicitly for the workflows you want to run.
Local development can run the contracts and fixture suites without granting live
tool authority.

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

Scaffold a pack with the CLI:

```bash
magi pack new validator my-check
```

This writes a `pack.toml`, an impl stub, and a generated smoke test under
`<cwd>/.magi/packs/` and prints their paths. Packs are discovered from
`~/.magi/packs/` and `<cwd>/.magi/packs/` with no environment setup. Override or
disable any first-party pack in `~/.magi/config.toml`:

```toml
[packs]
disable = ["openmagi.source-opened"]
```

- [Write your first pack](docs/pack-authoring.md)
- [Pack manifest reference](docs/pack-manifest-reference.md)
- [Typed-context API reference](docs/pack-context-reference.md)

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

## Example: One Task, Up the Stack

The clearest way to see how the layers connect is to grow one bot until each new
requirement forces the next layer. A lawyer wants a contract-review bot. Watch
the *kind* of each request decide which layer answers it, and where it stops
being "ask the model nicely" and becomes a runtime guarantee.

1. **Skill: "review contracts our firm's way."**
   A `review-contract` skill encodes the toxic-clause checklist, house style, and
   review order. No code; pure model-visible guidance. The need is a *procedure*,
   so a skill is enough.

2. **Tool: "you have to actually look up case law."**
   Register a `search_case_law` tool in the registry. A skill cannot add an
   ability; this is one new *capability* the model can call. Execution is
   otherwise unchanged.

3. **Recipe: "for contract review, always assemble this."**
   A `contract_review` pack declares `tool_refs=(search_case_law, read_pdf, …)`,
   a model, a review→verify phase split, and the few-shot and rule-injection for
   the domain. The pack *references* primitives; it does not implement them.
   Selecting it per task is how one runtime becomes a contract-review specialist
   without forking the agent.

4. **Evidence: "a legal opinion should cite its source."**
   The pack adds `evidence_refs=("citation:case-law-source",)`. Every
   `search_case_law` call is appended to the ledger at the dispatch seam
   (default-on), and a gate compares the final answer against the recorded
   sources so unsupported claims can be repaired, weakened, flagged, or blocked.
   This is the same receipts pattern coding uses (read receipts, stale-edit
   rejection, diff/test evidence). The point of this rung is that the requirement
   is *declared and recorded by the runtime*, not left to the prompt. See the
   **Verify Source Before Claim** status note below on where that recording
   becomes a hard block today.

5. **Harness: "client PII must never leak to that external API."**
   Same `search_case_law` call, but a different *kind* of requirement. Not "cite
   your source" (cooperation) but "leaking must be *impossible*, even under a
   prompt injection or a model mistake." No skill, tool, recipe ref, or workflow
   can guarantee this; they all run *as*, or *through*, the (bypassable) model. It
   needs a new non-bypassable mechanism: an egress gate at the dispatch boundary
   that inspects every outbound payload and blocks PII before it leaves. That is
   harness work: a new runtime primitive, authored as a pack
   ([Extending the runtime](#extending-the-runtime)), and a recipe then
   references the new gate to switch it on.

The line is sharp. Rungs 1–4 are "tell the model, or declare from parts that
already exist." Rung 5 is the only one that requires building runtime machinery,
and you reach it only when you need a guarantee that holds **whether or not the
model cooperates, even across turns, even where the model cannot see it.** Most
domain work (legal, finance, research, operations) lives in rungs 1–4.

### Example: Verify Source Before Claim

Rung 4's evidence machinery backs Magi's flagship governance example: a research
task answering only from inspected sources, each claim linked to a source span.
This is also where the honest ceiling of oracle-free verification shows. Three
tiers:

- **Deterministic, hard:** every *cited* source is real and was actually
  inspected (anti-fabrication); only approved sources were fetched (provenance);
  the full chain is in the audit ledger. Zero model calls.
- **Probabilistic, soft:** whether an *un-cited* factual claim slipped in
  (coverage), or whether a citation actually supports its claim (semantics).
  These need an LLM judge: the gate fires deterministically, the judgment does
  not.
- **Out of reach:** whether the agent read a correct source *correctly*. No gate
  guarantees reasoning; that is the model's job.

So the guarantee is provenance and process, not truth. Be precise, too, about how
strong the *implementation* is today:

> **Status:** this is the evidence-governance *model*, not a fresh-install hard
> block. The research final projection gate is **audit-only** (default-OFF): it
> records claims but it **does not block the final answer**. The gate that *does*
> block today is the coding-domain **pre-final** completion/evidence gate
> (default-ON for coding turns). Treat the research rungs as the governance
> model, not as out-of-the-box research blocking.

## Local web dashboard

`magi-agent serve` includes a browser dashboard for local work:

```bash
magi-agent serve --port 8080
open http://localhost:8080/dashboard
```

The dashboard streams public agent events, tool progress, evidence, and SSE
transport state when the runtime emits them. Use it for local research, coding,
document review, planning, and automation experiments without starting a
separate frontend project.

## Why Hooks Alone Are Not Enough

Hooks are useful. They can observe lifecycle events, add context, block a step,
or run checks before and after tool calls.

But strong deterministic guarantees usually require owning runtime state
transitions, not just seeing lifecycle payloads.

For example, take the rung-4 and rung-5 guarantees from the example above
("cite the source" and "PII can never leave") and try to build them as a
third-party hook around an existing agent. A `before_reply` hook may see the
draft answer, but it may not know which intermediate summaries were fed into the
next model call. An `after_tool` hook may see a tool result, but it usually
cannot define a structured source ledger, decide which claims become verified
runtime state, prevent unsupported claims from entering future context, or block
an outbound payload before it is sent. Even if the hook can inspect raw logs, it
has to reconstruct the whole run after the fact, which is expensive and
imprecise.

First-party coding agents can be reliable because their core loop owns state
such as file reads, edits, diffs, test runs, stale-edit checks, and final commit
gates. If that behavior is not built into the agent core, a hook-based extension
can only approximate it from the outside.

Magi exposes that first-party level of control as configurable runtime surfaces:

- model-visible context;
- runtime-only evidence and claim state;
- tool and activity boundaries;
- transition gates;
- repair and fallback behavior;
- governed output projection;
- append-only audit/checkpoint state.

So the harness is not merely "a hook that checks the final answer." It can
declare the state it needs, the evidence it requires, the boundaries where
validation runs, and the transitions that are allowed to continue.

## CLI

The `magi` CLI is the local interface for the same runtime contracts.

```bash
magi
magi --help
magi -p "Inspect this repository and summarize the test surface"
magi --output text "Inspect this repository and summarize the test surface"
magi --output stream-json "Inspect this repository and summarize the test surface"
```

The CLI supports headless output modes for automation and interactive modes for
local operator workflows. The CLI can run local fixture and development paths
without granting live tool authority.

## Optional External Integrations

External integration support, including Composio-backed connector surfaces, is
optional and default-off. Installing optional dependencies or setting a single
API key must not grant live tool authority by itself. Enabling integrations
should require explicit toolkit scope, credential scope, user approval, and
leak-safe evidence before an external action is enabled.

A provider key does enable the real local model plus first-party local tools
(file read/write/edit, patch, Bash, governed by permission modes); what stays
default-off is external delivery/integrations and production enforcement
authority.

Install optional Composio dependencies only when you are developing that surface
from a source checkout.

## Safety Model

High-authority behavior such as live model calls, tool execution, memory writes,
workspace mutation, browser or channel delivery, scheduled work, database
writes, billing mutation, and external integrations should stay behind explicit
configuration, preflight checks, approvals, and durable evidence.

For local CLI use, a provider key plus the permission-mode prompts already give
you a real model and first-party local tools. The default-off authority above
refers to external delivery/integrations and production enforcement boundaries,
not to whether the local agent can run a task.

Operators should treat HTTP success and SSE completion as transport evidence
only. Acceptance for governed workflows comes from durable records: delivery
receipts, source ledgers, mutation receipts, rollback receipts, verifier events,
and audit checkpoints.

## Develop From Source

Homebrew is the normal install path. This section is only for maintainers and
contributors working from a source checkout:

```bash
git clone https://github.com/openmagi/magi-agent.git
cd magi-agent

# install development extras
uv sync --extra dev --extra cli

# run the full scaffold test suite
uv run --extra dev pytest -q

# run the source checkout CLI through uv
uv run --extra cli magi --help
uv run --extra cli magi --output text "Summarize this repository"

# run the local HTTP API and dashboard from source
uv run magi-agent serve --port 8080
```

The local smoke path should not require service secrets, database credentials,
workspace volumes, live ToolHost dispatch, or model provider calls.

## Dependencies

Pinned dependency lines are intentional; no floating latest versions are used.

| Dependency | Version | Purpose |
| --- | ---: | --- |
| `google-adk` | `1.33.0` | Official ADK primitive boundary |
| `fastapi` | `0.136.1` | Health and HTTP route surface |
| `uvicorn` | `0.47.0` | Local/container ASGI server |
| `pydantic` | `2.13.4` | Strict runtime models |
| `pytest` | `9.0.3` | Dev/test runner |
| `httpx` | `0.28.1` | FastAPI test transport |
| `textual` | `8.2.7` | Optional interactive CLI UI |
| `rich` | `15.0.0` | Optional CLI rendering |

Build-system pins:

- `setuptools==80.9.0`
- `wheel==0.45.1`

## More Docs

- CLI reference: `docs/cli/magi.md`
- Getting started: `docs/getting-started.md`
- Learning path: `docs/learning-path.md`
- What works today: `docs/what-works-today.md`
- Runtime architecture: `magi_agent/ARCHITECTURE.md`

## License

Apache-2.0.
