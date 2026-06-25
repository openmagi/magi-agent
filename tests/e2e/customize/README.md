# Customize matrix end-to-end harness (PR-F-QA1 + F-QA2 + F-QA3 + F-QA4 + F-QA5)

End-to-end coverage for the `_LEGAL` matrix in
`magi_agent/customize/custom_rules.py`. Iterates every legal
`(kind, slot, action)` combination, authors the rule via the customize
storage API, drives a synthetic trigger at the matching runtime
chokepoint, asserts the verdict matches the matrix-declared action,
cleans up.

## What's covered (F-QA1 + F-QA2 + F-QA3 + F-QA4 + F-QA5)

### F-QA1 — tool-use slots (3)

- `pre_final`
- `before_tool_use`
- `after_tool_use`

| Kind                 | pre_final | before_tool_use | after_tool_use |
|----------------------|-----------|-----------------|----------------|
| `deterministic_ref`  | block / retry / audit | — | — |
| `tool_perm`          | — | block / ask_approval | — |
| `llm_criterion`      | block / retry / audit | — | override |
| `shacl_constraint`   | block | — | — |
| `prompt_injection`   | — | audit | — |
| `output_rewrite`     | — | — | audit |
| `shell_command`      | block / audit | block / audit | audit |
| `shell_check`        | block / audit | block / audit | audit |

### F-QA2 — turn-boundary slots (4)

All four funnel through `run_governed_turn` (the canonical
CLI/serve/child entry point). `before_turn_start` /
`on_user_prompt_submit` are GATE slots — `block` short-circuits the
engine stream BEFORE `rt.engine.run_turn_stream` is invoked.
`after_turn_end` fires in the finally block on TOP-LEVEL turns
(audit-only by `_LEGAL` — block excluded). `on_subagent_stop` fires
in the finally block on CHILD turns (`ctx.depth > 0`); F-LIFE1
lifted the action set to `{audit, block, ask_approval}` for
authorability but runtime parent-surfacing is NOT wired yet (TODO
per F-LIFE1 review pass — the asserter records the audit but does
NOT assert a parent-side block).

| Kind               | before_turn_start | after_turn_end | on_user_prompt_submit | on_subagent_stop |
|--------------------|-------------------|----------------|-----------------------|------------------|
| `llm_criterion`    | audit / block / ask_approval | audit | audit / block | audit / block / ask_approval |
| `prompt_injection` | — | — | audit | — |
| `shell_command`    | audit | audit | audit | audit |
| `shell_check`      | audit / block / ask_approval | audit | audit / block | audit / block / ask_approval |

`capability_scope` (spawn-only) ships in F-QA4. The remaining
~8 lifecycle slots (compaction / task / artifact / session) ship in
F-QA4-5.

### F-QA3 — per-LLM-call slots (2) + budget regression

Two slots funnel through the ADK plugin
`LifecycleLlmCallAuditControl` at the
`before_model_callback` / `after_model_callback` boundary:

- `before_llm_call` — block suppresses the outbound model call by
  returning a synthetic policy-blocked `LlmResponse`.
- `after_llm_call` — block REPLACES the just-emitted response with
  the synthetic refusal so the consumer never sees the offending
  text (per F-LIFE4a — already-streamed tokens cannot be un-rung).

| Kind            | before_llm_call | after_llm_call |
|-----------------|-----------------|----------------|
| `llm_criterion` | audit / block   | audit / block  |

