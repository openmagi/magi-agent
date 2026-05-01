/**
 * ContextEngine — first-class compaction boundary orchestration (T1-02).
 * Design reference: §7.12.b (revised 2026-04-19) +
 * docs/plans/2026-04-19-core-agent-phase-3-plan.md §3 T1-02.
 *
 * Replaces the superseded "anchor-in-prompt" design (§7.12.b draft)
 * which replicated legacy gateway issue #48547 — second-pass compaction
 * absorbed the regex-detected `<compaction-handoff>` string and the
 * boundary vanished. See docs/notes/2026-04-19-cc-parity-audit-01-agent-loop.md.
 *
 * The revised design stores each compaction as a
 * `TranscriptEntry.kind = "compaction_boundary"` row inside the
 * append-only transcript. `buildMessagesFromTranscript` partitions
 * entries around every boundary and emits a single synthetic system
 * summary message per boundary; entries AFTER the latest boundary
 * replay as normal user / assistant / tool messages. The model never
 * sees the literal anchor string — the summary IS the content of an
 * ordinary system message.
 *
 * Mirrors CC's `src/services/compact/` separation of concerns.
 */

import crypto from "node:crypto";
import { monotonicFactory } from "ulid";
import type { Session } from "../../Session.js";
import type { LLMClient, LLMMessage, LLMContentBlock } from "../../transport/LLMClient.js";
import type { TranscriptEntry } from "../../storage/Transcript.js";
import { isCompactionBoundary } from "../../storage/Transcript.js";
import { getContextWindowOrDefault } from "../../llm/modelCapabilities.js";

export type CompactionBoundaryEntry = Extract<
  TranscriptEntry,
  { kind: "compaction_boundary" }
>;

/**
 * Gap §11.6 — default reserve-token floor (response + tool-result
 * headroom) requested when no per-engine override is given. Capped at
 * runtime to `contextWindow * RESERVE_TOKEN_CAP_FRACTION` so small-
 * window models never starve themselves of live budget.
 */
export const DEFAULT_RESERVE_TOKENS = 40_000;

/**
 * Gap §11.6 — cap the reserve floor at 20 % of the model's context
 * window. Rationale:
 *   - 20 % is the "one-shot response + a couple of tool round-trips"
 *     budget that keeps the live transcript feasible even after a
 *     maximal compaction.
 *   - Any higher and a 16k-window model would reserve more than the
 *     post-compaction summary fits, producing an infinite compact
 *     loop (the exact failure mode Codex CLI hit upstream).
 *   - Any lower and a large-window model would leak too much budget
 *     into history, forcing premature compaction under normal load.
 */
export const RESERVE_TOKEN_CAP_FRACTION = 0.2;

/**
 * Gap §11.6 — minimum viable live budget (post-compaction) below which
 * we declare compaction_impossible instead of looping. 5k is empirical
 * for "Haiku can still emit a boundary summary and the successor turn
 * can still produce a usable reply + one tool call."
 */
export const DEFAULT_MIN_VIABLE_BUDGET_TOKENS = 5_000;

/**
 * Emitted to the SSE stream when the routed model's effective budget
 * cannot fit even a fully compacted transcript. The caller (Session)
 * translates this into an `agent_event.compaction_impossible` + a
 * user-facing text_delta in Korean.
 */
export class CompactionImpossibleError extends Error {
  readonly code = "compaction_impossible";
  readonly model: string;
  readonly contextWindow: number;
  readonly effectiveReserveTokens: number;
  readonly effectiveBudgetTokens: number;
  readonly minViableBudgetTokens: number;

  constructor(opts: {
    model: string;
    contextWindow: number;
    effectiveReserveTokens: number;
    effectiveBudgetTokens: number;
    minViableBudgetTokens: number;
  }) {
    super(
      `compaction_impossible: model=${opts.model} window=${opts.contextWindow} ` +
        `reserve=${opts.effectiveReserveTokens} budget=${opts.effectiveBudgetTokens} ` +
        `min=${opts.minViableBudgetTokens}`,
    );
    this.name = "CompactionImpossibleError";
    this.model = opts.model;
    this.contextWindow = opts.contextWindow;
    this.effectiveReserveTokens = opts.effectiveReserveTokens;
    this.effectiveBudgetTokens = opts.effectiveBudgetTokens;
    this.minViableBudgetTokens = opts.minViableBudgetTokens;
  }
}

