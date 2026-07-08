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

Where ADK-backed surfaces are marked default-off, docs should describe architecture and contracts without implying external authority is enabled.

## Why hooks alone are not enough

A hook can inspect a lifecycle payload. It usually cannot define first-class source ledgers, claim graphs, context projection state, repair state, or output projection state.

Magi Agent exposes first-party level of control as composable runtime surfaces so a harness can define state, evidence, boundaries, repair, and projection directly.

## Layering: first-party capability modules vs control surfaces

The runtime is organised in four strata. Understanding where each piece lives prevents a common mistake: embedding capability inside control surfaces, or governing capability through means that bypass the surfaces.

**Stratum 1 — runtime kernel.** The driver and turn engine, the ToolHost dispatch chain, the child-runner boundary (spawn cap, permission gates, full-text seam), and the evidence ledger. These are not configurable by end-users. They establish the invariants everything above depends on.

**Stratum 2 — first-party capability modules.** Native tools and imperative orchestrators: SpawnAgent (multi-step child delegation), deep web research, cross-verify, verify-audit, goal loop, deep-solve, and future modules of the same kind. These express capability that only imperative code can express — iterative reasoning loops, multi-stage child pipelines, test execution, convergence detection. No prompt or declarative rule can substitute for them, which is exactly why they must be independently governed rather than embedded in governance itself.

**Stratum 3 — control surfaces.** Three orthogonal axes:
- **Pack** (install/removal axis) — declares which capability modules are available; removing a pack dispatch-blocks its tools honestly.
- **Policy/rule** (ambient per-turn governance) — applies declarative constraints at every turn boundary (evidence gates, claim guards, repair rules).
- **Mode** (session posture) — sets the session-level risk posture (safe, eval, full, etc.) which is read by flags and profiles to adjust defaults.

Control surfaces select, govern, and posture stratum 2 rather than contain it. A pack does not implement capability; it gates it. A policy does not replace a verifier; it constrains when the verifier's output is accepted. A mode does not run a pipeline; it sets the flag profile that the pipeline reads.

**Stratum 4 — skills/prompts.** Model-facing guidance loaded into context. Skills can invoke stratum 2 tools and observe stratum 3 posture, but they cannot circumvent stratum 1 boundaries or bypass stratum 3 gating.

### The honest-toggle principle

Every first-party capability module must project honestly into the control surfaces:

- **Visible in the catalog** — the module must have metadata (pack manifest, tool registration) so users can see it exists and understand what it does.
- **Gated by a toggle that actually works** — the runtime flag or pack state that appears to disable the module must actually block dispatch; a read-only mirror that does not reach the dispatch path is a defect.
- **Dispatch-blocked when its pack is removed** — uninstalling or disabling a pack must prevent the associated tools from running, not just hide them from the UI.

Shipping behavior that bypasses the control surfaces is a defect even when the capability itself is correct. The control surfaces exist so operators and users retain meaningful authority over what runs on their behalf.

### Deep-solve as a conforming example

The deep-solve pipeline (stratum 2) conforms to this principle. Its dispatch is pack-gated: removing the `openmagi.deep-solve` pack blocks the `DeepSolve` tool honestly before any orchestration work begins. A profile flag (`MAGI_DEEP_SOLVE_ENABLED`) and a kill-switch provide two independent disable paths. Pack metadata and tool catalog registration make the capability visible. Child stages run under the same boundary caps, permission gates, and evidence ledger as any other child — the pipeline does not grant itself elevated privileges.
