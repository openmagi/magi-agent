import type { Discipline } from "../../Session.js";
import type { TranscriptEntry } from "../../storage/Transcript.js";
import type { HookContext, RegisteredHook } from "../types.js";

export interface CodingChildReviewGateAgent {
  getSessionDiscipline(sessionKey: string): Discipline | null;
  readSessionTranscript(
    sessionKey: string,
  ): Promise<ReadonlyArray<TranscriptEntry> | null>;
}

export interface CodingChildReviewGateOptions {
  agent?: CodingChildReviewGateAgent;
}

const COMPLETION_CLAIM_RE =
  /(?:완료|끝났|반영|구현|처리|해결|고쳤|통과|verified|completed|done|implemented|fixed|resolved|passed)/i;

const EXPLICITLY_UNVERIFIED_RE =
  /\b(?:not verified|unverified|could not verify|did not run|tests? failed|build failed)\b/i;

const CODING_PERSONA_RE =
  /(?:^|[-_\s])(coder|coding|code|developer|engineer|implementer)(?:$|[-_\s])/i;

const NON_IMPLEMENTER_PERSONA_RE =
  /(?:^|[-_\s])(planner|plan|reviewer|review|critic|synthesis|research|scout|explore)(?:$|[-_\s])/i;

const REVIEWER_PERSONA_RE =
  /(?:^|[-_\s])(reviewer|review|critic)(?:$|[-_\s])/i;

const CONFLICT_RESOLVER_PERSONA_RE =
  /(?:^|[-_\s])conflict[-_\s]?resolver(?:$|[-_\s])/i;

const CODE_MUTATION_TOOLS = new Set(["FileWrite", "FileEdit", "PatchApply", "SpawnWorktreeApply"]);

function isEnabled(): boolean {
  const raw = process.env.MAGI_CODING_CHILD_REVIEW;
  if (raw === undefined || raw === null) return true;
  const v = raw.trim().toLowerCase();
  return v === "" || v === "on" || v === "true" || v === "1";
}

function isCodingMode(discipline: Discipline | null): boolean {
  return discipline?.lastClassifiedMode === "coding";
}

function isCompletionClaim(text: string): boolean {
  return COMPLETION_CLAIM_RE.test(text) && !EXPLICITLY_UNVERIFIED_RE.test(text);
}

async function readTranscript(
  opts: CodingChildReviewGateOptions,
  ctx: HookContext,
): Promise<ReadonlyArray<TranscriptEntry>> {
  if (!opts.agent) return ctx.transcript;
  try {
    return (await opts.agent.readSessionTranscript(ctx.sessionKey)) ?? ctx.transcript;
  } catch (err) {
    ctx.log("warn", "[coding-child-review-gate] transcript read failed", {
      error: err instanceof Error ? err.message : String(err),
    });
    return ctx.transcript;
  }
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value) return null;
  if (typeof value === "object" && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }
  if (typeof value !== "string") return null;
  try {
    const parsed = JSON.parse(value) as unknown;
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? parsed as Record<string, unknown>
      : null;
  } catch {
    return null;
  }
}

type ToolResultEntry = Extract<TranscriptEntry, { kind: "tool_result" }>;

interface ToolResultRecord {
  entry: ToolResultEntry;
  index: number;
  output: Record<string, unknown> | null;
}

interface WorktreeConflictTarget {
  index: number;
  spawnDir: string | null;
  conflictedFiles: string[];
}

function resultMap(
  transcript: ReadonlyArray<TranscriptEntry>,
  turnId: string,
): Map<string, ToolResultRecord> {
  const out = new Map<string, ToolResultRecord>();
  for (const [index, entry] of transcript.entries()) {
    if (entry.kind !== "tool_result" || entry.turnId !== turnId) continue;
    out.set(entry.toolUseId, {
      entry,
      index,
      output: asRecord(entry.output),
    });
  }
  return out;
}

