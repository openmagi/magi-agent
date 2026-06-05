# Magi CLI — Claude Code / OpenCode Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement each PR task-by-task. Each PR below is executed in its own worktree as an independent unit; within a PR, steps use checkbox (`- [ ]`) syntax for tracking. Before executing a PR, write that PR's detailed bite-sized task plan (just-in-time) by re-reading the current code at the cited seams.

**Goal:** Make the local `magi` CLI behave like Claude Code / OpenCode — a real agentic loop where the model uses real tools (read/write/edit/bash/grep/glob) under interactive permission gating, with tool calls/results streamed to the terminal.

**Architecture:** All the hard parts already exist in `magi_agent` and are runner-agnostic. The CLI engine drives whatever ADK `Runner` it is given; the system prompt and tool set are baked into the `Agent` at runner-build time. Parity is therefore a **wiring** effort centered on one factory (`build_cli_model_runner`, added by PR #140), plus four independent follow-ups (headless approval, TUI rendering, prompt/plan-mode steering, safety/budget parity).

**Tech Stack:** Python 3.11+, `google-adk==1.33.0` (Agent/Runner/FunctionTool), ADK `LiteLlm` (via optional `litellm` extra), existing `magi_agent.tools` toolhost, `magi_agent.runtime.message_builder`, `magi_agent.cli` engine/permissions/TUI.

---

## Reference architecture docs (read before implementing)

- `docs/architecture/parity/opencode-agent-loop.md` — OpenCode tool contract, agent loop, suspendable `ask()` permission, edit fuzzy cascade, bash safety.
- `docs/architecture/parity/cc-agent-loop.md` — CC uniform Tool protocol, async-gen loop where `tool_use` is the only continue signal, fail-closed permission pipeline, `control_request`/`control_response` headless protocol, TodoWrite/plan-mode steering.
- `docs/architecture/parity/magi-cli-current-state.md` — **the load-bearing audit**: exactly which Magi parts are reusable and the precise seams (file:line). Every PR below cites it.

## The convergence seam (from the current-state audit §0)

The CLI engine (`cli/engine.py:243-250`) is handed a `runner` and only drives it via `OpenMagiRunnerAdapter` (`adk_bridge/runner_adapter.py:289-297`), passing per-turn only `user_id/session_id/invocation_id/new_message`. **There is no per-turn tools or systemInstruction.** Therefore the system prompt and tool set are properties of the `Agent` the CLI *constructs* at runner-build time. The single place to wire everything is the runner factory selected by `_build_default_runner` (`cli/wiring.py:189-192`).

## Base dependency

**PR #140** (`feat/cli-real-multiprovider-runner`, open) adds `cli/providers.py` (provider/key resolution for openai/anthropic/gemini/fireworks) and `cli/real_runner.py::build_cli_model_runner` building `Agent(model=LiteLlm(...), instruction=DEFAULT_INSTRUCTION, tools=[])`. **All parity PRs assume #140 is merged** (PR A extends `build_cli_model_runner`; it is the foundation). Land #140 first.

## PR dependency graph (designed to minimize stacked-merge hazard)

```
#140 (model runner)  ──►  PR A (real tools + real prompt)  ──►  ┌─ PR B (headless approval)
                                                                 ├─ PR C (TUI tool rendering)
                                                                 ├─ PR D (identity + plan-mode steering)
                                                                 └─ PR E (safety + budget parity)
```

- **PR A depends on #140.** It is the core parity jump; everything else builds on it.
- **PR B, C, D, E each branch off PR A and are mutually independent siblings** — they can merge in any order after A. File-overlap notes are called out per PR so a reviewer can confirm no collision.
- This avoids a deep linear stack. Per project rule (stacked-PR merge-order hazard): before merging any sibling, confirm its base is A (or main once A merged) and `git grep` the landed content in `origin/main` after each merge — never trust GitHub "merged" status.

---

## PR A — Attach real tools + real system prompt to the CLI agent

**Why:** This is the parity jump. After A, `magi -p "read X and edit Y"` actually reads/edits files in a loop. Audit refs: §1 (real tools via `core_toolhost`), §2 (toolhost→ADK adapter), §4 (system prompt), Gaps 1–3.

**Files:**
- Modify: `magi_agent/cli/real_runner.py` (extend `build_cli_model_runner`) — added by #140.
- Create: `magi_agent/cli/tool_runtime.py` (registry + dispatcher + `tool_context_factory` assembly, kept out of `real_runner.py` for focus).
- Test: `magi_agent/cli/tests/test_tool_runtime.py`, extend `magi_agent/cli/tests/test_real_runner.py`.

**Seam / approach (all reusable as-is per audit):**
1. Build the real tool registry (audit §1 "Reusable as-is" #2):
   - `registry = ToolRegistry()`; `register_core_tool_manifests(registry)` (`tools/catalog.py:245-249`); `bind_core_toolhost_handlers(registry)` (`tools/core_toolhost.py:108-115`). → 9 real, enabled tools (FileRead/Write/Edit, PatchApply, Glob, Grep, Bash, Clock, Calculation). The `core_toolhost` path is deliberately ungated (`core_toolhost.py:43-55`) — no flag flip.
2. Build a `ToolDispatcher` (`tools/dispatcher.py:81-225`).
3. Build a `tool_context_factory(adk_tool_context) -> magi_agent.tools.context.ToolContext` carrying `workspace_root=cwd`, `session_key`, `turn_id` (audit Gap 3). This is the only genuinely new logic.
4. `adk_tools = build_adk_function_tools_for_registry(registry, dispatcher, mode="act", tool_context_factory=..., attach_enabled=True)` (`adk_bridge/tool_adapter.py:230-258`).
5. `instruction = build_system_prompt(session_key=session_id, turn_id="cli", coding_agent=True, model=<provider model>)` (`runtime/message_builder.py:620-662`) — replaces `DEFAULT_INSTRUCTION` (audit §4).
6. Pass `tools=adk_tools, instruction=instruction` into the existing `Agent(...)` construction in `build_cli_model_runner`. Keep the App/Runner/session-service composition from #140 (template: `adk_bridge/local_runner.py:108-178`); optionally append edit-retry/resilience/compaction plugins (defer to PR E if noisy).

**Tests (non-mocked tool execution — the key proof):**
- `test_cli_agent_executes_real_fileread`: build the runner with a fake `BaseLlm` whose first response emits a `FileRead` function-call for a file written into a `tmp_path` workspace, and whose second response (after the tool result) emits final text. Drive a turn through the real ADK `Runner`; assert the file's real contents appear in the tool result event. (Proves real tool execution end-to-end, no mocking of the toolhost.)
- `test_cli_agent_instruction_is_real_system_prompt`: assert the built `Agent.instruction` is non-empty and contains a known `build_system_prompt` marker (e.g. an OUTPUT_RULES / TOOL_PREFERENCES fragment), not `DEFAULT_INSTRUCTION`.
- `test_tool_context_factory_carries_workspace_root`: dispatched tool sees `workspace_root == cwd`.
- Regression: existing `test_real_runner.py` + full `cli/tests` suite stay green; no-provider path still returns the stub.

**Acceptance:** With a real provider key + `magi-agent[providers]`, `magi -p "read pyproject.toml and tell me the version"` reads the file via the FileRead tool and answers from real content. `git grep` the new factory wiring in `origin/main` after merge.

**File overlap for siblings:** PR A owns `real_runner.py` + new `tool_runtime.py`. B touches `cli/wiring.py` + `cli/permissions.py` (headless), C touches `cli/tui/*`, D touches `tool_runtime.py` instruction/mode + `message_builder` inputs, E touches tool safety. D and E both read `tool_runtime.py` A creates — note in their PRs.

---

## PR B — Headless permission approval (control_request/control_response)

**Why:** Destructive tools (Bash, FileWrite) must prompt the user. The TUI approval path is already fully wired (audit §6b, `cli/wiring.py:261-268` + `cli/engine.py:722-842`); only the **headless** path is missing a sink. CC parity = the `control_request`/`control_response` FIFO (cc-agent-loop doc).

**Files:**
- Modify: `magi_agent/cli/wiring.py:168` (attach a `HeadlessSink` to the headless `RulesPermissionGate`), `magi_agent/cli/app.py` (thread `gate` into the headless engine call), `magi_agent/cli/permissions.py:392-402` (the deferred inbound stdin reader → `HeadlessSink.deliver`).
- Test: extend `magi_agent/cli/tests/` (a headless-approval test file).

**Seam / approach (audit §6, Gap 4):**
1. In `build_headless_runtime`, construct the gate WITH a `HeadlessSink` (`cli/permissions.py:387-601`) instead of sink-less.
2. Thread `gate` into the engine call so `run_turn_stream(..., gate=gate)` attaches the `before_tool_callback` (`cli/engine.py:303-308, 431-433`).
3. Add the inbound reader: parse `control_response` NDJSON frames from stdin and call `HeadlessSink.deliver` (deferred TODO at `cli/permissions.py:392-402`). Respect `permission_mode` (`default`/`acceptEdits`/`bypassPermissions`, `permissions.py:498-518`); EOF → safe deny (`:476-495`).

**Tests:**
- `test_headless_ask_emits_control_request`: a tool that triggers `ask` emits exactly one `control_request` frame with the tool name + input.
- `test_headless_control_response_allow_runs_tool` / `..._deny_skips_tool`.
- `test_headless_eof_safe_denies`.
- `test_bypass_permissions_mode_auto_allows`.

**Acceptance:** `printf '<control_response allow>' | magi -p "run ls"` executes; deny skips with the denial surfaced to the model as an `is_error` tool result.

**File overlap:** `cli/wiring.py`, `cli/app.py`, `cli/permissions.py`. Independent of C/D/E.

---

## PR C — TUI tool-call/result rendering (fix the bare UI)

**Why:** Today tool events render as a one-line summary and the shell is bare (no header/theme). Parity = show tool name, args, streamed result, and diffs for edits.

**Files:**
- Modify: `magi_agent/cli/tui/app.py` (`MagiTuiApp._fold_event` — wire in the renderers already built by `build_tool_renderers`, `cli/wiring.py`), `magi_agent/cli/tui/tool_render.py`, header/theme.
- Test: `magi_agent/cli/tests/test_tui_app.py` (extend), `test_render_diff.py` (extend).

**Seam / approach:** `build_tui_app` already builds `renderers = build_tool_renderers()` (`cli/wiring.py`); they are passed to `MagiTuiApp` but not consumed in `_fold_event`. Fold tool_start/tool_input/tool_end events into rendered tool nodes (name + collapsible args + result, with a diff view for FileEdit/PatchApply). Add a header (logo/model/cwd) and a basic theme. Mirror CC/OpenCode tool-result presentation (cc-agent-loop doc §5, opencode doc tool result shape).

**Tests:**
- `test_fold_event_renders_tool_call`: a tool_start+tool_end pair produces a tool node containing the tool name and result text.
- `test_fold_event_renders_edit_diff`: a FileEdit result renders a diff.
- Snapshot/`run_test` TUI test that the header shows model + cwd.

**Acceptance:** Running `magi` and asking it to read a file shows a rendered `FileRead(path=…)` node with the result; an edit shows a diff. (UI change — verify by running the TUI, not just tests.)

**File overlap:** `cli/tui/*` only. Independent of B/D/E.

---

## PR D — Identity + TodoWrite / plan-mode steering

**Why:** CC/OpenCode steer behavior heavily via the system prompt + TodoWrite + plan mode (cc-agent-loop doc §4, opencode doc plan/todo). Magi's `build_system_prompt` already emits coding-discipline/tool-preference blocks (audit §4) and the catalog already has `TodoWrite`/`EnterPlanMode`/`ExitPlanMode` (audit §1) with `available_in_modes` (plan/act).

**Files:**
- Modify: `magi_agent/cli/tool_runtime.py` (expose TodoWrite/Enter/ExitPlanMode; honor `mode`), `magi_agent/cli/real_runner.py` (load local identity → `build_system_prompt(identity=...)`), maybe `cli/app.py` (a `--permission-mode plan` / `/plan` already exists?).
- Create: `magi_agent/cli/identity.py` (load SOUL/AGENTS/TOOLS from cwd/`.magi/` if present; audit Gap 5).
- Test: `magi_agent/cli/tests/test_identity.py`, extend `test_tool_runtime.py`.

**Seam / approach (audit Gap 5):**
1. `load_identity(cwd) -> Mapping` reading optional `AGENTS.md`/`SOUL.md`/`TOOLS.md` from cwd or `.magi/`; pass to `build_system_prompt(identity=...)` so identity sections render (else empty, `message_builder.py:936-952`).
2. Ensure TodoWrite + EnterPlanMode/ExitPlanMode are in the exposed tool set; in plan mode, `registry.list_available("plan")` already excludes mutating tools (`available_in_modes`), so build tools with `mode` from the CLI permission/plan state.
3. Confirm plan-mode restricts FileWrite/Bash via `available_in_modes`.

**Tests:**
- `test_plan_mode_excludes_mutating_tools`: tools built with `mode="plan"` exclude FileWrite/Bash/PatchApply; include FileRead/Grep/Glob.
- `test_todowrite_tool_exposed`.
- `test_identity_files_loaded_into_prompt`: with an `AGENTS.md` in cwd, the built instruction contains its content.

**Acceptance:** `magi --permission-mode plan -p "..."` cannot write files; an `AGENTS.md` in the project is reflected in behavior.

**File overlap:** reads `tool_runtime.py` (A) + `real_runner.py` (A). Touches new `identity.py`. Coordinate with E if both edit `tool_runtime.py` (different functions).

---

## PR E — Safety + result-budget parity

**Why:** Match CC/OpenCode safety: bash out-of-root write detection, output truncation caps, edit fuzzy/strict + schema-error "rewrite your input" repair. Much exists in `Gate5BFullToolHost` (audit §1: real Bash via `subprocess.run`, optional fuzzy-edit/format/LSP, ripgrep); this PR closes specific gaps and adds tests + the model-facing error-repair message (opencode doc: schema errors → actionable prose; cc doc: fail-closed defaults).

**Files:**
- Modify: `magi_agent/cli/tool_runtime.py` (output budgeting wrapper around dispatched results; map dispatcher schema/`needs_approval` errors to actionable model text), possibly `magi_agent/tools/output_budget.py` (reuse existing budgeter), `magi_agent/tools/safety.py`.
- Test: `magi_agent/cli/tests/test_tool_safety.py`.

**Seam / approach:**
1. Reuse `tools/output_budget.py` to cap large tool outputs (truncate + note overflow), mirroring OpenCode's 2000-line/50KB cap (opencode doc).
2. Surface dispatcher errors (schema invalid, `tool_handler_missing`, `needs_approval`) as concise, model-actionable text rather than raw dumps (cc/opencode "rewrite your input" pattern).
3. Verify bash workspace-root containment (audit §1 notes `Gate5BFullToolHost` real `subprocess.run`); add a guard/test that writes outside `workspace_root` are blocked or require approval.

**Tests:**
- `test_oversized_tool_output_truncated`.
- `test_schema_error_returns_actionable_message`.
- `test_bash_write_outside_workspace_is_gated`.

**Acceptance:** A tool returning 1MB output is truncated with an overflow note; a bad FileEdit returns a model-actionable error; bash cannot silently write outside the workspace.

**File overlap:** `tool_runtime.py` (A) — coordinate with D (different functions/regions).

---

## Execution methodology (per the user's request)

1. **Per PR:** create an isolated worktree off the correct base (#140 for A; A for B–E). Write that PR's just-in-time bite-sized task plan (re-reading the cited current code), then run **superpowers:subagent-driven-development**: dispatch implementer → spec-compliance review → code-quality review → fix loops → green tests → commit. Open the PR.
2. **After all PRs implemented:** dispatch **N independent reviewer subagents** (e.g. security-reviewer, code-reviewer, a spec-compliance reviewer, a CC/OpenCode-parity reviewer) across the whole stack; fix findings.
3. **Deliver the final merge-ready PR list** with the exact merge order (#140 → A → {B,C,D,E}), each with its verification command (`git grep` content in `origin/main` after merge; never trust GitHub "merged" status — project rule).

## Self-review (spec coverage)

- Real tool execution → PR A (audit §1/§2/§4). ✓
- Interactive permission (TUI + headless) → TUI already wired; headless = PR B (audit §6). ✓
- Streamed tool call/result UI → PR C. ✓
- System prompt + todo/plan-mode steering (CC/OpenCode behavior) → PR A (prompt) + PR D (identity/plan). ✓
- Safety/budget parity (bash containment, truncation, edit/repair) → PR E. ✓
- Multi-provider model → PR #140 (base). ✓
- No placeholder code: each PR cites concrete seams (file:line) and concrete tests; detailed bite-sized task code is written just-in-time per PR against current source (avoids guessing far-future code).
