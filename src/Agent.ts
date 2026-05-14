/**
 * Agent — singleton per bot pod.
 * Design reference: §5.1.
 *
 * Phase 0: wiring skeleton. Startup/shutdown, session registry, turn-id
 * generation. Tool registry / LLM client / cron / channels get wired
 * in later phases.
 */

import path from "node:path";
import fs from "node:fs/promises";
import { monotonicFactory } from "ulid";
import {
  Session,
  type SessionMeta,
  type Discipline,
  type PermissionMode,
  personaFromSessionKey,
} from "./Session.js";
import {
  DEFAULT_DISCIPLINE,
  loadDisciplineConfig,
} from "./discipline/config.js";
import { isCodingHardModeSkillActive } from "./discipline/codingHardModeSkills.js";
import {
  makeCommitCheckpointTool,
  runGit,
} from "./tools/CommitCheckpoint.js";
import type { DisciplineSessionCounter } from "./hooks/builtin/disciplineHook.js";
import type { ChannelRef } from "./util/types.js";
import { createLogger } from "./util/logger.js";

const agentLogger = createLogger("Agent");
import { LLMClient } from "./transport/LLMClient.js";
import { DirectLLMClient } from "./transport/DirectLLMClient.js";
import { Workspace } from "./storage/Workspace.js";
import { AuditLog } from "./storage/AuditLog.js";
import { sessionFileName } from "./storage/TranscriptReader.js";
import { ToolRegistry } from "./tools/ToolRegistry.js";
import { IntentClassifier } from "./rules/IntentClassifier.js";
import { HookRegistry } from "./hooks/HookRegistry.js";
import { clearCircuitBreakerState } from "./hooks/builtin/repeatedFailureGuard.js";
import { registerBuiltinHooks } from "./hooks/builtin/index.js";
import { ContextEngine } from "./services/compact/ContextEngine.js";
import { QmdManager } from "./services/memory/QmdManager.js";
import { CompactionEngine } from "./services/memory/CompactionEngine.js";
import { HipocampusService } from "./services/memory/HipocampusService.js";
import { makeFileReadTool } from "./tools/FileRead.js";
import { makeFileWriteTool } from "./tools/FileWrite.js";
import { makeFileEditTool } from "./tools/FileEdit.js";
import { makePatchApplyTool } from "./tools/PatchApply.js";
import { makeMemoryRedactTool } from "./tools/MemoryRedact.js";
import { makeCodeWorkspaceTool } from "./tools/CodeWorkspace.js";
import { makeCodeSymbolSearchTool } from "./tools/CodeSymbolSearch.js";
import { makeCodeIntelligenceTool } from "./tools/CodeIntelligence.js";
import { makeCodeDiagnosticsTool } from "./tools/CodeDiagnostics.js";
import { makeRepositoryMapTool } from "./tools/RepositoryMap.js";
import { makeCodingBenchmarkTool } from "./tools/CodingBenchmark.js";
import { makeSpawnWorktreeApplyTool } from "./tools/SpawnWorktreeApply.js";
import { makePackageDependencyResolveTool } from "./tools/PackageDependencyResolve.js";
import { makeExternalSourceCacheTool } from "./tools/ExternalSourceCache.js";
import { makeExternalSourceReadTool } from "./tools/ExternalSourceRead.js";
import { makeBashTool } from "./tools/Bash.js";
import { makeSafeCommandTool } from "./tools/SafeCommand.js";
import { makeTestRunTool } from "./tools/TestRun.js";
import { makeProjectVerificationPlannerTool } from "./tools/ProjectVerificationPlanner.js";
import { makeGitDiffTool } from "./tools/GitDiff.js";
import { makeRepoTaskStateTool } from "./tools/RepoTaskState.js";
import { makeGlobTool } from "./tools/Glob.js";
import { makeGrepTool } from "./tools/Grep.js";
import { makeTaskBoardTool } from "./tools/TaskBoard.js";
import { makeNotifyUserTool } from "./tools/NotifyUser.js";
import {
  makeSpawnAgentTool,
  runChildAgentLoop,
  type SpawnChildOptions,
  type SpawnChildResult,
} from "./tools/SpawnAgent.js";
import { makeAskUserQuestionTool } from "./tools/AskUserQuestion.js";
import {
  makeExitPlanModeTool,
  type PlanModeController,
} from "./tools/ExitPlanMode.js";
import { makeSwitchToActModeTool } from "./tools/SwitchToActMode.js";
import {
  makeEnterPlanModeTool,
  type PlanModeEntryController,
} from "./tools/EnterPlanMode.js";
import { BackgroundTaskRegistry } from "./tasks/BackgroundTaskRegistry.js";
import { makeTaskListTool } from "./tools/TaskList.js";
import { makeTaskGetTool } from "./tools/TaskGet.js";
import { makeTaskOutputTool } from "./tools/TaskOutput.js";
import { makeTaskStopTool } from "./tools/TaskStop.js";
import { PolicyKernel } from "./policy/PolicyKernel.js";
import { DebugWorkflow } from "./debug/DebugWorkflow.js";
import { ArtifactManager } from "./artifacts/ArtifactManager.js";
import { OutputArtifactRegistry } from "./output/OutputArtifactRegistry.js";
import { makeArtifactCreateTool } from "./tools/ArtifactCreate.js";
import { makeArtifactReadTool } from "./tools/ArtifactRead.js";
import { makeArtifactListTool } from "./tools/ArtifactList.js";
import { makeArtifactUpdateTool } from "./tools/ArtifactUpdate.js";
import { makeArtifactDeleteTool } from "./tools/ArtifactDelete.js";
import { makeDocumentWriteTool } from "./tools/DocumentWrite.js";
import { makeBrowserTool } from "./tools/Browser.js";
import { makeSocialBrowserTool } from "./tools/SocialBrowser.js";
import { makeWebFetchTool } from "./tools/WebFetch.js";
import { makeWebSearchTool } from "./tools/WebSearch.js";
import { makeClockTool } from "./tools/Clock.js";
import { makeDateRangeTool } from "./tools/DateRange.js";
import { makeCalculationTool } from "./tools/Calculation.js";
import { makeKnowledgeSearchTool } from "./tools/KnowledgeSearch.js";
import { makeFileDeliverTool } from "./tools/FileDeliver.js";
import { makeFileSendTool } from "./tools/FileSend.js";
import { makeSpreadsheetWriteTool } from "./tools/SpreadsheetWrite.js";
import { registerSkillRuntimeHooks } from "./tools/SkillRuntimeHooks.js";
import { CronScheduler, type CronRecord } from "./cron/CronScheduler.js";
import { runScriptCron, type ScriptCronResult } from "./cron/ScriptCronRunner.js";
import { makeCronCreateTool } from "./tools/CronCreate.js";
import { makeCronListTool } from "./tools/CronList.js";
import { makeCronUpdateTool } from "./tools/CronUpdate.js";
import { makeCronDeleteTool } from "./tools/CronDelete.js";
import { MissionClient } from "./missions/MissionClient.js";
import { MissionActionReconciler } from "./missions/MissionActionReconciler.js";
import type { GoalMissionResumeInput, MissionChannelType } from "./missions/types.js";
import { makeMissionLedgerTool } from "./tools/MissionLedger.js";
import type { Turn } from "./Turn.js";
import type {
  ChannelAdapter,
  ChannelDeliveryReceipt,
} from "./channels/ChannelAdapter.js";
import { TelegramPoller } from "./channels/TelegramPoller.js";
import { DiscordClient } from "./channels/DiscordClient.js";
import { WebAppChannelAdapter } from "./channels/WebAppChannelAdapter.js";
import { dispatchInbound } from "./channels/ChannelDispatcher.js";
import { SlashCommandRegistry } from "./slash/registry.js";
import {
  makeCompactCommand,
  makeResetCommand,
  makeStatusCommand,
} from "./slash/commands.js";
import {
  makeAllSuperpowersSkillCommands,
  makeOnboardingCommand,
  makePlanCommand,
} from "./slash/superpowers.js";
import { ResetCounterStore } from "./slash/resetCounters.js";
import { RouterEngine } from "./routing/RouterEngine.js";
import type { RoutingMode } from "./routing/types.js";

/**
 * Lifecycle-level events emitted on the Agent-scoped event bus.
 * Distinct from `AgentEvent` (per-turn SSE stream) — these signal
 * transitions that outlive any single Turn.
 */
export type AgentLifecycleEvent =
  | {
      type: "session_closed";
      sessionKey: string;
      reason?: string;
      closedAt: number;
    };

export type BackgroundTaskDeliveryStatus = "completed" | "failed" | "aborted";

export interface BackgroundTaskDeliveryInput {
  sessionKey: string;
  taskId: string;
  status: BackgroundTaskDeliveryStatus;
  finalText?: string;
  errorMessage?: string;
}

interface CronMissionLink {
  missionId: string;
  missionRunId?: string;
}

export interface SkillReloadResult {
  loaded: number;
  issues: number;
  runtimeHooks: number;
}

const MAX_BACKGROUND_DELIVERY_BYTES = 15 * 1024;
const TRUNCATED_BACKGROUND_RESULT_MARKER = "\n\n[truncated background result]";

