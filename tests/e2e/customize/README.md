# Customize matrix end-to-end harness (PR-F-QA1)

End-to-end coverage for the `_LEGAL` matrix in
`magi_agent/customize/custom_rules.py`. Iterates every legal
`(kind, slot, action)` combination, authors the rule via the customize
storage API, drives a synthetic trigger at the matching runtime
chokepoint, asserts the verdict matches the matrix-declared action,
cleans up.

## What this PR covers (F-QA1)

Three slots (the most-used ones):

- `pre_final`
- `before_tool_use`
- `after_tool_use`

Combined with every legal kind, that is **22 combos** out of the full
matrix's 76. The remaining 14 lifecycle slots ship in F-QA2-5.

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

`capability_scope` fires at `spawn` only — covered in F-QA4.

## How to run

```bash
# From the repo root
pytest tests/e2e/customize/test_matrix_tool_use.py -v
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
- `MAGI_CUSTOMIZE_LIFECYCLE_EXTRA_EMITTERS_ENABLED`
- `MAGI_CUSTOMIZE_SESSION_TASK_EMITTERS_ENABLED`
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

F-QA1 with the patched judge: **$0** in API calls (no live LLM round
trips). Shell rules spawn real `bash`/`sh` subprocesses; the inline
scripts are `exit 0` / `exit 1` / `echo '{"passed": true}'` only.

F-QA3 (planned) with live LLM rows enabled: estimated **$0.50-2** per
full matrix run with a cheap binary-verdict critic model.

## Runtime

F-QA1 full matrix run: ~30-90 seconds (22 combos, each authoring a
rule, driving 1 trigger, and asserting). Shell rules dominate the
wall-clock (subprocess startup is ~10-50ms per spawn).

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
- **`capability_scope` / `spawn` slot** — ships in F-QA4.
- **Turn-boundary / LLM-call / compaction / task / artifact / session
  slots** — F-QA2-4.

## File layout

| File                       | Role                                       |
|----------------------------|--------------------------------------------|
| `matrix.py`                | `iter_legal_combinations`, scope filters   |
| `payload_factory.py`       | Per-kind minimal valid rule dict           |
| `triggers.py`              | Per-slot synthetic trigger drivers         |
| `asserter.py`              | Per-action verdict assertions              |
| `conftest.py`              | Fixtures (flags, judge, identity, cleanup) |
| `test_matrix_tool_use.py`  | Parametrized 5-step pattern                |
| `README.md`                | This file                                  |

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
| F-QA2     | turn-boundary slots (before/after_turn_start/end, prompt_submit, subagent_stop) |
| F-QA3     | LLM_CALL slots + per-turn critic budget regression          |
| F-QA4     | compaction / task / artifact / session_start / spawn slots  |
| F-QA5     | shell_command + shell_check matrix + cross-kind budget      |
| F-QA6     | (deferred) Playwright wizard UI smoke                       |