function isSuccessfulResult(result: ToolResultRecord | undefined): boolean {
  if (!result) return false;
  if (result.entry.isError === true) return false;
  if (result.entry.status && result.entry.status !== "ok" && result.entry.status !== "success") {
    return false;
  }
  const nestedStatus = result.output?.status;
  return nestedStatus === undefined || nestedStatus === "ok" || nestedStatus === "success";
}

function hasNonEmptyFinalText(result: ToolResultRecord): boolean {
  const text = result.output?.finalText;
  return typeof text === "string" && text.trim().length > 0;
}

function toolCallCount(result: ToolResultRecord): number {
  const raw = result.output?.toolCallCount;
  return typeof raw === "number" && Number.isFinite(raw) ? raw : 0;
}

function isMutatingApply(entry: Extract<TranscriptEntry, { kind: "tool_call" }>): boolean {
  if (entry.name !== "SpawnWorktreeApply") return false;
  const input = asRecord(entry.input);
  const action = input?.action;
  return action === "apply" || action === "cherry_pick";
}

function isCodeMutation(
  entry: Extract<TranscriptEntry, { kind: "tool_call" }>,
  result: ToolResultRecord | undefined,
): boolean {
  if (!CODE_MUTATION_TOOLS.has(entry.name)) return false;
  if (entry.name === "SpawnWorktreeApply") {
    return isSuccessfulResult(result) && isMutatingApply(entry) && result?.output?.applied === true;
  }
  if (entry.name === "PatchApply") {
    const input = asRecord(entry.input);
    return input?.dry_run !== true && isSuccessfulResult(result);
  }
  return isSuccessfulResult(result);
}

function hasChildChangedFiles(result: ToolResultRecord | undefined): boolean {
  const evidence = result?.output?.childEvidence;
  if (!evidence || typeof evidence !== "object" || Array.isArray(evidence)) return false;
  const changedFiles = (evidence as Record<string, unknown>).changedFiles;
  return Array.isArray(changedFiles) && changedFiles.some((value) => typeof value === "string");
}

function isCodingChildCall(
  entry: Extract<TranscriptEntry, { kind: "tool_call" }>,
  result: ToolResultRecord | undefined,
): boolean {
  if (entry.name !== "SpawnAgent") return false;
  const input = asRecord(entry.input);
  const persona = typeof input?.persona === "string" ? input.persona : "";
  if (NON_IMPLEMENTER_PERSONA_RE.test(persona)) return false;
  const writeSet = input?.write_set;
  const workspacePolicy = input?.workspace_policy;
  return (
    (CODING_PERSONA_RE.test(persona) && hasChildChangedFiles(result)) ||
    (Array.isArray(writeSet) && writeSet.length > 0) ||
    workspacePolicy === "git_worktree"
  );
}

