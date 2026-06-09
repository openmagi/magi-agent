# τ-bench v2 — Reliability Checkpoint Pack (Design)

Date: 2026-06-09
Branch: `feat/taubench-harness`
Status: design (pending implementation plan)

## Goal

Add a toggleable **reliability checkpoint pack** at the τ-bench driver boundary
that targets the *general* agent failure modes the v1 run + failure analysis
surfaced, and measure whether it raises τ-bench pass^k vs a clean vanilla
baseline. The levers are general agent-reliability mechanisms (schema validation,
write tracking, result grounding) — **not** patches overfit to specific tasks.

## Motivation (from the v1 failure analysis, clean baseline)

Tracing failing airline tasks on the clean baseline (post streaming-dedup merge)
showed a recurring, general cascade — e.g. Task 0:
1. agent calls `book_reservation(flight_type='one way')` — **malformed enum**
   (should be `one_way`) → tool errors;
2. agent then **hallucinates success** — "Reservation ID: HATHAT" — despite the
   error (no result grounding);
3. agent **re-books** (double write) → wrong DB state → reward 0.

Three general failure modes: (1) malformed tool args, (2) success claims not
grounded in tool results, (3) redundant non-idempotent writes. The v1 native
control-plane (resilience/observation controls) did not address these (full ≈
vanilla). v2 adds driver-boundary levers that do.

## Why the driver boundary (not the native control plane)

magi's native ADK control plane forbids plugin-level `on_before_tool` deny/rewrite
(it would bypass the agent-level permission gate). The τ-bench harness owns its
tool callables (`tau_env.py`) and loop (`episode.py`), so it CAN enforce
block/rewrite/verify at that boundary. v2 lives there. This is an independent
axis from the v1 control-plane flags.

## Components

### New: `magi_agent/benchmarks/taubench/reliability.py` (pure, no tau_bench, no network)
- `ReliabilityConfig` — frozen pydantic: `arg_validation: bool`, `dup_write_guard: bool`, `verify_before_final: bool` (all default False = behavior-preserving).
- `validate_args(tool_spec: dict, arguments: dict) -> str | None` — checks `arguments` against `tool_spec["parameters"]` JSON schema: required keys present, enum membership, basic type. Returns a corrective message string if invalid, else `None`.
- `WriteLedger` — records successful write tool calls; `WRITE_PREFIXES = ("book_", "cancel_", "update_", "send_")` classifies writes (configurable). Methods: `record(tool_name, args, ok: bool)`, `had_successful_write() -> bool`, `last_write_errored() -> bool`, `is_repeat_write(tool_name) -> bool` (a successful write of the same write-type already recorded).
- `verify_final(ledger: WriteLedger, agent_text: str) -> str | None` — if `agent_text` asserts success (success-language heuristic: contains "confirm"/"booked"/"reservation id"/"success"/"completed" case-insensitive) AND (`ledger.last_write_errored()` or not `ledger.had_successful_write()`) → return a one-time corrective string; else `None`.

### Modified: `tau_env.py` (gated by `ReliabilityConfig`)
In the per-tool `invoke` callable, before `env.step`:
- if `arg_validation`: `msg = validate_args(spec, arguments)`; if `msg`, **return `msg`** (corrective observation to the agent) WITHOUT calling `env.step`.
- if `dup_write_guard` and the tool is a write and `ledger.is_repeat_write(name)`: **return** a guard message WITHOUT executing.
- else execute `env.step`; then `ledger.record(name, args, ok = not error)`.
The ledger is created per episode and shared (like `EpisodeState`).

### Modified: `episode.py` (gated)
The current loop sends the agent's text to the user-sim as a `respond` every turn;
the user-sim decides when to STOP. There is no distinct "terminating respond"
event to hook, so `verify_before_final` triggers on **any** turn where the agent
text claims success: if `verify_before_final` and not yet nudged this episode,
`nudge = verify_final(ledger, agent_text)`; if `nudge`, **inject** `nudge` as the
next observation **instead of** routing the agent text as a respond (so the agent
gets one grounded chance to correct before the user-sim sees a false success
claim). Bounded to **one** nudge per episode (avoid correction loops). After the
single nudge is consumed, normal respond routing resumes.

