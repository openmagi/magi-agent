# τ-bench v3 — Completion+Scope Self-Review Lever (L4) + Measurement Hardening (Design)

Date: 2026-06-10
Branch: `feat/taubench-harness`
Status: design (pending implementation plan)

## Goal

Add a fourth toggleable, default-OFF reliability lever (`completion_review`, "L4")
at the τ-bench driver boundary that injects a single in-loop **completion + scope
self-review** before the agent concludes, plus a small measurement-fidelity
hardening of the retry path. L4 targets the dominant failure modes the vanilla
failure analysis surfaced (under-action 48% + over-action 15% = 63% of failures)
with a **general** mechanism — not benchmark overfitting.

## Motivation (from the vanilla 50-task failure analysis)

Classifying all 27 vanilla failures (airline, Sonnet 4.5):
- **C. UNDER_ACTION — 13/27 (48%)**: agent dropped a required, policy-permitted
  write (skipped a cancel, missed one item in a multi-item task, recognized a
  compensation but never issued it, invented a blocker and gave up).
- **B. CONSTRAINT_REASONING — 9/27 (33%)**: wrong value chosen (cabin, payment
  split, baggage, flight). Hard reasoning; partly overfit-risk; NOT this lever's
  target.
- **D. OVER_ACTION — 4/27 (15%)**: performed a write the user did not authorize
  (cancelled the whole reservation, committed a conditional change, yielded to a
  social-engineering "the rep approved it").
- **H. harness artifact — 1/27**: transient first-turn infra error (see below).

v2's existing levers (L1 arg-validation, L2 ledger-grounded success-claim, L3
dup-write) appear only as *secondary* causes here — they hit the long tail, which
is why v2 moved the score only +6pp (within single-trial noise). The head of the
distribution is **action discipline**: did the agent do every requested action,
and only those? That is what L4 targets.

## Why an in-loop LLM self-review (not deterministic, not native self_review)

- At runtime the harness does **not** have the ground-truth action list. Using it
  would be cheating/overfitting. So the check cannot be a deterministic comparator
  — it must reason from the conversation alone.
- magi's native `MAGI_SELF_REVIEW_ENABLED` control is an `on_after_agent`
  hook that runs in the **background and is observe-only** (returns `None`); it
  cannot inject an in-loop correction into the same episode. v1-full (all six
  control-plane flags on) was a null result partly because of this. L4 is
  genuinely different: it injects a corrective observation **in-loop**, at the
  moment the agent signals it is concluding, forcing a re-check before the
  user-sim sees the (possibly premature) conclusion.

L4 is therefore a new **driver-boundary** lever, consistent with v2's
architecture (harness-owned, default-OFF, toggleable), living in `reliability.py`
+ `episode.py`.

## Components

### Modified: `magi_agent/benchmarks/taubench/reliability.py` (pure, no tau_bench/network)
- `ReliabilityConfig` gains a fourth field: `completion_review: bool = False`
  (and `any_enabled` ORs it in).
- `_CONCLUSION_MARKERS`: the existing `_SUCCESS_MARKERS` **plus** refusal/closure
  phrases: "unable to", "cannot", "can't", "i'm sorry", "i am sorry", "is there
  anything else", "anything else i can", "won't be able", "not able to",
  "unfortunately". (Lowercased substring match.)
- `is_conclusion(agent_text: str) -> bool`: True if any `_CONCLUSION_MARKERS`
  phrase is present. Detects the agent wrapping up via either a success claim OR a
  refusal/closure — so under-action (refusal) and over-action/false-success are
  both caught.
- `completion_review_nudge() -> str`: a fixed, domain-agnostic self-review prompt
  (no GT, no airline rules). Content (exact string fixed in the plan):
  > "Before you confirm completion or close this out: re-read the user's messages
  > and list every concrete action they asked you to perform. For each, state
  > whether you actually executed it (and with which tool call) or not. Then check
  > whether you performed any action the user did NOT request. If a requested
  > action is missing, perform it now. If you performed an unrequested action,
  > correct it. Only confirm completion once every requested action — and only
  > those — has been done. Do not claim completion you cannot support."

### Modified: `magi_agent/benchmarks/taubench/episode.py`
- `run_episode` already has the one-shot L2 path (`nudged` latch + `verify_final`).
  Add an **independent** one-shot latch `reviewed = False` for L4.
- In the loop, after `if state.done: break`, before routing the respond, two
  **sequential** `if … continue` blocks (L2 first, then L4):
  - L2 (existing): `if cfg.verify_before_final and not nudged:` → `verify_final(...)`;
    if nudge, set `nudged=True`, `obs=nudge`, `continue`.
  - L4 (new), as a separate block right after: `if cfg.completion_review and not
    reviewed and is_conclusion(agent_text):` → set `reviewed=True`,
    `obs=completion_review_nudge()`, `continue`.
  - Because L2 is evaluated first and `continue`s when it fires, it naturally takes
    precedence within a single turn (deterministic, cheaper). The two latches
    (`nudged`, `reviewed`) are independent, so across the episode L2 may fire once
    and L4 may fire once (max two nudges total; `max_steps` bounds the loop).
