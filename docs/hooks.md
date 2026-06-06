# Hooks

Hooks are useful lifecycle surfaces, but not the whole determinism model.

Use hooks for lifecycle observation and local checks. Use harnesses and runtime state transitions for strong determinism.

## Hooks are useful lifecycle surfaces

Hooks can observe, add context, or block lifecycle events. They are appropriate for small checks, instrumentation, prompt additions, and local lifecycle policy.

A hook should be precise about the payload it receives and the action it can take. It should not pretend to own state that the runtime never recorded.

- Observe turn, model, tool, commit, compaction, artifact, or error lifecycle events.
- Add safe context when policy allows it.
- Block a boundary when the payload is sufficient to make that decision.

## Why hooks alone are not enough

Hooks can observe, add context, or block lifecycle events. Strong determinism needs control over runtime state transitions.

Third-party hooks often receive only a lifecycle payload. They usually cannot define or read first-class source ledgers, claim graphs, context projection state, repair state, or output projection state.

Even when raw logs are available, reconstructing the whole run at the end is expensive and imprecise. The hook must infer which sources were actually opened, which spans support which claims, what was rejected earlier, and what survived compaction.

Coding agents are reliable because their core loop owns file reads, edits, diffs, tests, stale-edit checks, and commit gates. Magi Agent exposes that first-party level of control as composable runtime surfaces, so users can add domain-specific harnesses without forking the agent core.
