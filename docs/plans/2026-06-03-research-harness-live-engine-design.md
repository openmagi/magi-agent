# Research Harness — Live-Engine Upgrade: Design & PR Plan

> **Goal:** give Magi's research harness a *real engine*. Today the harness has a
> complete contract/evidence/safety scaffold but **zero live network egress** —
> every provider is a sealed local fake. We wire live search / fetch / repo
> research as **first-class runtime harness + recipe + skill** surfaces, behind
> the *existing* default-OFF seals, feeding the *existing* evidence graph.
> **Method:** layer-by-layer teardown of OpenCode's research harness
> (`../cc-workspace/opencode`, see `docs/architecture/opencode-architecture.md`
> in the hosted monorepo) mapped onto Magi's existing Python/ADK runtime.
> **Companion analysis:** OpenCode 7-layer dissection (web search dual-MCP,
> webfetch CF-bypass, repo_clone, `reference.ts` @alias auto-clone, task
> delegation, permission isolation, 2-stage truncation).
> Date: 2026-06-03. Status: **design (no runtime code yet)**.

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

**Design rule #1 — the engine is a first-class harness, not buried egress.**
Live research is exposed as a `LiveResearchHarness` (under `harness/`), surfaced
as a saved recipe (`SavedWorkflowRegistry`) and a skill. The *egress mechanism*
(in-runtime SDK vs. hosted api-proxy call) is an injected provider detail behind
the port — never the public surface.

**Design rule #2 — seals flip to gates, never to "on".** Every `Literal[False]`
becomes an env-gated runtime flag following the established
`research_first_canary` pattern: default-OFF env enable + independent kill-switch
(`research/research_first_canary.py:23-27`). Production network stays
*unrepresentable* until a gate opens.

**Design rule #3 — real data flows into the existing evidence spine, unchanged.**
Every live result is projected into the source ledger → claim graph →
`final_projection_gate`. No live answer reaches the user without surviving
cite-or-omit. We add an engine; we do not weaken the gate.

