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
