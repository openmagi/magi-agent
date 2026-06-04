# Research Harness — Live-Engine Upgrade: Design & PR Plan

> **Goal:** give Magi's research harness a *real engine*. Today the harness has a
> complete contract/evidence/safety scaffold but **zero live network egress** —
> every provider is a sealed local fake. We wire live search / fetch / repo
> research as **first-class runtime harness + recipe + skill** surfaces, behind
> the *existing* default-OFF seals, feeding the *existing* evidence graph.
> **Method:** layer-by-layer teardown of OpenCode's research harness
> (`../cc-workspace/opencode`, see `docs/architecture/opencode-architecture.md`
> in local architecture notes) mapped onto Magi's existing Python/ADK runtime.
> **Companion analysis:** OpenCode 7-layer dissection (web search dual-MCP,
> webfetch CF-bypass, repo_clone, `reference.ts` @alias auto-clone, task
> delegation, permission isolation, 2-stage truncation).
> Date: 2026-06-03 (rev. 2026-06-04). Status: **PR0/PR1/PR3a/PR2 delivered &
> reviewed (open PRs #94/#95/#99/#96); PR3b+ remaining.** See §4 for status and
> the 2026-06-04 "minimal convergence" revision (standalone orchestrator removed).

---

## 1. The one architectural idea

OpenCode and Magi are **inverted**:

- **OpenCode** = a working engine with a thin safety skin. It really calls Exa /
  Parallel over MCP, really fetches HTML and turns it into Markdown (with a
  Cloudflare `cf-mitigated` honest-UA retry), really `git clone --depth 100`s
  external repos into a managed cache. Its evidence story is *nothing* — no
  claim graph, no verification, citations are prompt instructions.
- **Magi** = a complete contract/evidence/safety scaffold with **no engine**.
  `SearchProviderPort` / `FetchProviderPort` / `ReaderProviderPort` /
  `BrowserFallbackProviderPort` (`web_acquisition/live_provider_pack.py:62-93`)
  exist; the router, the SSRF firewall, the claim graph, the append-only
  evidence ledger, the 7-stage verifier bus, the derived-not-declared verdicts,
  the cite-or-omit final gate — all built and *sealed* with `Literal[False]`
  attachment flags. The only dependency that can touch a socket is `httpx`.

So the upgrade is **not** "port OpenCode's evidence patterns" (Magi already far
exceeds them). It is: **keep every Magi seal and gate, and feed them a real
engine** — borrowing OpenCode's *concrete egress mechanics* (dual-provider
routing, CF retry, shallow-clone blueprint) as the implementation behind Magi's
ports.

**Design rule #1 — extend the existing seam, do not add a parallel orchestrator.**
*(Revised 2026-06-04 — "minimal convergence".)* An earlier draft exposed a new
standalone `LiveResearchHarness` (under `harness/`). That was eliminated: live
research is driven through the **existing** tool seam
`web_acquisition/research_tools.py::LocalWebResearchToolBoundary.execute_tool`
(the already-present `WebSearch`/`WebFetch` → provider boundary dispatcher),
behind a default-OFF env gate. The *egress mechanism* (in-runtime SDK vs.
user-configured proxy call) is an injected provider detail behind the port — never the public
surface. **Why not the deep-research recipe?** Investigation found the existing
`build_deep_research_workflow` / executor stack does **not** execute web
acquisition at all today — `_tasks_from_contract` is a deliberate stub and
children get only read-only source tools. Un-stubbing that executor to drive live
acquisition end-to-end is a larger, separate change (see PR-recipe below);
the minimal convergence reuses the existing tool-boundary seam instead.

**Design rule #2 — seals flip to gates, never to "on".** Every `Literal[False]`
becomes an env-gated runtime flag following the established
`research_first_canary` pattern: default-OFF env enable + independent kill-switch
(`research/research_first_canary.py:23-27`). Live network access stays
*unrepresentable* until a gate opens.

**Design rule #3 — real data flows into the existing evidence spine, unchanged.**
Every live result is projected into the source ledger → claim graph →
`final_projection_gate`. No live answer reaches the user without surviving
cite-or-omit. We add an engine; we do not weaken the gate.

