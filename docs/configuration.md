# Configuration

Configure runtime-enforced control over context, tools, evidence, approvals, repair, projection, and audit.

Magi Agent configuration should describe what the runtime may show the model, what tools may do, what evidence is required, and how unsupported work is repaired or blocked.

## Policy snapshot

Each governed run starts by compiling an effective policy snapshot. The snapshot records applicable tools, approval rules, evidence rules, repair rules, projection rules, and audit obligations.

A stable policy snapshot makes runtime-enforced control reviewable: later validators can explain which rule caused a repair, downgrade, approval request, fallback, abstention, or block.

- Tools and permissions available for this route.
- Required source, file, test, calculation, or delivery evidence.
- Approval and idempotency obligations for side effects.
- Projection rules for model-visible context and user-visible output.

### Example .magi-agent/env.local

```
# Provider and model
CORE_AGENT_MODEL=<model-id>

# Required service URLs (set by your local runtime profile)
CORE_AGENT_API_PROXY_URL=http://localhost:8081
CORE_AGENT_CHAT_PROXY_URL=http://localhost:8082
CORE_AGENT_REDIS_URL=redis://localhost:6379

# Your API key (never committed to git)
ANTHROPIC_API_KEY=<your-provider-api-key>
```

## Model-visible context

Model-visible context is the allowed packet sent to the model. It can include user request, committed public summaries, allowed document refs, selected tool observations, memory projections, and citation refs.

It should not include raw secrets, private paths, hidden reasoning, raw child transcripts, unsupported claims, or runtime-only evidence and claim state.

## Runtime-only evidence and claim state

Runtime-only evidence and claim state includes source ledgers, claim graphs, approval receipts, idempotency keys, repair queues, rejected claims, validator decisions, output projection decisions, and append-only audit ledger entries.

This state exists so deterministic surfaces can be composed without stuffing every control record back into the model prompt.
