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
import {
  makeCommitCheckpointTool,
  runGit,
} from "./tools/CommitCheckpoint.js";
import type { DisciplineSessionCounter } from "./hooks/builtin/disciplineHook.js";
import type { ChannelRef } from "./util/types.js";
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
import { makeBashTool } from "./tools/Bash.js";
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
import { makeWebFetchTool } from "./tools/WebFetch.js";
import { makeWebSearchTool } from "./tools/WebSearch.js";
import { makeKnowledgeSearchTool } from "./tools/KnowledgeSearch.js";
import { makeFileDeliverTool } from "./tools/FileDeliver.js";
import { makeFileSendTool } from "./tools/FileSend.js";
import { makeSpreadsheetWriteTool } from "./tools/SpreadsheetWrite.js";
import { registerSkillRuntimeHooks } from "./tools/SkillRuntimeHooks.js";
import { CronScheduler, type CronRecord } from "./cron/CronScheduler.js";
import { makeCronCreateTool } from "./tools/CronCreate.js";
import { makeCronListTool } from "./tools/CronList.js";
import { makeCronUpdateTool } from "./tools/CronUpdate.js";
import { makeCronDeleteTool } from "./tools/CronDelete.js";
import type { Turn } from "./Turn.js";
import type { ChannelAdapter } from "./channels/ChannelAdapter.js";
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

