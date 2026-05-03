/**
 * Turn — atomic transaction lifecycle (§5.3 / §6-A invariant A).
 *
 * Thin coordinator after R3. Real work lives in `turn/*`:
 *   LLMStreamReader | ToolDispatcher | MessageBuilder | StopReasonHandler
 *   CommitPipeline | ToolSelector | AskUserController | HookContextBuilder
 */

import type { Session } from "./Session.js";
import type { UserMessage } from "./util/types.js";
import type { SseWriter } from "./transport/SseWriter.js";
import type { LLMContentBlock, LLMMessage, LLMToolDef, LLMUsage } from "./transport/LLMClient.js";
import type { AskUserQuestionOutput } from "./Tool.js";
import type { HookPoint } from "./hooks/types.js";
import { computeUsd } from "./llm/modelCapabilities.js";
import {
  appendRuntimeModelIdentityContext,
  buildSystemPrompt,
  buildMessages,
} from "./turn/MessageBuilder.js";
import { readOne as readOneStream } from "./turn/LLMStreamReader.js";
import { dispatch as dispatchTools, type ToolDispatchResult, UnknownToolLoopError } from "./turn/ToolDispatcher.js";
import { handle as handleStopReason } from "./turn/StopReasonHandler.js";
import {
  commit as commitTurn,
  abort as abortTurn,
  type CommitPipelineContext,
  type CommitResult,
} from "./turn/CommitPipeline.js";
import { buildToolDefs } from "./turn/ToolSelector.js";
import { AskUserController } from "./turn/AskUserController.js";
import { buildHookContext } from "./turn/HookContextBuilder.js";
import { HeartbeatMonitor, wrapSseWithMonitor } from "./turn/HeartbeatMonitor.js";
import { SessionHeartbeat } from "./turn/SessionHeartbeat.js";
import { RetryController } from "./turn/RetryController.js";
import type { TurnRoute, TurnStatus, TurnMeta, TurnStopReason, PlanResult, VerificationReport } from "./turn/types.js";
import { messagesHaveImages } from "./routing/messageText.js";
import { isRouterKeyword } from "./routing/types.js";

// Re-exports for callers + tests.
export type { TurnRoute, TurnStatus, TurnMeta, TurnStopReason, PlanResult, VerificationReport, TurnResult } from "./turn/types.js";
export { PLAN_MODE_ALLOWED_TOOLS } from "./turn/ToolSelector.js";
export { MAX_OUTPUT_TOKENS_RECOVERY_LIMIT, classifyStopReason, type StopReasonCase } from "./turn/StopReasonHandler.js";

/** Case-insensitive `[PLAN_MODE: on]` header trigger. */
export const PLAN_MODE_HEADER_RE = /\[PLAN_MODE:\s*on\]/i;
const DEFAULT_EMPTY_RESPONSE_FALLBACK_MODEL = "claude-haiku-4-5-20251001";

function emptyResponseFallbackModel(): string | null {
  const raw = process.env.CORE_AGENT_EMPTY_RESPONSE_FALLBACK_MODEL?.trim();
  if (raw && /^(?:off|none|disabled)$/i.test(raw)) return null;
  return raw && raw.length > 0 ? raw : DEFAULT_EMPTY_RESPONSE_FALLBACK_MODEL;
}

