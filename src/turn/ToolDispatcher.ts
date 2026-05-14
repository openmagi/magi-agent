/**
 * ToolDispatcher — executes tool_use blocks in parallel (§7.3).
 *
 * Extracted from `Turn.runTools` (R3 refactor, 2026-04-19). Owns the
 * full tool-dispatch surface:
 *   • beforeToolUse hook chain (bypass-aware, T2-08)
 *   • permission_bypass audit emission
 *   • tool resolution + unknown-tool error path
 *   • parallel execute via AbortController
 *   • tool_start / tool_end SSE emission
 *   • transcript tool_call / tool_result append
 *   • afterToolUse observer hook
 */

import type { Session } from "../Session.js";
import type { SseWriter } from "../transport/SseWriter.js";
import type { LLMContentBlock } from "../transport/LLMClient.js";
import type { HookContext } from "../hooks/types.js";
import type {
  AskUserQuestionInput,
  AskUserQuestionOutput,
  Tool,
  ToolContext,
  ToolResult,
} from "../Tool.js";
import type { UserMessage } from "../util/types.js";
import { buildToolInputPreview, summariseToolOutput } from "../util/toolResult.js";
import { applyToolResultBudget } from "./ToolResultBudget.js";
import { createLogger } from "../util/logger.js";

const logger = createLogger("ToolDispatcher");
import type { ToolCallLoopDetector, LoopCheckResult } from "./ToolCallLoopDetector.js";
import {
  decideRuntimePermission,
  type PermissionDecision,
} from "../permissions/PermissionArbiter.js";
import { isReadOnlyTool } from "../permissions/ToolPermissionAdapters.js";
import { buildPatchApplyApprovalInput } from "../tools/PatchApply.js";
import { decideToolAccess } from "./ToolArbiter.js";

export type PermissionMode = "default" | "plan" | "auto" | "bypass" | "workspace-bypass";

/**
 * Threshold at which repeated unknown-tool dispatches abort the turn
 * with stop_reason `"unknown_tool_loop"`. Ten is generous — a
 * well-behaved LLM typos a tool name once, sees the error in
 * tool_result, and self-corrects by iteration #2 or #3. Ten
 * consecutive misses within one turn is ~always a hallucination loop.
 */
export const UNKNOWN_TOOL_LOOP_THRESHOLD = 10;

export interface ToolDispatchContext {
  readonly session: Session;
  readonly sse: SseWriter;
  readonly turnId: string;
  readonly permissionMode: PermissionMode;
  /** Optional parent abort signal (e.g. user interrupt on the live turn). */
  readonly abortSignal?: AbortSignal;
  /** Build a HookContext for the given point. */
  readonly buildHookContext: (point: "beforeToolUse" | "afterToolUse") => HookContext;
  /** Fire-and-forget audit event (mirrors Turn.stageAuditEvent). */
  readonly stageAuditEvent: (event: string, data?: Record<string, unknown>) => void;
  /** Human-in-the-loop delegate — Turn's askUser. */
  readonly askUser: (input: AskUserQuestionInput) => Promise<AskUserQuestionOutput>;
  /**
   * Gap §11.3 unknown-tool guard — per-turn counter incremented on
   * every unknown tool_use. When it reaches `UNKNOWN_TOOL_LOOP_
   * THRESHOLD`, dispatch throws `UnknownToolLoopError` so Turn.ts
   * aborts with stop_reason=`unknown_tool_loop`. Optional for
   * backward-compat with callers (spawn pipelines) that don't want
   * the guard — when omitted the counter is internal to the dispatch
   * call and only the "available tools" enrichment kicks in.
   */
  readonly unknownToolCounter?: {
    get: () => number;
    inc: () => number;
  };
  /**
   * Names of tools actually exposed to the LLM for this turn (after
   * plan-mode filter + intent classification + MAX_TOOLS_PER_TURN cap).
   *
   * Two purposes (codex P1, 2026-04-20):
   *   1. The unknown-tool enrichment hint is built from this list, not
   *      from the full registry — prevents plan-mode from leaking
   *      `Bash` / `FileWrite` names to the LLM via an error message.
   *   2. Dispatch enforces this allowlist at execution time: a tool
   *      that resolves in the registry but is NOT in this set is
   *      treated exactly like an unknown tool (error tool_result,
   *      counter increment). Without this the registry-level resolver
   *      would happily execute a hidden tool the LLM named after seeing
   *      it in a prior hint.
   *
   * Optional for back-compat with spawn pipelines and tests that
   * dispatch directly. When omitted, allowlist enforcement is skipped
   * and the hint falls back to the full registry (legacy behaviour).
   */
  readonly exposedToolNames?: readonly string[];
  /** Current user message, including runtime-injected system addendum and attachments. */
  readonly currentUserMessage?: UserMessage;
  /**
   * Return true after a completed tool when the caller wants to yield
   * control back to the LLM before executing more tools from the same
   * assistant tool_use batch. Used by mid-turn steering: finish the
   * current tool call, synthesize tool_results for not-yet-run calls,
   * then let the next beforeLLMCall drain the user's steering message.
   */
  readonly shouldYieldAfterTool?: (
    result: ToolDispatchResult,
    toolUse: Extract<LLMContentBlock, { type: "tool_use" }>,
  ) => boolean;
  /** Cross-service diagnostic trace ID propagated to tools. */
  readonly traceId?: string;
  /**
   * Shared set of workspace-relative paths read in this session.
   * FileRead/Grep/Glob append to it; FileEdit/FileWrite check it.
   * Undefined = read-guard disabled (backward compat).
   */
  readonly filesRead?: Set<string>;
  /**
   * Per-turn loop detector. When omitted, loop detection is disabled
   * for backward compatibility with direct dispatch tests and spawn
   * pipelines.
   */
  readonly loopDetector?: ToolCallLoopDetector;
}

