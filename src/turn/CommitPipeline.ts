/**
 * CommitPipeline — turn commit + abort orchestration.
 *
 * Extracted from Turn.commit / Turn.abort (R3 refactor, 2026-04-19).
 * Owns:
 *   • beforeCommit gate (blockable)
 *   • assistant_text + turn_committed transcript append
 *   • turn_end SSE emission + legacy finish
 *   • afterCommit / afterTurnEnd / onTaskCheckpoint observer fires
 *   • abort path — reject pending asks, turn_aborted transcript,
 *     turn_end "aborted" SSE, onAbort + afterTurnEnd observers
 *
 * The pipeline uses a context object so Turn state (meta, blocks) is
 * explicit and testable. Policy is preserved byte-for-byte.
 */

import type { Session } from "../Session.js";
import type { SseWriter } from "../transport/SseWriter.js";
import type { LLMContentBlock } from "../transport/LLMClient.js";
import type { HookContext } from "../hooks/types.js";
import type { UserMessage, TokenUsage } from "../util/types.js";

export type CommitHookPoint =
  | "beforeCommit"
  | "afterCommit"
  | "afterTurnEnd"
  | "onTaskCheckpoint"
  | "onAbort";

export interface CommitPipelineContext {
  readonly session: Session;
  readonly sse: SseWriter;
  readonly userMessage: UserMessage;
  readonly turnId: string;
  readonly startedAt: number;
  readonly buildHookContext: (point: CommitHookPoint) => HookContext;
  /** Turn phase setter — delegates to private Turn.setPhase. */
  readonly setPhase: (phase: "committing" | "committed" | "aborted") => void;
  /** Getter for the mutable turn meta (usage, endedAt etc.). */
  readonly meta: {
    usage: TokenUsage;
    endedAt?: number;
  };
  /** All assistant blocks emitted across iterations. */
  readonly emittedAssistantBlocks: LLMContentBlock[];
  /** Current retry count for beforeCommit hook payload. */
  readonly commitRetryCount: number;
  /** Mutate the Turn's cached assistantText on commit. */
  readonly setAssistantText: (text: string) => void;
  /** Reject any pending askUser promises on abort. */
  readonly rejectAllPendingAsks: (reason: string) => void;
  /** Cached assistantText, used in abort's afterTurnEnd payload. */
  readonly getAssistantText: () => string;
}

export interface CommitResult {
  finalText: string;
}

/**
 * Commit path: beforeCommit → assistant_text append → turn_committed
 * append → phase=committed → turn_end SSE → observer hooks.
 */