export interface ContextEngineOptions {
  /** Default wall-clock deadline for the Haiku summarisation call. */
  haikuDeadlineMs?: number;
  /** Override the Haiku model id (tests). */
  summaryModel?: string;
  /**
   * Gap §11.6 — caller-configured reserve-token floor for live
   * response + tool-result budget. The engine caps this at runtime
   * to `contextWindow * RESERVE_TOKEN_CAP_FRACTION`, so a caller
   * requesting 40k on a 16k-window model will see the effective
   * reserve shrink to 3.2k rather than starve the model.
   */
  reserveTokens?: number;
  /**
   * Gap §11.6 — minimum post-compaction live budget below which the
   * engine gives up with `CompactionImpossibleError` instead of
   * looping. Default 5k.
   */
  minViableBudgetTokens?: number;
  /**
   * Gap §11.6 — resolver that takes a model id and returns its
   * context window. Injected so tests can stub hypothetical 16k /
   * 2k models without editing the real capability registry.
   * Default: `getContextWindowOrDefault` from modelCapabilities.
   */
  contextWindowResolver?: (model: string) => number;
}

/**
 * ContextEngine decides whether to compact and rehydrates transcripts
 * into LLM messages across prior compaction boundaries.
 */
export class ContextEngine {
  private readonly ulid = monotonicFactory();
  private readonly haikuDeadlineMs: number;
  private readonly summaryModel: string;
  private readonly configuredReserveTokens: number;
  private readonly minViableBudgetTokens: number;
  private readonly contextWindowResolver: (model: string) => number;

  constructor(
    private readonly llm: LLMClient,
    opts: ContextEngineOptions = {},
  ) {
    this.haikuDeadlineMs = opts.haikuDeadlineMs ?? 10_000;
    this.summaryModel = opts.summaryModel ?? "claude-haiku-4-5";
    this.configuredReserveTokens = opts.reserveTokens ?? DEFAULT_RESERVE_TOKENS;
    this.minViableBudgetTokens =
      opts.minViableBudgetTokens ?? DEFAULT_MIN_VIABLE_BUDGET_TOKENS;
    this.contextWindowResolver =
      opts.contextWindowResolver ?? getContextWindowOrDefault;
  }

  /**
   * Gap §11.6 — compute the effective reserve floor for a model,
   * capped at `RESERVE_TOKEN_CAP_FRACTION` of its context window.
   *
   * Exposed for tests and observability; Turn-layer code should keep
   * calling `maybeCompact`, which uses this internally.
   */
  effectiveReserveTokens(model: string): number {
    const windowTokens = this.contextWindowResolver(model);
    const cap = Math.floor(windowTokens * RESERVE_TOKEN_CAP_FRACTION);
    return Math.min(this.configuredReserveTokens, cap);
  }