export interface ToolDispatchResult {
  toolUseId: string;
  /** Text content, or array of content blocks (e.g. tool_reference for ToolSearch). */
  content: string | Array<{type: string; [key: string]: unknown}>;
  isError: boolean;
}

/**
 * Thrown by `dispatch` when the per-turn unknown-tool counter reaches
 * `UNKNOWN_TOOL_LOOP_THRESHOLD`. Turn.ts catches and aborts the turn
 * with `stop_reason = "unknown_tool_loop"` + user-facing SSE text.
 */
export class UnknownToolLoopError extends Error {
  readonly stopReason = "unknown_tool_loop";
  readonly unknownToolCount: number;
  constructor(count: number) {
    super(`unknown_tool_loop: ${count} unknown tool dispatches in one turn`);
    this.name = "UnknownToolLoopError";
    this.unknownToolCount = count;
  }
}

const USER_STEER_SKIP_STATUS = "skipped_user_steer";

function skippedForUserSteerContent(toolName: string): string {
  return [
    `Tool ${toolName} was not executed because the user sent a steering update while this tool batch was running.`,
    "Re-read the latest user steering message before deciding whether to run additional tools.",
  ].join(" ");
}

/**
 * Execute tool_use blocks in parallel. Independence is assumed (§7.3)
 * — each tool's transcript entries and SSE emissions are interleaved
 * but self-contained so ordering doesn't matter semantically.
 */
export async function dispatch(
  ctx: ToolDispatchContext,
  toolUses: Array<Extract<LLMContentBlock, { type: "tool_use" }>>,
): Promise<ToolDispatchResult[]> {
  const abortController = new AbortController();
  if (ctx.abortSignal) {
    if (ctx.abortSignal.aborted) {
      abortController.abort();
    } else {
      ctx.abortSignal.addEventListener("abort", () => abortController.abort(), {
        once: true,
      });
    }
  }
  const enterPlanUses = toolUses.filter((tu) => tu.name === "EnterPlanMode");
  const otherUses = toolUses.filter((tu) => tu.name !== "EnterPlanMode");
  const enteredResults: ToolDispatchResult[] = [];
  for (let index = 0; index < enterPlanUses.length; index += 1) {
    const tu = enterPlanUses[index]!;
    const result = await dispatchOne(ctx, tu, abortController);
    enteredResults.push(result);
    if (shouldYieldAfterTool(ctx, result, tu)) {
      const skipped = await skippedToolResultsForUserSteer(ctx, [
        ...enterPlanUses.slice(index + 1),
        ...otherUses,
      ]);
      const results = [...enteredResults, ...skipped];
      throwUnknownToolLoopIfNeeded(ctx);
      return results;
    }
  }
  const results = [
    ...enteredResults,
    ...(await dispatchInConcurrencySafeBatches(ctx, otherUses, abortController)),
  ];
  throwUnknownToolLoopIfNeeded(ctx);
  return results;
}

