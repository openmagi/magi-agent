import fs from "node:fs/promises";
import path from "node:path";
import type { Discipline } from "../../Session.js";
import type { CodingCommitUnit, CodingWorkspaceLock, RepoTaskState } from "../../tools/RepoTaskState.js";
import { matchesAny, normalisePath } from "../../discipline/globMatch.js";
import type { HookContext, RegisteredHook } from "../types.js";

export interface CodingWorkspaceLockAgent {
  getSessionDiscipline(sessionKey: string): Discipline | null;
}

export interface CodingWorkspaceLockOptions {
  workspaceRoot: string;
  agent: CodingWorkspaceLockAgent;
}

const COMPLETION_CLAIM_RE =
  /(?:완료|끝났|반영|구현|처리|해결|고쳤|통과|verified|completed|done|implemented|fixed|resolved|passed)/i;

const EXPLICITLY_UNVERIFIED_RE =
  /\b(?:not verified|unverified|could not verify|did not run|tests? failed|build failed)\b/i;

function isCodingMode(discipline: Discipline | null): discipline is Discipline {
  return discipline?.lastClassifiedMode === "coding" && discipline.git === true;
}

function isMutationTool(toolName: string): boolean {
  return (
    toolName === "FileWrite" ||
    toolName === "FileEdit" ||
    toolName === "PatchApply" ||
    toolName === "SpawnWorktreeApply"
  );
}

function pathFromInput(input: unknown): string | null {
  if (!input || typeof input !== "object" || Array.isArray(input)) return null;
  const rec = input as Record<string, unknown>;
  return typeof rec.path === "string" ? rec.path : null;
}

function isApplyMutation(toolName: string, input: unknown): boolean {
  if (toolName !== "SpawnWorktreeApply") return false;
  if (!input || typeof input !== "object" || Array.isArray(input)) return false;
  const action = (input as Record<string, unknown>).action;
  return action === "apply" || action === "cherry_pick";
}