function truncationGuardEnding(text: string): {
  readonly looksTruncated: boolean;
  readonly lastChar: string;
} {
  const trimmed = text.trimEnd().replace(/[\uFE0E\uFE0F]+$/u, "");
  const chars = Array.from(trimmed);
  const lastChar = chars.at(-1) ?? "";
  if (trimmed.length <= 200) return { looksTruncated: false, lastChar };

  // Terminal punctuation or a deliberate trailing symbol/emoji usually
  // means the answer is complete even if it does not end in ".".
  const terminalRe = /[.!?。！？…\n\r)）」』】\]}'"`]$/u;
  if (
    terminalRe.test(lastChar) ||
    /\p{Extended_Pictographic}/u.test(lastChar) ||
    /\p{Symbol}/u.test(lastChar)
  ) {
    return { looksTruncated: false, lastChar };
  }

  const tail = trimmed.slice(-180).trim();
  const fencedCodeTicks = trimmed.match(/```/g)?.length ?? 0;
  if (fencedCodeTicks % 2 === 1) {
    return { looksTruncated: true, lastChar };
  }

  // Mentions, URLs, and identifiers are common deliberate final
  // tokens in short status reports. A bare alphanumeric last character
  // is not enough evidence to treat an end_turn response as cut off.
  if (
    /(?:^|\s)@[A-Za-z0-9_]{2,}$/u.test(tail) ||
    /https?:\/\/\S+$/iu.test(tail) ||
    /(?:^|\s)[A-Za-z0-9_.-]+#[A-Za-z0-9_.-]+$/u.test(tail)
  ) {
    return { looksTruncated: false, lastChar };
  }

  const danglingPunctuationRe = /[,;:，、；：]$/u;
  const danglingKoreanRe =
    /(?:원인은|이유는|문제는|핵심은|다음은|경우에는|때문에|그리고|하지만|그러나|다만|또는|해야 하며|하며|으로|로|에게|에서|부터|까지|은|는|이|가|을|를)$/u;
  const danglingEnglishRe =
    /\b(?:because|and|or|but|with|without|the|a|an|to|of|for|from|as|by|if|when|while|where|which|that|is|are|was|were|will|can|could|should|would)$/iu;

  return {
    looksTruncated:
      danglingPunctuationRe.test(lastChar) ||
      danglingKoreanRe.test(tail) ||
      danglingEnglishRe.test(tail),
    lastChar,
  };
}

export class TurnInterruptedError extends Error {
  readonly handoffRequested: boolean;
  readonly source: string;

  constructor(
    reason: "user_interrupt" | "user_interrupt_handoff",
    handoffRequested: boolean,
    source: string,
  ) {
    super(reason);
    this.name = "TurnInterruptedError";
    this.handoffRequested = handoffRequested;
    this.source = source;
  }
}

function normalizeAssistantReplayBlocks(blocks: LLMContentBlock[]): LLMContentBlock[] {
  const toolUseBlocks = blocks.filter((block) => block.type === "tool_use");
  if (toolUseBlocks.length === 0) return blocks;
  const prefixBlocks = blocks.filter((block) => block.type !== "tool_use");
  return [...prefixBlocks, ...toolUseBlocks];
}

function finalAssistantReplayBlocks(blocks: LLMContentBlock[]): LLMContentBlock[] {
  return normalizeAssistantReplayBlocks(blocks).filter((block) => block.type !== "tool_use");
}

export class Turn {
  readonly meta: TurnMeta;
  /**
   * Max tool-use iterations per turn (bounds runaway loops).
   * 2026-04-20 0.17.1: bumped 25 → 200 for Claude Code parity. Admin
   * bot hit 17/25 on POS deep-dive and couldn't finish analysis+report
   * after data collection. 200 matches Claude Code's effective no-cap.
   * Env override: CORE_AGENT_MAX_TURN_ITERATIONS (clamped to 5..1000).
   */
  private static readonly MAX_ITERATIONS = (() => {
    const raw = process.env.CORE_AGENT_MAX_TURN_ITERATIONS;
    const parsed = raw !== undefined ? Number.parseInt(raw, 10) : NaN;
    return Number.isFinite(parsed) && parsed >= 5 && parsed <= 1000 ? parsed : 200;
  })();

  private planMode = false;
  private readonly asks: AskUserController;

  private assistantText = "";
  private emittedAssistantBlocks: LLMContentBlock[] = [];
  private canonicalAssistantMessages: LLMContentBlock[][] = [];
  private retryBaseMessages: LLMMessage[] = [];
  private recoveryAttempt = 0;
  /** Separate counter for empty-response recovery (thinking-only or
   * tool_use-only turns where no text block was emitted). Independent
   * from max_tokens recoveryAttempt so the two don't interfere. */
  private emptyResponseRetry = 0;
  static readonly MAX_EMPTY_RESPONSE_RETRIES = 3;
  /** Counter for truncated responses (text ends mid-sentence). */
  private truncationRecovery = 0;
  static readonly MAX_TRUNCATION_RETRIES = 2;
  /** When true, the next LLM call disables thinking to force text output. */
  private forceNoThinking = false;
  /** One-shot model override used only after repeated empty visible output. */
  private emptyResponseModelOverride: string | null = null;
  private emptyResponseFallbackUsed = false;
  /** Runtime model resolved once at turn start. */
  private runtimeModel: string | null = null;
  /** Gap §11.3 unknown-tool hallucination counter — shared across every
   * dispatchTools call within this turn. */
  private unknownToolCount = 0;
  private _commitRetryCount = 0;
  get commitRetryCount(): number { return this._commitRetryCount; }
  /** Increment commit retry counter — called by Session.runTurn
   * when beforeCommit hooks block and we retry. */
  incrementCommitRetry(): void { this._commitRetryCount += 1; }
  private interruptState: {
    handoffRequested: boolean;
    source: string;
    requestedAt: number;
  } | null = null;
  private readonly interruptController = new AbortController();

  requestInterrupt(
    handoffRequested = false,
    source = "api",
  ): { status: "accepted" | "noop"; handoffRequested: boolean } {
    if (this.meta.status === "committed" || this.meta.status === "aborted") {
      return { status: "noop", handoffRequested: false };
    }
    const mergedHandoff =
      handoffRequested || this.interruptState?.handoffRequested === true;
    this.interruptState = {
      handoffRequested: mergedHandoff,
      source,
      requestedAt: Date.now(),
    };
    if (!this.interruptController.signal.aborted) {
      const reason = mergedHandoff ? "user_interrupt_handoff" : "user_interrupt";
      this.interruptController.abort(new TurnInterruptedError(reason, mergedHandoff, source));
    }
    this.sse.agent({
      type: "turn_interrupted",
      turnId: this.meta.turnId,
      handoffRequested: mergedHandoff,
      source,
    });
    return { status: "accepted", handoffRequested: mergedHandoff };
  }

  assertNotInterrupted(): void {
    this.throwIfInterrupted();
  }

  constructor(
    readonly session: Session,
    readonly userMessage: UserMessage,
    turnId: string,
    readonly sse: SseWriter,
    declaredRoute: TurnRoute = "direct",
    options: { planMode?: boolean } = {},
  ) {
    this.meta = {
      turnId,
      sessionKey: session.meta.sessionKey,
      startedAt: Date.now(),
      declaredRoute,
      status: "pending",
      usage: { inputTokens: 0, outputTokens: 0, costUsd: 0 },
      configuredModel: session.agent.config.model,
      effectiveModel: session.agent.config.model,
    };
    this.asks = new AskUserController(turnId, sse);
    // T2-08 — Session permission mode is authoritative.
    const sessionInPlan = safeGetPermissionMode(session) === "plan";
    this.planMode =
      sessionInPlan ||
      options.planMode === true ||
      PLAN_MODE_HEADER_RE.test(userMessage.text);
    if (this.planMode && !sessionInPlan && typeof session.setPermissionMode === "function") {
      session.setPermissionMode("plan");
    }
  }

  // ── plan-mode surface ────────────────────────────────────────────
  isPlanMode(): boolean {
    return safeGetPermissionMode(this.session) === "plan" || this.planMode;
  }

  exitPlanMode(): void {
    this.planMode = false;
    if (typeof this.session.exitPlanMode === "function") {
      this.session.exitPlanMode();
    }
  }

  // ── askUser surface ──────────────────────────────────────────────
  resolveAsk(questionId: string, answer: AskUserQuestionOutput): boolean {
    return this.asks.resolve(questionId, answer);
  }

  // ── lifecycle ────────────────────────────────────────────────────
  async plan(): Promise<PlanResult | null> { return null; }
  async verify(): Promise<VerificationReport> { return { ok: true, violations: [] }; }

  /** Read-only accessor used by T1-04 test harness. */
  getRecoveryAttempt(): number { return this.recoveryAttempt; }

  async resolveRuntimeModel(): Promise<string> {
    if (!this.runtimeModel) {
      const resolver = (this.session.agent as {
        resolveRuntimeModel?: () => Promise<string>;
      }).resolveRuntimeModel;
      this.runtimeModel = typeof resolver === "function"
        ? await resolver.call(this.session.agent)
        : this.session.agent.config.model;
    }
    return this.runtimeModel;
  }

  async execute(): Promise<void> {
    this.sse.agent({ type: "turn_start", turnId: this.meta.turnId, declaredRoute: this.meta.declaredRoute });
    const preStart = await this.session.agent.hooks.runPre(
      "beforeTurnStart",
      { userMessage: this.userMessage.text },
      this.hookCtx("beforeTurnStart"),
    );
    if (preStart.action === "block") {
      const reason = preStart.reason ?? "Turn blocked by hook";
      this.sse.agent({ type: "text_delta", delta: `⚠️ ${reason}` });
      throw new Error(`beforeTurnStart blocked: ${reason}`);
    }
    this.setPhase("executing");
    this.checkBudgetOrThrow();
    const runtimeModel = await this.resolveRuntimeModel();
    await this.appendTurnPrologue();

    let systemPrompt = await buildSystemPrompt(
      this.session,
      this.meta.turnId,
      this.userMessage,
    );
    let messages = await buildMessages(this.session, this.userMessage, runtimeModel);
    // B5 — heartbeat monitor wraps this.sse so every downstream SSE
    // emission pings the silence timer. Long-running tool calls + LLM
    // streams that go silent > 20s produce `heartbeat` events on a
    // 30s cadence until activity resumes.
    const heartbeat = new HeartbeatMonitor({ sse: this.sse, turnId: this.meta.turnId });
    const sse = wrapSseWithMonitor(this.sse, heartbeat);
    // B5 session-alive heartbeat file — writes heartbeat.json every 10s
    // so external callers can cheaply verify session liveness.
    const sessionHeartbeat = new SessionHeartbeat({
      workspaceRoot: this.session.agent.config.workspaceRoot,
      sessionKey: this.session.meta.sessionKey,
    });
    let toolDefs = await buildToolDefs({
      session: this.session, sse, turnId: this.meta.turnId,
      userText: this.userMessage.text,
      planMode: this.planMode || safeGetPermissionMode(this.session) === "plan",
    });

    // #86: register SSE writer so Session.injectMessage() can emit
    // injection_queued events to the client during this turn.
    this.session.setActiveSse(this.sse);

    try {
      for (let iter = 0; iter < Turn.MAX_ITERATIONS; iter++) {
        this.throwIfInterrupted();
        heartbeat.start(iter);
        if (iter === 0) {
          await sessionHeartbeat.start(this.meta.turnId, iter).catch(() => {});
        } else {
          sessionHeartbeat.updateIteration(iter);
        }
        const preLLM = await this.session.agent.hooks.runPre(
          "beforeLLMCall",
          { messages, tools: toolDefs, system: systemPrompt, iteration: iter },
          this.hookCtx("beforeLLMCall"),
        );
        if (preLLM.action === "block") {
          const reason = preLLM.reason ?? "LLM call blocked by hook";
          this.sse.agent({ type: "text_delta", delta: `⚠️ ${reason}` });
          throw new Error(`beforeLLMCall blocked: ${reason}`);
        }
        if (preLLM.action === "skip") break;
        ({ messages, tools: toolDefs, system: systemPrompt } = preLLM.args);
        this.throwIfInterrupted();

        const effectiveModel = await this.resolveEffectiveModel(messages, toolDefs);
        appendRuntimeModelIdentityContext(messages, {
          configuredModel: this.session.agent.config.model,
          effectiveModel,
          ...(this.meta.routeDecision ? { routeDecision: this.meta.routeDecision } : {}),
        });
        const payloadSize = JSON.stringify(messages).length + JSON.stringify(systemPrompt).length;
        console.log(
          `[core-agent] llm-call iter=${iter} payloadSize=${payloadSize}` +
          ` msgCount=${messages.length} model=${effectiveModel}` +
          ` turnId=${this.meta.turnId}`,
        );

        const { blocks, stopReason, usage } = await readOneStream(
          { llm: this.session.agent.llm, model: effectiveModel, sse,
            abortSignal: this.interruptController.signal,
            onError: (code, err) => this.emitError(code, err) },
          systemPrompt, messages, toolDefs,
          this.readOptions(),
        );
        // Reset after use — only applies to the single recovery call.
        this.forceNoThinking = false;
        this.recordUsage(usage);
        this.emittedAssistantBlocks.push(...blocks);
        this.canonicalAssistantMessages.push(normalizeAssistantReplayBlocks(blocks));
        this.throwIfInterrupted();

        void this.session.agent.hooks.runPost(
          "afterLLMCall",
          { messages, tools: toolDefs, system: systemPrompt, iteration: iter, stopReason, assistantBlocks: blocks },
          this.hookCtx("afterLLMCall"),
        );

        // T1-05 — StopReasonHandler mutates messages + stateRef.
        const stateRef = { recoveryAttempt: this.recoveryAttempt, assistantTextSoFarLen: this.assistantTextSoFarLen() };
        const decision = handleStopReason(
          { stageAuditEvent: (e, d) => this.stageAuditEvent(e, d),
            logUnknown: (raw, t) => console.warn(`[core-agent] unknown stop_reason=${String(raw)} turnId=${t}`) },
          stateRef,
          { stopReasonRaw: stopReason, blocks, iter, turnId: this.meta.turnId, messages },
        );
        this.recoveryAttempt = stateRef.recoveryAttempt;

        // Diagnostic log — visible in `kubectl logs` for truncation / empty response debugging.
        const textLen = this.emittedAssistantBlocks
          .filter((b) => b.type === "text")
          .reduce((sum, b) => sum + ((b as { text: string }).text?.length ?? 0), 0);
        console.log(
          `[core-agent] iter=${iter} stop=${String(stopReason)} decision=${decision.kind}` +
          ` blocks=${blocks.length} textLen=${textLen}` +
          ` in=${usage.inputTokens} out=${usage.outputTokens}` +
          ` recovery=${this.recoveryAttempt} emptyRetry=${this.emptyResponseRetry}` +
          ` turnId=${this.meta.turnId}`,
        );

        if (decision.kind === "finalise") {
          const finalBlocks = this.applyFinalAssistantReplayBlocks(blocks);
          if (!this.meta.stopReason) {
            this.setStopReason(this.recoveryAttempt > 0 ? "max_tokens_recovered" : "end_turn");
          }
          this.throwIfInterrupted();
          // #86: pending injections — if user messages arrived during
          // the LLM response, continue to the next iteration so
          // beforeLLMCall → midTurnInjector drains them. The bot
          // finishes its current response naturally, then addresses
          // the queued messages.
          if (this.session.hasPendingInjections()) {
            console.log(
              `[core-agent] finalise deferred — pending injections exist` +
              ` turnId=${this.meta.turnId} iter=${iter}`,
            );
            this.clearUserVisibleDraftForDeferredFinalise(sse, "pending_injection");
            if (finalBlocks.length > 0) {
              messages.push({ role: "assistant", content: finalBlocks });
            }
            continue;
          }

          // ── Guard 1: empty-response (no text at all) ──────────
          // Covers: thinking-only, tool_use-only, subagent delegation.
          const hasText = this.emittedAssistantBlocks.some((b) => b.type === "text");
          if (!hasText && this.emptyResponseRetry < Turn.MAX_EMPTY_RESPONSE_RETRIES) {
            if (finalBlocks.length > 0) {
              messages.push({ role: "assistant", content: finalBlocks });
            }
            const nudge = this.emptyResponseRetry === 0
              ? "You completed your work but didn't produce a visible text response — the user sees an empty message. Please summarize what you did and your findings as text. Do NOT use thinking — write your summary directly as text."
              : "Your response was still empty. The user CANNOT see your thinking — only text output is visible. Write a brief summary of what you did as plain text immediately.";
            messages.push({ role: "user", content: nudge });
            this.emptyResponseRetry += 1;
            this.forceNoThinking = true; // Force text-only output on recovery
            iter -= 1;
            continue;
          }
          if (!hasText) {
            const currentModel = this.currentModel();
            const fallbackModel = emptyResponseFallbackModel();
            if (
              !this.emptyResponseFallbackUsed &&
              fallbackModel &&
              fallbackModel !== currentModel
            ) {
              this.emptyResponseFallbackUsed = true;
              this.emptyResponseModelOverride = fallbackModel;
              this.meta.routeDecision = undefined;
              this.stageAuditEvent("empty_response_fallback", {
                from: currentModel,
                to: fallbackModel,
                retries: this.emptyResponseRetry,
              });
              console.warn(
                `[core-agent] empty-response fallback model activated` +
                ` from=${currentModel} to=${fallbackModel}` +
                ` retries=${this.emptyResponseRetry}` +
                ` turnId=${this.meta.turnId}`,
              );
              messages.push({
                role: "user",
                content:
                  "The previous answering model produced no visible text after multiple retries. Retry once using the fallback text model. Use tools if needed, then write the user-visible answer directly as plain text.",
              });
              this.forceNoThinking = true;
              iter -= 1;
              continue;
            }
            const err = new Error(
              `empty response retry exhausted after ${this.emptyResponseRetry} retries`,
            );
            this.stageAuditEvent("turn_aborted", {
              reason: "empty_response_retry_exhausted",
              retries: this.emptyResponseRetry,
              model: currentModel,
            });
            this.setStopReason("empty_response_retry_exhausted");
            this.emitError("empty_response_retry_exhausted", err);
            throw err;
          }

          // ── Guard 2: truncated response (text ends mid-sentence) ──
          // The model sent end_turn but the response looks cut off.
          // This catches cases where thinking consumed most of the
          // output budget, leaving too few tokens for the text body.
          if (hasText && this.truncationRecovery < Turn.MAX_TRUNCATION_RETRIES) {
            const lastTextBlock = [...this.emittedAssistantBlocks]
              .reverse()
              .find((b) => b.type === "text") as { text: string } | undefined;
            const lastText = lastTextBlock?.text?.trimEnd() ?? "";
            const ending = truncationGuardEnding(lastText);
            if (ending.looksTruncated) {
              console.log(
                `[core-agent] truncation-guard fired: lastChar=${JSON.stringify(ending.lastChar)}` +
                ` textLen=${lastText.length} retry=${this.truncationRecovery}`,
              );
              if (finalBlocks.length > 0) {
                messages.push({ role: "assistant", content: finalBlocks });
              }
              messages.push({ role: "user", content: "Your response was cut off mid-sentence. Continue from where you left off." });
              this.truncationRecovery += 1;
              iter -= 1;
              continue;
            }
          }
          this.retryBaseMessages = [
            ...messages,
            ...(finalBlocks.length > 0
              ? [{ role: "assistant" as const, content: finalBlocks }]
              : []),
          ];
          return;
        }
        if (decision.kind === "recover") { iter -= 1; continue; }
        // decision.kind === "run_tools"
        this.throwIfInterrupted();
        let dispatched: ToolDispatchResult[];
        try {
          dispatched = await this.runToolsVia(sse, decision.toolUses, toolDefs);
        } catch (err) {
          if (err instanceof UnknownToolLoopError) {
            // Gap §11.3 — hallucination loop. Stop_reason + SSE text
            // already emitted by the dispatcher; abort the turn.
            this.stageAuditEvent("turn_aborted", {
              reason: "unknown_tool_loop",
              unknownToolCount: err.unknownToolCount,
              iter,
            });
            this.setStopReason("unknown_tool_loop");
            throw err;
          }
          throw err;
        }
        this.throwIfInterrupted();
        this.appendToolTurn(messages, blocks, dispatched);
      }

      const err = new Error(`turn exceeded ${Turn.MAX_ITERATIONS} tool iterations`);
      this.emitError("iteration_limit", err);
      this.setStopReason("iteration_limit");
      throw err;
    } finally {
      this.session.setActiveSse(null);
      heartbeat.stop();
      await sessionHeartbeat.stop().catch(() => {});
    }
  }

  async commit(): Promise<CommitResult> { return await commitTurn(this.buildCommitCtx()); }
  async commitWithRetry(maxAttempts = 3): Promise<CommitResult> {
    const controller = new RetryController({ maxAttempts });
    for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
      const result = await this.commit();
      if (result.status === "committed") return result;
      if (!result.retryable) {
        if (result.stopReason) this.setStopReason(result.stopReason);
        this.clearBlockedDraftBeforeAbort(result.reason);
        throw new Error(`beforeCommit blocked: ${result.reason}`);
      }

      const decision = controller.next({
        kind: result.retryKind ?? "before_commit_blocked",
        reason: result.reason,
        attempt,
      });
      if (decision.action === "abort") {
        if (result.retryKind === "structured_output_invalid") {
          this.setStopReason("structured_output_retry_exhausted");
        }
        this.clearBlockedDraftBeforeAbort(decision.reason);
        throw new Error(`beforeCommit blocked: ${decision.reason}`);
      }

      await this.recordRetryEvent(result.reason, attempt, maxAttempts);
      this.incrementCommitRetry();
      this.sse.agent({
        type: "retry",
        reason: result.reason,
        retryNo: attempt,
      });
      try {
        await this.resampleAfterBlockedCommit(
          result.finalText,
          decision.hiddenUserMessage,
        );
      } catch (resampleErr) {
        // The previous draft was rejected by a beforeCommit verifier.
        // Never restore it after retry failure: doing so leaks exactly
        // the response the verifier blocked. Show a truthful runtime
        // failure placeholder instead; the turn will still abort below.
        const fallbackText =
          "A runtime verifier blocked the previous draft, but the correction attempt failed before a valid response could be produced. Please retry.";
        console.warn(
          `[core-agent] resample failed after blocked commit; clearing blocked draft` +
          ` turnId=${this.meta.turnId}`,
        );
        this.sse.agent({ type: "response_clear" });
        this.sse.agent({ type: "text_delta", delta: fallbackText });
        this.emittedAssistantBlocks.push({ type: "text", text: fallbackText });
        throw resampleErr;
      }
    }
    throw new Error("commit retry attempts exhausted");
  }
  async abort(reason: string, stopReason: TurnStopReason = this.meta.stopReason ?? "aborted"): Promise<void> {
    this.setStopReason(stopReason);
    await abortTurn(this.buildCommitCtx(), reason, stopReason);
  }

  // ── internals ────────────────────────────────────────────────────

  private hookCtx(point: HookPoint) {
    return buildHookContext(
      this.session,
      this.sse,
      this.meta.turnId,
      point,
      this.currentModel(),
      this.interruptController.signal,
    );
  }

  private checkBudgetOrThrow(): void {
    const budget = this.session.budgetExceeded();
    if (!budget.exceeded) return;
    const reason = budget.reason ?? "unknown";
    const userFacing = `Session budget exceeded (${reason}). Please start a new session.`;
    this.sse.agent({ type: "text_delta", delta: userFacing });
    this.setStopReason("budget_exceeded");
    this.session.agent.auditLog
      .append("session_budget_exceeded", this.session.meta.sessionKey, this.meta.turnId, {
        reason,
        ...this.session.budgetStats(),
        maxTurns: this.session.maxTurns,
        maxCostUsd: this.session.maxCostUsd,
      })
      .catch(() => { /* audit failures never abort — §6 invariant G */ });
    throw new Error(userFacing);
  }

  /** Persist turn_started + user_message BEFORE any LLM call (invariant F). */
  private async appendTurnPrologue(): Promise<void> {
    const ts = Date.now();
    await this.session.transcript.append({
      kind: "turn_started", ts, turnId: this.meta.turnId, declaredRoute: this.meta.declaredRoute,
    });
    await this.session.transcript.append({
      kind: "user_message", ts, turnId: this.meta.turnId, text: this.userMessage.text,
    });
  }

  private appendToolTurn(
    messages: LLMMessage[],
    blocks: LLMContentBlock[],
    results: ToolDispatchResult[],
  ): void {
    messages.push({ role: "assistant", content: normalizeAssistantReplayBlocks(blocks) });
    messages.push({
      role: "user",
      content: results.map((r) => ({
        type: "tool_result" as const,
        tool_use_id: r.toolUseId,
        content: r.content,
        ...(r.isError ? { is_error: true as const } : {}),
      })),
    });
  }

  private applyFinalAssistantReplayBlocks(blocks: LLMContentBlock[]): LLMContentBlock[] {
    const finalBlocks = finalAssistantReplayBlocks(blocks);
    if (blocks.length > 0) {
      this.emittedAssistantBlocks.splice(
        Math.max(0, this.emittedAssistantBlocks.length - blocks.length),
        blocks.length,
        ...finalBlocks,
      );
    }
    if (this.canonicalAssistantMessages.length > 0) {
      this.canonicalAssistantMessages[this.canonicalAssistantMessages.length - 1] = finalBlocks;
    }
    return finalBlocks;
  }

  private async runTools(
    toolUses: Array<Extract<LLMContentBlock, { type: "tool_use" }>>,
  ): Promise<ToolDispatchResult[]> {
    return this.runToolsVia(this.sse, toolUses);
  }

  /** Variant used by the heartbeat-wrapped iteration loop so the
   * heartbeat monitor sees every tool_start / tool_end emission. */
  private async runToolsVia(
    sse: SseWriter,
    toolUses: Array<Extract<LLMContentBlock, { type: "tool_use" }>>,
    toolDefs?: ReadonlyArray<{ name: string }>,
  ): Promise<ToolDispatchResult[]> {
    return dispatchTools(
      {
        session: this.session,
        sse,
        turnId: this.meta.turnId,
        permissionMode: safeGetPermissionMode(this.session),
        buildHookContext: (point) => this.hookCtx(point),
        stageAuditEvent: (event, data) => this.stageAuditEvent(event, data),
        askUser: (q) => this.asks.ask(q),
        abortSignal: this.interruptController.signal,
        unknownToolCounter: {
          get: () => this.unknownToolCount,
          inc: () => ++this.unknownToolCount,
        },
        // Codex P1 (2026-04-20): pass the LLM-exposed tool set so the
        // dispatcher enforces the allowlist and scopes its unknown-tool
        // hint to names already visible to the LLM.
        ...(toolDefs ? { exposedToolNames: toolDefs.map((d) => d.name) } : {}),
      },
      toolUses,
    );
  }

  private async resampleAfterBlockedCommit(
    failedText: string,
    hiddenUserMessage: string,
  ): Promise<void> {
    const rawBlocks = this.emittedAssistantBlocks.length > 0
      ? [...this.emittedAssistantBlocks]
      : failedText ? [{ type: "text" as const, text: failedText }] : [];
    // Filter out empty text blocks — Anthropic API rejects them
    const failedBlocks = finalAssistantReplayBlocks(rawBlocks.filter(
      (b) => b.type !== "text" || ("text" in b && (b as { text: string }).text.length > 0),
    ));
    this.emittedAssistantBlocks = [];
    this.canonicalAssistantMessages.pop();
    this.assistantText = "";
    this.forceNoThinking = false;
    this.setPhase("executing");
    this.sse.agent({ type: "response_clear" });

    let systemPrompt = await buildSystemPrompt(this.session, this.meta.turnId);
    const runtimeModel = await this.resolveRuntimeModel();
    let messages = this.retryBaseMessages.length > 0
      ? [...this.retryBaseMessages]
      : await buildMessages(this.session, this.userMessage, runtimeModel);
    if (this.retryBaseMessages.length === 0 && failedBlocks.length > 0) {
      messages.push({ role: "assistant", content: failedBlocks });
    }
    messages.push({ role: "user", content: hiddenUserMessage });
    let toolDefs = await buildToolDefs({
      session: this.session,
      sse: this.sse,
      turnId: this.meta.turnId,
      userText: this.userMessage.text,
      planMode: this.planMode || safeGetPermissionMode(this.session) === "plan",
    });

    for (let iter = 0; iter < Turn.MAX_ITERATIONS; iter += 1) {
      const preLLM = await this.session.agent.hooks.runPre(
        "beforeLLMCall",
        { messages, tools: toolDefs, system: systemPrompt, iteration: iter },
        this.hookCtx("beforeLLMCall"),
      );
      if (preLLM.action === "block") {
        const reason = preLLM.reason ?? "LLM retry call blocked by hook";
        this.sse.agent({ type: "text_delta", delta: `⚠️ ${reason}` });
        throw new Error(`beforeLLMCall blocked: ${reason}`);
      }
      if (preLLM.action === "skip") return;
      ({ messages, tools: toolDefs, system: systemPrompt } = preLLM.args);

      const effectiveModel = await this.resolveEffectiveModel(messages, toolDefs);
      const { blocks, stopReason, usage } = await readOneStream(
        {
          llm: this.session.agent.llm,
          model: effectiveModel,
          sse: this.sse,
          abortSignal: this.interruptController.signal,
          onError: (code, err) => this.emitError(code, err),
        },
        systemPrompt,
        messages,
        toolDefs as LLMToolDef[],
        this.readOptions(),
      );
      this.forceNoThinking = false;
      this.recordUsage(usage);
      this.emittedAssistantBlocks.push(...blocks);
      this.canonicalAssistantMessages.push(normalizeAssistantReplayBlocks(blocks));
      void this.session.agent.hooks.runPost(
        "afterLLMCall",
        { messages, tools: toolDefs, system: systemPrompt, iteration: iter, stopReason, assistantBlocks: blocks },
        this.hookCtx("afterLLMCall"),
      );

      const stateRef = {
        recoveryAttempt: this.recoveryAttempt,
        assistantTextSoFarLen: this.assistantTextSoFarLen(),
      };
      const decision = handleStopReason(
        {
          stageAuditEvent: (e, d) => this.stageAuditEvent(e, d),
          logUnknown: (raw, t) =>
            console.warn(`[core-agent] unknown stop_reason=${String(raw)} turnId=${t}`),
        },
        stateRef,
        { stopReasonRaw: stopReason, blocks, iter, turnId: this.meta.turnId, messages },
      );
      this.recoveryAttempt = stateRef.recoveryAttempt;

      if (decision.kind === "finalise") {
        const finalBlocks = this.applyFinalAssistantReplayBlocks(blocks);
        this.retryBaseMessages = [
          ...messages,
          ...(finalBlocks.length > 0
            ? [{ role: "assistant" as const, content: finalBlocks }]
            : []),
        ];
        return;
      }
      if (decision.kind === "recover") {
        iter -= 1;
        continue;
      }
      const dispatched = await this.runToolsVia(this.sse, decision.toolUses, toolDefs);
      this.appendToolTurn(messages, blocks, dispatched);
    }
    throw new Error(`turn exceeded ${Turn.MAX_ITERATIONS} retry tool iterations`);
  }

  private async recordRetryEvent(
    reason: string,
    attempt: number,
    maxAttempts: number,
  ): Promise<void> {
    const controlEvents = (this.session as unknown as {
      controlEvents?: {
        append: (event: {
          type: "retry";
          turnId: string;
          reason: string;
          attempt: number;
          maxAttempts: number;
          visibleToUser: boolean;
        }) => Promise<unknown>;
      };
    }).controlEvents;
    if (!controlEvents) {
      throw new Error("control event ledger unavailable for retry");
    }
    await controlEvents.append({
      type: "retry",
      turnId: this.meta.turnId,
      reason,
      attempt,
      maxAttempts,
      visibleToUser: true,
    });
  }

  private throwIfInterrupted(): void {
    if (!this.interruptState) return;
    const reason = this.interruptState.handoffRequested
      ? "user_interrupt_handoff"
      : "user_interrupt";
    throw new TurnInterruptedError(
      reason,
      this.interruptState.handoffRequested,
      this.interruptState.source,
    );
  }

  private recordUsage(u: LLMUsage): void {
    this.meta.usage.inputTokens = Math.max(this.meta.usage.inputTokens, u.inputTokens);
    this.meta.usage.outputTokens += u.outputTokens;
    this.meta.usage.costUsd = computeUsd(
      this.meta.effectiveModel ?? this.session.agent.config.model,
      this.meta.usage.inputTokens,
      this.meta.usage.outputTokens,
    );
  }

  private currentModel(): string {
    return this.meta.effectiveModel ?? this.runtimeModel ?? this.session.agent.config.model;
  }

  private async resolveEffectiveModel(
    messages: LLMMessage[],
    toolDefs: ReadonlyArray<{ name: string }>,
  ): Promise<string> {
    if (this.emptyResponseModelOverride) {
      this.meta.configuredModel = await this.resolveRuntimeModel();
      this.meta.effectiveModel = this.emptyResponseModelOverride;
      this.meta.routeDecision = undefined;
      return this.emptyResponseModelOverride;
    }
    // Use dynamically resolved model (from api-proxy /v1/bot-model) if available,
    // falling back to pod env config.model. This ensures chat picker model changes
    // take effect without pod restart.
    const dynamicModel = await this.resolveRuntimeModel();
    const configuredModel = dynamicModel || this.session.agent.config.model;
    this.meta.configuredModel = configuredModel;
    if (!isRouterKeyword(configuredModel) || !this.session.agent.router) {
      this.meta.effectiveModel = configuredModel;
      this.meta.routeDecision = undefined;
      return configuredModel;
    }
    if (this.meta.routeDecision) {
      this.meta.effectiveModel = this.meta.routeDecision.model;
      return this.meta.routeDecision.model;
    }

    const decision = await this.session.agent.router.resolve({
      configuredModel,
      messages,
      hasTools: toolDefs.length > 0,
      hasImages: messagesHaveImages(messages),
    });
    this.meta.routeDecision = decision;
    this.meta.effectiveModel = decision.model;
    return decision.model;
  }

  private readOptions() {
    return {
      ...(this.forceNoThinking
        ? { thinkingOverride: { type: "disabled" as const } }
        : {}),
      ...(this.meta.routeDecision
        ? {
            routing: {
              profileId: this.meta.routeDecision.profileId,
              tier: this.meta.routeDecision.tier,
              provider: this.meta.routeDecision.provider,
              confidence: this.meta.routeDecision.confidence,
            },
          }
        : {}),
    };
  }

  private buildCommitCtx(): CommitPipelineContext {
    return {
      session: this.session,
      sse: this.sse,
      userMessage: this.userMessage,
      turnId: this.meta.turnId,
      startedAt: this.meta.startedAt,
      buildHookContext: (point) => this.hookCtx(point),
      setPhase: (phase) => this.setPhase(phase),
      meta: this.meta,
      emittedAssistantBlocks: this.emittedAssistantBlocks,
      canonicalAssistantMessages: this.canonicalAssistantMessages,
      commitRetryCount: this.commitRetryCount,
      setAssistantText: (text) => { this.assistantText = text; },
      rejectAllPendingAsks: (reason) => this.asks.rejectAll(reason),
      getAssistantText: () => this.assistantText,
    };
  }

  private setPhase(next: TurnStatus): void {
    this.meta.status = next;
    this.sse.agent({ type: "turn_phase", turnId: this.meta.turnId, phase: next });
  }

  setStopReason(reason: TurnStopReason): void {
    this.meta.stopReason = reason;
  }

  private emitError(code: string, err: unknown): void {
    const message = err instanceof Error ? err.message : String(err);
    this.sse.agent({ type: "error", code, message });
  }

  private clearBlockedDraftBeforeAbort(reason: string): void {
    if (this.assistantTextSoFarLen() === 0) return;
    this.sse.agent({ type: "response_clear" });
    console.warn(
      `[core-agent] cleared blocked draft before abort` +
      ` reason=${JSON.stringify(reason.slice(0, 120))}` +
      ` turnId=${this.meta.turnId}`,
    );
  }

  private clearUserVisibleDraftForDeferredFinalise(
    sse: SseWriter,
    reason: string,
  ): void {
    let removedTextLen = 0;
    let removedThinkingLen = 0;
    for (const block of this.emittedAssistantBlocks) {
      if (block.type === "text") {
        removedTextLen += block.text.length;
      } else if (block.type === "thinking") {
        removedThinkingLen += block.thinking.length;
      }
    }
    if (removedTextLen === 0 && removedThinkingLen === 0) return;

    this.emittedAssistantBlocks = this.emittedAssistantBlocks.filter(
      (block) => block.type !== "text" && block.type !== "thinking",
    );
    sse.agent({ type: "response_clear" });
    console.log(
      `[core-agent] cleared deferred visible draft reason=${reason}` +
      ` textLen=${removedTextLen} thinkingLen=${removedThinkingLen}` +
      ` turnId=${this.meta.turnId}`,
    );
  }

  /** Fire-and-forget audit event from within the turn loop (§6.G). */
  private stageAuditEvent(event: string, data?: Record<string, unknown>): void {
    void this.session.agent.auditLog.append(
      event, this.session.meta.sessionKey, this.meta.turnId, data,
    );
  }

  private assistantTextSoFarLen(): number {
    let n = 0;
    for (const b of this.emittedAssistantBlocks) if (b.type === "text") n += b.text.length;
    return n;
  }
}

/**
 * Legacy test-stub safety: `getPermissionMode` may be absent (T2-08
 * pre-existing stubs) — treat that as `"default"`.
 */
function safeGetPermissionMode(
  session: { getPermissionMode?: () => "default" | "plan" | "auto" | "bypass" },
): "default" | "plan" | "auto" | "bypass" {
  if (typeof session.getPermissionMode !== "function") return "default";
  return session.getPermissionMode();
}