function throwUnknownToolLoopIfNeeded(ctx: ToolDispatchContext): void {
  const counter = ctx.unknownToolCounter;
  if (counter && counter.get() >= UNKNOWN_TOOL_LOOP_THRESHOLD) {
    ctx.sse.agent({
      type: "text_delta",
      delta: "⚠️ 할루시네이션한 툴 호출 반복 감지. 턴 종료.",
    });
    ctx.stageAuditEvent("unknown_tool_loop", {
      count: counter.get(),
      threshold: UNKNOWN_TOOL_LOOP_THRESHOLD,
    });
    throw new UnknownToolLoopError(counter.get());
  }
}

function shouldYieldAfterTool(
  ctx: ToolDispatchContext,
  result: ToolDispatchResult,
  toolUse: Extract<LLMContentBlock, { type: "tool_use" }>,
): boolean {
  try {
    return ctx.shouldYieldAfterTool?.(result, toolUse) === true;
  } catch (err) {
    logger.warn("should_yield_after_tool_failed", {
      turnId: ctx.turnId, error: (err as Error).message,
    });
    return false;
  }
}

async function skippedToolResultsForUserSteer(
  ctx: ToolDispatchContext,
  toolUses: Array<Extract<LLMContentBlock, { type: "tool_use" }>>,
): Promise<ToolDispatchResult[]> {
  if (toolUses.length === 0) return [];
  ctx.stageAuditEvent("tool_batch_yielded_for_user_steer", {
    skippedToolUseIds: toolUses.map((tu) => tu.id),
    skippedToolNames: toolUses.map((tu) => tu.name),
  });
  const results: ToolDispatchResult[] = [];
  for (const tu of toolUses) {
    const content = skippedForUserSteerContent(tu.name);
    await ctx.session.transcript.append({
      kind: "tool_result",
      ts: Date.now(),
      turnId: ctx.turnId,
      toolUseId: tu.id,
      status: USER_STEER_SKIP_STATUS,
      output: content,
      isError: true,
      metadata: {
        reason: "user_steer_pending",
        toolName: tu.name,
      },
    });
    results.push({ toolUseId: tu.id, content, isError: true });
  }
  return results;
}

async function dispatchInConcurrencySafeBatches(
  ctx: ToolDispatchContext,
  toolUses: Array<Extract<LLMContentBlock, { type: "tool_use" }>>,
  abortController: AbortController,
): Promise<ToolDispatchResult[]> {
  const results: ToolDispatchResult[] = [];
  let readOnlyBatch: Array<Extract<LLMContentBlock, { type: "tool_use" }>> = [];
  const flushReadOnlyBatch = async (
    remainingAfterBatch: Array<Extract<LLMContentBlock, { type: "tool_use" }>>,
  ): Promise<boolean> => {
    if (readOnlyBatch.length === 0) return false;
    const batch = readOnlyBatch;
    readOnlyBatch = [];
    const batchResults = await Promise.all(
      batch.map((tu) => dispatchOne(ctx, tu, abortController)),
    );
    results.push(...batchResults);
    if (batchResults.some((result, index) => shouldYieldAfterTool(ctx, result, batch[index]!))) {
      results.push(...(await skippedToolResultsForUserSteer(ctx, remainingAfterBatch)));
      return true;
    }
    return false;
  };

  for (let index = 0; index < toolUses.length; index += 1) {
    const tu = toolUses[index]!;
    if (isConcurrencySafeToolUse(ctx, tu)) {
      readOnlyBatch.push(tu);
      continue;
    }
    if (await flushReadOnlyBatch(toolUses.slice(index))) return results;
    const result = await dispatchOne(ctx, tu, abortController);
    results.push(result);
    if (shouldYieldAfterTool(ctx, result, tu)) {
      results.push(...(await skippedToolResultsForUserSteer(ctx, toolUses.slice(index + 1))));
      return results;
    }
  }
  await flushReadOnlyBatch([]);
  return results;
}

