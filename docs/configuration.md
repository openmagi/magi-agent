# Configuration

Status: ✅ Active — the local CLI is configured with a single provider key or a
`~/.magi/config.toml`.

Configure runtime-enforced control over context, tools, evidence, approvals, repair, projection, and audit.

Magi Agent configuration should describe what the runtime may show the model, what tools may do, what evidence is required, and how unsupported work is repaired or blocked.

## Policy snapshot

Each governed run starts by compiling an effective policy snapshot. The snapshot records applicable tools, approval rules, evidence rules, repair rules, projection rules, and audit obligations.

A stable policy snapshot makes runtime-enforced control reviewable: later validators can explain which rule caused a repair, downgrade, approval request, fallback, abstention, or block.

- Tools and permissions available for this route.
- Required source, file, test, calculation, or delivery evidence.
- Approval and idempotency obligations for side effects.
- Projection rules for model-visible context and user-visible output.

## Local minimal config

The local `magi` CLI needs exactly ONE provider key (or a `~/.magi/config.toml`).
There are no required service URLs or identity variables for local use.

Option A — a single provider key in your environment:

```sh
# Pick ONE of these (auto-detected in this order):
export ANTHROPIC_API_KEY=<your-key>     # default model claude-sonnet-4-5
# export OPENAI_API_KEY=<your-key>      # default model gpt-4o
# export GEMINI_API_KEY=<your-key>      # default model gemini-2.0-flash
#   (GOOGLE_API_KEY is accepted as an alias for the gemini provider)
# export FIREWORKS_API_KEY=<your-key>   # default model accounts/fireworks/models/llama-v3p1-70b-instruct
```

Option B — a `~/.magi/config.toml` (override the path with `MAGI_CONFIG`):

```toml
[model]
provider = "anthropic"   # anthropic | openai | gemini | fireworks
# model  = "claude-sonnet-4-5"   # optional; overrides the provider default
api_key = "<your-key>"

# Or keep keys per-provider:
# [providers.anthropic]
# api_key = "<your-key>"
```

With neither set, `magi` still launches but uses a model-free stub runner.

See the [environment variable reference](/docs/env-reference) for the local
provider, server, build, and authority flags.

## Model-visible context

Model-visible context is the allowed packet sent to the model. It can include user request, committed public summaries, allowed document refs, selected tool observations, memory projections, and citation refs.

It should not include raw secrets, private paths, hidden reasoning, raw child transcripts, unsupported claims, or runtime-only evidence and claim state.

## Runtime-only evidence and claim state

Runtime-only evidence and claim state includes source ledgers, claim graphs, approval receipts, idempotency keys, repair queues, rejected claims, validator decisions, output projection decisions, and append-only audit ledger entries.

This state exists so deterministic surfaces can be composed without stuffing every control record back into the model prompt.