function runtimeMissionsEnabled(): boolean {
  return process.env.MAGI_MISSIONS === "1";
}

function missionActionsEnabled(): boolean {
  return runtimeMissionsEnabled() && process.env.MAGI_MISSION_ACTIONS !== "0";
}

function scriptCronEnabled(): boolean {
  return process.env.MAGI_SCRIPT_CRON === "1";
}

function truncateMissionText(value: string, limit: number): string {
  return value.length <= limit ? value : value.slice(0, limit - 1).trimEnd();
}

export function formatBackgroundTaskDelivery(
  input: BackgroundTaskDeliveryInput,
): string | null {
  if (input.status === "aborted") {
    return null;
  }

  let text: string;
  if (input.status === "completed") {
    text = (input.finalText ?? "").trim();
    if (text.length === 0) {
      return null;
    }
  } else {
    const msg = (input.errorMessage ?? "").trim();
    text =
      msg.length > 0
        ? `Background task ${input.taskId} failed: ${msg}`
        : `Background task ${input.taskId} failed.`;
  }

  const bytes = Buffer.byteLength(text, "utf8");
  if (bytes <= MAX_BACKGROUND_DELIVERY_BYTES) {
    return text;
  }

  const markerBytes = Buffer.byteLength(TRUNCATED_BACKGROUND_RESULT_MARKER, "utf8");
  const limit = Math.max(0, MAX_BACKGROUND_DELIVERY_BYTES - markerBytes);
  let used = 0;
  let truncated = "";
  for (const char of text) {
    const charBytes = Buffer.byteLength(char, "utf8");
    if (used + charBytes > limit) {
      break;
    }
    truncated += char;
    used += charBytes;
  }
  return `${truncated}${TRUNCATED_BACKGROUND_RESULT_MARKER}`;
}

export interface AgentConfig {
  botId: string;
  userId: string;
  /** legacy-compatible path: /home/ocuser/.magi/workspace. */
  workspaceRoot: string;
  gatewayToken: string;
  codexAccessToken?: string;
  codexRefreshToken?: string;
  apiProxyUrl: string;
  chatProxyUrl?: string;
  redisUrl?: string;
  /** Default model for this bot. Can be overridden per turn by hosted deployments. */
  model: string;
  /** OSS multi-provider support — delegates LLM calls to this provider. */
  llmProvider?: { stream(req: import("./transport/LLMClient.js").LLMStreamRequest): AsyncGenerator<import("./transport/LLMClient.js").LLMEvent, void, void> };
  /** Native router mode. Hosted mode still calls api-proxy for provider auth/billing. */
  routingMode?: RoutingMode;
  /** Built-in routing profile id, e.g. "standard" or "anthropic_only". */
  routingProfileId?: string;
  /** Standalone direct-provider config, only used by direct mode. */
  directProviders?: Record<
    string,
    { kind: "anthropic" | "openai-compatible"; baseUrl: string; apiKey?: string }
  >;
  /** OSS identity — optional agent display name. */
  agentName?: string;
  /** OSS identity — optional custom instructions. */
  agentInstructions?: string;
  /**
   * Initial tool-permission posture for newly-created sessions.
   * Hosted Magi Cloud pods can set "bypass" explicitly; OSS env mode
   * defaults to "workspace-bypass" so local workspace automation stays
   * low-friction while system/secret boundaries remain active.
   */
  defaultPermissionMode?: PermissionMode;
  telegramBotToken?: string;
  discordBotToken?: string;
  /**
   * T1-06 — maximum turns per session before Turn.execute() aborts
   * with a budget-exceeded error. Default 50 (see
   * DEFAULT_MAX_TURNS_PER_SESSION in Session.ts).
   */
  maxTurnsPerSession?: number;
  /**
   * T1-06 — maximum accumulated USD cost per session. No longer
   * enforced as a gate (api-proxy credit pre-deduction is the
   * authoritative spend limit). Retained for /status telemetry.
   * Defaults to Infinity.
   */
  maxCostUsdPerSession?: number;
  /**
   * C1 — channel adapter factories. Injected for tests so the Agent
   * doesn't open a real Telegram long-poll / Discord gateway socket.
   * When a token is set but no factory is provided, the Agent
   * constructs the native adapter.
   */
  telegramAdapterFactory?: (token: string, workspaceRoot: string) => ChannelAdapter;
  discordAdapterFactory?: (token: string) => ChannelAdapter;
  /**
   * §7.15 — outbound-only HTTP push to chat-proxy for web/app channels.
   * When webAppPushEndpointUrl + webAppPushHmacKey are set, the Agent
   * instantiates a WebAppChannelAdapter and exposes it via
   * `agent.webAppAdapter` so cron / background paths can deliver
   * messages to a live web/mobile client via Supabase Realtime.
   *
   * Factory override is accepted for tests — when provided, it wins
   * over the env-derived construction.
   */
  webAppPushEndpointUrl?: string;
  webAppPushHmacKey?: string;
  webAppAdapterFactory?: (cfg: {
    pushEndpointUrl: string;
    hmacKey: string;
    gatewayToken: string;
    botId: string;
    userId: string;
  }) => WebAppChannelAdapter;
  /**
   * Directory holding the bundled superpowers skills (one subdirectory
   * per skill). Defaults to `<repo>/infra/docker/magi-core-agent/skills/superpowers`
   * resolved from the module location. Tests inject a temp dir.
   * See `docs/plans/2026-04-20-superpowers-plugin-design.md`.
   */
  superpowersSkillsDir?: string;
}

export class Agent {
  readonly config: AgentConfig;
  readonly llm: LLMClient;
  readonly router: RouterEngine | null;
  readonly workspace: Workspace;
  readonly tools: ToolRegistry;
  readonly intent: IntentClassifier;
  readonly hooks: HookRegistry;
  /** T1-02: compaction-boundary orchestrator (§7.12.b revised). */
  readonly contextEngine: ContextEngine;
  readonly sessionsDir: string;
  readonly auditLog: AuditLog;
  /** T2-10: registry of `deliver="background"` SpawnAgent tasks. */
  readonly backgroundTasks: BackgroundTaskRegistry;
  /** T4-20: tiered artifact manager (L0 / L1 / L2). */
  readonly artifacts: ArtifactManager;
  /** User-visible generated file registry for document/file outputs. */
  readonly outputArtifacts: OutputArtifactRegistry;
  /** Cron scheduler — post-Phase-3 legacy-runtime parity. */
  readonly crons: CronScheduler;
  /** Mission ledger client for durable runtime coordination. */
  readonly missionClient: MissionClient;
  /** Polls durable mission user actions into local runtime state. */
  private readonly missionActionReconciler: MissionActionReconciler;
  /** Native hipocampus memory — qmd search index. */
  readonly qmdManager: QmdManager;
  /** Native hipocampus memory — compaction engine. */
  compactionEngine!: CompactionEngine;
  /** First-class Hipocampus runtime facade. */
  readonly hipocampus: HipocampusService;
  /** Typed runtime policy facade. */
  readonly policy: PolicyKernel;
  /** Workflow-native debugging state manager. */
  readonly debugWorkflow: DebugWorkflow;
  /**
   * Built-in slash commands (`/compact`, `/reset`, `/status`). Ported
   * from legacy gateway so migrated bots keep the same inline UX. See
   * `src/slash/registry.ts`.
   */
  readonly slashCommands: SlashCommandRegistry;
  /** Per-channel reset counter store — durable sidecar consumed by
   * {@link ChannelDispatcher} to namespace sessionKeys after `/reset`. */
  readonly resetCounters: ResetCounterStore;
  /** Hash → plaintext sessionKey cache populated as sessions register.
   * Used by Phase 2h audit/compliance endpoints to map transcript
   * filenames back to sessionKeys. Cold sessions (no in-memory mapping)
   * surface a `#<hash>` opaque id. */
  private readonly sessionKeyByHash = new Map<string, string>();
  private readonly sessions = new Map<string, Session>();
  private readonly ulid = monotonicFactory();
  private started = false;
  /** Active turns indexed by turnId — populated by Session.runTurn so
   * the HTTP ask-response endpoint + ExitPlanMode tool can find them. */
  private readonly activeTurns = new Map<string, Turn>();
  private readonly cancelledGoalMissionIds = new Set<string>();
  /** C1 — live channel adapters. Populated in start() when tokens set. */
  private readonly channelAdapters: ChannelAdapter[] = [];
  private skillRuntimeHooksRegistered = false;
  /**
   * Coding Discipline default — produced by `.discipline.yaml` on
   * Agent.start() or by falling through to {@link DEFAULT_DISCIPLINE}.
   * Stamped onto new Session.meta.discipline at
   * {@link getOrCreateSession} time. Per-turn classifier hook may
   * override on the session afterwards.
   */
  private disciplineDefault: Discipline = { ...DEFAULT_DISCIPLINE };
  /**
   * Per-session mutation counters for the Discipline subsystem. Keyed
   * by sessionKey; lazily created on first lookup. Purely in-memory —
   * counters reset on pod restart, which is intentional (the counter
   * is a "since session start" metric, not a persisted log).
   */
  private readonly disciplineCounters = new Map<
    string,
    DisciplineSessionCounter
  >();
  /**
   * §7.15 — outbound push adapter for web/app channels. Populated in
   * start() when webAppPushEndpointUrl + webAppPushHmacKey are set.
   * Unlike channelAdapters this is outbound-only, so it does not
   * register an inbound handler with ChannelDispatcher.
   */
  webAppAdapter: WebAppChannelAdapter | null = null;

