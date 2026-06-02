# Magi Agent CLI — Design & Mapping (informed by Claude Code CLI teardown)

> **Goal:** give Magi Agent a first-class CLI with **two equal surfaces** — a full interactive **Textual/Rich TUI** and a **headless NDJSON** mode — both driving the *same* runtime engine.
> **Method:** reverse-engineered the leaked Claude Code CLI (`../cc-workspace/claude-code`) layer-by-layer (see `docs/architecture/claude-code-cli/00-overview.md` + the 7 deep-dive docs), then mapped each subsystem onto Magi's existing Python/google-ADK runtime (`infra/docker/clawy-core-agent-python/openmagi_core_agent/`, server-only today).
> **Companion execution plan:** `docs/plans/prompts/magi-track-18-cli.md` (subagent-driven PR track).
> Date: 2026-05-30. Status: design (no code yet).

---

## 1. The one architectural idea

Claude Code's whole CLI rests on a single decision: **one engine boundary — `query()`, an async generator — feeds both the interactive REPL and the headless `-p` mode.** The REPL folds yielded events into React state; the headless path runs the *identical* loop and `normalizeMessage()`-projects each event into an NDJSON `SDKMessage`. Headless is a **projection of the engine stream, not a reimplementation.**

Magi must adopt the same shape. It already has the engine: `OpenMagiRunnerAdapter.run_turn()` is an `AsyncIterator` of ADK events (`adk_bridge/runner_adapter.py:284`), already projected to public events by `OpenMagiEventBridge.project_adk_event()` and consumed in production by `RunnerSessionBoundary.run_turn()` (`runtime/runner_session_boundary.py`). **The HTTP/SSE front-end (`transport/chat.py`) is just one consumer of that stream.** The CLI adds two more consumers of the *same* stream. No new engine.

```
                         ┌─────────────────────────────────────────────────┐
                         │  Magi runtime (unchanged engine)                  │
   user turn ──────────▶ │  OpenMagiRunnerAdapter.run_turn()  ──▶ async gen  │
                         │  → OpenMagiEventBridge.project_adk_event()        │
                         │  → RuntimeEvent{type,payload,turn_id} stream      │
                         └───────────────┬──────────────┬──────────────┬─────┘
                                         │              │              │
                  (today) transport/chat.py      NEW cli headless   NEW cli TUI
                  HTTP POST + SSE writer         NDJSON writer       Textual app
                  InMemorySseWriter.agent()      stdout, 1 obj/line  RichLog widgets
```

**Design rule #1:** the CLI never calls a model or a tool directly. It builds a `RunnerTurnInput`, drains the engine async-generator, and renders. Both surfaces share one **event→view projector** and one **permission gate**; only the I/O skin differs.

---

## 2. Subsystem mapping (Claude Code → Magi)

| Claude Code subsystem | CC mechanism | Magi today | Recommendation |
|---|---|---|---|
| **Engine boundary** | `query()` async gen; `QueryParams` in, event-union out, `Terminal` return | `OpenMagiRunnerAdapter.run_turn()` async iter; `RunnerSessionBoundary` drives it | Wrap as `AsyncGenerator[EngineEvent, Terminal]`; one boundary both surfaces call |
| **Headless protocol** | dual Zod unions (stdout/stdin), ~30 variants, `system` multiplexes 16 subtypes; single `result` = exit code | `RuntimeEvent` + `_sanitize_agent_event` → SSE | New `cli/ndjson.py` projector: `RuntimeEvent` → `SDKMessage`-shaped NDJSON; reuse the sanitizer |
| **Control / permissions** | symmetric `control_request/response/cancel`, `can_use_tool`, resolve-once race of ≤4 sinks | `ControlRequestStore` (pending/approved/denied/…); disabled by default | `PermissionGate` + `PromptSink` Protocol; TUI modal + headless control-frame + hook all plug in |
| **Slash commands** | discriminated union `prompt`/`local`/`local-jsx`; 7-source precedence; dual-mode mask | `SlashControlBoundary.project()` recognizes 7 commands | Extend into a Command registry with the 3 kinds; `local-jsx` → Textual "widget command" |
| **REPL / turn loop** | one `<REPL>` component; refs as truth; `QueryGuard` machine | none (server only) | New Textual `App`; turn loop drains engine gen; cancellation via `asyncio.Event` |
| **TUI rendering** | forked Ink + pure-TS Yoga + cell-diff buffer | none | **Textual/Rich** — ~70-80% of Ink is plumbing Textual ships free; port the ~20% UX |
| **Input/keys/vim** | immutable `Cursor`; 18-context keybinding resolver; vim machine; `/@#` autocomplete | none | Textual `Input/TextArea` + thin chord resolver + `keybindings.json`; **vim deferred** |
| **Message/diff display** | per-tool render methods; line + char-level diff; Rust NAPI color | none | `ToolRenderer` Protocol; `difflib` + Rich `Text` over `Syntax` |
| **Session / resume** | append-only JSONL, `parentUuid`-DAG, resume walks tips | `WorkspaceSessionService` + `SessionContinuityBoundary.import_committed_transcript()` | JSONL session log per `(bot,session)`; resume via existing continuity boundary |
| **Entrypoint / arg-parse** | two-tier bootstrap dispatcher; Commander; lazy subcommands | `main.py` → uvicorn only | New `cli/__main__.py`; **Typer**; default callback = agent; lazy `importlib` |

