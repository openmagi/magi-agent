# Neutral OSS Local Runtime — Implementation Blueprint

> **For agentic workers:** This is the pack root. Each phase is a separate plan doc
> (`01-…` … `07-…`). REQUIRED SUB-SKILL for executing any phase:
> `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans`.
> All steps use checkbox (`- [ ]`) syntax. This pack is **self-contained** — it does not
> reference any doc outside this repo.

**Goal:** Make the magi-agent runtime *neutral* — every primitive seam (tool, callback,
validator, harness, control-plane policy, evidence-producer, recipe, connector/MCP) is
authorable by a user through the **same** disk-pack mechanism first-party uses, first-party
holds **no privilege** (loaded the same way, removable, capability-parity by a typed context),
and authored primitives **execute live**. Scope: **OSS local, full-trust.** Hosted stays
opinionated separately and is out of scope.

**Architecture:** A near-empty microkernel — `{ pack loader, primitive registries, typed-context
dispatcher, ADK turn loop }` — plus everything-else-as-removable-packs. Packs are discovered from
disk (`pack.toml` manifests), declare `provides` entries statically (so the catalog builds without
executing pack code), and point at impls via `module:symbol`. First-party ships as **bundled
packs in the same format/loader**. Each primitive impl receives only a narrow typed context, so
first-party and third-party have identical capability.

**Tech stack:** Python 3.12+, `uv`, pydantic v2 (frozen models, `extra="forbid"`), Google ADK
(`google.adk`), `tomllib` (stdlib), pytest. Runtime entrypoints: `magi_agent/main.py` (serve),
`magi_agent/cli/real_runner.py` (`build_cli_model_runner`), `magi_agent/adk_bridge/` (ADK seam).

---

## 0. Ground truth (measured against HEAD 802e707b)

