# Runtime

How Magi Agent turns model proposals into governed state transitions via the Python ADK runtime.

The runtime is the engine that governs every agent action. The runtime loop separates model-visible context from runtime-only evidence and claim state. A local run with a configured provider key builds a model-backed ADK runner and drives each turn through the engine driver: provider and model resolution, single-flight session admission, the ADK event stream, and error classification with bounded recovery. External delivery and high-authority mutations remain governed by explicit gates.

## Runtime loop

The Python ADK runtime entry point is __main__.py, which calls main.py (parse_runtime_env() reads required env vars) and then app.py (create_app() registers FastAPI routes such as /health, /healthz, and /v1/chat/completions).

The runtime container creates RuntimeConfig, RuntimeProfile, AdkPrimitiveBoundary, ToolRegistry, and ResolvedPluginState. The real turn loop is driven by the engine: a user message becomes a TurnInput (cli/contracts.py), and MagiEngineDriver (cli/engine.py) drives the turn over a model-backed ADK runner built by cli/real_runner.py, with the provider and model resolved by cli/providers.py. The engine takes a per-session single-flight slot from ActiveTurnRegistry (runtime/active_turn_registry.py), streams ADK events through the adk_bridge adapter, and classifies failures through runtime/error_recovery. The same engine path drives the CLI, the TUI, and the `magi-agent serve` chat surface.

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

## TurnInput and session concurrency

Each turn is modeled as a TurnInput (cli/contracts.py) with fields: prompt, session_id, turn_id, initial_messages, harness_state, and image_blocks. Session concurrency is single-flight: the engine registers each turn in ActiveTurnRegistry (runtime/active_turn_registry.py) and rejects a second concurrent turn for the same session id.

The runtime uses two layers: runtime/openmagi_runtime.py is the core container, and the adk_bridge/ directory provides the adapter, callback, plugin, context-compaction, and tool-attachment surfaces used when a live ADK runner is built. Local CLI/dashboard runs can use those surfaces directly; external authority is controlled by deployment policy.

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