function patchPathsFromInput(input: unknown): string[] {
  if (!input || typeof input !== "object" || Array.isArray(input)) return [];
  const rec = input as Record<string, unknown>;
  if (rec.dry_run === true || typeof rec.patch !== "string") return [];
  const paths: string[] = [];
  for (const line of rec.patch.split(/\r?\n/)) {
    if (!line.startsWith("--- ") && !line.startsWith("+++ ")) continue;
    const raw = line.slice(4).trim().split(/\s+/)[0] ?? "";
    if (!raw || raw === "/dev/null") continue;
    paths.push(raw.replace(/^[ab]\//, ""));
  }
  return [...new Set(paths)];
}

function isCodingPath(target: string, discipline: Discipline): boolean {
  const norm = normalisePath(target);
  return (
    norm.startsWith("code/") ||
    matchesAny(target, discipline.sourcePatterns) ||
    matchesAny(target, discipline.testPatterns)
  );
}

async function readRepoTaskState(workspaceRoot: string): Promise<RepoTaskState | null> {
  try {
    const raw = await fs.readFile(path.join(workspaceRoot, ".magi/repo-task-state.json"), "utf8");
    const parsed = JSON.parse(raw) as Partial<RepoTaskState>;
    return {
      goal: typeof parsed.goal === "string" ? parsed.goal : null,
      plan: Array.isArray(parsed.plan) ? parsed.plan.filter((v): v is string => typeof v === "string") : [],
      touchedFiles: Array.isArray(parsed.touchedFiles)
        ? parsed.touchedFiles.filter((v): v is string => typeof v === "string")
        : [],
      pendingTests: Array.isArray(parsed.pendingTests)
        ? parsed.pendingTests.filter((v): v is string => typeof v === "string")
        : [],
      blockers: Array.isArray(parsed.blockers)
        ? parsed.blockers.filter((v): v is string => typeof v === "string")
        : [],
      acceptanceCriteria: Array.isArray(parsed.acceptanceCriteria)
        ? parsed.acceptanceCriteria.filter((v): v is string => typeof v === "string")
        : [],
      commitUnits: Array.isArray(parsed.commitUnits)
        ? parsed.commitUnits.filter(isCommitUnit)
        : [],
      activeUnitId: typeof parsed.activeUnitId === "string" ? parsed.activeUnitId : null,
      workspaceLock: isWorkspaceLock(parsed.workspaceLock) ? parsed.workspaceLock : null,
      updatedAt: typeof parsed.updatedAt === "string" ? parsed.updatedAt : null,
    };
  } catch {
    return null;
  }
}

function isCommitUnit(value: unknown): value is CodingCommitUnit {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const rec = value as Record<string, unknown>;
  return typeof rec.id === "string" && typeof rec.title === "string" && typeof rec.status === "string";
}

function isWorkspaceLock(value: unknown): value is CodingWorkspaceLock {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const rec = value as Record<string, unknown>;
  return (
    (rec.status === "active" || rec.status === "released") &&
    typeof rec.lockId === "string" &&
    typeof rec.ownerSessionKey === "string" &&
    typeof rec.goal === "string"
  );
}

function activeUnit(state: RepoTaskState | null): CodingCommitUnit | null {
  if (!state?.activeUnitId) return null;
  return state.commitUnits.find((unit) => unit.id === state.activeUnitId) ?? null;
}

function activeLock(state: RepoTaskState | null): CodingWorkspaceLock | null {
  return state?.workspaceLock?.status === "active" ? state.workspaceLock : null;
}

function emitViolation(ctx: HookContext, detail: string): void {
  ctx.emit({
    type: "rule_check",
    ruleId: "coding.workspace_lock",
    verdict: "violation",
    detail,
  });
}

function requiresLock(toolName: string, input: unknown, discipline: Discipline): boolean {
  if (!isMutationTool(toolName)) return false;
  if (isApplyMutation(toolName, input)) return true;
  if (toolName === "PatchApply") {
    return patchPathsFromInput(input).some((target) => isCodingPath(target, discipline));
  }
  const target = pathFromInput(input);
  return target !== null && isCodingPath(target, discipline);
}

function isCompletionClaim(text: string): boolean {
  return COMPLETION_CLAIM_RE.test(text) && !EXPLICITLY_UNVERIFIED_RE.test(text);
}

export function makeCodingWorkspaceLockHooks(
  opts: CodingWorkspaceLockOptions,
): {
  beforeToolUse: RegisteredHook<"beforeToolUse">;
  beforeCommit: RegisteredHook<"beforeCommit">;
} {
  return {
    beforeToolUse: {
      name: "builtin:coding-workspace-lock",
      point: "beforeToolUse",
      priority: 44,
      blocking: true,
      timeoutMs: 500,
      handler: async ({ toolName, input }, ctx) => {
        const discipline = opts.agent.getSessionDiscipline(ctx.sessionKey);
        if (!isCodingMode(discipline)) return { action: "continue" };
        if (!requiresLock(toolName, input, discipline)) return { action: "continue" };

        const state = await readRepoTaskState(opts.workspaceRoot);
        const lock = activeLock(state);
        const unit = activeUnit(state);
        if (lock && lock.ownerSessionKey !== ctx.sessionKey) {
          const reason = `Coding workspace lock is owned by ${lock.ownerSessionKey} for "${lock.goal}". Queue this request or continue that task instead of mixing edits.`;
          emitViolation(ctx, reason);
          return { action: "block", reason };
        }
        if (!lock) {
          const reason =
            "Coding workspace lock required. Call RepoTaskState action='update' with workspaceLock.action='acquire' and create an in_progress commit unit before editing coding files.";
          emitViolation(ctx, reason);
          if (discipline.requireCommit === "hard") return { action: "block", reason };
          return { action: "continue" };
        }
        if (!unit || unit.status !== "in_progress") {
          const reason =
            "Active coding workspace lock has no in_progress commit unit. Update RepoTaskState.activeUnitId and commitUnits before editing coding files.";
          emitViolation(ctx, reason);
          if (discipline.requireCommit === "hard") return { action: "block", reason };
        }
        return { action: "continue" };
      },
    },
    beforeCommit: {
      name: "builtin:coding-unit-completion-gate",
      point: "beforeCommit",
      priority: 87,
      blocking: true,
      timeoutMs: 500,
      handler: async ({ assistantText, filesChanged }, ctx) => {
        const discipline = opts.agent.getSessionDiscipline(ctx.sessionKey);
        if (!isCodingMode(discipline)) return { action: "continue" };
        if (!filesChanged || filesChanged.length === 0) return { action: "continue" };
        if (!isCompletionClaim(assistantText)) return { action: "continue" };

        const state = await readRepoTaskState(opts.workspaceRoot);
        const lock = activeLock(state);
        if (!lock || lock.ownerSessionKey !== ctx.sessionKey) return { action: "continue" };
        const unit = activeUnit(state);
        if (!unit || unit.status === "completed") return { action: "continue" };

        const reason = [
          `[RETRY:CODING_UNIT_INCOMPLETE] Active commit unit ${unit.id} is still ${unit.status}.`,
          "Record post-edit GitDiff/TestRun evidence, call CommitCheckpoint when git discipline is enabled, then update RepoTaskState.commitUnits status='completed' before claiming completion.",
          "If this is only partial progress, say that explicitly instead of claiming the task is done.",
        ].join("\n");
        emitViolation(ctx, reason);
        return { action: "block", reason };
      },
    },
  };
}