  /**
   * Lightweight Agent-level event bus. Subscribers receive
   * `session_closed` (#82) and any future lifecycle events that are
   * scoped above a single Turn's SseWriter. Listener errors are
   * caught + logged so one bad consumer never blocks the emission
   * path. Use {@link onAgentEvent} to subscribe.
   */
  private readonly agentEventListeners: Array<(event: AgentLifecycleEvent) => void> = [];

  constructor(config: AgentConfig) {
    this.config = config;
    this.llm =
      config.routingMode === "direct"
        ? new DirectLLMClient({
            providers: config.directProviders ?? {},
          })
        : config.llmProvider
          ? LLMClient.fromProvider(config.llmProvider, config.model)
          : new LLMClient({
              apiProxyUrl: config.apiProxyUrl,
              gatewayToken: config.gatewayToken,
              codexAccessToken: config.codexAccessToken,
              codexRefreshToken: config.codexRefreshToken,
              defaultModel: config.model,
            });
    this.router =
      config.routingMode === "hosted-proxy" || config.routingMode === "direct"
        ? new RouterEngine({
            llm: this.llm,
            profileId: config.routingProfileId ?? "standard",
          })
        : null;
    this.workspace = new Workspace(config.workspaceRoot);
    this.policy = new PolicyKernel(this.workspace);
    this.debugWorkflow = new DebugWorkflow();
    this.intent = new IntentClassifier(this.llm);
    this.hooks = new HookRegistry();
    this.contextEngine = new ContextEngine(this.llm);
    // Session transcripts live inside the workspace so they ride the
    // same PVC and are visible to hipocampus etc.
    this.sessionsDir = path.join(config.workspaceRoot, "core-agent", "sessions");
    this.auditLog = new AuditLog(config.workspaceRoot, config.botId);
    this.backgroundTasks = new BackgroundTaskRegistry(config.workspaceRoot);
    // T4-20 — tiered artifact manager. LLMClient wired for Haiku-backed
    // L1/L2 generation; falls back to deterministic summaries if Haiku fails.
    this.artifacts = new ArtifactManager(config.workspaceRoot, this.llm);
    this.outputArtifacts = new OutputArtifactRegistry(config.workspaceRoot);
    // Cron scheduler — hydrated + started in start(), tools registered
    // below so they're available before the first turn even if no
    // crons exist yet.
    this.crons = new CronScheduler(config.workspaceRoot);
    this.missionClient = new MissionClient({
      chatProxyUrl: config.chatProxyUrl ?? "",
      gatewayToken: config.gatewayToken,
    });
    this.missionActionReconciler = new MissionActionReconciler({
      workspaceRoot: config.workspaceRoot,
      missionClient: this.missionClient,
      backgroundTasks: this.backgroundTasks,
      crons: this.crons,
      goals: {
        resumeAfterRestart: (input) => this.resumeGoalMissionAfterRestart(input),
        cancel: (missionId) => this.cancelGoalMission(missionId),
      },
    });
    // Native hipocampus — qmd search. Started in start().
    this.qmdManager = new QmdManager(
      config.workspaceRoot,
      (process.env.MAGI_VECTOR_SEARCH ?? "off").trim().toLowerCase() === "on",
    );
    this.hipocampus = new HipocampusService({
      workspaceRoot: config.workspaceRoot,
      defaultModel: config.model,
      llm: this.llm,
      qmdManager: this.qmdManager,
    });
    // Slash commands — built-in registry + reset-counter sidecar. The
    // three core commands (/compact, /reset, /status) are registered
    // right here (not in start()) so unit tests that skip start() still
    // see the bundled set.
    this.resetCounters = new ResetCounterStore(this.sessionsDir);
    this.slashCommands = new SlashCommandRegistry();
    this.slashCommands.register(makeCompactCommand(this));
    this.slashCommands.register(makeResetCommand(this, this.resetCounters));
    this.slashCommands.register(makeStatusCommand(this, this.resetCounters));
    // Superpowers slash commands — `/plan`, `/onboarding`, and
    // `/superpowers:<name>` (one per bundled skill). Independent of
    // the SkillLoader Phase 2b tool registration: the slash commands
    // surface the SKILL.md body as synthetic assistant text (non-LLM,
    // non-billed path), while the tools are still invocable via
    // normal tool_use when the LLM turn runs. Per Kevin 2026-04-20:
    // full `/superpowers:*` names only, no aliases.
    const superpowersDir =
      config.superpowersSkillsDir ?? resolveDefaultSuperpowersDir();
    this.slashCommands.register(makePlanCommand(superpowersDir));
    this.slashCommands.register(makeOnboardingCommand(superpowersDir));
    for (const cmd of makeAllSuperpowersSkillCommands(superpowersDir)) {
      this.slashCommands.register(cmd);
    }

    // Register the Phase 1b built-in tool set. Phase 2 adds skill
    // loading on top of this via ToolRegistry.loadSkills().
    this.tools = new ToolRegistry();
    const getSourceChannelForTool = (ctx: { turnId: string }): ChannelRef | null =>
      this.getSourceChannelForTool(ctx);
    this.tools.register(makeFileReadTool(config.workspaceRoot));
    this.tools.register(makeFileWriteTool(config.workspaceRoot));
    this.tools.register(makeFileEditTool(config.workspaceRoot));
    this.tools.register(makePatchApplyTool(config.workspaceRoot));
    this.tools.register(makeMemoryRedactTool(config.workspaceRoot));
    this.tools.register(makeCodeWorkspaceTool(config.workspaceRoot));
    this.tools.register(makeCodeSymbolSearchTool(config.workspaceRoot));
    this.tools.register(makeCodeIntelligenceTool(config.workspaceRoot));
    this.tools.register(makeCodeDiagnosticsTool(config.workspaceRoot));
    this.tools.register(makeRepositoryMapTool(config.workspaceRoot));
    this.tools.register(makeCodingBenchmarkTool(config.workspaceRoot));
    this.tools.register(makeSpawnWorktreeApplyTool(config.workspaceRoot));
    this.tools.register(makePackageDependencyResolveTool());
    this.tools.register(makeExternalSourceCacheTool());
    this.tools.register(makeExternalSourceReadTool());
    this.tools.register(makeBashTool(config.workspaceRoot, this.backgroundTasks));
    this.tools.register(makeSafeCommandTool(config.workspaceRoot));
    this.tools.register(makeTestRunTool(config.workspaceRoot));
    this.tools.register(makeProjectVerificationPlannerTool(config.workspaceRoot));
    this.tools.register(makeGitDiffTool(config.workspaceRoot));
    this.tools.register(makeRepoTaskStateTool(config.workspaceRoot));
    this.tools.register(makeGlobTool(config.workspaceRoot));
    this.tools.register(makeGrepTool(config.workspaceRoot));
    this.tools.register(makeTaskBoardTool(this.sessionsDir));
    // §7.14 — out-of-band push notifications via chat-proxy broker.
    this.tools.register(
      makeNotifyUserTool({
        chatProxyUrl: config.chatProxyUrl ?? "",
        gatewayToken: config.gatewayToken,
        userId: config.userId,
      }),
    );
    // §7.12.d — native subagent delegation. Registered last so it sees
    // the full parent tool list when the child filters its allowlist.
    this.tools.register(
      makeSpawnAgentTool(this, this.backgroundTasks, {
        missionClient: this.missionClient,
        getSourceChannel: getSourceChannelForTool,
      }),
    );
    // T2-10 — query / stop tools over background SpawnAgent and Bash tasks.
    this.tools.register(makeTaskListTool(this.backgroundTasks));
    this.tools.register(makeTaskGetTool(this.backgroundTasks));
    this.tools.register(makeTaskOutputTool(this.backgroundTasks));
    this.tools.register(makeTaskStopTool(this.backgroundTasks));
    // T4-20 — tiered artifacts (Create / Read / List / Update / Delete).
    this.tools.register(makeArtifactCreateTool(this.artifacts));
    this.tools.register(makeArtifactReadTool(this.artifacts));
    this.tools.register(makeArtifactListTool(this.artifacts));
    this.tools.register(makeArtifactUpdateTool(this.artifacts));
    this.tools.register(makeArtifactDeleteTool(this.artifacts));
    this.tools.register(
      makeDocumentWriteTool(config.workspaceRoot, this.outputArtifacts, {
        agentic: {
          llm: this.llm,
          resolveModel: () => this.resolveRuntimeModel(),
          fallbackModel: config.model,
        },
      }),
    );
    this.tools.register(makeKnowledgeSearchTool({ name: "knowledge-search" }));
    this.tools.register(makeKnowledgeSearchTool({ name: "KnowledgeSearch" }));
    this.tools.register(makeBrowserTool(config.workspaceRoot));
    this.tools.register(makeSocialBrowserTool(config.workspaceRoot));
    this.tools.register(makeWebSearchTool({ name: "web-search" }));
    this.tools.register(makeWebSearchTool({ name: "WebSearch" }));
    this.tools.register(makeWebSearchTool({ name: "web_search" }));
    this.tools.register(makeWebFetchTool());
    this.tools.register(makeClockTool());
    this.tools.register(makeDateRangeTool());
    this.tools.register(makeCalculationTool());
    this.tools.register(makeSpreadsheetWriteTool(config.workspaceRoot, this.outputArtifacts));
    this.tools.register(
      makeMissionLedgerTool({
        client: this.missionClient,
        getSourceChannel: getSourceChannelForTool,
      }),
    );
    const sendFileToSourceChannel = async (
      channel: ChannelRef,
      filePath: string,
      caption: string | undefined,
      mode: "document" | "photo",
    ): Promise<ChannelDeliveryReceipt> =>
      this.sendFileToSourceChannel(channel, filePath, caption, mode);
    this.tools.register(
      makeFileSendTool({
        workspaceRoot: config.workspaceRoot,
        binDir: path.join(config.workspaceRoot, "..", "bin"),
        gatewayToken: config.gatewayToken,
        botId: config.botId,
        chatProxyUrl: config.chatProxyUrl ?? "",
        getSourceChannel: getSourceChannelForTool,
        sendFile: sendFileToSourceChannel,
      }),
    );
    this.tools.register(
      makeFileDeliverTool({
        workspaceRoot: config.workspaceRoot,
        outputRegistry: this.outputArtifacts,
        chatProxyUrl: config.chatProxyUrl ?? "",
        gatewayToken: config.gatewayToken,
        getSourceChannel: getSourceChannelForTool,
        sendFile: sendFileToSourceChannel,
      }),
    );
    // Cron suite. CronCreate captures the turn's source channel at
    // creation time so the LLM doesn't get to pick the target — it
    // inherits whatever channel started the turn (web / app / telegram
    // / discord). The source-channel lookup is wired via a closure
    // over the active turn map.
    this.tools.register(
      makeCronCreateTool({
        scheduler: this.crons,
        botId: config.botId,
        userId: config.userId,
        getSourceChannel: getSourceChannelForTool,
        getSession: (ctx) => {
          const turn = this.activeTurns.get(ctx.turnId);
          return turn?.session ?? null;
        },
      }),
    );
    this.tools.register(makeCronListTool(this.crons));
    this.tools.register(makeCronUpdateTool(this.crons));
    this.tools.register(makeCronDeleteTool(this.crons));
    // Coding Discipline — CommitCheckpoint. Always registered; hook-
    // gated so it errors early when `discipline.git === false` rather
    // than being invisible (cleaner LLM tool-discovery path).
    this.tools.register(
      makeCommitCheckpointTool({
        workspaceRoot: config.workspaceRoot,
        agent: {
          getSessionDiscipline: (sessionKey) =>
            this.getSessionDiscipline(sessionKey),
          getSessionCounter: (sessionKey) =>
            this.getDisciplineCounter(sessionKey),
        },
      }),
    );
    // §7.5 AskUserQuestion + §7.2 Plan mode.
    this.tools.register(makeAskUserQuestionTool());
    this.tools.register(
      makeEnterPlanModeTool((turnId) => {
        const turn = this.activeTurns.get(turnId);
        if (!turn) return null;
        const controller: PlanModeEntryController = {
          enterPlanMode: (input) => turn.session.planLifecycle.enterPlanMode(input),
        };
        return controller;
      }),
    );
    this.tools.register(
      makeExitPlanModeTool((turnId) => {
        const turn = this.activeTurns.get(turnId);
        if (!turn) return null;
        const controller: PlanModeController = {
          isPlanMode: () => turn.isPlanMode(),
          submitPlan: (input) => turn.session.planLifecycle.submitPlan(input),
        };
        return controller;
      }),
    );
    this.tools.register(makeSwitchToActModeTool(this.tools));
  }