function isReviewerChildCall(entry: Extract<TranscriptEntry, { kind: "tool_call" }>): boolean {
  if (entry.name !== "SpawnAgent") return false;
  const input = asRecord(entry.input);
  const persona = typeof input?.persona === "string" ? input.persona : "";
  return REVIEWER_PERSONA_RE.test(persona);
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function worktreeSpawnDir(
  entry: Extract<TranscriptEntry, { kind: "tool_call" }>,
  result: ToolResultRecord | undefined,
): string | null {
  const outputSpawnDir = result?.output?.spawnDir;
  if (typeof outputSpawnDir === "string" && outputSpawnDir.length > 0) return outputSpawnDir;
  const input = asRecord(entry.input);
  const inputSpawnDir = input?.spawnDir;
  return typeof inputSpawnDir === "string" && inputSpawnDir.length > 0 ? inputSpawnDir : null;
}

function worktreeConflictTarget(
  entry: Extract<TranscriptEntry, { kind: "tool_call" }>,
  result: ToolResultRecord | undefined,
): WorktreeConflictTarget | null {
  if (entry.name !== "SpawnWorktreeApply" || !result?.output) return null;
  const conflictReview = asRecord(result.output.conflictReview);
  if (!conflictReview) return null;
  const conflictedFiles = stringArray(result.output.conflictedFiles);
  const reviewFiles = stringArray(conflictReview.conflictedFiles);
  return {
    index: result.index,
    spawnDir: worktreeSpawnDir(entry, result),
    conflictedFiles: conflictedFiles.length > 0 ? conflictedFiles : reviewFiles,
  };
}

function writeSetCoversFiles(writeSet: unknown, conflictedFiles: readonly string[]): boolean {
  if (conflictedFiles.length === 0) return true;
  const allowed = new Set(stringArray(writeSet));
  return conflictedFiles.every((file) => allowed.has(file));
}

function isConflictResolverChildAfter(
  entry: Extract<TranscriptEntry, { kind: "tool_call" }>,
  result: ToolResultRecord | undefined,
  conflict: WorktreeConflictTarget,
): boolean {
  if (entry.name !== "SpawnAgent") return false;
  if (!result || result.index <= conflict.index || !isSuccessfulResult(result)) return false;
  if (!hasNonEmptyFinalText(result) || toolCallCount(result) <= 0) return false;
  const input = asRecord(entry.input);
  const persona = typeof input?.persona === "string" ? input.persona : "";
  if (!CONFLICT_RESOLVER_PERSONA_RE.test(persona)) return false;
  return writeSetCoversFiles(input?.write_set, conflict.conflictedFiles);
}

function successfulSameSpawnWorktreeDispositionAfter(
  entry: Extract<TranscriptEntry, { kind: "tool_call" }>,
  result: ToolResultRecord | undefined,
  conflict: WorktreeConflictTarget,
): boolean {
  if (entry.name !== "SpawnWorktreeApply") return false;
  if (!conflict.spawnDir || !result || result.index <= conflict.index || !isSuccessfulResult(result)) {
    return false;
  }
  const input = asRecord(entry.input);
  const action = typeof input?.action === "string" ? input.action : result.output?.action;
  if (action !== "apply" && action !== "cherry_pick" && action !== "reject") return false;
  if (action !== "reject" && result.output?.applied !== true) return false;
  return worktreeSpawnDir(entry, result) === conflict.spawnDir;
}

function latestUnresolvedWorktreeConflict(
  transcript: ReadonlyArray<TranscriptEntry>,
  turnId: string,
): WorktreeConflictTarget | null {
  const results = resultMap(transcript, turnId);
  let latest: WorktreeConflictTarget | null = null;

  for (const [index, entry] of transcript.entries()) {
    if (entry.kind !== "tool_call" || entry.turnId !== turnId) continue;
    const result = results.get(entry.toolUseId);
    const nextConflict = worktreeConflictTarget(entry, result);
    if (nextConflict) {
      latest = nextConflict;
      continue;
    }
    if (!latest || index <= latest.index) continue;
    if (
      isConflictResolverChildAfter(entry, result, latest) ||
      successfulSameSpawnWorktreeDispositionAfter(entry, result, latest)
    ) {
      latest = null;
    }
  }

  return latest;
}

function latestReviewTargetIndex(transcript: ReadonlyArray<TranscriptEntry>, turnId: string): number | null {
  const results = resultMap(transcript, turnId);
  let sawCodingChild = false;
  let latest: number | null = null;

  for (const [index, entry] of transcript.entries()) {
    if (entry.kind !== "tool_call" || entry.turnId !== turnId) continue;
    const result = results.get(entry.toolUseId);
    if (isCodingChildCall(entry, result) && isSuccessfulResult(result)) {
      sawCodingChild = true;
      latest = Math.max(latest ?? -1, result?.index ?? index);
      continue;
    }
    if (isCodeMutation(entry, result)) {
      latest = Math.max(latest ?? -1, result?.index ?? index);
    }
  }

  return sawCodingChild ? latest : null;
}

function hasSuccessfulReviewerAfter(
  transcript: ReadonlyArray<TranscriptEntry>,
  turnId: string,
  afterIndex: number,
): boolean {
  const results = resultMap(transcript, turnId);
  for (const [index, entry] of transcript.entries()) {
    if (index <= afterIndex || entry.kind !== "tool_call" || entry.turnId !== turnId) continue;
    if (!isReviewerChildCall(entry)) continue;
    const result = results.get(entry.toolUseId);
    if (!result || !isSuccessfulResult(result)) continue;
    if (!hasNonEmptyFinalText(result)) continue;
    if (toolCallCount(result) <= 0) continue;
    if (result.index <= afterIndex) continue;
    return true;
  }
  return false;
}

export function makeCodingChildReviewGateHook(
  opts: CodingChildReviewGateOptions = {},
): RegisteredHook<"beforeCommit"> {
  return {
    name: "builtin:coding-child-review-gate",
    point: "beforeCommit",
    priority: 89,
    blocking: true,
    timeoutMs: 2_000,
    handler: async ({ assistantText, filesChanged }, ctx) => {
      if (!isEnabled()) return { action: "continue" };
      const discipline = opts.agent?.getSessionDiscipline(ctx.sessionKey) ?? null;
      if (!isCodingMode(discipline)) return { action: "continue" };
      if (!isCompletionClaim(assistantText)) return { action: "continue" };

      const transcript = await readTranscript(opts, ctx);
      const unresolvedConflict = latestUnresolvedWorktreeConflict(transcript, ctx.turnId);
      if (unresolvedConflict) {
        const files = unresolvedConflict.conflictedFiles.length > 0
          ? unresolvedConflict.conflictedFiles.join(", ")
          : "unknown files";
        const reason = [
          "[RETRY:SPAWN_WORKTREE_CONFLICT_RESOLUTION_REQUIRED] SpawnWorktreeApply reported a child worktree conflict, but no successful conflict_resolver child or same-spawn apply/cherry_pick/reject disposition ran after it.",
          `Spawn a child with persona:"conflict_resolver", deliver:"return", workspace_policy:"trusted", and write_set covering: ${files}.`,
          "Alternatively, explicitly apply/cherry_pick or reject the preserved child worktree from the same spawnDir.",
          "After resolving, run GitDiff and relevant TestRun evidence, then spawn a reviewer child before claiming completion.",
        ].join("\n");
        ctx.emit({
          type: "rule_check",
          ruleId: "coding-child-worktree-conflict-gate",
          verdict: "violation",
          detail: `unresolved child worktree conflict for ${files}`,
        });
        return { action: "block", reason };
      }

      if (!filesChanged || filesChanged.length === 0) return { action: "continue" };
      const targetIndex = latestReviewTargetIndex(transcript, ctx.turnId);
      if (targetIndex === null) return { action: "continue" };
      if (hasSuccessfulReviewerAfter(transcript, ctx.turnId, targetIndex)) {
        ctx.emit({
          type: "rule_check",
          ruleId: "coding-child-review-gate",
          verdict: "ok",
          detail: "coding child work reviewed by a later reviewer SpawnAgent",
        });
        return { action: "continue" };
      }

      const reason = [
        "[RETRY:CODING_CHILD_REVIEW_REQUIRED] Coding child work changed files, but no successful reviewer SpawnAgent ran after the latest child apply/edit.",
        'Spawn a reviewer child with persona:"reviewer", deliver:"return", and the changed files plus acceptance criteria. The reviewer must inspect the latest diff/test evidence without mutating files.',
        "If this is only partial progress, say that explicitly instead of claiming completion.",
      ].join("\n");
      ctx.emit({
        type: "rule_check",
        ruleId: "coding-child-review-gate",
        verdict: "violation",
        detail: "coding child completion claim without a fresh reviewer SpawnAgent",
      });
      return { action: "block", reason };
    },
  };
}