```
                       ┌───────────────────────────────────────────────────────┐
                       │  LiveResearchHarness (NEW, first-class, default-OFF)   │
   research turn ─────▶│  plan ─▶ search ─▶ fetch/read ─▶ synthesize ─▶ verify  │
                       └────┬──────────┬───────────┬──────────────┬────────────┘
                            │          │           │              │
                   acquisition_plan  ProviderPort dispatch   existing evidence spine
                   (5 phases, exists)  (NEW live impls)       claim_graph / ledger /
                            │          │                      verifier_bus / final gate
                            ▼          ▼                              │ (UNCHANGED)
                   web_search ─ fetch ─ reader_extract ─ jsonld ─ browser_fallback
                   SearchPort  FetchPort  ReaderPort           BrowserFallbackPort
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
- The hosted Clawy infra already runs live jina-reader / insane-fetch /
  firecrawl workers. So a live provider can be either an in-runtime SDK *or* a
  thin client to those hosted endpoints — **both satisfy the same port**, and
  this plan keeps that choice behind the port (see PR2).

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

### PR2 — Live research harness spine + provider seam *(P1, Network: no, M)*
The first-class engine surface, **still returning fixtures** until a real
provider is injected. Establishes the seam every later PR plugs into.
- **Scope:**
  - New `harness/live_research_harness.py` — a `LiveResearchHarness` that drives
    `acquisition_plan`'s 5 phases against injected providers behind the existing
    4 ports, and projects every result into the existing source ledger / claim
    graph. Default-OFF.
  - Gate: new env enable + kill-switch pair modeled on
    `research_first_canary.py:23-27`. With gate OFF → harness returns the
    current fixture path unchanged (zero behavior drift).
  - Provider injection contract: the harness accepts a provider satisfying
    `SearchProviderPort` etc.; **the egress mechanism (SDK vs hosted-proxy
    client) is the provider's concern, not the harness's.** Ship a
    `NullLiveProvider` (still fixture) so the seam is testable end-to-end.
  - Flip the relevant `Literal[False]` on the *gated path only* to a runtime
    flag; sealed default remains false.
  - Add delta-contract row: `live_research_harness_gate`.
- **Files:** `harness/live_research_harness.py` (new),
  `web_acquisition/live_provider_pack.py` (gated flag),
  `shadow/opencode_delta_contract.py` (+1 row).
- **Tests:** gate OFF ⇒ fixture parity; gate ON + injected fixture provider ⇒
  results flow through ledger→claim graph→`final_projection_gate` and pass.
- **Acceptance:** end-to-end run with a fixture provider produces a cited answer
  that survives the final gate; default config unchanged. **Depends:** PR0.

### PR3 — Live SearchProvider *(P1, Network: yes, L)*
The first real egress, behind PR2's gate.
- **Scope:**
  - Implement a concrete `SearchProviderPort`. Borrow OpenCode's
    **dual-provider, session-deterministic routing** in
    `opencode_provider_router.py`: two backends, `checksum(session) % 2`
    selection, env override (OpenCode `websearch.ts:30`). Backends are pluggable
    — at least one of {Exa/Parallel SDK} *or* {hosted api-proxy client}; the
    router does not care which.
  - Every result → `WebAcquisitionSourceRecord` with `proofType="observed"` →
    source ledger → claim support refs. Reuse the SSRF firewall on any URL the
    result exposes.
  - Add provider SDK(s) to `pyproject.toml` only if the SDK path is chosen for a
    backend; the hosted-proxy backend needs no new dep (uses `httpx`).
  - Delta-contract: mark the search row covered.
- **Files:** new provider module under `web_acquisition/`,
  `opencode_provider_router.py` (real routing),
  `pyproject.toml` (conditional), delta contract.
- **Tests:** recorded-fixture HTTP (no live calls in CI); deterministic routing;
  evidence projection; firewall rejects SSRF/secret URLs in results.
- **Acceptance:** with gate ON + keys present, a real query returns cited,
  gate-passing results; CI uses recorded fixtures only. **Depends:** PR2.

### PR4 — Live FetchProvider + ReaderProvider *(P1, Network: yes, L)*
- **Scope:**
  - Concrete `FetchProviderPort`: real HTTP (`httpx`), HTML→Markdown, format
    negotiation, **Cloudflare `cf-mitigated: challenge` honest-UA retry**
    (OpenCode `webfetch.ts:79`), size cap (start at OpenCode's 5MB or Magi's
    `max_content_bytes=32_768` config — make it configurable), timeout.
  - Concrete `ReaderProviderPort` for the `reader_extract` phase (readability /
    main-content extraction). Optionally a hosted insane-fetch (curl_cffi
    WAF-bypass) backend for hard targets.
  - **Mandatory:** route every fetch URL through `policy.url_policy_error`
    *before* the socket opens (Magi's SSRF firewall is stronger than OpenCode's
    — keep it in front). Results → ledger with `proofType="opened"`.
  - `BrowserFallbackProviderPort` is a stretch sub-item (snapshot fallback);
    can defer to PR4b if it grows.
- **Files:** new fetch/reader provider modules, wire into
  `acquisition_plan` phases via PR2 harness.
- **Tests:** recorded-fixture fetch; CF-retry path; oversize rejection; SSRF
  firewall precedence; HTML→MD fidelity.
- **Acceptance:** gated live fetch produces `opened` source proofs that satisfy
  `require_opened_source_proof`. **Depends:** PR2 (PR3 recommended for end-to-end
  search→fetch).

### PR5 — Live repo research *(P2, Network: yes, L)*
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

### PR6 — `@alias` reference materialization *(P2, Network: yes, M)*
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

### PR7 — First-class research recipe + skill surface *(P2, Network: gated, M)*
- **Scope:** expose the engine as a **saved recipe + slash-command + skill**, per
  Design rule #1. Wire `LiveResearchHarness` into the existing
  `build_deep_research_workflow` (`recipes/workflow_recipe.py`) so a single
  user-facing surface runs plan → fan-out search/fetch → cross-review → cited
  synthesis using *live* providers. Register in `SavedWorkflowRegistry`. Add a
  skill manifest so the capability is discoverable/loadable.
- **Files:** `recipes/` (research recipe), `SavedWorkflowRegistry` registration,
  skill manifest (catalog), CLI/slash wiring.
- **Tests:** recipe materializes with live harness under gate; cross-review still
  filters claims; cited synthesis only emits surviving claims.
- **Acceptance:** one invocation (skill or saved command) runs the full live
  deep-research loop end-to-end, gate-passing. **Depends:** PR2–PR4 (PR5/PR6
  optional).

### PR8 — Iterative research loop *(P3, Network: gated, L)*
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
  acceptance criteria are satisfied, then stops. **Depends:** PR7.

### PR9 — *(optional, orthogonal)* harness hygiene *(P3, Network: no, M)*
- **Scope:** de-duplicate the ~8× copied secret/path regex into one shared
  module (drift risk); remove vestigial `trust_tier` gating-that-does-nothing;
  optionally collapse the most ceremonial `Literal[False]` posture boilerplate.
  **Non-blocking; can be dropped.** Pure refactor, no capability change.
- **Acceptance:** no behavior change; single source of truth for the matchers.
  **Depends:** none (can land anytime).

---

## 5. Sequencing & dependency graph

```
PR0 ─┬─ PR1 (P0, ship immediately)
     └─ PR2 ─┬─ PR3 ──┐
             ├─ PR4 ──┼─ PR7 ── PR8
             └─ PR5 ── PR6 ─────┘
PR9 (anytime, optional)
```

- **Critical path to a usable live engine:** PR0 → PR2 → PR3 → PR4 → PR7.
- **PR1** ships in parallel from day one (no dependencies beyond PR0).
- **PR5/PR6** (repo + @alias) and **PR8** (iterative loop) are capability
  extensions, not on the minimum critical path.

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
- **Egress-location ambiguity (SDK vs hosted-proxy).** Deferred behind the port
  in PR2; PR3/PR4 can ship either backend without reopening the harness.
- **Evidence-gate false negatives on messy live HTML.** Real pages produce noisy
  spans; the cite-or-omit gate may over-omit. Mitigation: tune
  reader/extraction in PR4; treat omission as safe-default, log for review.
- **Over-omission vs over-trust.** Keep `final_projection_gate` strict; do not
  relax it to make live answers look complete.

## 8. Open questions (resolve during PR2)

1. Primary live search backend for the first cut — Exa/Parallel SDK, or a hosted
   api-proxy client to the existing jina/insane-fetch/firecrawl workers?
2. Fetch size cap — adopt OpenCode's 5MB or keep Magi's 32KB default
   (configurable either way).
3. Should `BrowserFallbackProviderPort` ship in PR4 or split to PR4b?
