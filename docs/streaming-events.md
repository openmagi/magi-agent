# Streaming Events

Magi Agent streams public runtime progress through Server-Sent Events and CLI
`stream-json` output. Streaming is a projection surface: it should show useful
operator-visible progress without leaking hidden reasoning, credentials, raw
provider payloads, private paths, or unredacted tool data.

## Wire formats

HTTP streaming uses SSE frames:

```text
event: agent
data: {"type":"text_delta","delta":"Visible text"}

event: agent
data: {"type":"turn_result","terminal":"completed","usage":{"input_tokens":5}}

data: [DONE]
```

The CLI can emit line-delimited stream records:

```bash
magi --output stream-json --include-partial-messages "Stream progress"
```

API clients can use:

```bash
curl -N http://localhost:8080/v1/chat/stream \
  -H "Authorization: Bearer $GATEWAY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"Run a visible task"}]}'
```

## Event classes

Common public event classes include:

| Class | Examples | Public purpose |
| --- | --- | --- |
| Turn lifecycle | `turn_start`, `turn_phase`, `turn_end`, `turn_result`, `heartbeat` | Show where the run is and how it ended |
| Text | `text_delta`, `response_clear`, `llm_progress` | Stream visible answer progress |
| Tools | `tool_start`, `tool_progress`, `tool_end`, `patch_preview` | Show tool progress and safe summaries |
| Evidence | `source_inspected`, `rule_check`, `citation_gate`, `runtime_trace` | Show proof, validation, and audit progress |
| Recipes | `recipe_selection` | Show requested, applied, omitted, or blocked recipe refs |
| Control | `control_event`, `control_request`, `ask_user`, `plan_ready` | Ask for approval or clarification |
| Delegation | `spawn_started`, `spawn_result`, `child_started`, `child_progress`, `child_completed` | Show child-agent lifecycle |
| Automation | `task_board`, `mission_event`, `cron_run`, `background_task` | Show scheduled and mission progress |
| Artifacts | `document_draft`, `browser_frame` | Show safe artifact previews and browser observations |

Some internal event shapes may be intentionally unsupported, projected as an
alias, or blocked until their public contract is safe. The public stream should
fail closed when an event is unclassified.

## Sanitization

Public stream projection should:

- drop hidden reasoning and private provider events;
- remove raw tool arguments and raw tool results;
- redact private paths and credential-shaped text;
- keep only public refs, digests, statuses, reason codes, safe labels, and safe
  previews;
- scrub terminal errors before sending `turn_result`;
- emit `[DONE]` after the terminal frame.

For example, a `recipe_selection` event can expose recipe ids, versions,
digests, selection source, omission reason codes, and policy snapshot digest.
It should not expose raw policy snapshots or hidden prompt material.

## Control events

Approval and question events should include enough information for the operator
to decide, but not enough to leak secrets. Good control payloads use:

- control type;
- public subject ref;
- action summary;
- reason codes;
- approval or resume refs;
- request digest;
- allowed options.

The actual mutation, delivery, or side effect should wait for the approval path
defined by the harness.

## Completion events

The terminal `turn_result` frame should describe:

- terminal state;
- usage;
- cost if known;
- session id;
- turn id;
- redacted error when the run failed.

Completion text should not claim that background work, delivery, tests, or
external actions occurred unless the corresponding receipt exists.

## Dashboard use

The local dashboard consumes the same public event stream as API clients. Use it
to inspect:

- phase changes;
- visible answer deltas;
- tool progress;
- control requests;
- evidence and validation events;
- terminal state.

If a debugging workflow needs raw private payloads, inspect them locally with
the appropriate redaction discipline. Do not send them through public streaming
events.

## Related docs

- [Runtime](runtime.md)
- [API](api.md)
- [Harnesses](harnesses.md)
- [Tools](tools.md)
- [Security](security.md)