function isConcurrencySafeToolUse(
  ctx: ToolDispatchContext,
  tu: Extract<LLMContentBlock, { type: "tool_use" }>,
): boolean {
  const access = decideToolAccess({
    registry: ctx.session.agent.tools,
    toolName: tu.name,
    exposedToolNames: ctx.exposedToolNames,
  });
  if (!access.allowed) return true;
  const tool = access.tool as Tool<unknown, unknown>;
  if (tool.isConcurrencySafe === true) return true;
  if (tool.isConcurrencySafe === false) return false;
  if (tool.mutatesWorkspace === true) return false;
  return isReadOnlyTool(tu.name, tool);
}

const TRANSIENT_TOOL_ERROR_RE =
  /\b(ETIMEDOUT|ECONNRESET|EAI_AGAIN|ENOTFOUND|ECONNREFUSED|socket hang up|timeout|timed out|429|503|504|rate limit|temporarily unavailable)\b/i;

function isRetrySafeTool(tool: Tool<unknown, unknown>): boolean {
  const permission = (tool as { permission?: string }).permission;
  const dangerous = (tool as { dangerous?: boolean }).dangerous === true;
  return !dangerous && (permission === undefined || permission === "read" || permission === "meta");
}

function isTransientToolFailure(result: ToolResult): boolean {
  if (result.status === "ok") return false;
  const detail = result.errorMessage ?? result.errorCode ?? "";
  return typeof detail === "string" && TRANSIENT_TOOL_ERROR_RE.test(detail);
}

function hasBuiltinAutoApprovalHook(ctx: ToolDispatchContext): boolean {
  try {
    return ctx.session.agent.hooks
      .list("beforeToolUse")
      .some((hook) => hook.name === "builtin:auto-approval");
  } catch {
    return false;
  }
}

function shouldFallbackAskDangerousTool(
  ctx: ToolDispatchContext,
  tool: Tool<unknown, unknown>,
): boolean {
  if (ctx.permissionMode !== "default" && ctx.permissionMode !== "auto") return false;
  if (tool.dangerous !== true) return false;
  return !hasBuiltinAutoApprovalHook(ctx);
}

async function askDangerousToolConsent(
  ctx: ToolDispatchContext,
  toolName: string,
): Promise<string | null> {
  try {
    const answer = await ctx.askUser({
      question: `Tool ${toolName} is marked dangerous. Allow it to proceed?`,
      choices: [
        { id: "approve", label: "Approve" },
        { id: "deny", label: "Deny" },
      ],
    });
    if (answer.selectedId === "approve") return null;
    return `[PERMISSION:USER_DENIED] dangerous tool ${toolName}`;
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return `[PERMISSION:ASK_FAILED] dangerous tool ${toolName}: ${msg}`;
  }
}

