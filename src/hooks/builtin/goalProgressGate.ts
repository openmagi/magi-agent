/**
 * Goal progress gate — beforeCommit, priority 84.
 *
 * Covers a reliability gap not handled by completion/deferral gates:
 * a goal-oriented request can fail one tool action and then stop, or
 * claim investigation/action happened without any tool evidence. The
 * classifier is LLM-based for multilingual/general detection; this
 * hook uses deterministic transcript evidence to decide whether to
 * retry the turn.
 */

import type { RegisteredHook, HookContext } from "../types.js";
import type { TranscriptEntry } from "../../storage/Transcript.js";
import {
  getOrClassifyFinalAnswerMeta,
  getOrClassifyRequestMeta,
} from "./turnMetaClassifier.js";

const MAX_RETRIES = 1;
const MIN_FAILURES_BEFORE_HARD_BLOCKER = 2;

export interface GoalProgressGateAgent {
  readSessionTranscript(
    sessionKey: string,
  ): Promise<ReadonlyArray<TranscriptEntry> | null>;
}

export interface GoalProgressGateOptions {
  agent?: GoalProgressGateAgent;
}

function isEnabled(): boolean {
  const raw = process.env.MAGI_GOAL_PROGRESS_GATE;
  if (raw === undefined || raw === null) return true;
  const v = raw.trim().toLowerCase();
  return v === "" || v === "on" || v === "true" || v === "1";
}

export function countToolCallsThisTurn(
  transcript: ReadonlyArray<{ kind: string; turnId?: string }>,
  turnId: string,
): number {
  let n = 0;
  for (const entry of transcript) {
    if (entry.kind === "tool_call" && entry.turnId === turnId) n += 1;
  }
  return n;
}

export function countFailedToolResultsThisTurn(
  transcript: ReadonlyArray<{
    kind: string;
    turnId?: string;
    status?: string;
    isError?: boolean;
  }>,
  turnId: string,
): number {
  let n = 0;
  for (const entry of transcript) {
    if (entry.kind !== "tool_result" || entry.turnId !== turnId) continue;
    if (entry.isError === true || entry.status === "error") n += 1;
  }
  return n;
}

export function countSuccessfulToolResultsThisTurn(
  transcript: ReadonlyArray<{
    kind: string;
    turnId?: string;
    status?: string;
    isError?: boolean;
  }>,
  turnId: string,
): number {
  let n = 0;
  for (const entry of transcript) {
    if (entry.kind !== "tool_result" || entry.turnId !== turnId) continue;
    if (entry.isError !== true && entry.status === "ok") n += 1;
  }
  return n;
}

async function readTranscript(
  ctx: HookContext,
  agent?: GoalProgressGateAgent,
): Promise<ReadonlyArray<TranscriptEntry>> {
  if (!agent) return ctx.transcript as ReadonlyArray<TranscriptEntry>;
  try {
    const entries = await agent.readSessionTranscript(ctx.sessionKey);
    return entries ?? (ctx.transcript as ReadonlyArray<TranscriptEntry>);
  } catch (err) {
    ctx.log("warn", "[goal-progress-gate] transcript read failed", {
      error: err instanceof Error ? err.message : String(err),
    });
    return ctx.transcript as ReadonlyArray<TranscriptEntry>;
  }
}