```
   research child       ┌──────────────────────────────────────────────────────┐
   calls WebSearch/ ───▶│ EXISTING seam: LocalWebResearchToolBoundary.execute_  │
   WebFetch tool        │ tool  — env gate selects live vs legacy-fixture path  │
                        └───────────────┬───────────────────┬──────────────────┘
                          gate OFF / no live pack            gate ON + live pack+provider
                                 │                                   │
                          legacy LocalWebAcquisitionRuntime   LiveWebAcquisitionProviderPack.run
                          (fixture, UNCHANGED — zero drift)    (NEW, gated; SSRF firewall in front)
                                                                     │
                                            ProviderPort dispatch (NEW live impls — PR3b)
                                                                     ▼
                                            existing evidence spine: source ledger →
                                            claim_graph → verifier_bus → final gate (UNCHANGED)
```

---

## 2. Current state (condensed dissection)

### 2.1 Magi — what exists vs. what is sealed

| Capability | State | Anchor |
|---|---|---|
| Provider ports (search/fetch/reader/browser) | **defined, sealed** | `web_acquisition/live_provider_pack.py:62-93` |
| OpenCode-shaped provider router | **routes to FAKE only** | `web_acquisition/opencode_provider_router.py` (`OPENCODE_WEB_FAKE_PROVIDER_ID`) |
| Acquisition plan (5-phase, quality+fallback gate) | **plan object only, no executor** | `web_acquisition/acquisition_plan.py` (`build_web_acquisition_plan`, `_PHASES`) |
| SSRF / secret URL firewall | **strong, live** | `web_acquisition/policy.py` (`url_policy_error`, `_SENSITIVE_URL_RE`) |
| WebFetch (real HTTP, HTML→MD, CF retry) | **ABSENT** | — |
| Repo clone / overview | **tool names + Fixture only; `repo_url` rejected** | `web_acquisition/repo_research_tools.py` (`repo_url_not_allowed_fixture`) |
| `@alias` reference | **inverted** — workspace-only managed refs, permission-*adding* | `web_acquisition/reference_research_tools.py` |
| Claim graph / evidence graph / derived verdicts | **mature** | `research/claim_graph.py`, `research/evidence_graph.py` |
| Cite-or-omit final gate | **mature** | `research/final_projection_gate.py` |
| Append-only evidence ledger | **mature** | `evidence/ledger.py` |
| 7-stage verifier bus (det → critic) | **mature** | `harness/verifier_bus.py` |
| Research routing (regex, no LLM) | **complete** | `harness/research_routing.py` |
| Subagent fan-out (depth `Literal[1]`, envelope refs) | **complete, gated** | `recipes/research_child_runner.py`, `runtime/child_runner_boundary.py` |
| Parallel/map-reduce (semaphore ≤16) | **complete** | `harness/workflow_executor.py` |
| Deep-research workflow (plan→fan-out→cross-review→cited synth) | **complete** | `recipes/workflow_recipe.py` (`build_deep_research_workflow`) |
| Output budget (dual preview, content-addressed spill) | **complete; no delegation hint** | `tools/output_budget.py` (`_DEFAULT_LLM_PREVIEW_CHARS=4000`) |
| Read-ledger digest mutation gate | **complete** | `tools/read_ledger.py` |
| Deferred tool catalog (ToolSearch) | **complete** | `tools/tool_search.py`, `tools/deferred.py` |
| Goal loop (autonomous iterate/terminate) | **unattached scaffold** | `harness/goal_loop.py` (traffic-free) |
| OpenCode delta-parity matrix (14 rows) | **tracked** | `shadow/opencode_delta_contract.py` |
| Live provider SDK deps | **none** (only `httpx`) | `pyproject.toml` |

### 2.2 Where each repo wins

- **OpenCode wins** on having a live engine: dual-provider search, real fetch
  with CF-bypass, real shallow clone, `@alias` auto-clone, model-driven
  plan-mode orchestration.
- **Magi wins (often by a lot)** on: structured subagent returns (envelope refs,
  `finalOutputSchema`), parallel map-reduce, multi-layer permission +
  read-ledger gate, content-addressed out-of-band spill, and the entire
  evidence/claim/verification spine — none of which OpenCode has.