  /**
   * Decide whether the current transcript exceeds `tokenLimit` and, if
   * so, summarise everything up to now into a new
   * `compaction_boundary` entry appended to the transcript.
   *
   * Returns the created boundary entry, or `null` when:
   *   - token count is under threshold; OR
   *   - the Haiku call failed (fail-open — never block a turn on a
   *     failed compaction attempt).
   *
   * Gap §11.6 — before compacting, the engine verifies that the routed
   * model's context window can hold `(effectiveReserveFloor + minViable
   * LiveBudget)` at all. If even a perfectly compacted transcript would
   * leave < `minViableBudgetTokens` of live room, the engine throws
   * `CompactionImpossibleError` so the caller can translate it into a
   * user-facing SSE `compaction_impossible` event + prompt the user to
   * switch to a larger-window model. Without this cap a small-context
   * model routed mid-session would loop forever trying to compact into
   * a reserve that's already larger than its window.
   *
   * The `model` parameter is used only for the §11.6 cap/floor check;
   * it does NOT affect the Haiku summariser (summaryModel). When omitted
   * the engine skips the §11.6 check — existing callers who haven't
   * opted in behave exactly as before (29d8da97 boundary semantics
   * untouched).
   *
   * Idempotency / race: `summaryHash` is sha256 of `summaryText`. If
   * two boundaries land simultaneously the one with the lower
   * `boundaryId` (ULID lex order) wins when `buildMessagesFromTranscript`
   * sorts by on-disk order. No regex re-parsing is ever performed.
   */
  async maybeCompact(
    session: Session,
    transcriptEntries: readonly TranscriptEntry[],
    tokenLimit: number,
    model?: string,
  ): Promise<CompactionBoundaryEntry | null> {
    const before = estimateTranscriptTokens(transcriptEntries);
    if (before < tokenLimit) return null;

    // §11.6 pre-flight: is there enough window to even bother trying?
    // Runs only when the caller passed the model id. Throws so the
    // caller's error handler can surface `compaction_impossible` —
    // silently returning null would drop the turn into an infinite
    // compaction-loop at the call site.
    if (model !== undefined) {
      this.assertCompactionFeasible(model);
    }

    const summaryText = await this.summarise(transcriptEntries);
    if (summaryText === null) {
      // Fail-open: Haiku failed / timed out. No boundary this turn.
      return null;
    }

    const after = estimateTextTokens(summaryText);

    // §11.6 post-flight: even after compaction, does the live budget
    // clear the minimum viable threshold? Typically redundant with the
    // pre-flight check (pre-flight rejects tiny windows by definition),
    // but protects against a Haiku summary that expanded past the
    // window anyway.
    if (model !== undefined) {
      const windowTokens = this.contextWindowResolver(model);
      const reserve = this.effectiveReserveTokens(model);
      const postBudget = windowTokens - reserve - after;
      if (postBudget < this.minViableBudgetTokens) {
        throw new CompactionImpossibleError({
          model,
          contextWindow: windowTokens,
          effectiveReserveTokens: reserve,
          effectiveBudgetTokens: postBudget,
          minViableBudgetTokens: this.minViableBudgetTokens,
        });
      }
    }

    const summaryHash = sha256Hex(summaryText);
    const createdAt = Date.now();
    const boundary: CompactionBoundaryEntry = {
      kind: "compaction_boundary",
      ts: createdAt + 1,
      turnId: session.meta.sessionKey, // sessionKey as scope — no active turn at compaction time
      boundaryId: this.ulid(),
      beforeTokenCount: before,
      afterTokenCount: after,
      summaryHash,
      summaryText,
      createdAt,
    };
    await session.transcript.append({
      kind: "canonical_message",
      ts: createdAt,
      turnId: session.meta.sessionKey,
      messageId: `cm_${boundary.boundaryId}`,
      role: "system",
      content: [{ type: "text", text: renderBoundaryContent(boundary) }],
    });
    await session.transcript.append(boundary);
    await session.controlEvents?.append({
      type: "compaction_boundary",
      turnId: session.meta.sessionKey,
      boundaryId: boundary.boundaryId,
      beforeTokenCount: before,
      afterTokenCount: after,
      summaryHash,
      ts: createdAt + 2,
    });
    return boundary;
  }

  /**
   * §11.6 pre-flight. Throws `CompactionImpossibleError` if the model's
   * context window can't accommodate the reserve floor + minimum
   * viable live budget, even before a single token is spent on
   * transcript history. Shared between `maybeCompact` and any future
   * route-time model-swap guard.
   */
  assertCompactionFeasible(model: string): void {
    const windowTokens = this.contextWindowResolver(model);
    const reserve = this.effectiveReserveTokens(model);
    const headroom = windowTokens - reserve;
    if (headroom < this.minViableBudgetTokens) {
      throw new CompactionImpossibleError({
        model,
        contextWindow: windowTokens,
        effectiveReserveTokens: reserve,
        effectiveBudgetTokens: headroom,
        minViableBudgetTokens: this.minViableBudgetTokens,
      });
    }
  }

