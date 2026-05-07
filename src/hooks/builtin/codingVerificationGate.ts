import type { Discipline } from "../../Session.js";
import type { TranscriptEntry } from "../../storage/Transcript.js";
import {
  classifyEvidence,
  transcriptEvidenceForTurn,
} from "../../verification/VerificationEvidence.js";
import type { HookContext, RegisteredHook } from "../types.js";

export interface CodingVerificationGateAgent {
  getSessionDiscipline(sessionKey: string): Discipline | null;
  readSessionTranscript(
    sessionKey: string,
  ): Promise<ReadonlyArray<TranscriptEntry> | null>;
}

export interface CodingVerificationGateOptions {
  agent?: CodingVerificationGateAgent;
}

function isEnabled(): boolean {
  const raw = process.env.CORE_AGENT_CODING_VERIFY;
  if (raw === undefined || raw === null) return true;
  const v = raw.trim().toLowerCase();
  return v === "" || v === "on" || v === "true" || v === "1";
}

function explicitlyUnverified(text: string): boolean {
  return /\b(?:not verified|unverified|could not verify|did not run|tests? failed|build failed)\b/i.test(text) ||
    /(?:검증|테스트|빌드|확인).{0,16}(?:못|않|안\s*했|실패|불가|미실행)|미검증/u.test(text);
}

async function readTranscript(
  opts: CodingVerificationGateOptions,
  ctx: HookContext,
): Promise<ReadonlyArray<TranscriptEntry>> {
  if (!opts.agent) return ctx.transcript;
  try {
    return (await opts.agent.readSessionTranscript(ctx.sessionKey)) ?? ctx.transcript;
  } catch (err) {
    ctx.log("warn", "[coding-verification-gate] transcript read failed", {
      error: err instanceof Error ? err.message : String(err),
    });
    return ctx.transcript;
  }
}

function isCodingMode(discipline: Discipline | null): boolean {
  return discipline?.lastClassifiedMode === "coding";
}

export function makeCodingVerificationGateHook(
  opts: CodingVerificationGateOptions = {},
): RegisteredHook<"beforeCommit"> {
  return {
    name: "builtin:coding-verification-gate",
    point: "beforeCommit",
    priority: 88,
    blocking: true,
    timeoutMs: 2_000,
    handler: async ({ assistantText, filesChanged }, ctx: HookContext) => {
      if (!isEnabled()) return { action: "continue" };
      const discipline = opts.agent?.getSessionDiscipline(ctx.sessionKey) ?? null;
      if (!isCodingMode(discipline)) return { action: "continue" };
      if (!filesChanged || filesChanged.length === 0) return { action: "continue" };
      if (explicitlyUnverified(assistantText)) return { action: "continue" };

      const transcript = await readTranscript(opts, ctx);
      const evidence = transcriptEvidenceForTurn(transcript, ctx.turnId);
      const classified = classifyEvidence(evidence);
      if (classified.verification) {
        ctx.emit({
          type: "rule_check",
          ruleId: "coding-verification-gate",
          verdict: "ok",
          detail: `coding changes verified by ${classified.tools.join(", ")}`,
        });
        return { action: "continue" };
      }

      ctx.emit({
        type: "rule_check",
        ruleId: "coding-verification-gate",
        verdict: "violation",
        detail: "coding files changed without current-turn verification evidence",
      });
      return {
        action: "block",
        reason: [
          "[RETRY:CODING_VERIFICATION] Code files changed in this turn, but no current-turn verification evidence was found.",
          "Run TestRun with the relevant test/build/lint/typecheck command before claiming completion.",
          "If verification cannot be run, say that explicitly and report the remaining risk instead of claiming the work is complete.",
        ].join("\n"),
      };
    },
  };
}