  async resolveRuntimeModel(): Promise<string> {
    const resolver = (this.llm as {
      resolveRuntimeModel?: (fallbackModel?: string) => Promise<string>;
    }).resolveRuntimeModel;
    return typeof resolver === "function"
      ? await resolver.call(this.llm, this.config.model)
      : this.config.model;
  }

  /** Session.runTurn registers the turn so the HTTP endpoint +
   * ExitPlanMode tool can reach it. Paired with unregisterTurn(). */
  registerTurn(turn: Turn): void {
    this.activeTurns.set(turn.meta.turnId, turn);
  }

  unregisterTurn(turnId: string): void {
    this.activeTurns.delete(turnId);
  }

  private getSourceChannelForTool(ctx: { turnId: string }): ChannelRef | null {
    const turn = this.activeTurns.get(ctx.turnId);
    return turn?.session.meta.channel ?? null;
  }

  private async sendFileToSourceChannel(
    channel: ChannelRef,
    filePath: string,
    caption: string | undefined,
    mode: "document" | "photo",
  ): Promise<ChannelDeliveryReceipt> {
    if (channel.type !== "telegram" && channel.type !== "discord") {
      throw new Error(`direct file delivery unsupported for channel=${channel.type}`);
    }
    const adapter = this.channelAdapters.find((candidate) => candidate.kind === channel.type);
    if (!adapter) {
      throw new Error(`${channel.type} adapter not configured`);
    }
    if (mode === "photo") {
      return adapter.sendPhoto(channel.channelId, filePath, caption);
    }
    return adapter.sendDocument(channel.channelId, filePath, caption);
  }

  /** Look up a live turn. Used by HttpServer's ask-response endpoint. */
  getActiveTurn(turnId: string): Turn | undefined {
    return this.activeTurns.get(turnId);
  }

  /**
   * True when a turn is currently streaming for `sessionKey`. Used by
   * the /v1/chat/inject route (#86) to decide 409 vs enqueue. Cheap —
   * iterates at most activeTurns.size entries (bounded by concurrent
   * streams per bot, ~1-5 in practice).
   */
  hasActiveTurnForSession(sessionKey: string): boolean {
    for (const turn of this.activeTurns.values()) {
      if (turn.meta.sessionKey === sessionKey) return true;
    }
    return false;
  }

  private turnSnapshotService?: import("./checkpoint/TurnSnapshotService.js").TurnSnapshotService;

  setTurnSnapshotService(
    svc: import("./checkpoint/TurnSnapshotService.js").TurnSnapshotService,
  ): void {
    this.turnSnapshotService = svc;
  }

  getTurnSnapshotService(): import("./checkpoint/TurnSnapshotService.js").TurnSnapshotService | undefined {
    return this.turnSnapshotService;
  }

  markGoalMissionCancelled(missionId: string): boolean {
    if (this.cancelledGoalMissionIds.has(missionId)) return false;
    this.cancelledGoalMissionIds.add(missionId);
    return true;
  }

  isGoalMissionCancelled(missionId: string): boolean {
    return this.cancelledGoalMissionIds.has(missionId);
  }

  cancelGoalMission(missionId: string): void {
    this.markGoalMissionCancelled(missionId);
    for (const turn of this.activeTurns.values()) {
      const metadata = turn.userMessage.metadata;
      if (metadata?.goalMode === true && metadata.missionId === missionId) {
        turn.requestInterrupt(false, "api");
      }
    }
  }

  /**
   * Coding Discipline accessors — returned to hook + tool delegates.
   * Public so the hooks/builtin wiring + CommitCheckpoint factory can
   * consult them without reaching for the Session registry directly.
   */
  getSessionDiscipline(sessionKey: string): Discipline | null {
    const s = this.sessions.get(sessionKey);
    if (!s) return null;
    return s.meta.discipline ?? this.disciplineDefault;
  }

  setSessionDiscipline(sessionKey: string, next: Discipline): void {
    const s = this.sessions.get(sessionKey);
    if (!s) return;
    s.meta.discipline = next;
  }

  getDisciplineCounter(sessionKey: string): DisciplineSessionCounter {
    let c = this.disciplineCounters.get(sessionKey);
    if (!c) {
      c = { sourceMutations: 0, testMutations: 0, dirtyFilesSinceCommit: 0 };
      this.disciplineCounters.set(sessionKey, c);
    }
    return c;
  }

  async deliverBackgroundTaskResult(
    input: BackgroundTaskDeliveryInput,
  ): Promise<boolean> {
    const text = formatBackgroundTaskDelivery(input);
    if (!text) {
      return false;
    }

    const session = this.sessions.get(input.sessionKey);
    if (!session) {
      agentLogger.warn("background_delivery_skipped", {
        taskId: input.taskId,
        reason: "session_not_live",
        sessionKey: input.sessionKey,
      });
      return false;
    }

    const channel = session.meta.channel;
    try {
      if (channel.type === "app") {
        if (!this.webAppAdapter) {
          agentLogger.warn("background_delivery_skipped", {
            taskId: input.taskId,
            reason: "webapp_adapter_not_configured",
          });
          return false;
        }
        await this.webAppAdapter.send({
          chatId: channel.channelId,
          text,
        });
        return true;
      }

      if (channel.type === "telegram" || channel.type === "discord") {
        const adapter = this.channelAdapters.find((a) => a.kind === channel.type);
        if (!adapter) {
          agentLogger.warn("background_delivery_skipped", {
            taskId: input.taskId,
            reason: "adapter_not_configured",
            channelType: channel.type,
          });
          return false;
        }
        await adapter.send({
          chatId: channel.channelId,
          text,
        });
        return true;
      }

      agentLogger.warn("background_delivery_skipped", {
        taskId: input.taskId,
        reason: "unsupported_channel",
        channelType: channel.type,
      });
      return false;
    } catch (err) {
      agentLogger.warn("background_delivery_failed", {
        taskId: input.taskId,
        channelType: channel.type,
        channelId: channel.channelId,
        error: (err as Error).message,
      });
      return false;
    }
  }

