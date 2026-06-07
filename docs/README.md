# OpenMagi Docs

Open-source documentation for the Magi Agent programmable AI agent runtime.

Build agents that actually get things done by configuring the runtime around the model: context, tools, evidence, approvals, repair, projection, and audit.

[Website](https://openmagi.ai) · [Source](https://github.com/openmagi/magi-agent)

## What Magi Agent and OpenMagi are

Magi Agent is an open-source AI assistant that runs on your machine. Unlike prompt-only AI tools, it proves its work — verifying sources, testing code, and getting approval before taking action — so you can trust the results.

Magi Agent is a programmable AI agent runtime for agents that actually get things done. It treats the model as a proposer inside a governed runtime, not as the authority for tools, memory, artifacts, or external side effects.

OpenMagi is the platform and site. Open Magi Cloud is optional managed hosting for teams that want Magi Agent operated for them.

Start with Homebrew:

```bash
brew install --force-bottle openmagi/tap/magi-agent
magi-agent serve --port 8080
open http://localhost:8080/dashboard
```

Then use the local `magi` CLI for tasks. Source checkout is for contributors developing the runtime.

- Model proposes plans, claims, tool calls, and draft output.
- Runtime-enforced control decides which proposals become state, evidence, output, memory, artifacts, or side effects.
- Harnesses and recipes compose policy instead of relying on prompt text alone.
- Machine-readable docs are published for coding agents and documentation crawlers.

- [Install with Homebrew](/docs/getting-started)
- [Read architecture](/docs/architecture)
- [Read runtime](/docs/runtime)
- [Visit openmagi.ai](https://openmagi.ai)
- [View source](https://github.com/openmagi/magi-agent)

## The determinism problem

Prompt-only agents can sound careful while still inventing facts, skipping approvals, using stale context, or turning hidden tool output into public claims. The problem is not that models are useless; it is that model text is a weak place to enforce obligations.

Magi Agent makes the obligation explicit. A claim that needs evidence must be linked to source spans. A tool that mutates external state must pass through approval and idempotency checks. A memory write must pass through projection rather than copying raw transcript.

- Instructions are easy for models to forget under long context and tool pressure.
- Logs after the fact are too late to prevent unsupported state transitions.
- Reliable agents need boundary checks before intermediate and final outputs cross trust boundaries.

## Composable determinism

In plain terms: you pick the safety rules you want, combine them, and the runtime enforces them automatically — no matter what the model tries to do.

Composable determinism means each workflow can add runtime-enforced control surfaces: policy snapshots, model-visible context, runtime-only evidence and claim state, ToolHost / activity boundary checks, validators and guardrails, repair, approval, fallback, or abstention, governed output projection, and an append-only audit ledger.

The model-visible context is intentionally smaller than the runtime state. The model may see an allowed context packet, citation refs, summaries, and tool observations. The runtime keeps source ledgers, claim graphs, approval receipts, rejected claims, repair queues, projection decisions, and audit checkpoints as runtime-only evidence and claim state.

- Context projection controls what the model can see.
- Evidence ledgers and claim graphs control what the runtime can trust.
- Repair policy controls what happens when support is missing.
- Output projection controls what users, channels, memory, and artifacts can receive.

## Architecture loop

Magi Agent uses a two-plane architecture. The left side is what the model can see and propose. The right side is runtime-only control state used to decide whether proposals can advance.

Google ADK is the substrate for model/tool orchestration work. Magi Agent is the product/runtime contract that compiles policy and governs context, tools, evidence, repair, projection, and audit. The Python ADK migration remains gated and default-off for live production authority until the separate rollout gates pass.

### Two-plane architecture

```
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
    |                               memory, artifact, workspace
    v
Model can continue       <--------- Receipts + evidence ledger
                                    source spans, approval receipts,
                                    file/test/calculation/delivery proof

Final answer/artifact     <-------- Validators + repair/fallback policy
                                    unsupported claim -> repair, downgrade,
                                    abstain, block, or ask approval

User-visible projection   <-------- Output projector + audit checkpoint
```

## Verify Source Before Claim

User request: Read the uploaded product spec, market report, and competitor pricing table. Answer the competitive positioning questions. If something is not in the documents, say so clearly.

The runtime routes the turn to a source-verified research workflow, compiles an effective policy snapshot requiring inspected-source evidence, projects only allowed document refs and committed public context, then forces reads through ToolHost or a source-inspection boundary.

A source receipt records sourceId, snapshotDigest, contentDigest, retrievedAt, and citeable spans. Claim state linked to source spans is validated before intermediate boundaries such as child result, next-step summary, memory write, Slack draft, artifact, and final answer.

If the model writes "Competitor A charges $99 per seat", that claim must be linked to a pricing-table span. If it later writes "Competitor A is cheaper than us", the validator checks whether that comparison is derivable from recorded pricing claims.

When evidence is missing, the runtime can repair, downgrade, abstain, block, or ask approval. Governed output projection excludes raw tool output, hidden reasoning, private paths, secrets, and unsupported claims.

- Route to source-verified research workflow.
- Compile policy snapshot requiring inspected-source evidence.
- Project model-visible context without raw private tool output.
- Record source receipt with sourceId, snapshotDigest, contentDigest, retrievedAt, and citeable span refs.
- Validate claim graph before every relevant boundary.
- Project only public-safe supported claims and citation refs.

### Receipt and claim state

```
sourceReceipt:
  sourceId: src_product_spec_2026_05
  snapshotDigest: sha256:8f4c2b7a9d13
  contentDigest: sha256:5d96aa2f44b1
  retrievedAt: 2026-05-27T20:11:42Z
  citeableSpans:
    - spanId: pricing_table.rows.competitor_a.seat_price
      text: Competitor A charges $99 per seat

claimState:
  claimId: claim_competitor_a_seat_price
  text: Competitor A charges $99 per seat
  linkedSourceSpans:
    - sourceId: src_product_spec_2026_05
      spanId: pricing_table.rows.competitor_a.seat_price
```

## Why hooks alone are not enough

Hooks can observe, add context, or block lifecycle events. They are useful. Strong determinism needs control over runtime state transitions.

A hook can inspect a lifecycle payload. It usually cannot define first-class source ledgers, claim graphs, context projection state, repair state, or output projection state. If it checks the final answer after the run, it has to reconstruct state from logs, which is expensive and imprecise.

Coding agents are reliable because their core loop owns file reads, edits, diffs, tests, stale-edit checks, and commit gates. Magi Agent exposes that first-party level of control as composable runtime surfaces, so users can add domain-specific harnesses without forking the agent core.

## Machine-readable docs

Use the compact text docs for orientation and the full text docs when an AI coding agent needs the complete Magi Agent runtime narrative.

- [/llms.txt](/llms.txt)
- [/docs/llms.txt](/docs/llms.txt)
- [/docs/llms-full.txt](/docs/llms-full.txt)
