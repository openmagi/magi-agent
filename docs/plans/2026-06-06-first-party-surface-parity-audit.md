# First-Party Surface Parity Audit

Date: 2026-06-06

## Scope

This audit compares the hosted legacy TypeScript runtime in
`clawy/infra/docker/clawy-core-agent/dist/tools`, hosted template skills in
`clawy/src/lib/templates/skills`, and the OSS Python ADK runtime on current
`origin/main`.

The first PR scope is intentionally default-inert: catalog, fixture, and
contract coverage only. It does not attach browser workers, web providers,
document workers, knowledge writers, scheduled delivery, subagent execution, or
external integrations.

## Tool Surface Matrix

| Legacy TS surface | OSS Python ADK status |
| --- | --- |
| Core workspace/control tools: `FileRead`, `FileWrite`, `FileEdit`, `PatchApply`, `Glob`, `Grep`, `Bash`, `TestRun`, `GitDiff`, `ToolSearch`, `AskUserQuestion`, `EnterPlanMode`, `ExitPlanMode`, `Clock`, `Calculation`, `ArtifactCreate`, `ArtifactRead`, `ArtifactList`, `TaskList`, `TaskGet`, `TaskOutput`, `CronList` | Present in `magi_agent.tools.core_tool_manifests()` with conservative permission and parallel-safety metadata. |
| Native plugin tools: `ArtifactDelete`, `ArtifactUpdate`, `Browser`, `SocialBrowser`, `WebSearch`, `WebFetch`, `DocumentWrite`, `SpreadsheetWrite`, `KnowledgeSearch`, `MemoryRedact`, `MissionLedger`, `NotifyUser`, `CodeDiagnostics`, `CodeIntelligence`, `CodeSymbolSearch`, `CodeWorkspace`, `CodingBenchmark`, `CommitCheckpoint`, `PackageDependencyResolve`, `ProjectVerificationPlanner`, `RepoMap`, `RepositoryMap`, `RepoTaskState`, `SafeCommand`, `SkillLoader`, `SkillRuntimeHooks`, `SpawnAgent`, `SpawnWorktreeApply`, `CronCreate`, `CronUpdate`, `CronDelete`, `TaskStop`, `DateRange`, `ExternalSourceCache`, `ExternalSourceRead`, `TaskBoard`, `SwitchToActMode` | Present as first-party native plugin tool refs. Plugin state is default enabled for metadata discovery, but traffic and execution attachment remain false. |
| `FileDeliver`, `FileSend` | Present only as `openmagi.documents` tool capabilities, not live tool refs. This preserves delivery-contract visibility without granting delivery authority. |
| `ToolRegistry` | Implementation module, not an agent-callable tool. OSS has `magi_agent.tools.ToolRegistry`. |

The detailed per-tool fixture is
`tests/fixtures/parity/first_party_surface_audit_matrix.json`.

## Recipe And Harness Matrix

| Work class | OSS recipe/harness status |
| --- | --- |
| Research and web acquisition | `openmagi.web-acquisition`, `openmagi.research`, `openmagi.research-scout`; provider interfaces are metadata-only with provider calls disallowed by default. |
| Coding | `openmagi.dev-coding`, `openmagi.autopilot`, coding ownership/read-before-edit/final-projection contracts, local coding native plugin descriptors. |
| General automation | `openmagi.office-automation`, `openmagi.artifact-delivery`, `openmagi.browser-automation`, `openmagi.document-review`, `openmagi.lightweight-scripting`, plus general automation harness modules. |
| Scheduler and background work | `openmagi.scheduled-work`, scheduler/runtime harness modules, cron/task native plugin descriptors. Scheduler execution remains unattached. |
| Memory | `openmagi.memory-agentmemory`, memory recall/write policy modules, AgentMemory provider candidate descriptors. Live provider authority remains unattached. |
| Channels and delivery | `openmagi.channel-delivery`, artifact/file delivery contracts, Telegram/Discord channel boundaries. Provider-specific delivery/read receipts remain future work. |
| Skills and methodology | `openmagi.agent-methodology`, `openmagi.superpowers-compat`, bundled Superpowers workflow skills, `SkillLoader`, and `SkillRuntimeHooks`. |
| Security posture | `openmagi.context-safety`, `openmagi.evidence`, `openmagi.security-posture` native plugin harness capabilities. Hard-safety surfaces remain non-opt-out where declared. |

All first-party recipe packs in the fixture must compile with every
`RecipeAttachmentFlags` value false and no `live_tool_refs`,
`live_callback_refs`, or `runner_route_refs`.

## Missing Or Plan-Only Surfaces

1. Hosted template skills are not bundled as OSS first-party skill packs.
   The hosted tree contains broad domain and integration skills such as browser,
   document reader/writer, web search/fetch/insane fetch, knowledge
   search/write, coding standards, Google Docs/Sheets, Firecrawl, finance,
   legal, accounting, ads, maps, and social-channel skills. These depend on
   hosted scripts, provider credentials, sealed files, and production routing.
   Port them as curated OSS skill packs only after per-pack safety review and
   tests.
2. Live provider authority for browser, web acquisition, document conversion,
   knowledge write, scheduler delivery, and subagent execution remains
   intentionally absent. Future PRs should attach one provider family at a time
   through ToolHost policy, approval, credentials, receipts, and live contract
   tests.
3. Delivery ACK depth is still product-layer work. The OSS contracts expose
   artifact/file delivery evidence and channel boundaries, but provider-specific
   display/read receipts should stay separate from this audit PR.

## Contract Added

`tests/test_first_party_surface_parity_audit.py` now checks:

- every legacy TS agent-callable tool is represented as a core tool, native
  plugin tool, or metadata-only capability;
- `FileDeliver` and `FileSend` stay metadata-only capabilities;
- all first-party recipe packs remain default-inert;
- native plugin state does not attach traffic or execution;
- bundled workflow skills are present and hosted template skills are documented
  as plan-only with no default authority.