  /**
   * Entry point for SpawnAgent (§7.12.d) child turns. Kept here — rather
   * than inside Turn.ts — so the parent Turn's atomic-commit loop stays
   * untouched. Returns the child's final text + tool trail summary.
   */
  async spawnChildTurn(opts: SpawnChildOptions): Promise<SpawnChildResult> {
    return runChildAgentLoop(this, opts);
  }

  async start(): Promise<void> {
    if (this.started) return;
    this.started = true;

    // T2-10 — repopulate the background-task map from the PVC. Never
    // blocks startup on a read failure; hydrate() is idempotent.
    try {
      await this.backgroundTasks.hydrate();
    } catch (err) {
      agentLogger.warn("background_tasks_hydrate_failed", { error: (err as Error).message });
    }

    // Coding Discipline — read `.discipline.yaml` if present, seed
    // Agent.disciplineDefault. Failure here is non-fatal; we fall
    // through to the baked-in DEFAULT_DISCIPLINE.
    try {
      const cfg = await loadDisciplineConfig(this.config.workspaceRoot);
      if (cfg) {
        this.disciplineDefault = cfg;
        agentLogger.info("discipline_config_loaded", {
          tdd: cfg.tdd,
          git: cfg.git,
          enforcement: cfg.requireCommit,
        });
      }
    } catch (err) {
      agentLogger.warn("discipline_config_load_failed", { error: (err as Error).message });
    }

    // Kevin's A/A/A decision: `git init` always runs on Agent.start,
    // regardless of the discipline.git flag. The tiny overhead is
    // accepted so CommitCheckpoint always has somewhere to write when
    // discipline promotes mid-session. Idempotent — skips when already
    // initialised; logs + continues if the `git` binary is missing.
    await this.maybeInitGit();

    const readSessionTranscript = async (sessionKey: string) => {
      const s = this.sessions.get(sessionKey);
      if (!s) return null;
      try {
        return await s.transcript.readAll();
      } catch {
        return null;
      }
    };

    // Phase 2c: register built-in hooks (AEF module ports + hipocampus checkpoint).
    // T2-08 — pass a delegate wired to this Agent so the auto-approval
    // hook can look up the running session's permissionMode and
    // consult the tool registry for `tool.dangerous` metadata.
    const hookResult = registerBuiltinHooks(this.hooks, {
      workspaceRoot: this.config.workspaceRoot,
      sessionsDir: this.sessionsDir,
      policyKernel: this.policy,
      debugWorkflow: this.debugWorkflow,
      qmdManager: this.qmdManager,
      hipocampus: this.hipocampus,
      autoApprovalAgent: {
        getSessionPermissionMode: (sessionKey) => {
          const s = this.sessions.get(sessionKey);
          return s ? s.getPermissionMode() : null;
        },
        resolveTool: (name) => this.tools.resolve(name),
      },
      disciplineAgent: {
        getSessionDiscipline: (sessionKey) =>
          this.getSessionDiscipline(sessionKey),
        setSessionDiscipline: (sessionKey, next) =>
          this.setSessionDiscipline(sessionKey, next),
        getSessionCounter: (sessionKey) =>
          this.getDisciplineCounter(sessionKey),
        // Kevin's A/A/A rule #1 — surface "coding hard-mode skill
        // active" so the classifier hook can promote soft → hard on
        // coding-labeled turns for bots that bundle a coding agent skill.
        isCodingAgentSkillActive: () =>
          isCodingHardModeSkillActive(this.tools),
      },
      midTurnInjectorAgent: {
        getSession: (sessionKey) => this.sessions.get(sessionKey),
      },
      // Deterministic file delivery interceptor — Haiku-classified,
      // bypasses the main model for "send this file" requests.
      fileDelivery: {
        workspaceRoot: this.config.workspaceRoot,
        gatewayToken: this.config.gatewayToken,
        botId: this.config.botId,
        chatProxyUrl: this.config.chatProxyUrl ?? "",
        telegramBotToken: this.config.telegramBotToken,
        getSourceChannel: (ctx) => this.getSourceChannelForTool(ctx),
        sendFile: (channel, filePath, caption, mode) =>
          this.sendFileToSourceChannel(channel, filePath, caption, mode),
      },
      // Superpowers plan-mode auto-trigger — reads permissionMode to
      // skip the nudge when the user is already in plan mode.
      planModeAutoTriggerAgent: {
        getSessionPermissionMode: (sessionKey) => {
          const s = this.sessions.get(sessionKey);
          return s ? s.getPermissionMode() : null;
        },
        enterPlanMode: async (sessionKey, turnId) => {
          const s = this.sessions.get(sessionKey);
          if (!s) return;
          await s.planLifecycle.enterPlanMode({ turnId });
        },
      },
      clarificationGateAgent: {
        askClarification: async (input) => {
          const s = this.sessions.get(input.sessionKey);
          if (!s) {
            throw new Error(`session not found for clarification: ${input.sessionKey}`);
          }
          const choices = input.choices.slice(0, 4).map((label, index) => ({
            id: `choice_${index + 1}`,
            label,
          }));
          const request = await s.controlRequests.create({
            kind: "user_question",
            turnId: input.turnId,
            sessionKey: input.sessionKey,
            channelName: s.meta.channel.channelId,
            source: "system",
            prompt: input.question,
            proposedInput: {
              reason: input.reason,
              riskIfAssumed: input.riskIfAssumed,
              choices,
              allowFreeText: input.allowFreeText || choices.length === 0,
            },
            expiresAt: Date.now() + 5 * 60_000,
            idempotencyKey: `clarification:${input.turnId}`,
          });
          input.onRequest?.(request);
          const resolved = await s.waitForControlRequestResolution(
            request.requestId,
            input.signal,
          );
          return { request, resolved };
        },
      },
      // Superpowers onboarding nudge — reads session.meta for
      // onboarded/onboardingDeclines + budget stats.
      onboardingNeededCheckAgent: {
        getSession: (sessionKey) => this.sessions.get(sessionKey),
      },
      // #81 — inline task notification delegate. Returns + drains the
      // per-session queue held by BackgroundTaskRegistry.
      taskNotifierAgent: {
        drainForSession: (sessionKey) =>
          this.backgroundTasks.drainForSession(sessionKey),
      },
      // Layer 3 (pre-refusal verifier) — read the session transcript
      // from disk so the hook can see tool_call entries from the
      // still-running turn. beforeCommit runs after all tool calls
      // have been persisted to the JSONL.
      preRefusalVerifierAgent: {
        readSessionTranscript,
      },
      // Anti-hallucination hooks (priority 82 + 83) — same transcript
      // reader pattern as preRefusalVerifier. Without these delegates,
      // Mode A (tool-result grounding) and resourceExistenceChecker
      // fall back to the empty ctx.transcript and fail open.
      factGroundingAgent: {
        readSessionTranscript,
      },
      resourceCheckAgent: {
        readSessionTranscript,
      },
      // Reliability kernel gates use the same transcript-reader path
      // so "complete/fixed/verified" claims are checked against
      // current-turn tool calls instead of model memory.
      completionEvidenceAgent: {
        readSessionTranscript,
      },
      memoryMutationAgent: {
        readSessionTranscript,
      },
      fileEditSafetyAgent: {
        readSessionTranscript,
      },
      codingVerificationAgent: {
        getSessionDiscipline: (sessionKey) =>
          this.getSessionDiscipline(sessionKey),
        readSessionTranscript,
      },
      goalProgressGateAgent: {
        readSessionTranscript,
      },
      taskContractAgent: {
        readSessionTranscript,
      },
      artifactDeliveryAgent: {
        readSessionTranscript,
      },
      cronMetaAgent: {
        readSessionTranscript,
      },
      resourceBoundaryAgent: {
        readSessionTranscript,
      },
      userHarnessRuleAgent: {
        readSessionTranscript,
      },
      outputDeliveryAgent: {
        listUndelivered: async (sessionKey, turnId) => {
          const pending = await this.outputArtifacts.listUndelivered(sessionKey, turnId);
          return pending.map((artifact) => ({
            artifactId: artifact.artifactId,
            filename: artifact.filename,
          }));
        },
      },
      // Layer 4 (session resume) — snapshot = transcript +
      // last-activity anchor; append = queue the seed as hidden
      // first-iteration context. This must not use mid-turn injection:
      // post-reprovision resume context has higher authority than a
      // user follow-up and must be visible before the first LLM call.
      sessionResumeAgent: {
        getResumeSnapshot: async (sessionKey) => {
          const s = this.sessions.get(sessionKey);
          if (!s) return null;
          try {
            const entries = await s.transcript.readAll();
            return {
              transcript: entries,
              lastActivityAt: s.meta.lastActivityAt,
            };
          } catch {
            return null;
          }
        },
        appendResumeSeed: async (sessionKey, seed) => {
          const s = this.sessions.get(sessionKey);
          if (!s) return;
          s.meta.resumeSeededAt = Date.now();
          s.enqueueHiddenContext(seed);
        },
      },
    });
    agentLogger.info("builtin_hooks_registered", {
      registered: hookResult.registered,
      skipped: hookResult.skipped,
    });

    if (hookResult.turnSnapshotService) {
      this.setTurnSnapshotService(hookResult.turnSnapshotService);
    }

    try {
      await this.reloadWorkspaceSkills();
    } catch (err) {
      agentLogger.warn("skill_load_failed", { error: (err as Error).message });
    }

    // Cron scheduler — hydrate persisted cron records then wire the
    // fire handler + start the 30s tick-loop. The fire handler
    // synthesises a turn on the cron's deliveryChannel so delivery is
    // runtime-enforced (see CronCreate docstring for context).
    try {
      await this.crons.hydrate();
      this.crons.setFireHandler((record) => this.fireCron(record));
      this.crons.start();
      agentLogger.info("crons_hydrated", {
        count: this.crons.list().length,
        tickerStarted: true,
      });
    } catch (err) {
      agentLogger.warn("cron_hydrate_failed", { error: (err as Error).message });
    }

    if (missionActionsEnabled()) {
      try {
        await this.missionActionReconciler.start();
        agentLogger.info("mission_reconciler_started");
      } catch (err) {
        agentLogger.warn("mission_reconciler_start_failed", { error: (err as Error).message });
      }
    }

    // Native hipocampus memory — qmd index + compaction engine + daily cron.
    try {
      await this.hipocampus.start(); // fail-open
      this.compactionEngine = this.hipocampus.getCompactionEngine() as CompactionEngine;
      // Internal daily cron for compaction maintenance
      this.crons.registerInternal({
        name: "hipocampus-maintenance",
        schedule: "0 3 * * *", // daily at 03:00
        handler: async () => {
          try {
            await this.hipocampus.compact();
          } catch (err) {
            agentLogger.warn("hipocampus_cron_failed", { error: (err as Error).message });
          }
        },
      });
      // Register compactor + flush hooks now that engine exists.
      // These hooks were skipped in the initial registerBuiltinHooks call
      // because compactionEngine wasn't available yet.
      const compactorHook = (await import("./hooks/builtin/hipocampusCompactor.js")).makeHipocampusCompactorHook(
        this.compactionEngine,
        this.qmdManager,
        undefined, // use default flushMemory
        this.config.workspaceRoot,
      );
      this.hooks.register(compactorHook);
      const flushHook = (await import("./hooks/builtin/hipocampusFlush.js")).makeHipocampusFlushHook(
        this.config.workspaceRoot,
      );
      this.hooks.register(flushHook);
      agentLogger.info("hipocampus_started", {
        qmd: this.qmdManager.isReady(),
        vector: (process.env.MAGI_VECTOR_SEARCH ?? "off").trim().toLowerCase() === "on",
        compactorFlush: "registered",
      });
    } catch (err) {
      agentLogger.warn("hipocampus_init_failed", { error: (err as Error).message });
    }

    // C1 — channel adapters. Instantiated + started for each configured
    // token. Factory override is accepted so tests (+ future alt-backed
    // adapters like a Baileys WhatsApp stack) can plug in without the
    // Agent reaching for network.
    await this.startChannelAdapters();

    // §7.15 — outbound-only web/app push adapter. No token-gated
    // decision because the adapter's constructor validates everything.
    this.startWebAppAdapter();

    // Phase 1b: session registry is hydrated lazily on first ingress.
  }

