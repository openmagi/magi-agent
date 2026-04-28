/**
 * Completion evidence gate.
 *
 * Prevents "done/fixed/passing" claims from being committed unless the
 * current turn transcript contains successful work/verification evidence.
 * This ports the reliable part of verification-before-completion into
 * a deterministic runtime gate.
 */

import type { HookContext, RegisteredHook } from "../types.js";
import type { TranscriptEntry } from "../../storage/Transcript.js";
import type { DebugWorkflow } from "../../debug/DebugWorkflow.js";
import {
  classifyEvidence,
  matchesCompletionClaim,
  shouldBlockClaim,
  transcriptEvidenceForTurn,
} from "../../verification/VerificationEvidence.js";

export { matchesCompletionClaim };

const MAX_RETRIES = 1;

export interface CompletionEvidenceAgent {
  readSessionTranscript(
    sessionKey: string,
  ): Promise<ReadonlyArray<TranscriptEntry> | null>;
}

export interface CompletionEvidenceGateOptions {
  agent?: CompletionEvidenceAgent;
  debugWorkflow?: DebugWorkflow;
}

function isEnabled(): boolean {
  const raw = process.env.CORE_AGENT_COMPLETION_EVIDENCE;
  if (raw === undefined || raw === null) return true;
  const v = raw.trim().toLowerCase();
  return v === "" || v === "on" || v === "true" || v === "1";
}

export function hasFreshCompletionEvidence(
  transcript: ReadonlyArray<TranscriptEntry>,
  turnId: string,
): boolean {
  return classifyEvidence(transcriptEvidenceForTurn(transcript, turnId)).verification;
}

async function readTranscript(
  opts: CompletionEvidenceGateOptions,
  ctx: HookContext,
): Promise<ReadonlyArray<TranscriptEntry>> {
  if (!opts.agent) return ctx.transcript as ReadonlyArray<TranscriptEntry>;
  try {
    const entries = await opts.agent.readSessionTranscript(ctx.sessionKey);
    return entries ?? (ctx.transcript as ReadonlyArray<TranscriptEntry>);
  } catch (err) {
    ctx.log("warn", "[completion-evidence-gate] transcript read failed", {
      error: err instanceof Error ? err.message : String(err),
    });
    return ctx.transcript as ReadonlyArray<TranscriptEntry>;
  }
}

export function makeCompletionEvidenceGateHook(
  opts: CompletionEvidenceGateOptions = {},
): RegisteredHook<"beforeCommit"> {
  return {
    name: "builtin:completion-evidence-gate",
    point: "beforeCommit",
    priority: 87,
    blocking: true,
    timeoutMs: 2_000,
    handler: async ({ assistantText, retryCount }, ctx: HookContext) => {
      try {
        if (!isEnabled()) return { action: "continue" };
        if (!matchesCompletionClaim(assistantText)) return { action: "continue" };

        const debugState =
          opts.debugWorkflow?.getTurnState(ctx.sessionKey, ctx.turnId) ??
          ctx.debugWorkflow?.getTurnState(ctx.sessionKey, ctx.turnId) ??
          null;
        if (debugState?.classified && (!debugState.investigated || !debugState.verified)) {
          if (retryCount >= MAX_RETRIES) {
            ctx.log("warn", "[completion-evidence-gate] debug retry exhausted; failing open", {
              retryCount,
            });
            return { action: "continue" };
          }
          return {
            action: "block",
            reason: [
              "[RETRY:COMPLETION_EVIDENCE] This is a debug/fix turn, so a fix claim needs both investigation and verification state.",
              "Inspect first, patch second, verify last. Do not claim it is fixed until the debug workflow shows both investigation and verification checkpoints.",
            ].join("\n"),
          };
        }

        const transcript = await readTranscript(opts, ctx);
        const evidence = transcriptEvidenceForTurn(transcript, ctx.turnId);
        if (!shouldBlockClaim(assistantText, evidence)) {
          const classification = classifyEvidence(evidence);
          ctx.emit({
            type: "rule_check",
            ruleId: "completion-evidence-gate",
            verdict: "ok",
            detail: classification.verification
              ? "completion claim has same-turn verification evidence"
              : "completion claim explicitly reports unverified or partial status",
          });
          return { action: "continue" };
        }

        if (retryCount >= MAX_RETRIES) {
          ctx.log("warn", "[completion-evidence-gate] retry exhausted; failing open", {
            retryCount,
          });
          return { action: "continue" };
        }

        ctx.emit({
          type: "rule_check",
          ruleId: "completion-evidence-gate",
          verdict: "violation",
          detail: `completion claim without same-turn evidence; retryCount=${retryCount}`,
        });
        return {
          action: "block",
          reason: [
            "[RETRY:COMPLETION_EVIDENCE] You are claiming the work is complete, fixed, passing, or verified,",
            "but the current turn has no successful tool evidence for that claim.",
            "",
            "Before finalising:",
            "1) Run the relevant verification command or inspect the changed artifact.",
            "2) If verification is not possible, say that explicitly instead of claiming success.",
            "3) Re-draft the final answer with the actual evidence and any remaining risk.",
          ].join("\n"),
        };
      } catch (err) {
        ctx.log("warn", "[completion-evidence-gate] failed; commit continues", {
          error: err instanceof Error ? err.message : String(err),
        });
        return { action: "continue" };
      }
    },
  };
}

export const completionEvidenceGateHook = makeCompletionEvidenceGateHook();
