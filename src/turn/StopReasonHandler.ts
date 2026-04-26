/**
 * StopReasonHandler — the decision table over Anthropic stop_reason.
 *
 * Extracted from `Turn.execute` (R3 refactor, 2026-04-19). Owns:
 *   • Normalised `StopReasonCase` taxonomy (T1-05)
 *   • Output-token recovery (T1-04) with
 *     `MAX_OUTPUT_TOKENS_RECOVERY_LIMIT = 3`
 *   • Codex gate2 P2 fix — drop unresolved tool_use blocks before
 *     issuing a "Continue." recovery nudge
 *   • Refusal → `rule_check_violation` audit
 *   • Unknown → warn + `stop_reason_unknown` audit, finalise as
 *     end_turn
 *
 * Returns a discriminated union: the execute() loop just translates
 * the decision into return / break / continue — no policy lives in
 * the loop itself.
 */

import type { LLMContentBlock, LLMMessage } from "../transport/LLMClient.js";

/**
 * Normalised taxonomy of Anthropic stop_reason values (T1-05). The
 * raw wire value is cast into one of these cases by
 * {@link classifyStopReason} so the turn loop branches on an
 * exhaustive set rather than the binary `tool_use vs else`.
 */
export type StopReasonCase =
  | "end_turn"
  | "tool_use"
  | "stop_sequence"
  | "max_tokens"
  | "refusal"
  | "pause_turn"
  | "unknown";

/**
 * Maximum number of output-token recovery continuations per turn
 * (T1-04). Mirrors Claude Code's own bounded loop so a misbehaving
 * model can't loop on max_tokens forever.
 */
export const MAX_OUTPUT_TOKENS_RECOVERY_LIMIT = 3;

export function classifyStopReason(
  raw: string | null | undefined,
): StopReasonCase {
  switch (raw) {
    case "end_turn":
    case "tool_use":
    case "stop_sequence":
    case "max_tokens":
    case "refusal":
    case "pause_turn":
      return raw;
    default:
      return "unknown";
  }
}

export type StopReasonRaw =
  | "end_turn"
  | "tool_use"
  | "max_tokens"
  | "stop_sequence"
  | "refusal"
  | "pause_turn"
  | null;

/**
 * Decision outcomes for the outer turn loop.
 *   - `finalise`        → fall out of the loop, commit path takes over.
 *   - `run_tools`       → outer loop should execute the tool_use blocks
 *                         and continue to the next iteration.
 *   - `recover`         → a "Continue." recovery has been staged: the
 *                         handler has already mutated `messages` and
 *                         bumped `recoveryAttempt`. The outer loop
 *                         should rewind iter by one and continue.
 *   - `interrupted`     → mid-turn injection aborted the stream.
 *                         Partial blocks are pushed as assistant msg.
 *                         Next iteration's beforeLLMCall drains the
 *                         injection queue.
 */
export type StopReasonDecision =
  | { kind: "finalise" }
  | {
      kind: "run_tools";
      toolUses: Array<Extract<LLMContentBlock, { type: "tool_use" }>>;
    }
  | { kind: "recover" };

export interface StopReasonHandlerState {
  /** Current recovery attempt count; may be mutated by the handler. */
  recoveryAttempt: number;
  /** Aggregated text length so far (for exhausted audit payload). */
  assistantTextSoFarLen: number;
}

export interface StopReasonHandlerDeps {
  /** Fire-and-forget audit event. */
  readonly stageAuditEvent: (event: string, data?: Record<string, unknown>) => void;
  /** Logger for the `unknown` case — Turn uses `console.warn`. */
  readonly logUnknown: (raw: string | null | undefined, turnId: string) => void;
}

export interface HandleArgs {
  readonly stopReasonRaw: StopReasonRaw;
  readonly blocks: LLMContentBlock[];
  readonly iter: number;
  readonly turnId: string;
  /**
   * The live messages array. The handler mutates it in place for the
   * recovery path (append assistant blocks + "Continue." user msg).
   */
  readonly messages: LLMMessage[];
}

/**
 * Decide what the outer execute() loop should do next.
 *
 * State mutation contract:
 *   • On `recover`, `state.recoveryAttempt` is incremented and
 *     `messages` is appended to. Caller must rewind iter.
 *   • On other kinds, state/messages are untouched.
 */
export function handle(
  deps: StopReasonHandlerDeps,
  state: StopReasonHandlerState,
  args: HandleArgs,
): StopReasonDecision {
  const { stopReasonRaw, blocks, iter, turnId, messages } = args;
  const stopCase = classifyStopReason(stopReasonRaw);

  switch (stopCase) {
    case "end_turn":
    case "stop_sequence":
      return { kind: "finalise" };

    case "refusal":
      // Stage an audit event so downstream compliance pipelines see
      // the model refused, then finalise with the refusal text
      // visible. The audit event is the ground truth.
      deps.stageAuditEvent("rule_check_violation", {
        reason: "model_refusal",
        stop_reason: stopReasonRaw,
        iteration: iter,
      });
      return { kind: "finalise" };

    case "unknown":
      // Log + stage audit, finalise as end_turn.
      deps.logUnknown(stopReasonRaw, turnId);
      deps.stageAuditEvent("stop_reason_unknown", {
        raw: stopReasonRaw,
        iteration: iter,
      });
      return { kind: "finalise" };

    case "max_tokens":
    case "pause_turn": {
      // T1-04 — Output-token recovery. Append the assistant's partial
      // content and a "Continue." nudge, bump the counter. `pause_turn`
      // shares the same budget.
      if (state.recoveryAttempt >= MAX_OUTPUT_TOKENS_RECOVERY_LIMIT) {
        deps.stageAuditEvent("output_recovery_exhausted", {
          finalLength: state.assistantTextSoFarLen,
          limit: MAX_OUTPUT_TOKENS_RECOVERY_LIMIT,
          stop_reason: stopReasonRaw,
        });
        return { kind: "finalise" };
      }
      // [codex gate2 P2] Drop unresolved tool_use blocks — Anthropic
      // /v1/messages rejects an assistant tool_use that isn't followed
      // by a matching tool_result.
      const toolUseBlockCount = blocks.filter(
        (b) => b.type === "tool_use",
      ).length;
      const filteredBlocks = blocks.filter((b) => b.type !== "tool_use");
      if (toolUseBlockCount > 0) {
        deps.stageAuditEvent("output_recovery_drop_unresolved_tool_use", {
          dropped: toolUseBlockCount,
          iter,
          recoveryAttempt: state.recoveryAttempt,
        });
      }
      if (filteredBlocks.length > 0) {
        messages.push({ role: "assistant", content: filteredBlocks });
      }
      messages.push({ role: "user", content: "Continue." });
      state.recoveryAttempt += 1;
      deps.stageAuditEvent("output_recovery", {
        iteration: iter,
        recoveryAttempt: state.recoveryAttempt,
        stop_reason: stopReasonRaw,
      });
      return { kind: "recover" };
    }

    case "tool_use": {
      const toolUses = blocks.filter(
        (b): b is Extract<LLMContentBlock, { type: "tool_use" }> =>
          b.type === "tool_use",
      );
      if (toolUses.length === 0) {
        // Defensive: stop_reason says tool_use but no blocks — bail.
        return { kind: "finalise" };
      }
      return { kind: "run_tools", toolUses };
    }
  }
}