  /**
   * Rehydrate a transcript into LLM messages, collapsing everything
   * BEFORE the latest compaction boundary (inclusive of any earlier
   * boundaries) into a single synthetic system summary message per
   * boundary. Post-boundary entries replay as ordinary messages.
   *
   * When the transcript contains multiple boundaries, each boundary's
   * `summaryText` produces its own system message; entries in between
   * two boundaries are discarded (they were already absorbed into the
   * later boundary's summary). Only the entries AFTER the final
   * boundary replay verbatim.
   */
  buildMessagesFromTranscript(entries: readonly TranscriptEntry[]): LLMMessage[] {
    const sorted = [...entries].sort(sortEntries);

    // Find the last compaction boundary. Everything before it is
    // superseded by its summary; only that summary + post-boundary
    // entries survive. If no boundary, replay the whole transcript.
    let lastBoundaryIdx = -1;
    for (let i = sorted.length - 1; i >= 0; i--) {
      const entry = sorted[i];
      if (entry && isCompactionBoundary(entry)) {
        lastBoundaryIdx = i;
        break;
      }
    }

    if (lastBoundaryIdx >= 0) {
      const boundary = sorted[lastBoundaryIdx] as CompactionBoundaryEntry;
      const postBoundary = sorted.slice(lastBoundaryIdx + 1);
      const messages: LLMMessage[] = [];
      messages.push(renderBoundaryAsSystemMessage(boundary));
      messages.push(...transcriptEntriesToMessages(postBoundary));
      return messages;
    }

    return transcriptEntriesToMessages(sorted);
  }

  /**
   * Drive a single Haiku summarisation round-trip against the
   * pre-compaction transcript. Returns `null` on any failure so the
   * caller fails open (no boundary is written).
   */
  private async summarise(
    entries: readonly TranscriptEntry[],
  ): Promise<string | null> {
    const deadline = Date.now() + this.haikuDeadlineMs;
    const system = [
      "You compact a conversational transcript into a compact handoff",
      "summary for a successor assistant instance. Preserve:",
      "- Active task / goal.",
      "- Preserve execution-contract state exactly when present:",
      "  goal, constraints, current plan, completed steps, blockers, acceptance criteria, verification evidence, artifacts, and remaining risks.",
      "- Decisions already made.",
      "- Open questions / pending sub-tasks.",
      "- Files, ids, and numeric values the successor needs.",
      "Write 10-30 lines of dense prose. No preamble, no postamble.",
    ].join("\n");

    const userPayload = renderEntriesForSummary(entries).slice(0, 180_000);

    let output = "";
    try {
      const stream = this.llm.stream({
        model: this.summaryModel,
        system,
        messages: [{ role: "user", content: userPayload }],
        max_tokens: 1024,
        temperature: 0,
      });
      for await (const evt of stream) {
        if (Date.now() > deadline) return null;
        if (evt.kind === "text_delta") output += evt.delta;
        if (evt.kind === "error") return null;
        if (evt.kind === "message_end") break;
      }
    } catch {
      return null;
    }

    const trimmed = output.trim();
    if (trimmed.length === 0) return null;
    return trimmed;
  }
}

// ── helpers ────────────────────────────────────────────────────────────

function sortEntries(a: TranscriptEntry, b: TranscriptEntry): number {
  if (a.ts !== b.ts) return a.ts - b.ts;
  // Deterministic tie-breaker: compaction_boundary sorts by boundaryId
  // (ULID — lex order == time order) so simultaneous boundaries pick
  // a stable winner.
  if (isCompactionBoundary(a) && isCompactionBoundary(b)) {
    return a.boundaryId < b.boundaryId ? -1 : a.boundaryId > b.boundaryId ? 1 : 0;
  }
  return 0;
}

function renderBoundaryAsSystemMessage(
  boundary: CompactionBoundaryEntry,
): LLMMessage {
  return {
    role: "user",
    content: [{ type: "text", text: renderBoundaryContent(boundary) } as LLMContentBlock],
  };
}

function renderBoundaryContent(boundary: CompactionBoundaryEntry): string {
  const iso = new Date(boundary.createdAt).toISOString();
  return `[Compaction boundary ${boundary.boundaryId} @ ${iso}]\n${boundary.summaryText}`;
}

