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
import type { AcceptanceCriterion } from "../execution/ExecutionContract.js";
import { StructuredOutputContract, type StructuredOutputSpec } from "../structured/StructuredOutputContract.js";
import {
  classifyEvidence,
  transcriptEvidenceForTurn,
} from "../verification/VerificationEvidence.js";
import type { RetryBlockKind } from "./RetryController.js";
import type { TurnStopReason } from "./types.js";
import { normalizeUserVisibleRouteMetaTags } from "./visibleText.js";

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
    stopReason?: TurnStopReason;
  };
  /** All assistant blocks emitted across iterations. */
  readonly emittedAssistantBlocks: LLMContentBlock[];
  /** Per-LLM-call assistant messages preserved for canonical replay. */
  readonly canonicalAssistantMessages?: ReadonlyArray<ReadonlyArray<LLMContentBlock>>;
  /** Current retry count for beforeCommit hook payload. */
  readonly commitRetryCount: number;
  /** Mutate the Turn's cached assistantText on commit. */
  readonly setAssistantText: (text: string) => void;
  /** Reject any pending askUser promises on abort. */
  readonly rejectAllPendingAsks: (reason: string) => void;
  /** Cached assistantText, used in abort's afterTurnEnd payload. */
  readonly getAssistantText: () => string;
}

