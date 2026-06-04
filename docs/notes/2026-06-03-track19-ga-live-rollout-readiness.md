# Track 19 — General-Automation Live Harness Rollout Readiness Note

**Date:** 2026-06-03
**Status:** readiness gate implemented (Track 19 PR4). Gate is default-OFF.
No activation window has been opened. No live mutations are enabled by this
note.

## Master Flag

```
MAGI_GA_LIVE_ENABLED=1   # truthy: {1, true, yes, on} (case-insensitive)
```

Single source of truth in `magi_agent/config/env.py:general_automation_live_enabled()`.
Default OFF — the flag is absent from all current deployments. Setting it to
any non-truthy value (or leaving it unset) is a no-op versus `main`.

## Gate Stages

The `magi_agent/gates/ga_live_readiness.py` readiness gate decides whether GA
live execution is permitted for a given environment/bot. It is fail-closed:
any unknown or incomplete configuration resolves to `disabled` or `blocked`.

| Stage | Condition | `executionMode` | `status` | `liveExecutionAllowed` |
| --- | --- | --- | --- | --- |
| Disabled | `MAGI_GA_LIVE_ENABLED` not set or falsy | `disabled` | `disabled` | `False` |
| Shadow | flag ON + kill-switch OFF + shadow mode ON + valid scope, but gate \< 5 or promotion not confirmed | `shadow` | `shadow` | `False` |
| Canary-live | flag ON + selected bot `186bf3d7` + `promotedGate >= 5` + `canaryPromotionConfirmed = True` | `live` | `live` | `True` |
| Fleet | same as canary-live (gate controls both; fleet promotion is an ops scope expansion, not a separate code state) | `live` | `live` | `True` |
| Any blocking reason | kill switch, malformed digest, bot not selected, env not allowlisted, shadow disabled | `disabled` | `blocked` | `False` |

The `liveExecutionAllowed` field in `GaLiveReadinessConfig` is locked to
`Literal[False]` — it cannot be forged via env or serialisation (same pattern
as `workflow_executor_readiness.py::live_dispatch_allowed` and gate7's
`child_execution_allowed`).

## Canary Bot

- Bot short-id: `186bf3d7`
- Full id: `186bf3d7-7d00-4c8b-86c9-c1734c66a1e4`
- The gate's `selectedBotDigest` must be set to
  `sha256:<sha256-of-full-or-short-id>` matching the runtime `BOT_ID`.

## Promotion Criteria

The following must all be true before setting `canaryPromotionConfirmed=True`:

1. Shadow-mode observation complete with no unexpected policy-classifier
   divergences (compare `gatedCalls`, `blocked`, `approvalRequired`, `allowed`
   telemetry counters against pre-promotion baseline).
2. `promotedGate >= 5` recorded in the gate config (gates 1–5 previously
   satisfied).
3. At least one canary turn end-to-end with `liveExecutionAllowed=True` on bot
   `186bf3d7`, confirming the live gate emits receipts to the evidence ledger
   and the task-completion verifier sees them.
4. No hard-safety verifier escalations in the canary window.
5. Operator review of `completionVerifierRepairs` telemetry count (repairs
   indicate the model did not emit the required artifact receipt without
   prompting; repairs > 0 before fleet promotion require a root-cause review).

Fleet promotion (expanding `selectedBotDigest` beyond the canary) requires the
same criteria plus a separate explicit operator scope-expansion.

## Telemetry Counters

The gate exposes a `counterRequirements` list in health metadata so dashboards
know which counters to provision. The following counters are emitted via
`emit_ga_live_telemetry_record()`:

| Counter | Description |
| --- | --- |
| `gatedCalls` | Total tool-dispatch calls that passed through the live gate |
| `blocked` | Gate decisions that returned `deny` |
| `approvalRequired` | Gate decisions that returned `ask` (pending_control_request) |
| `allowed` | Gate decisions that returned `allow` (proceed) |
| `completionVerifierRepairs` | Finalise repairs triggered by the task-completion verifier |

Counter call sites: `harness/general_automation/live_gate.py` (gatedCalls/
blocked/approvalRequired/allowed) and the PR3 task-completion verifier
(completionVerifierRepairs). Wiring the counter increments into those paths is
a straightforward non-invasive follow-up in the P1/P2 parallel track.

## Invariants Preserved

- `MAGI_GA_LIVE_ENABLED` default OFF — this note does not change that.
- No hard-safety verifier, authority flag, or sealed core path was modified in
  PR4. The gate is an *observer/decider*, not an executor.
- The gate `GaLiveReadinessConfig` model uses `extra="forbid"`, `frozen=True`,
  and `validate_default=True`, consistent with the surrounding gate models.
- The coding-workflow executor rollout pattern (`workflow_executor_readiness.py`)
  is preserved; this gate mirrors it without sharing state.

## Files Changed (PR4)

- `magi_agent/gates/ga_live_readiness.py` — the gate (new file)
- `tests/test_ga_live_readiness.py` — 27 tests, all GREEN
- `docs/notes/2026-06-03-track19-ga-live-rollout-readiness.md` — this note
