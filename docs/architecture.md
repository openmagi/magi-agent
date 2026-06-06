# Architecture

The two-plane architecture behind Magi Agent composable determinism.

Magi Agent separates the model-visible loop from the runtime-only control plane so policy, evidence, repair, projection, and audit can govern every state transition.

## Two-plane architecture

The primary architecture is not a tall pipeline. It is a two-plane loop: model-visible proposals on one side, runtime-only control state on the other.

The model proposes actions, claims, and drafts. The runtime decides when those proposals become state, evidence, output, memory, artifacts, or external side effects.

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

## Google ADK substrate, Magi Agent contract

Google ADK provides model/tool orchestration primitives. Magi Agent defines the higher-level runtime contract: policy snapshot, context projection, ToolHost, source ledger, claim graph, validators, repair/fallback policy, output projector, and append-only audit ledger.

Where ADK-backed surfaces are marked default-off, docs should describe architecture and contracts without implying live production authority is enabled.

## Why hooks alone are not enough

A hook can inspect a lifecycle payload. It usually cannot define first-class source ledgers, claim graphs, context projection state, repair state, or output projection state.

Magi Agent exposes first-party level of control as composable runtime surfaces so a harness can define state, evidence, boundaries, repair, and projection directly.
