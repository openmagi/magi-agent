# Post-P6 Roadmap — Authoring Ecosystem (Pack B) + Full Microkernel (Pack C)

> **Status: ROADMAP (chains after P1–P6).** Detailed per-phase docs for B and C are authored
> **after P6 lands**, grounded in the *shipped* pack APIs (authoring them now would fabricate
> against types that don't exist yet — the same lesson the Phase-0 oracle taught when it proved
> the doc's optimistic control-plane schema wrong). This file defines scope, decomposition, and
> the chaining/autonomy model so execution can continue without re-deciding.

Picks up where `00-BLUEPRINT.md` ends: P1–P6 = neutral runtime + 8 `provides` types +
control-plane migration + §1 no-privilege proof (the architecture + pattern). This roadmap makes
neutrality **usable** (Pack B) and pushes the kernel toward **fully minimal** (Pack C).

---

## Pack B — Authoring Ecosystem ("neutral but discoverable")

**Why:** after P6 the runtime *is* neutral, but a third party has no obvious way to discover,
scaffold, author, and ship a pack. Without B, no-privilege is real-but-unusable (the 0.1.29
diagnosis P6: "third-party ecosystem = policy doc only, 0 external examples").

**magi-agent repo (branch-isolated, autonomous behind gates):**
- **B1 — `magi pack new <type>` scaffolding CLI.** Generates a ready `pack.toml` + impl stub
  (`module:symbol`) + test stub for any of the 8 provides types. Acceptance: `magi pack new
  validator my-check` produces a loadable pack whose smoke test passes.
- **B2 — Authoring docs.** `pack.toml` schema reference; the typed-context API reference per
  primitive type (what each `*Ctx` exposes = the capability ceiling); "write your first pack"
  guide; the capability-parity statement (first-party uses the same contexts). Lives in
  `docs/` of magi-agent (OSS-facing).
- **B3 — Example third-party pack.** A real, external-shaped working pack (e.g. a custom
  validator + a custom callback) committed as a template + smoke test, proving the end-to-end
  authoring path a stranger would walk.

**clawy repo (hosted/bot-facing — NOT in the local-full-trust autonomy bucket; CHECKPOINT before rollout):**
- **B4 — Recipe-making skill assessment + fix.** The bot skill that authors recipes. Assess
  whether it still works against the manifest-built flat catalog + surfaces pack-authored
  primitives; fix if needed. Rollout = template sync to running bots → **mass-bot-patch care +
  Kevin checkpoint** (real bots).
- **B5 — Hosted "Customize" tab assessment + fix.** The dashboard customize flow composes catalog
  refs; assess against the new pack/catalog model; fix if needed. **Hosted = real users →
  checkpoint before merge/deploy.**
- B4/B5 are **conditional** ("if needed") — first task is an ASSESSMENT against the shipped pack
  surface; only plan fixes if the assessment finds breakage.

---

## Pack C — Full Microkernel (decompose the deferred first-party into policy-packs)

**Principle (same as P5):** every subsystem decomposes into **policy (→ removable pack)** +
**mechanism/store (→ kernel)**. Nothing below is *inherently* kernel — it's deferred, not
excluded. Apply the P5 policy/mechanism split at larger volume; parallelizable by subsystem.

| Subsystem (currently hardcoded) | → policy-pack | → kept in kernel |
|---|---|---|
| **gates** (`gate5b_full_toolhost.py` ~89KB) | tool impls → `tool` packs; authz/dispatch *policy* → `control_plane`/`validator` packs | pure tool-dispatch wiring |
| **goal_loop_control** | "continue/stop" *policy* → loop-policy pack (new provides sub-type or control_plane) | `run_async` re-entry mechanism |
| **scheduler** | "which job / when" *policy* → schedule-policy pack | timer + executor mechanism |
| **memory** (`memory_recall/write/compaction`) | recall/write/compaction *strategy* → strategy packs | memory **store** (mirrors evidence-ledger split) |

**Per-subsystem method (Ci):**
1. Build a golden-style behavior oracle for that subsystem (the Phase-0 pattern) — capture current
   behavior before touching it.
2. Decompose: extract the policy into pack primitive(s) receiving a typed context; keep only the
   bare mechanism in kernel; expose the capabilities the policy needs on the context (capability
   parity).
3. Migrate first-party policy into bundled packs; flip the live path to pack-loaded.
4. Gate: subsystem oracle green (or intentional-diff adversarially verified) + full suite.

**C acceptance (the fully-minimal kernel):** the irreducible kernel = `{ loader, registries,
typed-context dispatcher, ADK loop }` + bare stores/dispatch (evidence store, hook bus, memory
store, session service, event sink). **Every** opinionated policy across gates/goal-loop/scheduler/
memory is a removable pure pack; the §1 no-privilege assertions hold across all of them.

---

## Sequencing & chaining (after P6 green)

```
P6 green
  → (auto) /workflows authors Pack B + Pack C DETAILED phase docs, grounded in shipped APIs
  → implement Pack B (B1–B3 magi, autonomous behind gates; B4–B5 clawy = assess → checkpoint)
  → implement Pack C (C1 gates is the big one; C2/C3/C4 parallelizable by subsystem)
  → final §1 acceptance across ALL subsystems + full suite + real-model E2E
  → pre-merge report → Kevin merges
```

**Autonomy (same policy as P1–P6, `feedback_autonomy_branch_isolated`):**
- magi-agent work (B1–B3, all of C): branch-isolated, fully reversible → **autonomous behind
  gates** (oracle/tests/adversarial review on behavior changes). Real HALT only on merge,
  genuine golden ambiguity, or destructive op.
- clawy work (B4–B5): hosted/bot-facing, affects real users → **checkpoint before merge/deploy**
  (NOT the same full-trust-local bucket). Plan + implement on a branch freely; rollout is Kevin's.

**Ordering note:** Pack B (usability) should land before Pack C (completeness) — a usable neutral
runtime with the 8 core types is more valuable to ship than a maximally-minimal kernel no one can
author against yet. C can proceed in parallel/after as capacity allows.
