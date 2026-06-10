<div align="center">

# Open Magi Agent

**The programmable AI agent that gets real work done under your rules.**

[Website](https://openmagi.ai) ·
[CLI](docs/cli/magi.md) ·
[Releases](https://github.com/openmagi/magi-agent/releases)

![ci](https://github.com/openmagi/magi-agent/actions/workflows/ci.yml/badge.svg)
![status](https://img.shields.io/badge/status-early%20beta-f97316)
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

Coding agents worked because the coding loop is unusually structured: read
files, edit, diff, typecheck, test, and commit. The workflow itself gives the
agent deterministic checkpoints. Most research, operations, finance, document
review, and general automation work does not have that structure by default.

Magi adds that structure at runtime.

## The Solution: Composable Determinism

Magi does not make the model deterministic. The model can still be creative,
incomplete, or wrong. Magi makes the state transitions around the model
deterministic.

A workflow can define:

- what context is visible to the model;
- which tools are allowed and which require approval;
- which actions must write receipts;
- which claims require source, file, calculation, test, or delivery evidence;
- which validators run before a tool call, child result, memory write, final
  answer, artifact, or external delivery;
- how the runtime repairs, retries, downgrades, falls back, or abstains;
- what becomes user-visible output;
- what is recorded in the audit ledger.

The important part is that this behavior is composable. A user or project team
can attach a source-verification harness, an approval harness, a coding
verification harness, a spreadsheet reconciliation harness, or a meta-agent
inspection harness without rewriting the agent core for every workflow.

## Install

Install with Homebrew:

```bash
brew update
brew install --force-bottle openmagi/tap/magi-agent
```

Configure a model provider. Set one provider API key — the runtime auto-detects
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

# 3. Ask a no-tools question — the real model answers
magi -p "What is 2+2?"
```

Setting one provider key builds a real model-backed runner. With no key (and no
`~/.magi/config.toml`), the CLI falls back to a model-free stub. The default
model per provider is `claude-sonnet-4-6` (anthropic), `gpt-5.5` (openai),
`gemini-3.5-flash` (gemini), and `accounts/fireworks/models/kimi-k2-instruct`
(fireworks). Model ids drift; override with `MAGI_MODEL` or `[model].model`.

For a task that uses tools (file read/write/edit, patch, Bash), tool execution
is gated by Claude-Code-style permission modes. Headless `-p` runs use the
`default` mode, which asks per tool and cannot auto-resolve those asks without an
input stream, so use `acceptEdits` (or run the interactive TUI and approve):

```bash
# Interactive: approve tool use when prompted
magi

# Headless: auto-allow edit-class tools
magi -p --permission-mode acceptEdits "Read README.md and summarize the install steps"
```

Expect the model to answer pure questions directly; for tool-using tasks you
will see permission prompts unless you pass `--permission-mode acceptEdits` (or
`bypassPermissions`).

## Front door / where to start

- Getting started: [docs/getting-started.md](docs/getting-started.md)
- Learning path: [docs/learning-path.md](docs/learning-path.md)
- What works today: [docs/what-works-today.md](docs/what-works-today.md)
- CLI reference: [docs/cli/magi.md](docs/cli/magi.md)

## Architecture

Magi controls the loop around ADK. The model sees a bounded context packet and
proposes work. Runtime-only policy, evidence, validation, and projection state
decide which proposals can continue.

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

| Component | Job |
| --- | --- |
| Workflow config | Selects the runtime policy for a class of work |
| Harness | Adds reusable enforcement behavior to runtime stages |
| Policy snapshot | Freezes the effective rules for the current run |
| Context projector | Decides what the model is allowed to see |
| ADK Runner boundary | Lets the model propose text, actions, and tool calls |
| ToolHost | Owns tool execution, permission checks, and approvals |
| Evidence ledger | Records source, file, calculation, test, approval, and delivery receipts |
| Validators | Check whether claims and actions satisfy the policy |
| Repair policy | Defines retry, downgrade, fallback, abstention, or block behavior |
| Output projector | Renders only public-safe, policy-compliant output |
| Audit/checkpoint | Preserves digest-safe evidence for review and replay |

The model proposes work inside this loop. The runtime decides when model text
becomes state, evidence, memory, artifact content, external side effect, or
user-visible output.

## First-Party Harnesses

Magi ships first-party harness contracts for the common work classes that need
deterministic checkpoints:

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

## Example: Verify Source Before Claim

Suppose the user asks:

```text
Read the uploaded product spec, market report, and competitor pricing table.
Answer the competitive positioning questions. If something is not in the
documents, say so clearly.
```

In a prompt-only agent, "only use the documents" is just text in the prompt. In
Magi, a source-verified research workflow changes the loop.

1. **Policy snapshot.** The runtime records that source-sensitive claims require
   inspected-source evidence, the uploaded documents are the allowed source set,
   and unsupported claims must be repaired, downgraded, or blocked.
2. **Context projection.** The model receives the user request, allowed document
   refs, committed public context, and evidence requirements. It does not
   receive raw private logs, hidden tool data, or arbitrary workspace paths.
3. **Source boundary.** If the model proposes reading `market_report.pdf`, the
   source read goes through ToolHost or a source-inspection boundary. The
   runtime writes a receipt with fields like `sourceId`, document ref,
   `snapshotDigest`, `contentDigest`, `retrievedAt`, and citeable span refs.
4. **Claim boundary.** If the model extracts "Competitor A charges $99 per
   seat", the research harness can represent that as a claim linked to the exact
   source span. The claim is not trusted just because the model wrote it.
5. **Intermediate validation.** The same validators can run before a child-agent
   result is accepted, before a summary becomes next-step context, before a
   memory write, before a Slack draft, and before the final answer. Unsupported
   claims do not have to wait until the final response to be caught.
6. **Repair or abstain.** If the model later writes "Competitor A is cheaper
   than us" but the ledger does not contain enough pricing evidence to derive
   that comparison, the runtime can ask for another allowed source inspection,
   weaken the wording, remove the claim, say the documents do not support it, or
   block the step.
7. **Governed projection.** The final projector renders supported claims,
   citation refs, uncertainty, and explicit gaps. Raw tool output, private
   paths, auth material, hidden reasoning, and unsupported claims stay out of
   the user-visible answer.

That is the difference between "please cite sources" and runtime enforcement.
The source ledger, claim graph, validators, repair policy, and output projector
all participate in the run.

## Example: Coding With Receipts

For coding work, Magi treats the workflow as an evidence-producing transaction:

1. The runtime records the files read before an edit is proposed.
2. Stale edits are rejected when the file changed after the read receipt.
3. Patch application creates a mutation receipt.
4. Rollback/delete proof is recorded for sandboxed mutation paths.
5. Diff and test evidence gates run before a completion claim is projected.
6. Final output cannot claim success unless the required verification evidence
   exists.

The same pattern can be applied to analysis, operations, document generation,
or channel delivery: define the evidence, then make the runtime enforce it.

## Why Hooks Alone Are Not Enough

Hooks are useful. They can observe lifecycle events, add context, block a step,
or run checks before and after tool calls.

But strong deterministic guarantees usually require owning runtime state
transitions, not just seeing lifecycle payloads.

For example, imagine trying to build the source-verification workflow above as a
third-party hook around an existing agent. A `before_reply` hook may see the
draft answer, but it may not know which intermediate summaries were fed into the
next model call. An `after_tool` hook may see a tool result, but it usually
cannot define a structured source ledger, decide which claims become verified
runtime state, or prevent unsupported claims from entering future context. Even
if the hook can inspect raw logs, it has to reconstruct the whole run after the
fact, which is expensive and imprecise.

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
(file read/write/edit, patch, Bash, behind permission prompts); what stays
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
