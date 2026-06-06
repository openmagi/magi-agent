# Runtime

Magi controls the loop around the model. The model proposes work; runtime policy
decides which state transitions are allowed.

```text
user request
  -> context projection
  -> model proposal
  -> tool / evidence / policy boundary
  -> repair, retry, ask, block, or continue
  -> public projection
```

## Public events

The runtime may emit public events such as:

- `turn_phase`
- `llm_progress`
- `tool_start`
- `tool_progress`
- `tool_end`
- `control_request`
- `turn_result`

Private paths, secrets, raw provider payloads, and hidden reasoning should not
be projected as public events.

## Completion

A turn should not finish by promising future background work unless a real
background job, receipt, or blocker is recorded. If the work cannot be completed
now, the runtime should say what is blocked and why.

