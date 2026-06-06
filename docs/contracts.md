# Contracts

Contracts define the runtime obligations for a governed agent run.

A contract turns user intent into route selection, policy snapshot inputs, required evidence, approval obligations, repair behavior, projection rules, and audit checkpoints.

## Execution contract shape

A useful contract states what the agent is trying to do, what evidence is required, which tools may execute, which approvals are needed, and what output projection is allowed.

Contracts are how Magi Agent makes composable determinism inspectable instead of burying requirements inside prompt prose.

- route and workflow selection
- effective policy snapshot inputs
- required source, file, test, calculation, and delivery evidence
- approval and idempotency requirements
- repair, fallback, abstention, and block rules
- governed output projection and audit checkpoint requirements

## Verify Source Before Claim

The source-verification contract requires inspected-source evidence before factual claims can become child results, summaries, memory, Slack drafts, artifacts, or final answer text.

The claim state linked to source spans is runtime-only until the projector emits supported public claims and citation refs.

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