export type CommitResult =
  | { status: "committed"; finalText: string }
  | {
      status: "blocked";
      reason: string;
      finalText: string;
      retryable: boolean;
      retryKind?: RetryBlockKind;
      stopReason?: TurnStopReason;
    };

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
  finalText = normalizeUserVisibleRouteMetaTags(finalText).replace(/^\s+/, "");

  const planVerificationBlock = await planVerificationBlockReason(
    ctx.session,
    finalText,
  );
  if (planVerificationBlock) {
    return {
      status: "blocked",
      reason: planVerificationBlock,
      finalText,
      retryable: true,
    };
  }

  const structuredOutputBlock = await structuredOutputBlockReason(ctx, finalText);
  if (structuredOutputBlock) {
    return {
      status: "blocked",
      reason: structuredOutputBlock.reason,
      finalText,
      retryable: structuredOutputBlock.retryable,
      retryKind: "structured_output_invalid",
      ...(structuredOutputBlock.stopReason
        ? { stopReason: structuredOutputBlock.stopReason }
        : {}),
    };
  }

  // ── beforeCommit hook ───────────────────────────────────────
  const toolCallCount = ctx.emittedAssistantBlocks.filter(
    (b) => b.type === "tool_use",
  ).length;
  const filesChanged = collectFilesChanged(ctx.emittedAssistantBlocks);
  const toolReadHappened = ctx.emittedAssistantBlocks.some(
    (b) =>
      b.type === "tool_use" &&
      typeof (b as { name?: string }).name === "string" &&
      /^(FileRead|Grep|Glob)$/.test((b as { name: string }).name),
  );
  await recordExecutionContractEvidence(ctx);
  const preCommit = await ctx.session.agent.hooks.runPre(
    "beforeCommit",
    {
      assistantText: finalText,
      toolCallCount,
      toolReadHappened,
      userMessage: ctx.userMessage.text,
      retryCount: ctx.commitRetryCount,
      filesChanged,
    },
    ctx.buildHookContext("beforeCommit"),
  );
  if (preCommit.action === "block") {
    const reason = preCommit.reason ?? "beforeCommit blocked";
    return {
      status: "blocked",
      reason,
      finalText,
      retryable: isBeforeCommitBlockRetryable(reason),
    };
  }

  await appendCanonicalAssistantMessages(ctx);
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
  const stopReason = ctx.meta.stopReason ?? "end_turn";
  ctx.meta.stopReason = stopReason;
  await appendStopReason(ctx, stopReason);
  ctx.sse.agent({
    type: "turn_end",
    turnId: ctx.turnId,
    status: "committed",
    stopReason,
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

  return { status: "committed", finalText };
}

export function isBeforeCommitBlockRetryable(reason: string): boolean {
  const normalized = reason.trim();
  if (/^\[RULE:SEALED_FILES\]/u.test(normalized)) return false;
  if (/^hook:[^\s]+ threw:/iu.test(normalized)) return false;
  if (/^hook:[^\s]+ .*?(?:timeout|timed out)/iu.test(normalized)) return false;
  return true;
}

async function recordExecutionContractEvidence(
  ctx: CommitPipelineContext,
): Promise<void> {
  const contract = ctx.session.executionContract;
  if (!contract) return;
  try {
    const transcript = await ctx.session.transcript.readAll();
    const evidence = transcriptEvidenceForTurn(transcript, ctx.turnId);
    const classified = classifyEvidence(evidence);
    if (!classified.verification) return;
    const criterionIds = inferCriterionIdsForVerification(
      contract.snapshot().taskState.criteria,
      classified.verificationCommands,
    );
    contract.recordVerificationEvidence({
      source: "beforeCommit",
      status: "passed",
      command: classified.verificationCommands[0],
      ...(criterionIds.length > 0 ? { criterionIds } : {}),
      detail:
        classified.verificationCommands.length > 0
          ? classified.verificationCommands.join("; ")
          : `verification tools: ${classified.tools.join(", ")}`,
    });
  } catch {
    // Verification evidence is advisory state; existing commit gates
    // still run against transcript directly and can block if needed.
  }
}

function inferCriterionIdsForVerification(
  criteria: AcceptanceCriterion[],
  commands: string[],
): string[] {
  const pending = criteria.filter(
    (criterion) =>
      criterion.required &&
      criterion.status !== "passed" &&
      criterion.status !== "waived",
  );
  if (pending.length === 0) return [];
  if (pending.length === 1) return [pending[0]!.id];

  const commandText = commands.join("\n").toLowerCase();
  if (!commandText) return [];
  return pending
    .filter((criterion) => criterionMatchesVerificationCommand(criterion.text, commandText))
    .map((criterion) => criterion.id);
}

function criterionMatchesVerificationCommand(
  criterionText: string,
  commandText: string,
): boolean {
  const text = criterionText.toLowerCase();
  const testCriterion = /(?:test|tests|테스트)/i.test(text);
  const lintCriterion = /(?:lint|eslint|린트)/i.test(text);
  const buildCriterion = /(?:build|빌드)/i.test(text);
  const verifyCriterion = /(?:verify|verification|검증|확인)/i.test(text);

  if (testCriterion && /\b(?:npm\s+(?:run\s+)?test|pnpm\s+test|yarn\s+test|bun\s+test|vitest|jest|pytest|go\s+test|cargo\s+test)\b/i.test(commandText)) {
    return true;
  }
  if (lintCriterion && /\b(?:npm\s+(?:run\s+)?lint|pnpm\s+lint|yarn\s+lint|bun\s+lint|eslint|ruff|clippy)\b/i.test(commandText)) {
    return true;
  }
  if (buildCriterion && /\b(?:npm\s+(?:run\s+)?build|pnpm\s+build|yarn\s+build|bun\s+build|tsc|cargo\s+build|go\s+build)\b/i.test(commandText)) {
    return true;
  }
  if (verifyCriterion && /\b(?:test|lint|build|verify|check|qa|검증)\b/i.test(commandText)) {
    return true;
  }
  return false;
}

async function appendCanonicalAssistantMessages(ctx: CommitPipelineContext): Promise<void> {
  const messages = (ctx.canonicalAssistantMessages ?? []).filter((blocks) => blocks.length > 0);
  if (messages.length === 0) return;
  const ts = Date.now();
  for (let i = 0; i < messages.length; i += 1) {
    await ctx.session.transcript.append({
      kind: "canonical_message",
      ts,
      turnId: ctx.turnId,
      messageId: `${ctx.turnId}:assistant:${i + 1}`,
      role: "assistant",
      content: JSON.parse(JSON.stringify(messages[i])) as unknown[],
    });
  }
}

async function planVerificationBlockReason(
  session: Session,
  finalText: string,
): Promise<string | null> {
  if (typeof session.controlProjection !== "function") return null;
  const projection = await session.controlProjection();
  if (projection.activePlan?.state !== "verification_pending") return null;
  if (
    projection.verification &&
    typeof projection.verification === "object" &&
    (projection.verification as { status?: unknown }).status === "passed"
  ) {
    return null;
  }
  if (declaresUnverifiedOrPartial(finalText)) return null;
  return [
    "approved plan is still verification_pending",
    "Run the relevant verification command or explicitly report the result as unverified/partial.",
  ].join(". ");
}

async function structuredOutputBlockReason(
  ctx: CommitPipelineContext,
  finalText: string,
): Promise<{
  reason: string;
  retryable: boolean;
  stopReason?: TurnStopReason;
} | null> {
  const spec = structuredOutputSpecOf(ctx.session);
  if (!spec) return null;
  const contract = new StructuredOutputContract(spec);
  const assessment = await contract.assess({
    text: finalText,
    turnId: ctx.turnId,
    attempt: ctx.commitRetryCount + 1,
    emitAgentEvent: (event) => ctx.sse.agent(event),
    emitControlEvent: async (event) => {
      await optionalControlEvents(ctx.session)?.append(event);
    },
  });
  if (assessment.ok) return null;
  return {
    reason: assessment.reason,
    retryable: assessment.status !== "retry_exhausted",
    ...(assessment.status === "retry_exhausted"
      ? { stopReason: "structured_output_retry_exhausted" as const }
      : {}),
  };
}

function structuredOutputSpecOf(session: Session): StructuredOutputSpec | null {
  const candidate = session as unknown as {
    getStructuredOutputContract?: () => StructuredOutputSpec | null;
    structuredOutputContract?: StructuredOutputSpec | null;
  };
  return candidate.getStructuredOutputContract?.() ?? candidate.structuredOutputContract ?? null;
}

function declaresUnverifiedOrPartial(text: string): boolean {
  return /\b(unverified|not verified|partial|partially verified|unable to verify|cannot verify)\b/i.test(text) ||
    /검증(?:하지|을)?\s*못|미검증|부분\s*검증|확인(?:하지|을)?\s*못/.test(text);
}

/**
 * Abort path: phase=aborted → reject pending asks → turn_aborted
 * transcript (best-effort) → turn_end "aborted" SSE → onAbort +
 * afterTurnEnd observers.
 */
export async function abort(
  ctx: CommitPipelineContext,
  reason: string,
  stopReason: TurnStopReason = "aborted",
): Promise<void> {
  ctx.setPhase("aborted");
  ctx.meta.endedAt = Date.now();
  ctx.meta.stopReason = stopReason;
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
  await appendStopReason(ctx, stopReason);
  ctx.sse.agent({
    type: "turn_end",
    turnId: ctx.turnId,
    status: "aborted",
    stopReason,
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

async function appendStopReason(
  ctx: CommitPipelineContext,
  reason: TurnStopReason,
): Promise<void> {
  try {
    await optionalControlEvents(ctx.session)?.append({
      type: "stop_reason",
      turnId: ctx.turnId,
      reason,
    });
  } catch {
    /* control-event telemetry must not prevent turn close */
  }
}

function optionalControlEvents(session: Session): {
  append: (event: Parameters<Session["controlEvents"]["append"]>[0]) => Promise<unknown>;
} | null {
  const ledger = (session as unknown as { controlEvents?: unknown }).controlEvents;
  if (!ledger || typeof ledger !== "object") return null;
  const append = (ledger as { append?: unknown }).append;
  if (typeof append !== "function") return null;
  return ledger as {
    append: (event: Parameters<Session["controlEvents"]["append"]>[0]) => Promise<unknown>;
  };
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