/**
 * Convert a flat sorted list of transcript entries into a properly
 * alternating user/assistant message sequence for the Anthropic API.
 *
 * 2026-04-22 fundamental rewrite: the previous implementation only
 * handled `user_message` and `assistant_text`, silently dropping
 * `tool_call` and `tool_result`. This caused:
 *   - Consecutive assistant messages (tool interactions removed)
 *   - Consecutive user messages (tool_result gap)
 *   - Bot responding to wrong message (context corruption)
 *
 * legacy gateway reference: `anthropic-transport-stream.ts` groups blocks
 * by turn, merges tool_use into assistant messages, and batches
 * tool_result into user messages. We replicate that here.
 *
 * Anthropic API contract:
 *   - Messages MUST strictly alternate: user → assistant → user → ...
 *   - assistant content can have: text, thinking, tool_use blocks
 *   - user content can have: text, tool_result blocks
 *   - Multiple tool_result blocks can be in one user message
 */
function transcriptEntriesToMessages(
  entries: readonly TranscriptEntry[],
): LLMMessage[] {
  const messages: LLMMessage[] = [];
  const canonicalAssistantTurnIds = new Set(
    entries
      .filter(
        (entry): entry is Extract<TranscriptEntry, { kind: "canonical_message" }> =>
          entry.kind === "canonical_message" && entry.role === "assistant",
      )
      .map((entry) => entry.turnId),
  );
  const canonicalAssistantConsumedToolResult = new Set<string>();

  // Accumulator for the current message being built.
  let pendingAssistantBlocks: LLMContentBlock[] = [];
  let pendingToolResults: LLMContentBlock[] = [];

  function flushAssistant(): void {
    if (pendingAssistantBlocks.length === 0) return;
    messages.push({ role: "assistant", content: [...pendingAssistantBlocks] });
    pendingAssistantBlocks = [];
  }

  function flushToolResults(): void {
    if (pendingToolResults.length === 0) return;
    messages.push({ role: "user", content: [...pendingToolResults] });
    pendingToolResults = [];
  }

  for (const entry of entries) {
    switch (entry.kind) {
      case "user_message": {
        // Flush any pending assistant/tool_result before user message
        flushAssistant();
        flushToolResults();
        const iso = new Date(entry.ts).toISOString();
        // Truncate historical user messages that contain inline file
        // content (can be 500KB+). Replace file content blocks with a
        // short placeholder so the LLM knows the file existed.
        let userText = entry.text;
        if (userText.length > 8192) {
          userText = userText.replace(
            /--- file content ---[\s\S]*?--- end file ---/g,
            "[file content truncated for context — use FileRead to access]",
          );
        }
        messages.push({
          role: "user",
          content: `[Time: ${iso}]\n${userText}`,
        });
        break;
      }
      case "assistant_text": {
        if (
          canonicalAssistantTurnIds.has(entry.turnId) &&
          !canonicalAssistantConsumedToolResult.has(entry.turnId)
        ) {
          break;
        }
        // Flush any pending tool_results first (they must come before
        // the next assistant message per Anthropic alternation).
        flushToolResults();
        // Accumulate — assistant text + tool_use blocks from the same
        // turn will be merged into a single assistant message.
        pendingAssistantBlocks.push({ type: "text", text: entry.text });
        break;
      }
      case "tool_call": {
        if (
          canonicalAssistantTurnIds.has(entry.turnId) &&
          !canonicalAssistantConsumedToolResult.has(entry.turnId)
        ) {
          break;
        }
        // tool_use blocks go into the assistant message alongside text.
        // Flush tool_results first if any are pending (from a prior
        // tool round in the same turn).
        flushToolResults();
        // Truncate large tool inputs in historical replay to avoid
        // context explosion (267-turn sessions with many tool calls).
        const inputStr = JSON.stringify(entry.input ?? {});
        const truncatedInput = inputStr.length > 1024
          ? JSON.parse(JSON.stringify({ _truncated: true, name: entry.name, preview: inputStr.slice(0, 512) }))
          : entry.input;
        pendingAssistantBlocks.push({
          type: "tool_use",
          id: entry.toolUseId,
          name: entry.name,
          input: truncatedInput,
        });
        break;
      }
      case "tool_result": {
        // Flush the assistant message that contained the tool_use
        // before adding tool_result (tool_results are role: "user").
        flushAssistant();
        if (canonicalAssistantTurnIds.has(entry.turnId)) {
          canonicalAssistantConsumedToolResult.add(entry.turnId);
        }
        // Truncate historical tool results to 2KB. Full results are
        // only needed in the CURRENT turn (which uses the in-memory
        // messages array, not transcript replay). Historical results
        // just need enough context for the LLM to follow the thread.
        const rawOutput = entry.output ?? "";
        const MAX_TOOL_RESULT_REPLAY = 2048;
        const content = rawOutput.length > MAX_TOOL_RESULT_REPLAY
          ? rawOutput.slice(0, MAX_TOOL_RESULT_REPLAY) + "\n...[truncated]"
          : rawOutput;
        pendingToolResults.push({
          type: "tool_result",
          tool_use_id: entry.toolUseId,
          content,
          ...(entry.isError ? { is_error: true } : {}),
        } as LLMContentBlock);
        break;
      }
      case "canonical_message": {
        const blocks = canonicalContentBlocks(entry.content);
        if (blocks.length === 0) break;
        if (entry.role === "assistant") {
          flushToolResults();
          pendingAssistantBlocks.push(...blocks);
        } else {
          flushAssistant();
          flushToolResults();
          messages.push({ role: "user", content: blocks });
        }
        break;
      }
      // Lifecycle markers (turn_started, turn_committed, turn_aborted)
      // are not converted to messages — they're structural only.
      default:
        break;
    }
  }

  // Flush any trailing blocks
  flushAssistant();
  flushToolResults();

  // ── Merge consecutive same-role messages ──────────────────────
  // Aborted turns write user_message + tool_call/tool_result to
  // transcript but NOT assistant_text (only written on commit).
  // This creates consecutive user messages:
  //   user("prev question")  ← from aborted turn, no assistant reply
  //   user("current question")
  // The model responds to the first, not the latest. legacy gateway's
  // validateAnthropicTurns() merges consecutive same-role messages
  // to fix this. We do the same.
  const merged = mergeConsecutiveSameRole(messages);
  return stripDanglingToolUses(merged);
}