  /**
   * Reload prompt/script skills from the bot workspace without restarting the
   * pod. Used during startup and by the admin skills refresh endpoint.
   */
  async reloadWorkspaceSkills(): Promise<SkillReloadResult> {
    const skillsDir = path.join(this.config.workspaceRoot, "skills");
    const superpowersDir =
      this.config.superpowersSkillsDir ?? resolveDefaultSuperpowersDir();
    const skillRoots = [
      { skillsDir, workspaceRoot: this.config.workspaceRoot },
      ...(path.resolve(superpowersDir) === path.resolve(skillsDir)
        ? []
        : [{ skillsDir: superpowersDir, workspaceRoot: superpowersDir }]),
    ];
    const loaded = await this.tools.loadSkillRoots(skillRoots, {
      trustedSkillRoots: splitPathListEnv(
        process.env.MAGI_TRUSTED_SKILL_ROOTS ??
          process.env.MAGI_TRUSTED_SKILL_ROOTS,
      ),
      trustedSkillDirs: splitPathListEnv(
        process.env.MAGI_TRUSTED_SKILL_DIRS ??
          process.env.MAGI_TRUSTED_SKILL_DIRS,
      ),
    });
    const rpt = this.tools.skillReport();
    const issues = rpt?.issues.length ?? 0;
    let runtimeHooks = 0;
    if (rpt && !this.skillRuntimeHooksRegistered) {
      runtimeHooks = registerSkillRuntimeHooks(this.hooks, rpt.runtimeHooks);
      if (rpt.runtimeHooks.length > 0) {
        this.skillRuntimeHooksRegistered = true;
      }
    }
    this.restoreNativeToolOverrides();
    agentLogger.info("skills_loaded", {
      loaded,
      issues,
      runtimeHooks,
      roots: skillRoots.map((root) => root.skillsDir),
    });
    return { loaded, issues, runtimeHooks };
  }

  private restoreNativeToolOverrides(): void {
    // Keep native tools deterministic even when a workspace ships
    // prompt-only skills with the same names.
    this.tools.replace(makeKnowledgeSearchTool({ name: "knowledge-search" }));
    this.tools.replace(makeKnowledgeSearchTool({ name: "KnowledgeSearch" }));
    this.tools.replace(makeBrowserTool(this.config.workspaceRoot));
    this.tools.replace(makeSocialBrowserTool(this.config.workspaceRoot));
    this.tools.replace(makeWebSearchTool({ name: "web-search" }));
    this.tools.replace(makeWebSearchTool({ name: "WebSearch" }));
    this.tools.replace(makeWebSearchTool({ name: "web_search" }));
    this.tools.replace(makeWebFetchTool());
    this.tools.replace(makePackageDependencyResolveTool());
    this.tools.replace(makeExternalSourceCacheTool());
    this.tools.replace(makeExternalSourceReadTool());
    this.tools.replace(makePatchApplyTool(this.config.workspaceRoot));
    this.tools.replace(makeMemoryRedactTool(this.config.workspaceRoot));
    this.tools.replace(makeCodeWorkspaceTool(this.config.workspaceRoot));
    this.tools.replace(makeCodeSymbolSearchTool(this.config.workspaceRoot));
    this.tools.replace(makeCodeIntelligenceTool(this.config.workspaceRoot));
    this.tools.replace(makeCodeDiagnosticsTool(this.config.workspaceRoot));
    this.tools.replace(makeRepositoryMapTool(this.config.workspaceRoot));
    this.tools.replace(makeCodingBenchmarkTool(this.config.workspaceRoot));
    this.tools.replace(makeSpawnWorktreeApplyTool(this.config.workspaceRoot));
    this.tools.replace(makeSafeCommandTool(this.config.workspaceRoot));
    this.tools.replace(makeProjectVerificationPlannerTool(this.config.workspaceRoot));
    this.tools.replace(makeGitDiffTool(this.config.workspaceRoot));
    this.tools.replace(makeRepoTaskStateTool(this.config.workspaceRoot));
    this.tools.replace(makeClockTool());
    this.tools.replace(makeDateRangeTool());
    this.tools.replace(makeCalculationTool());
    this.tools.replace(makeSwitchToActModeTool(this.tools));
  }

  /** Construct + start any channel adapters whose tokens are set. */
  private async startChannelAdapters(): Promise<void> {
    const adapters: ChannelAdapter[] = [];
    if (this.config.telegramBotToken) {
      const factory =
        this.config.telegramAdapterFactory ??
        ((token, wsRoot) =>
          new TelegramPoller({ botToken: token, workspaceRoot: wsRoot }));
      adapters.push(
        factory(this.config.telegramBotToken, this.config.workspaceRoot),
      );
    }
    if (this.config.discordBotToken) {
      const factory =
        this.config.discordAdapterFactory ??
        ((token: string) => new DiscordClient({ botToken: token, workspaceRoot: this.config.workspaceRoot }));
      adapters.push(factory(this.config.discordBotToken));
    }
    for (const adapter of adapters) {
      adapter.onInboundMessage(async (msg) => {
        try {
          await dispatchInbound(this, adapter, msg);
        } catch (err) {
          agentLogger.warn("channel_dispatch_failed", {
            kind: adapter.kind,
            error: (err as Error).message,
          });
        }
      });
      try {
        await adapter.start();
        this.channelAdapters.push(adapter);
        agentLogger.info("channel_adapter_started", { kind: adapter.kind });
      } catch (err) {
        agentLogger.warn("channel_adapter_start_failed", {
          kind: adapter.kind,
          error: (err as Error).message,
        });
      }
    }
  }

