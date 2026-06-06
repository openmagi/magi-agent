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

## Work Loop

A run normally moves through these stages:

1. Build model-visible context from the user request, session state, allowed
   memory, and workspace references.
2. Call the model or runner boundary.
3. Route proposed tool calls through permission, workspace, and approval policy.
4. Record public-safe receipts for reads, writes, calculations, deliveries, and
   external actions.
5. Run validators and repair policy.
6. Project only supported, public-safe output to the user.

The model can write text, but the runtime decides whether that text becomes
state, memory, artifact content, an external side effect, or final output.

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

## Dashboard Stream

`magi-agent serve` includes the dashboard at `/dashboard`. The dashboard can
show:

- runtime health and build metadata;
- chat/session events;
- tool progress and public receipts;
- control requests for human approval;
- evidence and completion status when emitted by the runtime.

Public event projection is intentionally narrower than internal logs. If a
private payload is needed for debugging, inspect it locally with the appropriate
redaction discipline instead of sending it through user-visible events.

## Completion

A turn should not finish by promising future background work unless a real
background job, receipt, or blocker is recorded. If the work cannot be completed
now, the runtime should say what is blocked and why.

Completion claims should be backed by evidence appropriate to the task:

- source-sensitive answers need source evidence;
- coding work needs read, mutation, diff, and verification evidence;
- file delivery needs a created artifact and delivery receipt;
- external sends need channel or API delivery receipts;
- delegated work needs accepted child output or a clear blocker.

## Repair and Fallback

When evidence is missing or a validator blocks completion, the runtime can:

- ask for approval or clarification;
- inspect another allowed source;
- retry a tool call;
- weaken or remove an unsupported claim;
- report a concrete blocker;
- abstain from claiming success.

Silent success is the wrong fallback for governed work.
