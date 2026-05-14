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
  const raw = process.env.MAGI_CODING_VERIFY;
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

function hasCurrentTurnDiffEvidence(evidence: ReturnType<typeof transcriptEvidenceForTurn>): boolean {
  return evidence.some(
    (item) =>
      item.tool === "GitDiff" &&
      item.isError !== true &&
      (item.status === undefined || item.status === "ok" || item.status === "success"),
  );
}

const CODE_MUTATION_TOOLS = new Set(["FileWrite", "FileEdit", "SpawnWorktreeApply"]);

type ToolResultEntry = Extract<TranscriptEntry, { kind: "tool_result" }>;

interface ToolResultRecord {
  entry: ToolResultEntry;
  index: number;
}

function currentTurnResultMap(
  transcript: ReadonlyArray<TranscriptEntry>,
  turnId: string,
): Map<string, ToolResultRecord> {
  const results = new Map<string, ToolResultRecord>();
  for (const [index, entry] of transcript.entries()) {
    if (entry.kind === "tool_result" && entry.turnId === turnId) {
      results.set(entry.toolUseId, { entry, index });
    }
  }
  return results;
}

function latestCodeMutationIndex(
  transcript: ReadonlyArray<TranscriptEntry>,
  turnId: string,
): number | null {
  const results = currentTurnResultMap(transcript, turnId);
  let latest: number | null = null;
  for (const [index, entry] of transcript.entries()) {
    if (entry.kind !== "tool_call" || entry.turnId !== turnId) continue;
    if (!CODE_MUTATION_TOOLS.has(entry.name)) continue;
    const result = results.get(entry.toolUseId);
    if (!isCodeMutationResult(entry.name, result?.entry)) continue;
    const mutationIndex = result?.index ?? index;
    latest = latest === null ? mutationIndex : Math.max(latest, mutationIndex);
  }
  return latest;
}

function isCodeMutationResult(
  toolName: string,
  result: ToolResultEntry | undefined,
): boolean {
  if (toolName === "FileWrite" || toolName === "FileEdit") return true;
  if (toolName !== "SpawnWorktreeApply") return false;
  if (!result || result.isError === true) return false;
  if (result.status && result.status !== "ok" && result.status !== "success") return false;
  const output = parseToolOutputObject(result.output);
  return output?.applied === true;
}

function parseToolOutputObject(output: unknown): Record<string, unknown> | null {
  if (!output) return null;
  if (typeof output === "object" && !Array.isArray(output)) {
    return output as Record<string, unknown>;
  }
  if (typeof output !== "string") return null;
  try {
    const parsed = JSON.parse(output) as unknown;
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? parsed as Record<string, unknown>
      : null;
  } catch {
    return null;
  }
}

function transcriptEvidenceForTurnAfter(
  transcript: ReadonlyArray<TranscriptEntry>,
  turnId: string,
  afterIndex: number,
): ReturnType<typeof transcriptEvidenceForTurn> {
  const results = currentTurnResultMap(transcript, turnId);
  const out: ReturnType<typeof transcriptEvidenceForTurn> = [];
  for (const [index, entry] of transcript.entries()) {
    if (entry.kind !== "tool_call" || entry.turnId !== turnId) continue;
    if (index <= afterIndex) continue;
    const result = results.get(entry.toolUseId);
    if (!result || result.index <= afterIndex) continue;
    out.push({
      tool: entry.name,
      input: entry.input,
      status: result.entry.status,
      output: result.entry.output,
      isError: result.entry.isError,
      metadata: result.entry.metadata,
    });
  }
  return out;
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
      const latestMutationIndex = latestCodeMutationIndex(transcript, ctx.turnId);
      const evidence = latestMutationIndex === null
        ? transcriptEvidenceForTurn(transcript, ctx.turnId)
        : transcriptEvidenceForTurnAfter(transcript, ctx.turnId, latestMutationIndex);
      const classified = classifyEvidence(evidence);
      const hasDiff = hasCurrentTurnDiffEvidence(evidence);
      if (classified.verification && hasDiff) {
        ctx.emit({
          type: "rule_check",
          ruleId: "coding-verification-gate",
          verdict: "ok",
          detail: `coding changes verified by ${classified.tools.join(", ")} with GitDiff evidence`,
        });
        return { action: "continue" };
      }

      ctx.emit({
        type: "rule_check",
        ruleId: "coding-verification-gate",
        verdict: "violation",
        detail: latestMutationIndex === null
          ? "coding files changed without current-turn verification evidence"
          : "coding files changed without verification evidence after the last code edit",
      });
      return {
        action: "block",
        reason: [
          "[RETRY:CODING_VERIFICATION] Code files changed in this turn, but post-edit verification evidence is incomplete.",
          "After the last code edit, run GitDiff to capture changed-file/diff evidence and TestRun with the relevant test/build/lint/typecheck command before claiming completion.",
          "If verification cannot be run, say that explicitly and report the remaining risk instead of claiming the work is complete.",
        ].join("\n"),
      };
    },
  };
}
