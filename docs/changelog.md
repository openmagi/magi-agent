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