/**
 * Remove tool_use blocks from assistant messages when no matching
 * tool_result exists in the immediately following user message.
 * The Anthropic API returns 400 for orphaned tool_use — this happens
 * when a turn was aborted mid-tool-execution (tool_call written to
 * transcript but tool_result never written).
 *
 * legacy gateway reference: `stripDanglingAnthropicToolUses()` in
 * `src/agents/pi-embedded-helpers/turns.ts`.
 */
function stripDanglingToolUses(messages: LLMMessage[]): LLMMessage[] {
  for (let i = 0; i < messages.length; i++) {
    const msg = messages[i]!;
    if (msg.role !== "assistant" || !Array.isArray(msg.content)) continue;

    const toolUseIds: string[] = [];
    const seenToolUseIds = new Set<string>();
    msg.content = (msg.content as LLMContentBlock[]).filter((block) => {
      if (block.type !== "tool_use" || !("id" in block)) return true;
      const id = (block as { id: string }).id;
      if (seenToolUseIds.has(id)) return false;
      seenToolUseIds.add(id);
      toolUseIds.push(id);
      return true;
    });
    if (toolUseIds.length === 0) continue;

    // Check if the next message begins with matching tool_results.
    // Anthropic rejects `[text, tool_result]` after an assistant
    // `tool_use`: the tool_result blocks must be immediately after.
    const next = messages[i + 1];
    const matchedIds = new Set<string>();
    if (next && next.role === "user" && Array.isArray(next.content)) {
      const nextBlocks = next.content as LLMContentBlock[];
      const leadingResultsById = new Map<string, LLMContentBlock>();
      let firstNonResult = 0;
      for (const block of nextBlocks) {
        if (block.type !== "tool_result") break;
        if ("tool_use_id" in block) {
          const id = (block as { tool_use_id: string }).tool_use_id;
          if (!leadingResultsById.has(id)) leadingResultsById.set(id, block);
        }
        firstNonResult += 1;
      }
      const orderedResults = toolUseIds.flatMap((id) => {
        const block = leadingResultsById.get(id);
        if (!block) return [];
        matchedIds.add(id);
        return [block];
      });
      next.content = [...orderedResults, ...nextBlocks.slice(firstNonResult)];
    }

    // Remove unmatched tool_use blocks
    const unmatched = toolUseIds.filter((id) => !matchedIds.has(id));
    if (unmatched.length === 0) continue;

    const unmatchedSet = new Set(unmatched);
    msg.content = (msg.content as LLMContentBlock[]).filter(
      (block) => !(block.type === "tool_use" && unmatchedSet.has((block as { id: string }).id)),
    );

    // If assistant message is now empty (only had orphaned tool_use), remove it
    if (msg.content.length === 0) {
      messages.splice(i, 1);
      i -= 1;
    }
  }

  // Also strip orphaned tool_result blocks (tool_result without preceding tool_use)
  for (let i = 0; i < messages.length; i++) {
    const msg = messages[i]!;
    if (msg.role !== "user" || !Array.isArray(msg.content)) continue;

    const hasToolResult = msg.content.some((b) => b.type === "tool_result");
    if (!hasToolResult) continue;

    // Collect tool_use IDs from the preceding assistant message
    const prev = messages[i - 1];
    const prevToolIds = new Set<string>();
    if (prev && prev.role === "assistant" && Array.isArray(prev.content)) {
      for (const block of prev.content) {
        if (block.type === "tool_use" && "id" in block) {
          prevToolIds.add((block as { id: string }).id);
        }
      }
    }

    // Remove tool_result blocks that don't match any tool_use, are not
    // at the leading edge of the user message, or duplicate a prior
    // historical result for the same tool_use id.
    const seenResultIds = new Set<string>();
    let stillLeading = true;
    msg.content = (msg.content as LLMContentBlock[]).filter((block) => {
      if (block.type !== "tool_result") {
        stillLeading = false;
        return true;
      }
      if (!stillLeading) return false;
      const id = (block as { tool_use_id: string }).tool_use_id;
      if (!prevToolIds.has(id)) return false;
      if (seenResultIds.has(id)) return false;
      seenResultIds.add(id);
      return true;
    });

    // If user message is now empty, remove it
    if (msg.content.length === 0) {
      messages.splice(i, 1);
      i -= 1;
    }
  }

  return messages;
}