export async function commit(ctx: CommitPipelineContext): Promise<CommitResult> {
  ctx.setPhase("committing");
  // Final assistant text = concatenation of all text blocks emitted
  // across every iteration in this turn. Tool calls already appended
  // tool_call / tool_result entries inline.
  let finalText = ctx.emittedAssistantBlocks
    .filter((b): b is Extract<LLMContentBlock, { type: "text" }> => b.type === "text")
    .map((b) => b.text)
    .join("");

  // Thinking-to-text fallback — if the model put everything in thinking
  // and produced no/minimal text (common with extended thinking + tool
  // loops), extract the last meaningful portion of thinking as the text
  // response. Claude.ai never shows empty text after thinking; we
  // replicate that by falling back to thinking content.
  if (finalText.trim().length < 50) {
    const thinkingBlocks = ctx.emittedAssistantBlocks
      .filter((b): b is Extract<LLMContentBlock, { type: "thinking" }> => b.type === "thinking")
      .map((b) => b.thinking);
    const thinkingContent = thinkingBlocks.join("\n").trim();
    if (thinkingContent.length > 100) {
      // Extract the last part of thinking that looks like a user-facing
      // response (usually the model's final answer is at the end of
      // thinking when it forgets to emit text).
      const lines = thinkingContent.split("\n");
      // Take last ~2000 chars as fallback response
      let fallback = "";
      for (let i = lines.length - 1; i >= 0 && fallback.length < 2000; i--) {
        fallback = lines[i] + "\n" + fallback;
      }
      finalText = fallback.trim();
      console.log(
        `[core-agent] thinking-to-text fallback: thinkingLen=${thinkingContent.length}` +
        ` extractedLen=${finalText.length} turnId=${ctx.turnId}`,
      );
      // Emit the fallback text to the SSE stream so the client sees it
      ctx.sse.agent({ type: "text_delta", delta: finalText });
    }
  }

  // ── beforeCommit hook ───────────────────────────────────────
  const toolCallCount = ctx.emittedAssistantBlocks.filter(
    (b) => b.type === "tool_use",
  ).length;
  const toolReadHappened = ctx.emittedAssistantBlocks.some(
    (b) =>
      b.type === "tool_use" &&
      typeof (b as { name?: string }).name === "string" &&
      /^(FileRead|Grep|Glob)$/.test((b as { name: string }).name),
  );
  const preCommit = await ctx.session.agent.hooks.runPre(
    "beforeCommit",
    {
      assistantText: finalText,
      toolCallCount,
      toolReadHappened,
      userMessage: ctx.userMessage.text,
      retryCount: ctx.commitRetryCount,
    },
    ctx.buildHookContext("beforeCommit"),
  );
  if (preCommit.action === "block") {
    throw new Error(`beforeCommit blocked: ${preCommit.reason}`);
  }

  if (finalText.length > 0) {
    await ctx.session.transcript.append({
      kind: "assistant_text",
      ts: Date.now(),
      turnId: ctx.turnId,
      text: finalText,
    });
    ctx.setAssistantText(finalText);
  }
  await ctx.session.transcript.append({
    kind: "turn_committed",
    ts: Date.now(),
    turnId: ctx.turnId,
    inputTokens: ctx.meta.usage.inputTokens,
    outputTokens: ctx.meta.usage.outputTokens,
  });
  ctx.setPhase("committed");
  ctx.meta.endedAt = Date.now();
  ctx.sse.agent({
    type: "turn_end",
    turnId: ctx.turnId,
    status: "committed",
  });
  ctx.sse.legacyFinish();

  // ── afterCommit + afterTurnEnd observers ───────────────────
  void ctx.session.agent.hooks.runPost(
    "afterCommit",
    { assistantText: finalText },
    ctx.buildHookContext("afterCommit"),
  );
  void ctx.session.agent.hooks.runPost(
    "afterTurnEnd",
    {
      userMessage: ctx.userMessage.text,
      assistantText: finalText,
      status: "committed",
    },
    ctx.buildHookContext("afterTurnEnd"),
  );

  // ── onTaskCheckpoint (hipocampus feed) ─────────────────────
  // Observer only — never blocks. Built-in hipocampusCheckpoint and
  // any user-authored memory hook consume this.
  const toolNames = ctx.emittedAssistantBlocks
    .filter((b): b is Extract<LLMContentBlock, { type: "tool_use" }> => b.type === "tool_use")
    .map((b) => b.name);
  const filesChanged = collectFilesChanged(ctx.emittedAssistantBlocks);
  void ctx.session.agent.hooks.runPost(
    "onTaskCheckpoint",
    {
      userMessage: ctx.userMessage.text,
      assistantText: finalText,
      toolCallCount: toolNames.length,
      toolNames,
      filesChanged,
      startedAt: ctx.startedAt,
      endedAt: ctx.meta.endedAt ?? Date.now(),
    },
    ctx.buildHookContext("onTaskCheckpoint"),
  );

  return { finalText };
}

/**
 * Abort path: phase=aborted → reject pending asks → turn_aborted
 * transcript (best-effort) → turn_end "aborted" SSE → onAbort +
 * afterTurnEnd observers.
 */
export async function abort(
  ctx: CommitPipelineContext,
  reason: string,
): Promise<void> {
  ctx.setPhase("aborted");
  ctx.meta.endedAt = Date.now();
  // Any tools still waiting on the human must unblock so their
  // in-flight execute() promise resolves before the turn returns.
  ctx.rejectAllPendingAsks(reason);
  // Best-effort abort log; failure here is non-fatal.
  try {
    await ctx.session.transcript.append({
      kind: "turn_aborted",
      ts: Date.now(),
      turnId: ctx.turnId,
      reason,
    });
  } catch {
    /* swallow */
  }
  ctx.sse.agent({
    type: "turn_end",
    turnId: ctx.turnId,
    status: "aborted",
    reason,
  });
  ctx.sse.legacyFinish();

  // ── onAbort + afterTurnEnd observers ───────────────────────
  void ctx.session.agent.hooks.runPost(
    "onAbort",
    { reason },
    ctx.buildHookContext("onAbort"),
  );
  void ctx.session.agent.hooks.runPost(
    "afterTurnEnd",
    {
      userMessage: ctx.userMessage.text,
      assistantText: ctx.getAssistantText(),
      status: "aborted",
      reason,
    },
    ctx.buildHookContext("afterTurnEnd"),
  );
}

/**
 * Best-effort extract of workspace-relative paths the turn wrote to,
 * by scanning FileWrite / FileEdit tool_use inputs.
 */
export function collectFilesChanged(
  blocks: ReadonlyArray<LLMContentBlock>,
): string[] {
  const out: string[] = [];
  for (const b of blocks) {
    if (b.type !== "tool_use") continue;
    if (b.name !== "FileWrite" && b.name !== "FileEdit") continue;
    const p = (b.input as { path?: unknown } | null)?.path;
    if (typeof p === "string" && p.length > 0) out.push(p);
  }
  return [...new Set(out)];
}
