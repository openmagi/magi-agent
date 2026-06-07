# Hooks

Hooks let projects attach policy and evidence behavior without rewriting the
agent core.

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