export function makeGoalProgressGateHook(
  opts: GoalProgressGateOptions = {},
): RegisteredHook<"beforeCommit"> {
  return {
    name: "builtin:goal-progress-gate",
    point: "beforeCommit",
    // After output-delivery (87) would make retries less clear; before
    // resource/completion gates so the draft is redirected while the
    // problem is still "keep trying / provide evidence".
    priority: 84,
    blocking: true,
    failOpen: true,
    handler: async ({ assistantText, retryCount, userMessage }, ctx: HookContext) => {
      try {
        if (!isEnabled()) return { action: "continue" };
        if (!assistantText || assistantText.trim().length === 0) {
          return { action: "continue" };
        }

        const requestMeta = await getOrClassifyRequestMeta(ctx, { userMessage });
        const requestRequiresAction = requestMeta.goalProgress.requiresAction;
        const finalMeta = await getOrClassifyFinalAnswerMeta(ctx, {
          userMessage,
          assistantText,
        });
        const finalRequiresEvidence =
          finalMeta.assistantClaimsActionWithoutEvidence ||
          finalMeta.assistantEndsWithUnexecutedPlan ||
          finalMeta.assistantNeedsMoreRuntimeWork ||
          (requestRequiresAction && finalMeta.assistantGivesUpEarly);
        if (!requestRequiresAction && !finalRequiresEvidence) {
          return { action: "continue" };
        }
        if (
          !finalMeta.assistantGivesUpEarly &&
          !finalMeta.assistantClaimsActionWithoutEvidence &&
          !finalMeta.assistantEndsWithUnexecutedPlan &&
          !finalMeta.assistantNeedsMoreRuntimeWork
        ) {
          return { action: "continue" };
        }

        const transcript = await readTranscript(ctx, opts.agent);
        const toolCalls = countToolCallsThisTurn(transcript, ctx.turnId);
        const failedResults = countFailedToolResultsThisTurn(transcript, ctx.turnId);
        const successfulResults = countSuccessfulToolResultsThisTurn(transcript, ctx.turnId);

        if (finalMeta.assistantClaimsActionWithoutEvidence && toolCalls === 0) {
          ctx.log("warn", "[goal-progress-gate] blocking action claim without tool evidence", {
            reason: finalMeta.reason,
          });
          ctx.emit({
            type: "rule_check",
            ruleId: "goal-progress-gate",
            verdict: "violation",
            detail: "assistant claimed concrete action with no current-turn tool calls",
          });
          return {
            action: "block",
            reason: [
              "[RETRY:GOAL_PROGRESS_ACTION_EVIDENCE]",
              "The user asked for concrete work, and the draft claims you already",
              "checked/debugged/tried/performed actions, but this turn has no",
              "tool-call evidence. Do not narrate actions you did not perform.",
              "Either call the necessary tools now and report the actual evidence,",
              "or answer without claiming completed actions.",
            ].join("\n"),
          };
        }

        if (
          finalMeta.assistantEndsWithUnexecutedPlan ||
          finalMeta.assistantNeedsMoreRuntimeWork
        ) {
          ctx.log("warn", "[goal-progress-gate] blocking plan-only turn ending", {
            toolCalls,
            failedResults,
            successfulResults,
            retryCount,
            reason: finalMeta.reason,
          });
          ctx.emit({
            type: "rule_check",
            ruleId: "goal-progress-gate",
            verdict: "violation",
            detail: `runtime work still needed; toolCalls=${toolCalls} failedResults=${failedResults} successfulResults=${successfulResults}`,
          });
          return {
            action: "block",
            reason: [
              "[RETRY:GOAL_PROGRESS_EXECUTE_NEXT]",
              "The user asked for concrete goal progress, and the draft ends",
              "with more runtime work still needed instead of executing the next",
              "needed action and returning the result.",
              "Do not end the turn at the planning boundary. Continue now:",
              "- Call the next required tool/subagent/action in this turn.",
              "- Use the resulting evidence to synthesize the requested output.",
              "- If the work cannot proceed, report the concrete hard blocker",
              "  with evidence rather than promising future work.",
            ].join("\n"),
          };
        }

        if (retryCount >= MAX_RETRIES) {
          ctx.log("warn", "[goal-progress-gate] retry budget exhausted; failing open", {
            retryCount,
            reason: finalMeta.reason,
          });
          ctx.emit({
            type: "rule_check",
            ruleId: "goal-progress-gate",
            verdict: "violation",
            detail: "retry exhausted; failing open",
          });
          return { action: "continue" };
        }

        if (
          finalMeta.assistantGivesUpEarly &&
          (toolCalls === 0 ||
            (failedResults > 0 && failedResults < MIN_FAILURES_BEFORE_HARD_BLOCKER))
        ) {
          ctx.log("warn", "[goal-progress-gate] blocking early give-up without enough attempts", {
            toolCalls,
            failedResults,
            successfulResults,
            reason: finalMeta.reason,
          });
          ctx.emit({
            type: "rule_check",
            ruleId: "goal-progress-gate",
            verdict: "violation",
            detail: `early give-up; toolCalls=${toolCalls} failedResults=${failedResults} successfulResults=${successfulResults}`,
          });
          return {
            action: "block",
            reason: [
              "[RETRY:GOAL_PROGRESS_REQUIRED]",
              "The user asked for goal progress. You hit a recoverable tool failure",
              "and the draft stops early or asks the user to choose next steps.",
              "Do not treat one failed attempt as terminal. Continue agentically:",
              "- Re-check the current state if needed.",
              "- Try a different tool path, selector, command, or input strategy.",
              "- If you still cannot complete it after concrete attempts, report",
              "  the hard blocker with the exact evidence and what was tried.",
              "Remove premature give-up wording and continue the work in this turn.",
            ].join("\n"),
          };
        }

        return { action: "continue" };
      } catch (err) {
        ctx.log("warn", "[goal-progress-gate] failed; commit continues", {
          error: err instanceof Error ? err.message : String(err),
        });
        return { action: "continue" };
      }
    },
  };
}

export const goalProgressGateHook = makeGoalProgressGateHook();