---

## 3. The headless NDJSON protocol (the spine — build first)

This is the single most portable, highest-leverage piece. Both modes and any future SDK depend on it; the TUI is "the headless stream rendered."

### 3.1 Wire contract

Two message directions (mirror CC's `StdoutMessageSchema` / `StdinMessageSchema`). Every frame carries `uuid` + `session_id`.

**Outbound (Magi → stdout), one JSON object per line:**

| `type` | meaning | key fields |
|---|---|---|
| `system` + `subtype:"init"` | session handshake | `tools[]`, `model`, `mcp_servers[]`, `cwd`, `session_id` |
| `assistant` | full assistant message (a turn's content) | `message` (ADK content projected), `parent_tool_use_id?` |
| `user` | **tool results live here** (not assistant) | `message.content[]` of `tool_result` |
| `stream_event` | raw token/partial deltas | only when `--include-partial-messages` |
| `system` + `subtype:"status"\|"task_*"\|"compact_boundary"\|…` | lifecycle side-channel | (CC multiplexes 16; Magi starts with a handful) |
| `result` | **exactly one, terminal** | `subtype:"success"\|"error_max_turns"\|…`, `result`, `usage`, `total_cost_usd`, `is_error` |
| `control_request` | CLI asks host (e.g. `can_use_tool`) | `request_id`, `request{subtype,…}` |

**Inbound (stdin → Magi), bidirectional `--input-format stream-json`:**

| `type` | meaning |
|---|---|
| `user` | next user message (multi-turn over one process) |
| `control_response` | answer to a `control_request` (permission decision, etc.) |
| `control_cancel_request` | interrupt the current turn |

**Exit code** derives solely from the terminal `result.is_error`.

### 3.2 Map Magi `RuntimeEvent` → protocol

`runtime/events.py` `EventKind` = `status|token|tool|control|artifact|error`. Projector (`cli/ndjson.py`):
- `token` → `stream_event` (gated by partial flag) and accumulate into the `assistant` frame.
- `tool` (call) → part of `assistant` frame content; `tool` (result) → `user` frame.
- `control` → `control_request`.
- `error` → contributes to the terminal `result` (`is_error:true`).
- `status`/`artifact` → `system` subtypes.
Reuse `_sanitize_agent_event()` (`transport/sse.py`) verbatim — same redaction the SSE path uses.

### 3.3 Python pitfalls (from the CC teardown — do not relearn these)

1. **One `asyncio.Queue` + a single drainer task.** FIFO ordering is a *correctness* invariant: a `control_request` must never overtake the `assistant` frame that motivated it. Never write to stdout from two coroutines.
2. **Unbuffered, per-line flush.** Python buffers `sys.stdout` when piped (not a TTY) — the #1 "works in terminal, hangs in CI" bug. Write `line + "\n"` then `flush()`, or `sys.stdout.reconfigure(line_buffering=True)`.
3. **Escape U+2028/U+2029** in the JSON serializer (CC's `ndjsonSafeStringify`); they break some NDJSON readers.
4. **Guard stdout; logs → stderr.** Any stray `print()`/logging on stdout corrupts the stream. Route all logging to stderr; in dev, detect and reroute non-JSON stdout lines.
5. **`request_id` correlation dict + bounded dedup set** for control responses; drop late/duplicate answers.
6. **Threading the sub-agent tree:** ADK `transfer_to_agent` → emit `task_*` + `parent_tool_use_id`; do **not** invent a nesting frame. The stream stays flat; parents are reconstructed by id.

---

## 4. Permission gate — one concept, two surfaces

CC's cleanest idea here: a **two-layer** model.
- **Rules engine** returns `allow | deny | ask`. `allow`/`deny` short-circuit with **no UI on either surface**.
- Only `ask` prompts — and the prompt is a `Promise` the turn `await`s. Up to four racers (TUI dialog, permission hook, Bash auto-classifier, remote bridge) can resolve it; a **resolve-once `claim()`** guard makes the first winner authoritative and tears down the losers.

Magi already has `ControlRequestStore` (states `pending/approved/denied/answered/cancelled/timed_out`; `create_tool_permission_request`/`resolve_request`/`cancel_request`). Wrap it with:

```python
class PromptSink(Protocol):
    async def ask(self, req: ControlRequest) -> PermissionDecision: ...

class PermissionGate:
    async def check(self, req) -> PermissionDecision:
        d = self.rules.evaluate(req)          # allow/deny short-circuit, no UI
        if d.kind != "ask": return d
        return await self._race(req, self.sinks)  # resolve-once, tear down losers
```

- **TUI sink** → push a Textual modal (`ToolUseConfirm`), resolve on button.
- **Headless sink** → emit `can_use_tool` `control_request`, await the matching `control_response`. **Headless still prompts** (don't silently auto-allow) unless `--permission-mode acceptEdits|bypassPermissions` is set.
- **Hook sink** → optional, races the human (CC's Bash classifier pattern).

Decision options to support from day one: **allow-once**, **allow + remember rule** (persist a `PermissionUpdate` so the next identical call short-circuits in the rules engine), **reject + feedback**, and **`updated_input`** (edit the tool call before allowing). Build the resolve-once + cancel machinery *first*; it's the part that's subtly broken if retrofitted.

---

## 5. Commands — one model, masked per surface

Three command kinds (CC's discriminated union), expressed as a language-agnostic contract:

| Kind | Python signature | Returns | Works in headless? |
|---|---|---|---|
| `prompt` | `async build_prompt(args, ctx) -> list[ContentBlock]` | data → expands into a model turn (this is also what a **skill** is) | **Yes** |
| `local` | `async call(args, ctx) -> Text \| Compact \| Skip` | side-effect + text | Yes, if it opts in (`supports_non_interactive`) |
| `widget` (CC `local-jsx`) | `async call(on_done, ctx, args) -> Widget` | mounts a Textual modal/screen; **completion signalled via `on_done(...)` callback**, not the return value | **No** (interactive-only) |

- **Dual-mode mask:** each command declares `{tui, headless}` surfaces. Headless keeps `prompt` + opt-in `local`; `widget` commands are structurally excluded (CC enforces this at `main.tsx:2622` / rejects in print mode).
- **Discovery & precedence** (CC's 7 sources, earlier shadows later): bundled → builtin-plugin → skill-dir (`.claude/skills` / Magi equiv) → workflow → plugin → plugin-skills → builtins. Registry **memoized per-cwd**, but `is_enabled`/availability **re-filtered every call** so `/login`-style state changes take effect live.
- **Skills *are* commands** — one registry, two filtered views (the user `/` menu vs the model's skill-tool). Magi's `SlashControlBoundary` (recognizes `compact/reset/status/onboarding/plan/goal/superpowers`) is the seed of the `local` set.
- The `widget` callback carries `display`, `should_query`, `meta_messages`, `next_input`, `submit_next_input` — wire all five; guard the `Future` with a `done_was_called` flag (deadlock defense).

---

## 6. The interactive TUI (Textual/Rich)

**Verdict from the Ink teardown:** full UX parity is feasible and *smaller* than Ink's 96-file source suggests, because **~70-80% of `src/ink/` is a from-scratch reimplementation of things Textual ships as its core** (Ink is a thin React→ANSI bridge with no compositor; Textual *is* the compositor).

**Get for free (do NOT port the Ink versions):** flex layout (→ Textual CSS), the cell-diff double-buffer + interning pools + `log-update` engine (→ Textual's internal compositor), scrolling/`ScrollBox` (→ `ScrollView`/`RichLog`), the 23KB keypress/Kitty/SGR-mouse/bracketed-paste parser (→ Textual driver), mouse click/hover/focus/tab-cycling, alternate screen, modal dialogs, OSC-8 hyperlinks, syntax highlighting + markdown (→ Rich `Syntax`/`Markdown`), and headless rendering (Textual supports it natively).

**Custom work (the real 20%):**
1. **Streaming-transcript widget — THE one architectural risk.** Textual handles the cell-diff, but not the "don't re-parse 10k lines of markdown every 16ms" trap. **Mitigation (prototype & benchmark FIRST):** append *finalized* message blocks to a `RichLog`, keep a *single mutable* live widget for the in-flight assistant block, and **coalesce stream chunks** (~30-60ms). This is exactly where CC spent its entire optimization budget.
2. Find-in-transcript navigation (Rich gives the highlight primitive, not the nav).
3. Word-level intra-line diff coloring (Rich `Text` backgrounds layered over `Syntax` foreground).
4. Full-parity text selection (basic ships in modern Textual; word/line snap + gutter exclusion + drag-past-edge are custom).
5. Clipboard (OSC 52 vs `pyperclip`).

**Per-tool rendering** is a `ToolRenderer` Protocol (no central switch — each tool ships its own render, CC `Tool.ts:524-653`):
```python
class ToolRenderer(Protocol):
    def render_call(self, partial_input) -> RenderNode: ...   # MUST accept partial streaming input
    def render_result(self, result) -> RenderNode: ...
    def extract_search_text(self, node) -> str: ...           # must equal what's displayed (find-fidelity)
```
`RenderNode` → a Rich renderable for the TUI, plain text for NDJSON. One contract, both surfaces.

---

## 7. Input, keybindings, vim, autocomplete

- **Keybindings:** a `keybindings.json` = `{bindings: [{context, bindings: {keystroke -> action | "command:<name>" | null}}]}`. ~18 contexts, closed-enum actions, **last-wins merge** (defaults + user), **prefix-preferred chords**, `null` unbinds. Port two terminal quirks verbatim: **alt≡meta collapse** and the **escape-sets-meta** guard. Textual gives single-key/single-context bindings + event bubbling for free; a thin custom resolver in `App.on_key` owns chords + file-override merge + unbind.
- **Vim:** the state machine ports ~1:1 (~1.6k LOC of dict dispatch) but the `Cursor`/`MeasuredText` model (word classes, inclusive/linewise, grapheme/NFC) that `TextArea` lacks is the real cost. **Full parity ≈ 1.5–2.5 weeks → DEFER to v1.1.** Optional v1 "vim-lite" (`NORMAL/INSERT` + `hjkl w b 0 $ x dd`) is ~20% of the effort.
- **Autocomplete:** prefix router — `/`commands (ghost text), `!`bash, `@`files/agents/MCP, `#`channels — over the pre-cursor slice. Use `rapidfuzz` (replaces nucleo+Fuse), cap ~15, debounce 50/150ms with token-staleness guards (`@work(exclusive=True)`). Menu via a custom `OptionList` overlay; ghost text = `value + dim(ghost)`.

---

## 8. Session & resume

- Persist an **append-only JSONL** log per `(bot_id, session_id)` (CC: `~/.claude/projects/<cwd>/<id>.jsonl`, batched ~100ms). Each durable event = one line wrapped in a `parent_uuid`-linked envelope → a DAG, not a flat list (supports branching/rewind).
- **Resume/continue:** parse lines → uuid map → find leaf tip → walk `parent_uuid` back → rebuild the linear message list → strip envelopes → feed as `initial_messages` to a fresh engine drain (same path as live).
- Magi already has the rehydration primitive: `SessionContinuityBoundary.import_committed_transcript()` + `WorkspaceSessionService`. The CLI supplies session identity via flags/env (parallel to `parse_session_identity()` reading headers today).

---

## 9. Entrypoint & cold start

- **Framework: Typer** (Click underneath). Root `@app.callback(invoke_without_command=True)` = the default **agent** command taking `[prompt]` + flags; `add_typer(...)` groups mirror CC's sibling subcommands (`mcp`, `config`, `doctor`, `auth`, …); `resume`/`continue` are flags, not subcommands.
- **Mode branch** (CC's core decision): `is_non_interactive = bool(prompt_arg or -p or not stdin.isatty())` → headless `run_headless()`; else `launch_repl()`.
- **Cold start matters more in Python** (import cost is real). Strategy:
  - A thin stdlib-only console-script does Layer-0 fast paths (`--version` with zero heavy imports).
  - Lazy per-command impls via `importlib`; lazy Click subcommands.
  - **Defer `textual` + `google-adk` imports to the interactive branch only** — headless NDJSON must not pay for Textual.
  - Per-cwd memoized command registry built lazily, never at import.
  - Gate with `python -X importtime`.

---

## 10. Phased roadmap (→ see `magi-track-18-cli.md` for the PR-by-PR execution plan)

All CLI code lands under a new `openmagi_core_agent/cli/` package, **default-OFF / additive** (the server entrypoint `main.py` is untouched). No engine changes.

| Phase | PR | Deliverable | Gate/flag |
|---|---|---|---|
| **A. Spine** | PR1 | `cli/ndjson.py` protocol + `cli/headless.py`: `magi -p "..." --output text\|json\|stream-json`, single-`result` exit codes, control-frame plumbing (permission still "deny-by-default" placeholder) | `MAGI_CLI_ENABLED` |
| | PR2 | Engine boundary: `cli/engine.py` exposing `run_turn` drain as `AsyncGenerator[EngineEvent, Terminal]` reusing `RunnerSessionBoundary`; cancellation (`asyncio.Event` + orphan-tool_result synthesis); single-flight | — |
| | PR3 | Session JSONL + `--resume`/`--continue` via `SessionContinuityBoundary` | — |
| **B. Permissions** | PR4 | `PermissionGate` + `PromptSink` (headless control-frame sink first); rules engine allow/deny/ask; resolve-once + cancel | reuses `ControlRequestStore` |
| **C. Commands** | PR5 | Command registry (3 kinds + dual-mode mask) seeded from `SlashControlBoundary`; discovery/precedence; `prompt`+`local` work headless | — |
| **D. TUI** | PR6 | **Streaming-transcript spike** (RichLog + single live widget + chunk coalescing) — benchmark before building more | `textual` extra |
| | PR7 | Textual `App` + REPL turn loop draining the same engine; TUI `PromptSink` modal; input widget + autocomplete (`/@#`) | — |
| | PR8 | `ToolRenderer` Protocol + diff rendering (`difflib` + Rich `Text`/`Syntax`); per-tool views | — |
| | PR9 | Keybindings (`keybindings.json` + chord resolver, last-wins merge); **vim deferred** | — |
| **E. Rollout** | PR10 | Console-script entrypoint (Typer), cold-start lazy-import discipline, `--version` fast path; docs + `magi --help` | `MAGI_CLI_ENABLED` → default on |

**Sequencing rationale:** the headless spine (A) is the contract the TUI renders; permissions (B) and commands (C) are surface-agnostic and reusable; the TUI (D) is built last and *on top of* the proven stream, de-risked by the PR6 transcript spike.

---

## 11. What to explicitly NOT copy from Claude Code

- The forked Ink + pure-TS Yoga + cell-diff buffer + BSU/ESU atomic output — Textual owns all of this.
- The 23KB keypress/mouse/paste parser — Textual's driver owns it.
- React-specific machinery (refs-as-truth, `useSyncExternalStore`, the React Compiler) — irrelevant in Python; use plain async + Textual reactivity.
- `local-jsx`'s JSX return — replaced by the Textual widget + `on_done` callback contract.
- Build-time `feature()` DCE for ant-only branches — Magi has no such split.

---

## 12. Open questions for Kevin

1. **Distribution:** ship the CLI as a separate console-script/extra (`pip install magi-cli` / `magi`) or bundle into the existing image? (Affects cold-start + dependency surface — Textual is interactive-only.)
2. **Auth/credit:** the CLI hits the same api-proxy billing path as bots? Per-user token vs bot token for a developer running `magi` locally?
3. **Permission default in headless:** prompt-by-default (safe, CC-style) vs `acceptEdits` default for DX? Recommend prompt-by-default with an explicit `--permission-mode` opt-out.
4. **vim in v1?** Recommendation: defer (v1.1); ship vim-lite only if there's demand.
5. **Scope of v1 TUI:** transcript + input + permissions + diff is the MVP; do we want find-in-transcript, text selection, and `@`-file autocomplete in v1 or v1.1?