async function dispatchOne(
  ctx: ToolDispatchContext,
  tu: Extract<LLMContentBlock, { type: "tool_use" }>,
  abortController: AbortController,
): Promise<ToolDispatchResult> {
  const { session, sse, turnId } = ctx;
  const permissionMode = session.getPermissionMode?.() ?? ctx.permissionMode;
  const access = decideToolAccess({
    registry: session.agent.tools,
    toolName: tu.name,
    exposedToolNames: ctx.exposedToolNames,
  });
  const started = Date.now();

  // Emit tool_start with input_preview — clients render this as the
  // expandable activity card. 400 char cap keeps it light over SSE.
  const inputPreview = buildToolInputPreview(tu.name, tu.input);
  sse.agent({
    type: "tool_start",
    id: tu.id,
    name: tu.name,
    input_preview: inputPreview,
  });

  if (!access.allowed) {
    // Gap §11.3 — enrich the tool_result with the available tool list
    // so the LLM can self-correct immediately instead of retrying the
    // same typo. Also increment the per-turn unknown-tool counter so
    // `dispatch()` can detect a hallucination loop at the batch
    // boundary and abort the turn once the threshold is reached.
    const counter = ctx.unknownToolCounter;
    const currentCount = counter ? counter.inc() : 0;
    const err = access.message;
    logger.warn("unknown_tool", {
      toolName: tu.name, turnId, count: currentCount,
      ...(ctx.traceId ? { traceId: ctx.traceId } : {}),
    });
    sse.agent({
      type: "tool_end",
      id: tu.id,
      status: "error",
      durationMs: Date.now() - started,
      output_preview: err,
    });
    await session.transcript.append({
      kind: "tool_result",
      ts: Date.now(),
      turnId,
      toolUseId: tu.id,
      status: "unknown_tool",
      output: err,
      isError: true,
    });
    return { toolUseId: tu.id, content: err, isError: true };
  }
  const tool = access.tool;

  let softWarningPrefix = "";
  if (ctx.loopDetector) {
    const loopCheck: LoopCheckResult = ctx.loopDetector.check(tu.name, tu.input);
    if (loopCheck.action === "hard_escalation") {
      const isFrequency = loopCheck.frequencyCount !== undefined;
      const warning = isFrequency
        ? `Loop detected: ${tu.name} called ${loopCheck.frequencyCount} times this turn (frequency limit). Breaking loop — change your approach. If waiting for a background task, write your current progress and end the turn instead of polling.`
        : `Loop detected: ${tu.name} called ${loopCheck.count} times with identical parameters. Breaking loop — change your approach.`;
      ctx.stageAuditEvent("tool_loop_detected", {
        toolName: tu.name,
        hash: loopCheck.hash,
        count: loopCheck.count,
        action: "hard_escalation",
        ...(isFrequency ? { frequencyCount: loopCheck.frequencyCount, trigger: "frequency" } : { trigger: "consecutive" }),
      });
      sse.agent({
        type: "tool_end",
        id: tu.id,
        status: "error",
        durationMs: Date.now() - started,
        output_preview: warning,
      });
      await session.transcript.append({
        kind: "tool_result",
        ts: Date.now(),
        turnId,
        toolUseId: tu.id,
        status: "error",
        output: warning,
        isError: true,
      });
      return { toolUseId: tu.id, content: warning, isError: true };
    }
    if (loopCheck.action === "soft_warning") {
      const isFrequency = loopCheck.frequencyCount !== undefined;
      softWarningPrefix = isFrequency
        ? `[WARNING: ${tu.name} has been called ${loopCheck.frequencyCount} times this turn. You may be in a polling loop. Consider summarizing progress and ending the turn, or use Bash sleep to add delays between checks.]\n`
        : `[WARNING: This is call #${loopCheck.count} with identical parameters. Consider changing your approach.]\n`;
    }
  }

  // ── beforeToolUse hook ─────────────────────────────────────
  // Hooks may rewrite the input or block with a reason (which
  // becomes the tool_result content with is_error=true so the
  // model can self-correct). T2-07 — thread the askUser delegate
  // so hooks returning `permission_decision: "ask"` can reach the
  // human via the turn's pendingAsks machinery.
  //
  // T2-08 — bypass-like sessions skip the beforeToolUse chain
  // entirely. An audit event
  // `permission_bypass` is emitted per tool so the skipped hook
  // chain is still observable.
  const bypass = isBypassLikeMode(permissionMode);
  if (bypass) {
    ctx.stageAuditEvent("permission_bypass", {
      toolName: tu.name,
      toolUseId: tu.id,
    });
  }
  const preTool = bypass
    ? {
        action: "continue" as const,
        args: { toolName: tu.name, toolUseId: tu.id, input: tu.input },
      }
    : await session.agent.hooks.runPre(
        "beforeToolUse",
        { toolName: tu.name, toolUseId: tu.id, input: tu.input },
        {
          ...ctx.buildHookContext("beforeToolUse"),
          askUser: (q) => ctx.askUser(q),
        },
      );

  if (preTool.action === "block") {
    const blockedMsg = `blocked by hook: ${preTool.reason}`;
    sse.agent({
      type: "tool_end",
      id: tu.id,
      status: "permission_denied",
      durationMs: Date.now() - started,
      output_preview: blockedMsg,
    });
    await session.transcript.append({
      kind: "tool_result",
      ts: Date.now(),
      turnId,
      toolUseId: tu.id,
      status: "permission_denied",
      output: blockedMsg,
      isError: true,
    });
    return { toolUseId: tu.id, content: blockedMsg, isError: true };
  }
  const effectiveInput =
    preTool.action === "continue" ? preTool.args.input : tu.input;

  const permission = await decideRuntimePermission({
    mode: permissionMode,
    source: "turn",
    toolName: tu.name,
    input: effectiveInput,
    tool,
    workspaceRoot: session.agent.config.workspaceRoot,
  });
  const permissionResult = await resolvePermissionDecision(
    ctx,
    tu,
    started,
    permission,
    effectiveInput,
  );
  if (permissionResult.result) return permissionResult.result;
  const permittedInput = permissionResult.input;

  await session.transcript.append({
    kind: "tool_call",
    ts: Date.now(),
    turnId,
    toolUseId: tu.id,
    name: tu.name,
    input: permittedInput,
  });

  const toolCtx: ToolContext = {
    botId: session.agent.config.botId,
    sessionKey: session.meta.sessionKey,
    turnId,
    workspaceRoot: session.agent.config.workspaceRoot,
    memoryMode: session.meta.channel?.memoryMode,
    abortSignal: abortController.signal,
    toolUseId: tu.id,
    executionContract: session.executionContract,
    sourceLedger: session.sourceLedger,
    ...(ctx.traceId ? { traceId: ctx.traceId } : {}),
    ...(ctx.currentUserMessage ? { currentUserMessage: ctx.currentUserMessage } : {}),
    ...(ctx.filesRead ? { filesRead: ctx.filesRead } : {}),
    emitProgress: (p) => {
      sse.agent({ type: "tool_start", id: tu.id, name: p.label });
      logger.info("tool_dispatch", {
        turnId, toolName: p.label, toolId: tu.id,
        ...(ctx.traceId ? { traceId: ctx.traceId } : {}),
      });
    },
    emitAgentEvent: (event) => {
      // Tool-emitted structured events (task_board, future
      // artifact_* etc.) go onto the same SSE agent channel.
      sse.agent(event as Parameters<typeof sse.agent>[0]);
    },
    emitControlEvent: async (event) => {
      await session.controlEvents?.append(
        event as Parameters<typeof session.controlEvents.append>[0],
      );
    },
    askUser: (q) => ctx.askUser(q),
    staging: {
      stageFileWrite: () => {
        /* Phase 1c: StagedWriteJournal; tools currently write directly */
      },
      stageTranscriptAppend: () => {
        /* no-op — Turn owns transcript directly */
      },
      stageAuditEvent: (event: string, data?: Record<string, unknown>) => {
        // Phase 2h — fire-and-forget append to the per-bot audit log.
        // Best-effort (errors swallowed inside AuditLog) so audit
        // failures never abort a turn (§6 invariant G).
        void session.agent.auditLog.append(
          event,
          session.meta.sessionKey,
          turnId,
          data,
        );
      },
    },
  };

  let result: ToolResult;
  let retryNo = 0;
  for (;;) {
    try {
      result = await (tool as Tool<unknown, unknown>).execute(permittedInput, toolCtx);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      result = {
        status: "error",
        errorCode: "tool_threw",
        errorMessage: msg,
        durationMs: Date.now() - started,
      };
    }
    if (
      retryNo < 1 &&
      !abortController.signal.aborted &&
      isRetrySafeTool(tool as Tool<unknown, unknown>) &&
      isTransientToolFailure(result)
    ) {
      retryNo += 1;
      sse.agent({
        type: "retry",
        reason: result.errorMessage ?? result.errorCode ?? "transient_tool_failure",
        retryNo,
        toolUseId: tu.id,
        toolName: tu.name,
      });
      ctx.stageAuditEvent("tool_retry", {
        toolName: tu.name,
        toolUseId: tu.id,
        retryNo,
        reason: result.errorMessage ?? result.errorCode ?? "transient_tool_failure",
      });
      continue;
    }
    break;
  }

  // P1-1: Track files read by read-class tools
  if (ctx.filesRead && result.status === "ok") {
    trackFilesRead(ctx.filesRead, tu.name, permittedInput);
  }

  const previewSource = result.output ?? result.errorMessage ?? "";
  const preview =
    typeof previewSource === "string"
      ? previewSource
      : JSON.stringify(previewSource);
  const rawContent = softWarningPrefix + summariseToolOutput(result);
  const content = process.env.MAGI_TOOL_RESULT_BUDGET !== "0"
    ? applyToolResultBudget(rawContent, tu.name)
    : rawContent;
  const isError = result.status !== "ok";

  sse.agent({
    type: "tool_end",
    id: tu.id,
    status: result.status,
    durationMs: result.durationMs,
    output_preview:
      preview.length > 400 ? `${preview.slice(0, 400)}...` : preview,
  });
  await session.transcript.append({
    kind: "tool_result",
    ts: Date.now(),
    turnId,
    toolUseId: tu.id,
    status: result.status,
    output: content.slice(0, 64 * 1024),
    isError,
    ...(result.metadata ? { metadata: result.metadata } : {}),
  });

  // ── afterToolUse hook (observer) ───────────────────────────
  void session.agent.hooks.runPost(
    "afterToolUse",
    { toolName: tu.name, toolUseId: tu.id, input: permittedInput, result },
    ctx.buildHookContext("afterToolUse"),
  );

  // ToolSearch returns tool_reference blocks that the API expands into
  // full tool schemas in the model's context. Pass them through as
  // structured content instead of serialising to text.
  if (
    tu.name === "ToolSearch" &&
    result.status === "ok" &&
    result.output &&
    typeof result.output === "object" &&
    "tool_references" in (result.output as Record<string, unknown>) &&
    Array.isArray((result.output as { tool_references: unknown }).tool_references)
  ) {
    const refs = (result.output as { tool_references: Array<{ type: string; tool_name: string }> }).tool_references;
    if (refs.length > 0) {
      return { toolUseId: tu.id, content: refs, isError: false };
    }
  }

  return { toolUseId: tu.id, content, isError };
}