Implementer agents MUST treat these as the starting facts and **re-verify each at the point of
use** (the tree moves; line numbers drift — grep, don't trust line refs blindly).

- **Composition plane already near-neutral:** `authoring/` (recipe builder), `recipes/compiler.py`
  (`PackRegistry`, `RecipePackManifest`, `_first_party_packs()` at ~`:1932`/`:1341`),
  `authoring/compiler.py` (`CompileRecipePackCatalog`, `.default()` at ~`:70`), MCP
  (`plugins/mcp_adapter.py`), skills (`plugins/native/skills.py`, `_WORKSPACE_SKILL_BASES`).
- **Primitive plane privileged (the gap):** `App(name=…, root_agent=agent, plugins=[plane_plugin])`
  in `cli/real_runner.py:~254`; `build_default_plugin()` in `adk_bridge/control_plane.py:~1175`
  assembles 6 first-party LoopControls; catalog is always `.default()` hardcode on the live path.
- **Liveness already wired (good news):** validator / evidence-requirement / phase-routing
  enforcement runs live on the CLI path at `cli/engine.py:~2138–2186` (re-grep for
  `evidence_requirements`, `required_validators`, `phase_route_decision`). `approval_gates` is
  emit-only. HTTP path returns the plan as metadata only.
- **Control-plane coupling (the hard knot):** 6 LoopControls in `build_default_plugin()`:
  1 trivial (`MaxStepsBrakeControl`), 5 need interface work, reaching **4 internal seams**:
  (S-A) evidence ledger / receipt store (`GaConstraintReinjectionControl`),
  (S-B) nested session/event traversal (`SelfReviewAfterTurnControl`),
  (S-C) per-invocation mutable state lifecycle (`_EditRetryLoopControl._attempts`,
  `_ResilienceLoopControl._detectors/_recovery_state`),
  (S-D) boundary services `ContextLifecycleBoundary` + `WorkspaceSessionService`
  (`_CompactionLoopControl`).
- **Out of scope:** `gates/` (esp. `gate5b_full_toolhost.py` ~89KB) is a **separate tool-dispatch
  layer**, NOT control-plane — do not migrate it here.
- **`ForkRunner`** (SelfReview's isolated-agent spawn) is a privileged op; for full-trust local we
  **expose it on the public context** (a design decision, not a rewrite).
- **Verification surface GOOD:** headless fake-model via `LOCAL_DEV_MODEL_SENTINEL = "local-dev"`
  (`config/env.py`) — **no API keys**; ~96 control-plane behavior tests; all 4 hard scenarios
  reachable in isolation via mock runner. **Gap:** no turn-level golden oracle → **Phase 0 builds
  one (load-bearing).**

---

## 1. The "no privilege" acceptance spec (the whole pack passes only if this holds)

> Every first-party behavior is expressible and loadable through the **same** (a) pack loader,
> (b) flat catalog registration, and (c) typed primitive context as third-party — **no in-code
> shortcut, no first-party-only ref tier, no richer handle for first-party.**

Concrete final-state assertions (each becomes a test in later phases):
- `grep` finds **no** first-party LoopControl/tool/validator registered via a hardcoded path that a
  user pack cannot replicate (target: `build_default_plugin` no longer hand-assembles controls;
  it loads them from bundled packs).
- The live catalog is built from loaded pack manifests, not `CompileRecipePackCatalog.default()`.
- A user pack in `~/.magi/packs/` can **add** a primitive of every `provides` type, **override** a
  first-party ref, and **remove** (forbid) a first-party ref — verified by tests.
- Every primitive impl's signature takes only its typed context (no `RuntimeContext`/AppState
  god-object, no privileged kwargs for first-party).

---

## 2. Architecture decisions (D1–D7) — the contract phases must honor

- **D1 Loader:** disk-manifest discovery is canonical. Search path =
  bundled first-party dir (`magi_agent/firstparty/packs/`) + user dirs (`~/.magi/packs/`,
  `<cwd>/.magi/packs/`) via `pack.toml` discovery. `config.toml [packs]` controls
  enable/disable/order/override. Entry-points discovery is a *later, secondary* source (not v1).
- **D2 Unified `provides` schema (8 types):** `tool · callback · validator · harness ·
  control_plane · evidence_producer · recipe · connector` (MCP under connector). `before_model`
  folds into callback/control_plane; `hard_invariant` omitted (hosted floor).
- **D3 Registration ABI:** declarative `pack.toml` lists `provides` **statically**; each entry =
  `ref` + `impl = "module:symbol"` (code) or `spec = "relpath"` (declarative recipe). Loader builds
  catalog from the manifest **without importing impls**; impls are **lazy-imported** at registration
  time. Ordered types (`callback`, `control_plane`) carry `priority` + `phase`; `control_plane`
  carries `gate_position` (default `after` the permission gate; explicit opt-in to move earlier).
- **D4 Catalog from manifests, flat:** `CompileRecipePackCatalog` (live path) is built from the
  union of loaded packs' `provides` refs. No first-party-only tier.
- **D5 Typed context per type (capability parity):** each primitive impl receives a narrow, typed,
  read-mostly context exposing exactly its type's capabilities; first-party gets the same object.
  Designed so it can later carry a capability-set (the hosted extension) without signature change.
- **D6 Microkernel core:** core = `{ loader, registries, typed-context dispatcher, ADK loop }`.
  Everything else (permission gate, evidence gates, validators, control-plane policies, compaction,
  recipes) = removable first-party packs. For full-trust local even the permission gate is
  removable.
- **D7 Liveness:** lean on already-live enforce points (validator/evidence/phase at
  `cli/engine.py`). Extend: (a) approval-gate enforcement (follow the evidence-gate pattern);
  (b) **control-plane decouple** — `build_default_plugin`/`App(plugins=…)` must accept user-pack
  LoopControls in parallel with first-party (the de-privileging keystone).

---

## 3. File-structure map (what gets created; new code is greenfield)

New package `magi_agent/packs/` (the neutral kernel):
- `magi_agent/packs/manifest.py` — `PackManifest`, `ProvidesEntry` (8 typed variants) — pydantic.
- `magi_agent/packs/discovery.py` — search-path resolution + `pack.toml` discovery + `config.toml
  [packs]` override.
- `magi_agent/packs/loader.py` — orchestrates discovery → static catalog build → lazy impl import →
  registry registration.
- `magi_agent/packs/registries.py` — typed registries per primitive type (or one keyed registry).
- `magi_agent/packs/context.py` — the D5 typed-context dataclasses per type + the dispatcher that
  builds them from ADK callback args.
- `magi_agent/packs/catalog_build.py` — manifests → `CompileRecipePackCatalog` (D4).
- `magi_agent/firstparty/packs/` — bundled first-party packs (`*/pack.toml` + impls), migrated from
  hardcode in later phases.

Modified (existing, requires read-first):
- `cli/real_runner.py` (`build_default_plugin`/`App` construction) — accept loaded packs.
- `adk_bridge/control_plane.py` — the 6 LoopControls → typed-context impls.
- `authoring/compiler.py` — catalog injection point.
- `cli/engine.py` — confirm user-registered validators/evidence reach the live enforce path.
- `config/env.py`, config loading — `[packs]` section.

Tests mirror under `tests/packs/…` and `tests/firstparty/…`; the golden oracle under
`tests/fixtures/neutral_runtime_golden/`.

---

## 4. Phases & dependency graph

```
Phase 0  Golden oracle + baseline           (load-bearing; unblocks all verification)
   │
Phase 1  Manifest + discovery + loader       (greenfield)
   │            │
Phase 2  Typed-context ABI + registries      (greenfield)  ← depends on P1 manifest types
   │            │
Phase 3  Validator vertical slice end-to-end (proves architecture)  ← P1+P2
   │
   ├── Phase 4  Easy provides types (tool, evidence_producer, recipe, connector/MCP, harness, callback)
   │            (each independent; PARALLELIZABLE)  ← P1+P2+P3 pattern
   │
Phase 5  Control-plane migration (6 controls / 4 seams)  ← P2 typed-context + P0 oracle
   │            (S-A…S-D each independent after the shared context surface; PARTLY PARALLEL)
   │
Phase 6  First-party migration + microkernel shrink + flat catalog flip  ← all above
   │            (de-privileging keystone; flips build_default_plugin to pack-loaded)
   │
Phase 7  Acceptance: the §1 "no privilege" assertions as tests + local-trust default flip
```

**Hard ordering:** P0 → P1 → P2 → P3 before anything parallel. P5 needs P2's typed contexts and
P0's oracle. P6 needs P3+P4+P5. P7 last.

---

## 5. /workflows parallelization map

Use this to drive `/workflows` once each phase doc exists.

| Stage | Parallel? | Units | Barrier after? |
|---|---|---|---|
| P0 oracle | serial (1 agent) | build 4-scenario golden harness | yes — everything depends on it |
| P1 loader | mostly serial | manifest → discovery → loader → catalog_build (pipeline, each stage feeds next) | yes |
| P2 ABI | partial | context dataclasses (parallel per type) ∥ registries ∥ dispatcher; then integrate | yes |
| P3 slice | serial (1 agent) | validator end-to-end (proof) | yes — gates P4/P5 |
| **P4 easy types** | **HIGH parallel** | 6 independent agents: tool / evidence_producer / recipe / connector-MCP / harness / callback — each authors+implements its type against the P3 pattern | merge/dedup |
| **P5 control-plane** | **partial parallel** | shared step: design the 4 seam surfaces (serial, 1 agent) → then 4 parallel agents, one per seam (S-A ledger, S-B session-snapshot, S-C state-lifecycle, S-D boundary), each migrating its control(s) + golden-diff verify | barrier (oracle diff) |
| P6 migration | partial | per first-party pack migration is independent once the kernel accepts packs; control-plane flip is serial | yes |
| P7 acceptance | serial | run §1 assertions as tests | done |

**Workflow shape (canonical):** `pipeline(units, implement_stage, verify_stage)` where
`verify_stage` runs the phase's pytest selection + (for P5) a golden-oracle diff. Adversarial
review subagents per unit are recommended for P5 (the high-risk knot).

---

## 6. Conventions every phase MUST follow

- **TDD, bite-sized:** write failing test → run (see it fail) → minimal impl → run (pass) → commit.
  One logical change per commit. Conventional-commit messages (`feat(packs): …`, `test(packs): …`,
  `refactor(control-plane): …`).
- **Verify with fake-model, no keys:** set `MAGI_CONFIG` to an isolated temp config to avoid
  `~/.magi/config.toml` contamination (known test-env gotcha); use `LOCAL_DEV_MODEL_SENTINEL`
  (`"local-dev"`) for runtime runs. Provider keys are NOT required for any test in this pack.
- **Re-grep, don't trust line numbers:** all `:NNN` refs in this pack are HEAD-802e707b snapshots.
  Each task's first step is a `grep`/read to locate the current target before editing.
- **No behavior regression on control-plane:** any change to the 6 controls MUST pass the Phase-0
  golden oracle diff in addition to unit tests.
- **Reversibility:** new code lives in `magi_agent/packs/` and `magi_agent/firstparty/packs/`
  (additive). Existing-file edits keep the old path working (dual-load) until Phase 6 flips the
  default. Each phase is independently revertible by branch/commit.
- **Pydantic models:** frozen, `extra="forbid"`, `populate_by_name=True`, alias camelCase to match
  existing `authoring/` conventions (see `authoring/compiler.py` `_MODEL_CONFIG`).

---

## 7. Status / per-phase doc index

- [ ] `01-phase0-golden-oracle.md` — **AUTHORED (this pack).** Load-bearing verification harness.
- [ ] `02-phase1-manifest-discovery-loader.md` — to author (greenfield-heavy; can write complete code).
- [ ] `03-phase2-typed-context-abi-registries.md` — to author (greenfield-heavy).
- [ ] `04-phase3-validator-vertical-slice.md` — to author (reads `cli/engine.py` enforce path).
- [ ] `05-phase4-easy-provides-types.md` — to author (6 parallel units).
- [ ] `06-phase5-control-plane-migration.md` — to author (reads `adk_bridge/control_plane.py` per seam).
- [ ] `07-phase6-firstparty-migration-and-acceptance.md` — to author (microkernel shrink + §1 tests).

**Authoring note:** phases 02–07 each require grounding in current code (reading the exact target
files), so they are best authored in parallel by code-reading agents via `/workflows` against this
blueprint — which is also the first real exercise of the parallelization the implementation will
use. Phase 0 + this blueprint are complete and executable now.
