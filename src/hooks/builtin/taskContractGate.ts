/**
 * Task contract gate.
 *
 * Enforces the reliability-relevant part of prompt-level task
 * contracts: a `verification_mode=full` contract cannot be closed with
 * sample-only language or without same-turn evidence.
 */

import type { HookContext, RegisteredHook } from "../types.js";
import type { TranscriptEntry } from "../../storage/Transcript.js";
import type { DebugWorkflow } from "../../debug/DebugWorkflow.js";
import {
  hasFreshCompletionEvidence,
  type CompletionEvidenceAgent,
} from "./completionEvidenceGate.js";

const MAX_RETRIES = 1;

export type VerificationMode = "full" | "sample" | "none";

const SAMPLE_ONLY_RE = /(?:샘플|일부|대표|몇\s*개|spot[- ]?check|sample(?:d| only)?|partial(?:ly)?)/i;
const FULL_CLAIM_RE = /(?:전체|전부|모두|full|exhaustive|complete).{0,24}(?:검증|확인|verified|checked)/i;

export interface TaskContractGateOptions {
  agent?: CompletionEvidenceAgent;
  debugWorkflow?: DebugWorkflow;
}

function isEnabled(): boolean {
  const raw = process.env.CORE_AGENT_TASK_CONTRACT_GATE;
  if (raw === undefined || raw === null) return true;
  const v = raw.trim().toLowerCase();
  return v === "" || v === "on" || v === "true" || v === "1";
}

export function extractVerificationMode(text: string): VerificationMode | null {
  if (!text) return null;
  const xml = text.match(/<verification_mode>\s*([^<\s]+)\s*<\/verification_mode>/i);
  const raw = xml?.[1] ?? text.match(/verification_mode\s*[:=]\s*["']?([a-z]+)/i)?.[1];
  if (!raw) return null;
  const normalized = raw.trim().toLowerCase();
  if (normalized === "full") return "full";
  if (normalized === "sample") return "sample";
  if (normalized === "none") return "none";
  return null;
}

async function readTranscript(
  opts: TaskContractGateOptions,
  ctx: HookContext,
): Promise<ReadonlyArray<TranscriptEntry>> {
  if (!opts.agent) return ctx.transcript as ReadonlyArray<TranscriptEntry>;
  try {
    const entries = await opts.agent.readSessionTranscript(ctx.sessionKey);
    return entries ?? (ctx.transcript as ReadonlyArray<TranscriptEntry>);
  } catch (err) {
    ctx.log("warn", "[task-contract-gate] transcript read failed", {
      error: err instanceof Error ? err.message : String(err),
    });
    return ctx.transcript as ReadonlyArray<TranscriptEntry>;
  }
}

function shouldGateFullContract(userMessage: string, assistantText: string): boolean {
  if (extractVerificationMode(userMessage) !== "full") return false;
  if (SAMPLE_ONLY_RE.test(assistantText)) return true;
  return FULL_CLAIM_RE.test(assistantText);
}

export function makeTaskContractGateHook(
  opts: TaskContractGateOptions = {},
): RegisteredHook<"beforeCommit"> {
  return {
    name: "builtin:task-contract-gate",
    point: "beforeCommit",
    priority: 88,
    blocking: true,
    timeoutMs: 2_000,
    handler: async ({ assistantText, userMessage, retryCount }, ctx: HookContext) => {
      try {
        if (!isEnabled()) return { action: "continue" };
        if (!shouldGateFullContract(userMessage, assistantText)) {
          return { action: "continue" };
        }

        const debugState =
          opts.debugWorkflow?.getTurnState(ctx.sessionKey, ctx.turnId) ??
          ctx.debugWorkflow?.getTurnState(ctx.sessionKey, ctx.turnId) ??
          null;
        if (debugState?.classified && (!debugState.investigated || !debugState.verified)) {
          if (retryCount >= MAX_RETRIES) {
            ctx.log("warn", "[task-contract-gate] debug retry exhausted; failing open", {
              retryCount,
            });
            return { action: "continue" };
          }
          return {
            action: "block",
            reason: [
              "[RETRY:TASK_CONTRACT_VERIFY] Debug turns under a full verification contract need both investigation and verification checkpoints.",
              "Do not close the task until you have inspected the failure and re-run the relevant exhaustive check.",
            ].join("\n"),
          };
        }

        const transcript = await readTranscript(opts, ctx);
        if (!SAMPLE_ONLY_RE.test(assistantText) && hasFreshCompletionEvidence(transcript, ctx.turnId)) {
          return { action: "continue" };
        }

        if (retryCount >= MAX_RETRIES) {
          ctx.log("warn", "[task-contract-gate] retry exhausted; failing open", {
            retryCount,
          });
          return { action: "continue" };
        }

        ctx.emit({
          type: "rule_check",
          ruleId: "task-contract-gate",
          verdict: "violation",
          detail: "full verification contract not satisfied by current draft/evidence",
        });
        return {
          action: "block",
          reason: [
            "[RETRY:TASK_CONTRACT_VERIFY] The active task contract requires full verification.",
            "Do not close a full-verification task with sample-only language or without same-turn verification evidence.",
            "Run the exhaustive check, or explicitly downgrade the result as unverified/partial if exhaustive verification is impossible.",
          ].join("\n"),
        };
      } catch (err) {
        ctx.log("warn", "[task-contract-gate] failed; commit continues", {
          error: err instanceof Error ? err.message : String(err),
        });
        return { action: "continue" };
      }
    },
  };
}

export const taskContractGateHook = makeTaskContractGateHook();
