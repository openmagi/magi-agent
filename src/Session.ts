/**
 * Session — per-sessionKey conversation thread.
 * Design reference: §5.2 / §6-F.
 *
 * Owns: append-only transcript, per-session mutex (one Turn at a time),
 * layered context builder, in-memory turn history (hydrated on startup
 * via transcript replay).
 *
 * Phase 0: class shape only.
 */

import type { Agent } from "./Agent.js";
import { Turn, TurnInterruptedError, type TurnResult } from "./Turn.js";
import {
  isResearchProofBlockReason,
  researchProofFailureNoticeText,
} from "./turn/ResearchProofFailureNotice.js";
import type { ChannelRef, TokenUsage, UserMessage } from "./util/types.js";
import type { GoalMissionResumeInput, MissionChannelType } from "./missions/types.js";
import { judgeGoalTurn } from "./goals/GoalJudge.js";
import {
  buildGoalContinuationMessage,
  canContinueGoal,
  distillGoalSpec,
  goalLoopMaxTurns,
  goalRequestFromMessage,
} from "./goals/GoalLoop.js";
import { StubSseWriter, type SseWriter } from "./transport/SseWriter.js";
import { Transcript } from "./storage/Transcript.js";
import { ControlEventLedger } from "./control/ControlEventLedger.js";
import { ControlRequestStore } from "./control/ControlRequestStore.js";
import { projectControlEvents, type ControlProjection } from "./control/ControlProjection.js";
import { PlanLifecycle } from "./plan/PlanLifecycle.js";
import { ExecutionContractStore } from "./execution/ExecutionContract.js";
import { ResearchContractStore } from "./research/ResearchContract.js";
import { SourceLedgerStore } from "./research/SourceLedger.js";
import type { ResolveControlRequestInput } from "./control/ControlRequestStore.js";
import type { ControlRequestRecord } from "./control/ControlEvents.js";
import { CompactionImpossibleError } from "./services/compact/ContextEngine.js";
import type { StructuredOutputSpec } from "./structured/StructuredOutputContract.js";
import {
  Context,
  DEFAULT_CONTEXT_ID,
  loadMetaIndex,
  newContextId,
  writeMetaIndex,
  type ContextMeta,
  type ContextStats,
} from "./Context.js";
import { matchSlashCommand } from "./slash/registry.js";

const TERMINAL_ABORT_FALLBACK =
  "Warning: The run stopped before completion. No final answer was produced. Please retry.";

function emitTerminalAbortFallback(sse: SseWriter, reason?: string): void {
  sse.agent({ type: "response_clear" });
  if (reason && isResearchProofBlockReason(reason)) {
    sse.agent({ type: "text_delta", delta: researchProofFailureNoticeText(reason) });
    return;
  }
  sse.agent({ type: "text_delta", delta: TERMINAL_ABORT_FALLBACK });
}

/**
 * Coding Discipline block (docs/plans/2026-04-20-coding-discipline-design.md).
 *
 * Observed + optionally-enforced TDD / git hygiene posture for a
 * session. Lives on Session.meta so it is both in-memory per turn and
 * surfaced through the external metadata bridge (§3.2 — pending) when
 * dashboards want to show "this session is in TDD mode".
 *
 * Defaults are applied by the v2→v3 session meta migration; individual
 * sessions may be upgraded per-turn by the classifier hook (which
 * inspects the user message) or pinned via `.discipline.yaml`.
 */
export interface Discipline {
  /** TDD mode — observe source edits and expect a matching test file. */
  tdd: boolean;
  /** Git hygiene — change-count afterTurnEnd reminder + CommitCheckpoint. */
  git: boolean;
  /**
   * Enforcement intensity:
   *   - `"off"`:   no observation, block, or audit emission
   *   - `"soft"`:  audit emission only (reminders, tdd_violation events)
   *   - `"hard"`:  beforeToolUse block on TDD violation
   */
  requireCommit: "off" | "soft" | "hard";
  /** Reminder threshold for pending dirty files. */
  maxChangesBeforeCommit: number;
  /** Globs matched against workspace-relative paths. */
  testPatterns: string[];
  /** Globs matched against workspace-relative paths. */
  sourcePatterns: string[];
  /**
   * Per-turn-classified label (coding | exploratory | other). Written
   * by {@link classifyTurnModeHook} on beforeLLMCall; read by the
   * discipline prompt block so the LLM sees the current mode.
   */
  lastClassifiedMode?: "coding" | "exploratory" | "other";
  /**
   * When true, the classifier hook stops touching this block. Set by
   * the workspace `.discipline.yaml` override so operators can pin a
   * bot to specific settings without the heuristic overriding them.
   */
  frozen?: boolean;
  /**
   * Operator-pinned "never require a failing test first" flag. When
   * true, the TDD half of discipline observes source edits but does
   * not treat the missing-test case as a violation (no audit emit, no
   * block). Populated from the simple `.discipline.yaml` schema
   * `{ mode, skipTdd }`.
   */
  skipTdd?: boolean;
}

export interface SessionMeta {
  sessionKey: string;
  botId: string;
  channel: ChannelRef;
  /** Persona name, e.g. "main" or "researcher". Parsed from sessionKey. */
  persona?: string;
  createdAt: number;
  lastActivityAt: number;
  /**
   * Role hint. `"subagent"` marks ephemeral spawn-child sessions that
   * must NOT own durable crons (they vanish with the parent turn —
   * any surviving cron would be an orphan with no valid channel to
   * deliver to). Undefined is treated as a regular top-level session.
   */
  role?: "subagent";
  /**
   * IDs of session-scoped (non-durable) crons owned by this session.
   * Populated by CronCreate when `durable: false`. Session.close()
   * deletes each from the scheduler so they don't leak across
   * restarts / pod rotations. Durable crons do NOT appear here —
   * they live only in `workspace/core-agent/crons/index.json`.
   */
  crons?: string[];
  /**
   * Coding Discipline posture. Populated by Agent.getOrCreateSession
   * (reading the workspace default) + the classifier hook per turn.
   * Undefined on very old in-memory sessions is treated as
   * {@link DEFAULT_DISCIPLINE}.
   */
  discipline?: Discipline;
  /**
   * Onboarding flags consumed by the `onboardingNeededCheck`
   * beforeTurnStart hook (see `docs/plans/2026-04-20-superpowers-plugin-design.md`).
   *   - `onboarded=true` suppresses the nudge permanently for the bot.
   *   - `onboardingDeclines` counts how many times the user has
   *     declined; the hook stops nudging once it reaches 2.
   * Both start undefined (=> false / 0) on fresh sessions; the hook
   * updates them in-place when it fires.
   */
  onboarded?: boolean;
  onboardingDeclines?: number;
  /**
   * Set when the session-resume hook has queued authoritative resume
   * context for the next LLM turn. The onboarding nudge must not fire
   * on that same post-reprovision turn because it competes with the
   * user's active work recovery.
   */
  resumeSeededAt?: number;
}

export interface SessionStats {
  turnsCommitted: number;
  turnsAborted: number;
  lastTurnAt?: number;
}