function isBypassLikeMode(mode: PermissionMode): boolean {
  return mode === "bypass" || mode === "workspace-bypass";
}

async function resolvePermissionDecision(
  ctx: ToolDispatchContext,
  tu: Extract<LLMContentBlock, { type: "tool_use" }>,
  started: number,
  permission: PermissionDecision,
  effectiveInput: unknown,
): Promise<{ result: ToolDispatchResult | null; input: unknown }> {
  if (permission.decision === "allow") {
    const input = permission.updatedInput ?? effectiveInput;
    await recordPermissionDecision(ctx, tu.name, "allow", permission.reason, permission.updatedInput);
    return { result: null, input };
  }

  if (permission.decision === "ask") {
    await recordPermissionDecision(ctx, tu.name, "ask", permission.reason);
    const proposedInput = await buildPermissionProposedInput(
      ctx,
      tu.name,
      permission,
      effectiveInput,
    );
    const request = await ctx.session.controlRequests.create({
      kind: "tool_permission",
      turnId: ctx.turnId,
      sessionKey: ctx.session.meta.sessionKey,
      channelName: ctx.session.meta.channel.channelId,
      source: "turn",
      prompt: permission.reason,
      proposedInput,
      expiresAt: Date.now() + 10 * 60_000,
      idempotencyKey: `tool_permission:${ctx.turnId}:${tu.id}`,
    });
    ctx.sse.agent({
      type: "control_event",
      seq: 0,
      event: {
        type: "control_request_created",
        request,
      },
    } as Parameters<typeof ctx.sse.agent>[0]);
    const resolved = await ctx.session.waitForControlRequestResolution(request.requestId);
    if (resolved.state === "approved") {
      const input = approvedInputForTool(tu.name, resolved.updatedInput, effectiveInput);
      await recordPermissionDecision(ctx, tu.name, "allow", "approved by user", input);
      return { result: null, input };
    }
    const msg = permissionDeniedMessage(
      permission.reason,
      resolved.state,
      resolved.feedback,
    );
    await recordPermissionDecision(ctx, tu.name, "deny", msg);
    return { result: await permissionDeniedResult(ctx, tu, started, msg), input: effectiveInput };
  }

  const msg = `permission denied: ${permission.reason}`;
  await recordPermissionDecision(ctx, tu.name, "deny", permission.reason);
  return { result: await permissionDeniedResult(ctx, tu, started, msg), input: effectiveInput };
}

