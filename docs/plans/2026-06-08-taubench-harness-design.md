# τ-bench Harness — Design

Date: 2026-06-08
Branch: `feat/taubench-harness` (off `main` @ e5cb63c)
Status: design (pending implementation plan)

## Goal

Build a first-party **τ-bench (tau-bench) measurement harness** for `magi-agent`
that runs the **real magi runtime** as the τ-bench agent and measures **pass^k**
(reliability) under two configs — **magi-full** (composable-determinism control
plane ON) vs **magi-vanilla** (bare) — to show whether magi's determinism layer
raises pass^k, and to compare against published reference numbers.

Why τ-bench (vs LegalBench): τ-bench is multi-step, stateful, tool-using, with a
verifiable success criterion (final DB-state hash) and a reliability metric
(pass^k). Unlike LegalBench (one-shot, model-dominated), τ-bench is **harness-
sensitive** — published external-harness work (Blueprint, arXiv 2508.02721)
shows +10 pass@1 over the function-call baseline. This is the right arena to
demonstrate magi's composable determinism.

## Scope decisions (locked)

- **Approach A — real runtime as the agent.** Drive magi's real `build_cli_model_runner`
  (the same in-process entry the GAIA harness uses), not a hand-rolled provider
  loop. τ-bench's tools + user-simulator become the agent's world.
- **magi-full = control-plane flags ON; magi-vanilla = default (bare).** PR #195
  (merged 2026-06-06) wired a unified ADK control plane into both runners via
  `adk_bridge/control_plane.py` (`build_default_plugin`, registered in
  `App(plugins=[...])` inside `cli/real_runner.py`). Its controls are **default-OFF
  env flags**, so the only variable between full and vanilla is the flag set —
  a clean native ablation.
- **Agent model = Claude Sonnet 4.5** (best Anthropic on public τ-bench airline,
  0.70). **User-simulator = gpt-4o** (the τ-bench standard, for clean comparison
  to published numbers). OpenAI + Anthropic keys taken from the server config.
- **Comparison = magi-full vs magi-vanilla vs public reference** (Sonnet 4.5
  airline pass^1 ≈ 0.70 / retail ≈ 0.86, with the published-prompt-addendum
  caveat).
- **v1 scope:** cheap adapter validation (5–10 tasks × 1 trial) → then **airline
  (50 tasks) × 4 trials** for Pass^1..4. retail / both-domains is a later
  expansion.

## The control-plane flags (what "full" turns on)

`build_default_plane` (control_plane.py) registers these, all default-OFF:

| Control | Env flag |
| --- | --- |
| Edit-retry reflection | `MAGI_EDIT_RETRY_REFLECTION_ENABLED` |
| Resilience: loop guard | `MAGI_LOOP_GUARD_ENABLED` |
| Resilience: error recovery | `MAGI_ERROR_RECOVERY_ENABLED` |
| Context compaction | `MAGI_CONTEXT_COMPACTION_ENABLED` |
| Max-steps brake | `MAGI_MAX_STEPS_BRAKE_ENABLED` |
| Self-review after turn | `MAGI_SELF_REVIEW_ENABLED` |

`magi-full` sets all six before building the runner; `magi-vanilla` leaves them
unset (byte-identical to a bare run).

**Honest limit (drives v2, not v1):** the two strongest τ-bench levers are NOT
expressible in the current native plane: (a) **policy_guard** — blocking/
rewriting a tool action against `env.wiki` — is forbidden, because the plane's
`on_before_tool` deny/rewrite would bypass the agent-level permission gate
(`ControlPlane.register` raises if a control overrides `on_before_tool`); and
(b) **verify→re-iterate** — ADK has no loop-re-entry callback. These remain a
gap; v2 may add them at the τ-bench driver's tool boundary (where blocking IS
expressible).

## Architecture

