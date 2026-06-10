# Default-Off Gates

> Status: **proposal / forming.** This page defines the staged-rollout vocabulary
> for default-off feature seams. The stage *definitions* below are stable enough
> to navigate by; the stage *assignment* of individual flags is still being
> ratified with the feature owners and may move. Treat a flag's stage as
> "maximum stage this seam can safely reach," not a promise that it is on.

Open Magi Agent ships most new behaviour behind a **default-off** environment
flag. The flag registry lives in
[`magi_agent/config/flags.py`](https://github.com/openmagi/magi-agent/blob/main/magi_agent/config/flags.py)
(`FlagSpec.stage`), and the env-reference enumerates the public ones in
[Environment Variable Reference](/docs/env-reference).

This page answers the two questions a contributor or self-host operator hits
when they meet one of those flags:

1. **What does turning this on actually do** — observe-only, local behaviour
   change, or live authority?
2. **When is a flag allowed to move up a stage** (and what stops it from sitting
   off forever)?

## Why staged gates exist

A boundary or feature seam that lands "fully on" couples an unproven change to
live traffic. Staged gates decouple *shipping the code* from *granting it
authority*: the seam merges default-off, runs in observe-only mode while it
earns evidence, becomes flippable for local validation, and only then becomes a
default-on candidate. Each step is reversible by flipping the flag off.

The runtime reinforces this at the type level for the strongest boundaries:
authority flags such as `traffic_attached` / `execution_attached` are typed
`Literal[False]`, so promoting them past observe-only requires a deliberate code
change, not a config tweak. See
[Boundaries](/docs/boundaries) and the
[developer overview](https://github.com/openmagi/magi-agent/blob/main/internal/docs/developer-overview.md).

## The three stages

The stages map directly to `FlagSpec.stage` (`stage1` / `stage2` / `stage3`) in
the registry.

| Stage | Meaning | What "on" does | Traffic impact |
|---|---|---|---|
| **Stage 1 — Built / Observe** | Code exists, default-OFF. Turning it on records or logs only; it does not change a decision or output. | Observe-only / audit-only. Output is byte-identical (or at most adds telemetry). | None. |
| **Stage 2 — Local / Gated** | Wired and flippable. Turning it on changes behaviour at local / single-user scope (blocks, routes, exposes a tool). May need a live dependency (provider key). | Real behaviour change, fully reversible by flipping the flag off. | Local / session-scoped only. |
| **Stage 3 — Authority / Traffic** | Validated and ready to attach authority to hosted live traffic and managed systems. A default-ON candidate. | Authority attached to production decisions. | Production. |

### Stage 1 — Built / Observe

The seam is merged and structurally complete but inert with respect to outcomes.
Enabling it must not change which decision the agent makes or what it emits to
the user — at most it writes a ledger entry, a metric, or a log line. A reader
who flips a Stage 1 flag should be able to diff the run and see no behavioural
delta beyond observability.

Entry criteria:

- Unit tests green for the seam.
- The seam is **fail-open**: if the observation path errors, the turn proceeds
  unchanged.
- No live dependency is required to keep the default (off) path working.

Representative flags (default-OFF, observe-or-scaffold today):

- `MAGI_DEEP_WEB_RESEARCH_ENABLED` — the live deep web-research harness; off by
  default, the `WebSearch` / `WebFetch` tools return an honest
  `web_research_not_configured` error rather than simulated results.
- `MAGI_LEARNING_ENABLED` — the learned-skills / self-improvement loop master
  switch; the loop records candidates but does not feed the live serve path.
- `MAGI_OBSERVABILITY_ENABLED` — the hook-tap observability module (pure
  visibility, no decision change).

### Stage 2 — Local / Gated

The seam is wired into a real code path and flipping the flag visibly changes
behaviour, but only at local / single-user scope: it blocks an action, routes a
request, or exposes a tool for *this* process or session. The change is always
reversible — turning the flag off restores the prior behaviour immediately, so
the flag itself is the rollback mechanism.

Entry criteria (in addition to Stage 1):

- Integration tests covering the on-path behaviour.
- An explicit, documented rollback: flipping the flag off returns to the Stage 1
  behaviour with no residual state.
- An operator-facing reference entry (the flag appears in
  [Environment Variable Reference](/docs/env-reference)).

Representative flags:

- `MAGI_MEMORY_ENABLED` — the agent memory subsystem master switch; turning it
  on changes recall/persistence behaviour for the local agent.
- `MAGI_CHANNEL_WORKFLOWS_ENABLED` — bot-user dynamic channel workflows
  (classifier-driven routing) at the channel scope.
- `MAGI_RUNTIME_PROFILE` — selects a runtime profile (`safe` / `eval` / …) that
  reversibly disables the default-ON resilience seams.

### Stage 3 — Authority / Traffic

The seam is validated enough to attach authority to hosted live traffic and
managed systems — a default-ON candidate. Reaching Stage 3 is where a flag is
considered for flipping its default to ON.

Entry criteria (in addition to Stage 2):

- Live measurement evidence that the on-path behaves as intended on real
  traffic (not just fixtures).
- A hosted-deployment synchronisation plan: the hosted control plane
  (`MAGI_CONTROL_STAGE`) and any provisioning-worker env overrides must move
  together, since code defaults alone do not change already-running pods.
- A canary / staged exposure before a fleet-wide flip.

`MAGI_CONTROL_STAGE` is the hosted control-plane stage selector itself and is the
mechanism by which hosted boundaries are advanced toward live authority; it is a
Stage 3 control rather than a Stage 1 observe seam.

### Already-promoted defaults (default-ON)

A handful of seams have completed the ladder and ship default-ON, with the flag
retained as a kill switch / honesty selector rather than an opt-in:

- `MAGI_NATIVE_RECEIPTS_HONEST` — default-ON; keeps native tool receipts honest
  (no fabricated success). Flipping it off is a deliberate downgrade, not an
  opt-in.
- The profile-aware resilience and coding-harness seams (`MAGI_LOOP_GUARD_ENABLED`,
  `MAGI_LSP_DIAGNOSTICS_ENABLED`, `MAGI_RIPGREP_ENABLED`, …) resolve ON in the
  full runtime profile and OFF under a safe/eval `MAGI_RUNTIME_PROFILE`. They are
  modelled as `profile_bool` in the registry so their default is reported as
  profile-resolved, not a flat constant.

## Promotion and the "don't strand a seam" policy

A stage is not a place a flag lives forever. Promotion is the act of moving a
flag from stage N to stage N+1 once the entry criteria for N+1 are met — and,
for the final hop, flipping its default to ON.

**Policy — a default-off seam must be promoted or deleted within two minor
releases.** Once a seam ships at Stage 1, the owning area is expected to either
advance it (gather the evidence and move it to Stage 2/3, or flip its default)
or remove it within two minor releases. Seams that cannot earn their next stage
in that window are dead weight: they accumulate untested on-paths, mislead
operators about what the agent can do, and rot. "Enable-able or deleted" is the
rule; indefinite scaffold is not an allowed state.

Concretely, when adding a default-off flag:

1. Register it in `magi_agent/config/flags.py` with its `scope` and the maximum
   `stage` the seam can safely reach (this is a one-line registry entry — the
   env-reference and stage table are generated from it).
2. Land it default-OFF at Stage 1.
3. Within two minor releases, open a follow-up that either promotes it (with the
   evidence the next stage requires) or removes the seam and its flag.

## Rolling back

Every stage is reversible by flipping the flag off:

- **Stage 1 / Stage 2** — set the flag to a falsey value (`0` / `false` / unset).
  The seam returns to its prior behaviour with no residual state. Because the
  flag *is* the rollback mechanism, no migration is needed.
- **Stage 3 / default-ON** — for hosted authority, rolling back also means
  reverting the hosted control-plane stage (`MAGI_CONTROL_STAGE`) and any
  provisioning-worker env override in lockstep; a code-default change alone does
  not affect already-running pods. Roll back the fleet via the same canary path
  used to promote.

For the strongest boundaries (authority flags typed `Literal[False]`), there is
no runtime override at all — they cannot be flipped on by configuration, so
their "rollback" is simply that they were never granted authority without a code
change.

## See also

- [Environment Variable Reference](/docs/env-reference) — the public flags.
- [Boundaries](/docs/boundaries) — how boundary authority is structured.
- [What Works Today](/docs/what-works-today) — the live vs. shadow vs. planned map.