/**
 * Live budget snapshot surfaced to HTTP /v1/session/:sessionKey/stats
 * + returned by Session.stats(). Accumulates across every turn in the
 * session's lifetime (no reset on commit/abort — the budget is the
 * whole session, not the turn).
 */
export interface SessionBudgetStats {
  turns: number;
  inputTokens: number;
  outputTokens: number;
  costUsd: number;
}

/** Reason the session budget was exhausted. */
export type BudgetExceededReason = "turns" | "cost";

export interface BudgetCheckResult {
  exceeded: boolean;
  reason?: BudgetExceededReason;
}

function goalLoopEnabled(): boolean {
  return process.env.CORE_AGENT_GOAL_LOOP === "1";
}

function truncateGoalText(value: string, limit: number): string {
  return value.length <= limit ? value : value.slice(0, limit - 1).trimEnd();
}

function metadataString(
  metadata: UserMessage["metadata"] | undefined,
  key: string,
): string | undefined {
  const value = metadata?.[key];
  return typeof value === "string" && value.trim().length > 0
    ? value.trim()
    : undefined;
}

function metadataNumber(
  metadata: UserMessage["metadata"] | undefined,
  key: string,
): number | undefined {
  const value = metadata?.[key];
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function metadataStringArray(
  metadata: UserMessage["metadata"] | undefined,
  key: string,
): string[] {
  const value = metadata?.[key];
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string" && item.trim().length > 0);
}

/**
 * Default maximum turns per session before Turn.ts aborts.
 * 2026-04-20 0.17.1: 50 → 1000 for Claude Code parity (effective
 * no-cap for normal conversations). Per-bot override via
 * AgentConfig.maxTurnsPerSession still wins.
 */
export const DEFAULT_MAX_TURNS_PER_SESSION = 1000;

/** Default maximum USD cost per session before Turn.ts aborts. 0 = unlimited. */
export const DEFAULT_MAX_COST_USD_PER_SESSION = 0;

/**
 * T2-08 — Session-level permission posture.
 *
 * Decouples "plan mode" (UX convention — read-only tool surface
 * surfaced to the model) from "permission posture" (runtime consent
 * policy for tool execution). Prior to T2-08 the two were collapsed
 * onto the single `Turn.planMode` flag + `PLAN_MODE_ALLOWED_TOOLS`
 * whitelist in Turn.ts (DEBT-PLAN-PERMS-01).
 *
 * Modes:
 *   - `default`: every dangerous tool call requires askUser (legacy
 *     behaviour — selfClaimVerifier and any configured
 *     `dangerous_patterns` hook drive the prompt).
 *   - `plan`: planning-only — tool registry is filtered to read-only
 *     + ExitPlanMode per `PLAN_MODE_ALLOWED_TOOLS`.
 *   - `auto`: auto-approve tools with `tool.dangerous === false`;
 *     dangerous tools still require askUser.
 *   - `bypass`: full access, no beforeToolUse hook prompts
 *     (admin/shadow sessions only — not user-reachable).
 */
export type PermissionMode = "default" | "plan" | "auto" | "bypass";

export interface BackgroundTask {
  taskId: string;
  toolName: string;
  startedAt: number;
  status: "running" | "done" | "error";
}

/**
 * Minimal async mutex. Used so a single Session never runs two Turns
 * concurrently (important for transcript ordering + invariant A).
 */
class AsyncMutex {
  private chain: Promise<unknown> = Promise.resolve();

  async run<T>(fn: () => Promise<T>): Promise<T> {
    const prev = this.chain;
    let release!: () => void;
    const held = new Promise<void>((resolve) => (release = resolve));
    this.chain = prev.then(() => held);
    await prev;
    try {
      return await fn();
    } finally {
      release();
    }
  }
}

export class Session {
  readonly meta: SessionMeta;
  readonly controlEvents: ControlEventLedger;
  readonly controlRequests: ControlRequestStore;
  readonly planLifecycle: PlanLifecycle;
  readonly executionContract: ExecutionContractStore;
  readonly sourceLedger: SourceLedgerStore;
  readonly researchContract: ResearchContractStore;
  /** Legacy convenience accessor — returns the active context's
   * transcript so every pre-T4-19 call site keeps working unchanged. */
  get transcript(): Transcript {
    return this.getActiveContext().transcript;
  }
  private readonly mutex = new AsyncMutex();

  // ── T4-19 multi-context state ──────────────────────────────────
  private readonly contexts = new Map<string, Context>();
  private activeContextId: string = DEFAULT_CONTEXT_ID;
  private metaHydrated = false;

  // ── T1-06 session budget state ────────────────────────────────
  private cumulativeTurns = 0;
  private cumulativeInputTokens = 0;
  private cumulativeOutputTokens = 0;
  private cumulativeCostUsd = 0;

  /** Maximum turns allowed in this session. */
  readonly maxTurns: number;
  /** Maximum accumulated USD cost allowed in this session. */
  readonly maxCostUsd: number;

  // ── T2-08 permission posture state ─────────────────────────────
  private permissionMode: PermissionMode = "default";
  /**
   * Mode that was active immediately before the session entered
   * `plan`. Captured on the plan-mode transition so ExitPlanMode can
   * restore the prior posture rather than hard-coding back to
   * `default`. `null` when plan-mode has not been entered.
   */
  private prePlanMode: PermissionMode | null = null;

  // ── mid-turn injection state (#86) ─────────────────────────────
  /**
   * FIFO queue of user messages submitted while a turn was already
   * streaming. The midTurnInjector beforeLLMCall hook drains this
   * before each iteration so the LLM sees the injection on its next
   * step rather than waiting for the current turn to finalise.
   *
   * Not mutex-guarded on push — Node's single-threaded event loop
   * gives us atomic .push / .splice, and the drain happens inside
   * the turn's own mutex-held critical section.
   */
  private pendingInjections: UserMessage[] = [];
  private injectionSeq = 0;
  private pendingHiddenContext: string[] = [];
  private structuredOutputContract: StructuredOutputSpec | null = null;
  private controlRequestWaiters = new Map<
    string,
    {
      resolve: (record: ControlRequestRecord) => void;
      cleanup: () => void;
    }
  >();
  /** Cap per turn so a runaway client cannot flood the queue. */
  static readonly MAX_PENDING_INJECTIONS = 5;

  /**
   * SSE writer for the active turn — set by Turn.execute() so that
   * injectMessage() can emit an `injection_queued` event to the client.
   * Cleared when no turn is active.
   */
  private _activeSse: { agent(evt: unknown): void } | null = null;
  private activeTurn: Turn | null = null;

  /** Called by Turn.execute() to register/clear the active SSE writer. */
  setActiveSse(sse: { agent(evt: unknown): void } | null): void {
    this._activeSse = sse;
  }

  // ── lifecycle state (#82) ──────────────────────────────────────
  /**
   * Guards {@link close} against re-entry. Flipped true on the first
   * close() call so subsequent calls (SIGTERM after cron cleanup, etc.)
   * are safe no-ops.
   */
  private _closed = false;
  /**
   * Optional AbortController used to cancel in-flight turns on close.
   * Lazily instantiated by {@link getAbortSignal} so callers that never
   * need cooperative abort don't pay the allocation. The controller is
   * kept for the whole Session lifetime — a single turn abort does
   * *not* replace it, because close() is the only documented aborter
   * and it happens once.
   */
  private abortController: AbortController | null = null;

  constructor(
    meta: SessionMeta,
    readonly agent: Agent,
  ) {
    this.meta = meta;
    this.permissionMode = agent.config.defaultPermissionMode ?? "default";
    this.maxTurns =
      agent.config.maxTurnsPerSession ?? DEFAULT_MAX_TURNS_PER_SESSION;
    this.maxCostUsd =
      agent.config.maxCostUsdPerSession ?? DEFAULT_MAX_COST_USD_PER_SESSION;
    // Default context is always present — eager construction avoids an
    // async getter dance everywhere transcript is read.
    this.bootstrapDefaultContext();
    this.controlEvents = new ControlEventLedger({
      rootDir: agent.sessionsDir,
      sessionKey: meta.sessionKey,
      transcript: this.transcript,
    });
    this.controlRequests = new ControlRequestStore({ ledger: this.controlEvents });
    this.executionContract = new ExecutionContractStore();
    this.sourceLedger = new SourceLedgerStore();
    this.researchContract = new ResearchContractStore();
    this.planLifecycle = new PlanLifecycle({
      sessionKey: meta.sessionKey,
      channelName: meta.channel.channelId,
      controlEvents: this.controlEvents,
      controlRequests: this.controlRequests,
      getPermissionMode: () => this.getPermissionMode(),
      setPermissionMode: (mode) => this.setPermissionMode(mode),
      exitPlanMode: () => this.exitPlanMode(),
      enqueueHiddenContext: (message) => this.enqueueHiddenContext(message),
    });
  }

  async controlProjection(): Promise<ControlProjection> {
    return projectControlEvents(await this.controlEvents.readAll());
  }

  async resolveControlRequest(
    requestId: string,
    input: ResolveControlRequestInput,
  ) {
    const resolved = await this.planLifecycle.resolveControlRequest(requestId, input);
    this.notifyControlRequestWaiter(resolved);
    return resolved;
  }

  async waitForControlRequestResolution(
    requestId: string,
    signal?: AbortSignal,
  ): Promise<ControlRequestRecord> {
    const projection = await this.controlRequests.project();
    const existing = projection.requests[requestId];
    if (!existing) throw new Error(`control request not found: ${requestId}`);
    if (existing.state !== "pending") return existing;

    return await new Promise<ControlRequestRecord>((resolve) => {
      const timeoutMs = Math.max(0, existing.expiresAt - Date.now() + 25);
      let settled = false;
      const settle = (record: ControlRequestRecord) => {
        if (settled) return;
        settled = true;
        cleanup();
        resolve(record);
      };
      const timeout = setTimeout(() => {
        this.controlRequests
          .resolve(requestId, { decision: "denied" })
          .then((record) => settle(record))
          .catch(() => settle(existing));
      }, timeoutMs);
      const abort = () => {
        this.controlRequests
          .cancel(requestId, "turn aborted while waiting for control request")
          .then((record) => settle(record))
          .catch(() => settle(existing));
      };
      const cleanup = () => {
        clearTimeout(timeout);
        signal?.removeEventListener("abort", abort);
        if (this.controlRequestWaiters.get(requestId)?.resolve === settle) {
          this.controlRequestWaiters.delete(requestId);
        }
      };
      signal?.addEventListener("abort", abort, { once: true });
      if (signal?.aborted) {
        abort();
        return;
      }
      this.controlRequestWaiters.set(requestId, { resolve: settle, cleanup });
    });
  }

  private notifyControlRequestWaiter(record: ControlRequestRecord): void {
    const waiter = this.controlRequestWaiters.get(record.requestId);
    if (!waiter) return;
    waiter.resolve(record);
  }

  /**
   * Hydrate cumulative budget stats from the on-disk transcript so that
   * pod restarts don't reset `cumulativeTurns` to 0. Without this, the
   * onboarding hook (and any "first turn" heuristic) falsely fires on
   * every pod restart because `budgetStats().turns === 0`.
   *
   * Must be called once after construction — it is async because
   * transcript I/O is async. Safe to call multiple times (idempotent
   * when budget is already > 0). Agent.getOrCreateSession invokes this.
   */
  async hydrateBudgetFromTranscript(): Promise<void> {
    // Skip if budget already hydrated (session was reused in-memory).
    if (this.cumulativeTurns > 0) return;
    try {
      const entries = await this.transcript.readAll();
      let turns = 0;
      let inputTokens = 0;
      let outputTokens = 0;
      let earliestTs = Infinity;
      for (const e of entries) {
        // Track earliest timestamp across ALL entry types so
        // createdAt survives pod restarts (2026-04-22 bug fix).
        if (e.ts > 0 && e.ts < earliestTs) {
          earliestTs = e.ts;
        }
        if (e.kind === "turn_committed") {
          turns += 1;
          inputTokens += e.inputTokens ?? 0;
          outputTokens += e.outputTokens ?? 0;
        }
      }
      if (turns > 0) {
        this.cumulativeTurns = turns;
        this.cumulativeInputTokens = inputTokens;
        this.cumulativeOutputTokens = outputTokens;
        // costUsd is not stored in transcript entries — leave at 0.
        // The budget exceeded check for cost will be slightly under-
        // counted after restart, but the turns check (which matters
        // for onboarding) is correct.
      }
      // 2026-04-22 bug fix: hydrate createdAt from the earliest
      // transcript entry so the bot reports correct session start
      // time after pod restarts (previously reset to Date.now()).
      if (earliestTs < Infinity && earliestTs < this.meta.createdAt) {
        this.meta.createdAt = earliestTs;
      }
    } catch {
      // Fail-open: if transcript can't be read, start from 0.
      // This matches the pre-fix behaviour.
    }
  }

  private bootstrapDefaultContext(): void {
    const now = Date.now();
    const defaultMeta: ContextMeta = {
      contextId: DEFAULT_CONTEXT_ID,
      sessionKey: this.meta.sessionKey,
      title: "default",
      createdAt: now,
      lastActivityAt: now,
      archived: false,
    };
    this.contexts.set(
      DEFAULT_CONTEXT_ID,
      new Context(defaultMeta, this.agent.sessionsDir),
    );
  }

  /**
   * Lazily load the on-disk meta index (if one exists) so previously
   * created non-default contexts reappear after a pod restart. Safe to
   * call multiple times — idempotent.
   */
  private async hydrateMetaIfNeeded(): Promise<void> {
    if (this.metaHydrated) return;
    this.metaHydrated = true;
    const meta = await loadMetaIndex(this.agent.sessionsDir, this.meta.sessionKey);
    if (!meta) return;
    for (const cm of meta.contexts) {
      if (this.contexts.has(cm.contextId)) {
        // Update default context's persisted title/archived if any.
        const existing = this.contexts.get(cm.contextId)!;
        existing.meta.title = cm.title;
        existing.meta.archived = cm.archived;
        if (cm.systemPromptAddendum !== undefined) {
          existing.meta.systemPromptAddendum = cm.systemPromptAddendum;
        }
        continue;
      }
      this.contexts.set(cm.contextId, new Context(cm, this.agent.sessionsDir));
    }
    if (meta.activeContextId && this.contexts.has(meta.activeContextId)) {
      this.activeContextId = meta.activeContextId;
    }
  }

  private async persistMetaIndex(): Promise<void> {
    const list: ContextMeta[] = [];
    for (const c of this.contexts.values()) list.push({ ...c.meta });
    await writeMetaIndex(this.agent.sessionsDir, this.meta.sessionKey, {
      contexts: list,
      activeContextId: this.activeContextId,
    });
  }

  getActiveContext(): Context {
    const c = this.contexts.get(this.activeContextId);
    if (!c) {
      // Should never happen post-bootstrap; guard defensively.
      const d = this.contexts.get(DEFAULT_CONTEXT_ID);
      if (!d) throw new Error("session has no default context");
      this.activeContextId = DEFAULT_CONTEXT_ID;
      return d;
    }
    return c;
  }

  async listContexts(): Promise<ContextMeta[]> {
    await this.hydrateMetaIfNeeded();
    return [...this.contexts.values()].map((c) => ({ ...c.meta }));
  }

  async getContext(contextId: string): Promise<Context | null> {
    await this.hydrateMetaIfNeeded();
    return this.contexts.get(contextId) ?? null;
  }

  async createContext(opts: {
    title: string;
    systemPromptAddendum?: string;
  }): Promise<Context> {
    await this.hydrateMetaIfNeeded();
    const now = Date.now();
    const meta: ContextMeta = {
      contextId: newContextId(),
      sessionKey: this.meta.sessionKey,
      title: opts.title,
      createdAt: now,
      lastActivityAt: now,
      archived: false,
      ...(opts.systemPromptAddendum
        ? { systemPromptAddendum: opts.systemPromptAddendum }
        : {}),
    };
    const ctx = new Context(meta, this.agent.sessionsDir);
    this.contexts.set(meta.contextId, ctx);
    await this.persistMetaIndex();
    return ctx;
  }

  async switchContext(contextId: string): Promise<void> {
    await this.hydrateMetaIfNeeded();
    const ctx = this.contexts.get(contextId);
    if (!ctx) throw new Error(`context not found: ${contextId}`);
    if (ctx.meta.archived) throw new Error(`context is archived: ${contextId}`);
    this.activeContextId = contextId;
    await this.persistMetaIndex();
  }

  async patchContext(
    contextId: string,
    patch: { title?: string; archived?: boolean; systemPromptAddendum?: string },
  ): Promise<ContextMeta> {
    await this.hydrateMetaIfNeeded();
    const ctx = this.contexts.get(contextId);
    if (!ctx) throw new Error(`context not found: ${contextId}`);
    if (patch.title !== undefined) ctx.meta.title = patch.title;
    if (patch.archived !== undefined) ctx.meta.archived = patch.archived;
    if (patch.systemPromptAddendum !== undefined) {
      ctx.meta.systemPromptAddendum = patch.systemPromptAddendum;
    }
    await this.persistMetaIndex();
    return { ...ctx.meta };
  }

  async deleteContext(contextId: string): Promise<void> {
    await this.hydrateMetaIfNeeded();
    if (!this.contexts.has(contextId)) return;
    const nonArchivedCount = [...this.contexts.values()].filter(
      (c) => !c.meta.archived,
    ).length;
    const target = this.contexts.get(contextId)!;
    if (!target.meta.archived && nonArchivedCount <= 1) {
      throw new Error("refusing to delete the last non-archived context");
    }
    this.contexts.delete(contextId);
    if (this.activeContextId === contextId) {
      // Fall back to default, or the first available non-archived.
      const fallback =
        this.contexts.get(DEFAULT_CONTEXT_ID) ??
        [...this.contexts.values()].find((c) => !c.meta.archived);
      this.activeContextId = fallback?.meta.contextId ?? DEFAULT_CONTEXT_ID;
      if (!this.contexts.has(this.activeContextId)) {
        this.bootstrapDefaultContext();
      }
    }
    await this.persistMetaIndex();
  }

  contextStats(contextId: string): ContextStats | null {
    const c = this.contexts.get(contextId);
    return c ? c.stats() : null;
  }

  /** Current permission posture (§T2-08). */
  getPermissionMode(): PermissionMode {
    return this.permissionMode;
  }

  /**
   * Transition to a new permission posture. Captures `prePlanMode`
   * on the default→plan / auto→plan / bypass→plan transition so
   * {@link exitPlanMode} can restore it. A redundant set to the same
   * mode is a no-op (prePlanMode is NOT overwritten on plan→plan).
   */
  setPermissionMode(next: PermissionMode): void {
    if (next === this.permissionMode) return;
    if (next === "plan" && this.permissionMode !== "plan") {
      this.prePlanMode = this.permissionMode;
    }
    this.permissionMode = next;
  }

  /**
   * Mode that was active before the session entered plan mode, if
   * any. Exposed so HTTP inspection + ExitPlanMode callers can reason
   * about restoration.
   */
  getPrePlanMode(): PermissionMode | null {
    return this.prePlanMode;
  }

  /**
   * Leave plan mode, restoring the captured pre-plan posture (or
   * falling back to `default` when none was captured — e.g. the
   * session was constructed directly into `plan`). Clears
   * `prePlanMode` after restoration.
   */
  exitPlanMode(): void {
    if (this.permissionMode !== "plan") return;
    const restored = this.prePlanMode ?? "default";
    this.permissionMode = restored;
    this.prePlanMode = null;
  }

  /**
   * Accumulate a committed turn's usage into the session budget. Called
   * by Turn.ts after each turn finishes (commit or abort — the tokens
   * were spent either way).
   */
  recordTurnUsage(usage: TokenUsage): void {
    this.cumulativeTurns += 1;
    this.cumulativeInputTokens += usage.inputTokens;
    this.cumulativeOutputTokens += usage.outputTokens;
    this.cumulativeCostUsd += usage.costUsd;
  }

  /**
   * Check whether the session budget has been exhausted. Called by
   * Turn.ts at the top of execute() — if exceeded, the turn aborts
   * before any LLM call is made.
   */
  budgetExceeded(): BudgetCheckResult {
    if (this.cumulativeTurns >= this.maxTurns) {
      return { exceeded: true, reason: "turns" };
    }
    if (this.maxCostUsd > 0 && this.cumulativeCostUsd >= this.maxCostUsd) {
      return { exceeded: true, reason: "cost" };
    }
    return { exceeded: false };
  }

  /** Snapshot of the current budget consumption. */
  budgetStats(): SessionBudgetStats {
    return {
      turns: this.cumulativeTurns,
      inputTokens: this.cumulativeInputTokens,
      outputTokens: this.cumulativeOutputTokens,
      costUsd: this.cumulativeCostUsd,
    };
  }

  /**
   * Attach a session-scoped (non-durable) cron's id to this session.
   * CronCreate calls this after successfully creating the record on
   * the scheduler so {@link close} knows what to sweep. Idempotent —
   * re-adding an id is a no-op.
   */
  registerSessionCron(cronId: string): void {
    const list = this.meta.crons ?? (this.meta.crons = []);
    if (!list.includes(cronId)) list.push(cronId);
  }

  /**
   * Lazily allocate the session's abort controller. Returned to
   * callers that want to cooperatively abort on session close (e.g.
   * long-running tool executions inside a cron-spawned turn).
   */
  getAbortSignal(): AbortSignal {
    if (!this.abortController) this.abortController = new AbortController();
    return this.abortController.signal;
  }

  /** Whether {@link close} has already been called. */
  isClosed(): boolean {
    return this._closed;
  }

  /**
   * Release session-scoped resources. Today this means:
   *   1. Aborting any in-flight turn via the session AbortController
   *      (no-op when none was ever allocated).
   *   2. Draining {@link pendingInjections} so queued mid-turn
   *      injections don't leak into a reused Session instance.
   *   3. Dropping every non-durable cron the session owns from the
   *      scheduler — durable crons are explicitly left alone so
   *      daily/weekly reports keep firing after the session's HTTP
   *      connection has gone.
   *   4. Emitting a `session_closed` AgentEvent for observability.
   *
   * Idempotent — the first call flips {@link _closed} true; later
   * calls short-circuit and are a no-op.
   *
   * @param reason Optional audit string attached to the emitted event
   *   (e.g. `"cron_complete"`, `"shutdown"`, `"http_close"`).
   */
  async close(reason?: string): Promise<void> {
    if (this._closed) return;
    this._closed = true;

    if (this.activeTurn) {
      try {
        this.activeTurn.requestInterrupt(false, reason ?? "session_close");
      } catch (err) {
        console.warn(
          `[core-agent] session close: active turn interrupt failed sessionKey=${this.meta.sessionKey}: ${(err as Error).message}`,
        );
      }
    }

    // 1. Abort any in-flight work that cooperatively listens on the
    //    session's AbortSignal. Safe when no controller was ever
    //    allocated — we just skip.
    if (this.abortController && !this.abortController.signal.aborted) {
      try {
        this.abortController.abort();
      } catch (err) {
        console.warn(
          `[core-agent] session close: abort failed sessionKey=${this.meta.sessionKey}: ${(err as Error).message}`,
        );
      }
    }

    // 2. Drain mid-turn injection queue — anything still queued at
    //    close time is dropped on the floor (the owning turn is either
    //    already aborted or completed, and the injection window is
    //    over).
    if (this.pendingInjections.length > 0) {
      this.pendingInjections = [];
    }

    // 3. Session-scoped cron sweep (pre-existing behaviour).
    const ids = this.meta.crons ?? [];
    if (ids.length > 0) {
      // Copy first — scheduler.delete may interact with other state
      // and we want to clear the list even on partial delete failure
      // so the Session doesn't keep retrying the same dead ids.
      const toDrop = [...ids];
      this.meta.crons = [];
      for (const cronId of toDrop) {
        // Only delete session-owned crons. The scheduler's `delete`
        // is idempotent so a missing id is a no-op. Defence-in-depth:
        // we check the scheduler's copy of sessionKey before removing.
        const rec = this.agent.crons?.get(cronId);
        if (!rec) continue;
        if (rec.durable) continue; // somebody upgraded it; don't drop
        if (rec.sessionKey && rec.sessionKey !== this.meta.sessionKey) continue;
        try {
          await this.agent.crons.delete(cronId);
        } catch (err) {
          console.warn(
            `[core-agent] session close: cron delete failed cronId=${cronId}: ${(err as Error).message}`,
          );
        }
      }
    }

    // 4. Observability event — Agent.emitAgentEvent is optional so
    //    tests that use a minimal Agent stub don't need to wire a
    //    listener unless they're asserting on the event.
    this.agent.emitAgentEvent?.({
      type: "session_closed",
      sessionKey: this.meta.sessionKey,
      ...(reason ? { reason } : {}),
      closedAt: Date.now(),
    });
  }

  // ── mid-turn injection API (#86) ───────────────────────────────
  /**
   * Queue a user message to be absorbed into the currently-streaming
   * turn's next LLM iteration. Caller should verify a turn is active
   * before calling (route handler returns 409 otherwise).
   *
   * Returns `{ injectionId }` on success, or `null` if the queue is
   * already at `MAX_PENDING_INJECTIONS` — client retries later.
   */
  injectMessage(
    text: string,
    source: "web" | "mobile" | "telegram" | "discord" | "api" = "api",
  ): { injectionId: string; queuedCount: number } | null {
    if (this.pendingInjections.length >= Session.MAX_PENDING_INJECTIONS) {
      return null;
    }
    this.injectionSeq += 1;
    const injectionId = `inj-${this.meta.sessionKey}-${this.injectionSeq}`;
    this.pendingInjections.push({
      text,
      receivedAt: Date.now(),
      metadata: { injection: { id: injectionId, source } },
    });
    const count = this.pendingInjections.length;
    // Notify the client that the message was queued. If the active turn
    // is currently inside an LLM stream, ask it to restart at the next
    // iteration immediately; if it is running a tool, the injector still
    // drains at the next beforeLLMCall boundary after that tool returns.
    if (this._activeSse) {
      try {
        this._activeSse.agent({
          type: "injection_queued",
          injectionId,
          text,
          queuedCount: count,
        });
      } catch { /* fail-open */ }
    }
    this.activeTurn?.requestSteerResume(source);
    return { injectionId, queuedCount: count };
  }

  requestInterrupt(
    handoffRequested = false,
    source = "api",
  ): { status: "accepted" | "noop"; handoffRequested: boolean } {
    if (!this.activeTurn) {
      return { status: "noop", handoffRequested: false };
    }
    const activeTurn = this.activeTurn;
    const result = activeTurn.requestInterrupt(handoffRequested, source);
    if (result.status === "accepted" && !handoffRequested) {
      const missionId = metadataString(activeTurn.userMessage.metadata, "missionId");
      if (activeTurn.userMessage.metadata?.goalMode === true && missionId) {
        const newlyCancelled = this.markGoalMissionCancelled(missionId);
        if (newlyCancelled) {
          const missionRunId = metadataString(activeTurn.userMessage.metadata, "missionRunId");
          void this.appendGoalCancelledEvent({
            missionId,
            ...(missionRunId ? { missionRunId } : {}),
            reason: "user_interrupt",
          });
        }
      }
    }
    return result;
  }

  /**
   * Atomic drain — returns all queued injections and empties the
   * queue. Called by the midTurnInjector hook at the start of each
   * LLM iteration.
   */
  drainPendingInjections(): UserMessage[] {
    return this.pendingInjections.splice(0);
  }

  hasPendingInjections(): boolean {
    return this.pendingInjections.length > 0;
  }

  enqueueHiddenContext(message: string): void {
    if (message.trim().length === 0) return;
    this.pendingHiddenContext.push(message);
  }

  drainPendingHiddenContext(): string[] {
    return this.pendingHiddenContext.splice(0);
  }

  setStructuredOutputContract(spec: StructuredOutputSpec | null): void {
    this.structuredOutputContract = spec;
  }

  getStructuredOutputContract(): StructuredOutputSpec | null {
    return this.structuredOutputContract;
  }

  /** Read-only peek for diagnostics / status command. */
  peekPendingInjectionCount(): number {
    return this.pendingInjections.length;
  }

  private async prepareGoalLoopMessage(
    userMessage: UserMessage,
    sse: SseWriter,
    turnId: string,
    goalMode: boolean,
  ): Promise<UserMessage> {
    if (!goalLoopEnabled()) return userMessage;
    const goalRequest = goalRequestFromMessage({
      text: userMessage.text,
      goalMode,
    });
    if (!goalRequest) return userMessage;
    const goalSpec = await distillGoalSpec({
      llm: this.agent.llm,
      model: this.agent.config.model,
      rawRequest: goalRequest.objective,
    });

    const maxTurns = goalLoopMaxTurns();
    const metadata: NonNullable<UserMessage["metadata"]> = {
      ...(userMessage.metadata ?? {}),
      goalMode: true,
      missionKind: "goal",
      missionTitle: goalSpec.title,
      goalObjective: goalSpec.objective,
      goalCompletionCriteria: goalSpec.completionCriteria,
      goalSourceRequest: goalRequest.objective,
      goalTurnsUsed: 0,
      goalMaxTurns: maxTurns,
    };

    try {
      const mission = await this.agent.missionClient.createMission({
        channelType: this.meta.channel.type as MissionChannelType,
        channelId: this.meta.channel.channelId,
        kind: "goal",
        title: truncateGoalText(goalSpec.title, 240),
        summary: truncateGoalText(goalSpec.objective, 500),
        status: "running",
        createdBy: "user",
        metadata: {
          objective: goalSpec.objective,
          sourceRequest: goalRequest.objective,
          completionCriteria: goalSpec.completionCriteria,
          sessionKey: this.meta.sessionKey,
          turnId,
          turnsUsed: 0,
          maxTurns,
        },
      });
      const run = await this.agent.missionClient.createRun(mission.id, {
        triggerType: "user",
        status: "running",
        sessionKey: this.meta.sessionKey,
        turnId,
        metadata: {
          objective: goalSpec.objective,
          sourceRequest: goalRequest.objective,
          completionCriteria: goalSpec.completionCriteria,
          turnsUsed: 0,
          maxTurns,
        },
      });
      metadata.missionId = mission.id;
      if (typeof run.id === "string") metadata.missionRunId = run.id;
      sse.agent({ type: "mission_created", mission });
      sse.agent({
        type: "mission_event",
        missionId: mission.id,
        eventType: "created",
        message: "Goal mission started",
      });
    } catch (err) {
      console.warn(
        `[core-agent] goal mission create failed sessionKey=${this.meta.sessionKey}: ${(err as Error).message}`,
      );
    }

    return {
      ...userMessage,
      text: goalRequest.text,
      metadata,
    };
  }

  private async prepareGoalContinuationRun(
    userMessage: UserMessage,
    sse: SseWriter,
    turnId: string,
  ): Promise<UserMessage> {
    if (!goalLoopEnabled()) return userMessage;
    const metadata = userMessage.metadata;
    if (metadata?.goalContinuation !== true) return userMessage;
    const missionId = metadataString(metadata, "missionId");
    if (!missionId) return userMessage;

    const objective = metadataString(metadata, "goalObjective") ?? userMessage.text;
    const title = metadataString(metadata, "missionTitle") ?? objective;
    const completionCriteria = metadataStringArray(metadata, "goalCompletionCriteria");
    const nextMetadata: NonNullable<UserMessage["metadata"]> = {
      ...metadata,
      goalMode: true,
      missionKind: "goal",
      missionTitle: title,
      goalObjective: objective,
      goalCompletionCriteria: completionCriteria,
    };
    const resumeAfterRestart = metadata.goalResumeAfterRestart === true;
    const actionEventId = metadataString(metadata, "goalResumeActionEventId");
    try {
      const run = await this.agent.missionClient.createRun(missionId, {
        triggerType: resumeAfterRestart ? "resume" : "goal_continue",
        status: "running",
        sessionKey: this.meta.sessionKey,
        turnId,
        metadata: {
          objective,
          completionCriteria,
          turnsUsed: metadataNumber(metadata, "goalTurnsUsed") ?? 0,
          ...(resumeAfterRestart ? { restartRecovery: true } : {}),
          ...(actionEventId ? { actionEventId } : {}),
        },
      });
      if (typeof run.id === "string") nextMetadata.missionRunId = run.id;
      sse.agent({
        type: "mission_event",
        missionId,
        eventType: "heartbeat",
        message: "Goal continuation started",
      });
    } catch (err) {
      console.warn(
        `[core-agent] goal continuation run create failed sessionKey=${this.meta.sessionKey}: ${(err as Error).message}`,
      );
    }
    return { ...userMessage, metadata: nextMetadata };
  }

  private async appendGoalMissionEvent(input: {
    missionId: string;
    missionRunId?: string;
    eventType: "heartbeat" | "completed" | "blocked" | "failed" | "resumed" | "cancelled";
    message?: string;
    payload?: Record<string, unknown>;
  }): Promise<void> {
    try {
      await this.agent.missionClient.appendEvent(input.missionId, {
        ...(input.missionRunId ? { runId: input.missionRunId } : {}),
        actorType: "system",
        eventType: input.eventType,
        ...(input.message ? { message: input.message } : {}),
        payload: input.payload ?? {},
      });
    } catch (err) {
      console.warn(
        `[core-agent] goal mission event append failed missionId=${input.missionId}: ${(err as Error).message}`,
      );
    }
  }

  private isGoalMissionCancelled(missionId: string): boolean {
    const agent = this.agent as Agent & {
      isGoalMissionCancelled?: (missionId: string) => boolean;
    };
    return agent.isGoalMissionCancelled?.(missionId) ?? false;
  }

  private markGoalMissionCancelled(missionId: string): boolean {
    const agent = this.agent as Agent & {
      markGoalMissionCancelled?: (missionId: string) => boolean;
    };
    return agent.markGoalMissionCancelled?.(missionId) ?? true;
  }

  private async appendGoalCancelledEvent(input: {
    missionId: string;
    missionRunId?: string;
    reason: string;
    actionEventId?: string;
  }): Promise<void> {
    await this.appendGoalMissionEvent({
      missionId: input.missionId,
      ...(input.missionRunId ? { missionRunId: input.missionRunId } : {}),
      eventType: "cancelled",
      message: "Goal mission cancelled",
      payload: {
        reason: input.reason,
        ...(input.actionEventId ? { actionEventId: input.actionEventId } : {}),
      },
    });
  }

  private recordGoalJudgeContractEvidence(input: {
    objective: string;
    missionId: string;
    decision: string;
    reason: string;
    turnsUsed: number;
    maxTurns: number;
    status: "passed" | "failed" | "partial" | "unknown";
  }): void {
    this.executionContract.recordVerificationEvidence({
      source: "hook",
      status: input.status,
      detail:
        input.status === "passed"
          ? `Goal judge marked complete: ${input.reason || "Goal completed"}`
          : `Goal judge marked ${input.decision}: ${input.reason || "Goal not complete"}`,
      assertions: [
        `decision=${input.decision}`,
        `objective=${input.objective}`,
        `turnsUsed=${input.turnsUsed}`,
        `maxTurns=${input.maxTurns}`,
      ],
      resourceIds: [input.missionId],
    });
  }

  private scheduleGoalContinuation(message: UserMessage): void {
    const timer = setTimeout(() => {
      void this.runGoalContinuation(message).catch((err: unknown) => {
        console.warn(
          `[core-agent] goal continuation failed sessionKey=${this.meta.sessionKey}: ${(err as Error).message}`,
        );
      });
    }, 0);
    timer.unref?.();
  }

  private async runGoalContinuation(message: UserMessage): Promise<void> {
    const missionId = metadataString(message.metadata, "missionId");
    if (missionId && this.isGoalMissionCancelled(missionId)) return;
    const result = await this.runTurn(message, new StubSseWriter());
    if (missionId && this.isGoalMissionCancelled(missionId)) return;
    const text = result.assistantText.trim();
    if (text.length === 0) return;
    await this.agent.deliverAssistantTextToChannel(this.meta.channel, text, "goal");
  }

  async resumeGoalAfterRestart(
    input: Omit<GoalMissionResumeInput, "sessionKey" | "channel">,
  ): Promise<void> {
    if (this.isGoalMissionCancelled(input.missionId)) return;
    const maxTurns = input.maxTurns ?? goalLoopMaxTurns();
    const resumeMessage = buildGoalContinuationMessage({
      objective: input.objective,
      title: input.title,
      completionCriteria: input.completionCriteria,
      missionId: input.missionId,
      turnsUsed: input.turnsUsed,
      maxTurns,
      previousAssistantText:
        input.resumeContext ?? "Runtime restarted before this goal finished.",
      reason: "Restart recovery requested a fresh continuation.",
    });
    resumeMessage.metadata = {
      ...(resumeMessage.metadata ?? {}),
      goalResumeAfterRestart: true,
      goalResumeActionEventId: input.actionEventId,
      ...(input.startedAt ? { goalRestartedAt: input.startedAt } : {}),
      ...(input.sourceRequest ? { goalSourceRequest: input.sourceRequest } : {}),
    };

    await this.appendGoalMissionEvent({
      missionId: input.missionId,
      eventType: "resumed",
      message: "Goal mission resumed after restart",
      payload: {
        actionEventId: input.actionEventId,
        reason: "restart_recovery",
        ...(input.startedAt ? { startedAt: input.startedAt } : {}),
      },
    });

    const result = await this.runTurn(resumeMessage, new StubSseWriter());
    const text = result.assistantText.trim();
    if (text.length === 0) return;
    await this.agent.deliverAssistantTextToChannel(this.meta.channel, text, "goal");
  }

  private async evaluateGoalContinuation(
    userMessage: UserMessage,
    assistantText: string,
  ): Promise<void> {
    if (!goalLoopEnabled()) return;
    const metadata = userMessage.metadata;
    if (metadata?.goalMode !== true) return;
    const missionId = metadataString(metadata, "missionId");
    const objective = metadataString(metadata, "goalObjective");
    if (!missionId || !objective) return;
    const title = metadataString(metadata, "missionTitle") ?? objective;
    const completionCriteria = metadataStringArray(metadata, "goalCompletionCriteria");

    const turnsUsed = (metadataNumber(metadata, "goalTurnsUsed") ?? 0) + 1;
    const maxTurns = metadataNumber(metadata, "goalMaxTurns") ?? goalLoopMaxTurns();
    const missionRunId = metadataString(metadata, "missionRunId");
    if (this.isGoalMissionCancelled(missionId)) {
      await this.appendGoalCancelledEvent({
        missionId,
        ...(missionRunId ? { missionRunId } : {}),
        reason: "mission_cancel_requested",
      });
      return;
    }
    let decision;
    try {
      decision = await judgeGoalTurn({
        llm: this.agent.llm,
        model: this.agent.config.model,
        objective,
        completionCriteria,
        userText: userMessage.text,
        assistantText,
      });
    } catch (err) {
      await this.appendGoalMissionEvent({
        missionId,
        missionRunId,
        eventType: "blocked",
        message: (err as Error).message,
        payload: { reason: "goal_judge_failed", turnsUsed, maxTurns },
      });
      return;
    }
    if (this.isGoalMissionCancelled(missionId)) {
      await this.appendGoalCancelledEvent({
        missionId,
        ...(missionRunId ? { missionRunId } : {}),
        reason: "mission_cancel_requested",
      });
      return;
    }

    if (decision.decision === "done") {
      this.recordGoalJudgeContractEvidence({
        objective,
        missionId,
        decision: decision.decision,
        reason: decision.reason,
        turnsUsed,
        maxTurns,
        status: "passed",
      });
      await this.appendGoalMissionEvent({
        missionId,
        missionRunId,
        eventType: "completed",
        message: decision.reason || "Goal completed",
        payload: { turnsUsed, maxTurns },
      });
      return;
    }

    if (decision.decision === "blocked" || decision.decision === "needs_user") {
      this.recordGoalJudgeContractEvidence({
        objective,
        missionId,
        decision: decision.decision,
        reason: decision.reason,
        turnsUsed,
        maxTurns,
        status: "partial",
      });
      await this.appendGoalMissionEvent({
        missionId,
        missionRunId,
        eventType: "blocked",
        message: decision.reason || "Goal needs user input",
        payload: { decision: decision.decision, turnsUsed, maxTurns },
      });
      return;
    }

    const state = {
      missionId,
      objective,
      turnsUsed,
      maxTurns,
      paused: false,
      cancelled: false,
    };
    if (!canContinueGoal(state)) {
      this.recordGoalJudgeContractEvidence({
        objective,
        missionId,
        decision: "budget_exhausted",
        reason: "Goal turn budget exhausted",
        turnsUsed,
        maxTurns,
        status: "partial",
      });
      await this.appendGoalMissionEvent({
        missionId,
        missionRunId,
        eventType: "blocked",
        message: "Goal turn budget exhausted",
        payload: { turnsUsed, maxTurns },
      });
      return;
    }

    await this.appendGoalMissionEvent({
      missionId,
      missionRunId,
      eventType: "heartbeat",
      message: "Goal continuation scheduled",
      payload: { reason: decision.reason, turnsUsed, maxTurns },
    });
    this.scheduleGoalContinuation(
      buildGoalContinuationMessage({
        objective,
        title,
        completionCriteria,
        missionId,
        missionRunId,
        turnsUsed,
        maxTurns,
        previousAssistantText: assistantText,
        reason: decision.reason,
      }),
    );
  }

  async runTurn(
    userMessage: UserMessage,
    sse: SseWriter,
    options: {
      planMode?: boolean;
      contextId?: string;
      runtimeModelOverride?: string;
      goalMode?: boolean;
    } = {},
  ): Promise<TurnResult> {
    return this.mutex.run(async () => {
      this.meta.lastActivityAt = Date.now();

      // Built-in slash commands (`/compact`, `/reset`, `/status`). Must
      // run inside the mutex so the handler sees a stable transcript,
      // but BEFORE any Turn is constructed — slash commands are
      // non-LLM, non-billed, and must not touch the budget counters.
      // Unknown `/foo` falls through to the normal LLM path.
      const slashMatch = matchSlashCommand(
        userMessage.text,
        this.agent.slashCommands,
      );
      if (slashMatch) {
        const turnId = this.agent.nextTurnId();
        const startedAt = Date.now();
        let slashStopReason: "end_turn" | "aborted" = "end_turn";
        let slashStatus: "committed" | "aborted" = "committed";
        sse.agent({
          type: "turn_start",
          turnId,
          declaredRoute: "direct",
        });
        try {
          await slashMatch.command.handler(slashMatch.args, {
            session: this,
            sse,
            ...(options.runtimeModelOverride ? { runtimeModelOverride: options.runtimeModelOverride } : {}),
          });
          sse.agent({
            type: "turn_end",
            turnId,
            status: "committed",
            stopReason: "end_turn",
          });
          // Legacy OpenAI-compat terminator so chat-proxy parsers close
          // the streaming response cleanly.
          sse.legacyFinish();
        } catch (err) {
          const msg = err instanceof Error ? err.message : String(err);
          sse.agent({
            type: "error",
            code: "slash_command_failed",
            message: msg,
          });
          slashStopReason = "aborted";
          slashStatus = "aborted";
          sse.agent({
            type: "turn_end",
            turnId,
            status: "aborted",
            stopReason: "aborted",
            reason: msg,
          });
        }
        return {
          meta: {
            turnId,
            sessionKey: this.meta.sessionKey,
            startedAt,
            endedAt: Date.now(),
            declaredRoute: "direct",
            status: slashStatus,
            usage: { inputTokens: 0, outputTokens: 0, costUsd: 0 },
            stopReason: slashStopReason,
          },
          assistantText: "",
          stopReason: slashStopReason,
        };
      }

      // T4-19: optional explicit context switch for this turn.
      if (options.contextId) {
        try {
          await this.switchContext(options.contextId);
        } catch {
          // Unknown / archived context — fall through to the active
          // context rather than failing the whole turn.
        }
      }
      await this.hydrateMetaIfNeeded();
      const turnId = this.agent.nextTurnId();
      const goalLoopMessage = await this.prepareGoalLoopMessage(
        userMessage,
        sse,
        turnId,
        options.goalMode === true,
      );
      const effectiveUserMessage = await this.prepareGoalContinuationRun(
        goalLoopMessage,
        sse,
        turnId,
      );
      this.executionContract.startTurn({
        userMessage: effectiveUserMessage.text,
        metadata: effectiveUserMessage.metadata,
      });
      const active = this.getActiveContext();
      sse.agent({
        type: "context_activated",
        contextId: active.meta.contextId,
        title: active.meta.title,
      });
      // T2-08 backward-compat: translate legacy `planMode` option +
      // inline `[PLAN_MODE: on]` marker into the session permission
      // posture. Turn.ts still reads the options flag for its local
      // `planMode` boolean, but the tool-filter decision is now driven
      // by Session.getPermissionMode().
      const planRequested =
        options.planMode === true ||
        /\[PLAN_MODE:\s*on\]/i.test(effectiveUserMessage.text);
      if (planRequested && this.permissionMode !== "plan") {
        this.setPermissionMode("plan");
      }
      const turn = new Turn(this, effectiveUserMessage, turnId, sse, "direct", options);
      this.activeTurn = turn;
      this.agent.registerTurn(turn);
      let committedAssistantText = "";
      try {
        // Gap §11.6 — reject a turn BEFORE it starts if the routed
        // model's context window can't hold the reserve floor + the
        // minimum viable live budget. Throwing here falls into the
        // CompactionImpossibleError branch below so the user sees the
        // Korean "switch to a larger model" notice instead of a silent
        // compaction-loop timeout.
        const runtimeModel = await turn.resolveRuntimeModel();
        this.agent.contextEngine.assertCompactionFeasible(runtimeModel);
        await turn.execute();
        turn.assertNotInterrupted();
        const report = await turn.verify();
        turn.assertNotInterrupted();
        if (!report.ok) {
          const reason = `verify failed: ${report.violations.join(",")}`;
          emitTerminalAbortFallback(sse, reason);
          await turn.abort(reason);
        } else {
          turn.assertNotInterrupted();
          const commitResult = await turn.commitWithRetry();
          turn.assertNotInterrupted();
          committedAssistantText = commitResult.finalText;
        }
      } catch (err) {
        if (err instanceof TurnInterruptedError) {
          await turn.abort(err.message);
          return {
            meta: turn.meta,
            assistantText: committedAssistantText,
            stopReason: turn.meta.stopReason ?? "aborted",
          };
        }
        // Gap §11.6 — small-context-model routing ran into the
        // compaction reserve-token floor. Emit the structured
        // telemetry + a Korean user-facing text_delta so the client
        // can prompt the user to switch models, then abort cleanly.
        if (err instanceof CompactionImpossibleError) {
          sse.agent({
            type: "compaction_impossible",
            model: err.model,
            contextWindow: err.contextWindow,
            effectiveReserveTokens: err.effectiveReserveTokens,
            effectiveBudgetTokens: err.effectiveBudgetTokens,
            minViableBudgetTokens: err.minViableBudgetTokens,
          });
          const userMsg =
            "모델 컨텍스트 창이 너무 작아 compaction이 불가능합니다. 더 큰 컨텍스트 모델로 전환하세요.";
          // Emit on the agent channel only — see LLMStreamReader.ts for
          // the dual-emit regression context.
          sse.agent({ type: "text_delta", delta: userMsg });
          await turn.abort(err.message, "compaction_impossible");
        } else {
          const msg = err instanceof Error ? err.message : String(err);
          sse.agent({ type: "error", code: "turn_failed", message: msg });
          emitTerminalAbortFallback(sse, msg);
          await turn.abort(msg);
        }
      } finally {
        this.activeTurn = null;
        // T1-06: session budget (tenant-aggregate). T4-19: also
        // per-context budget for UI stats.
        this.recordTurnUsage(turn.meta.usage);
        active.recordTurn(turn.meta.usage);
        this.agent.unregisterTurn(turnId);
      }
      const result = {
        meta: turn.meta,
        assistantText: committedAssistantText,
        stopReason: turn.meta.stopReason ?? "aborted",
      };
      if (turn.meta.status === "committed" && committedAssistantText.trim().length > 0) {
        void this.evaluateGoalContinuation(effectiveUserMessage, committedAssistantText);
      }
      return result;
    });
  }

  async listActiveBackgroundTasks(): Promise<BackgroundTask[]> {
    return [];
  }

  async stats(): Promise<SessionStats> {
    return { turnsCommitted: 0, turnsAborted: 0 };
  }
}

/**
 * Parse persona hint from sessionKey. Format observed today:
 *   agent:<persona>:<channel>:<channelId>:<bucket>
 * e.g. `agent:main:app:general:7`, `agent:researcher:app:analysis:3`.
 */
export function personaFromSessionKey(sessionKey: string): string | undefined {
  const parts = sessionKey.split(":");
  if (parts[0] === "agent" && parts.length >= 2) return parts[1];
  return undefined;
}