  /**
   * §7.15 — construct the outbound-only WebAppChannelAdapter when the
   * bot was provisioned with the push endpoint + shared HMAC key.
   * Failures are logged and swallowed so a misconfigured HMAC does
   * not block Telegram / Discord delivery paths from starting.
   */
  private startWebAppAdapter(): void {
    const url = this.config.webAppPushEndpointUrl;
    const key = this.config.webAppPushHmacKey;
    if (!url || !key) {
      return;
    }
    try {
      const factory =
        this.config.webAppAdapterFactory ??
        ((cfg) => new WebAppChannelAdapter(cfg));
      const adapter = factory({
        pushEndpointUrl: url,
        hmacKey: key,
        gatewayToken: this.config.gatewayToken,
        botId: this.config.botId,
        userId: this.config.userId,
      });
      // No inbound handler — this adapter is outbound-only.
      // start() is a no-op on WebAppChannelAdapter but we call it for
      // lifecycle symmetry with Telegram/Discord.
      void adapter.start();
      this.webAppAdapter = adapter;
      agentLogger.info("webapp_push_started");
    } catch (err) {
      agentLogger.warn("webapp_push_start_failed", { error: (err as Error).message });
    }
  }

  async stop(): Promise<void> {
    if (!this.started) return;
    this.started = false;

    // #82 — close every live in-memory session so pending injections
    // are drained, AbortControllers fire for any still-streaming turn,
    // and observability emits `session_closed`. Transcripts live on
    // the PVC and persist across this — close() only releases
    // in-memory resources + session-scoped crons. Errors are logged
    // and do not block the rest of the shutdown path.
    const liveSessionKeys = [...this.sessions.keys()];
    for (const key of liveSessionKeys) {
      try {
        await this.closeSession(key, "shutdown");
      } catch (err) {
        agentLogger.warn("session_close_on_shutdown_failed", {
          sessionKey: key,
          error: (err as Error).message,
        });
      }
    }

    this.crons.stop();
    this.missionActionReconciler.stop();
    await this.hipocampus.stop();
    // C1 — stop all channel adapters. Each adapter is responsible for
    // aborting its own long-polls / closing gateway sockets.
    for (const adapter of this.channelAdapters.splice(0)) {
      try {
        await adapter.stop();
      } catch (err) {
        agentLogger.warn("channel_adapter_stop_failed", {
          kind: adapter.kind,
          error: (err as Error).message,
        });
      }
    }
    // §7.15 — outbound-only web/app adapter. No-op stop but exercised
    // for symmetry; release the reference so tests can assert teardown.
    if (this.webAppAdapter) {
      try {
        await this.webAppAdapter.stop();
      } catch (err) {
        agentLogger.warn("webapp_adapter_stop_failed", { error: (err as Error).message });
      }
      this.webAppAdapter = null;
    }
    // Phase 2: drain inflight turns, flush transcript buffers.
    // Current implementation relies on graceful SIGTERM +
    // HTTP shutdown to quiesce running turns.
  }

  /**
   * Cron fire path. Given a CronRecord, synthesise a user message
   * carrying the cron prompt + fire it through the owning session on
   * the cron's deliveryChannel (NOT whatever the bot might prefer).
   *
   * Delivery: the default SseWriter would require a live HTTP client,
   * so crons run with a stub writer. After the synthetic turn commits,
   * the runtime forwards the committed assistant text to the captured
   * delivery channel itself:
   *   - app → chat-proxy `/v1/bot-channels/post` (durable channel history)
   *   - telegram/discord → native channel adapter `send()`
   * This closes the "cron fired but nothing arrived" gap when the
   * model simply answers normally instead of explicitly invoking a
   * separate delivery tool.
   */
  private async createCronMissionRun(
    record: CronRecord,
    sessionKey: string,
  ): Promise<CronMissionLink | null> {
    if (!runtimeMissionsEnabled()) return null;
    try {
      const mission = await this.missionClient.createMission({
        channelType: record.deliveryChannel.type as MissionChannelType,
        channelId: record.deliveryChannel.channelId,
        kind: record.mode === "script" ? "script_cron" : "cron",
        title: truncateMissionText(record.description?.trim() || `Cron ${record.cronId}`, 240),
        summary: truncateMissionText(record.prompt, 500),
        status: "running",
        createdBy: "cron",
        idempotencyKey: `cron:${record.cronId}`,
        metadata: {
          cronId: record.cronId,
          expression: record.expression,
          durable: record.durable,
        },
      });
      record.missionId = mission.id;
      const run = await this.missionClient.createRun(mission.id, {
        triggerType: record.mode === "script" ? "script_cron" : "cron",
        status: "running",
        sessionKey,
        cronId: record.cronId,
        metadata: {
          expression: record.expression,
          deliveryChannel: record.deliveryChannel,
        },
      });
      const runId = typeof run.id === "string" ? run.id : undefined;
      if (runId) record.missionRunId = runId;
      return { missionId: mission.id, ...(runId ? { missionRunId: runId } : {}) };
    } catch (err) {
      agentLogger.warn("cron_mission_create_failed", {
        cronId: record.cronId,
        error: (err as Error).message,
      });
      return null;
    }
  }

  private async appendCronMissionEvent(
    link: CronMissionLink | null,
    input: {
      eventType: "completed" | "failed";
      message?: string;
      payload?: Record<string, unknown>;
    },
  ): Promise<void> {
    if (!link) return;
    try {
      await this.missionClient.appendEvent(link.missionId, {
        ...(link.missionRunId ? { runId: link.missionRunId } : {}),
        actorType: "cron",
        eventType: input.eventType,
        ...(input.message ? { message: input.message } : {}),
        payload: input.payload ?? {},
      });
    } catch (err) {
      agentLogger.warn("cron_mission_event_failed", { missionId: link.missionId, error: (err as Error).message });
    }
  }

  private shouldDeliverScriptCronStdout(
    record: CronRecord,
    result: ScriptCronResult,
  ): boolean {
    const policy = record.deliveryPolicy ?? "stdout_non_empty";
    if (policy === "never") return false;
    if (result.stdout.trim().length > 0) return true;
    if (record.quietOnEmptyStdout !== false) return false;
    return policy === "always";
  }

  private async fireScriptCron(record: CronRecord): Promise<void> {
    if (!scriptCronEnabled()) {
      throw new Error("script cron disabled");
    }
    if (!record.scriptPath) {
      throw new Error("script cron missing scriptPath");
    }
    const sessionKey = `agent:cron:${record.deliveryChannel.type}:${record.deliveryChannel.channelId}:${record.cronId}`;
    const missionLink = await this.createCronMissionRun(record, sessionKey);
    try {
      const result = await runScriptCron({
        workspaceRoot: this.config.workspaceRoot,
        scriptPath: record.scriptPath,
        timeoutMs: record.timeoutMs ?? 60_000,
      });
      if (result.timedOut) {
        throw new Error(`script cron timed out after ${record.timeoutMs ?? 60_000}ms`);
      }
      if (result.code !== 0) {
        throw new Error(
          `script cron exited with code ${result.code}${result.stderr ? `: ${result.stderr.trim()}` : ""}`,
        );
      }
      if (this.shouldDeliverScriptCronStdout(record, result)) {
        await this.deliverCronAssistantText(record.deliveryChannel, result.stdout);
      }
      await this.appendCronMissionEvent(missionLink, {
        eventType: "completed",
        payload: {
          cronId: record.cronId,
          mode: "script",
          stdoutChars: result.stdout.length,
          stderrChars: result.stderr.length,
        },
      });
    } catch (err) {
      await this.appendCronMissionEvent(missionLink, {
        eventType: "failed",
        message: (err as Error).message,
        payload: { cronId: record.cronId, mode: "script" },
      });
      throw err;
    }
  }

  private async fireCron(record: CronRecord): Promise<void> {
    if (record.mode === "script") {
      await this.fireScriptCron(record);
      return;
    }
    const sessionKey = `agent:cron:${record.deliveryChannel.type}:${record.deliveryChannel.channelId}:${record.cronId}`;
    const session = await this.getOrCreateSession(sessionKey, record.deliveryChannel);
    const { StubSseWriter } = await import("./transport/SseWriter.js");
    const stubSse = new StubSseWriter();
    const missionLink = await this.createCronMissionRun(record, sessionKey);
    try {
      const turnResult = await session.runTurn(
        {
          text: record.prompt,
          receivedAt: Date.now(),
          metadata: { source: "cron", cronId: record.cronId },
        },
        stubSse,
      );
      const assistantText = turnResult.assistantText.trim();
      if (assistantText.length > 0) {
        const deliveryAuditData = {
          cronId: record.cronId,
          channelType: record.deliveryChannel.type,
          channelId: record.deliveryChannel.channelId,
          textChars: assistantText.length,
          textBytes: Buffer.byteLength(assistantText, "utf8"),
        };
        await this.auditLog.append(
          "cron_delivery_started",
          sessionKey,
          turnResult.meta.turnId,
          deliveryAuditData,
        );
        try {
          await this.deliverCronAssistantText(record.deliveryChannel, assistantText);
          await this.auditLog.append(
            "cron_delivery_succeeded",
            sessionKey,
            turnResult.meta.turnId,
            deliveryAuditData,
          );
        } catch (err) {
          await this.auditLog.append(
            "cron_delivery_failed",
            sessionKey,
            turnResult.meta.turnId,
            {
              ...deliveryAuditData,
              error: (err as Error).message,
            },
          );
          throw err;
        }
      }
      await this.appendCronMissionEvent(missionLink, {
        eventType: "completed",
        payload: {
          cronId: record.cronId,
          assistantTextChars: assistantText.length,
        },
      });
    } catch (err) {
      await this.appendCronMissionEvent(missionLink, {
        eventType: "failed",
        message: (err as Error).message,
        payload: { cronId: record.cronId },
      });
      throw err;
    } finally {
      // #82 — non-durable cron fire: close the synthetic session so
      // its in-memory state + pendingInjections + any session-scoped
      // crons it created don't leak across pod lifetime. Durable
      // crons keep firing via the scheduler's own record — close()
      // here only sweeps the one-shot Session instance we built to
      // host this invocation's Turn. Errors are swallowed so a close
      // failure never masks a genuine turn error.
      if (record.durable === false) {
        try {
          await this.closeSession(sessionKey, "cron_complete");
        } catch (err) {
          agentLogger.warn("cron_close_session_failed", { cronId: record.cronId, error: (err as Error).message });
        }
      }
    }
  }

