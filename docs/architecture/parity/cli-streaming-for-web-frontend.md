# CLI Streaming for the Local Web Chat GUI

A reference for the OSS/local **Magi Agent web chat GUI** that must stream
model and tool output the way the `magi` CLI already does: incremental
assistant tokens, tool-call / tool-result rendering, permission approval
prompts, and cancellation.

This document is scoped to the local Magi Agent runtime and dashboard served by
`magi-agent serve`. It is not a hosted OpenMagi dashboard contract. Hosted
deployments already have a separate chat-proxy/OpenAI-SSE adapter with product
auth, interrupt/inject handling, selected-route receipts, and `event: agent`
frames; applying this design there requires an explicit adapter layer rather
than copying the local transport literally.

The CLI is built as **one engine, two surfaces**. A single async generator —
`EngineDriver.run_turn_stream(...)` — is the source of truth for a turn. Two
surfaces consume the *identical* event stream: the headless NDJSON projection
(`magi_agent/cli/headless.py`) and the Textual TUI
(`magi_agent/cli/tui/app.py`). A third consumer already exists: the **local
dashboard** chat endpoint (PR #145, commit `68c0399`) drives the same engine and
ships its events to a browser over **SSE**. A richer local web chat GUI should
extend that local dashboard consumer, not create a second engine.

> Citations are `path:line` against the worktree at
> `magi-agent-oss-worktrees/docs-streaming` (branch `docs/cli-streaming-web-frontend`).

---

## 1. Overview — one engine, two surfaces

The contract lives in `magi_agent/cli/contracts.py`. `EngineDriver` is a
`Protocol` whose single method `run_turn_stream` returns an
`AsyncGenerator[RuntimeEvent, EngineResult]`
(`magi_agent/cli/contracts.py:135-159`):

```python
def run_turn_stream(
    self, runtime, turn_input, *, cancel: asyncio.Event,
    gate: "PermissionGate | None" = None,
) -> AsyncGenerator[RuntimeEvent, EngineResult]: ...
```

**Terminal-result convention.** A Python `async def` generator cannot
`return value`, so the terminal `EngineResult` is **the FINAL yielded item** —
the last object an `async for` produces is an `EngineResult`, not a
`RuntimeEvent`. Consumers iterate and, on encountering an `EngineResult`, stop
and treat it as terminal (`magi_agent/cli/contracts.py:14-33`,
`97-116`).

Every consumer drains the generator with the same shape:

| Consumer | Drain site | What it does with each item |
|----------|-----------|------------------------------|
| Headless NDJSON | `_project_stream` `magi_agent/cli/headless.py:529-607` | project to wire frames live |
| Textual TUI | `_run_turn` / `_fold_event` `magi_agent/cli/tui/app.py:528-578` | fold into transcript widgets |
| Local dashboard (PR #145) | `_local_adk_chat_sse` `magi_agent/transport/chat.py:801-866` | emit each `payload` over SSE |

The concrete engine is `MagiEngineDriver`
(`magi_agent/cli/engine.py:202`). It is import-clean: nothing heavy (ADK /
google-genai / textual) is imported until a turn is actually iterated
(`magi_agent/cli/engine.py:163-184`).

---

## 2. The event model

### 2.1 `RuntimeEvent`

`RuntimeEvent` is a frozen pydantic model with **three** fields
(`magi_agent/runtime/events.py:40-45`):

```python
class RuntimeEvent(BaseModel):
    model_config = ConfigDict(frozen=True)
    type: EventKind                  # "status"|"token"|"tool"|"control"|"artifact"|"error"
    payload: dict[str, object]
    turn_id: str | None = None
```

`EventKind` is the coarse routing tag (`magi_agent/runtime/events.py:37`). The
**fine-grained** event identity lives in `payload["type"]` (an inner type such
as `text_delta`, `tool_start`, `tool_end`, `turn_phase`, `turn_end`). The engine
derives `EventKind` from that inner `payload["type"]` via `_map_event_kind`
(`magi_agent/cli/engine.py:138-160`):

| `EventKind` | inner `payload["type"]` values |
|-------------|-------------------------------|
| `token` | `text_delta` |
| `tool` | `tool_start`, `tool_progress`, `tool_end` |
| `control` | `control_event`, `control_replay_complete`; `control_request` only when a prompt sink projects it into the outbound transport |
| `artifact` | `source_inspected`, `document_draft`, `research_artifact_delta`, `patch_preview` |
| `error` | `error` |
| `status` | everything else (e.g. `turn_phase`, `turn_end`, `heartbeat`) |

So **always branch on BOTH** `event.type` (coarse) and `payload["type"]`
(inner). The TUI and headless both do exactly this.

Important distinction: `control_request` approval prompts are produced by a
`PromptSink` such as `HeadlessSink`, not by the model runner itself. A web
transport that wants permission modals must multiplex both producers into the
browser stream: `run_turn_stream(...)` for runtime events and the sink's
outbound control frames while the engine is blocked in `gate.check(...)`.

### 2.2 Payload schemas per inner type

The payloads are produced by the ADK→public-event bridge and read by the
projection/fold helpers (`magi_agent/cli/headless.py:188-337`,
`magi_agent/cli/tui/app.py:88-172`). The fields each surface actually reads:

| `event.type` | inner `payload.type` | payload fields read | Meaning | How the web UI renders it |
|---|---|---|---|---|
| `token` | `text_delta` | `delta` (preferred) or `text` | One incremental chunk of assistant text | Append to the in-flight assistant bubble |
| `tool` | `tool_start` | `id`, `name`, `input`/`arguments`/`input_preview`/`inputPreview`, optional `parentToolUseId`/`parent_tool_use_id`/`parentToolId` | A tool call began | Open a tool-call card keyed by `id` |
| `tool` | `tool_progress` | `id`, `label`, `status`, `message`, `detail`, `progress`, `output`/`result` | Mid-tool progress | Update the open card (spinner/percent) |
| `tool` | `tool_end` | `id`, `status` (`ok`/`error`/`blocked`/`needs_approval`), `output_preview`/`outputPreview`, `interrupted` (bool), `durationMs` | Tool finished (or was rejected/interrupted) | Close the card; render result or rejection |
| `status` | `turn_phase` | `turnId`, `phase` (`pending`/`planning`/`executing`/`verifying`/`committing`/`committed`/`aborted`), `label`, `detail` | Lifecycle phase marker | Optional status chip / activity line |
| `status` | `turn_end` | `turnId`, `status` (`committed`/`aborted`), `reason` | Turn finished marker (synthesized on cancel/abort) | Stop spinners; show abort reason if any |
| `status` | `heartbeat`, `child_progress`, ... | varies | Keepalive / sub-agent progress | Coarse activity line (low priority) |
| `artifact` | `source_inspected`, `patch_preview`, ... | varies | Evidence / diff / document artifact | Specialized card (diffs, citations) |
| `error` | `error` | `reason` / message | A turn-level error event | Inline error notice |

> Field-name tolerance is real and load-bearing: the helpers read several
> spellings because the stub and the ADK bridge differ. `_token_text` reads
> `delta` then `text` (`magi_agent/cli/headless.py:188-200`,
> `magi_agent/cli/tui/app.py:88-95`); `_tool_input` reads
> `input`/`arguments`/`input_preview`/`inputPreview`
> (`magi_agent/cli/headless.py:244-258`); `_tool_result` reads
> `output`/`output_preview`/`outputPreview`/`result`
> (`magi_agent/cli/tui/app.py:135-147`). The web GUI should mirror this
> tolerance.

### 2.3 The terminal `EngineResult` / `Terminal`

`EngineResult` (`magi_agent/cli/contracts.py:97-116`):

```python
@dataclass
class EngineResult:
    terminal: Terminal           # completed|aborted|max_turns|error
    usage: dict = {}
    cost_usd: float = 0.0
    error: str | None = None
    session_id: str | None = None
    turn_id: str | None = None
```

`Terminal` is an enum whose values equal member names for stable JSON
(`magi_agent/cli/contracts.py:88-94`): `completed`, `aborted`, `max_turns`,
`error`.

The engine emits exactly one terminal per turn:

- normal completion → `Terminal.completed` (`magi_agent/cli/engine.py:567-574`)
- user cancel → `Terminal.aborted`, `error="cancelled"`
  (`magi_agent/cli/engine.py:538-545`)
- runner/model error → `Terminal.error` with the error string
  (`magi_agent/cli/engine.py:557-564`)
- concurrent turn for the same session →
  `Terminal.aborted`, `error="active_session_turn"`
  (`magi_agent/cli/engine.py:314-324`)
- no runner resolvable → `Terminal.error`, `error="no_runner"`
  (`magi_agent/cli/engine.py:373-382`)

---

## 3. The headless NDJSON wire protocol

This is the **web↔engine contract** when consuming the headless surface
directly. In `stream-json` mode every frame is one JSON object per line
(NDJSON), written through a single `NdjsonWriter` so ordering is FIFO and each
line is flushed (`magi_agent/cli/headless.py:16-20`, `713`). Frame models are in
`magi_agent/cli/protocol.py`.

### 3.1 Outbound frames (engine → client)

**`system` / `init`** — first frame of a `stream-json` run
(`magi_agent/cli/protocol.py:32-39`, emitted at
`magi_agent/cli/headless.py:745-754`):

```json
{"type":"system","subtype":"init","uuid":"...","session_id":"<sid>",
 "tools":[],"model":"magi","mcp_servers":[],"cwd":"/work"}
```

**`assistant` (text)** — one coalesced run of `token` events. The projection
buffers consecutive tokens and flushes them as ONE assistant frame when a
non-token event or the terminal arrives (`magi_agent/cli/headless.py:261-267`,
`544-596`):

```json
{"type":"assistant","uuid":"...","session_id":"<sid>",
 "message":{"role":"assistant","content":"Hello, here is the plan..."},
 "parent_tool_use_id":null}
```

**`assistant` (tool_use)** — emitted on a `tool_start`
(`magi_agent/cli/headless.py:270-285`, `582-585`):

```json
{"type":"assistant","uuid":"...","session_id":"<sid>",
 "message":{"role":"assistant","content":[
   {"type":"tool_use","id":"call_123","name":"Bash",
    "input":{"cmd":"ls -la"}}]},
 "parent_tool_use_id":null}
```

**`user` (tool_result)** — emitted on a `tool_end`
(`magi_agent/cli/headless.py:288-307`, `586-589`):

```json
{"type":"user","uuid":"...","session_id":"<sid>",
 "message":{"role":"user","content":[
   {"type":"tool_result","tool_use_id":"call_123",
    "content":"total 12\ndrwxr-xr-x ...","is_error":false,"status":"ok"}]}}
```

`is_error` is true when `status` ∈ {`error`,`blocked`} or
`payload.interrupted` is set (`magi_agent/cli/headless.py:299`).

**`system` / status** — `tool_progress` and every other non-token, non-tool
event (status / artifact / control / error) project here. `subtype` is
`task_progress` for progress-ish inner types, `task_started` for
started, else `status` (`magi_agent/cli/headless.py:310-326`, `590-594`):

```json
{"type":"system","subtype":"task_progress","uuid":"...","session_id":"<sid>",
 "payload":{"kind":"tool","type":"tool_progress","id":"call_123",
            "label":"running","progress":0.5}}
```

**`stream_event`** — only when `include_partial=True`. A raw, lightly-redacted
echo of the `RuntimeEvent` (token text replaced with `[redacted]`)
(`magi_agent/cli/protocol.py:52-55`, `magi_agent/cli/headless.py:329-337`,
`561-572`):

```json
{"type":"stream_event","uuid":"...","session_id":"<sid>",
 "event":{"type":"token","payload":{"delta":"[redacted]"}}}
```

**`result`** — the terminal frame, projected from `EngineResult`
(`magi_agent/cli/protocol.py:68-79`, `magi_agent/cli/headless.py:787-806`):

```json
{"type":"result","subtype":"success","uuid":"...","session_id":"<sid>",
 "result":"Hello, here is the plan...","usage":{"input_tokens":8,"output_tokens":12},
 "total_cost_usd":0.0,"is_error":false,"errors":[]}
```

`subtype` ∈ {`success`, `error_max_turns`, `error_during_execution`}
(`magi_agent/cli/headless.py:172-177`); `is_error` is true for any non-completed
terminal or any non-null `error` (`magi_agent/cli/headless.py:180-185`).

**`control_request`** — emitted by the permission gate's `HeadlessSink`, not by
`run_turn_stream(...)`, to ask the client to approve a tool
(`magi_agent/cli/protocol.py:82-86`,
`magi_agent/cli/permissions.py:533-540`). If the local web transport consumes
only the engine generator and does not also drain the sink/frame writer, the UI
will never see this prompt while the engine waits:

```json
{"type":"control_request","uuid":"...","session_id":"<sid>",
 "request_id":"turn-1:Bash:1",
 "request":{"tool_name":"Bash","arguments":{"cmd":"rm -rf build"},
            "reason":"tool_use"}}
```

### 3.2 Inbound frames (client → engine)

Read by `_route_inbound_line` (`magi_agent/cli/headless.py:406-443`); models in
`magi_agent/cli/protocol.py:102-118`.

**`control_response`** — the decision for a `control_request`, correlated by
`request_id`. Routed to `sink.deliver(...)`
(`magi_agent/cli/headless.py:432-437`). The `response` body schema is parsed by
`HeadlessSink._translate` (`magi_agent/cli/permissions.py:31-58`, `554-601`):

```json
{"type":"control_response","request_id":"turn-1:Bash:1",
 "response":{"decision":"allow"}}
```

`response` fields (all optional except `decision`):
`decision` (`"allow"|"deny"` — **anything not `"allow"` is a deny**, fail-safe),
`remember` (bool → persists a rule), `matcher` (rule matcher, default `"*"`),
`feedback` (carried on deny), `updated_input` (allow with rewritten args),
`interrupt` (deny that also cancels the turn).

**`control_cancel_request`** — sets the cancel event
(`magi_agent/cli/headless.py:438-443`, `magi_agent/cli/protocol.py:113-116`):

```json
{"type":"control_cancel_request","request_id":"turn-1:Bash:1"}
```

**`user`** — a user input frame model exists
(`magi_agent/cli/protocol.py:102-105`) but `_route_inbound_line` handles only
the two control frames above today.

### 3.3 Inbound reader, EOF, dedup

The inbound reader runs on a **daemon thread** so a still-open stdin pipe can
never gate process exit; each line is marshalled back to the loop via
`call_soon_threadsafe` (`magi_agent/cli/headless.py:446-526`). On **EOF** the
reader calls `sink.close()`, which fail-closes every pending ask with a synthetic
`deny` response — never an auto-allow (`magi_agent/cli/headless.py:504-515`,
`magi_agent/cli/permissions.py:476-495`). The sink keeps a bounded
(`_DEDUP_CAP=1024`) ordered set of resolved `request_id`s so late/duplicate
`control_response` frames are dropped (`magi_agent/cli/permissions.py:421`,
`444-474`).

---

## 4. Permission approval flow

Tool approval is intercepted **before** a tool runs, via an ADK
`before_tool_callback` the engine attaches when a `gate` is passed
(`magi_agent/cli/engine.py:722-842`). The callback builds a `ControlRequest`
and awaits `gate.check(req)`. `ControlRequest`
(`magi_agent/runtime/control.py:49-56`) has fields `request_id`, `turn_id`,
`tool_name`, `arguments`, `reason`.

The gate (`RulesPermissionGate.check`,
`magi_agent/cli/permissions.py:240-253`):

1. evaluates static + remembered rules; `allow`/`deny` short-circuit with no
   frame.
2. on `ask`, races every attached `PromptSink` (`_race`,
   `magi_agent/cli/permissions.py:255-335`); first answer wins, losers are
   cancelled (resolve-once). With **no sink**, the result is a safe **deny**.

For the headless surface the sink is `HeadlessSink`, which emits the
`control_request` frame and awaits the matching `control_response`
(`magi_agent/cli/permissions.py:498-552`).

**Permission modes** (`magi_agent/cli/permissions.py:115`, `498-518`):

- `bypassPermissions` — blanket allow, **no frame** emitted. (This is what the
  dashboard uses — see §6.)
- `acceptEdits` — auto-allow edit-class tools (`FileEdit`, `FileWrite`, `Edit`,
  `Write`, `ApplyPatch` — `magi_agent/cli/permissions.py:120-122`) with no
  frame; all other tools still prompt.
- `default` — always prompt (emit frame + await response). Never a silent
  auto-allow. In `default` mode with no inbound channel, headless leaves the gate
  **sink-less** so an ask can't hang — it falls back to safe deny
  (`magi_agent/cli/headless.py:726-741`).

The decision the gate returns drives the ADK callback
(`magi_agent/cli/engine.py:799-840`): `deny` returns a `{"status":"blocked",
"error":"permission_denied",...}` dict (ADK treats a returned dict as the tool
result and **skips** the tool); `deny` + `interrupt` also sets `cancel`; `allow`
with `updated_input` rewrites `args` in place (re-validated against the rules
engine first).

### 4.1 Sequence (ASCII)

```
 Client (web)            Engine (run_turn_stream)            Gate / HeadlessSink
     |                            |                                  |
     |  POST prompt               |                                  |
     |--------------------------->|  before_tool_callback fires      |
     |                            |--- ControlRequest -------------->|
     |                            |                                  |  rules: ask
     |   control_request frame    |                                  |--- emit frame
     |<======================================================================|
     |                            |                                  |  await future
     |  control_response          |                                  |
     |  {"decision":"allow"}      |                                  |
     |======================================================================>|
     |                            |  PermissionDecision(allow)       |  deliver()
     |                            |<---------------------------------|
     |                            |  tool runs -> tool_end           |
     |   tool_end / result        |                                  |
     |<---------------------------|                                  |

  EOF / stdin closed  ->  sink.close()  ->  every pending ask resolves to DENY
```

---

## 5. Turn lifecycle & cancellation

A turn (`MagiEngineDriver.run_turn_stream` → `_drive`,
`magi_agent/cli/engine.py:297-574`):

1. **Single-flight per session.** `ActiveTurnRegistry.try_acquire(session_key)`;
   a second concurrent turn for the same `session_id` is rejected with a
   terminal `aborted` / `active_session_turn` and never runs
   (`magi_agent/cli/engine.py:312-324`). The slot is released in a `finally` on
   every path (`magi_agent/cli/engine.py:346-353`).
2. **Drive the ADK run.** Each ADK event is projected to public agent-event
   dicts, sanitized, wrapped as a `RuntimeEvent`, and yielded
   (`magi_agent/cli/engine.py:472-484`). `text_delta` → `token`; tool events →
   `tool` (start/progress/end); phases → `status`.
3. **Pending-tool tracking.** `tool_start` records the `id`; `tool_end` clears
   it (`magi_agent/cli/engine.py:680-692`).
4. **Terminal.** Normal end yields `EngineResult(completed)`.

**Cancellation** is an `asyncio.Event` raced against each ADK pull
(`magi_agent/cli/engine.py:627-660`): every step waits on
`{next_event, cancel.wait()}` with `FIRST_COMPLETED`, so a mid-step cancel is
honored promptly. On cancel the engine (a) **synthesizes orphan `tool_end`
events** for every still-pending tool call (status `error`, `interrupted:true`)
so the transcript stays balanced (`magi_agent/cli/engine.py:523-527`,
`694-719`), (b) emits a `turn_end` status (`reason:"user_interrupt"`), then (c)
yields `EngineResult(aborted, error="cancelled")`
(`magi_agent/cli/engine.py:523-545`).

Cancel is triggered three ways: the TUI `action_cancel_turn` sets the event
(`magi_agent/cli/tui/app.py:635-638`); an inbound `control_cancel_request`
frame sets it (`magi_agent/cli/headless.py:438-443`); a denied tool with
`interrupt:true` sets it (`magi_agent/cli/engine.py:813-816`).

---

## 6. Reference integration: the local dashboard (PR #145)

**Found in `magi_agent/transport/chat.py`** (PR #145, commit `68c0399`,
"feat(dashboard): run local chat through headless engine"). This is the existing
proof that the headless engine can power a browser chat — the web GUI should
follow it.

**Route + transport.** `POST /v1/chat/completions` (bearer-gated against
`runtime.config.gateway_token`) returns a FastAPI `StreamingResponse` with
`media_type="text/event-stream"` — i.e. **SSE**
(`magi_agent/transport/chat.py:916-933`, `790-798`).

**How it drives the engine** (`magi_agent/transport/chat.py:801-866`):

1. `build_headless_runtime(cwd=..., permission_mode="bypassPermissions",
   session_id=..., model=...)` builds the engine + gate
   (`magi_agent/cli/wiring.py:107-204`). It uses **`bypassPermissions`** so the
   pre-tool gate auto-allows (no `control_request` round-trip) — dispatcher /
   toolhost hard-safety still runs after the ADK gate
   (`magi_agent/cli/wiring.py:178-186`).
2. It calls `headless.engine.run_turn_stream(None, {"prompt", "session_id",
   "turn_id"}, cancel=asyncio.Event(), gate=headless.gate)` directly — i.e. it
   consumes the **same async generator** as the CLI, NOT the NDJSON projection.
3. For each yielded `RuntimeEvent` it emits **two** SSE messages: a named
   `event: agent` frame carrying the raw `RuntimeEvent.payload`, plus an
   OpenAI-style `data:` chunk with the text `delta` when present
   (`magi_agent/transport/chat.py:848-864`). On the terminal `EngineResult` it
   emits an `error` agent event if `item.error`, then closes with
   `finish_reason:"stop"` and `data: [DONE]`.

SSE frame helpers (`magi_agent/transport/chat.py:885-890`):

```
event: agent
data: {"type":"tool_start","id":"call_1","name":"Bash","input":{...}}

data: {"choices":[{"index":0,"delta":{"content":"Hello"}}]}
```

**What the local web chat GUI can reuse vs. must add:**

| Reuse as-is | Must add for a richer GUI |
|-------------|---------------------------|
| `build_headless_runtime` + `run_turn_stream` (same engine) | A non-bypass permission mode (`default`) so users approve tools |
| SSE `event: agent` carrying raw `RuntimeEvent.payload` | An outbound prompt-sink stream for `control_request` frames plus an **inbound** channel for `control_response` / cancel (SSE is one-way; the dashboard sends none) |
| OpenAI-style `delta` chunks for token streaming | Tool-call/result **cards** (the dashboard only forwards text deltas) |
| Per-turn `cancel` event seam | A way to reach that `cancel` from the browser (cancel endpoint / WS message) |

The dashboard is intentionally minimal (bypass perms, no inbound, text-only
deltas). A richer local web chat GUI needs the additions in the right column.

---

## 7. Blueprint for the local web chat GUI

### 7.1 Transport

Two viable options; both consume the *same* `run_turn_stream` generator.

- **SSE + a side channel (recommended for a quick build).** Mirror PR #145 for
  runtime events, but add a second outbound producer for prompt-sink frames.
  The server should push both `RuntimeEvent` payloads and `HeadlessSink`
  `control_request` frames into one ordered queue before writing SSE. Because
  SSE is server→client only, add a small **inbound POST endpoint** for the two
  inbound frames — `control_response` and `control_cancel_request` — that the
  server marshals into `sink.deliver(...)` / `cancel.set()`. This reuses the
  dashboard pattern and prevents the engine from waiting forever in
  `gate.check(...)` while the browser never receives the approval prompt.
- **WebSocket (recommended if you want one duplex socket).** Send outbound
  `RuntimeEvent` payloads, prompt-sink `control_request` frames, and the
  terminal; receive `control_response` / `control_cancel_request` on the same
  socket. Wire the inbound messages exactly like `_route_inbound_line`
  (`magi_agent/cli/headless.py:406-443`): `control_response` →
  `sink.deliver(ControlResponse(**msg))`, `control_cancel_request` →
  `cancel.set()`.

Run with `permission_mode="default"` (not `bypassPermissions`) so tools prompt,
and attach a `HeadlessSink` (or a custom `PromptSink`) to the gate's `sinks`
list — the gate races it just like the TUI's modal sink
(`magi_agent/cli/wiring.py:417-424`).

### 7.2 Event → React-component mapping (mirror `_fold_event`)

Mirror the TUI's fold (`magi_agent/cli/tui/app.py:562-599`). Maintain a
`Map<toolUseId, ToolCard>` for open tool calls. Pseudocode:

```ts
function onRuntimeEvent(ev) {            // ev = RuntimeEvent.payload, with ev.type inner
  if (coarse(ev) === "token") {          // payload.type === "text_delta"
    appendAssistantDelta(ev.delta ?? ev.text);   // streaming bubble
    return;
  }
  flushAssistantBubble();                // ORDERING: commit text BEFORE any tool render
  const inner = ev.type;                 // tool_start | tool_progress | tool_end | turn_phase | turn_end | ...
  switch (inner) {
    case "tool_start":    openToolCard(ev.id, ev.name, toolInput(ev)); break;
    case "tool_progress": updateToolCard(ev.id, ev); break;
    case "tool_end":      closeToolCard(ev.id, {
                            status: ev.status,                       // ok|error|blocked|needs_approval
                            output: ev.output_preview ?? ev.outputPreview,
                            rejected: REJECTED.has(ev.status) || ev.interrupted,
                          }); break;
    case "turn_end":      stopSpinners(ev.status, ev.reason); break;
    default:              appendStatusLine(statusSummary(ev));       // phases / heartbeat
  }
}
// on EngineResult / result frame: render terminal (usage, cost, error), unlock input.
```

Handle prompt-sink frames separately:

```ts
function onControlRequest(frame) {
  openApprovalModal({
    requestId: frame.request_id,
    toolName: frame.request.tool_name,
    arguments: frame.request.arguments,
    reason: frame.request.reason,
  });
}
```

Component mapping:

| Event | Component |
|-------|-----------|
| `text_delta` | streaming **AssistantBubble** (append token) |
| `tool_start` | **ToolCallCard** (collapsed: name + input summary) |
| `tool_progress` | ToolCallCard progress row (spinner / %) |
| `tool_end` (ok) | ToolCallCard result body (`output_preview`) |
| `tool_end` (blocked/error/interrupted) | ToolCallCard **rejected** state — mirror `_is_rejected_end`, statuses `{rejected,blocked,denied,deny,error}` or `interrupted` (`magi_agent/cli/tui/app.py:150-158`) |
| `turn_phase` / `heartbeat` | subtle **StatusChip** / activity line |
| `artifact` (`patch_preview`, `source_inspected`) | **DiffCard** / **CitationCard** |
| prompt-sink `control_request` | **ApprovalModal** (see 7.4) |
| `EngineResult` / `result` | terminal: usage/cost, error banner if `is_error`, unlock composer |

### 7.3 Streaming token rendering

Append `delta`/`text` to the live assistant bubble per `token` event. Optionally
coalesce on the server into one assistant message per token run (the headless
projection does this — `magi_agent/cli/headless.py:544-596`) so a re-render after
the turn shows one clean message rather than N fragments.

### 7.4 Approval modal wired to `control_response`

On a `control_request` SSE frame, open a modal showing `request.tool_name`,
`request.arguments`, `request.reason`. The user's choice POSTs/sends a
`control_response` keyed by the **same `request_id`** with a `response` body per
§3.2 (`{"decision":"allow"}`, `{"decision":"deny","feedback":"..."}`,
`{"decision":"allow","remember":true,"matcher":"cmd=ls"}`,
`{"decision":"allow","updated_input":{...}}`,
`{"decision":"deny","interrupt":true}`). The server forwards it to
`sink.deliver(ControlResponse(**body))`. If the user navigates away (channel
closes), call `sink.close()` so the pending ask fails **closed** (deny), never
open.

### 7.5 Cancellation

Expose a Stop button that sends `control_cancel_request` (or hits a cancel
endpoint) → server calls `cancel.set()` on that turn's event. Expect a synthetic
`tool_end` (`interrupted:true`) for any open tool card and a `turn_end`
(`reason:"user_interrupt"`) before the terminal `EngineResult(aborted)` — close
cards and unlock the composer on those.

### 7.6 Pitfalls (do not skip)

- **Ordering: flush assistant text before rendering a tool.** Both surfaces
  finalize the in-flight assistant block on the FIRST non-token event, *before*
  rendering the tool (`magi_agent/cli/tui/app.py:569-572`;
  `magi_agent/cli/headless.py:579-589`). If you render a tool card while tokens
  are still buffered, the transcript order is wrong.
- **Single-flight per session.** One in-flight turn per `session_id`; a second
  concurrent submit returns `aborted` / `active_session_turn`
  (`magi_agent/cli/engine.py:312-324`). Disable the composer while a turn runs,
  or use distinct session ids.
- **Dedup + EOF deny.** Handle late/duplicate `control_response` frames (drop by
  `request_id`) and fail **closed** on disconnect — match
  `HeadlessSink`'s bounded dedup and `close()` semantics
  (`magi_agent/cli/permissions.py:444-495`).
- **Read multiple field spellings.** `delta`/`text`,
  `input`/`arguments`/`input_preview`/`inputPreview`,
  `output`/`output_preview`/`outputPreview`/`result` (§2.2) — the engine and stub
  differ.
- **Branch on BOTH `event.type` and `payload.type`.** The coarse `EventKind`
  alone (e.g. `tool`) does not tell you start vs progress vs end.
- **Use `bypassPermissions` only for trusted/local contexts.** It silently skips
  the approval round-trip (`magi_agent/cli/permissions.py:499-501`). A
  user-facing GUI should run `default` (or `acceptEdits`) and show the modal.

---

## Appendix — key files

| File | Role |
|------|------|
| `magi_agent/cli/contracts.py` | `EngineDriver`, `RuntimeEvent` re-export, `EngineResult`, `Terminal`, `PermissionGate`, `PermissionDecision` |
| `magi_agent/runtime/events.py` | `RuntimeEvent` model + `EventKind` (`:37-45`) |
| `magi_agent/cli/engine.py` | `MagiEngineDriver.run_turn_stream` / `_drive`; cancel; gate callback |
| `magi_agent/cli/headless.py` | NDJSON projection (`_project_stream`), inbound reader, `run_headless` |
| `magi_agent/cli/protocol.py` | outbound/inbound frame pydantic models |
| `magi_agent/cli/permissions.py` | `RulesPermissionGate`, `HeadlessSink`, modes, control-response schema |
| `magi_agent/cli/tui/app.py` | `_fold_event` / `_render_tool_event` — the event→UI reference |
| `magi_agent/cli/wiring.py` | `build_headless_runtime` / `build_tui_app` composition roots |
| `magi_agent/transport/chat.py` | **PR #145** dashboard SSE integration (`_local_adk_chat_sse`) |