async function buildPermissionProposedInput(
  ctx: ToolDispatchContext,
  toolName: string,
  permission: PermissionDecision,
  effectiveInput: unknown,
): Promise<unknown> {
  if (toolName === "PatchApply") {
    return await buildPatchApplyApprovalInput(
      effectiveInput,
      ctx.session.agent.config.workspaceRoot,
    );
  }
  return permission.decision === "ask"
    ? permission.proposedInput ?? effectiveInput
    : effectiveInput;
}

function approvedInputForTool(
  toolName: string,
  updatedInput: unknown,
  effectiveInput: unknown,
): unknown {
  if (toolName === "PatchApply") {
    return isPatchApplyExecutableInput(updatedInput) ? updatedInput : effectiveInput;
  }
  return updatedInput ?? effectiveInput;
}

function isPatchApplyExecutableInput(value: unknown): boolean {
  return !!value && typeof value === "object" && typeof (value as { patch?: unknown }).patch === "string";
}

function permissionDeniedMessage(
  reason: string,
  state: string,
  feedback?: string,
): string {
  const base = state === "timed_out"
    ? `permission denied: ${reason} (request timed out)`
    : `permission denied: ${reason}`;
  const trimmedFeedback = typeof feedback === "string" ? feedback.trim() : "";
  if (!trimmedFeedback) return base;
  return `${base}\n\nUser feedback:\n${trimmedFeedback.slice(0, 4_000)}`;
}