  private async deliverCronAssistantText(
    channel: ChannelRef,
    text: string,
  ): Promise<void> {
    await this.deliverAssistantTextToChannel(channel, text, "cron");
  }

  async deliverAssistantTextToChannel(
    channel: ChannelRef,
    text: string,
    source = "agent",
  ): Promise<void> {
    if (channel.type === "app") {
      if (!this.config.chatProxyUrl) {
        throw new Error(`${source} app delivery failed: no chatProxyUrl configured`);
      }
      const url = `${this.config.chatProxyUrl.replace(/\/$/, "")}/v1/bot-channels/post`;
      const resp = await fetch(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${this.config.gatewayToken}`,
        },
        body: JSON.stringify({
          channel: channel.channelId,
          content: text,
        }),
      });
      if (!resp.ok) {
        const errText = await resp.text().catch(() => "");
        throw new Error(
          `${source} app delivery failed: HTTP ${resp.status} ${errText.slice(0, 200)}`,
        );
      }
      return;
    }

    if (channel.type === "telegram" || channel.type === "discord") {
      const adapter = this.channelAdapters.find((a) => a.kind === channel.type);
      if (!adapter) {
        throw new Error(`${source} ${channel.type} adapter not configured`);
      }
      await adapter.send({
        chatId: channel.channelId,
        text,
      });
      return;
    }
  }

  nextTurnId(): string {
    return this.ulid();
  }

  /**
   * Ensure a `.git` directory exists at the workspace root. Idempotent
   * — skips when `.git` already present. Never fails Agent.start: if
   * `git` binary is missing or init errors, we log a warning and flip
   * {@link disciplineDefault}.git to false so CommitCheckpoint doesn't
   * silently break.
   */
  private async maybeInitGit(): Promise<void> {
    const gitDir = path.join(this.config.workspaceRoot, ".git");
    try {
      await fs.access(gitDir);
      return;
    } catch {
      // .git missing — try to init.
    }
    try {
      const init = await runGit(this.config.workspaceRoot, ["init"]);
      if (init.code !== 0) {
        this.disciplineDefault = { ...this.disciplineDefault, git: false };
        agentLogger.warn("discipline_git_init_failed", { code: init.code, stderr: init.stderr.slice(0, 200) });
        return;
      }
      await runGit(this.config.workspaceRoot, [
        "config",
        "user.email",
        "bot@magi.local",
      ]);
      await runGit(this.config.workspaceRoot, [
        "config",
        "user.name",
        "magi-bot",
      ]);
      agentLogger.info("discipline_git_init", { root: this.config.workspaceRoot });
    } catch (err) {
      this.disciplineDefault = { ...this.disciplineDefault, git: false };
      agentLogger.warn("discipline_git_init_error", { error: (err as Error).message });
    }
  }

  async getOrCreateSession(
    sessionKey: string,
    channelRef: ChannelRef,
  ): Promise<Session> {
    const existing = this.sessions.get(sessionKey);
    if (existing) return existing;
    const now = Date.now();
    const meta: SessionMeta = {
      sessionKey,
      botId: this.config.botId,
      channel: channelRef,
      persona: personaFromSessionKey(sessionKey),
      createdAt: now,
      lastActivityAt: now,
      discipline: { ...this.disciplineDefault },
    };
    const session = new Session(meta, this);
    // Hydrate budget from transcript so pod restarts don't reset
    // cumulativeTurns to 0 (causes false "first turn" detection).
    await session.hydrateBudgetFromTranscript();
    this.sessions.set(sessionKey, session);
    // 2026-04-21: clear circuit breaker state when a new session starts.
    // The state file is per-workspace (not per-session), so a stale trip
    // from a prior session's sealed-files violation would block all new
    // messages even after /reset. Fire-and-forget — failure is safe.
    clearCircuitBreakerState(this.config.workspaceRoot).catch(() => {});
    // Phase 2h — keep the hash→sessionKey reverse mapping fresh so the
    // compliance / audit endpoints can recover plaintext keys from the
    // on-disk filename hash.
    const fname = sessionFileName(sessionKey);
    this.sessionKeyByHash.set(fname.replace(/\.jsonl$/, ""), sessionKey);
    return session;
  }

  async resumeGoalMissionAfterRestart(input: GoalMissionResumeInput): Promise<void> {
    const session = await this.getOrCreateSession(input.sessionKey, input.channel);
    await session.resumeGoalAfterRestart(input);
  }

  /** Snapshot of the hash → sessionKey reverse index (Phase 2h). */
  sessionKeyIndex(): Map<string, string> {
    return new Map(this.sessionKeyByHash);
  }

  /** Read-only list of live sessions (Phase 2h compliance endpoint). */
  listSessions(): Session[] {
    return [...this.sessions.values()];
  }

  /**
   * Look up an existing session by its key. Returns undefined when no
   * session is live — callers (notably the /v1/chat/inject route and
   * the midTurnInjector hook) MUST NOT auto-create a session here; a
   * missing session in those flows is a signal to return 404 / fail
   * open, not a trigger for provisioning.
   */
  getSession(sessionKey: string): Session | undefined {
    return this.sessions.get(sessionKey);
  }

  /**
   * Subscribe to Agent-scoped lifecycle events. Returns an
   * unsubscribe function. Use sparingly — this is not a general
   * pub/sub surface; it's meant for audit/metrics consumers that
   * want to observe session_closed etc.
   */
  onAgentEvent(listener: (event: AgentLifecycleEvent) => void): () => void {
    this.agentEventListeners.push(listener);
    return () => {
      const i = this.agentEventListeners.indexOf(listener);
      if (i >= 0) this.agentEventListeners.splice(i, 1);
    };
  }

  /**
   * Emit a lifecycle event to every subscribed listener. Public so
   * Session.close() (and future lifecycle call sites) can fire
   * without reaching into private state. Listener errors are caught
   * and logged — they must never break the emitter's caller.
   */
  emitAgentEvent(event: AgentLifecycleEvent): void {
    for (const listener of this.agentEventListeners) {
      try {
        listener(event);
      } catch (err) {
        agentLogger.warn("agent_event_listener_failed", { type: event.type, error: (err as Error).message });
      }
    }
  }

  /**
   * Close + deregister a live session. Returns `false` when the
   * session is unknown (already closed or never created). Returns
   * `true` after the session's {@link Session.close} has resolved and
   * the registry entry has been removed. Safe to call concurrently
   * with a running turn — close() itself is idempotent and the turn's
   * abort signal is the mechanism it uses to stop work cooperatively.
   *
   * Note: the session's reverse `sessionKeyByHash` mapping is left in
   * place intentionally — audit endpoints still want to resolve the
   * filename hash to a sessionKey after the session has ended.
   */
  async closeSession(sessionKey: string, reason?: string): Promise<boolean> {
    const sess = this.sessions.get(sessionKey);
    if (!sess) return false;
    try {
      await sess.close(reason);
    } finally {
      // Always drop the registry entry — even a partial close() error
      // shouldn't leave a half-dead Session reachable from
      // getSession(). close() itself is idempotent so a reopening
      // client path would reconstruct a fresh Session.
      this.sessions.delete(sessionKey);
    }
    return true;
  }
}

function splitPathListEnv(value: string | undefined): string[] {
  if (!value) return [];
  return value
    .split(",")
    .map((item) => item.trim())
    .filter((item) => item.length > 0);
}

/**
 * Locate the bundled superpowers skills directory on disk. Resolution
 * order:
 *   1. `$MAGI_SUPERPOWERS_DIR` env override.
 *   2. `<cwd>/skills/superpowers` — matches the Docker WORKDIR (/app)
 *      and the repo layout (`infra/docker/magi-core-agent/`).
 *
 * The directory may not exist in unit tests that never touch
 * superpowers — the slash handlers fail open with a short pointer
 * text when SKILL.md reads fail, so an unresolved path is harmless.
 */
function resolveDefaultSuperpowersDir(): string {
  const override = process.env.MAGI_SUPERPOWERS_DIR;
  if (override && override.trim().length > 0) return override.trim();
  return path.resolve(process.cwd(), "skills", "superpowers");
}
