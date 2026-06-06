# Runtime

How Magi Agent turns model proposals into governed state transitions via the Python ADK runtime.

The runtime is the engine that governs every agent action. The runtime loop separates model-visible context from runtime-only evidence and claim state. A local run with a configured provider key builds a model-backed ADK runner, then flows through session boundaries, model routing, message building, event streaming, projection validation, and error classification. Hosted production routing, external delivery, and high-authority mutations remain governed by explicit gates.

## Runtime loop

The Python ADK runtime entry point is __main__.py, which calls main.py (parse_runtime_env() reads required env vars) and then app.py (create_app() registers FastAPI routes: /health, /healthz, /v1/chat/completions with Gate5B canary checks, and shadow diagnostic routes).

The runtime container creates RuntimeConfig, RuntimeProfile, AdkPrimitiveBoundary, ToolRegistry, and ResolvedPluginState. The real turn loop is: user message enters RunnerSessionBoundary.run_turn(), which takes a policy snapshot plus harness resolution, then routes through model_routing.py for model selection, message_builder.py for context packet assembly, the ADK runner, event streaming, projection_write_boundary.py for output validation, and error_taxonomy.py for error classification.

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

## TurnInput and session boundary

Each turn is modeled as a TurnInput (from runtime/turn_controller.py) with fields: user_id, session_id, turn_id, message_text, and harness_state. The RunnerSessionBoundary (runtime/runner_session_boundary.py) manages session concurrency and error classification for each turn.

The runtime uses two layers: runtime/openmagi_runtime.py is the core container, and the adk_bridge/ directory provides the adapter, callback, plugin, context-compaction, and tool-attachment surfaces used when a live ADK runner is built. Local CLI/dashboard runs can use those surfaces directly; hosted production authority is still controlled separately by deployment policy.

## Boundary validation

Validators run before more than the final answer. They also run before intermediate boundaries: child result import, next-step summary, memory write, Slack draft, artifact publication, delivery, and final output.

This keeps unsupported claims from becoming future context, durable memory, or external messages even if the final answer gate would catch them later.

- before context projection
- before tool execution
- after ToolHost receipt creation
- before child result import
- before next-step summary
- before memory write
- before Slack draft
- before artifact publication
- before final output

## Repair, approval, fallback, or abstention

When evidence is missing, Magi Agent should not silently project the unsupported claim. The policy decides whether to gather more evidence, weaken the wording, ask approval, use a fallback, abstain, or block.

Repair is bounded and auditable. The append-only audit ledger should show the failed validation, attempted repair, final projection, and any remaining caveat.