async function permissionDeniedResult(
  ctx: ToolDispatchContext,
  tu: Extract<LLMContentBlock, { type: "tool_use" }>,
  started: number,
  msg: string,
): Promise<ToolDispatchResult> {
  ctx.sse.agent({
    type: "tool_end",
    id: tu.id,
    status: "permission_denied",
    durationMs: Date.now() - started,
    output_preview: msg,
  });
  await ctx.session.transcript.append({
    kind: "tool_result",
    ts: Date.now(),
    turnId: ctx.turnId,
    toolUseId: tu.id,
    status: "permission_denied",
    output: msg,
    isError: true,
  });
  return { toolUseId: tu.id, content: msg, isError: true };
}

async function recordPermissionDecision(
  ctx: ToolDispatchContext,
  toolName: string,
  decision: "allow" | "deny" | "ask",
  reason: string,
  updatedInput?: unknown,
): Promise<void> {
  try {
    await ctx.session.controlEvents?.append({
      type: "permission_decision",
      turnId: ctx.turnId,
      source: "turn",
      toolName,
      decision,
      reason,
      ...(updatedInput !== undefined ? { updatedInput } : {}),
    });
  } catch (err) {
    logger.warn("permission_decision_event_failed", {
      turnId: ctx.turnId, error: (err as Error).message,
    });
  }
}

const READ_TRACKING_TOOLS: Record<string, (input: unknown) => string[]> = {
  FileRead: (input) => {
    const p = (input as { path?: string })?.path;
    return p ? [p.replace(/^\.\//, "")] : [];
  },
  Grep: (input) => {
    const p = (input as { path?: string })?.path;
    return p ? [p.replace(/^\.\//, "") + (p.endsWith("/") ? "" : "/")] : [];
  },
  Glob: (input) => {
    const p = (input as { path?: string })?.path;
    return p ? [p.replace(/^\.\//, "") + (p.endsWith("/") ? "" : "/")] : [];
  },
};

function trackFilesRead(filesRead: Set<string>, toolName: string, input: unknown): void {
  const extractor = READ_TRACKING_TOOLS[toolName];
  if (!extractor) return;
  for (const p of extractor(input)) {
    if (p) filesRead.add(p);
  }
}