- **Only place OpenCode is cleanly better on a Magi-strong axis:** the
  *agent-aware delegation hint* on truncated output ("don't read this yourself —
  delegate to explore"). Magi spills better but never advises delegation.

### 2.3 Strategic notes (constraints this plan respects)

- Research-harness code is **settled, not in-flight** (last 40 commits are the
  unrelated `learning/` layer). Low collision risk.
- No research design doc exists yet — this is the first.
- Some deployments may already run reader / fetch / crawl workers. A live
  provider can therefore be either an in-runtime SDK *or* a thin client to a
  user-configured proxy endpoint — **both satisfy the same port**, and this plan
  keeps that choice behind the port (see PR2).

---

## 3. Non-goals

1. **Do not import OpenCode's evidence approach.** Magi's claim graph + derived
   verdicts + cite-or-omit + append-only ledger already exceed it.
2. **Do not remove the seals.** Flip `Literal[False]` to env-gated flags; never
   default-on.
3. **Do not weaken `final_projection_gate` / `verifier_bus`.** Live data must
   pass the same gates fixtures pass today.
4. **Trimming Magi's over-engineering** (object-identity issuance registries,
   ~8× duplicated secret regex, vestigial `trust_tier`) is **out of scope** for
   the engine work — tracked separately as optional PR9.

---

## 4. PR breakdown (all end-to-end shippable)

Each PR is independently mergeable, default-OFF where it adds capability, ships
with tests, and updates the delta-contract matrix where relevant. Effort is
rough (S/M/L). "Network" = whether the PR can actually touch a socket.