```text
tau-bench (cloned, MIT): Env(airline/retail), tasks, user-sim (gpt-4o), tools,
                         reward (final DB-state hash + required outputs), pass^k
        │
MagiTauAgent(tau_bench.agents.base.Agent).solve(env, task_index, max_num_steps):
  1. env.reset(task_index) -> first user message; env.wiki (policy); env.tools_info
  2. build_cli_model_runner(config=Sonnet4.5, instruction=env.wiki,
        tools=[ADK FunctionTool per env tool; callable -> env.step(Action(name,kwargs))],
        model_factory=<real LiteLlm | scripted fake in tests>, session_id=fixed)
  3. multi-turn loop:
       run_async(user_id, session_id, new_message=<user msg as Content>)
         - tool calls auto-route to env.step via the FunctionTools
         - turn ends when the agent yields user-facing text with no pending tool
       -> env.step(Action("respond", {content})) -> user-sim reply
       -> loop with reply as next new_message
       until env_response.done / "###STOP###" / max_num_steps
  4. return SolveResult(reward, messages, info)
        │
runner: for each task in subset, for each of `trials` runs, run MagiTauAgent
        under the chosen config (full|vanilla); collect per-(task,trial) reward
        │
scorer: pass^k = mean_over_tasks( C(successes,k) / C(trials,k) ); + avg reward;
        per config -> full vs vanilla, alongside published reference
```

## Components (new; mirrors `benchmarks/gaia/` + `benchmarks/legalbench/`)

| File | Purpose |
| --- | --- |
| `magi_agent/benchmarks/taubench/__init__.py` | package init |
| `magi_agent/benchmarks/taubench/tau_env.py` | Locate/import `tau_bench`; build an env for a domain/task. Translate `env.tools_info` (OpenAI function-calling JSON) into magi ADK `FunctionTool`s whose callable does `env.step(Action(name, kwargs))` and returns the observation string. |
| `magi_agent/benchmarks/taubench/agent.py` | `MagiTauAgent(Agent)` implementing `solve()` — the multi-turn `run_async` drive loop + the **turn-boundary handshake** (detect agent user-facing text → `respond` → user-sim reply → resume same session). |
| `magi_agent/benchmarks/taubench/harness.py` | `run_task(domain, task_index, *, config, trials)` and `run_subset(...)`; sets control-plane flags for `config="full"`, builds runner + agent, collects rewards. |
| `magi_agent/benchmarks/taubench/scorer.py` | Pure scorer: `pass_hat_k(successes_per_task, trials)`, average reward, per-config `TauReport` (mirrors `legal_eval`/`gaia` scorer style). |
| `magi_agent/benchmarks/taubench/cli.py` | Default-OFF gate `MAGI_TAUBENCH_ENABLED`; flags `--domain/--max-tasks/--trials/--config`; provider binding (agent=Sonnet 4.5, user-sim=gpt-4o); JSON report. |
| `tests/benchmarks/taubench/` | Fake scripted env + fake model (`model_factory` injects a scripted `BaseLlm`, like the GAIA tests). No network. |

### Reused (do not rebuild)
- `magi_agent/cli/real_runner.py::build_cli_model_runner` + `run_async` (the GAIA
  in-process pattern is the template).
- `adk_bridge/control_plane.py` (the determinism controls — toggled via flags).
- `magi_agent/cli/providers.py::resolve_provider_config` (agent provider).
- Scorer/report style from `benchmarks/legal_eval.py` and `benchmarks/gaia/scorer.py`.

## Data flow

1. Harness picks a domain + task subset; for `config="full"` sets the six env
   flags; builds the runner once per task (fresh session).
2. `MagiTauAgent.solve` runs the multi-turn loop against the env; every tool call
   routes to `env.step`; every user-facing message routes to `env.step("respond")`
   and pulls the user-sim reply.
3. The episode ends on env `done`/STOP/max-steps; `env_response.reward` (0/1) is
   recorded for that (task, trial).
4. After all trials, the scorer computes pass^1..k per task → averaged, plus
   average reward, per config.

## Methodology / comparison (number credibility)

- Hold everything constant except the config: same model (Sonnet 4.5), same
  user-sim (gpt-4o), same tasks, same trials, same extraction of the agent's
  actions. The only difference between full and vanilla is the control-plane
  flag set → the pass^k delta is attributable to the determinism layer.
- Report **pass^1..4** (τ-bench leaderboard standard, `--trials 4`) + average
  reward, for both configs.