const MAX_BACKGROUND_DELIVERY_BYTES = 15 * 1024;
const TRUNCATED_BACKGROUND_RESULT_MARKER = "\n\n[truncated background result]";

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
  /** legacy-compatible path: /home/ocuser/.clawy/workspace. */
  workspaceRoot: string;
  gatewayToken: string;
  codexAccessToken?: string;
  codexRefreshToken?: string;
  apiProxyUrl: string;
  chatProxyUrl?: string;
  redisUrl?: string;
  /** Default model for this bot. Overridable per-turn via smart-router. */
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
    { kind: "anthropic" | "openai-compatible"; baseUrl: string; apiKey: string }
  >;
  /** OSS identity — optional agent display name. */
  agentName?: string;
  /** OSS identity — optional custom instructions. */
  agentInstructions?: string;
  /**
   * Initial tool-permission posture for newly-created sessions.
   * Hosted Clawy Pro pods set "bypass" explicitly to preserve the
   * low-friction product UX; bare Session construction defaults to
   * "default" so hooks are active unless runtime config opts out.
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
   * per skill). Defaults to `<repo>/infra/docker/clawy-core-agent/skills/superpowers`
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
  /** C1 — live channel adapters. Populated in start() when tokens set. */
  private readonly channelAdapters: ChannelAdapter[] = [];
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
    // Native hipocampus — qmd search. Started in start().
    this.qmdManager = new QmdManager(
      config.workspaceRoot,
      (process.env.CORE_AGENT_VECTOR_SEARCH ?? "off").trim().toLowerCase() === "on",
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
    this.tools.register(makeFileReadTool(config.workspaceRoot));
    this.tools.register(makeFileWriteTool(config.workspaceRoot));
    this.tools.register(makeFileEditTool(config.workspaceRoot));
    this.tools.register(makeBashTool(config.workspaceRoot));
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
    this.tools.register(makeSpawnAgentTool(this, this.backgroundTasks));
    // T2-10 — query / stop tools over background SpawnAgent tasks.
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
    this.tools.register(makeWebSearchTool());
    this.tools.register(makeWebFetchTool());
    this.tools.register(makeSpreadsheetWriteTool(config.workspaceRoot, this.outputArtifacts));
    this.tools.register(
      makeFileSendTool({
        workspaceRoot: config.workspaceRoot,
        binDir: path.join(config.workspaceRoot, "..", "bin"),
        gatewayToken: config.gatewayToken,
        botId: config.botId,
        chatProxyUrl: config.chatProxyUrl ?? "",
        getSourceChannel: (ctx) => {
          const turn = this.activeTurns.get(ctx.turnId);
          return turn?.session.meta.channel ?? null;
        },
        sendFile: async (channel, filePath, caption, mode) => {
          if (channel.type !== "telegram" && channel.type !== "discord") {
            throw new Error(`FileSend does not support ${channel.type} channel delivery`);
          }
          const adapter = this.channelAdapters.find((a) => a.kind === channel.type);
          if (!adapter) {
            throw new Error(`FileSend ${channel.type} adapter not configured`);
          }
          if (mode === "photo") {
            await adapter.sendPhoto(channel.channelId, filePath, caption);
          } else {
            await adapter.sendDocument(channel.channelId, filePath, caption);
          }
        },
      }),
    );
    this.tools.register(
      makeFileDeliverTool({
        workspaceRoot: config.workspaceRoot,
        outputRegistry: this.outputArtifacts,
        chatProxyUrl: config.chatProxyUrl ?? "",
        gatewayToken: config.gatewayToken,
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
        getSourceChannel: (ctx) => {
          const turn = this.activeTurns.get(ctx.turnId);
          return turn?.session.meta.channel ?? null;
        },
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
      console.warn(
        `[core-agent] background delivery skipped taskId=${input.taskId}: session not live sessionKey=${input.sessionKey}`,
      );
      return false;
    }

    const channel = session.meta.channel;
    try {
      if (channel.type === "app") {
        if (!this.webAppAdapter) {
          console.warn(
            `[core-agent] background delivery skipped taskId=${input.taskId}: webapp adapter not configured`,
          );
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
          console.warn(
            `[core-agent] background delivery skipped taskId=${input.taskId}: ${channel.type} adapter not configured`,
          );
          return false;
        }
        await adapter.send({
          chatId: channel.channelId,
          text,
        });
        return true;
      }

      console.warn(
        `[core-agent] background delivery skipped taskId=${input.taskId}: unsupported channel=${channel.type}`,
      );
      return false;
    } catch (err) {
      console.warn(
        `[core-agent] background delivery failed taskId=${input.taskId} channel=${channel.type}:${channel.channelId}: ${(err as Error).message}`,
      );
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
      console.warn(
        `[core-agent] background-tasks hydrate failed: ${(err as Error).message}`,
      );
    }

    // Coding Discipline — read `.discipline.yaml` if present, seed
    // Agent.disciplineDefault. Failure here is non-fatal; we fall
    // through to the baked-in DEFAULT_DISCIPLINE.
    try {
      const cfg = await loadDisciplineConfig(this.config.workspaceRoot);
      if (cfg) {
        this.disciplineDefault = cfg;
        console.log(
          `[core-agent] discipline: config loaded tdd=${cfg.tdd} git=${cfg.git} enforcement=${cfg.requireCommit}`,
        );
      }
    } catch (err) {
      console.warn(
        `[core-agent] discipline config load failed: ${(err as Error).message}`,
      );
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
        // Kevin's A/A/A rule #1 — surface "is coding-agent skill
        // active" so the classifier hook can promote soft → hard on
        // coding-labeled turns for bots that bundle the skill.
        isCodingAgentSkillActive: () =>
          this.tools.resolve("coding-agent") !== null,
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
      },
      // Superpowers plan-mode auto-trigger — reads permissionMode to
      // skip the nudge when the user is already in plan mode.
      planModeAutoTriggerAgent: {
        getSessionPermissionMode: (sessionKey) => {
          const s = this.sessions.get(sessionKey);
          return s ? s.getPermissionMode() : null;
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
      taskContractAgent: {
        readSessionTranscript,
      },
      artifactDeliveryAgent: {
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
      // Layer 4 (session resume) — snapshot = committed transcript +
      // last-activity anchor; append = queue the seed as a pending
      // injection so the midTurn injector absorbs it into the first
      // post-resume iteration.
      sessionResumeAgent: {
        getResumeSnapshot: async (sessionKey) => {
          const s = this.sessions.get(sessionKey);
          if (!s) return null;
          try {
            const entries = await s.transcript.readCommitted();
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
          s.injectMessage(seed, "api");
        },
      },
    });
    console.log(
      `[core-agent] hooks: builtin registered=${hookResult.registered} skipped=[${hookResult.skipped.join(",")}]`,
    );

    // Phase 2a: load workspace skills as first-class Tools (§9.8 P1).
    // Failure here is non-fatal — the bot still has the 6 core tools.
    const skillsDir = path.join(this.config.workspaceRoot, "skills");
    try {
      const n = await this.tools.loadSkills(skillsDir, this.config.workspaceRoot, {
        trustedSkillRoots: splitPathListEnv(
          process.env.CLAWY_TRUSTED_SKILL_ROOTS ??
            process.env.CORE_AGENT_TRUSTED_SKILL_ROOTS,
        ),
        trustedSkillDirs: splitPathListEnv(
          process.env.CLAWY_TRUSTED_SKILL_DIRS ??
            process.env.CORE_AGENT_TRUSTED_SKILL_DIRS,
        ),
      });
      const rpt = this.tools.skillReport();
      const issues = rpt?.issues.length ?? 0;
      const runtimeHooks = rpt
        ? registerSkillRuntimeHooks(this.hooks, rpt.runtimeHooks)
        : 0;
      console.log(
        `[core-agent] skills: loaded=${n} issues=${issues} runtimeHooks=${runtimeHooks} from ${skillsDir}`,
      );
      // Keep KB search deterministic even when a workspace ships a
      // prompt-only skill with the same name.
      this.tools.replace(makeKnowledgeSearchTool({ name: "knowledge-search" }));
      this.tools.replace(makeKnowledgeSearchTool({ name: "KnowledgeSearch" }));
      // Keep native browser deterministic even when a workspace ships a
      // prompt-only skill with the same name.
      this.tools.replace(makeBrowserTool(this.config.workspaceRoot));
      // Keep native web tools deterministic even when a workspace ships
      // prompt-only skills with the same names.
      this.tools.replace(makeWebSearchTool());
      this.tools.replace(makeWebFetchTool());
    } catch (err) {
      console.warn(`[core-agent] skill load failed: ${(err as Error).message}`);
    }

    // Cron scheduler — hydrate persisted cron records then wire the
    // fire handler + start the 30s tick-loop. The fire handler
    // synthesises a turn on the cron's deliveryChannel so delivery is
    // runtime-enforced (see CronCreate docstring for context).
    try {
      await this.crons.hydrate();
      this.crons.setFireHandler((record) => this.fireCron(record));
      this.crons.start();
      console.log(
        `[core-agent] crons hydrated=${this.crons.list().length} tickerStarted`,
      );
    } catch (err) {
      console.warn(`[core-agent] cron hydrate failed: ${(err as Error).message}`);
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
            console.warn(`[core-agent] hipocampus cron failed: ${(err as Error).message}`);
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
      console.log(
        `[core-agent] hipocampus: qmd=${this.qmdManager.isReady()} vector=${(process.env.CORE_AGENT_VECTOR_SEARCH ?? "off").trim().toLowerCase() === "on"} compactor+flush=registered`,
      );
    } catch (err) {
      console.warn(`[core-agent] hipocampus init failed: ${(err as Error).message}`);
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
          console.warn(
            `[core-agent] ${adapter.kind} dispatch failed: ${(err as Error).message}`,
          );
        }
      });
      try {
        await adapter.start();
        this.channelAdapters.push(adapter);
        console.log(`[core-agent] channel adapter started kind=${adapter.kind}`);
      } catch (err) {
        console.warn(
          `[core-agent] channel adapter start failed kind=${adapter.kind}: ${(err as Error).message}`,
        );
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
      console.log(`[core-agent] webapp push adapter started`);
    } catch (err) {
      console.warn(
        `[core-agent] webapp push adapter start failed: ${(err as Error).message}`,
      );
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
        console.warn(
          `[core-agent] session close on shutdown failed sessionKey=${key}: ${(err as Error).message}`,
        );
      }
    }

    this.crons.stop();
    await this.hipocampus.stop();
    // C1 — stop all channel adapters. Each adapter is responsible for
    // aborting its own long-polls / closing gateway sockets.
    for (const adapter of this.channelAdapters.splice(0)) {
      try {
        await adapter.stop();
      } catch (err) {
        console.warn(
          `[core-agent] channel adapter stop failed kind=${adapter.kind}: ${(err as Error).message}`,
        );
      }
    }
    // §7.15 — outbound-only web/app adapter. No-op stop but exercised
    // for symmetry; release the reference so tests can assert teardown.
    if (this.webAppAdapter) {
      try {
        await this.webAppAdapter.stop();
      } catch (err) {
        console.warn(
          `[core-agent] webapp adapter stop failed: ${(err as Error).message}`,
        );
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
  private async fireCron(record: CronRecord): Promise<void> {
    const sessionKey = `agent:cron:${record.deliveryChannel.type}:${record.deliveryChannel.channelId}:${record.cronId}`;
    const session = await this.getOrCreateSession(sessionKey, record.deliveryChannel);
    const { StubSseWriter } = await import("./transport/SseWriter.js");
    const stubSse = new StubSseWriter();
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
        await this.deliverCronAssistantText(record.deliveryChannel, assistantText);
      }
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
          console.warn(
            `[core-agent] fireCron: closeSession failed cronId=${record.cronId}: ${(err as Error).message}`,
          );
        }
      }
    }
  }

  private async deliverCronAssistantText(
    channel: ChannelRef,
    text: string,
  ): Promise<void> {
    if (channel.type === "app") {
      if (!this.config.chatProxyUrl) {
        console.warn("[clawy-agent] cron delivery to app channel skipped — no chatProxyUrl configured");
        return;
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
          `cron app delivery failed: HTTP ${resp.status} ${errText.slice(0, 200)}`,
        );
      }
      return;
    }

    if (channel.type === "telegram" || channel.type === "discord") {
      const adapter = this.channelAdapters.find((a) => a.kind === channel.type);
      if (!adapter) {
        throw new Error(`cron ${channel.type} adapter not configured`);
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
        console.warn(
          `[core-agent] discipline: git init failed (code=${init.code}) — disabling git half. ${init.stderr.slice(0, 200)}`,
        );
        return;
      }
      await runGit(this.config.workspaceRoot, [
        "config",
        "user.email",
        "bot@clawy.pro",
      ]);
      await runGit(this.config.workspaceRoot, [
        "config",
        "user.name",
        "clawy-bot",
      ]);
      console.log(
        `[core-agent] discipline: git repo initialised at ${this.config.workspaceRoot}`,
      );
    } catch (err) {
      this.disciplineDefault = { ...this.disciplineDefault, git: false };
      console.warn(
        `[core-agent] discipline: git init threw — disabling git half: ${(err as Error).message}`,
      );
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
        console.warn(
          `[core-agent] agent-event listener failed type=${event.type}: ${(err as Error).message}`,
        );
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
 *   1. `$CORE_AGENT_SUPERPOWERS_DIR` env override.
 *   2. `<cwd>/skills/superpowers` — matches the Docker WORKDIR (/app)
 *      and the repo layout (`infra/docker/clawy-core-agent/`).
 *
 * The directory may not exist in unit tests that never touch
 * superpowers — the slash handlers fail open with a short pointer
 * text when SKILL.md reads fail, so an unresolved path is harmless.
 */
function resolveDefaultSuperpowersDir(): string {
  const override = process.env.CORE_AGENT_SUPERPOWERS_DIR;
  if (override && override.trim().length > 0) return override.trim();
  return path.resolve(process.cwd(), "skills", "superpowers");
}