> **Status (2026-06-04).** ✅ **DELIVERED & reviewed** (spec + quality +
> adversarial), open PRs awaiting merge: PR0 (#94), PR1 (#95), **PR3a live pack
> (#99)**, **PR2 convergence (#96)**. ⏳ **REMAINING:** PR3b (real clients, key
> env), PR-repo, PR-@alias, PR-recipe (full convergence), PR-loop, PR-hygiene.
> Note the structural change from the original draft: the standalone
> `LiveResearchHarness` was **eliminated** in favor of driving the live pack
> through the existing tool seam (Design rule #1, revised).

### PR0 — This design doc *(no code, Network: no, S)*
- **Scope:** this file. Establishes the architecture, the seal→gate pattern, the
  PR sequence, acceptance contract.
- **Acceptance:** reviewed + merged; subsequent PRs reference it.

### PR1 — Context-economy parity *(P0, Network: no, S)*
Two pure, seal-free wins that need no provider.
- **1a. Agent-aware delegation hint.** Add an optional delegation hint to
  `BudgetedToolResult`'s public projection when output is spilled out-of-band
  (`storedOutOfBand=True`) *and* a research-child/task capability is available —
  mirroring OpenCode `truncate.ts:130` ("full output saved to `<ref>`; delegate
  to a sub-agent to Grep/Read it, do not read it yourself to save context").
  Magi already spills content-addressed (`resultRef=result:<digest>`); we only
  add the advisory string + a capability check.
  - Files: `tools/output_budget.py` (projection), a capability flag from the
    dispatch context.
- **1b. Search query freshness enrichment.** `normalize_query`
  (`web_acquisition/policy.py:85`) currently only collapses/redacts/truncates.
  Add opt-in current-year/recency enrichment (OpenCode `websearch.txt:13`
  "MUST use this year"). Deterministic, no network.
  - Files: `web_acquisition/policy.py`.
- **Tests:** projection emits hint only when spilled + capability present; query
  enrichment is idempotent and respects the 512-char cap.
- **Acceptance:** no behavior change when capability absent; hint + enrichment
  unit-covered. **Depends:** PR0.

### PR3a — Gated live execution boundary *(P1, Network: no, M)* — ✅ DELIVERED (#99, base `main`)
The foundation: a parallel, default-OFF live boundary added to the existing
`web_acquisition/live_provider_pack.py`, **without touching the sealed fake pack
or its `Literal[False]` seals**.
- **Scope (as built):**
  - `LiveWebAcquisitionProviderPack` — new `openmagi_live_provider` trust marker;
    gates on `enabled` + `live_network_enabled` (a real `bool`; default-False is
    the seal) + a **mandatory non-empty `provider_allowlist`** (empty ⇒ deny,
    fail-safe) + trust marker. **SSRF firewall (`url_policy_error`) runs before
    every provider call** (all ops incl. `reader`); records built via the reused
    `_records_from_output` with the same redaction the fake path gets.
  - `LiveWebAcquisitionPackConfig` (new frozen config) + `StubLiveProvider`
    (canned, no network). Promotes `OPERATION_TO_PROVIDER_NAME` to public.
  - **No `Literal[False]` seal flipped**; the gate is the new `bool`
    `live_network_enabled` defaulting false. `shadow/opencode_delta_contract.py`
    intentionally **not** touched (delta-row registration deferred — its rows are
    golden-test exact-match validated).
- **Carried to PR3b (code comment):** `url_policy_error` is a literal denylist
  with no DNS resolution — resolve+re-check the IP before real egress.
- **Depends:** PR0.

### PR2 — Minimal convergence: drive the live pack through the existing tool seam *(P1, Network: no, M)* — ✅ DELIVERED (#96, base #99)
Eliminates the standalone orchestrator (Design rule #1). Drives PR3a's live pack
through the **existing** `LocalWebResearchToolBoundary.execute_tool` seam.
- **Scope (as built):**
  - `web_acquisition/research_tools.py`: optional `live_pack`/`live_provider`/
    `env` on the boundary. When the env gate
    (`CORE_AGENT_PYTHON_LIVE_WEB_ACQUISITION_ENABLED` + kill-switch) is active
    **and** a live pack + provider are injected → map `WebSearch→search` /
    `WebFetch→fetch`, build a `WebAcquisitionProviderRequest`, run
    `LiveWebAcquisitionProviderPack.run`, and project the result (+ source-ledger
    parity) to `ToolResult`. Otherwise → the **unchanged** legacy
    `runtime.run(...)` fixture path (zero drift).
  - Narrowed the existing import-boundary test to allow
    `research_tools`→`live_provider_pack` (now required); every real
    network/toolhost prefix retained; live-pack import verified to load zero
    network modules.
  - Sealed `fixture_only`/`live_authority_allowed` class attrs left unchanged
    (only the canned `StubLiveProvider` ships here; real network is PR3b).
- **Carried to PR3b (code comments):** provider-controlled **metadata values**
  (bare hostnames) aren't scrubbed by `safe_metadata` — add host/URL-aware
  redaction or a key allowlist; wrap the live `run()` for exceptions.
- **Tests:** gate OFF / no live pack ⇒ legacy path, live pack never called
  (spy); gate ON + `StubLiveProvider` ⇒ pack driven, ToolResult ok with records;
  SSRF-blocked URL → blocked before provider; no raw url/secret leak; same-turn
  WebSearch≠WebFetch requestIds. **Depends:** PR3a.

### PR3b — Real provider clients + egress hardening *(P1, Network: yes, L)* — ⏳ REMAINING (key env)
The first real egress, behind the PR3a gate. **Live-verify in a keyed
environment; CI uses recorded/mocked transport only.**
- **Scope:**
  - Concrete `SearchProviderPort` + `FetchProviderPort` implementations
    (`openmagi_live_provider = True`). Recommended egress: **httpx-direct**,
    mirroring OpenCode's MCP-over-HTTP (Exa/Parallel), minimal deps; the port
    keeps a proxy-client variant possible for deployments that need one. Borrow
    OpenCode's **dual-provider session-deterministic routing**
    (`checksum(session)%2`, env override) and **Cloudflare `cf-mitigated`
    honest-UA retry** + HTML→Markdown for fetch.
  - **Egress hardening (FIX-FIRST before any real provider ships):**
    (1) **DNS-rebinding guard** — resolve the host and re-check the resolved IP
    against the private/metadata classification *before* the socket opens (do NOT
    put DNS I/O into the pure `url_policy_error`; add an egress-time guard);
    (2) **provider metadata redaction** — host/URL-aware scrub or a key allowlist
    on provider-controlled metadata values; (3) **exception wrapping** — wrap the
    live `run()`/client so network errors return `blocked`/`repair_required`
    rather than bubbling; (4) revisit `LocalWebResearchToolBoundary` sealed attrs
    once a real provider can be injected.
  - Inject the live provider into the tool seam (PR2 wiring already accepts it).
- **Tests:** recorded-fixture HTTP (no live calls in CI); deterministic routing;
  DNS-rebinding rejection (mock `getaddrinfo`); CF-retry; SSRF precedence;
  evidence projection through ledger→claim graph→`final_projection_gate`.
- **Acceptance:** gate ON + keys → cited, gate-passing live results. **Depends:**
  PR3a, PR2.

### PR-repo — Live repo research *(P2, Network: yes, L)*
- **Scope:** real `repo_clone` / `repo_overview` behind the seal. Implement
  OpenCode's blueprint: `git clone --depth 100` (shallow), per-path flock,
  stale-cache wipe on origin mismatch, ecosystem/package-manager/entrypoint
  detection, depth-limited structure tree (200-line cap), path-traversal defense
  + argv hardening (`--` separators, `-c` flags). Flip
  `repo_url_not_allowed_fixture` to a **gated** allow; Fixture variants stay for
  tests. Keep Magi's existing evidence projection + path-safety (already strong).
- **Files:** `web_acquisition/repo_research_tools.py`, a new git-cache module,
  `research_agents.py` (allow live grants under gate), delta contract.
- **Tests:** clone into a managed cache (fixture remote in CI), overview
  detection, refresh path, traversal/argv-injection rejection.
- **Acceptance:** scout-style agent clones + inspects an external repo under
  gate, with open-receipt evidence. **Depends:** PR2.

### PR-@alias — reference materialization *(P2, Network: yes, M)*
- **Scope:** adopt OpenCode's *trigger UX* — a configured `@alias` mention
  resolves a named reference, materializes it (clone/refresh via PR5's cache),
  and routes the agent toward the read-only research child — **but keep Magi's
  philosophy: emit evidence receipts + scope checks, do NOT bypass permissions**
  (OpenCode's `reference.contains()` bypass is inverted here by design). Gate +
  kill-switch.
- **Files:** `web_acquisition/reference_research_tools.py`, prompt-injection
  point for `@alias`, config schema for named references.
- **Tests:** alias → materialize → receipt; unknown alias rejected; scope/stale
  checks preserved.
- **Acceptance:** `@alias` materializes a managed reference with receipts under
  gate. **Depends:** PR5.

### PR-recipe — Full convergence: deep-research recipe drives live acquisition *(P2, Network: gated, L)*
*(This is the deferred "full convergence" — the larger executor change the
minimal PR2 intentionally avoided.)*
- **Scope:** make `build_deep_research_workflow` actually drive live web
  acquisition end-to-end. Today its executor doesn't: `_tasks_from_contract`
  (`harness/workflow_executor.py`) is a deliberate stub and the spawned children
  get only read-only source tools (`research_child_runner.py`). Un-stub task
  decomposition, grant `WebSearch`/`WebFetch` to the `explore`/`web_current`
  children, and route those tool calls through the PR2-extended
  `LocalWebResearchToolBoundary` (which already drives the live pack). Then a
  single saved recipe / slash-command / skill (`SavedWorkflowRegistry`) runs plan
  → fan-out search/fetch → cross-review → cited synthesis on *live* data.
- **Files:** `harness/workflow_executor.py` (un-stub `_tasks_from_contract`),
  `recipes/research_child_runner.py` (relax child tool scope under gate),
  `research_agents.py` / recipe wiring, skill manifest.
- **Tests:** recipe materializes under gate; children drive the tool seam;
  cross-review filters claims; cited synthesis only emits surviving claims.
- **Acceptance:** one gated invocation runs the full live deep-research loop
  end-to-end, gate-passing. **Depends:** PR2, PR3b. **Note:** changes
  deliberately-stubbed executor core — design-review gated.

### PR-loop — Iterative research loop *(P3, Network: gated, L)*
- **Scope:** attach the unconnected `goal_loop` scaffold
  (`harness/goal_loop.py`, traffic-free today) — or add a model-driven planner
  over the regex `research_routing` — to drive **iterative** research with
  explicit termination (coverage of acceptance criteria, budget, or
  `final_projection_gate` pass). Respect `DEFAULT_GOAL_LOOP_MAX_SPAWN_DEPTH=2`
  and `maxRetryBudget`. This is the only PR that meaningfully changes
  orchestration shape; design-review gated.
- **Files:** `harness/goal_loop.py` (attach), `harness/research_routing.py`
  (optional model planner), executor wiring.
- **Tests:** loop terminates on criteria satisfaction; terminates on budget;
  no unbounded recursion.
- **Acceptance:** a multi-hop question iterates search→fetch→re-plan until
  acceptance criteria are satisfied, then stops. **Depends:** PR-recipe.

### PR-hygiene — *(optional, orthogonal)* harness hygiene *(P3, Network: no, M)*
- **Scope:** de-duplicate the ~8× copied secret/path regex into one shared
  module (drift risk); remove vestigial `trust_tier` gating-that-does-nothing;
  optionally collapse the most ceremonial `Literal[False]` posture boilerplate.
  **Non-blocking; can be dropped.** Pure refactor, no capability change.
- **Acceptance:** no behavior change; single source of truth for the matchers.
  **Depends:** none (can land anytime).

---

## 5. Sequencing & dependency graph

```
main ─┬─ PR0 #94 (docs)
      ├─ PR1 #95 (context-economy, independent)
      └─ PR3a #99 (live pack) ── PR2 #96 (convergence) ── PR3b ──┬─ PR-recipe ── PR-loop
                                                                  └─ PR-repo ── PR-@alias
PR-hygiene (anytime, optional)
```

- **Delivered & awaiting merge:** PR0 (#94), PR1 (#95), PR3a (#99), PR2 (#96).
- **Merge order:** PR0/PR1/PR3a (all base `main`, any order) → PR2 (after PR3a).
- **Critical path to a usable live engine:** PR3a → PR2 → **PR3b** (key env) →
  PR-recipe.
- **PR-repo / PR-@alias** (external code) and **PR-loop** (iterative) are
  capability extensions, off the minimum critical path.

## 6. Cross-cutting acceptance contract (every capability PR)

1. **Default-OFF:** new env enable + independent kill-switch; sealed default
   stays `false`. No config change flips behavior.
2. **Evidence-preserving:** live results project into source ledger → claim graph
   → `final_projection_gate`; no answer bypasses cite-or-omit.
3. **Firewall-in-front:** every outbound URL passes `policy.url_policy_error`
   before egress.
4. **CI is offline:** recorded fixtures only; no live network in tests.
5. **Delta-contract updated:** relevant `REQUIRED_OPENCODE_DELTA_ROWS` marked
   covered (or a new row added) with the pinned commit.
6. **Zero drift when OFF:** fixture/test suite identical with gate disabled.

## 7. Risks

- **Seal-flip blast radius.** Flipping a `Literal[False]` touches frozen Pydantic
  validators. Mitigation: gate-scoped flags, never the sealed default; PR2
  establishes the single seam so later PRs don't each re-derive it.
- **Egress-location ambiguity (SDK vs proxy-client).** Deferred behind the port
  in PR2; PR3/PR4 can ship either backend without reopening the harness.
- **Evidence-gate false negatives on messy live HTML.** Real pages produce noisy
  spans; the cite-or-omit gate may over-omit. Mitigation: tune
  reader/extraction in PR4; treat omission as safe-default, log for review.
- **Over-omission vs over-trust.** Keep `final_projection_gate` strict; do not
  relax it to make live answers look complete.

## 8. Decisions & open questions

**Resolved (2026-06-04):**
1. **Live backend = in-runtime, httpx-direct** (Design rule #1 / option A) — OSS
   users run their own keys; no dependence on a managed proxy. The port still
   allows a proxy-client provider for deployments that need one.
2. **Convergence = minimal** — drive the live pack through the existing tool
   seam; do NOT add a standalone orchestrator. Full executor convergence
   (un-stub `_tasks_from_contract`) is the separate **PR-recipe**.

**Open (resolve during PR3b):**
1. Fetch size cap — adopt OpenCode's 5MB or keep Magi's 32KB default
   (configurable either way).
2. Should `BrowserFallbackProviderPort` ship with PR3b or split out?
3. DNS-rebinding guard placement — a dedicated egress-time resolver step (keeps
   `url_policy_error` pure) vs. an egress allowlist pin.