- Compare absolute full/vanilla numbers to the published Sonnet 4.5 reference
  (airline ≈ 0.70), **footnoting** that the published figure used a prompt
  addendum (not vanilla reference) and is pass^1 — so treat it as a loose
  ceiling, not an exact apples-to-apples point. The defensible primary claim is
  the **internal full-vs-vanilla delta**.

## τ-bench dependency

- `tau_bench` is clone-only (MIT), not on PyPI. The harness imports it at run
  time. Vendor via a documented setup step (clone into a known path / optional
  editable install); do NOT commit the τ-bench dataset/code into this repo.
- **Tests must not require tau-bench installed or any network** — they use a
  fake in-memory env + scripted model. Only the live run requires `tau_bench`
  cloned + OpenAI (user-sim) + Anthropic (agent) keys.

## Gating, cost, safety

- Default-OFF env gate `MAGI_TAUBENCH_ENABLED`; the CLI refuses to run unless set.
- Cost is significant (multi-turn × trials × 2 configs × 2 models). v1 mitigates
  via: cheap adapter validation (5–10 tasks × 1 trial) before the airline-50 × 4
  run; `--max-tasks` and `--trials` flags; agent on Sonnet 4.5, user-sim gpt-4o.
- API keys are read from server/provider config; never logged or written to the
  JSON report or committed files.

## Error handling

- tau-bench import / env build failure → clear error; the CLI exits non-zero.
- Provider/model error mid-episode → that trial is recorded as `infra-error` and
  **excluded from the pass^k denominator** (infra noise must not count as a model
  failure), never silently a success. The report surfaces the infra-error count.
- `max_num_steps` reached without env `done` → reward 0 for that trial (τ-bench
  semantics: incomplete task).
- Turn-boundary ambiguity (agent text that is neither a tool call nor a clear
  user message) → conservative default: treat trailing model text with no
  pending tool as a user-facing `respond`; log when this fires.

## Testing

- Fake scripted env: an in-memory stand-in exposing `reset/step/wiki/tools_info`
  with a deterministic reward, so the loop + scorer are tested without tau-bench.
- Fake model via `model_factory` (scripted `BaseLlm`) producing a fixed sequence
  of tool calls + a final user message → exercises tool→`env.step` routing and
  the turn-boundary handshake deterministically.
- Pure unit tests: `pass_hat_k` math (e.g. trials=4, successes=2 → C(2,k)/C(4,k)),
  OpenAI-tool-schema → FunctionTool translation, `respond` handshake, config flag
  application (full sets the six env flags; vanilla sets none).
- Live smoke (gated, manual): airline `--max-tasks 2 --trials 1 --config vanilla`.

## Risks

- **Turn-boundary handshake** (highest): distinguishing "agent is addressing the
  user (await reply)" from "agent is done" from the `run_async` event stream.
  magi has no explicit user-facing-message signal. This is the core integration
  work and gets the most test coverage; getting continuation/termination wrong
  breaks every episode.
- **Multi-call session resume** is new harness code (GAIA drives a single
  `run_async`); session persistence supports it but it's unexercised by existing
  tests.
- **Determinism ceiling:** the native plane's current controls are resilience/
  observation (compaction, max-steps, error-recovery, self-review, reflection),
  not policy-block/verify-reiterate. The full-vs-vanilla delta may be modest;
  report it honestly and note the v2 levers.

## Out of scope (v1) / v2 candidates

- retail domain; both-domains full run.
- τ²-bench (separate repo, different agent interface).
- **Driver-boundary policy_guard + verify→re-iterate** (the levers the native
  plane can't express) — the most likely pass^k movers; deferred to v2.
- A larger trial count or multi-provider matrix.

## Success criteria

1. The harness runs `MagiTauAgent` through a full multi-turn τ-bench episode and
   records τ-bench's reward — validated on a tiny subset.
2. `scorer` produces a reproducible per-config report: pass^1..4 + average
   reward for `full` and `vanilla`, with the published reference alongside.
3. All pure components (scorer, tool translation, handshake, flag application)
   covered by fake-env/fake-model tests; suite green; no network required.
4. Default-OFF; no behavior change when the gate is unset.