- L4 fail-open: `is_conclusion` is a pure substring check; wrap the L4 condition's
  function calls so an exception degrades to no-injection.

### Modified: `magi_agent/benchmarks/taubench/cli.py`
- No signature change — `reliability: ReliabilityConfig | None` already threads
  through. The new `completion_review` field rides along and is emitted in the
  report via `model_dump()`. Measurement sets it programmatically.

### Modified: `magi_agent/benchmarks/taubench/harness.py` (measurement fidelity)
- `run_with_retry(attempt)` currently retries once immediately on `infra_error`.
  Strengthen to retry up to **2** times with a short bounded backoff between
  attempts (a passed-in `sleep` callable defaulting to `time.sleep`, so tests
  inject a no-op and assert call counts without real delay). Still counts as
  non-success + surfaces `infra_error` if all attempts infra-error. This reduces
  transient-API-blip noise (the task-13 artifact) without masking real failures.

## Data flow

per episode: `EpisodeState` + `WriteLedger` (+ `nudged`, `reviewed` latches). Each
turn: agent runs (tools record into ledger via tau_env). On a respond turn, in
order: L2 (ledger-grounded success-claim) → else L4 (conclusion → completion+scope
self-review). At most one nudge of each kind per episode. Reward unchanged.

## Trigger heuristic & error handling

- `is_conclusion` is intentionally broad (success ∪ refusal/closure) and one-shot:
  a slightly-early trigger is low-cost (the agent re-checks and either fixes a gap
  or re-affirms, then continues), because L4 injects a *reasoning prompt*, not a
  block. If measurement shows the trigger is too eager/noisy, narrow the marker
  set (documented as the tuning knob) rather than adding task-specific logic.
- All L4 lever calls fail-open (exception → no injection; episode never crashes).
- L4 nudge is strictly one-shot per episode (the `reviewed` latch); no correction
  loops.

## Toggle / ablation / measurement

- `ReliabilityConfig(arg_validation, dup_write_guard, verify_before_final,
  completion_review)` — all independently toggleable, all default False.
- Measurement: **vanilla (all off) vs v3 (completion_review only)** airline 50×1
  to isolate L4. If it shows a meaningful lift → **v2+v3 (all four on)** and full
  50×4 + per-lever ablation. Honest framing: report absolute + delta; single-trial
  SE ≈ ±0.07, so a quick-subset delta is a signal, not proof.

## Out of scope / non-overfit guardrails

- No GT at runtime; no airline-specific rules in markers or the nudge prompt.
- B-bucket constraint-reasoning failures (cabin/payment/flight value errors) are
  NOT targeted here — some are genuine hard reasoning / overfit-risk.
- The completion-review prompt must stay a general "do all and only what was
  asked" check; it must not enumerate domain policies.
- The analysis driver (`/tmp/taubench_analyze_failures.py`) is a throwaway tool,
  not part of this change.

## Testing (pure + integration, no tau_bench / no network)

- `is_conclusion`: catches success ("...is booked"), refusal ("I'm unable to do
  that"), closure ("is there anything else I can help with?"); does NOT fire on an
  info-seeking question ("Can you confirm your travel dates first?").
- `completion_review_nudge`: returns a non-empty string; contains no airline
  tokens (no "flight"/"reservation"/"cabin" hardcoding).
- `ReliabilityConfig`: `completion_review` defaults False; `any_enabled` reflects
  it.
- episode integration (fake runner): a refusal/closure respond with
  `completion_review=True` → exactly one L4 nudge injected as the next observation
  (respond replaced); an info-question respond → no injection; one-shot enforced;
  L2 and L4 latches independent (L2 firing doesn't consume L4 and vice versa);
  with all levers off → behavior byte-identical.
- `run_with_retry`: infra-error then success on 2nd attempt → success, sleep
  called once; persistent infra-error → (False, True) after the bounded attempts,
  sleep called the bounded number of times; no infra-error → no retry, no sleep.
- Full module suite stays green (current 60 tests + new).

## Success criteria

1. New pure functions + integration covered by fake-runner tests; suite green; no
   tau_bench/network in tests.
2. `completion_review=False` (default) → byte-identical behavior to v2.
3. Quick-subset measurement produces a reproducible vanilla vs v3 comparison.
4. L4 trigger + prompt remain general (no GT, no airline-specific tokens).
5. `run_with_retry` backoff reduces transient-infra noise without masking real
   failures (asserted via injected sleep + deterministic attempt counts).