v1 `_LEGAL` accepts `llm_criterion` only at these slots (the per-
call hot path's cost ceiling makes other kinds inappropriate). The
4 matrix combos pin the F-LIFE4a contract; two budget tests pin the
per-turn critic cap shared across before/after.

### F-QA4 — late-lifecycle slots (6 + 1 SKIP)

Six late-lifecycle slots fan out through their respective production
chokepoints; one slot ships a SKIP placeholder for the F-LIFE4b
honest-degrade.

- `before_compaction` — `MagiContextCompactionPlugin._apply_tail_trim`.
  `block` causes the plugin to RETURN EARLY without mutating
  `llm_request.contents` (the asserter compares pre/post identity).
- `after_compaction` — same plugin call as `before_compaction`; the
  audit fires post-tail-drop. Audit-only by `_LEGAL` (block excluded).
- `on_task_checkpoint` — `WorkQueueDriver.run_once` fires the audit at
  every `claimed` / `completed` / `failed` / `short_circuited`
  transition. F-LIFE4a review pass NOTE: `block` only honors at the
  `claimed` transition (post-execution revert requires a compensating-
  action wire — separate follow-up). Other transitions still emit
  audit records but the gate verdict is recorded as audit-only.
- `on_artifact_created` — `FileDeliveryBoundary.execute` fires the
  audit on the provider `status="ok"` branch. Block is honestly
  impossible (artifact already written); the matrix only exposes
  `ask_approval`, which the boundary translates into a
  `delivery_intent` decision carrying
  `diagnostic_metadata.requires_approval=True` and
  `reason_codes=("artifact_review_pending",)`. The asserter treats
  this as the audit-ledger `requires_approval` marker.
- `on_task_complete` — `_OnTaskCompleteCollector.run_audit` inside
  `run_governed_turn`'s `finally` block. v1 signal: the top-level
  turn's final assistant text carries a line-anchored `<task_done>`
  marker. F-LIFE4b review pass NOTE: `block` / `ask_approval`
  annotate the audit ledger with `requires_approval` / `gate_verdict`
  but the compensating-action wire (turn rollback) is deferred. The
  asserter verifies the engine ran for every action and no synthetic
  policy-blocked terminal appeared.
- `on_session_start` — `LifecycleSessionControl.on_before_model`.
  First-fire-per-session contract: the asserter invokes
  `on_before_model` TWICE on the same `session_id` and pins
  "second call MUST be silent" plus "first call honors block by
  returning the synthetic policy-blocked `LlmResponse`".
- `spawn` (`capability_scope` only) — `apply_capability_scope` is
  driven directly with a `denyTools=["shell_exec"]` rule. The
  asserter verifies the deterministic subtraction landed
  (`shell_exec` removed from the resolved toolset; the remaining
  toolset equals the original minus the deny entry). The production
  composition chain (parent_cap → capability_scope → allowedTools →
  spawn_cap) is covered by the per-component firing tests; F-QA4
  only verifies the F4 hook fired and subtracted honestly.

| Kind                | before_compaction | after_compaction | on_task_checkpoint | on_artifact_created | on_task_complete | on_session_start | spawn |
|---------------------|-------------------|------------------|--------------------|---------------------|------------------|------------------|-------|
| `llm_criterion`     | audit / block     | audit            | audit / block / ask_approval | audit / ask_approval | audit / block / ask_approval | audit / block | — |
| `shell_command`     | audit             | audit            | audit              | audit               | —                | —                | — |
| `shell_check`       | audit / block     | audit            | audit / block / ask_approval | audit / ask_approval | —                | —                | — |
| `capability_scope`  | —                 | —                | —                  | —                   | —                | —                | block |

`on_session_end` is explicitly SKIPPED — F-LIFE4b ships no
transport-side emit wire in v1 (`tests/e2e/customize/triggers.py`
ships an `ON_SESSION_END_SKIP_REASON` constant; the matrix test
file routes the row through `pytest.skip()` with that reason). The
slot stays in the wizard so operators can author rules ahead of the
wire — the audit ledger remains silent until a follow-up adds the
emit.

### F-QA5 — shell kinds matrix + cross-kind budget (~22 combos + 3 budget tests)

F-QA5 completes the F-QA series by pinning **shell kind fan-out
helpers directly end-to-end** including real subprocess spawn. The
earlier matrices exercise shell rules through the facade
(`before_tool_use` / `after_tool_use`) and the governed-turn wrapper
(`on_user_prompt_submit` / `after_turn_end` / etc.); F-QA5 drives
the 9 `run_shell_command_at_<slot>` + 2 `run_shell_check_at_<slot>`
helpers from `magi_agent.customize.lifecycle_audit` per-slot,
asserting the audit ledger + derived gate verdict against the
matrix-declared action contract.

Two scope axes:

* **Matrix** (`test_matrix_shell.py`) — `(kind, slot, action)` rows
  filtered to `kind in {shell_command, shell_check}` and `slot in
  F_QA5_SHELL_SLOTS` (the union of the 11 v1 shell lifecycle
  slots). Each row authors a real `ShellPayload`, persists the rule,
  invokes the matching helper, asserts via
  `assert_shell_action_honored`.
* **Cross-kind budget** (`test_shell_cross_kind_budget.py`) —
  shared `MAGI_CUSTOMIZE_SHELL_AUDIT_BUDGET` counter contract
  across `shell_command` + `shell_check` kinds.

Per-test contracts (cross-kind budget):

1. `test_cross_kind_budget_shares_counter` — author 1
   `shell_command` rule + 1 `shell_check` rule (both at `pre_final`).
   Set `MAGI_CUSTOMIZE_SHELL_AUDIT_BUDGET=1`. Drive both helpers
   sequentially: assert the FIRST one spawns a subprocess (audit
   record with `status="executed"`) and the SECOND one returns a
   `budget_exhausted` audit record without invoking the runner.
   This is the canonical "the budget is shared across kinds" pin.
2. `test_budget_works_with_only_shell_command_enabled` — flip
   `MAGI_CUSTOMIZE_SHELL_COMMAND_ENABLED=1` /
   `MAGI_CUSTOMIZE_SHELL_CHECK_ENABLED=0`. Verify the budget
   initializes via the F-EXEC2 review fix's union-gate
   (`shell_command_enabled OR shell_check_enabled`) — without the
   fix the budget would honest-degrade to `None` (no cap) in this
   asymmetric flag state.
3. `test_budget_works_with_only_shell_check_enabled` — mirror of
   (2). Same union-gate contract from the converse direction.

Cross-kind budget contract diagram:

```
                  shell_budget_for(session_id, turn_id)
                 ───────────────────────────────────────
                              │
                              ▼
              ┌──────────────────────────────────┐
              │  _SHARED_BUDGET[(sid, tid)]      │
              │  ─ FIFO-bounded; initialises     │
              │    from MAGI_CUSTOMIZE_SHELL_    │
              │    AUDIT_BUDGET env knob         │
              └──────────────────────────────────┘
                              │
            ┌─────────────────┴─────────────────┐
            ▼                                   ▼
  shell_command helper                  shell_check helper
  (apply_shell_command_rule)            (apply_shell_check_rule)
            │                                   │
            ▼                                   ▼
  decrements shared counter ────►◄──── decrements shared counter
            │                                   │
            └────────► budget=0 → both kinds short-circuit
                       with status="budget_exhausted"
```

| Kind            | shell slots (F-QA5 enumerated)                                                                                                         |
|-----------------|----------------------------------------------------------------------------------------------------------------------------------------|
| `shell_command` | pre_final / before_tool_use / after_tool_use / on_user_prompt_submit / on_subagent_stop / before_turn_start / after_turn_end / before_compaction / after_compaction / on_task_checkpoint / on_artifact_created |
| `shell_check`   | pre_final / before_tool_use / after_tool_use / on_user_prompt_submit / on_subagent_stop / before_turn_start / after_turn_end / before_compaction / after_compaction / on_task_checkpoint / on_artifact_created |

Shell-script safety guarantee: **only** the following inline
scripts are ever authored / spawned by F-QA5:

| Script                          | Used by                                |
|---------------------------------|----------------------------------------|
| `exit 0`                        | shell_command audit / ask_approval rows |
| `exit 1`                        | shell_command + shell_check block rows  |
| `echo '{"passed": true}'`       | shell_check audit / ask_approval rows   |

Each spawn is a fresh `bash -c '<script>'` via
`asyncio.subprocess.create_subprocess_exec`. The runner's whitelist
restricts the inherited env to a safe minimum (no secrets leak); no
filesystem writes; <50ms wall time per spawn. The F-QA5 matrix +
budget tests are therefore **safe to run on any host** with `bash`
on `PATH` — the same constraint already enforced by
`tests/customize_firing/test_shell_command_cross_slot_budget.py`.

## How to run

```bash
# From the repo root
pytest tests/e2e/customize/test_matrix_tool_use.py -v
pytest tests/e2e/customize/test_matrix_turn_boundary.py -v
pytest tests/e2e/customize/test_matrix_llm_call.py -v
pytest tests/e2e/customize/test_llm_call_budget_exhaustion.py -v
pytest tests/e2e/customize/test_matrix_late_lifecycle.py -v
pytest tests/e2e/customize/test_matrix_shell.py -v
pytest tests/e2e/customize/test_shell_cross_kind_budget.py -v
# Or all at once
pytest tests/e2e/customize/ -v

# F-QA4 deterministic-only smoke (skips llm_criterion rows so a
# fresh-install host without the patched_judge fixture can run a
# quick capability_scope + shell sanity sweep):
pytest tests/e2e/customize/test_matrix_late_lifecycle.py -v \
  -k "not llm_criterion"
```

Collection alone (sanity check the matrix without executing rules):

```bash
pytest tests/e2e/customize/ --collect-only
```

A single combo (rapid iteration on a failing row):

```bash
pytest tests/e2e/customize/test_matrix_tool_use.py \
  -v -k "llm_criterion-after_tool_use-override"
```

## Required environment

These flags are flipped ON automatically by the `flags_on` fixture
inside `conftest.py`:

- `MAGI_CUSTOMIZE_VERIFICATION_ENABLED`
- `MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED`
- `MAGI_EGRESS_GATE_ENABLED` (gates the llm_criterion critic factory)
- `MAGI_CUSTOMIZE_PROMPT_INJECTION_ENABLED`
- `MAGI_CUSTOMIZE_OUTPUT_REWRITE_ENABLED`
- `MAGI_CUSTOMIZE_LIFECYCLE_EXPANSION_ENABLED`
- `MAGI_CUSTOMIZE_LIFECYCLE_TURN_HOOKS_ENABLED`
- `MAGI_CUSTOMIZE_LIFECYCLE_LLM_CALL_HOOKS_ENABLED`
- `MAGI_CUSTOMIZE_LLM_CALL_HOOKS_ENABLED` (F-QA3)
- `MAGI_CUSTOMIZE_LIFECYCLE_EXTRA_EMITTERS_ENABLED`
- `MAGI_CUSTOMIZE_SESSION_TASK_EMITTERS_ENABLED`
- `MAGI_CUSTOMIZE_LIFECYCLE_SESSION_TASK_EMITTERS_ENABLED` (F-QA4 — canonical name consulted by `session_task_emitters_enabled`)
- `MAGI_CUSTOMIZE_CAPABILITY_SCOPE_ENABLED` (F-QA4 — gates the F4 spawn-time subtraction wire)
- `MAGI_CUSTOMIZE_SHELL_COMMAND_ENABLED`
- `MAGI_CUSTOMIZE_SHELL_CHECK_ENABLED`
- `MAGI_SHACL_VERIFIER_ENABLED`

You do not need to export these yourself for F-QA1 — the conftest
fixture sets and restores them per-test.

### Provider keys

F-QA1's `llm_criterion` tests do **not** require a real provider key.
The `patched_judge` fixture monkeypatches
`magi_agent.customize.criterion_engine.evaluate_criterion` and
`magi_agent.customize.after_tool_gate.evaluate_criterion` with an
in-process fake that returns the verdict the matrix-action contract
requires (pass for audit / ask_approval, fail for block / retry /
override).

F-QA3 will add real-LLM rows behind a `require_provider_key` skip
gate. At least one of these must be exported when those rows ship:

- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`
- `GEMINI_API_KEY`
- `FIREWORKS_API_KEY`
- `OPENROUTER_API_KEY`

## Cost envelope

F-QA1 + F-QA2 with the patched judge: **$0** in API calls (no live LLM
round trips). The F-QA2 turn-boundary slots heavily exercise
`llm_criterion` (4 slots × 3 actions for that kind alone), so the
`patched_judge` fixture is doing most of the work — without it the
matrix would be the most expensive slice in the suite. Shell rules
spawn real `bash`/`sh` subprocesses; the inline scripts are `exit 0`
/ `exit 1` / `echo '{"passed": true}'` only.

F-QA3 with the patched judge: **$0** in API calls. The 4 LLM-call
matrix combos + 2 budget tests all monkeypatch
`evaluate_criterion`, so even the budget exhaustion regression (which
fires up to 6 ADK plugin calls) costs nothing. Cost only materializes
when the future `require_provider_key`-gated live-LLM rows are
opted in:

- F-QA3 (planned) with live LLM rows enabled: estimated **$1-4** per
  full matrix run with a cheap binary-verdict critic model. The 4
  llm_criterion-heavy LLM-call combos add ~$0.20-1 on top of the
  F-QA2 estimate (per-turn budget=3 caps cost per call).

F-QA4 with the patched judge: **$0** in API calls. The matrix adds
~25 combos across 6 driven slots + 1 skipped slot (`on_session_end`).
llm_criterion rows reuse the F-QA3 `patched_judge` fixture; shell
rules at the late-lifecycle slots are audit-only fan-outs that may
spawn a trivial `exit 0` / `exit 1` subprocess each; the
capability_scope `spawn` row is pure in-memory subtraction (no
subprocess, no judge). Estimated runtime: **~10-30 seconds** for the
full F-QA4 slice. Cost only materializes when live LLM critic rows
are opted in — the F-QA4 slots reuse the same per-turn budget cap as
F-QA3 so a misbehaving rule cannot blow past the ceiling.

F-QA5 shell kinds matrix + cross-kind budget: **$0** in API calls
(no LLM round trip — both kinds are deterministic shell helpers).
Subprocess cost = **~$0** at the host level: every spawn is a
trivial `bash -c 'exit 0'` / `bash -c 'exit 1'` / `bash -c 'echo
"{...}"'` with the runner's whitelisted env. Wall-clock budget:
~22 matrix combos × ~30ms per spawn ≈ **~1 second of subprocess
work**, plus pytest collection + fixture cascade overhead. The
3 cross-kind budget tests add 1-2 spawns each. Total F-QA5
runtime: **~5-15 seconds**. No live LLM cost path — F-QA5 is the
only F-QA slice with zero opt-in cost dependencies (no
`require_provider_key` skip gate; no `patched_judge` fixture).

## Runtime

F-QA1 full matrix run: ~30-90 seconds (22 combos, each authoring a
rule, driving 1 trigger, and asserting). Shell rules dominate the
wall-clock (subprocess startup is ~10-50ms per spawn).

F-QA2 adds **~20 combos** (4 slots × kinds × actions per the
turn-boundary table above). Each row drives a real
`run_governed_turn` with a fake engine + monkeypatched judge — no
subprocess spawn, no LLM round trip — so the F-QA2 slice runs in
~20-40 seconds. The shell_command / shell_check rows still spawn
real subprocesses for their pre-final / before-tool-use siblings
(F-QA1), but the F-QA2 turn-boundary shell rows fire the audit
fan-out helpers which do NOT spawn at all when the matching rule's
inline script is the trivial `exit 0` / `exit 1`.

F-QA3 adds **4 matrix combos + 2 budget tests** (2 slots × 1 kind ×
2 actions = 4 llm_criterion combos; 2 dedicated budget tests pin
the per-turn cap + cross-slot shared-budget contract). Each row
drives the ADK plugin directly with a synthetic callback_context +
LlmRequest / LlmResponse stub — no engine, no subprocess, no LLM
round trip — so the F-QA3 slice runs in ~5-10 seconds.

## What's intentionally NOT covered

- **Negative / OFF-path** — `tests/customize_firing/test_*_firing.py`
  modules already cover the "master flag OFF ⇒ byte-identical" axis.
  F-QA's job is the ON-path matrix.
- **Wizard UI flow** — Kevin's call: local-only, no Playwright. UI
  regressions are caught by per-component `*.local.test.ts` vitest
  suites.
- **CI integration** — tests live in the repo but no GitHub Actions
  wiring. Run on demand before significant releases.
- **Hosted bot runtime** — local OSS only. Hosted bot pods would need
  a separate fixture for the K8s harness.
- **Performance / latency regression** — F-QA verifies correctness.
- **Live transport-side `on_session_end` emit** — F-LIFE4b ships only
  the validator + audit helper; the transport wire is a follow-up. The
  F-QA4 matrix collects the rows (visible in `--collect-only`) but
  skips them with the deterministic
  `ON_SESSION_END_SKIP_REASON` (in `triggers.py`).
- **Compensating-action wire for `on_task_complete` / `on_task_checkpoint`** —
  block / ask annotations land in the audit ledger but no turn-
  rollback / state-revert wire ships in v1 (F-LIFE4b review pass NOTE).
  F-QA4 verifies the audit annotations; runtime rollback follows.

## File layout

| File                            | Role                                       |
|---------------------------------|--------------------------------------------|
| `matrix.py`                     | `iter_legal_combinations`, scope filters (F_QA1_SLOTS / F_QA2_SLOTS / F_QA3_SLOTS) |
| `payload_factory.py`            | Per-kind minimal valid rule dict           |
| `triggers.py`                   | Per-slot synthetic trigger drivers (F-QA1 tool-use + F-QA2 turn-boundary + F-QA3 LLM-call) |
| `asserter.py`                   | Per-action verdict assertions              |
| `conftest.py`                   | Fixtures (flags, judge, identity, cleanup) |
| `test_matrix_tool_use.py`       | F-QA1 parametrized matrix (3 tool-use slots) |
| `test_matrix_turn_boundary.py`  | F-QA2 parametrized matrix (4 turn-boundary slots) |
| `test_matrix_llm_call.py`       | F-QA3 parametrized matrix (2 LLM-call slots) |
| `test_llm_call_budget_exhaustion.py` | F-QA3 per-turn critic budget regression |
| `test_matrix_late_lifecycle.py` | F-QA4 parametrized matrix (6 driven slots + 1 SKIP for `on_session_end`) |
| `test_matrix_shell.py`          | F-QA5 parametrized matrix (shell_command + shell_check kinds, all 11 slots) |
| `test_shell_cross_kind_budget.py` | F-QA5 cross-kind shared budget contract (3 tests) |
| `README.md`                     | This file                                  |

## Adding a new kind / slot / action

1. Add the row to `_LEGAL` in `magi_agent/customize/custom_rules.py`.
2. Add a branch to `build_payload()` in `payload_factory.py` returning a
   valid rule for the new kind (the validator must accept it).
3. Add a branch to the relevant `trigger_*` function in `triggers.py`
   that exercises the kind's runtime chokepoint.
4. Add a branch to the relevant `_assert_*_honored` in `asserter.py`
   that maps the runtime evidence onto pass/fail.

No matrix list to update — `iter_legal_combinations` re-reads `_LEGAL`
each test run.

## Future series

| PR        | Adds                                                       |
|-----------|------------------------------------------------------------|
| F-QA2     | turn-boundary slots (before/after_turn_start/end, prompt_submit, subagent_stop) — shipped |
| F-QA3     | LLM_CALL slots + per-turn critic budget regression — shipped |
| F-QA4     | compaction / task_checkpoint / artifact / task_complete / session_start / spawn slots — shipped (`on_session_end` SKIP placeholder; transport-side emit wire follow-up) |
| F-QA5     | shell_command + shell_check kinds matrix + cross-kind shared-budget contract — **shipped** (F-QA series **COMPLETE**) |
| (future)  | (optional) Playwright wizard UI smoke                       |