/**
 * Merge adjacent messages with the same role into a single message.
 * Handles both string and content-block-array formats. Required by
 * the Anthropic API which demands strict user/assistant alternation.
 */
function mergeConsecutiveSameRole(messages: LLMMessage[]): LLMMessage[] {
  if (messages.length <= 1) return messages;
  const merged: LLMMessage[] = [messages[0]!];
  for (let i = 1; i < messages.length; i++) {
    const curr = messages[i]!;
    const prev = merged[merged.length - 1]!;
    if (curr.role !== prev.role) {
      merged.push(curr);
      continue;
    }
    // Same role — merge content into prev
    const prevBlocks = toContentBlocks(prev.content);
    const currBlocks = toContentBlocks(curr.content);
    prev.content = [...prevBlocks, ...currBlocks];
  }
  return merged;
}

/** Normalise message content to LLMContentBlock[]. */
function toContentBlocks(
  content: string | LLMContentBlock[],
): LLMContentBlock[] {
  if (typeof content === "string") {
    return [{ type: "text", text: content } as LLMContentBlock];
  }
  return content;
}

function canonicalContentBlocks(content: unknown[]): LLMContentBlock[] {
  const blocks: LLMContentBlock[] = [];
  const hasUserFacingText = content.some(
    (block) =>
      !!block &&
      typeof block === "object" &&
      (block as Record<string, unknown>).type === "text" &&
      typeof (block as Record<string, unknown>).text === "string",
  );
  for (const block of content) {
    if (!block || typeof block !== "object") continue;
    const obj = block as Record<string, unknown>;
    if (obj.type === "text" && typeof obj.text === "string") {
      blocks.push({ type: "text", text: obj.text });
      continue;
    }
    if (
      obj.type === "thinking" &&
      typeof obj.thinking === "string" &&
      typeof obj.signature === "string" &&
      !hasUserFacingText
    ) {
      blocks.push({
        type: "thinking",
        thinking: obj.thinking,
        signature: obj.signature,
      });
      continue;
    }
    if (obj.type === "thinking" || obj.type === "redacted_thinking") {
      continue;
    }
    if (
      obj.type === "tool_use" &&
      typeof obj.id === "string" &&
      typeof obj.name === "string"
    ) {
      blocks.push({
        type: "tool_use",
        id: obj.id,
        name: obj.name,
        input: obj.input,
      });
      continue;
    }
    if (obj.type === "tool_result" && typeof obj.tool_use_id === "string") {
      const result = canonicalToolResultBlock(obj);
      if (result) blocks.push(result);
    }
  }
  return blocks;
}

