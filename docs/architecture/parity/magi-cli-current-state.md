# Magi CLI — Current-State + Reuse Inventory (CLI Parity Audit)

Audit target: `/Users/kevin/Desktop/claude_code/magi-agent-oss-worktrees/cli-parity-plan`
(package root `magi_agent/`, branch `feat/cli-parity-plan`, HEAD `e49a695`).

Goal: give the local `magi` CLI agent the REAL tool-execution loop + REAL system
prompt (Claude Code / OpenCode parity), reusing existing parts.

> NOTE on the brief's premise. The brief describes `magi_agent/cli/real_runner.py`
> with `build_cli_model_runner` building `Agent(model=LiteLlm(...),
> instruction=DEFAULT_INSTRUCTION, tools=[])`. **That file/symbol does not exist on
> this branch** (`grep -rn build_cli_model_runner magi_agent/` → no matches; HEAD is
> at PR #139, the described work is PR #140 / downstream). The *actual* default CLI
> runner today is `magi_agent/cli/local_runner.py::build_local_cli_runner` →
> `LocalCliRunner`, which is **even more inert**: it is model-free, emits one canned
> text event, and attaches **no tools and no real instruction**
> (`cli/local_runner.py:11-65`). Everything below audits the real current state; the
> seams identified are exactly the ones a `build_cli_model_runner` must wire.

---

## 0. The load-bearing fact (how the CLI agent gets tools + prompt)

The CLI engine does NOT build an Agent. It is handed a `runner` and only drives it:

- `MagiEngineDriver._resolve_runner` takes `runner` (or `runtime.runner`) — `cli/engine.py:243-250`.
- It wraps it in `OpenMagiRunnerAdapter(runner=runner)` and calls `adapter.run_turn(...)` — `cli/engine.py:398, 452-453`.
- `OpenMagiRunnerAdapter.run_turn` just calls `self.runner.run_async(user_id, session_id, invocation_id, new_message)` — `adk_bridge/runner_adapter.py:289-297`.
- `RunnerTurnInput` carries **only** ids + `new_message` + opaque `harness_state`/`state_delta`/`run_config` — there is **NO per-turn `systemInstruction` and NO per-turn `tools` field** — `adk_bridge/runner_adapter.py:242-257`; the adapter's allowlisted kwargs are `user_id/session_id/invocation_id/new_message` only (`runner_adapter.py:276-287`).

**Conclusion (the seam):** the system prompt and the tool set are properties of the
ADK `Agent`/`Runner` object the CLI *constructs*, baked in at runner-build time
(`Agent(model=..., instruction=<prompt>, tools=<adk tools>)`). The engine, adapter,
event bridge, and permission gate are all reusable as-is and runner-agnostic. The
ONLY place new wiring is required is the **runner factory** that today is
`cli/local_runner.py::build_local_cli_runner` (called by `cli/wiring.py:189-192`).

---

## 1. The tool-host system (`magi_agent/tools/`)

### Manifest schema — `tools/manifest.py`
`ToolManifest` (pydantic, frozen) — `manifest.py:39-173`. Key fields:
`name, description, kind, source, permission ("read"/"write"/"execute"/"net"/"meta"),
input_schema, dangerous, mutates_workspace, available_in_modes (plan/act),
parallel_safety, side_effect_class, adk_tool_type ("FunctionTool"/"LongRunningFunctionTool"),
timeout_ms, budget, enabled_by_default, tags`. Validators enforce side-effect/parallel
coherence (`manifest.py:95-173`).

### Registration / dispatch contract
- `ToolRegistration(manifest, handler, enabled, protected)` — `tools/base.py:15-20`. A
  registration with `handler=None` is metadata-only (not executable).
- `ToolRegistry` — `tools/registry.py:42-161`: `register` (handler optional,
  `enabled=manifest.enabled_by_default`), `bind_handler` (attach a handler + optionally
  force-enable via `enabled_by_registry_policy`), `resolve_enabled`, `list_available(mode)`.
  Core/builtin tools are `protected` (`registry.py:164-165`).
- `ToolDispatcher.dispatch(name, args, ToolContext, mode, exposed_tool_names)` —
  `tools/dispatcher.py:81-225`: resolves registration → checks exposed/enabled/mode →
  validates args against manifest schema → `tool_handler_missing` if `handler is None`
  (`dispatcher.py:165-178`) → runs the GeneralAutomation live-gate + `ToolPermissionPolicy`
  → executes the handler (optionally offloaded to a thread for readonly/concurrency-safe
  tools) → returns a `ToolResult`.

### Catalog — what tools exist
`tools/catalog.py:44-238` registers **metadata-only** core manifests (handler=None):
`ToolSearch, FileRead, FileWrite, FileEdit, PatchApply, Glob, Grep, Bash, TestRun,
GitDiff, AskUserQuestion, EnterPlanMode, ExitPlanMode, ArtifactCreate/Read/List, Clock,
Calculation, HealthStatus, TaskList/Get/Output, CronList`. All `enabled_by_default=True`
but **inert without a bound handler**.

### Real handler sources (which tools actually execute)

| Handler source | Real tools | File:line |
|---|---|---|
| **`CoreToolhostHandlerSet` / `bind_core_toolhost_handlers`** | `Clock, Calculation, FileRead, Glob, Grep, FileWrite, FileEdit, PatchApply, Bash` (`CORE_TOOLHOST_DIRECT_TOOL_NAMES`) | `tools/core_toolhost.py:14-115` |
| **`Gate5BFullToolHost`** (the real implementations the above binds to) | legacy 9 (Clock/Calc/FileRead/Glob/Grep/FileWrite/FileEdit/PatchApply/Bash) executed **directly** + registry-dispatched first-party tools | `gates/gate5b_full_toolhost.py:691-856` |
| **`LocalReadOnlyToolHost`** (env-driven, readonly only) | `FileRead, Glob, Grep, GitDiff` (real fs reads, redaction, ripgrep) | `tools/local_readonly.py:29, 113-662` |
| **`LocalFakeToolHost`** (FAKE / receipt-only) | `LocalEchoReceipt, LocalStatusReceipt` — return synthetic receipts, **no effect** | `adk_bridge/local_toolhost.py:9, 42-95, 170-178` |

**Real vs fake summary:**
- **REAL, executes side effects**: `FileRead, FileWrite, FileEdit, PatchApply, Glob,
  Grep, Bash` (`Gate5BFullToolHost._handle`, `gate5b_full_toolhost.py:698-855`) — full
  workspace IO, real `subprocess.run` for Bash (`:837`), Codex-style envelope patch,
  optional fuzzy-edit/format-on-write/LSP-diagnostics/ripgrep.
- **REAL, readonly only**: `FileRead, Glob, Grep, GitDiff` via `LocalReadOnlyToolHost`
  (GitDiff is fixture-backed, `local_readonly.py:487-494`).
- **REAL but metered/policy-bearing**: `Clock`, `Calculation` (deterministic),
  plus the broad first-party `GATE5B_FULL_TOOLHOST_TOOL_NAMES` registry list
  (`gate5b_full_toolhost.py:76-139`) which dispatch through `ToolDispatcher` and only
  fire if a handler is bound in the registry (most are NOT bound on the OSS default path).
- **FAKE / inert**: `LocalEchoReceipt`, `LocalStatusReceipt` (`local_toolhost.py`).

### `core_toolhost` is the reusable, ungated execution path
`CoreToolhostHandlerSet` constructs a `Gate5BFullToolHost` with a config that is
**already unblocked** — `enabled=True`, `killSwitchEnabled=False`, `environment="local"`
(`core_toolhost.py:43-55`) — and calls `host.dispatch(...)` directly
(`core_toolhost.py:72-105`), **bypassing** the heavily-gated
`build_gate5b_full_toolhost_bundle` scope checks. This is the single most reusable
"real local tools" entry point. It binds handlers onto the registry's catalog manifests
(`core_toolhost.py:58-70`), so after `register_core_tool_manifests(registry)` +
`bind_core_toolhost_handlers(registry)` the registry has **9 real, enabled, executable
tools**.

**Reuse verdict:** REUSABLE AS-IS for the CLI. No flag flip needed (the gating is
deliberately bypassed by `CoreToolhostHandlerSet`). Seam: build a registry, register
core manifests, bind core handlers.

---

## 2. ADK tool adapter (`adk_bridge/tool_adapter.py`, `local_toolhost.py`, `local_runner.py`)

### How a toolhost tool becomes an ADK tool — `tool_adapter.py`
`build_adk_tool_for_manifest(manifest, dispatcher, mode, tool_context_factory,
exposed_tool_names)` — `tool_adapter.py:52-73`. It wraps the manifest in an async
callable `invoke_openmagi_tool(arguments, tool_context)` that calls
`dispatcher.dispatch(manifest.name, arguments, tool_context_factory(tool_context),
mode=mode, exposed_tool_names=...)` and returns `result.model_dump(by_alias=True)`
(`tool_adapter.py:25-49`), then constructs an ADK `FunctionTool` (or
`LongRunningFunctionTool`) named after the manifest (`tool_adapter.py:67-73`).

Bulk builders:
- `build_adk_function_tools_for_registry(registry, dispatcher, mode, tool_context_factory,
  attach_enabled, exposed_tool_names, exclude_names)` — `tool_adapter.py:230-258`. Returns
  `[]` unless `attach_enabled=True`; otherwise builds one ADK FunctionTool per
  `registry.list_available(mode)` filtered by `exposed_tool_names`.
- `build_adk_function_tools_for_granted_names(...)` — `tool_adapter.py:261-287`.

**This is the exact factory that turns the real core registry into a `tools=[...]` list
for an ADK `Agent`.** It needs: a registry with bound handlers (§1), a `ToolDispatcher`,
a `tool_context_factory` (maps the ADK `ToolContext` → `magi_agent.tools.context.ToolContext`,
must carry `workspace_root`), and `attach_enabled=True`.

**Reuse verdict:** REUSABLE AS-IS. The CLI runner factory calls this to produce the ADK
tool list. The only new code is a small `tool_context_factory` and passing
`attach_enabled=True`.

### `local_toolhost.py` — the FAKE adapter (not for parity)
`LocalToolHostAdkBundle` + `build_local_toolhost_adk_tools` build ADK FunctionTools that
call `LocalFakeToolHost.record_call` → synthetic receipts only (`local_toolhost.py:98-150`).
`is_local_fake_receipt_adk_tool` tags them (`:153-158`). **Use is a deliberate dead-end:
it exists to test attachment plumbing without real effects. Do NOT reuse for the real CLI
loop.**

### `build_local_adk_runner` (`adk_bridge/local_runner.py`) — why it is "local/inert"
`build_local_adk_runner(...)` — `local_runner.py:89-178`. It:
- Requires `CORE_AGENT_PYTHON_LOCAL_ADK_RUNNER` truthy or raises `LocalAdkRunnerDisabled`
  (`local_runner.py:85-99`).
- Builds `Agent(model=LocalInertLlm(...), instruction=<test string>, tools=<fake tools>)`
  (`local_runner.py:102-107`).
- **`LocalInertLlm.generate_content_async` raises `LocalAdkRunnerExecutionBlocked`** to
  guarantee zero provider traffic (`local_runner.py:50-60`).
- **Only accepts `LocalToolHostAdkBundle` fake-receipt tools** — `_local_toolhost_bundle_tools`
  rejects any non-fake tool (`local_runner.py:194-211`).

So it is structurally incapable of a real loop: inert model + fake-only tools. It is an
attachment/boundary test harness, NOT a usable CLI runner. **It DOES, however, demonstrate
the correct real wiring** (Agent + WorkspaceSessionService + InMemoryMemory/Artifact +
plugin composition for edit-retry/resilience/compaction, `local_runner.py:108-178`) — that
plugin/App/Runner composition is a reusable template for the real CLI runner, just swap
`LocalInertLlm`→`LiteLlm`/real model and fake tools→real ADK tools from §2.

---

## 3. Full-toolhost path (`gates/gate5b_full_toolhost.py`)

### The "production/full" wiring exists
`build_gate5b_full_toolhost_bundle(config, scope, workspace_root, tool_registry, ...)` —
`gate5b_full_toolhost.py:1364-1421`. On a passing scope it returns a `Gate5BFullToolBundle`
with `status="ready"` and `tools=(<ADK FunctionTool per exposed name>)` (`:1406-1421`).
The exposed names are the union of the legacy 9 + the broad first-party registry list
(`GATE5B_FULL_TOOLHOST_TOOL_NAMES`, `:64-139`).

### What gates it (it is heavily flag/scope-gated)
`_selected_scope_error` (`gate5b_full_toolhost.py:1424-1451`) blocks unless ALL hold:
`config.enabled`, `not kill_switch_enabled`, `route_attachment_enabled`,
`scope.selectedBotDigest == config.selected_bot_digest`, owner digest match,
environment match + in allowlist, `max_tool_calls_per_turn > 0`, workspace dir exists.
`Gate5BFullToolAttachmentFlags` hard-pins several authorities to `Literal[False]`
(`productionAttached`, `memoryWriteAllowed`, `channelDeliveryAllowed`, `dbWriteAllowed`,
etc. — `:310-333`), i.e. it is a *diagnostic/canary* surface, not a general-purpose loop.

### Env defaults (server) keep it OFF
`build_gate5b_full_toolhost_config_from_env` (`transport/chat.py:649-736`):
`enabled = CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_ENABLED` (default falsy →
**False**), `killSwitchEnabled` default `"1"` → **True**. So out of the box the bundle
returns `status="disabled"`/`"blocked"` and `tools=()`.

### Is it usable for a local CLI?
Indirectly. The *implementation* (`Gate5BFullToolHost._handle`) is exactly the real tool
logic the CLI wants, but the *bundle builder*'s scope machinery is designed for hosted
selection, not a local CLI. The OSS code already provides the clean bypass:
`CoreToolhostHandlerSet` (§1) instantiates `Gate5BFullToolHost` directly with an
enabled/kill-switch-off config and skips the scope gate. **Recommended path for the CLI is
the `core_toolhost` bypass, NOT `build_gate5b_full_toolhost_bundle`.**

**Reuse verdict:** the full-toolhost *engine* is reusable via the core_toolhost seam (no
flag flip). The gated *bundle* is NOT the right entry point for a local CLI (would need
many env flags + digest scope set, and even then pins authorities false).

---

## 4. System-prompt assembly (`runtime/message_builder.py`)

### What it produces
`build_system_prompt(...)` → a single joined string (`message_builder.py:620-662`).
`build_system_prompt_blocks(...)` → list of `{"type":"text","text":...}` blocks, with
optional provider cache markers when `cache_enabled=True` (`:665-771`). Both route through
the single assembler `_assemble_prompt_sections` (`:377-443`).

### Sections it assembles (real, Claude-Code-grade)
Static (cacheable): rendered identity (BOOTSTRAP/SOUL/LEARNING/IDENTITY/USER/AGENTS/TOOLS,
`:38-46, 936-952`), `DEFERRAL_PREVENTION_BLOCK`, `OUTPUT_RULES_BLOCK`,
`OUTPUT_EFFICIENCY_BLOCK`, `ACTION_SAFETY_BLOCK`, and on `coding_agent=True`
`CODING_DISCIPLINE_BLOCK` + `TOOL_PREFERENCES_BLOCK` + optional per-family coding hint
(`:408-431`). Dynamic: session/turn/time/channel header, runtime temporal context,
memory-mode block, user addendum (`:432-443`). Notably `TOOL_PREFERENCES_BLOCK` already
tells the model to prefer `FileRead/FileEdit/Glob/Grep` over shell (`:243-256`) — directly
relevant once real tools are attached.

### What it needs as inputs
All keyword-only and **all optional with safe defaults** (`:620-635`): `session_key`,
`turn_id` (required-ish, plain strings), then optional `identity` (Mapping), `channel`,
`user_message`, `now`, `timezone`, `coding_agent: bool`, `model: str`,
`model_aware_prompts_enabled`, plus optional `hook_bus`/`harness_state`/`hook_context`/
`evidence_sink` (all default `None` → no hooks fired). **No runner, no ADK, no network
dependency.** A minimal call `build_system_prompt(session_key=..., turn_id=...,
coding_agent=True, model=<model>)` yields a complete prompt.

### Can the CLI reuse it for the Agent instruction?
**Yes — directly.** `ADK Agent.instruction` accepts a static string. Call
`build_system_prompt(...)` once at runner-build time and pass the result as
`Agent(instruction=...)`. Caveat: the prompt embeds the per-turn header
(`[Session][Turn][Time]`) and temporal context; baked into a static instruction it is
fixed for the runner's life. That is acceptable for a single-session CLI; for freshness
`refresh_runtime_time_header` exists (`:332-350`) but is not required for parity. Identity
sections (SOUL/AGENTS/TOOLS) would be empty unless the CLI loads local identity files into
the `identity` mapping (a small, optional enhancement).

### Important: it is currently wired to NOTHING live
`grep` shows `build_system_prompt` is referenced only inside `prompt/injection.py`,
`prompt/splitter.py`, and its own tests — **no runner, app, or transport calls it.** Even
the server's real Agent uses a hand-written diagnostic string `_build_shadow_instruction`
(`shadow/gate5b4c3_live_runner_boundary.py:1158-1166`), not `build_system_prompt`. So the
CLI would be the FIRST consumer to wire this into a live `Agent.instruction`.

**Reuse verdict:** REUSABLE AS-IS as the instruction source. Seam: call it in the CLI
runner factory, pass result to `Agent(instruction=...)`. New code = one call + optional
identity-file loading.

---

## 5. How the server agent runs tools (`runtime/adk_turn_runner.py`, `main.py`, `app.py`, transport)

### There is a real Agent-construction path, but it is gated + diagnostic
The only live `Agent(...)`/`Runner(...)` construction with real tools is in the Gate5B-4c-3
shadow live-runner boundary: `primitives.Agent(instruction=runner_input.system_instruction,
tools=list(self._adk_tools), model=...)` (`shadow/gate5b4c3_live_runner_boundary.py:512-533`),
`primitives.Runner(...)` (`:564`), driven via `runner.run_async(...)` (`:619`).
- Its tools come from `gate1a_bundle.tools` (the readonly Gate1A bundle) passed as
  `adk_tools` only when `gate1a_bundle.status == "ready"` — `transport/chat.py:2418`.
- Its instruction is the hand-written `_build_shadow_instruction` (NOT `build_system_prompt`)
  — `gate5b4c3_live_runner_boundary.py:1158-1166`.
- The whole path requires `shadow_config.live_runner_boundary_enabled` + a counter store +
  budget reservation + scope/env gating — `transport/chat.py:2321-2421`.

### `AdkTurnRunner` (`runtime/adk_turn_runner.py`) is a LOCAL/INERT test boundary
`AdkTurnRunner.run_turn` (`adk_turn_runner.py:496-672`) only accepts a
`LocalAdkTurnRunnerBoundary` wrapping a `LocalAdkReplayRunner`
(`_validate_local_runner_candidate`, `:924-936`) and **rejects any live ADK/provider
runner** (`:928-929`) and the production `local_runner` (`:930-931`). Its
`AdkTurnAuthority`/`AdkTurnProductionWrites` pin every authority to `Literal[False]`
(`:231-381`). It is a replay/attestation harness, not a real execution path.

### The DEFAULT server chat path is also model-free
`_local_adk_chat_response` calls `build_local_response(...)` — the same canned
model-free text as the CLI default runner — `transport/chat.py:791-800`,
`cli/local_runner.py:55-65`. `main.py`/`app.py` build config + store
`gate5b_full_toolhost_config` on the runtime (`main.py:54-72`) but do not, by default,
attach real tools to a real model.

**Conclusion:** the SERVER does NOT run a real general-purpose tools+prompt loop on the
default path either; its real-tool path is the gated/diagnostic shadow boundary with a
readonly bundle and a diagnostic instruction. There is **no existing "rich server agent"
to copy a turnkey real loop from** — the reusable real pieces are the lower-level building
blocks (§1–§2–§4) plus the Agent/Runner/plugin composition template in
`adk_bridge/local_runner.py:108-178`.

---

## 6. Permission seam (`cli/engine.py`, `cli/permissions.py`, `tools/permission.py`)

### Two distinct permission layers exist; they are complementary, not duplicative.

**(a) Tool-time, runner-side (`tools/permission.py`)** — runs INSIDE `ToolDispatcher`.
`ToolPermissionPolicy.decide(manifest, args, ctx, mode)` (`tools/permission.py:42-122`)
returns `allow`/`deny`/`ask`. `ask` is produced for dangerous / workspace-mutating /
write-execute-net / `requires-approval`-tagged tools (`approval_required_reason`,
`:125-134`). The dispatcher maps `ask` → `ToolResult(status="needs_approval")`
(`dispatcher.py:199-200`). **This layer has no interactive surface of its own** — it just
emits `needs_approval` in the result. In the `core_toolhost` bypass it is sidestepped:
`Gate5BFullToolHost` calls a permission *preflight* (`_preflight_legacy_tool` →
`ToolPermissionPolicy().decide`, `gate5b_full_toolhost.py:899-934`) but with a
`selected_full_toolhost` scope that **pre-approves** dangerous tools
(`selected_full_toolhost_preapproved`, `permission.py:137-159`). So through core_toolhost,
Bash/FileWrite run WITHOUT asking — the interactive gate must come from layer (b).

**(b) CLI engine before_tool_callback gate (`cli/engine.py` + `cli/permissions.py`)** —
the real interactive seam. `MagiEngineDriver._attach_gate_callback` prepends an async ADK
`before_tool_callback` onto `runner.agent` (`cli/engine.py:722-760`); the callback
`_build_gate_before_tool` (`:771-842`) builds a `ControlRequest` per tool call and awaits
`gate.check(req)` (`:799-811`). Returning a dict DENIES (skips the tool, dict becomes the
result); returning `None` ALLOWS; mutating `args` rewrites input — and an allow+rewrite is
re-validated against `gate.rules` to prevent escalation (`:818-840`). This fires **before
ADK invokes the tool's `run_async`**, so it intercepts every tool regardless of toolhost.

The gate is `RulesPermissionGate` (`cli/permissions.py:216-253`): `RulesEngine.evaluate`
returns `allow`/`deny`/`ask` from static+remembered glob rules, **default `ask` (never
silent allow)** (`permissions.py:163-213`). On `ask` it races `PromptSink`s
(`_race`, `:255-335`). Sinks:
- `HeadlessSink` (`permissions.py:387-601`): emits one `control_request` NDJSON frame and
  awaits a `control_response`; supports `default`/`acceptEdits`/`bypassPermissions` modes
  (`:498-518`); EOF → safe deny (`:476-495`).
- TUI sink: `build_tui_app` appends `app.sink` (a `TextualSink`) to `rt.gate.sinks`
  (`cli/wiring.py:261-268`) so an `ask` opens a ToolUseConfirm modal in the TUI.

### Where tool-time approval surfaces in the TUI
The `before_tool_callback` → `gate.check` → `ask` → race → `app.sink.ask` →
Textual ToolUseConfirm modal. **The wiring already exists end-to-end**
(`cli/wiring.py:261-268` + `cli/engine.py:722-842`). The one gap: the gate is only attached
when the engine is invoked with a non-None `gate` (`run_turn_stream(..., gate=...)`,
`cli/engine.py:303-308, 431-433`); `build_headless_runtime` constructs the gate but the
headless caller must thread it in, and `RulesPermissionGate()` is built with **no sinks**
on the headless path (`cli/wiring.py:168`), so headless `ask` → safe deny until a
`HeadlessSink` is added. TUI is wired.

### Interaction between (a) and (b)
They compose: (b) intercepts at the ADK callback BEFORE the tool runs; if (b) allows, the
tool executes and (a) runs inside the dispatcher (in core_toolhost, pre-approved). For
parity the CLI relies on (b) for interactive approval and lets (a) handle schema/mode
enforcement. No conflict; (b) is the user-facing seam.

**Reuse verdict:** REUSABLE AS-IS. The TUI approval path is fully wired. Gap: pass `gate`
into the headless engine call + attach a `HeadlessSink` to the headless gate for non-TUI
approvals.

---

## Reusable as-is

1. **Engine / adapter / event bridge** — `MagiEngineDriver` (`cli/engine.py:202-574`),
   `OpenMagiRunnerAdapter` (`adk_bridge/runner_adapter.py:272-300`), `OpenMagiEventBridge`
   (used at `cli/engine.py:399, 472`). Runner-agnostic; drive any real ADK runner.
2. **Real local tools** — `register_core_tool_manifests` + `bind_core_toolhost_handlers`
   (`tools/catalog.py:245-249`, `tools/core_toolhost.py:108-115`) yielding 9 real,
   enabled tools (FileRead/Write/Edit, PatchApply, Glob, Grep, Bash, Clock, Calculation)
   via `Gate5BFullToolHost` — already ungated by the core_toolhost bypass.
3. **Toolhost→ADK adapter** — `build_adk_function_tools_for_registry(... attach_enabled=True)`
   (`adk_bridge/tool_adapter.py:230-258`) + `build_adk_tool_for_manifest` (`:52-73`).
4. **System prompt** — `build_system_prompt` / `build_system_prompt_blocks`
   (`runtime/message_builder.py:620-771`); minimal inputs, no deps, ready as
   `Agent(instruction=...)`.
5. **Permission gate (interactive)** — `RulesPermissionGate` + `RulesEngine` +
   `HeadlessSink` (`cli/permissions.py`), engine `before_tool_callback` attach
   (`cli/engine.py:722-842`), TUI sink wiring (`cli/wiring.py:261-268`).
6. **Runner/plugin composition template** — `adk_bridge/local_runner.py:108-178` (App +
   Runner + WorkspaceSessionService + InMemory memory/artifact + edit-retry/resilience/
   compaction plugins). Copy the composition; swap inert model + fake tools for real ones.
7. **Permission policy (mode/schema enforcement)** — `ToolPermissionPolicy`
   (`tools/permission.py:42-122`), runs inside the dispatcher automatically.

## Gaps to build

1. **A real CLI runner factory** (the missing `build_cli_model_runner`). Today
   `cli/local_runner.py::build_local_cli_runner` returns a model-free, tool-less
   `LocalCliRunner` (`cli/local_runner.py:11-65`); `build_local_adk_runner` is inert by
   design (§2). Need a NEW factory that builds
   `Agent(model=<real LiteLlm/Gemini>, instruction=build_system_prompt(...), tools=<real
   ADK tools from §2>)` + `Runner`, following the `adk_bridge/local_runner.py:108-178`
   template. This is the ONE place all reusable parts converge. Wire it into
   `cli/wiring.py:189-192` (`_build_default_runner`).
2. **A real model** — no `LiteLlm` is constructed anywhere on this branch
   (`grep LiteLlm` → only a doc comment in `tool_adapter.py:435`). Need provider/model
   selection (model string already plumbed but unused: `cli/wiring.py:140` "reserved seam").
3. **`tool_context_factory`** — map ADK `ToolContext` → `magi_agent.tools.context.ToolContext`
   carrying `workspace_root` (cwd) + session/turn ids, so dispatched tools operate on the
   right workspace. None exists for the CLI today.
4. **Headless approval wiring** — pass `gate` into the headless engine call and attach a
   `HeadlessSink` to the headless `RulesPermissionGate` (built sink-less at
   `cli/wiring.py:168`); add the inbound stdin reader that calls `HeadlessSink.deliver`
   (noted as deferred in `cli/permissions.py:392-402`). TUI path already wired.
5. **Identity loading (optional, parity polish)** — load local SOUL/AGENTS/TOOLS files into
   the `identity` mapping for `build_system_prompt` so the CLI prompt has real identity
   sections (otherwise they render empty; `message_builder.py:936-952`).
6. **First-party tool handlers (optional)** — the broad `GATE5B_FULL_TOOLHOST_TOOL_NAMES`
   list (`gate5b_full_toolhost.py:76-139`, e.g. WebFetch/WebSearch/TaskBoard) is
   registry-dispatched but has no bound handlers on the OSS default path, so only the 9
   core tools execute. Binding more is additive, not required for a coding-loop parity MVP.
