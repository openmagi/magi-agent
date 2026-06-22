# Changelog

Open Magi Agent is in early beta. For the authoritative list of tagged builds and
their assets, see [GitHub Releases](https://github.com/openmagi/magi-agent/releases).
This page summarizes notable user-facing changes between releases.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/).
Versions follow the tags published on GitHub Releases.

## Unreleased

### Added

### Changed

### Fixed

## 0.1.69

### Added
- Goal-loop trio Layer 2 PR-C (#841): clean-break goal-loop judge call now
  closes the goal-mode planner loop end-to-end alongside PR-A toggle (#835
  in 0.1.67) and PR-B ContextVar wire (#839 in 0.1.68).
- Customize hub PR-D1 unified NL → rule compiler backend (#844, default-OFF
  via `MAGI_CUSTOMIZE_NL_RULE_COMPILER_ENABLED`): `customize/rule_compiler.py`
  routes a natural-language draft into one of six `routedKind`s
  (deterministic_ref / tool_perm / llm_criterion / shacl_constraint /
  seam_spec / custom_check) through a three-gate compile / review / commit
  pipeline that dispatches to the matching validator. PR-A hardening reused
  (nonce, precheck, distinct factory). New POST
  `/v1/app/customize/rules/compile` endpoint.
- Local serve wires `emit_agent_event` so subagent activity (`child_started`,
  `child_progress`, `child_completed`) shows up in the Work pane (#845).
- Engine now surfaces upstream error class + sanitized traceback as an
  `engine_error_detail` status event, and each orphan tool carries
  `errorDetail` with `errorClass` (#847). The next repro shows the real
  trigger in one shot.
- Chat transcript persists the activity summary line ("Ran N actions ▸",
  collapsed by default) instead of dropping it on finalize (#846, paired
  with #838's `_v:4` envelope from 0.1.68). Live verbose progress logs
  (`id` starting with `llm:`) stay in the Work pane only and are filtered
  out of the finalized summary count.

### Fixed
- Add-rule picker now renders in-place above the rules table instead of as
  an off-screen overlay modal (#842). State machine is
  `idle → picking → authoring → idle`; "← Pick different" affordance gets
  the operator back to the picker. Legacy `AddRuleModal` wrapper kept for
  back-compat.
- Orphan `tool_end` events no longer falsely blame user cancellation (#843).
  When the parent run has no matching `tool_start`, the event surface
  reports a real engine error class instead of attributing the outcome to
  the operator.

## 0.1.68

### Added
- `.magi/{BOOTSTRAP,IDENTITY,USER,LEARNING,AGENTS}.md` self-identity slots
  are wired into the prompt assembly and the legacy SOUL prompt path is
  decoupled (#836). Fresh installs without `.magi/*` files stay
  byte-identical; identity / prompt suites are at 248 green.
- Tool activities now persist in the chat-core history envelope behind a
  default-OFF `_v:4` schema bump (#838); the chat surfaces can recover the
  full tool-event timeline on reload once it's flipped on.
- Layer 2 PR-B (#839): `goalMode` is now wired through to a
  `GoalLoopPolicy` ContextVar in transport, so the runtime sees a real
  per-turn goal-mode signal instead of a hint.

### Changed
- I-4 chat-route consolidation (#833 primary + #834 follow-up): six
  separate chat-route env reads collapse into one decision, plus the
  workspace / control-plane truthy reads move onto the registry. I-1
  batch 4 lands the tri-state document-authoring registry in the same
  PR. Net effect on the raw-env-read ratchet: 89 → 70 (-19) — locked in
  via `scripts/flag_reads_budget.txt`.

### Fixed
- `DEFERRAL_PREVENTION_BLOCK` strengthened (#832): Layer 1 anti-deferral
  prompt instructions are more explicit so the planner is less likely to
  punt obvious questions back to the operator instead of executing.

## 0.1.67

### Added
- Goal mission toggle restored on the chat composer as a Phase 1 opt-in
  (#835): a per-turn toggle lets the operator promote a single message
  into a goal-mode mission without leaving the chat surface.

## 0.1.66

### Changed
- Customize hub Phase 2 (#829, follow-up to #824): the rule forms gain a
  live English preview line that updates as you fill them out ("Every turn,
  block the final answer unless …"), the Add-rule modal pre-fills the right
  underlying form (e.g. "Restrict tool" opens directly on `tool_perm`
  instead of defaulting to `deterministic_ref`), and the SeamBuilder summary
  reads as one humanized sentence per action ("Modify existing preset
  coding-verification: wiring → opt_in") with the raw JSON tucked behind a
  disclosure. New `describe-draft.ts` pure-function module is the single
  source of truth and is direct-unit-tested (28 vitest cases).

## 0.1.65

### Changed
- I-2 truthy convention unification: all `MAGI_*_ENABLED` reads go through a
  single strict `env_bool` (#825 PR A — 31 files, 13 denylist sites + 16
  allowlist sites consolidated + 3 new authority `FlagSpec`s), and the four
  `*_live` channel gates flip from denylist to allowlist (#826 PR B), closing
  the I-2 ratchet. `'0'` / `'false'` / `'no'` / empty / unknown values are
  now uniformly False; only canonical truthy values flip a flag ON.

  Behavior changes worth flagging:
  - `MAGI_RUNNER_POLICY_ROUTE_BLOCKING_ENABLED='0'` (set by the dogfood
    profile) now stays OFF as the profile already intended; an explicit
    `_INTENTIONALLY_DISABLED_BOOL_FLAGS` pin records the intent.
  - `MAGI_GATEWAY_DAEMON_ENABLED='garbage'` now resolves False, where the
    legacy permissive reader treated any non-empty as True. The gate/health
    invariant is preserved (they still agree), but in the False direction.

## 0.1.64

### Changed
- Customize hub Phase 1 mental-model rewrite (#824): the four prior surfaces
  (Presets / Custom Rules / Custom Checks / SeamSpec) collapse into a single
  Rules table with origin badges (built-in / custom / after-tool / SeamSpec).
  SeamSpec mutations now appear inline. Add-rule is a 4-way picker (block bad
  answer / restrict tool / filter result / rewire built-in) that opens the
  matching existing form. Verification and Advanced sub-nav items go away;
  Rules + Guidance take their place. Existing form internals are unchanged.

### Fixed
- Child runner no longer silently reports `status=ok` when ADK emits an
  `error_code` event (#827). Errors are now classified — finish-signal
  variants stay benign, the rest raise `_ChildLlmTurnError(reason=
  "child_llm_<slug>")` so observability surfaces a real failure code instead
  of a blank turn.

## 0.1.63

### Added
- Memory tab now surfaces the workspace archive read-only (#819): the
  dashboard exposes the persisted memory archive alongside the live
  notebook so self-hosters can browse past entries without leaving the
  Memory surface.
- Session-end fact extraction is wired through CLI + serve boundaries
  with new `runtime/active_sessions.py` and `runtime/session_extract_runtime.py`
  modules (#821). Local default-ON so self-hosters benefit; hosted stays
  default-OFF.

### Changed
- Dogfood / lab profile (`scripts/dogfood-full-on.env`) catches up with
  25 capability flags that had already landed in the registry but weren't
  yet flipped on for lab (#820), and arms the live Slack + Discord channel
  watchers behind their import-safe gates (#823). Hosted and bare
  profiles unchanged; default-OFF posture preserved everywhere else.

## 0.1.62

### Added
- Memory PR-1 (#806): the CLI headless turn loop now records every turn into
  the compaction store, closing the parity gap with the existing serve-side
  recorder.
- Memory PR-2 (#807): production cheap-model compaction summarizer with
  fail-open to truncation, behind its own flag.
- Memory PR-3 (#808): optional cheap-model semantic re-rank over BM25
  recall, default-OFF.
- Memory PR-4 (#809): session-end auto-extraction of declarative facts,
  default-OFF.
- C-4 PR-I tenancy / ops / artifacts collapse onto `FalseOnlyAuthorityModel`
  (#801), and the billing follow-up (#802). Closes the C-4 cascade across
  tenancy + billing surfaces.
- C-4 ratchet (#810): a meta-test forbids any new `def model_construct`
  outside `ops/authority`, so future contributors cannot reintroduce the
  forge-true escape hatch the cascade just removed.
- C-10 / C-11 / C-12 cleanup bundle (#798): home redaction limits +
  composio / model_tiers cleanups, with the gate1 SSE redaction golden
  refreshed in the same PR.
- Customize PageHint cards (#804): the wordy amber banners across the
  Verification / Gates / Guidance / Hooks panels collapse into structured
  `✓ can / ✗ cannot / ⓘ note` cards that scan in one glance.
- Customize collapsible preset groups (#817): each domain group can fold,
  the toggle bar offers Expand all / Collapse all, and the `enabled / total`
  badge updates as toggles flip.

### Changed
- Dashboard Settings + Overview surfaces rebuilt (#815, follow-up to #805):
  GlassCard wrappers and intro descriptions removed in favor of section
  headers + hairline dividers; Provider + Model and the API-key env-var +
  workspace-path move into 2-col grids; the duplicated Local Agent hero
  card and the black gradient status panel disappear in favor of one
  header row carrying title + status pill + serve command + Open chat /
  Configure; Runtime + Workspace Inventory collapse into one 6-tile
  strip; quick actions stay as a 3-col row with single-line cards.
- I-1 flag-registry migration: `is_*_enabled` flags move from inline
  callers into the shared `flag_bool` / `flag_profile_bool` registries
  across three batches (#811 batch 1 — 8 flags, #813 batch 2 — 7 remaining
  simple-body flags + empty inventory allowlist, #818 batch 3 — 6
  profile-aware flags). Inventory is now empty so any future inline flag
  read trips the gate.

### Fixed
- SpawnAgent now advertises the live model registry instead of stale
  `claude-opus-4-5` (#816). The tool's `model` parameter description is
  generated from `available_child_model_routes(env)` so it shares the same
  source of truth the runner validates against; the catalog can no longer
  drift away from what the parent LLM is taught.

## 0.1.61

### Added
- Customize UX restructure (#800): Verification splits into inner-tabs, Gates
  are unified across panels, and the Hooks / Advanced distinction is
  clarified. New `GatesPanel`, `GuidancePanel`, `PresetTogglesPanel`, and
  `VerificationTabs` headless components plug into the Phase 4 hub.
- C10 default-OFF `<coding_context>` auto-injection block (#794): a
  registration-time runtime module + `tool_runtime` wire that surfaces the
  caller's current coding-mode context (env.py 3 helpers) to first-party
  recipes. Behind its own flag; OFF path stays byte-identical and the 16
  new tests pin the boundary.

### Changed
- Customize presets registry now keeps the 9 intended-dormant presets and
  pins them via an `_INTENDED_DORMANT_PRESETS` constant with a per-entry
  reason and a helper, instead of deleting them (#788, PR-A v2). The audit
  tests (`test_autopilot_presets`, `test_harness_audit_contract`,
  `test_harness_policy_state`, `test_customize_preset_scopes`) all stay
  green. `coding-workspace-lock` is classified as a user-rule capability
  rather than a preset.

## 0.1.60

### Added
- Customize PR-C SeamSpec stack lands across three default-OFF PRs gated by
  `MAGI_CUSTOMIZE_SEAM_SPEC_ENABLED`:
  - PR-C1 (#789): SeamSpec IR + NL-to-SeamSpec compiler, registration-time
    and runtime-dormant.
  - PR-C2 (#793): SeamSpec store + `seam_for_user` runtime resolver + 3
    `/seams` endpoints (compile / PUT / DELETE). OFF path keeps the store
    empty and the lookup byte-identical with the builtin map.
  - PR-C3 (#796): Customize hub gains a 5th `advanced` sub-nav with the
    SeamSpec rule builder UI panel; the backend endpoints from PR-C2 power
    it.
- C-2 security kernel: `safe_metadata` reconciled onto a single
  `public_diagnostic_metadata` helper so producers reuse one redaction seam
  (#790).
- C-6 + C-7 + C-9 security kernel: one SSRF classifier and one credential
  vocabulary now back all egress + credential surfaces (#795). Removes
  duplicate classifier/vocabulary branches across producers.

### Fixed
- Fireworks LiteLLM path now drops `reasoning_effort` for every value
  (`max` / `high` / `medium` / `low` / `minimal`), not only `max`, closing
  the Kimi / MiniMax "no final answer text arrived" instant-fail that #753
  left open. `thinking` payloads remain untouched (#791).
- Web picker exposes Gemini 3.5 Flash (#792). The backend slug map already
  carried it; only the picker UI list and label-consistency contract test
  were out of sync.

## 0.1.59

### Added
- Customize Phase 4 hub (#786): the customize surface is now a full-page hub
  with a left sub-nav over `verification` / `tools` / `recipes` / `hooks`
  sections, replacing the prior modal stack. URL state syncs through
  `?section=`. Modal bodies were split into headless Panel components
  (legacy modals kept), Recipes is pack-aware (unmapped recipes grey out),
  Hooks is a self-host-only placeholder.
- Dashboard pack authoring is default-ON under the `lab` profile (#785), via
  the `LAB_EXPERIMENTAL_FLAGS` seed pattern, so dogfood/lab self-hosters get
  the Phase 4 builders + producers/gates without flipping the registry
  default. Hosted and bare profiles remain default-OFF; setting the flag to
  `0` under `lab` still wins.

## 0.1.58

### Added
- Customize SHACL compiler hardening (#783, back-port from
  `magi-control-plane`): (1) UNTRUSTED-fence around natural-language input
  with a per-call 16-hex nonce + case-insensitive forgery strip so user NL
  cannot forge the fence; (2) reviewer-not-compiler identity guard at the
  call site (`transport/customize.py` passes two distinct lambdas;
  `compile_with_review` enforces `is`-not); (3) aggregate-text 60K precheck
  that returns 422 before any LLM call; (4) deterministic `_shacl_validate`
  with Turtle parse, pyshacl pass, and vacuity check, surfaced as a
  separate `shaclIssues[]` response signal distinct from the LLM critic.
- C-4 PR-G2 force-false collapse: `harness/*` authority models reuse
  `FalseOnlyAuthorityModel` (#780). 8 `test_evidence_harness_boundary.py`
  sites flipped from raise to coerce-to-False to match the collapsed-base
  pattern; out-of-scope mixed sites (`spawn_depth` / `runOn`) keep their
  raise validator.
- C-4 PR-G3 force-false collapse: `recipes/*` authority models reuse
  `FalseOnlyAuthorityModel` (#765). `ForgedStack` test helper moved to module
  level to work around pydantic 2.13's nested-class deferred-annotation
  resolution (same pattern that landed in #757). Closes the C-4 cascade
  (PR-D/E/F/G1/G2/G3/H all merged).

### Fixed
- Local-runner turn projection no longer downgrades a committed turn into
  abort when no receipt accompanies the live local path (#782). Adds
  `expect_receipt: bool = True` to `project_runner_end_event`; the local
  `live_compatible` path (`event_adapter.py:876`) passes `False` so a
  successful local turn is `committed` from the raw projection layer
  forward, instead of being rewritten to `abort` for missing a receipt that
  the hosted contract requires but the local path never produces. Default
  stays `True`, so hosted projection is byte-identical. Provider-specific
  dashboard rendering differences observed on Kimi/GPT collapse at this
  layer too. Pairs with #779's surface-level provider-routing fix to close
  the diagnosed two-layer issue.

## 0.1.57

### Added
- C-4 PR-G1 force-false collapse: `evidence/*` authority models reuse
  `FalseOnlyAuthorityModel` (#763). 2nd-pass audit aligned 2 missed sites with
  the coerce-to-False pattern (`CodingToolReceiptConfig` raise to coerce, plus
  `ChildRuntimeEnvelope` guard separated from out-of-scope `MetaTaskPlan` /
  `MetaProjectionActivationFlags` which keep their raise validator). PR-G2
  (harness) and PR-G3 (recipes) still open pending the same cascade pass.
- Customize Phase 2: `custom_rules.scope` enforcement accessor seam wires the
  preset-scope data model into `tool_perm` and `verification_policy` (#773).
- Customize Phase 3: `enabled_recipes` allowlist enforcement at runtime
  (`real_runner` + `customize/catalog`), default-OFF (#776).

### Fixed
- Per-turn model override now switches the provider as well (#779). When the
  chat picker sends a `<provider>/<model>` slug like
  `anthropic/claude-sonnet-4-6`, `resolve_provider_config` honors the slug's
  provider instead of layering the override's model id on top of the config's
  default provider, which previously caused LiteLLM "openai does not support
  parameters: ['reasoning_effort'], for model=anthropic/..." errors. Fireworks
  raw ids (`accounts/fireworks/models/...`) are correctly treated as bare ids.
- Web picker label refresh (#778): "Sonnet 4.6" / "Opus 4.8" labels match the
  current Claude line; backed by a label-consistency contract test.

## 0.1.56

### Added
- Reasoning-effort per-turn override reaches the runtime (PR2c, #775). A
  `ContextVar` carries the per-turn level through `chat_routes`,
  `_build_litellm_model`, and `child_runner_live`'s streaming-loop LiteLlm
  rebuilds without changing any wire signature. `_model_reasoning_kwargs`
  consults the ContextVar before falling back to env; `thinking_type` /
  `budget_tokens` remain top-level escape hatches. Payload-and-env both unset
  stays byte-identical.

## 0.1.55

### Added
- C-4 PR-D/E/F/H force-false collapse: `tools/*` (#757), `channels/*` (#771),
  `permissions/auto_control` (#762), and `memory/*` (#766) authority models now
  reuse the `FalseOnlyAuthorityModel` base. PR-G1/G2/G3 (`evidence`, `harness`,
  `recipes`) remain open pending an envelope-contract fix.
- Background-task UX glue: web ack-card + N-running indicator view-models in
  `chat-core` (#769) so the chat surfaces can render the durable-work-queue's
  state. Pure logic, no React/lib deps.
- Lab-profile opt-in for the background-task UX: `scripts/dogfood-full-on.env`
  now exports all five `MAGI_BACKGROUND_*`/`MAGI_WORK_QUEUE_EXECUTOR_ENABLED`
  flags + a hermetic ON-path verification test (`test_background_task_onpath_verify.py`)
  that walks the full enqueue → store → dispatcher → inject-buffer → chat
  consumer chain (#768). Default-OFF preserved.
- Dashboard pack builders web UI (PR4/5 #759, PR5/5 #760): dashboard
  custom-checks/REST endpoints + builder surface, completing the deny-on-present
  authoring path. default-OFF (`MAGI_DASHBOARD_PACK_AUTHORING_ENABLED`).
- Reasoning-effort per-turn knob (PR2a #770 UI dropdown, PR2b #772 chat-client
  wire). Shown only for Anthropic / OpenAI / Gemini models; default `medium`.
  Backend per-turn override lands in PR2c/d (OSS + hosted), so the dropdown is
  currently cosmetic on main.
- OSS picker reflects real runtime config + drop smart-routing knob (#767).

### Changed
- Customize Phase 1: preset scope data model (`config/customize/scope.py`) +
  38-preset classification + helpers + catalog scope reach the OSS payload.
  Engine filter call reverted — wiring will be added by Phase 2/4 follow-ups
  (#754).

### Fixed
- Lab repair preamble no longer fires on non-coding turns. Phase 0 scope-fix
  keeps the gate fully engaged but limits the repair preamble to the two
  surfaces that need it (`repair_loop is_coding_turn`, `real_runner audit→repair`
  coding-only). Site 1 on `engine._pre_final_gate_applies` reverted (#752).

## 0.1.54

### Added
- C-4 PR-C: collapse `connectors/*` force-false bases onto
  `FalseOnlyAuthorityModel`, with 5 per-class semantic invariants preserved
  (`__getattribute__` defense-in-depth, `_force_contract_only`,
  `_force_no_secret_material`, redacted serializer, fail-loud
  `model_construct`/`model_copy`) (#748).
- Hosted flip foundation (default-OFF, hosted-only): `HostedRuntime` +
  `build_hosted_runtime` (#738), `hosted_request_to_turn_context` mapper (#739),
  and the PR5/5 shadow-comparison harness CI gate (#750). Combined with #740 and
  #744 already on 0.1.53, the full flip stack is now in main.
- Customize: `tool_perm.match` `path` / `pathAllowlist` keys for workspace-lock
  custom rules (#751).
- Computer-use robustness: empty-screenshot guard + task-directed app hint
  (#743).
- TUI/headless `/tasks` slash command (#742).
- Documentation: honest classification of the first-party scaffold packs (#741).

### Fixed
- Local-runtime "no final-answer text" recovery: normalize
  `reasoning_effort="max"` per provider so the lab overlay default no longer
  trips OpenAI/Gemini's 4-retry → BadRequest path (`max` → `xhigh` for
  OpenAI/OpenRouter, `max` → `high` for Gemini; Anthropic keeps `max`) (#753).

## 0.1.53

### Added
- H3 LLM producer family (all default-OFF, fail-open, byte-identical when off):
  pre-refusal (#712), completion/promise (#723), resource/self-claim (#726),
  claim-citation (#730), output-purity (#737).
- C-4 strict-authority kernel: `FalseOnlyAuthorityModel` base + golden harness
  (#731), plus C-1 (#714) and C-5 (#713) security refactors that already landed
  on 0.1.52 main between releases.
- Orchestrator pattern turned ON in the lab/full dogfood profile (#717), with
  per-spawn recipe binding (#707), the ON-path go-live verification (#710), and
  hosted wire-profile parity foundations (#702, #722, #740).
- Work-queue completion path: P4 exactly-once (#693), P5 board API/UI/notifier
  (#703/#709/#715), P6 safety prereqs (#728), and a default-OFF
  `/workflows`-style background-tasks runner for web + TUI (#732).
- `magi computer-use install` CLI (real download + integrity-verified install)
  plus the cua-driver 0.5.7 contract (#727); the original #711 contract was
  silently reverted during the #714 rebase and was fully recovered (#729, #733).
- Self-host HookBus default-ON in the full/lab profile (#716) — a no-op until
  the operator authors hooks, hosted unaffected.
- Customize SHACL conversational compile + beginner guide panel + English i18n
  (#734), key-aware chat model picker refresh (#735).
- External recipe + verifier discovery via Python entry_points (`magi.recipes`
  / `magi.verifiers`), strict default-OFF and tighten-only (#718, #719).
- One canonical `canonical_digest()` + FrozenContractModel kernel (#713) and
  one redaction kernel in `ops/safety.py` (#714).
- Documented profiles + the `lab` dogfood profile on the install page (#675,
  carried forward).

### Changed
- Dashboard bundle rebuilt for the message/input area catch-up (#720), SHACL
  conversational compile UI (#734), work-queue board view (#709 + #732), local
  runtime model presets refresh (#735).
- `config/_truthy.py` leaf extracted to break the `flags.py` ↔ `env.py` import
  cycle; `env.py` keeps a byte-identical re-export shim (#725).
- Hosted gate5b wire-profile parity advances: tool-event projector default
  values + the `engine → Gate5B4C3LiveRunnerBoundaryResult` shim (#722, #740).

### Fixed
- Permission scope is now fail-closed with mode-derived strict defaults and a
  default-OFF rollback hatch; `MemoryWrite` stays auto-allowed under fail-closed
  via self-gated readiness, and read-only net tools (`WebSearch`/`WebFetch`) are
  auto-allowed under the default strict scope (#704).
- `persist_model` keeps provider + model coherent (no more `fireworks/gpt-5.5`
  half-set), via stable id-family inference (#724).
- Dead scaffolding deleted (~22k lines): unused shadow contracts plus the tests
  that imported them (#721).
- Hosted clawy: KaTeX math rendering parity for the chat (#1587, hosted repo).
- Documentation-only catch-up of `magi-cp` control-plane plans (#1588, hosted).

## 0.1.52

### Added
- Main-agent-as-orchestrator substrate (all default-OFF): a read+plan+spawn main
  profile, per-spawn `allowedTools` ∩ `spawn_cap` ceiling, and `recipeRefs` that
  bind a child's pre-turn gates/validators/instructions (#691, #707, #710).
- Durable work-queue P4 exactly-once and P5 read-only board API + dashboard board
  view (#693, #703, #709).
- Deterministic SHACL constraint verifier: engine + customize rule kind + NL→SHACL
  compiler + dashboard rule builder, all default-OFF (#690, #694, #700, #701).
- macOS computer-use tool — local, model-agnostic, strictly opt-in and NOT
  profile-enabled; fail-closed installer with a real-binary-verified cua-driver
  contract (#689, #711).
- Answer-quality LLM verification gate, opt-in (#708).
- Hosted wire-profile parity foundation for the gate5b tool-event shape (#702).
- Stream the model's thinking in the local dashboard by default (collapsible
  thinking block); hosted multi-tenant keeps it off (#692).

### Changed
- Dashboard bundle rebuilt for the Work-panel catch-up, key-aware model picker,
  LaTeX/KaTeX rendering, Discord/Slack connect UI, SHACL rule builder, and the
  work-queue board view.

### Fixed
- Security hardening: credential proxy and permission-scope fail-closed posture,
  web tools routed through the SSRF-guarded dispatcher, constant-time gateway-token
  comparison, and active-turn ownership keyed by (session, turn) (#695, #696, #705,
  #706).
- Runtime: gate5b runs Bash/TestRun off the event loop, killable persistent-python
  subprocess timeout, the local dashboard honors the selected model, and the
  anthropic provider routes through cache-aware Claude (#697, #698, #699, #650).

## 0.1.51

### Added
- CLI Anthropic prompt caching: the anthropic provider routes through a
  cache-aware Claude model when message caching is enabled, with a fail-safe
  fallback to the standard path (#650).
- Subagent answer-forwarding: `SpawnAgent` surfaces the child's actual result
  and model attribution to the parent instead of an opaque envelope (#681), and
  the child-runner boundary projection exposes the child's sanitized summary
  (#683).
- Work-queue P3 goal_mode fusion (Ralph loop), default-OFF and inert (#679).
- Research + automation methodology prompt guidance blocks, default-OFF (#678).
- Channels: dashboard connect UI + auth-gated admin token routes for Discord &
  Slack, reusing the encrypted credential store (#677).
- Documented runtime profiles and the `lab` dogfood profile on the install page
  (#675).

### Changed
- Dashboard bundle rebuilt to pick up the key-aware chat model picker (#680),
  LaTeX/KaTeX math rendering (#676), and the channel connect UI (#677).

### Fixed
- Serve: finalize empty/thinking-only turns even without tool-only events, and
  hold the chat queue when a turn ends with work but no final answer so a
  mid-task stop does not feed the next queued message into the unfinished run
  (#686).
- Inline interpreter code (`python3 -c`) is allowed under an explicit bypass
  (YOLO) scope while staying denied in the default strict scope (#676).

## 0.1.50

### Added
- `lab` runtime profile and customize verification activation: customize
  verification + custom rules are profile-aware default-ON (full profile; OFF
  under safe/eval), and a full first-party harness preset suite (artifact
  delivery, redaction, evidence-pack, document-authoring, deterministic-evidence,
  config-aware WHAT-menu, coding-child-review capability) is wired (#652, #664,
  #645, #647, #649, #651, #653, #672, #673).
- Live channel bridge: shared inbound->turn->reply with Telegram (bidirectional),
  Discord (gateway), and Slack (Socket Mode) providers, all gated (#660, #667,
  #669).
- Durable multi-agent work-queue: SQLite task store + dispatcher, default-OFF
  (#658, #674).
- Key-aware sub-agent model routes + local dashboard multi-provider key config
  (#632); user-explicit recipe pin backend seam (#663); subagent harness
  convergence Phase 2A/2B (governed-turn + tighten-only tools) (#644, #656, #671).

### Changed
- Dashboard: styled Select component, per-turn model picker in local serve, and a
  refreshed bundle (#654, #662, #661); model-tier catalog cleanup (#665).

### Fixed
- Local dashboard: user-created channels stay navigable in the static export
  (#659). README-contract guarantees restored after the trim (#643).

## 0.1.49

### Added
- A `lab` runtime profile (`MAGI_RUNTIME_PROFILE=lab`): an opt-in dogfood tier
  that enables the full experimental feature set (all default-OFF flat flags) on
  top of the non-safe profile defaults. Registry defaults and the
  safe/eval/minimal/conservative profiles are unchanged; each flag is still
  individually reversible with `MAGI_X=0` (#652).

### Changed
- Dashboard: native `<select>` elements replaced with a styled, keyboard- and
  ARIA-friendly Select component across the OSS dashboard (#654).

### Fixed
- Recipes: the bundled `authoring-static` recipe pack is no longer
  `defaultEnabled`, so enabling the kernel recipe flag (e.g. via the `lab`
  profile) does not globally auto-select a read-only authoring recipe that would
  hijack coding/chat turns and drop their verification wiring (#655).

## 0.1.48

### Added
- Customize Verification Rules — full custom-rule surface (all default-OFF):
  pre-final LLM-criterion rules (#642) and an after-tool-use ingestion gate that
  strips a tool result by deterministic `contentMatch` and/or a gated LLM
  criterion (#648). Both fail-open; the LLM sub-mode is inert without the egress
  gate.
- Harness convergence Phase 2A: subagents can run through the shared
  `run_governed_turn` primitive (governed turn-loop, multi-turn, memory-mode,
  evidence) while preserving their restricted toolset — gated, default-OFF, with
  a verified no-escalation invariant (#644).

### Fixed
- Restore README-contract guarantees (Homebrew install, `magi-agent serve`,
  local web dashboard, flagship example status) that the README trim had
  dropped (#643).

### Changed
- Docs: clarify the no-fork pack-kernel seam (kernel recipe/role packs stay
  default-OFF) in what-works-today (#641).

## 0.1.47

### Added
- Context compaction (all default-OFF): real-token accounting (#616),
  deterministic tool-output prune tier (#623), summary injection + protected-tool
  preserve on tail-drop (#618), anchored summary + summary circuit breaker (#619),
  manual `/compact` force-compaction (#620).
- Customize Verification Rules redesign (default-OFF): WHEN-domain modal grouping
  with honest tier/opt badges (#633), deterministic custom-rule builder (#636),
  tool-permission custom rules layered over immutable safety (#639).
- Evidence: hosted gate5b4c3 serving runner produces durable per-turn evidence
  (#634, flag-gated); evidence-ledger reader + retention prune over the existing
  sink (#629).
- Kernel completion: no-fork recipe + role packs are now consumable (#626,
  default-OFF).
- Recipe-routing feedback hook: live `select_recipe` choices drive the
  completion-gate obligations (#637, default-OFF).
- Harness convergence Phase 0/1: a single `run_governed_turn` primitive shared by
  the CLI and serving paths, plus golden regression safety-nets (#638, #628).

### Changed
- Memory: recalled memory is framed as a background reference led by a
  continuity policy, so a reset no longer bleeds prior context into the answer
  (#630).
- README narrative realigned around the capability-vs-governance thesis and
  trimmed (#622, #624, #625, #635, #640).

### Fixed
- Usage dashboard runtime cost: price by the provider-qualified litellm model id,
  with a manual per-MTok override for models absent from litellm's map (#627).

## 0.1.46

### Added
- Dashboard: Credentials is now its own sidebar tab, split out of Settings
  (#606).

### Changed
- Run-until-done now relies on prompt-trust: the serve loop terminates on the
  model's final answer (OpenCode-style) rather than re-invoking on a phrasing or
  progress heuristic. The serve prompt was reworded from "complete the requested
  work in this turn" to "execute each step now with tools and continue until the
  work is done — don't just state a plan and stop." Removes the #612 regex
  deferral heuristic (#614).

### Fixed
- OSS local bot: locally-created chat channels now persist. "Add Channel"
  previously appeared to do nothing because the mount-time channel refetch
  overwrote the localStorage cache with only the default channel; the local
  fetch now reads the persisted list (#615).

## 0.1.45

### Added
- Customize tab: verification-rule modal reaches frontend parity (36 presets ×
  7 categories with enforcement badges and a USER-RULES editor), and authored
  USER-RULES are injected into the serve prompt (#603).

### Changed
- Chat-core Batch B: moved the types-dependent core (attachments,
  public-tool-preview, research-evidence, history-merge, e2ee, …) into the
  vendored `chat-core` single-source. Logic unchanged — imports/folds only
  (#613).

### Fixed
- Customize verification route coverage: the bundle-vs-backend route gate no
  longer false-negatives two-parameter routes
  (`/v1/app/customize/verification/{kind}/{item_id}`); drift detection for
  genuinely missing routes is preserved (#603).

## 0.1.44

### Added

- Recipe-scoped tool permissions: once a recipe is selected, tool access is
  scoped to that recipe's granted tools plus always-allowed base tools; other
  recipes' exclusive tools are blocked mid-turn with feedback. Default-OFF,
  gated behind the recipe-routing flag (#610).
- Browser tool now bridges OpenAI-compatible providers (Fireworks, OpenRouter)
  via a provider-specific base URL, with provider-aware vision defaults and a
  `MAGI_BROWSER_USE_VISION` override (#608).
- Keyless web acquisition for the local overlay: jina-reader (keyless) and
  insane-fetch (local curl_cffi) are enabled by default so a fresh, keyless
  user gets a working WebFetch path; "not configured" messages now point to the
  keyless browser fallback (#609).
- The bot prompt now surfaces the available sub-agent model routes (#607).

### Changed

- Serve loop now continues when the model defers instead of executing (restates
  a plan / "next concrete action" without a tool call): it re-invokes with a
  bounded (max 4), heuristic-gated nudge so multi-step tasks run to completion
  instead of stopping after planning (#612).

### Fixed

- Gemini multi-tool 400: when context compaction trimmed the conversation head
  and left `contents` starting with a model `function_call` turn, Gemini
  rejected the request and the live runner died mid-stream. The content-ordering
  repair now drops leading dangling function-response turns and prepends a
  synthetic user opener so a leading function call is always preceded by a user
  turn (#611).
- A hung sub-agent can no longer hang its parent: every child turn is now bound
  by a timeout (#605).

## 0.1.43

### Added
- Description-based LLM recipe/worker routing seam (default-OFF `MAGI_RECIPE_ROUTING_LLM_ENABLED` / `MAGI_WORKER_ROUTING_LLM_ENABLED`); packs carry a `when_to_use` routing signal (#599).

## 0.1.42

### Added
- Serve: stream model reasoning to a collapsible thinking block via `thinking_delta` (all providers; gated behind `MAGI_STREAM_THINKING`) (#600).
- Customize: phased verification-preset scaffold (master flag `MAGI_CUSTOMIZE_VERIFICATION_ENABLED`, default-OFF) — coding-verification opt-out plus opt-in fact-grounding / source-authority / artifact-delivery presets (#595, #601, #602).
- Source-ledger evidence gate for non-coding turns (default-OFF `MAGI_SOURCE_LEDGER_EVIDENCE_GATE_ENABLED`) (#589).
- Child runner honors an operator route allowlist and surfaces valid SpawnAgent routes (#596).
- Dashboard: browsable Composio catalog + hosted-parity Telegram flow, and a local-runtime Model field with per-provider presets (#591, #592).

### Fixed
- ADK: repair Gemini content ordering before the model call to stop multi-tool `runner_error` crashes (profile-gated) (#597).

## 0.1.41

### Added
- TUI: live token usage in the footer/sidebar and the headless stream (#579).
- TUI: safe double-press quit and an optional end-of-turn input queue (`MAGI_TUI_QUEUE`) (#580).
- TUI: `ctrl+o` to expand truncated tool output (`MAGI_TUI_EXPAND_TOOLS`) (#583).
- TUI: footer current-activity word with an optional stall hint (`MAGI_TUI_STALL_SECONDS`) (#584).
- TUI: `@`-file mention autocomplete (`MAGI_TUI_FILE_MENTIONS`) and an honest identity row (#586).
- Self-serve Integrations tab: connect Composio toolkits and a Telegram bot from the dashboard (#578), including a phone-number → BotFather easy-setup path (#587).
- Chat interrupt/inject wiring to in-flight turns (#575).

### Changed
- TUI: cell-aware (CJK) truncation across the interface (#585).
- Serve: stream live work-console events on the local full-engine path; the coding evidence gate now applies only to turns that mutate files (#582).

### Fixed
- Web search: send the `query` field the platform `/v1/search` expects and raise the per-fetch content cap to 128 KB (#576).

## 0.1.40

### Added
- Install-profile bootstrap: at CLI startup the runtime loads `~/.magi/profile.env`
  (`KEY=VAL` / `export KEY=VAL`) and `setdefault`s each `MAGI_*` flag, so a packaged
  install can seed a profile that boots with its chosen gates on. No file is a no-op
  (plain installs keep the default-OFF gates); an explicit env var still wins; an
  explicit `MAGI_RUNTIME_PROFILE=safe`/`eval` skips it.

## 0.1.39

### Added
- The dashboard serve path (Gate5B full toolhost) can now spawn live sub-agents
  via `SpawnAgent` with the full read/write tool surface, gated by
  `MAGI_GATE5B_LIVE_SUBAGENTS_ENABLED` (requires the live child-runner master
  gate). Child-runner depth/total/output/toolset caps are preserved.

## 0.1.38

### Fixed
- Hosted Gate5B/full-toolhost child-runner public events now preserve live
  child-runner receipts instead of reporting helper assignment while the final
  answer says the child runner was unavailable.

## 0.1.37

### Changed
- CLI runs that omit `--permission-mode` now default to `bypassPermissions`,
  while explicit permission-mode choices remain respected.

### Fixed
- Added a `/v1/app/tools` compatibility route for the restored dashboard
  surface.
- Hosted Gate5B/full-toolhost public events now include redacted tool input
  previews so Work panels can show what tools are doing while they run.
- Added a SpawnAgent full-toolhost regression test for live child-runner wiring.

## 0.1.36

### Added
- Added a strict opt-in fact-grounding verification gate for final answers.
- Added opt-in Gate5B governance wiring so selected runtime runs can exercise
  the control-plane and pre-final grounding paths.
- Added `scripts/dogfood-full-on.env` as a sourceable full-runtime dogfood
  profile without changing code defaults.

## 0.1.35

### Fixed
- Runtime Docker images now install the first-party browser, WAF fetch,
  provider, Composio, and CLI extras, plus Playwright Chromium OS dependencies,
  so packaged deployments expose the same BrowserTask, robust web fetch, and
  first-party tool surfaces as source installs.

## 0.1.34

### Fixed
- Interactive TUI bypass permission mode no longer routes tool calls through the
  modal approval sink, so trusted local runs can stay non-interactive.
- CLI and local dashboard runs now prefer configured direct first-party web
  tools before falling back to platform-routed web surfaces, restoring local
  WebSearch/WebFetch availability for key-configured installs and replacing
  internal provider hints with user-facing setup guidance.

## 0.1.33

### Added
- OpenRouter is now a first-class LiteLLM provider. Set
  `OPENROUTER_API_KEY` and use `openrouter/<vendor>/<model>` model slugs, or
  let provider auto-detection pick OpenRouter after direct provider keys.

### Changed
- Removed the channel workflow confirmation gate so workflow routing no longer
  depends on the retired confirmation store.

### Fixed
- Hosted selected Gate5B requests can project a digest-only session identity
  from hosted chat requests, allowing `MAGI_HOSTED_SESSION_REUSE=1` canaries to
  reuse ADK sessions across turns without opening context-continuity write
  authorities.
- Workspace skill discovery now includes bot-generated skills in
  `skills-learned/`, restoring migrated hosted custom skills without copying or
  rewriting PVC state.

## 0.1.32

### Added
- First-party activity evidence now records bundled pack, tool, and recipe
  execution, with a packaged evidence pack for installed-runtime checks.

### Changed
- Tool dispatch capture and Gate5B/full-toolhost gating now surface
  first-party activity evidence consistently across source and packaged installs.

## 0.1.31

### Fixed
- Hosted selected Gate5B streams now surface child-runner progress while the
  child work is running, so hosted Work panels no longer wait until final answer
  projection to show helper activity.

## 0.1.30

### Added
- User-authored runtime packs can now be discovered with zero setup, scaffolded
  with `magi pack new`, and loaded alongside bundled first-party packs.
- Gates, goal-loop, scheduler, and memory policy surfaces are now represented as
  neutral first-party policy packs, widening the programmable runtime surface
  without giving bundled packs special privileges.
- Full-profile installs include the live child-runner defaults needed for
  subagent execution when the full-profile runtime enables that profile.

### Changed
- First-party recipes, tools, hooks, control-plane surfaces, evidence producers,
  and policy gates continue to resolve through the same pack machinery exposed
  to user-authored packs.
- Local and packaged runtime installs now exercise installed-wheel pack
  discovery instead of relying only on source-checkout behavior.

### Fixed
- Bundled first-party `pack.toml` manifests are included in wheels and source
  distributions. This fixes installed environments that previously discovered
  zero packs while source checkouts appeared healthy.
- Pack discovery skips unreadable user pack directories so health checks
  continue through restricted home-directory permissions.
- Headless output now surfaces final-only ADK model text for providers that do
  not emit partial deltas, while avoiding duplicate output when partial tokens
  are present.
- Reset-boundary chat history now preserves post-reset user turns for web
  requests instead of sending only the reset marker and latest prompt.
- Canary/runtime direct usage receipts and selected runtime metering can be
  emitted through the api-proxy path when enabled.

## 0.1.29

### Added
- Subagents now run through a real child-runner boundary with a gated live
  child-runner surface, forwarding the parent's actual objective to the child
  turn.
- The CLI `/model` command is wired to the TUI model picker and persists the
  selection to config; image multimodal input wiring was restored.
- Document authoring gained a DOCX coverage loop, and new default-OFF,
  extras-gated modality tools (VideoFrames, MusicNotation, AudioTranscribe-URL)
  plus a default-OFF autonomous vision browser tool (browser-use).
- Active learnings are injected into CLI prompts, an introspection evidence
  ledger records lifecycle events, and a cross-verify recipe was added.
- The runtime can optionally route LiteLLM traffic through the api-proxy gateway.
- The interactive TUI now includes a dynamic status footer, a toggleable
  todo/context/files sidebar, edit diff previews in permission prompts, and
  focus-aware bell/toast notifications.

### Changed
- The interactive TUI is quieter and more compact: tool calls/results render as
  one-line entries instead of large collapsible cards, internal lifecycle
  diagnostics (routing/policy/turn plumbing) are hidden by default (set
  `MAGI_TUI_VERBOSE=1` to surface them), and surface backgrounds are transparent
  so the terminal theme shows through.

### Fixed
- Tool result previews no longer leak raw `ToolResult` receipt JSON into the TUI
  transcript; only human-readable output is shown.
- Memory write redaction is hardened before summarize, and the app API now
  honors an explicit workspace-root environment override.
- A read-safe class of complex shell commands is allowed for the local coding
  agent.
- The TUI footer now resets after turn errors, and the sidebar clears stale
  todo entries when the latest TodoWrite list is empty.

## 0.1.28

### Added
- Local dashboard Customize controls now expose runtime catalog data,
  verification presets, custom tool toggles, and persisted tool overrides
  through the app API and static dashboard bundle.
- ADK-backed local turns now stream through owned SSE run configuration, giving
  the dashboard and CLI cleaner runtime progress delivery.
- Hipocampus memory now includes gated QMD recall, MemoryWrite registry wiring,
  local full memory tools, append/background compaction, ROOT synthesis, and
  channel memory-mode enforcement.
- Canary model routing can now select configured full-provider canary routes for
  targeted Gate5B runs.
- The default-off Agent Vault egress seam adds an egress proxy boundary for
  future controlled external access.

### Changed
- Web acquisition support now includes the Jina and Insane Fetch providers, with
  WAF-oriented fetch support kept optional behind the `waf` extra.

### Fixed
- Memory collection now confines QMD roots and blocks protected raw memory reads.
- Introspection egress evidence is redacted and the critic path uses hardened
  prompt boundaries.
- Insane Fetch DNS pinning now uses curl options, and the tau-bench harness now
  matches the current `get_env` API.

## 0.1.27

### Fixed
- Magi now keeps a protected base self-identity and treats repository
  `CLAUDE.md`/`AGENTS.md` files as project context instead of agent identity, so
  local runs no longer adopt a workspace's legacy bot persona.
- Installed workspace skills are now loaded without the previous bundled-skill
  cap, allowing `magi-agent serve` and the CLI to expose the full trusted
  workspace skill tree while preserving per-skill body size limits and path
  safety checks.

## 0.1.26

### Added
- Local `magi-agent serve` now ships the restored static web dashboard and app
  API routes in the Python package, so a clean Homebrew install can serve
  `/dashboard` without a Node or Next.js process.
- The local CLI real runner now exposes first-party tools, local tool evidence
  collection, and full-profile runner policy surfaces when a model provider is
  configured.

### Changed
- Clean local installs default to the full local runtime profile, enabling the
  first-party local chat, tool, evidence, policy, repair, learning, scheduler,
  and observability surfaces unless the operator opts out with
  `MAGI_RUNTIME_PROFILE=safe|minimal|off|conservative` or
  `MAGI_AGENT_LOCAL_FULL_RUNTIME_DEFAULTS=0`.

### Fixed
- Dashboard settings now use local app API routes instead of cloud-only
  endpoints.
- First-party runner policy callbacks and control-plane surfaces are now wired
  through the full-profile local runner path rather than remaining metadata-only.

## 0.1.25

### Added
- `magi doctor` now runs real environment diagnostics: provider configuration,
  the `litellm` dependency, config-file readability, and workspace writability.
- Documentation: "What works today" capability page, a "Common tasks → command"
  index, a Telegram/Discord channels guide, an in-session (slash) commands guide,
  a glossary, and this changelog.

### Changed
- Local TUI approval UX now includes the current approval flow improvements and
  transcript rendering uplift from the post-0.1.24 mainline.
- Documentation now clearly separates the local CLI's real execution (a provider
  key enables a real model plus first-party tools behind permission prompts) from
  the enforcement/governance layer, which ships default-off (shadow).
- Configuration docs split local CLI setup (one provider key) from deployment
  variables that the local CLI does not need.

### Fixed
- Local dashboard and CLI chat now keep runner policy routing scoped to each
  runtime driver instance instead of mutating a process-global env override.
- Hosted phase selection now uses the live task profile for routing decisions.
- Streaming dashboard control requests now render and resolve correctly.
- Runner-policy phase routing is no longer default-on, preventing unintended
  model downgrades from stale routing metadata.
- Corrected default model ids in the docs to match the runtime
  (`claude-sonnet-4-6`, `gpt-5.5`, `gemini-3.5-flash`, `kimi-k2-instruct`).
- Authority-flag env vars now documented with the real `CORE_AGENT_PYTHON_` prefix.
- Fixed an evidence-contract example that used snake_case triggers
  (`after_tool_use`) instead of the valid `afterToolUse`/`beforeCommit` tokens.
- Documented that headless one-shot `magi -p` in `default` mode cannot resolve
  tool approvals (use `--permission-mode acceptEdits`/`bypassPermissions` or the
  interactive TUI); corrected the tool catalog count and added `MemoryWrite`.

> Earlier history predates this changelog. Use `git log` and GitHub Releases for
> a complete record.