function canonicalToolResultBlock(
  obj: Record<string, unknown>,
): LLMContentBlock | null {
  const content = obj.content;
  if (typeof content === "string") {
    return {
      type: "tool_result",
      tool_use_id: obj.tool_use_id as string,
      content,
      ...(typeof obj.is_error === "boolean" ? { is_error: obj.is_error } : {}),
    };
  }
  if (!Array.isArray(content)) return null;
  const textBlocks = content
    .map((item): { type: "text"; text: string } | null => {
      if (!item || typeof item !== "object") return null;
      const block = item as Record<string, unknown>;
      if (block.type !== "text" || typeof block.text !== "string") return null;
      return { type: "text", text: block.text };
    })
    .filter((item): item is { type: "text"; text: string } => item !== null);
  return {
    type: "tool_result",
    tool_use_id: obj.tool_use_id as string,
    content: textBlocks,
    ...(typeof obj.is_error === "boolean" ? { is_error: obj.is_error } : {}),
  };
}

function estimateTranscriptTokens(entries: readonly TranscriptEntry[]): number {
  let total = 0;
  for (const entry of entries) {
    if (entry.kind === "user_message" || entry.kind === "assistant_text") {
      total += estimateTextTokens(entry.text);
    } else if (entry.kind === "tool_call") {
      total += estimateTextTokens(JSON.stringify(entry.input ?? {}));
    } else if (entry.kind === "tool_result") {
      total += estimateTextTokens(entry.output ?? "");
    } else if (entry.kind === "canonical_message") {
      total += estimateTextTokens(JSON.stringify(entry.content));
    } else if (isCompactionBoundary(entry)) {
      total += entry.afterTokenCount;
    }
  }
  return total;
}

/**
 * Cheap char-based token estimate (~4 chars/token). Replaces a tiktoken
 * dependency for the threshold check — exact count is unnecessary
 * because `tokenLimit` is itself heuristic.
 */
function estimateTextTokens(text: string): number {
  if (text.length === 0) return 0;
  return Math.ceil(text.length / 4);
}

function renderEntriesForSummary(entries: readonly TranscriptEntry[]): string {
  const lines: string[] = [];
  for (const entry of entries) {
    if (entry.kind === "user_message") {
      lines.push(`USER: ${entry.text}`);
    } else if (entry.kind === "assistant_text") {
      lines.push(`ASSISTANT: ${entry.text}`);
    } else if (entry.kind === "tool_call") {
      lines.push(`TOOL_CALL ${entry.name}: ${JSON.stringify(entry.input ?? {})}`);
    } else if (entry.kind === "tool_result") {
      lines.push(`TOOL_RESULT ${entry.status}: ${entry.output ?? ""}`);
    } else if (isCompactionBoundary(entry)) {
      lines.push(`PRIOR_BOUNDARY ${entry.boundaryId}: ${entry.summaryText}`);
    }
  }
  return lines.join("\n");
}

function sha256Hex(text: string): string {
  return crypto.createHash("sha256").update(text).digest("hex");
}
