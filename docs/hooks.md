# Hooks

Hooks let projects attach policy and evidence behavior without rewriting the
agent core.

## Wiring state (what runs today)

> **Status: gated (default-OFF).** User hooks do nothing until you opt in.

The hook system is wired CC-style:

- **User hooks load from `settings.json`.** The loader
  (`magi_agent/hooks/settings_loader.py`) reads `~/.magi/settings.json` and the
  workspace `.magi/settings.json`, mapping CC event names (`PreToolUse`,
  `PostToolUse`, ...) onto the runtime hook points below.
- **A command-executor bridge attaches those hooks to the live turn loop.**
  `magi_agent/cli/hook_wiring.py` builds a `HookBus` and bridges its
  `beforeToolUse` / `afterToolUse` hooks onto the engine's ADK
  before/after-tool callbacks (`magi_agent/cli/engine.py`). `PreToolUse` runs as
  a `before-tool` callback (it may approve, deny, or ask); `PostToolUse` runs as
  an `after-tool` callback (it observes the result and never blocks it).
- **Default-OFF.** Everything is gated by `MAGI_USER_HOOKS_ENABLED`
  (`magi_agent/config/env.py`). When the gate is OFF — the default — no
  `settings.json` hooks load, no bridge is attached, and a turn is
  byte-identical to running without hooks. The gate is meant for self-host /
  local CLI only; keep it OFF in hosted multi-tenant deployments because command
  hooks run operator-supplied shell.
- **Only the command executor is wired.** The `http` and `llm` hook executors
  are declared in the manifest but are **not yet wired** into the engine
  (deferred to a later PR); those hook kinds fail open today.

### Callback order

The engine attaches several `before-tool` layers in a fixed order:

```text
gate  ->  user hook  ->  control-plane  ->  runner_policy_route
```

The permission **gate** is prepended first, so a permission deny short-circuits
before anything else. The **user hook** (HookBus bridge) runs only on calls the
gate already allowed. The **control-plane** plugin and **runner_policy_route**
run after that. The `after-tool` path runs the `PostToolUse` bridge to observe
the tool result.

## Hook Lifecycle

Hooks run around runtime stages. A hook can observe, add evidence, replace a
safe phase input, block a phase, or ask for a permission decision depending on
the hook point and configuration.

Useful hook points include:

- context building;
- model request preparation;
- tool call validation;
- tool result normalization;
- evidence extraction;
- completion classification;
- final projection;
- audit reporting.

## Common Hook Points

| Hook point | Typical use |
| --- | --- |
| `beforeTurnStart` | Normalize the incoming user request and initialize run state |
| `beforeLLMCall` | Add allowed contract, memory, or source context |
| `afterLLMCall` | Inspect model output and stop reason |
| `beforeToolUse` | Approve, deny, ask, replace, or block a proposed tool call |
| `afterToolUse` | Normalize tool output and record evidence |
| `beforeCommit` | Verify final answer, artifacts, delivery, and acceptance criteria |
| `afterCommit` | Observe committed assistant output |
| `onAbort` | Record why the run stopped |
| `onError` | Capture public-safe error metadata |
| `beforeCompaction` | Preserve required context before transcript compaction |
| `afterCompaction` | Verify compacted context is safe and sufficient |

## Hook Results

Typical results:

```text
continue              allow the phase to proceed
replace               replace a safe phase input with a validated value
block                 stop the phase with a user-visible reason
skip                  skip a phase when that is explicitly allowed
permission_decision   approve, deny, or ask for a tool permission
```

Use blocking hooks sparingly. A blocking hook should explain the specific
missing evidence or unsafe action so the user or runtime can repair it.

## Hook rules

- Keep hooks narrow and testable.
- Do not hide external side effects inside prompt text.
- Prefer digest-safe public evidence over raw private payloads.
- Make disabled/default behavior explicit.
- Keep timeouts bounded.
- Do not project secrets, raw provider payloads, private filesystem paths, or
  hidden reasoning.
- Pair hooks with focused fixtures for pass and block cases.

## Example: Before-Commit Artifact Gate

Use `beforeCommit` when a rule decides whether final output is allowed. A file
creation task can require artifact evidence before the assistant says the file
is ready:

```text
Rule: if the user asked for a file, final output must reference an artifact path
or delivery receipt. If neither exists, block with "Required artifact evidence
is missing."
```

That rule is more reliable than asking the model to remember "do not forget to
attach the file" in a prompt.