### Modified: `cli.py` `run_eval`
- Add `reliability: ReliabilityConfig | None = None` (None = all off).
- Thread it into the agent/episode/tool construction.
- Measurement configs: **vanilla** = control-plane off + reliability off; **v2** = control-plane off + reliability all-on.

### Modified: `agent.py` / `harness` plumbing
Thread `reliability` + the per-episode `WriteLedger` through `build_magi_tau_agent` → `run_episode` → `build_env_function_tools`, alongside the existing `EpisodeState`.

## Data flow

per episode: create `EpisodeState` + `WriteLedger` → build tools (with reliability config + ledger) → run loop. Each tool call: validate args (L1) → dup check (L3) → `env.step` → record in ledger. On any turn whose agent text claims success: `verify_final` (L2) → at most one nudge per episode (injected instead of the respond), then normal respond routing resumes. Reward from `env_response` as before.

## Toggle / ablation

`ReliabilityConfig(arg_validation, dup_write_guard, verify_before_final)` — each independently toggleable. First measurement: **vanilla (all off) vs v2 (all on)**. If v2 helps, ablate per-lever (one on at a time) to attribute the lift.

## Measurement plan

1. **Quick subset first:** airline 50 tasks × 1 trial, vanilla-clean vs v2 (~1.5–2h each). Compare pass^1 + avg_reward.
2. If v2 shows a meaningful lift → **full airline 50 × 4** vanilla vs v2 (pass^1..4) + per-lever ablation.
3. Honest framing: report absolute + the delta; the levers are general, so a lift implies a general agent-reliability improvement, not airline overfit.

## Error handling

- Levers fail-open conceptually but here they *intervene* (block/return corrective). A lever bug must not crash the episode: wrap lever calls so an exception in a lever degrades to "no intervention" (execute normally) and is logged, never an episode crash.
- `validate_args` must be conservative: only reject on clear schema violations (missing required, enum mismatch, wrong primitive type). Do NOT reject on unknown-but-plausible args (avoid false blocks that hurt the agent).
- `verify_final` nudge is one-shot per episode; never an infinite correction loop.

## Testing (pure, no tau_bench / no network)

- `validate_args`: catches `flight_type='one way'` (enum miss), missing required key, wrong type; passes valid args; passes unknown optional keys.
- `WriteLedger`: write classification by prefix; `is_repeat_write` true on 2nd same-type successful write; `last_write_errored`/`had_successful_write` transitions.
- `verify_final`: returns nudge when success-language + (last write errored OR no successful write); returns None otherwise; respects one-shot.
- Integration (fake env + fake runner): tool callable returns the validation message without calling `env.step` on malformed args; dup write blocked; loop injects exactly one nudge.
- All gated off by default → existing tests unaffected; add reliability-on cases.

## Out of scope / non-overfit guardrails

- No task-specific rules, no airline-specific arg fixes — only schema-driven validation + generic write tracking + generic success-claim grounding.
- `verify_before_final` success-language heuristic is intentionally simple; if it proves too fragile in measurement, narrow it (last-write-errored only) rather than expanding task-specific patterns.
- No changes to the native control plane (separate axis, deferred).

## Success criteria

1. Pure lever functions + integration covered by fake-env/fake-runner tests; suite green; no tau_bench/network in tests.
2. Default-off `ReliabilityConfig` → byte-identical behavior to current harness when off.
3. Quick-subset measurement produces a reproducible vanilla vs v2 comparison; full run + ablation only if the subset shows promise.
4. Levers remain general (schema/ledger/grounding) — no per-task patches.
